"""Scene-aware project knowledge chat backed by retrieval and the configured LLM."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .knowledge_base import KnowledgeBase
from .vector_store import KnowledgeVectorStore
from .workspace import WorkspaceError


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except (OSError, ValueError, TypeError):
        return default


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=6, ge=1, le=12)
    provider_config_id: str = Field(default="default", pattern=r"^[A-Za-z0-9_.-]{1,64}$")


class TunnelChatStore:
    def __init__(self, workspace, project_root: Path):
        self.workspace = workspace
        self.project_root = project_root
        self.knowledge = KnowledgeBase(workspace.root, project_root)
        self.vectors = KnowledgeVectorStore(workspace.root, self.knowledge)
        self.root = workspace.root / ".tunnel-scenes"

    def _scene_path(self, scene_id):
        if not re.fullmatch(r"[0-9a-f]{32}", scene_id):
            raise WorkspaceError("场景或对话不存在", 404)
        path = (self.root / scene_id).resolve()
        if path.parent != self.root.resolve() or not (path / "scene.json").is_file():
            raise WorkspaceError("隧道场景不存在", 404)
        return path

    def _scene(self, scene_id):
        return _read_json(self._scene_path(scene_id) / "scene.json", {})

    def _chat_path(self, scene_id):
        return self._scene_path(scene_id) / "chat.json"

    def history(self, scene_id):
        self._scene(scene_id)
        payload = _read_json(self._chat_path(scene_id), {"schema_version": "1.0", "scene_id": scene_id, "messages": []})
        messages = payload.get("messages") if isinstance(payload, dict) else []
        return {"scene_id": scene_id, "messages": messages if isinstance(messages, list) else []}

    def clear_history(self, scene_id):
        self._scene(scene_id)
        self._chat_path(scene_id).write_text(json.dumps({"schema_version": "1.0", "scene_id": scene_id, "messages": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.history(scene_id)

    def _save_turn(self, scene_id, user_message, assistant_message):
        payload = self.history(scene_id)
        payload["messages"] = (payload["messages"] + [user_message, assistant_message])[-100:]
        self._chat_path(scene_id).write_text(json.dumps({"schema_version": "1.0", **payload}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _task_context(self, task_id):
        try:
            task = self.workspace.get_task(task_id)
        except Exception:
            return {"task_id": task_id, "status": "unavailable"}
        stages = task.get("stages") or {}
        context = {"task_id": task_id, "filename": task.get("filename"),
                   "image_size": [task.get("width"), task.get("height")],
                   "stages": {key: value.get("status") for key, value in stages.items() if isinstance(value, dict)}}
        judgment_path = self.workspace.root / ".lithology-judgment" / "tasks" / task_id / "state.json"
        judgment = _read_json(judgment_path, {})
        if judgment:
            regions = []
            for region_id, entry in (judgment.get("regions") or {}).items():
                final = entry.get("final_judgment") or {}
                regions.append({"region_id": region_id,
                                "candidates": [item.get("lithology") or item.get("label") for item in (entry.get("candidates") or [])[:5]],
                                "final": final.get("final_lithology") or final.get("category") or "unknown",
                                "status": final.get("status") or entry.get("llm_status") or "unavailable",
                                "needs_review": final.get("needs_review", True)})
            context["lithology_judgment"] = regions
        return context

    def scene_context(self, scene_id):
        scene = self._scene(scene_id)
        sections = []
        tasks = []
        for section in scene.get("sections") or []:
            source = section.get("source") or {}
            sections.append({"id": section.get("id"), "label": section.get("label"),
                             "chainage_m": section.get("chainage_m"), "visible": section.get("visible", True),
                             "source_kind": source.get("kind"), "task_id": source.get("task_id"),
                             "execution_id": source.get("execution_id")})
            if source.get("task_id") and source["task_id"] not in tasks:
                tasks.append(source["task_id"])
        geology = scene.get("geology") or {}
        return {"scene": {"id": scene.get("id"), "name": scene.get("name"),
                           "chainage_prefix": scene.get("chainage_prefix"),
                           "start_chainage_m": scene.get("start_chainage_m"),
                           "end_chainage_m": scene.get("end_chainage_m"),
                           "illustrative": scene.get("illustrative", True)},
                "sections": sections,
                "geology_connections": [{"kind": item.get("kind"), "method": item.get("method"),
                                         "from_section_id": item.get("from_section_id"), "to_section_id": item.get("to_section_id"),
                                         "from_object_id": item.get("from_object_id"), "to_object_id": item.get("to_object_id")}
                                        for item in (geology.get("connections") or [])],
                "task_results": [self._task_context(task_id) for task_id in tasks]}

    def _provider(self, config_id):
        return _read_json(self.workspace.root / ".lithology-judgment" / "providers.json", {}).get(config_id) or {}

    @staticmethod
    def _auth_headers(provider):
        key, style = provider.get("api_key", ""), str(provider.get("auth_style", "bearer"))
        if style == "header": return {"api-key": key, "Content-Type": "application/json"}
        if style == "api_key": return {"Authorization": "Api-Key " + key, "Content-Type": "application/json"}
        return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    @staticmethod
    def _opener(provider):
        proxy = str(provider.get("proxy") or "").strip()
        import urllib.request
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy else urllib.request.ProxyHandler({}))

    @staticmethod
    def _extract_json(text):
        if not text: return None
        cleaned = str(text).strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"): cleaned = cleaned[4:]
        try: return json.loads(cleaned)
        except ValueError: pass
        start = cleaned.find("{")
        if start < 0: return None
        depth = 0
        for index in range(start, len(cleaned)):
            if cleaned[index] == "{": depth += 1
            elif cleaned[index] == "}":
                depth -= 1
                if depth == 0:
                    try: return json.loads(cleaned[start:index + 1])
                    except ValueError: return None
        return None

    def _call_model(self, provider, query, context, evidence, history):
        base, key, model = str(provider.get("base_url", "")).rstrip("/"), provider.get("api_key", ""), provider.get("model", "")
        if not base or not key or not model: return None, "provider_not_configured"
        evidence_text = "\n".join(f"[{item['id']}] {item['title']}：{item['text']}" for item in evidence)
        system = ("你是掌子面地质素描项目知识助手。只能依据给定的当前场景和知识证据回答。"
                  "必须区分图像候选、算法结果、知识库记录和已验证工程属性；证据不足时明确说不确定，不能编造。"
                  "请用中文回答，并在回答中引用证据编号。")
        user = json.dumps({"question": query, "current_scene": context, "knowledge_evidence": evidence_text}, ensure_ascii=False)
        messages = [{"role": "system", "content": system}, *history[-8:], {"role": "user", "content": user}]
        payload = {"model": model, "temperature": provider.get("temperature", .1), "top_p": provider.get("top_p", .8), "max_tokens": provider.get("max_tokens", 1200), "stream": False, "messages": messages, "response_format": {"type": "json_object"}}
        if provider.get("reasoning_effort"): payload["reasoning_effort"] = provider["reasoning_effort"]
        request = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode(), headers=self._auth_headers(provider), method="POST")
        try:
            with self._opener(provider).open(request, timeout=90) as response: data = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code not in {400, 422}: return None, f"provider_call_failed: HTTP {exc.code}"
            payload.pop("response_format", None)
            request = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode(), headers=self._auth_headers(provider), method="POST")
            try:
                with self._opener(provider).open(request, timeout=90) as response: data = json.loads(response.read().decode("utf-8", "replace"))
            except Exception as retry_error:
                return None, f"provider_call_failed: {type(retry_error).__name__}"
        except Exception as exc:
            return None, f"provider_call_failed: {type(exc).__name__}"
        message = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        parsed = self._extract_json(message)
        if isinstance(parsed, dict): return parsed, None
        return {"answer": str(message), "needs_review": True, "citations": []}, None

    def chat(self, scene_id, body: ChatRequest):
        context = self.scene_context(scene_id)
        retrieval = self.vectors.retrieve(body.message, body.top_k, body.provider_config_id)
        provider = self._provider(body.provider_config_id)
        existing = self.history(scene_id)["messages"]
        history = [{"role": item.get("role"), "content": item.get("content", "")} for item in existing if item.get("role") in {"user", "assistant"}]
        result, error = self._call_model(provider, body.message, context, retrieval["documents"], history)
        turn_id, created_at = uuid.uuid4().hex, _now()
        citations = result.get("citations") if isinstance(result, dict) else []
        if not isinstance(citations, list) or not citations:
            citations = [{"document_id": item["id"], "title": item["title"], "source": item.get("source"), "score": item.get("score")} for item in retrieval["documents"]]
        if error:
            answer = "大模型尚未配置或当前不可用；已完成知识检索，但没有生成模型回答。"
            status, needs_review, model = "unavailable", True, provider.get("model")
        else:
            answer = str(result.get("answer") or result.get("response") or result.get("reason") or "模型未返回可显示内容")
            status, needs_review, model = "completed", bool(result.get("needs_review", True)), provider.get("model")
        user_record = {"id": turn_id + ":user", "role": "user", "content": body.message, "created_at": created_at}
        assistant_record = {"id": turn_id + ":assistant", "role": "assistant", "content": answer, "created_at": created_at, "citations": citations, "retrieval": retrieval, "status": status, "model": model, "needs_review": needs_review}
        self._save_turn(scene_id, user_record, assistant_record)
        return {"conversation_id": turn_id, "answer": answer, "citations": citations, "retrieval": {"mode": retrieval["mode"], "document_count": len(retrieval["documents"]), "vector_model": retrieval.get("vector_model")}, "needs_review": needs_review, "status": status, "model": model, "error": error, "created_at": created_at}


def register_tunnel_chat_routes(app, get_workspace):
    project_root = Path(__file__).resolve().parents[2]
    store = None

    def current():
        nonlocal store
        if store is None: store = TunnelChatStore(get_workspace(), project_root)
        return store

    @app.get("/api/tunnel-scenes/{scene_id}/chat/history")
    def chat_history(scene_id: str): return current().history(scene_id)

    @app.delete("/api/tunnel-scenes/{scene_id}/chat/history")
    def clear_chat_history(scene_id: str): return current().clear_history(scene_id)

    @app.post("/api/tunnel-scenes/{scene_id}/chat")
    def scene_chat(scene_id: str, body: ChatRequest): return current().chat(scene_id, body)

    @app.get("/api/knowledge/vector/status")
    def vector_status(): return current().vectors.status()

    @app.post("/api/knowledge/vector/rebuild")
    def vector_rebuild(provider_config_id: str = "default"): return current().vectors.rebuild(provider_config_id)
