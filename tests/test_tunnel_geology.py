"""Illustrative geometry uses exact immutable executions, never current guesses."""
from copy import deepcopy
import hashlib
import json
import time

from fastapi.testclient import TestClient
import pytest

from face_mapping.api import create_app
from face_mapping.tunnel_geology import _extract, _sample
from tools.generate_synthetic import generate_synthetic


def finish(client, task_id, stage=None, force=False):
    route = f"/api/tasks/{task_id}/stages/{stage}/run" if stage else f"/api/tasks/{task_id}/run-all"
    response = client.post(route, json={"force": force})
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{response.json()['job_id']}").json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(.02)
    pytest.fail("recognition timed out")


@pytest.fixture
def ready(tmp_path):
    root = tmp_path / "workbench"
    photo = generate_synthetic(tmp_path / "face.png", 192, 144)
    with TestClient(create_app(root)) as client:
        task = client.post("/api/tasks", files={"file": ("face.png", photo.read_bytes(), "image/png")}).json()
        config = task["config"]
        config["face"]["mode"] = "full_image"
        config["preprocessing"]["max_dimension"] = 192
        config["vectorization"]["min_length_px"] = 4
        client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
        assert finish(client, task["id"])["status"] == "completed"
        task = client.get(f"/api/tasks/{task['id']}").json()
        scene = client.post("/api/tunnel-scenes", json={"name": "地质示意", "start_chainage_m": 1000, "end_chainage_m": 1060}).json()
        for chainage in (1010, 1030, 1050):
            response = client.post(f"/api/tunnel-scenes/{scene['id']}/sections", json={"task_id": task["id"], "chainage_m": chainage, "demo_copy": True})
            assert response.status_code == 201, response.text
            scene = response.json()
        yield client, root, task, scene


def geology_url(scene):
    return f"/api/tunnel-scenes/{scene['id']}/geology"


def build(client, scene, **parameters):
    response = client.post(geology_url(scene) + "/build", json=parameters)
    assert response.status_code == 200, response.text
    return response.json()


def source_hashes(root, task_id):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / task_id).rglob("*") if p.is_file()}


def test_real_execution_geometry_freezes_both_kinds_reopens_and_never_changes_source(ready):
    client, root, task, scene = ready
    before = source_hashes(root, task["id"])
    scene = build(client, scene)
    geology = scene["geology"]
    assert geology["status"] == "illustrative_unverified"
    assert {c["kind"] for c in geology["connections"]} == {"fracture", "lithology"}
    assert all(c["match_cost"] >= 0 and c["method"] == "auto_nearest_normalized_v1" for c in geology["connections"])
    assert any("重复源" in message for message in geology["warnings"])
    for section in scene["sections"]:
        feature = geology["features"][section["id"]]
        assert feature["fractures"] and feature["lithology"]
        for kind, stage in (("fracture", "trace_extraction"), ("lithology", "lithology_segmentation")):
            provenance = feature["provenance"]["branches"][kind]
            assert provenance["execution_id"] == task["stages"][stage]["execution_id"]
            assert provenance["cache_key"] == task["stages"][stage]["cache_key"]
            assert provenance["dependency_verification"] == "job_execution_ids_and_sketch_cache_key"
        assert all(f["category"] == "unknown" and f["closed"] for f in feature["lithology"])
        assert all(2 <= len(f["points"]) <= 64 and all(0 <= number <= 1 for p in f["points"] for number in p) for f in feature["fractures"] + feature["lithology"])
    assert before == source_hashes(root, task["id"])
    exported = client.get(f"/api/tunnel-scenes/{scene['id']}/export").json()
    assert exported["geology"] == geology
    with TestClient(create_app(root)) as reopened:
        assert reopened.get(f"/api/tunnel-scenes/{scene['id']}").json()["geology"] == geology
    # A changed recognition execution never replaces the geometry frozen above.
    config = task["config"]
    config["fracture"]["threshold"] = .95
    client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert finish(client, task["id"])["status"] == "completed"
    assert client.get(f"/api/tasks/{task['id']}").json()["stages"]["trace_extraction"]["execution_id"] != task["stages"]["trace_extraction"]["execution_id"]
    rebuilt = build(client, scene)
    assert rebuilt["geology"]["features"] == geology["features"]
    assert len(rebuilt["geology"]["connections"]) == len(geology["connections"])


