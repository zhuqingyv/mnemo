"""Knowledge pack loader for Mnemo Guide.

Loads JSON knowledge cards from ``data/guide_knowledge/``, parses them into
``KnowledgeCard`` objects, and builds a lightweight keyword-based search
index that works without external dependencies.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from mnemo.guide.types import KnowledgeCard


def _get_data_dir() -> Path:
    """Locate ``data/guide_knowledge/`` relative to project root.

    Tries several candidate paths so the pack works under both editable
    installs and binary distributions.
    """
    candidates = [
        Path(__file__).parent.parent.parent.parent / "data" / "guide_knowledge",
        Path.cwd() / "data" / "guide_knowledge",
        Path(os.environ.get("MNEMO_DATA_DIR", "")) / "guide_knowledge",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    raise FileNotFoundError("Cannot find data/guide_knowledge/ directory")


def _tokenize(text: str) -> set[str]:
    """Basic tokenization: lowercase, split on non-alphanumeric boundaries."""
    if not text:
        return set()
    return set(re.findall(r"\w+", text.lower()))


def _card_to_search_text(card: KnowledgeCard) -> str:
    """Flatten a card into a single string for full-text indexing."""
    parts = [
        card.title,
        card.summary,
        card.content,
        " ".join(card.tags),
        " ".join(card.intents),
        " ".join(card.platforms),
        " ".join(card.clients),
    ]
    return " ".join(p for p in parts if p)


class KnowledgePack:
    """Singleton loader and search index for guide knowledge cards.

    Usage::

        pack = KnowledgePack.load()
        results = pack.search("how to install", top_k=5)
        faqs = pack.get_faq_cards()
        cards = pack.get_cards_by_intent("install")
    """

    _instance: Optional["KnowledgePack"] = None

    def __init__(self, cards: list[KnowledgeCard]) -> None:
        self._cards: dict[str, KnowledgeCard] = {}
        self._by_type: dict[str, list[KnowledgeCard]] = {}
        self._by_intent: dict[str, list[KnowledgeCard]] = {}

        for card in cards:
            self._cards[card.id] = card

            t = card.type
            if t not in self._by_type:
                self._by_type[t] = []
            self._by_type[t].append(card)

            for intent in card.intents:
                if intent not in self._by_intent:
                    self._by_intent[intent] = []
                self._by_intent[intent].append(card)

        # Build search index: {word: [card_id, ...]}
        self._index: dict[str, set[str]] = {}
        for card_id, card in self._cards.items():
            tokens = _tokenize(_card_to_search_text(card))
            for token in tokens:
                if token not in self._index:
                    self._index[token] = set()
                self._index[token].add(card_id)

    @classmethod
    def load(cls) -> "KnowledgePack":
        """Load (or return cached) the knowledge pack from disk."""
        if cls._instance is not None:
            return cls._instance

        data_dir = _get_data_dir()
        cards: list[KnowledgeCard] = []

        for file_path in sorted(data_dir.glob("*.json")):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                # Skip malformed files; log a warning but keep going.
                print(f"[mnemo.guide] WARNING: skipping {file_path}: {exc}")
                continue

            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = [data]
            else:
                continue

            for item in items:
                try:
                    cards.append(_parse_card(item))
                except Exception as exc:
                    print(
                        f"[mnemo.guide] WARNING: skipping card in {file_path}: {exc}"
                    )
                    continue

        cls._instance = cls(cards)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear the singleton (useful for testing)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[KnowledgeCard]:
        """Full-text search returning the top-k most relevant cards.

        Scoring weights:
            - title match  → weight 10
            - summary match → weight 5
            - content match → weight 1
            - tag match     → weight 8
            - intent match  → weight 3
        """
        if not query or not self._cards:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        candidates: set[str] = set()
        for token in query_tokens:
            matched = self._index.get(token, set())
            candidates.update(matched)

        if not candidates:
            return []

        scores: dict[str, int] = {}
        for card_id in candidates:
            card = self._cards[card_id]
            score = 0

            title_tokens = _tokenize(card.title)
            score += sum(10 for t in query_tokens if t in title_tokens)

            summary_tokens = _tokenize(card.summary)
            score += sum(5 for t in query_tokens if t in summary_tokens)

            content_tokens = _tokenize(card.content)
            score += sum(1 for t in query_tokens if t in content_tokens)

            tag_text = " ".join(card.tags).lower()
            score += sum(8 for t in query_tokens if t in _tokenize(tag_text))

            intent_text = " ".join(card.intents).lower()
            score += sum(3 for t in query_tokens if t in _tokenize(intent_text))

            scores[card_id] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [self._cards[card_id] for card_id, _ in ranked[:top_k]]

    def get_cards_by_intent(self, intent: str) -> list[KnowledgeCard]:
        """Return all cards tagged with a given intent."""
        return list(self._by_intent.get(intent, []))

    def get_faq_cards(self) -> list[KnowledgeCard]:
        """Return all cards whose type is ``"faq"``."""
        return list(self._by_type.get("faq", []))

    def get_card(self, card_id: str) -> Optional[KnowledgeCard]:
        """Look up a single card by its id."""
        return self._cards.get(card_id)

    def get_cards_by_type(self, card_type: str) -> list[KnowledgeCard]:
        """Return all cards of a given type."""
        return list(self._by_type.get(card_type, []))

    @property
    def card_count(self) -> int:
        """Total number of loaded knowledge cards."""
        return len(self._cards)


def _parse_card(data: dict) -> KnowledgeCard:
    """Parse a JSON dict into a KnowledgeCard, filling in defaults."""
    return KnowledgeCard(
        id=str(data.get("id", "")),
        type=data.get("type", "concept"),
        title=data.get("title", ""),
        summary=data.get("summary", ""),
        tags=data.get("tags", []),
        intents=data.get("intents", []),
        content=data.get("content", ""),
        platforms=data.get("platforms", []),
        clients=data.get("clients", []),
        steps=data.get("steps", []),
        commands=data.get("commands", []),
        warnings=data.get("warnings", []),
        related=data.get("related", []),
    )
