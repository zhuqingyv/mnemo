"""FAQ matcher for Mnemo Guide.

Matches user questions against FAQ knowledge cards using keyword overlap
scoring. Returns the best-matching card when the score exceeds a threshold.
"""

from __future__ import annotations

import re
from typing import Optional

from mnemo.guide.knowledge_pack import KnowledgePack
from mnemo.guide.types import KnowledgeCard


def _tokenize(text: str) -> set[str]:
    """Lowercase tokenize on word boundaries."""
    if not text:
        return set()
    return set(re.findall(r"\w+", text.lower()))


class FAQMatcher:
    """Match user questions to FAQ knowledge cards.

    Usage::

        pack = KnowledgePack.load()
        matcher = FAQMatcher(pack)
        card = matcher.match("Agent 没有记忆怎么办？")
    """

    # Minimum keyword overlap score before a match is returned.
    DEFAULT_THRESHOLD = 3

    def __init__(self, pack: KnowledgePack, threshold: int = DEFAULT_THRESHOLD) -> None:
        self.faq_cards = pack.get_faq_cards()
        self.threshold = threshold

    def match(self, question: str) -> Optional[KnowledgeCard]:
        """Find the best matching FAQ card, or None if no good match.

        Scoring is computed as the number of overlapping tokens between
        the question and the card's title, summary, and tags — weighted
        toward titles.
        """
        if not self.faq_cards or not question.strip():
            return None

        q_tokens = _tokenize(question)
        if not q_tokens:
            return None

        best_card: Optional[KnowledgeCard] = None
        best_score = 0

        for card in self.faq_cards:
            score = 0

            # Title match (weight 3)
            title_tokens = _tokenize(card.title)
            score += len(q_tokens & title_tokens) * 3

            # Summary match (weight 2)
            summary_tokens = _tokenize(card.summary)
            score += len(q_tokens & summary_tokens) * 2

            # Tag match (weight 4)
            tag_text = " ".join(card.tags)
            tag_tokens = _tokenize(tag_text)
            score += len(q_tokens & tag_tokens) * 4

            if score > best_score:
                best_score = score
                best_card = card

        if best_score >= self.threshold and best_card is not None:
            return best_card

        return None
