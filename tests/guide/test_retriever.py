"""Tests for the Knowledge Retriever and Knowledge Pack."""

import pytest
from mnemo.guide.knowledge_pack import KnowledgePack
from mnemo.guide.retriever import KnowledgeRetriever


@pytest.fixture(autouse=True)
def reset_pack() -> None:
    """Reset the singleton between tests."""
    KnowledgePack.reset()


@pytest.fixture
def pack() -> KnowledgePack:
    return KnowledgePack.load()


@pytest.fixture
def retriever(pack: KnowledgePack) -> KnowledgeRetriever:
    return KnowledgeRetriever(pack)


class TestKnowledgePack:
    def test_loads_cards(self, pack: KnowledgePack) -> None:
        assert pack.card_count > 0

    def test_has_faq_cards(self, pack: KnowledgePack) -> None:
        faqs = pack.get_faq_cards()
        assert len(faqs) > 0
        assert all(c.type == "faq" for c in faqs)

    def test_has_concept_cards(self, pack: KnowledgePack) -> None:
        concepts = pack.get_cards_by_type("concept")
        assert len(concepts) > 0

    def test_has_install_cards(self, pack: KnowledgePack) -> None:
        install = pack.get_cards_by_type("install_guide")
        assert len(install) > 0

    def test_search_returns_results(self, pack: KnowledgePack) -> None:
        results = pack.search("Mnemo")
        assert len(results) > 0

    def test_search_ranking(self, pack: KnowledgePack) -> None:
        results = pack.search("安装")
        # First result should be most relevant to installation
        if results:
            first_title = results[0].title.lower()
            has_install = "安装" in first_title
            has_verify = "验证" in first_title
            assert has_install or has_verify

    def test_get_card_by_id(self, pack: KnowledgePack) -> None:
        card = pack.get_card("mnemo-overview")
        assert card is not None
        assert card.title == "Mnemo 是什么"

    def test_get_card_nonexistent(self, pack: KnowledgePack) -> None:
        card = pack.get_card("nonexistent-id")
        assert card is None

    def test_get_cards_by_intent(self, pack: KnowledgePack) -> None:
        cards = pack.get_cards_by_intent("troubleshooting")
        assert len(cards) > 0
        assert all("troubleshooting" in c.intents for c in cards)

    def test_search_empty_query(self, pack: KnowledgePack) -> None:
        results = pack.search("")
        assert results == []

    def test_search_no_match(self, pack: KnowledgePack) -> None:
        results = pack.search("zzxxyyww")
        assert results == []

    def test_card_types_present(self, pack: KnowledgePack) -> None:
        expected_types = ["concept", "install_guide", "client_setup", "troubleshooting", "faq", "security", "command_template"]
        for t in expected_types:
            cards = pack.get_cards_by_type(t)
            assert len(cards) > 0, f"No cards of type {t} found"


class TestKnowledgeRetriever:
    def test_retrieve_by_intent(self, retriever: KnowledgeRetriever) -> None:
        cards = retriever.retrieve("怎么安装", intent="install")
        assert len(cards) > 0

    def test_retrieve_by_client(self, retriever: KnowledgeRetriever) -> None:
        cards = retriever.retrieve("Claude Code", intent="client_setup", client="claude_code")
        assert len(cards) > 0

    def test_retrieve_by_platform(self, retriever: KnowledgeRetriever) -> None:
        cards = retriever.retrieve("Mac", intent="install", platform="macos")
        assert len(cards) > 0

    def test_retrieve_top_k(self, retriever: KnowledgeRetriever) -> None:
        cards = retriever.retrieve("Mnemo", intent="mnemo_overview", top_k=3)
        assert len(cards) <= 3

    def test_retrieve_empty_query(self, retriever: KnowledgeRetriever) -> None:
        cards = retriever.retrieve("", intent="unknown")
        assert cards == []

    def test_retrieve_no_relevant(self, retriever: KnowledgeRetriever) -> None:
        cards = retriever.retrieve("zzxxyyww", intent="unknown")
        assert cards == []
