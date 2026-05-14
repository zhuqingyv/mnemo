"""FastAPI router for the Mnemo Guide API.

Exposes ``POST /api/v1/guide/ask`` — accepts a user question about Mnemo
and returns an answer.

Answer pipeline (two paths):

**LLM path** (when ``guide_model.enabled`` is True and the provider is
available):
  1. Fixed reply shortcut (identity / off-topic / URL → return immediately).
  2. Extractor: LLM extracts search keywords from the question.
  3. Knowledge retriever: full-text search with extracted keywords.
  4. Responder: PromptBuilder + LLM generates answer from search results.
  5. OutputValidator: safety check → return if valid, fall through if not.

**V0 deterministic path** (fallback — always available):
  1. Fixed reply → FAQ matcher → knowledge retriever → install templates →
     generic fallback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from mnemo.config import MnemoConfig
from mnemo.guide.fallback import FallbackHandler
from mnemo.guide.faq_matcher import FAQMatcher
from mnemo.guide.install_templates import InstallTemplateEngine
from mnemo.guide.intent_router import IntentRouter
from mnemo.guide.knowledge_pack import KnowledgePack
from mnemo.guide.model import (
    DisabledModelProvider,
    LlamaCppModelProvider,
    LocalModelProvider,
    OllamaModelProvider,
    OutputValidator,
    PromptBuilder,
)
from mnemo.guide.retriever import KnowledgeRetriever
from mnemo.guide.types import KnowledgeCard

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    """Incoming question about Mnemo."""

    question: str = Field(..., min_length=1, description="用户关于 Mnemo 的问题")


class AskResponse(BaseModel):
    """Answer returned by the Mnemo Guide."""

    answer: str = Field(..., description="回答文本")
    intent: str = Field(..., description="检测到的用户意图")
    commands: list[dict[str, Any]] = Field(
        default_factory=list, description="相关命令行模板"
    )
    model_used: bool = Field(
        default=False, description="是否使用了 LLM（V0 始终为 False）"
    )
    source: str = Field(default="faq", description="回答来源: llm / fixed_reply / faq / knowledge_card / install_template / fallback")
    cards_used: list[str] = Field(
        default_factory=list, description="引用的知识卡片 ID"
    )


# ---------------------------------------------------------------------------
# Keyword extraction prompt (for the LLM extractor stage)
# ---------------------------------------------------------------------------

EXTRACTOR_PROMPT = """你是一个关键词提取器。从用户消息中提取用于搜索的查询关键词。

规则：
- 提取 3-8 个关键词，用逗号分隔
- 同时提取中文和英文关键词
- 提取名词和动词，忽略语气词
- 原始用户消息可能混合中英文，提取时保持原样