def test_build_after_source_rerun_resolves_bound_sketch_not_latest(ready):
    client, root, task, scene = ready
    old_trace = task["stages"]["trace_extraction"]["execution_id"]
    config = task["config"]
    config["fracture"]["threshold"] = .85
    client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert finish(client, task["id"])["status"] == "completed"
    before = source_hashes(root, task["id"])
    scene = build(client, scene)
    assert all(snapshot["provenance"]["branches"]["fracture"]["execution_id"] == old_trace for snapshot in scene["geology"]["features"].values())
    assert before == source_hashes(root, task["id"])


def test_connections_crud_preserve_manual_and_remove_section_archives(ready):
    client, root, task, scene = ready
    scene = build(client, scene)
    base = geology_url(scene)
    auto = next(c for c in scene["geology"]["connections"] if c["kind"] == "fracture")
    scene = client.delete(f"{base}/connections/{auto['id']}").json()
    body = {key: auto[key] for key in ("kind", "from_section_id", "to_section_id", "from_object_id", "to_object_id")}
    body["reverse_to"] = True
    added = client.post(base + "/connections", json=body)
    assert added.status_code == 201, added.text
    scene = added.json()
    manual = scene["geology"]["connections"][-1]
    assert manual["method"] == "manual" and manual["match_cost"] is None and manual["reverse_to"]
    assert client.post(base + "/connections", json=body).status_code == 409
    reverse = dict(body, from_section_id=body["to_section_id"], to_section_id=body["from_section_id"], from_object_id=body["to_object_id"], to_object_id=body["from_object_id"])
    assert client.post(base + "/connections", json=reverse).status_code == 409
    scene = client.put(f"{base}/connections/{manual['id']}", json={"visible": False, "reverse_to": False}).json()
    assert scene["geology"]["connections"][-1]["visible"] is False
    scene = build(client, scene, kinds=["fracture"])
    assert next(c for c in scene["geology"]["connections"] if c["id"] == manual["id"])["method"] == "manual"
    # Placement edits retain the normalized frozen source geometry.
    snapshot = deepcopy(scene["geology"]["features"])
    section = scene["sections"][0]
    scene = client.put(f"/api/tunnel-scenes/{scene['id']}/sections/{section['id']}", json={"offset_u_m": 1.2, "rotation_deg": 20, "flip_horizontal": True}).json()
    assert scene["geology"]["features"] == snapshot
    response = client.put(base + "/settings", json={"opacity": .7, "mode": "fracture"})
    assert response.status_code == 200
    response = client.put(base + "/settings", json={"show_source_lines": False})
    assert response.json()["geology"]["settings"] == {"opacity": .7, "mode": "fracture", "show_source_lines": False}
    scene = client.delete(f"/api/tunnel-scenes/{scene['id']}/sections/{section['id']}").json()
    assert all(section["id"] not in {c["from_section_id"], c["to_section_id"]} for c in scene["geology"]["connections"])
    assert section["id"] in scene["geology"]["removed_features"]
    assert scene["geology"]["removed_connections"]


def test_legacy_read_is_read_only_and_explicit_build_migrates(ready):
    client, root, task, scene = ready
    scene_path = root / ".tunnel-scenes" / scene["id"] / "scene.json"
    scene.pop("geology")
    scene_path.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
    content = scene_path.read_bytes()
    assert "geology" not in client.get(f"/api/tunnel-scenes/{scene['id']}").json()
    assert scene_path.read_bytes() == content
    initialized = client.put(geology_url(scene) + "/settings", json={"mode": "fracture"})
    assert initialized.status_code == 200
    assert initialized.json()["geology"]["settings"]["mode"] == "fracture"
    assert not initialized.json()["geology"]["features"]
    assert build(client, scene)["geology"]["connections"]


