"""Fallback handler chain for Mnemo Guide.

Routes a question through a chain of handlers, each more general than the
last, until one produces an answer. This is the V0 implementation with no
model — everything is deterministic.
"""

from __future__ import annotations

from mnemo.guide.faq_matcher import FAQMatcher
from mnemo.guide.fixed_replies import (
    IDENTITY_REPLY,
    OFF_TOPIC_REPLY,
)
from mnemo.guide.install_templates import InstallTemplateEngine
from mnemo.guide.intent_router import IntentRouter
from mnemo.guide.retriever import KnowledgeRetriever
from mnemo.guide.types import GuideResponse, KnowledgeCard


# ---------------------------------------------------------------------------
# Fallback messages
# ---------------------------------------------------------------------------

FALLBACK_MESSAGE = (
    "我不确定，这个问题没有出现在当前 Mnemo 说明书中。"
    "你可以查看官方文档 https://zhuqingyv.github.io/mnemo/"
)


def _card_to_answer(card: KnowledgeCard) -> str:
    """Convert a knowledge card into a plain-text answer."""
    lines: list[str] = []

    lines.append(f"**{card.title}**\n")
    lines.append(card.summary)

    if card.content:
        lines.append("")
        lines.append(card.content)

    if card.steps:
        lines.append("")
        lines.append("**步骤：**")
        for i, step in enumerate(card.steps, 1):
            lines.append(f"{i}. {step}")

    if card.commands:
        lines.append("")
        lines.append("**命令：**")
        for cmd in card.commands:
            desc = cmd.get("description", "")
            command = cmd.get("command", "")
            if desc:
                lines.append(f"- {desc}: `{command}`")
            else:
                lines.append(f"- `{command}`")

    if card.warnings:
        lines.append("")
        lines.append("**注意：**")
        for w in card.warnings:
            lines.append(f"- {w}")

    if card.related:
        lines.append("")
        lines.append("**相关主题：**")
        for r in card.related:
            lines.append(f"- {r}")

    return "\n".join(lines)


class FallbackHandler:
    """Handle a question through the deterministic fallback chain.

    Chain order (V0 — no model):
    1. Fixed reply (identity / off-topic / URL).
    2. FAQ matcher — keyword-overlap match against FAQ cards.
    3. Knowledge retriever — intent-filtered + full-text search.
    4. Install template engine — platform/client-specific commands.
    5. Generic fallback message.
    """

    def handle(
        self,
        question: str,
        intent: str,
        retriever: KnowledgeRetriever,
        faq_matcher: FAQMatcher,
        install_engine: InstallTemplateEngine,
        router: IntentRouter,
    ) -> GuideResponse:
        """Run the fallback chain and return the best answer."""

        # Re-route to pick up the full RouterResult (for client/platform)
        route_result = router.route(question)

        # Step 1: Fixed reply
        if route_result.is_fixed_reply and route_result.fixed_reply_text:
            return GuideResponse(
                answer=route_result.fixed_reply_text,
                intent=route_result.intent,
                source="fixed_reply",
            )

        # Step 2: FAQ matcher
        faq_card = faq_matcher.match(question)
        if faq_card is not None:
            return GuideResponse(
                answer=_card_to_answer(faq_card),
                intent=intent,
                source="faq",
                cards_used=[faq_card.id],
            )

        # Step 3: Knowledge retriever
        cards = retriever.retrieve(
            question=question,
            intent=route_result.intent,
            client=route_result.client,
            platform=route_result.platform,
            top_k=5,
        )
        if cards:
            top_card = cards[0]
            return GuideResponse(
                answer=_card_to_answer(top_card),
                intent=route_result.intent,
                source="knowledge_card",
                cards_used=[c.id for c in cards[:3]],
            )

        # Step 4: Install template engine
        template = install_engine.generate(
            platform=route_result.platform or "unknown",
            client=route_result.client or "unknown",
            intent=route_result.intent,
        )
        if template and template.get("steps"):
            answer_lines = list(template["steps"])
            if template.get("commands"):
                answer_lines.append("")
                answer_lines.append("**命令：**")
                for cmd in template["commands"]:
                    desc = cmd.get("description", "")
                    command = cmd.get("command", "")
                    if desc:
                        answer_lines.append(f"- {desc}: `{command}`")
                    else:
                        answer_lines.append(f"- `{command}`")
            if template.get("note"):
                answer_lines.append("")
                answer_lines.append(f"*{template['note']}*")

            commands_list = template.get("commands", [])
            return GuideResponse(
                answer="\n".join(answer_lines),
                intent=route_result.intent,
                source="install_template",
                commands=commands_list,
            )

        # Step 5: Generic fallback
        return GuideResponse(
            answer=FALLBACK_MESSAGE,
            intent=route_result.intent,
            source="fallback",
        )
