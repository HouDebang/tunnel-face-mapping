"""Portable, auditable vector index for project knowledge sources."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import os
import numpy as np

def _now():
    return datetime.now(timezone.utc).isoformat()

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except (OSError, ValueError, TypeError):
        return default

def _tokens(text):
    tokens = []
    for token in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_+.\-]+", str(text or "").lower()):
        if token[0] >= "\u4e00" and token[0] <= "\u9fff":
            tokens.append(token)
            # Chinese queries often omit spaces (for example
            # “花岗岩岩性判定”); retain short n-grams for fallback retrieval.
            for size in (2, 3, 4):
                tokens.extend(token[index:index + size] for index in range(max(0, len(token) - size + 1)))
        else:
            tokens.append(token)
    return list(dict.fromkeys(item for item in tokens if item))

class KnowledgeVectorStore:
    """Local NumPy cosine index. Neo4j remains the graph relationship store."""
    def __init__(self, workspace_root: Path, knowledge):
        self.workspace_root = Path(workspace_root)
        self.knowledge = knowledge
        self.root = self.workspace_root / ".lithology-judgment" / "knowledge"
        self.root.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.root / "vector_index.json"
        self.matrix_path = self.root / "vector_index.npz"

    @property
    def config_path(self):
        return self.workspace_root / ".lithology-judgment" / "providers.json"

    def _provider(self, config_id="default"):
        return _read_json(self.config_path, {}).get(config_id) or {}

    @staticmethod
    def _auth_headers(provider):
        key, style = provider.get("api_key", ""), str(provider.get("auth_style", "bearer"))
        if style == "header": return {"api-key": key, "Content-Type": "application/json"}
        if style == "api_key": return {"Authorization": "Api-Key " + key, "Content-Type": "application/json"}
        return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    @staticmethod
    def _opener(provider):
        proxy = str(provider.get("proxy") or "").strip()
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy else urllib.request.ProxyHandler({}))

    def documents(self):
        docs = []
        for record in self.knowledge.records("lithology", "", 400).get("records", []):
            profile = record.get("visual_profile") or {}
            aliases = "、".join(record.get("aliases") or []) or "无"
            text = (f"岩性名称：{record.get('standard_name', '')}；别名：{aliases}；岩类：{record.get('rock_group', '未分组')}；"
                    f"亚类：{record.get('subgroup', '—')}；父类：{record.get('parent_name', '—')}；"
                    f"视觉描述：{'、'.join(profile.get('descriptors') or [])}；识别要点：{profile.get('distinguishing', '—')}；"
                    f"来源：{record.get('basis', '项目岩性库')}。")
            docs.append({"id": f"lithology:{record.get('record_id')}", "kind": "lithology", "title": record.get("standard_name", "岩性记录"), "text": text, "source": record.get("basis", "项目岩性库"), "record_id": record.get("record_id")})
        for record in self.knowledge.records("site", "", 100).get("records", []):
            text = (f"记录表字段：{record.get('field', '')}；类别：{record.get('category', '—')}；可获取性：{record.get('obtainability', '—')}；"
                    f"说明：{record.get('note', '—')}；样表示例：{record.get('example_value', '—')}。")
            docs.append({"id": f"site:{record.get('record_id')}", "kind": "site_field", "title": record.get("field", "记录表字段"), "text": text, "source": "项目地质素描记录表", "record_id": record.get("record_id")})
        # The JSON knowledge files are the portable vector source of truth.
        # Avoid a network round trip while building the index; Neo4j remains
        # available to the graph page independently.
        previous_backend = os.environ.get("FACE_MAPPING_GRAPH_BACKEND")
        os.environ["FACE_MAPPING_GRAPH_BACKEND"] = "local_json"
        try:
            graph = self.knowledge.graph(limit=900)
        finally:
            if previous_backend is None: os.environ.pop("FACE_MAPPING_GRAPH_BACKEND", None)
            else: os.environ["FACE_MAPPING_GRAPH_BACKEND"] = previous_backend
        labels = {node.get("id"): node.get("label", node.get("id")) for node in graph.get("nodes", [])}
        for index, edge in enumerate(graph.get("edges", [])):
            if edge.get("source") not in labels or edge.get("target") not in labels: continue
            relation = edge.get("relation", "related_to")
            docs.append({"id": f"relation:{index}:{edge['source']}:{edge['target']}", "kind": "graph_relation", "title": f"{labels[edge['source']]} {relation} {labels[edge['target']]}", "text": f"知识图谱关系：{labels[edge['source']]} --{relation}--> {labels[edge['target']]}。", "source": "Neo4j / 项目知识图谱", "record_id": None})
        return docs

    def source_signature(self, docs=None):
        docs = docs if docs is not None else self.documents()
        site_doc = self.knowledge._document("site")
        lithology_doc = self.knowledge._document("lithology")
        refs = list(site_doc.get("source_refs") or []) + list(lithology_doc.get("source_refs") or [])
        payload = {"refs": refs, "documents": [(doc["id"], doc["text"]) for doc in docs]}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _choose_embedding_model(self, provider):
        configured = str(provider.get("embedding_model") or "").strip()
        if configured: return configured
        base = str(provider.get("base_url") or "").rstrip("/")
        if not base or not provider.get("api_key"): return ""
        request = urllib.request.Request(base + "/models", headers=self._auth_headers(provider), method="GET")
        with self._opener(provider).open(request, timeout=30) as response: payload = json.loads(response.read().decode("utf-8", "replace"))
        models = payload.get("data") if isinstance(payload, dict) else []
        ids = [item.get("id") for item in models if isinstance(item, dict) and item.get("id")]
        return next((model for model in ids if "embedding" in model.lower()), "")

    def _embed(self, texts, provider, model):
        base = str(provider.get("base_url") or "").rstrip("/")
        if not base or not provider.get("api_key") or not model: raise RuntimeError("embedding_provider_not_configured")
        rows = []
        for start in range(0, len(texts), 32):
            batch = texts[start:start + 32]
            request = urllib.request.Request(base + "/embeddings", data=json.dumps({"model": model, "input": batch}, ensure_ascii=False).encode(), headers=self._auth_headers(provider), method="POST")
            with self._opener(provider).open(request, timeout=90) as response: payload = json.loads(response.read().decode("utf-8", "replace"))
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(batch): raise RuntimeError("embedding_response_invalid")
            rows.extend(item.get("embedding") for item in sorted(data, key=lambda item: item.get("index", 0)))
        matrix = np.asarray(rows, dtype=np.float32)
        if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1]: raise RuntimeError("embedding_dimension_invalid")
        return matrix

    def _valid_metadata(self):
        meta = _read_json(self.metadata_path, {})
        if not meta or not self.matrix_path.is_file() or meta.get("source_signature") != self.source_signature(): return None
        return meta

    def status(self):
        meta = self._valid_metadata()
        if not meta: return {"status": "not_built", "mode": "lexical_fallback", "documents": len(self.documents())}
        return {"status": "ready", "mode": "embedding", "documents": meta.get("documents", 0), "dimension": meta.get("dimension"), "embedding_model": meta.get("embedding_model"), "source_signature": meta.get("source_signature"), "built_at": meta.get("built_at")}

    def rebuild(self, config_id="default"):
        docs, provider = self.documents(), self._provider(config_id)
        try:
            model, matrix = self._choose_embedding_model(provider), None
            matrix = self._embed([doc["text"] for doc in docs], provider, model)
            temporary_matrix, temporary_meta = self.matrix_path.with_suffix(".tmp.npz"), self.metadata_path.with_suffix(".tmp")
            np.savez_compressed(temporary_matrix, embeddings=matrix)
            meta = {"schema_version": "1.0", "source_signature": self.source_signature(docs), "embedding_model": model, "dimension": int(matrix.shape[1]), "documents": len(docs), "built_at": _now(), "records": docs}
            temporary_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary_matrix.replace(self.matrix_path); temporary_meta.replace(self.metadata_path)
            return self.status()
        except Exception as exc:
            return {"status": "failed", "mode": "lexical_fallback", "documents": len(docs), "message": f"{type(exc).__name__}: {exc}"}

    def _lexical(self, query, docs, limit):
        tokens = _tokens(query); scored = []
        for doc in docs:
            haystack = (doc.get("title", "") + " " + doc.get("text", "")).lower()
            score = sum(1.0 for token in tokens if token in haystack)
            if score: scored.append((score / max(len(tokens), 1), doc))
        scored.sort(key=lambda item: (-item[0], item[1].get("id", "")))
        return [{**doc, "score": round(float(score), 4)} for score, doc in scored[:limit]]

    def retrieve(self, query, limit=6, config_id="default"):
        limit = max(1, min(int(limit), 12)); docs, meta = self.documents(), self._valid_metadata()
        if meta:
            try:
                provider, model = self._provider(config_id), meta.get("embedding_model")
                query_vector = self._embed([query], provider, model)[0]
                matrix = np.asarray(np.load(self.matrix_path)["embeddings"], dtype=np.float32)
                denominator = np.linalg.norm(matrix, axis=1) * max(float(np.linalg.norm(query_vector)), 1e-12)
                scores = (matrix @ query_vector) / np.maximum(denominator, 1e-12)
                records, order = meta.get("records") or docs, np.argsort(-scores)[:limit]
                return {"mode": "embedding", "vector_model": model, "documents": [{**records[int(index)], "score": round(float(scores[int(index)]), 4)} for index in order]}
            except Exception: pass
        return {"mode": "lexical_fallback", "vector_model": None, "documents": self._lexical(query, docs, limit)}
