"""M1 单测：EmbeddingService + Ollama 客户端 + 熔断器 + warmup。

- 不 mock 数据库；EmbeddingService 的 HTTP 调用（requests.post）通过 monkeypatch 替换。
- pytest + pytest-asyncio（asyncio_mode=auto）。
- 熔断器的 time.monotonic 通过 monkeypatch 控制推进。
"""

from __future__ import annotations

import math
from typing import Any

import pytest
import pytest_asyncio
import requests

from mnemo.config import MnemoConfig
from mnemo.services import embedding_service as es_mod
from mnemo.services.embedding_service import CircuitState, EmbeddingService


EMBEDDING_DIM = 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


def _ok_vector(dim: int = EMBEDDING_DIM, fill: float = 0.01) -> list[float]:
    return [fill] * dim


def _ok_payload(dim: int = EMBEDDING_DIM) -> dict[str, Any]:
    return {"embeddings": [_ok_vector(dim)]}


@pytest_asyncio.fixture
async def service() -> EmbeddingService:
    config = MnemoConfig(
        embedding_circuit_threshold=3,
        embedding_circuit_cooldown_s=60,
        embedding_timeout_ms=800,
        embedding_warmup_timeout_ms=2000,
    )
    return EmbeddingService(config=config)


# ---------------------------------------------------------------------------
# 单元测试（9 条）
# ---------------------------------------------------------------------------


async def test_embed_returns_1024_dim_on_ok(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1. embed() 正常返回 1024 维向量。"""
    def fake_post(*_a: Any, **_kw: Any) -> _FakeResp:
        return _FakeResp(_ok_payload())

    monkeypatch.setattr(es_mod.requests, "post", fake_post)
    vec = await service.embed("hello")
    assert vec is not None
    assert len(vec) == EMBEDDING_DIM
    assert all(isinstance(v, float) for v in vec[:8])


async def test_embed_returns_none_on_ollama_timeout(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2. Ollama 超时 → 返回 None。"""
    def fake_post(*_a: Any, **_kw: Any):
        raise requests.Timeout("fake timeout")

    monkeypatch.setattr(es_mod.requests, "post", fake_post)
    assert await service.embed("hello") is None


async def test_circuit_breaker_opens_after_3_timeouts(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3. 连续 3 次超时后直接返回 None 不调 Ollama。"""
    call_count = {"n": 0}

    def fake_post(*_a: Any, **_kw: Any):
        call_count["n"] += 1
        raise requests.Timeout("fake timeout")

    monkeypatch.setattr(es_mod.requests, "post", fake_post)

    for _ in range(3):
        assert await service.embed("x") is None
    assert call_count["n"] == 3
    assert service.circuit_state == CircuitState.OPEN

    # 第 4 次即使 Ollama 换成正常响应，熔断器仍然拦截（state=OPEN，未到 cooldown）
    def fake_post_ok(*_a: Any, **_kw: Any) -> _FakeResp:
        call_count["n"] += 1
        return _FakeResp(_ok_payload())

    monkeypatch.setattr(es_mod.requests, "post", fake_post_ok)
    assert await service.embed("x") is None
    assert call_count["n"] == 3  # 未被调用


async def test_circuit_breaker_half_open_after_60s(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4. 熔断器：60s 后半开试探恢复。"""
    clock = {"t": 1000.0}
    monkeypatch.setattr(es_mod.time, "monotonic", lambda: clock["t"])

    # 先触发 3 次超时打开熔断
    def fake_post_timeout(*_a: Any, **_kw: Any):
        raise requests.Timeout()

    monkeypatch.setattr(es_mod.requests, "post", fake_post_timeout)
    for _ in range(3):
        await service.embed("x")
    assert service.circuit_state == CircuitState.OPEN

    # 推进 61s，切换为正常响应，下次 embed 应试探并成功恢复
    clock["t"] += 61.0

    def fake_post_ok(*_a: Any, **_kw: Any) -> _FakeResp:
        return _FakeResp(_ok_payload())

    monkeypatch.setattr(es_mod.requests, "post", fake_post_ok)
    vec = await service.embed("x")
    assert vec is not None
    assert service.circuit_state == CircuitState.CLOSED


async def test_warmup_sets_ready_true_on_success(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5. warmup() 成功设置 ready=True。"""
    monkeypatch.setattr(
        es_mod.requests, "post", lambda *_a, **_kw: _FakeResp(_ok_payload())
    )
    assert service.ready is False
    ok = await service.warmup()
    assert ok is True
    assert service.ready is True
    assert service.circuit_state == CircuitState.CLOSED


async def test_warmup_does_not_raise_on_timeout(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """6. warmup() 超时不抛错。"""
    def fake_post(*_a: Any, **_kw: Any):
        raise requests.Timeout()

    monkeypatch.setattr(es_mod.requests, "post", fake_post)
    ok = await service.warmup()
    assert ok is False
    assert service.ready is False


def test_prepare_text_truncates_long_content() -> None:
    """7. prepare_text() 截断 content 到 1500 字符。"""
    config = MnemoConfig(embedding_content_max_chars=1500)
    svc = EmbeddingService(config=config)
    long_content = "abcd" * 1000  # 4000 字符
    out = svc.prepare_text("标题", "摘要", long_content)
    # "标题. 摘要 " + content[:1500]
    assert "abcd" in out
    # content 片段最大 1500
    content_part = out.split(" ", 2)[-1]
    assert len(content_part) <= 1500


async def test_embed_returns_none_on_nan(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8. NaN 检测 → 返回 None。"""
    nan_vec = [math.nan] + [0.1] * (EMBEDDING_DIM - 1)

    def fake_post(*_a: Any, **_kw: Any) -> _FakeResp:
        return _FakeResp({"embeddings": [nan_vec]})

    monkeypatch.setattr(es_mod.requests, "post", fake_post)
    assert await service.embed("hello") is None


async def test_embed_batch_processes_multiple_texts(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9. embed_batch() 正确批量处理。"""
    counter = {"n": 0}

    def fake_post(*_a: Any, **_kw: Any) -> _FakeResp:
        counter["n"] += 1
        # 每次填充不同的标量以便验证顺序
        vec = [float(counter["n"])] * EMBEDDING_DIM
        return _FakeResp({"embeddings": [vec]})

    monkeypatch.setattr(es_mod.requests, "post", fake_post)
    texts = [f"t{i}" for i in range(10)]
    results = await service.embed_batch(texts, batch_size=4)
    assert len(results) == 10
    for i, vec in enumerate(results):
        assert vec is not None
        assert len(vec) == EMBEDDING_DIM
        assert vec[0] == float(i + 1)  # 顺序保持


async def test_embed_dim_mismatch_returns_none(
    service: EmbeddingService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """10. 额外：维度不匹配 → 返回 None。"""
    bad_vec = [0.1] * 512  # 不是 1024

    def fake_post(*_a: Any, **_kw: Any) -> _FakeResp:
        return _FakeResp({"embeddings": [bad_vec]})

    monkeypatch.setattr(es_mod.requests, "post", fake_post)
    assert await service.embed("hello") is None