def test_invalid_settings_references_overlaps_and_finite_numbers(ready):
    client, root, task, scene = ready
    base = geology_url(scene)
    for body in ({"kinds": []}, {"kinds": ["fake"]}, {"max_match_distance": "NaN"}, {"max_connections_per_pair": 0}):
        assert client.post(base + "/build", json=body).status_code == 422
    for body in ({"mode": "fake"}, {"opacity": "Infinity"}, {"opacity": .01}):
        assert client.put(base + "/settings", json=body).status_code == 422
    scene = build(client, scene)
    connection = scene["geology"]["connections"][0]
    body = {key: connection[key] for key in ("kind", "from_section_id", "to_section_id", "from_object_id", "to_object_id")}
    assert client.post(base + "/connections", json=dict(body, to_section_id=body["from_section_id"])).status_code == 422
    assert client.post(base + "/connections", json=dict(body, to_object_id="DOES_NOT_EXIST")).status_code == 422
    assert client.post(base + "/connections", json=dict(body, to_section_id="0" * 32)).status_code == 422
    assert client.put(f"{base}/connections/{connection['id']}", json={"visible": None}).status_code == 422
    assert client.put(f"{base}/connections/{connection['id']}", json={"kind": "lithology"}).status_code == 422
    # Existing connected endpoints cannot collapse to one chainage.
    response = client.put(f"/api/tunnel-scenes/{scene['id']}/sections/{scene['sections'][1]['id']}", json={"chainage_m": 1010})
    assert response.status_code == 422 and "已有地质连接" in response.text
    # Unconnected sections may overlap, but must not be silently auto-ordered.
    scene = client.post(f"/api/tunnel-scenes/{scene['id']}/sections", json={"task_id": task["id"], "chainage_m": 1010}).json()
    response = client.post(base + "/build", json={})
    assert response.status_code == 409 and "同里程" in response.text
    client.put(f"/api/tunnel-scenes/{scene['id']}/sections/{scene['sections'][-1]['id']}", json={"visible": False})
    assert client.post(base + "/build", json={}).status_code == 200


def test_corrupted_source_no_guess_and_frozen_snapshot_integrity(ready):
    client, root, task, scene = ready
    raw = json.loads((root / task["id"] / "task.json").read_text(encoding="utf-8"))
    trace = next(r for r in raw["history"] if r["execution_id"] == task["stages"]["trace_extraction"]["execution_id"])
    artifact = next(a for a in trace["artifacts"] if a["name"] == "fracture_traces.json")
    path = root / task["id"] / artifact["relative_path"]
    original = path.read_bytes()
    path.write_bytes(b"corrupt")
    response = client.post(geology_url(scene) + "/build", json={})
    assert response.status_code == 409 and "SHA256" in response.text
    assert client.get(f"/api/tunnel-scenes/{scene['id']}").json()["geology"]["features"] == {}
    path.write_bytes(original)
    scene = build(client, scene)
    scene_path = root / ".tunnel-scenes" / scene["id"] / "scene.json"
    first = scene["sections"][0]["id"]
    scene["geology"]["features"][first]["fractures"][0]["points"][0][0] += .01
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    response = client.post(geology_url(scene) + "/build", json={})
    assert response.status_code == 409 and "冻结几何" in response.text
    assert client.get(f"/api/tunnel-scenes/{scene['id']}").status_code == 409
    assert client.get(f"/api/tunnel-scenes/{scene['id']}/export").status_code == 409


