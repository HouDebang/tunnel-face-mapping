"""Placement is illustrative; snapshots must never mutate source measurements."""
import hashlib
import io
import json
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from face_mapping.api import create_app
from tools.generate_synthetic import generate_synthetic


@pytest.fixture
def setup_scene(tmp_path):
    root = tmp_path / "workbench"
    photo = generate_synthetic(tmp_path / "test.png", 128, 96)
    with TestClient(create_app(root)) as client:
        response = client.post("/api/tasks", files={"file": ("test.png", photo.read_bytes(), "image/png")})
        assert response.status_code == 201
        task = response.json()
        scene = client.post("/api/tunnel-scenes", json={"name": "演示隧道", "start_chainage_m": 1200, "end_chainage_m": 1260}).json()
        yield client, task, scene, root


def section_url(scene):
    return f"/api/tunnel-scenes/{scene['id']}/sections"


def add_original(client, task, scene, **kwargs):
    response = client.post(section_url(scene), json={"task_id": task["id"], "chainage_m": 1220,
                           "source_kind": "original", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def run_all(client, task):
    config = task["config"]
    config["preprocessing"]["max_dimension"] = 96
    config["face"]["mode"] = "full_image"
    assert client.put(f"/api/tasks/{task['id']}/config", json={"config": config}).status_code == 200
    response = client.post(f"/api/tasks/{task['id']}/run-all", json={})
    assert response.status_code == 202
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{response.json()['job_id']}").json()
        if job["status"] in {"completed", "failed"}:
            assert job["status"] == "completed", job
            return client.get(f"/api/tasks/{task['id']}").json()
        time.sleep(.02)
    pytest.fail("recognition did not finish")


def test_scene_crud_snapshots_and_export_survive_reopen(setup_scene):
    client, task, scene, root = setup_scene
    assert scene["illustrative"] and scene["coordinate_system"] == "local_tunnel_m"
    assert scene["sections"] == []
    assert client.get("/api/tunnel-scenes").json()["scenes"][0]["section_count"] == 0
    original_index = (root / task["id"] / "task.json").read_bytes()
    scene = add_original(client, task, scene, label="K1+220")
    section = scene["sections"][0]
    assert section["position_m"] == [20, 3.2, 0]
    assert section["display_width_m"] <= .72 * scene["width_m"]
    assert section["display_height_m"] <= .7 * scene["height_m"]
    assert section["display_width_m"] / section["display_height_m"] == pytest.approx(128 / 96)
    asset = section["source"]["assets"]["original"]
    response = client.get(asset["url"])
    assert hashlib.sha256(response.content).hexdigest() == asset["sha256"]
    assert Image.open(io.BytesIO(response.content)).size == (128, 96)
    assert section["source"]["input_sha256"] == task["sha256"]
    assert section["source"]["execution_id"] is None
    assert client.get(asset["url"].replace("layer=original", "layer=sketch")).status_code == 404
    response = client.put(f"{section_url(scene)}/{section['id']}", json={"chainage_m": 1230, "offset_u_m": 2,
                          "offset_v_m": 1, "rotation_deg": 15, "visible": False})
    assert response.status_code == 200, response.text
    updated = response.json()["sections"][0]
    assert updated["position_m"] == [30, 4.2, 2]
    assert updated["source"] == section["source"]
    assert updated["visible"] is False
    response = client.put(f"/api/tunnel-scenes/{scene['id']}", json={"name": "改名", "height_m": 10})
    assert response.status_code == 200
    assert response.json()["sections"][0]["position_m"] == [30, 5, 2]
    exported = client.get(f"/api/tunnel-scenes/{scene['id']}/export")
    assert exported.headers["content-disposition"].endswith('.json"')
    assert json.loads(exported.content)["name"] == "改名"
    assert (root / task["id"] / "task.json").read_bytes() == original_index
    with TestClient(create_app(root)) as reopened:
        assert reopened.get(f"/api/tunnel-scenes/{scene['id']}").json()["sections"][0] == response.json()["sections"][0]
        assert reopened.get(asset["url"]).content == client.get(asset["url"]).content
        removed = reopened.delete(f"{section_url(scene)}/{section['id']}").json()
        assert removed["sections"] == []
        assert removed["removed_sections"][0]["source"] == section["source"]
        assert (root / ".tunnel-scenes" / scene["id"] / f"{section['id']}-original.jpg").is_file()


def test_sketch_freezes_real_execution_and_stays_when_source_changes(setup_scene):
    client, task, scene, root = setup_scene
    response = client.post(section_url(scene), json={"task_id": task["id"], "chainage_m": 1220})
    assert response.status_code == 409
    assert "素描" in response.text
    ready = run_all(client, task)
    sketch = ready["stages"]["sketch_rendering"]
    stats_before = client.get(f"/api/tasks/{task['id']}/results").json()
    index_before = (root / task["id"] / "task.json").read_bytes()
    response = client.post(section_url(scene), json={"task_id": task["id"], "chainage_m": 1220})
    assert response.status_code == 201, response.text
    scene = response.json()
    section = scene["sections"][0]
    assert section["source"]["execution_id"] == sketch["execution_id"]
    assert set(section["source"]["assets"]) == {"original", "sketch"}
    asset = section["source"]["assets"]["sketch"]
    frozen = client.get(asset["url"]).content
    assert hashlib.sha256(frozen).hexdigest() == asset["sha256"]
    source_artifact = next(a for a in sketch["artifacts"] if a["name"] == "geological_sketch.png")
    assert asset["source_sha256"] == source_artifact["sha256"]
    assert stats_before == client.get(f"/api/tasks/{task['id']}/results").json()
    assert index_before == (root / task["id"] / "task.json").read_bytes()
    # Explicit historical selection pins a valid recorded execution too.
    historical = client.post(section_url(scene), json={"task_id": task["id"], "chainage_m": 1240,
                                                      "execution_id": sketch["execution_id"], "demo_copy": True})
    assert historical.status_code == 201
    assert historical.json()["sections"][-1]["demo_copy"] is True
    artifact_path, _ = client.app.state.workspace.artifact_path(task["id"], source_artifact["id"], sketch["execution_id"])
    artifact_path.write_bytes(b"changed source")
    assert client.get(asset["url"]).content == frozen
    rejected = client.post(section_url(scene), json={"task_id": task["id"], "chainage_m": 1240,
                                                    "execution_id": sketch["execution_id"]})
    assert rejected.status_code == 409
    # Existing scene remains independent of all original task files.
    original, _ = client.app.state.workspace.artifact_path(task["id"], "original")
    original.unlink()
    assert client.get(section["source"]["assets"]["original"]["url"]).status_code == 200


@pytest.mark.parametrize("changes", [
    {"name": " "}, {"width_m": 0}, {"height_m": "NaN"}, {"end_chainage_m": 0},
    {"start_chainage_m": "Infinity"}, {"chainage_prefix": "\n"}, {"unknown": 1},
])
def test_invalid_scene_creation_rejected_without_extra_scenes(setup_scene, changes):
    client, _, _, _ = setup_scene
    response = client.post("/api/tunnel-scenes", json={"name": "bad", **changes})
    assert response.status_code == 422, response.text
    assert len(client.get("/api/tunnel-scenes").json()["scenes"]) == 1


def test_invalid_ids_placement_and_texture_hash_report_clear_status(setup_scene):
    client, task, scene, root = setup_scene
    assert client.get("/api/tunnel-scenes/not-a-scene").status_code == 404
    assert client.get("/api/tunnel-scenes/" + "0" * 32).status_code == 404
    route = section_url(scene)
    for changes, expected in [({"task_id": "../../x"}, 422), ({"task_id": "0" * 32}, 404),
                              ({"chainage_m": 1300}, 422), ({"chainage_m": "NaN"}, 422),
                              ({"display_width_m": -1}, 422), ({"execution_id": "0" * 32}, 422)]:
        response = client.post(route, json={"task_id": task["id"], "source_kind": "original", "chainage_m": 1220, **changes})
        assert response.status_code == expected, response.text
    scene = add_original(client, task, scene)
    section = scene["sections"][0]
    assert section["label"] == "掌子面 · K 1220 m"
    assert 0 < len(section["label"]) <= 120
    for update in ({"task_id": task["id"]}, {"source_kind": "sketch"}, {"rotation_deg": "Infinity"},
                   {"chainage_m": None}, {"visible": None}, {"display_width_m": None}):
        response = client.put(f"{route}/{section['id']}", json=update)
        assert response.status_code == 422, response.text
    response = client.put(f"/api/tunnel-scenes/{scene['id']}", json={"end_chainage_m": 1210})
    assert response.status_code == 422
    assert client.get(f"/api/tunnel-scenes/{scene['id']}").json() == scene
    assert client.get(section["source"]["assets"]["original"]["url"].replace("layer=original", "layer=../../scene.json")).status_code == 422
    path = root / ".tunnel-scenes" / scene["id"] / f"{section['id']}-original.jpg"
    path.write_bytes(b"corrupt texture")
    assert client.get(section["source"]["assets"]["original"]["url"]).status_code == 409
