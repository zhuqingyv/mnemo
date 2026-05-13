"""CLI entry point for Mnemo Guide — used by Tauri to invoke the guide
backend as a subprocess without requiring an HTTP server.

Protocol (stdin/stdout JSON):
    echo '{"question": "怎么安装"}' | python3 -m mnemo.guide.cli

Returns JSON with fields: answer, intent, commands, source, cards_used.
"""

from __future__ import annotations

import json
import sys

from mnemo.guide.fallback import FallbackHandler
from mnemo.guide.faq_matcher import FAQMatcher
from mnemo.guide.install_templates import InstallTemplateEngine
from mnemo.guide.intent_router import IntentRouter
from mnemo.guide.knowledge_pack import KnowledgePack
from mnemo.guide.retriever import KnowledgeRetriever


def main() -> None:
    raw = sys.stdin.read()
    try:
        req = json.loads(raw)
    except json.JSONDecodeError:
        _write_error("输入格式错误，请传入 JSON: {\"question\": \"...\"}")
        return

    question = req.get("question", "").strip()
    if not question:
        _write_error("请提供一个问题。")
        return

    # Lazy init — same singletons as the HTTP router
    pack = KnowledgePack.load()
    intent_router = IntentRouter()
    faq_matcher = FAQMatcher(pack)
    retriever = KnowledgeRetriever(pack)
    install_engine = InstallTemplateEngine()
    fallback_handler = FallbackHandler()

    route_result = intent_router.route(question)

    # Fixed reply shortcut
    if route_result.is_fixed_reply and route_result.fixed_reply_text:
        _write_result(
            answer=route_result.fixed_reply_text,
            intent=route_result.intent,
            source="fixed_reply",
        )
        return

    # Full fallback chain
    result = fallback_handler.handle(
        question=question,
        intent=route_result.intent,
        retriever=retriever,
        faq_matcher=faq_matcher,
        install_engine=install_engine,
        router=intent_router,
    )

    _write_result(
        answer=result.answer,
        intent=result.intent,
        commands=result.commands,
        source=result.source,
        cards_used=result.cards_used,
    )


def _write_result(
    answer: str,
    intent: str,
    commands: list[dict] | None = None,
    source: str = "faq",
    cards_used: list[str] | None = None,
) -> None:
    result = {
        "answer": answer,
        "intent": intent,
        "commands": commands or [],
        "source": source,
        "cards_used": cards_used or [],
    }
    print(json.dumps(result, ensure_ascii=False))


def _write_error(message: str) -> None:
    result = {
        "answer": message,
        "intent": "unknown",
        "commands": [],
        "source": "error",
        "cards_used": [],
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
