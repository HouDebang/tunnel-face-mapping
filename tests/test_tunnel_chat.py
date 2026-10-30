import json
from pathlib import Path

from face_mapping.knowledge_base import KnowledgeBase
from face_mapping.tunnel_chat import ChatRequest, TunnelChatStore
from face_mapping.vector_store import KnowledgeVectorStore


SCENE_ID = "0123456789abcdef0123456789abcdef"


class DummyWorkspace:
    def __init__(self, root):
        self.root = Path(root)

    def get_task(self, task_id):
        return {"filename": "synthetic.png", "stages": {"lithology_segmentation": {"status": "completed"}}}


def make_scene(root):
    folder = Path(root) / ".tunnel-scenes" / SCENE_ID
    folder.mkdir(parents=True)
    (folder / "scene.json").write_text(json.dumps({
        "id": SCENE_ID, "name": "测试场景", "start_chainage_m": 0,
        "end_chainage_m": 100, "sections": [], "geology": {"connections": []}
    }, ensure_ascii=False), encoding="utf-8")


def test_vector_store_lexical_fallback_and_source_trace(tmp_path):
    knowledge = KnowledgeBase(tmp_path, Path.cwd())
    store = KnowledgeVectorStore(tmp_path, knowledge)
    status = store.status()
    assert status["mode"] == "lexical_fallback"
    result = store.retrieve("花岗岩", limit=3)
    assert result["mode"] == "lexical_fallback"
    assert result["documents"]
    assert all("source" in item and "api_key" not in json.dumps(item) for item in result["documents"])


def test_scene_chat_history_is_persistent_and_redacted(tmp_path):
    make_scene(tmp_path)
    store = TunnelChatStore(DummyWorkspace(tmp_path), Path.cwd())
    result = store.chat(SCENE_ID, ChatRequest(message="当前场景有哪些断面？"))
    assert result["status"] == "unavailable"
    assert result["retrieval"]["mode"] == "lexical_fallback"
    history = store.history(SCENE_ID)
    assert len(history["messages"]) == 2
    assert "api_key" not in json.dumps(history, ensure_ascii=False)
    assert store.clear_history(SCENE_ID)["messages"] == []
