"""Pure-helper tests for the embeddings module. No network."""

from embeddings.embedder import node_embedding_text, text_hash, to_pgvector


def test_node_embedding_text_composes() -> None:
    node = {
        "node_type": "Event",
        "label": "Konzert X",
        "properties": {
            "description": "Tolles Konzert",
            "categories": ["Musik", "Hörde"],
            "event_type": "public_event",
        },
    }
    t = node_embedding_text(node)
    for token in ("Event", "Konzert X", "Tolles Konzert", "Musik", "Hörde", "public_event"):
        assert token in t


def test_node_embedding_text_skips_missing() -> None:
    assert node_embedding_text({"node_type": "POI", "label": "Apotheke", "properties": {}}) == "POI | Apotheke"


def test_text_hash_stable_and_distinct() -> None:
    assert text_hash("a") == text_hash("a")
    assert text_hash("a") != text_hash("b")


def test_to_pgvector() -> None:
    assert to_pgvector([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"
