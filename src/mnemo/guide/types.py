"""Core types used across the Mnemo Guide system."""

from typing import Literal, Optional
from dataclasses import dataclass, field


GuideIntent = Literal[
    "identity",
    "capability",
    "mnemo_overview",
    "install",
    "client_setup",
    "mcp_explain",
    "global_prompt_explain",
    "troubleshooting",
    "verify",
    "privacy_security",
    "command_template",
    "off_topic",
    "unknown",
]


@dataclass
class KnowledgeCard:
    """A single knowledge card from the guide knowledge pack.

    Represents one documented fact, procedure, FAQ entry, or template
    about Mnemo — install guides, client setup steps, troubleshooting
    advice, concept explanations, etc.
    """

    id: str
    type: str  # concept, install_guide, client_setup, troubleshooting, faq, security, command_template
    title: str
    summary: str
    tags: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    content: str = ""
    platforms: list[str] = field(default_factory=list)
    clients: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


@dataclass
class GuideResponse:
    """Answer returned by the guide system for a user question."""

    answer: str
    intent: str
    commands: list[dict] = field(default_factory=list)
    model_used: bool = False
    source: Literal[
        "fixed_reply", "knowledge_card", "faq", "install_template", "fallback"
    ] = "faq"
    cards_used: list[str] = field(default_factory=list)


@dataclass
class RouterResult:
    """Result of routing a user question through the intent detector."""

    intent: GuideIntent
    client: Optional[str] = None
    platform: Optional[str] = None
    is_fixed_reply: bool = False
    fixed_reply_text: Optional[str] = None
