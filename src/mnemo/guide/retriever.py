"""Knowledge retriever for Mnemo Guide.

Wraps ``KnowledgePack`` and applies intent, client, and platform filters
before full-text search to return the most relevant cards for a question.
"""

from __future__ import annotations

from typing import Optional

from mnemo.guide.knowledge_pack import KnowledgePack
from mnemo.guide.types import KnowledgeCard


class KnowledgeRetriever:
    """Retrieve relevant knowledge cards for a user question.

    Applies intent, client, and platform filters before full-text
    search to maximize relevance.

    Usage::

        pack = KnowledgePack.load()
        retriever = KnowledgeRetriever(pack)
        cards = retriever.retrieve(
            "how to install on mac",
            intent="install",
            platform="macos",
        )
    """

    def __init__(self, pack: KnowledgePack) -> None:
        self.pack = pack

    def retrieve(
        self,
        question: str,
        intent: str,
        client: Optional[str] = None,
        platform: Optional[str] = None,
        top_k: int = 5,
    ) -> list[KnowledgeCard]:
        """Retrieve relevant knowledge cards for a question.

        Logic:
        1. Start with intent-filtered cards when available.
        2. If a client is known, boost cards that mention it.
        3. If a platform is known, boost cards that mention it.
        4. Full-text search the narrowed pool.
        5. Return top_k cards sorted by relevance.
        """
        # Step 1: intent-filtered candidate pool
        intent_cards = self.pack.get_cards_by_intent(intent)

        # If no intent-specific cards, fall back to all cards for search
        if not intent_cards:
            # Try a broader search: no intent filter
            results = self.pack.search(question, top_k=top_k)
            return self._sort_by_context(results, client, platform)

        # Step 2-3: score cards in the narrowed pool
        # Build a temporary mapping for scoring
        scored: list[tuple[KnowledgeCard, int]] = []
        for card in intent_cards:
            score = 0
            if client and client in [c.lower() for c in card.clients]:
                score += 50
            if platform and platform in [p.lower() for p in card.platforms]:
                score += 40
            scored.append((card, score))

        # Sort by context score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # If very few context-relevant cards, add search results
        candidates = [c for c, s in scored]

        # Also do a full-text search within the intent-filtered cards
        # to surface cards that match the question text
        search_results = self.pack.search(question, top_k=top_k * 2)
        # Filter: only keep cards in the intent pool
        intent_ids = {c.id for c in intent_cards}
        search_filtered = [c for c in search_results if c.id in intent_ids]

        # Merge: context-scored cards first, then search-matching cards not yet included
        seen_ids: set[str] = set()
        merged: list[KnowledgeCard] = []
        for c in candidates:
            if c.id not in seen_ids:
                seen_ids.add(c.id)
                merged.append(c)
        for c in search_filtered:
            if c.id not in seen_ids:
                seen_ids.add(c.id)
                merged.append(c)

        return merged[:top_k]

    def _sort_by_context(
        self,
        cards: list[KnowledgeCard],
        client: Optional[str],
        platform: Optional[str],
    ) -> list[KnowledgeCard]:
        """Sort cards by client/platform match, keeping original search order."""
        if not client and not platform:
            return cards

        def _score(card: KnowledgeCard) -> int:
            s = 0
            if client and client in [c.lower() for c in card.clients]:
                s += 50
            if platform and platform in [p.lower() for p in card.platforms]:
                s += 40
            return s

        scored = [(c, _score(c)) for c in cards]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored]
