from face_mapping.graph_store import Neo4jGraphStore


def test_neo4j_adapter_falls_back_without_configuration():
    store = Neo4jGraphStore("", "neo4j", "")
    status = store.status()
    assert status["configured"] is False
    assert status["available"] is False
    result = store.sync({"nodes": [], "edges": []})
    assert result["synced"] is False
