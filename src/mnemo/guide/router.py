"""FastAPI router for the Mnemo Guide API.

Exposes ``POST /api/v1/guide/ask`` — accepts a user question about Mnemo
and returns a deterministic answer from the knowledge pack. V0: no model.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from mnemo.guide.fallback import FallbackHandler
from mnemo.guide.faq_matcher import FAQMatcher
from mnemo.guide.install_templates import InstallTemplateEngine
from mnemo.guide.intent_router import IntentRouter
from mnemo.guide.knowledge_pack import KnowledgePack
from mnemo.guide.retriever import KnowledgeRetriever

from pydantic import BaseModel, Field

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
    model_used: bool = Field(default=False, description="是否使用了模型（V0 始终为 False）")
    source: str = Field(default="faq", description="回答来源")
    cards_used: list[str] = Field(
        default_factory=list, description="引用的知识卡片 ID"
    )


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


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["guide"])


@router.post("/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    """Answer a Mnemo-related question.

    For V0 there is NO model — everything goes through the deterministic
    fallback chain: fixed replies → FAQ matcher → knowledge retriever →
    install template engine → generic fallback.
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

    # If the router says this is a fixed reply, return immediately.
    if route_result.is_fixed_reply and route_result.fixed_reply_text:
        return AskResponse(
            answer=route_result.fixed_reply_text,
            intent=route_result.intent,
            source="fixed_reply",
        )

    # Otherwise, run through the full fallback chain.
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