用户消息: {user_message}
关键词:"""


# ---------------------------------------------------------------------------
# Module-level singletons (initialised lazily)
# ---------------------------------------------------------------------------

_router: APIRouter | None = None
_pack: KnowledgePack | None = None
_intent_router: IntentRouter | None = None
_faq_matcher: FAQMatcher | None = None
_retriever: KnowledgeRetriever | None = None
_install_engine: InstallTemplateEngine | None = None
_fallback_handler: FallbackHandler | None = None
_provider: LocalModelProvider | None = None
_provider_checked: bool = False
_provider_available: bool = False
_prompt_builder: PromptBuilder | None = None
_output_validator: OutputValidator | None = None


def _get_pack() -> KnowledgePack:
    global _pack
    if _pack is None:
        _pack = KnowledgePack.load()
    return _pack


def _get_intent_router() -> IntentRouter:
    global _intent_router
    if _intent_router is None:
        _intent_router = IntentRouter()
    return _intent_router


def _get_faq_matcher() -> FAQMatcher:
    global _faq_matcher
    if _faq_matcher is None:
        _faq_matcher = FAQMatcher(_get_pack())
    return _faq_matcher


def _get_retriever() -> KnowledgeRetriever:
    global _retriever
    if _retriever is None:
        _retriever = KnowledgeRetriever(_get_pack())
    return _retriever


def _get_install_engine() -> InstallTemplateEngine:
    global _install_engine
    if _install_engine is None:
        _install_engine = InstallTemplateEngine()
    return _install_engine


def _get_fallback_handler() -> FallbackHandler:
    global _fallback_handler
    if _fallback_handler is None:
        _fallback_handler = FallbackHandler()
    return _fallback_handler


def _get_provider() -> LocalModelProvider:
    """Create the appropriate provider based on config."""
    global _provider
    if _provider is None:
        config = MnemoConfig()
        gm = config.guide_model

        if not gm.get("enabled", False):
            _provider = DisabledModelProvider()
        elif gm.get("provider") == "ollama":
            _provider = OllamaModelProvider(config)
        else:
            _provider = LlamaCppModelProvider(config)
    return _provider


def _get_prompt_builder() -> PromptBuilder:
    global _prompt_builder
    if _prompt_builder is None:
        _prompt_builder = PromptBuilder()
    return _prompt_builder


def _get_output_validator() -> OutputValidator:
    global _output_validator
    if _output_validator is None:
        _output_validator = OutputValidator()
    return _output_validator


async def _check_provider() -> bool:
    """Check if the model provider is available (cached result)."""
    global _provider_checked, _provider_available
    if not _provider_checked:
        provider = _get_provider()
        _provider_available = await provider.is_available()
        _provider_checked = True
        if _provider_available:
            logger.info("Guide LLM provider is available")
        else:
            logger.info("Guide LLM provider is not available")
    return _provider_available


# ---------------------------------------------------------------------------
# LLM Answer Pipeline
# ---------------------------------------------------------------------------

async def _try_llm_answer(
    question: str,
    route_result: Any,
    retriever: KnowledgeRetriever,
) -> AskResponse | None:
    """Attempt to answer via the LLM pipeline.

    Returns an ``AskResponse`` on success, or ``None`` to fall through
    to the V0 deterministic chain.
    """
    provider = _get_provider()

    # --- Stage 1: Extract keywords ---
    extract_prompt = EXTRACTOR_PROMPT.format(user_message=question)
    try:
        keywords_text = await provider.generate(extract_prompt, max_tokens=100)
    except Exception:
        logger.exception("LLM keyword extraction failed")
        return None

    if not keywords_text.strip():
        return None

    # Parse keywords: split by comma (Chinese or English), strip whitespace
    keywords = [
        k.strip() for k in keywords_text.replace("，", ",").split(",")
        if k.strip()
    ]
    if not keywords:
        return None

    logger.debug("Extracted keywords: %s", keywords)

    # --- Stage 2: Search knowledge ---
    # Use the first 3 keywords as the search query
    search_query = " ".join(keywords[:3])
    cards: list[KnowledgeCard] = retriever.retrieve(
        question=search_query,
        intent=route_result.intent,
        client=route_result.client,
        platform=route_result.platform,
        top_k=5,
    )

    if not cards:
        # No cards found — try search with the full original question
        cards = retriever.retrieve(
            question=question,
            intent=route_result.intent,
            client=route_result.client,
            platform=route_result.platform,
            top_k=5,
        )

    # --- Stage 3: Build prompt and generate answer ---
    prompt_builder = _get_prompt_builder()
    prompt = prompt_builder.build_answer_prompt(
        question=question,
        intent=route_result.intent,
        context_cards=cards,
    )

    try:
        answer = await provider.generate(prompt)
    except Exception:
        logger.exception("LLM answer generation failed")
        return None

    if not answer.strip():
        return None

    # --- Stage 4: Validate output ---
    validator = _get_output_validator()
    is_valid, rejection = validator.validate(
        answer, question, route_result.intent
    )
    if not is_valid:
        logger.warning(
            "LLM answer rejected by validator: %s (answer preview: %.100s...)",
            rejection, answer,
        )
        return None

    # --- Stage 5: Return LLM answer ---
    return AskResponse(
        answer=answer,
        intent=route_result.intent,
        source="llm",
        model_used=True,
        cards_used=[c.id for c in cards[:5]],
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["guide"])


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    """Answer a Mnemo-related question.

    Tries the LLM pipeline first when ``guide_model.enabled`` is True
    and the provider is reachable. Falls back to the V0 deterministic
    chain on any failure or when the model is disabled.
    """
    question = payload.question.strip()
    if not question:
        return AskResponse(
            answer="请提出一个关于 Mnemo 的问题。",
            intent="unknown",
            source="fallback",
        )

    intent_router = _get_intent_router()
    route_result = intent_router.route(question)

    # Fixed reply shortcut — same for both LLM and V0 paths.
    if route_result.is_fixed_reply and route_result.fixed_reply_text:
        return AskResponse(
            answer=route_result.fixed_reply_text,
            intent=route_result.intent,
            source="fixed_reply",
        )

    # --- Try LLM path ---
    if await _check_provider():
        try:
            llm_response = await asyncio.wait_for(
                _try_llm_answer(question, route_result, _get_retriever()),
                timeout=45.0,
            )
            if llm_response is not None:
                return llm_response
        except asyncio.TimeoutError:
            logger.warning("LLM pipeline timed out — falling back to V0")
        except Exception:
            logger.exception("LLM pipeline error — falling back to V0")

    # --- V0 deterministic fallback chain ---
    handler = _get_fallback_handler()
    result = handler.handle(
        question=question,
        intent=route_result.intent,
        retriever=_get_retriever(),
        faq_matcher=_get_faq_matcher(),
        install_engine=_get_install_engine(),
        router=intent_router,
    )

    return AskResponse(
        answer=result.answer,
        intent=result.intent,
        commands=result.commands,
        model_used=result.model_used,
        source=result.source,
        cards_used=result.cards_used,
    )


@router.get("/status")
async def status() -> dict:
    """Return guide model status for the frontend StatusBar.

    Returns:
        dict with keys: ``enabled``, ``available``, ``provider``, ``model_name``.
    """
    config = MnemoConfig()
    gm = config.guide_model
    enabled = gm.get("enabled", False)
    available = False

    if enabled:
        provider = _get_provider()
        try:
            available = await asyncio.wait_for(provider.is_available(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            available = False

    return {
        "enabled": enabled,
        "available": available,
        "provider": gm.get("provider", "disabled"),
        "model_name": gm.get("model_name", ""),
    }
