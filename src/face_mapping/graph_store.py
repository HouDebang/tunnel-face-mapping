"""Optional Neo4j graph storage for the project knowledge graph.

The application keeps the JSON graph as the portable source of truth. When
Neo4j is configured and the ``neo4j`` driver is installed, this adapter can
materialize the same nodes and relationships in Neo4j for native traversal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        self.uri = uri.strip()
        self.user = user.strip()
        self.password = password
        self.database = database.strip() or "neo4j"
        self._driver = None
        self._error = None

    @classmethod
    def from_env(cls):
        dotenv = {}
        for candidate in (Path.cwd() / ".env.neo4j", Path(__file__).resolve().parents[2] / ".env.neo4j"):
            if not candidate.is_file():
                continue
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    dotenv[key.strip()] = value.strip().strip('"').strip("'")
            except OSError:
                pass
        def value(name, default=""):
            return os.environ.get(name) or dotenv.get(name, default)
        if not os.environ.get("FACE_MAPPING_GRAPH_BACKEND") and dotenv.get("FACE_MAPPING_GRAPH_BACKEND"):
            os.environ["FACE_MAPPING_GRAPH_BACKEND"] = dotenv["FACE_MAPPING_GRAPH_BACKEND"]
        return cls(
            value("FACE_MAPPING_NEO4J_URI"),
            value("FACE_MAPPING_NEO4J_USER", "neo4j"),
            value("FACE_MAPPING_NEO4J_PASSWORD"),
            value("FACE_MAPPING_NEO4J_DATABASE", "neo4j"),
        )

    @property
    def configured(self):
        return bool(self.uri and self.user and self.password)

    def _load_driver(self):
        if self._driver is not None:
            return self._driver
        if not self.configured:
            return None
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            self._error = "neo4j Python driver is not installed; install the optional neo4j dependency."
            return None
        try:
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self._driver.verify_connectivity()
            return self._driver
        except Exception as exc:  # driver errors vary by server/version
            self._error = f"{type(exc).__name__}: {exc}"
            self._driver = None
            return None

    def status(self):
        driver = self._load_driver()
        if not self.configured:
            return {"backend": "local", "configured": False, "available": False,
                    "message": "未配置 Neo4j；当前使用本地 JSON 图谱。"}
        if driver is None:
            return {"backend": "neo4j", "configured": True, "available": False,
                    "uri": self.uri, "database": self.database, "message": self._error or "Neo4j 不可用。"}
        try:
            with driver.session(database=self.database) as session:
                counts = session.run(
                    "MATCH (n:FaceMappingNode) WITH count(n) AS nodes "
                    "OPTIONAL MATCH ()-[r:FACE_MAPPING_RELATION]->() "
                    "RETURN nodes, count(r) AS edges"
                ).single()
            return {"backend": "neo4j", "configured": True, "available": True,
                    "uri": self.uri, "database": self.database,
                    "nodes": int(counts["nodes"]), "edges": int(counts["edges"]),
                    "message": "Neo4j 已连接。"}
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            return {"backend": "neo4j", "configured": True, "available": False,
                    "uri": self.uri, "database": self.database, "message": self._error}

    @staticmethod
    def _properties(node: dict[str, Any]):
        props = {"id": str(node.get("id")), "label": str(node.get("label", "")),
                 "node_type": str(node.get("type", "entity")),
                 "layer": str(node.get("layer", "instance"))}
        for key in ("kind", "group", "record_id", "class_id", "version", "parent"):
            if node.get(key) is not None and isinstance(node.get(key), (str, int, float, bool)):
                props[key] = node[key]
        props["payload_json"] = json.dumps(node, ensure_ascii=False, allow_nan=False)
        return props

    def sync(self, graph: dict[str, Any], replace: bool = True):
        driver = self._load_driver()
        if driver is None:
            return self.status() | {"synced": False}
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        try:
            with driver.session(database=self.database) as session:
                if replace:
                    session.run("MATCH (n:FaceMappingNode) DETACH DELETE n").consume()
                session.run(
                    "CREATE CONSTRAINT face_mapping_node_id IF NOT EXISTS FOR (n:FaceMappingNode) REQUIRE n.id IS UNIQUE"
                ).consume()
                node_rows = [{"id": str(node.get("id")), "props": self._properties(node)} for node in nodes]
                session.run(
                    "UNWIND $rows AS row MERGE (n:FaceMappingNode {id: row.id}) SET n += row.props",
                    rows=node_rows
                ).consume()
                edge_rows = []
                for edge in edges:
                    source, target = edge.get("source"), edge.get("target")
                    if not source or not target:
                        continue
                    predicate = str(edge.get("relation") or edge.get("predicate") or "related_to")
                    edge_rows.append({"source": str(source), "target": str(target),
                                      "predicate": predicate, "layer": str(edge.get("layer", "instance")),
                                      "label": predicate})
                session.run(
                    "UNWIND $rows AS row "
                    "MATCH (a:FaceMappingNode {id: row.source}), (b:FaceMappingNode {id: row.target}) "
                    "MERGE (a)-[r:FACE_MAPPING_RELATION {predicate: row.predicate, layer: row.layer}]->(b) "
                    "SET r.label = row.label",
                    rows=edge_rows
                ).consume()
            return self.status() | {"synced": True, "source_nodes": len(nodes), "source_edges": len(edges)}
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            return self.status() | {"synced": False, "message": self._error}

    def fetch(self, limit: int = 900):
        """Read the materialized graph back as the frontend node/edge shape."""
        driver = self._load_driver()
        if driver is None:
            return None
        try:
            with driver.session(database=self.database) as session:
                node_rows = session.run(
                    "MATCH (n:FaceMappingNode) RETURN n ORDER BY n.id LIMIT $limit", limit=int(limit)
                )
                nodes = []
                for row in node_rows:
                    props = dict(row["n"])
                    payload = props.pop("payload_json", None)
                    try:
                        node = json.loads(payload) if payload else props
                    except (TypeError, ValueError):
                        node = props
                    nodes.append(node)
                edge_rows = session.run(
                    "MATCH (a:FaceMappingNode)-[r:FACE_MAPPING_RELATION]->(b:FaceMappingNode) "
                    "RETURN a.id AS source, b.id AS target, r.predicate AS relation, r.layer AS layer"
                )
                edges = [{"source": row["source"], "target": row["target"],
                          "relation": row["relation"], "layer": row["layer"]} for row in edge_rows]
            return {"query": "", "nodes": nodes, "edges": edges, "backend": "neo4j"}
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            return None

    def close(self):
        if self._driver is not None:
            self._driver.close()
            self._driver = None
