from pathlib import Path

from face_mapping.knowledge_base import KnowledgeBase


def test_site_record_ontology_and_field_network(tmp_path):
    store = KnowledgeBase(tmp_path / "workbench", Path(__file__).resolve().parents[1])
    overview = store.overview()
    site = next(item for item in overview["bases"] if item["key"] == "site")
    assert site["records"] == 28
    assert site["instance_count"] == 29  # record-schema instance + 28 field instances
    assert site["relation_count"] >= 70
    graph = store.graph(limit=200)
    assert any(node.get("layer") == "ontology" for node in graph["nodes"])
    field_edges = [edge for edge in graph["edges"] if edge.get("layer") == "instance"
                   and edge.get("source", "").startswith("field:")]
    assert field_edges
    assert any(edge["relation"] == "supports" for edge in field_edges)
    assert any(edge["relation"] == "requires_evidence" for edge in field_edges)