def test_sampling_limits_closed_holes_and_continuous_canvas():
    points = [[i / 100, .5] for i in range(101)]
    sampled = _sample(points, False)
    assert len(sampled) == 64 and sampled[0] == [0, .5] and sampled[-1] == [1, .5]
    warnings = []
    record = {"execution_id": "f" * 32, "data": {"regions": [{"region_id": "R001", "pixel_area": 80,
              "polygon": [{"exterior": [[0, 0], [10, 0], [10, 8], [0, 8], [0, 0]], "holes": [[[1, 1], [2, 1], [2, 2], [1, 1]]]}]}]}}
    features = _extract("lithology", record, 10, 8, warnings)
    assert features[0]["points"] == [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert features[0]["id"] == "R001:P0" and features[0]["category"] == "unknown"
    assert any("内孔" in w for w in warnings)


def test_read_objects_without_auto_connect_handles_overlaps_and_original_unavailable(ready):
    client, root, task, scene = ready
    # The existing user's arrangement may include several sources at one station.
    for chainage in (1010, 1030, 1050):
        scene = client.post(f"/api/tunnel-scenes/{scene['id']}/sections", json={"task_id": task["id"], "chainage_m": chainage}).json()
    assert client.post(geology_url(scene) + "/build", json={}).status_code == 409
    scene = build(client, scene, auto_connect=False)
    assert len(scene["geology"]["features"]) == 6
    assert not scene["geology"]["connections"]
    left, right = scene["sections"][:2]
    features = scene["geology"]["features"]
    manual = {"kind": "fracture", "from_section_id": left["id"], "to_section_id": right["id"],
              "from_object_id": features[left["id"]]["fractures"][0]["id"], "to_object_id": features[right["id"]]["fractures"][0]["id"]}
    response = client.post(geology_url(scene) + "/connections", json=manual)
    assert response.status_code == 201, response.text
    connections = response.json()["geology"]["connections"]
    assert build(client, scene, auto_connect=False)["geology"]["connections"] == connections
    # Original-only snapshots explicitly describe missing geometry, not guessed curves.
    original_scene = client.post("/api/tunnel-scenes", json={"name": "原图展示"}).json()
    original_scene = client.post(f"/api/tunnel-scenes/{original_scene['id']}/sections", json={"task_id": task["id"], "chainage_m": 20, "source_kind": "original"}).json()
    response = build(client, original_scene, auto_connect=False)
    snapshot = response["geology"]["features"][response["sections"][0]["id"]]
    assert not snapshot["fractures"] and not snapshot["lithology"]
    assert all(branch["status"] == "unavailable" for branch in snapshot["provenance"]["branches"].values())
    assert any("原照片" in warning for warning in response["geology"]["warnings"])


def test_missing_optional_branch_reports_unavailable_and_preserves_other_geometry(ready, monkeypatch):
    import importlib

    module = importlib.import_module("face_mapping.stage_executor")

    client, root, task, scene = ready
    execute = module.execute_stage

    def missing_lithology(stage, *args, **kwargs):
        if stage == "lithology_segmentation":
            raise ValueError("intentional missing lithology for partial-stage test")
        return execute(stage, *args, **kwargs)

    monkeypatch.setattr(module, "execute_stage", missing_lithology)
    config = task["config"]
    config["lithology"]["clusters"] = 5
    client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert finish(client, task["id"])["status"] == "failed"
    partial = client.get(f"/api/tasks/{task['id']}").json()
    assert partial["stages"]["sketch_rendering"]["status"] in {"completed", "warning"}
    scene = client.post("/api/tunnel-scenes", json={"name": "部分分支"}).json()
    for chainage in (10, 30):
        response = client.post(f"/api/tunnel-scenes/{scene['id']}/sections", json={"task_id": task["id"], "chainage_m": chainage})
        assert response.status_code == 201, response.text
        scene = response.json()
    scene = build(client, scene)
    assert {c["kind"] for c in scene["geology"]["connections"]} == {"fracture"}
    for snapshot in scene["geology"]["features"].values():
        assert snapshot["fractures"] and not snapshot["lithology"]
        assert snapshot["provenance"]["branches"]["lithology"]["status"] == "unavailable"
    assert any("lithology unavailable" in warning for warning in scene["geology"]["warnings"])
