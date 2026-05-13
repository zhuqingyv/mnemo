"""Prompt builder for Mnemo Guide.

Assembles the system prompt, question context, and knowledge cards into
a single prompt string ready for the model provider. Used when a real
model is available (post-V0).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from mnemo.guide.types import KnowledgeCard


# ---------------------------------------------------------------------------
# Default system prompt (inlined for V0 — can load from file later)
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = """你是 Mnemo Guide，一个本地说明书助手。
你的职责是回答关于 Mnemo（本地 AI 记忆层）的安装、配置、MCP 接入和使用问题。

回答规则：
1. 只能回答 Mnemo 相关问题。如果问题超出范围，礼貌告知用户。
2. 回答必须基于提供的上下文知识卡片，不要编造信息。
3. 如果有安装命令或配置步骤，请完整列出。
4. 不要声称你有联网能力、shell 访问或能读取私人记忆。
5. 保持回答简洁、准确、有帮助。
"""

DEFAULT_ANSWER_TEMPLATE = """## 用户问题
{question}

## 用户意图
{intent}

## 相关上下文
{context}

请基于以上上下文回答用户的问题。如果上下文不足以回答，请诚实告知。"""


class PromptBuilder:
    """Build model prompts for the Mnemo Guide.

    Usage::

        builder = PromptBuilder()
        prompt = builder.build_answer_prompt(
            question="如何安装 Mnemo？",
            intent="install",
            context_cards=[card1, card2],
        )
    """

    def __init__(self, system_prompt_path: Optional[str] = None) -> None:
        self._system_prompt = DEFAULT_SYSTEM_PROMPT

        if system_prompt_path:
            path = Path(system_prompt_path)
            if path.is_file():
                self._system_prompt = path.read_text(encoding="utf-8")

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def build_answer_prompt(
        self,
        question: str,
        intent: str,
        context_cards: list[KnowledgeCard],
    ) -> str:
        """Build the complete answer prompt with question, intent, and context.

        Format:
            system_prompt + answer_template filled with question, intent, context.
        """
        context_text = self._format_cards(context_cards)

        user_prompt = DEFAULT_ANSWER_TEMPLATE.format(
            question=question,
            intent=intent,
            context=context_text,
        )

        return f"{self._system_prompt}\n\n{user_prompt}"

    def _format_cards(self, cards: list[KnowledgeCard]) -> str:
        """Format a list of knowledge cards into a text block."""
        if not cards:
            return "（无相关上下文卡片）"

        parts: list[str] = []
        for i, card in enumerate(cards, 1):
            block = f"### 卡片 {i}: {card.title}\n"
            block += f"类型: {card.type}\n"
            block += f"摘要: {card.summary}\n"

            if card.content:
                block += f"\n{card.content}\n"

            if card.steps:
                block += "\n步骤:\n"
                for j, step in enumerate(card.steps, 1):
                    block += f"  {j}. {step}\n"

            if card.commands:
                block += "\n命令:\n"
                for cmd in card.commands:
                    desc = cmd.get("description", "")
                    command = cmd.get("command", "")
                    if desc:
                        block += f"  - {desc}: `{command}`\n"
                    else:
                        block += f"  - `{command}`\n"

            if card.warnings:
                block += "\n⚠ 注意:\n"
                for w in card.warnings:
                    block += f"  - {w}\n"

            parts.append(block)

        return "\n---\n".join(parts)
