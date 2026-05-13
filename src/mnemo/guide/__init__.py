"""Mnemo Guide — local offline manual assistant for Mnemo-related questions.

Answers ONLY Mnemo-related questions using a public knowledge pack.
Provides a FastAPI router at ``/api/v1/guide/ask``.
"""

from mnemo.guide.router import router
from mnemo.guide.knowledge_pack import KnowledgePack
from mnemo.guide.intent_router import IntentRouter
from mnemo.guide.fixed_replies import IDENTITY_REPLY, OFF_TOPIC_REPLY
from mnemo.guide.faq_matcher import FAQMatcher
from mnemo.guide.retriever import KnowledgeRetriever
from mnemo.guide.install_templates import InstallTemplateEngine
from mnemo.guide.fallback import FallbackHandler

__all__ = [
    "router",
    "KnowledgePack",
    "IntentRouter",
    "IDENTITY_REPLY",
    "OFF_TOPIC_REPLY",
    "FAQMatcher",
    "KnowledgeRetriever",
    "InstallTemplateEngine",
    "FallbackHandler",
]
