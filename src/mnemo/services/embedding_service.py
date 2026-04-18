"""EmbeddingService — Ollama HTTP 客户端 + 二级降级 + 熔断器。

写入主流程永不因 embedding 失败而失败：
- L0: Ollama 主路径，单条 800ms 超时；失败走 L2。
- L2: 返回 None，调用方负责写 knowledge_event 记录。

熔断器：连续 N 次失败 → 跳过 L0 直接返回 None；cooldown 后半开试探 1 次。

HTTP 调用用同步 requests（M0 评测脚本已验证），在 asyncio 场景下通过
run_in_executor 桥接，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from mnemo.config import MnemoConfig


logger = logging.getLogger(__name__)


class CircuitState:
    CLOSED = "closed"       # 正常
    OPEN = "open"           # 熔断中，直接返回 None
    HALF_OPEN = "half_open" # cooldown 结束，放行 1 次试探


@dataclass
class _CircuitBreaker:
    threshold: int
    cooldown_s: float

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._fail_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._half_open_inflight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def allow(self) -> bool:
        """判断当前请求是否放行。

        CLOSED → 放行；OPEN → 若 cooldown 到期切 HALF_OPEN 放行 1 次；
        HALF_OPEN 且已有 inflight 请求 → 拒绝，避免并发击穿。
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_inflight = True
                    return True
                return False
            # HALF_OPEN
            if self._half_open_inflight:
                return False
            self._half_open_inflight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._fail_count = 0
            self._state = CircuitState.CLOSED
            self._half_open_inflight = False

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = False
                return
            self._fail_count += 1
            if self._fail_count >= self.threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._fail_count = 0
            self._state = CircuitState.CLOSED
            self._half_open_inflight = False
            self._opened_at = 0.0


class EmbeddingService:
    """Ollama embedding 客户端。

    同步 HTTP 调用通过 asyncio.to_thread 桥接，避免阻塞事件循环。
    返回 None 表示 L2 降级（调用方写 knowledge_event）。
    """

    def __init__(self, config: MnemoConfig | None = None):
        self.config = config or MnemoConfig()
        self._endpoint = f"{self.config.ollama_url.rstrip('/')}/api/embed"
        self._timeout_s = self.config.embedding_timeout_ms / 1000.0
        self._breaker = _CircuitBreaker(
            threshold=self.config.embedding_circuit_threshold,
            cooldown_s=float(self.config.embedding_circuit_cooldown_s),
        )
        self.ready = False
        self._warmup_lock = asyncio.Lock()

    # ---- text prep --------------------------------------------------------

    def prepare_text(
        self,
        title: str,
        summary: str | None = None,
        content: str | None = None,
    ) -> str:
        """拼接 title / summary / content，content 截断到配置上限。"""
        summary = summary or ""
        content = content or ""
        max_chars = self.config.embedding_content_max_chars
        truncated = content[:max_chars]
        return f"{title}. {summary} {truncated}".strip()

    # ---- core HTTP --------------------------------------------------------

    def _embed_sync(
        self, text: str, timeout_s: float
    ) -> Optional[list[float]]:
        """阻塞版本，调用方负责线程桥接。返回 None = 失败/降级。"""
        try:
            resp = requests.post(
                self._endpoint,
                json={"model": self.config.embedding_model, "input": text},
                timeout=timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.Timeout:
            logger.debug("embedding timeout after %.3fs", timeout_s)
            return None
        except requests.RequestException as e:
            logger.debug("embedding http error: %s", e)
            return None
        except ValueError as e:
            logger.debug("embedding json decode error: %s", e)
            return None

        embeddings = data.get("embeddings")
        if not embeddings or not isinstance(embeddings, list):
            logger.debug("embedding response missing 'embeddings' field")
            return None
        vec = embeddings[0]
        if not isinstance(vec, list) or not vec:
            return None
        # NaN 检查：只检查前 32 维即可识别异常模型输出。
        if any(not isinstance(v, (int, float)) or math.isnan(float(v)) for v in vec[:32]):
            logger.debug("embedding contains NaN / non-numeric")
            return None
        if len(vec) != self.config.embedding_dim:
            logger.warning(
                "embedding dim mismatch: got %d expected %d",
                len(vec),
                self.config.embedding_dim,
            )
            return None
        return [float(v) for v in vec]

    # ---- public async API -------------------------------------------------

    async def embed(self, text: str) -> Optional[list[float]]:
        """单条 embed。返回 None = L2 降级。"""
        if not text:
            return None
        if not self._breaker.allow():
            logger.debug("circuit open, skipping embed")
            return None
        vec = await asyncio.to_thread(self._embed_sync, text, self._timeout_s)
        if vec is None:
            self._breaker.record_failure()
        else:
            self._breaker.record_success()
        return vec

    async def embed_batch(
        self,
        texts: list[str],
        batch_size: int = 64,
    ) -> list[Optional[list[float]]]:
        """批量 embed。逐条调用（Ollama batch 语义不稳定），保持顺序。

        batch_size 用来控制并发分片，当前实现为顺序调用以避免 Ollama 本地
        并发争抢模型内存；后续若证实 Ollama 支持并发可改为 gather。
        """
        if not texts:
            return []
        results: list[Optional[list[float]]] = []
        for text in texts:
            results.append(await self.embed(text))
        return results

    async def warmup(self) -> bool:
        """启动时调用，不阻塞主流程。成功 → ready=True 且熔断器清零。"""
        async with self._warmup_lock:
            if self.ready:
                return True
            timeout_s = self.config.embedding_warmup_timeout_ms / 1000.0
            try:
                vec = await asyncio.to_thread(
                    self._embed_sync, "warmup", timeout_s
                )
            except Exception as e:  # noqa: BLE001 — warmup 永不抛
                logger.warning("warmup exception: %s", e)
                return False
            if vec is None:
                logger.warning("warmup failed / timeout after %.1fs", timeout_s)
                return False
            self._breaker.reset()
            self.ready = True
            logger.info("embedding warmup ready (dim=%d)", len(vec))
            return True

    @property
    def circuit_state(self) -> str:
        return self._breaker.state
