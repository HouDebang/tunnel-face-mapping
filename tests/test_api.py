"""HTTP contracts for the local workbench; image quality is not accuracy evidence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import time
import threading
import zipfile

import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from face_mapping.config import PipelineConfig
from face_mapping.schemas import PipelineResult
from tools.generate_synthetic import generate_synthetic


@pytest.fixture
def client(tmp_path):
    from face_mapping.api import create_app

    with TestClient(create_app(workspace_root=tmp_path / "workbench")) as connection:
        yield connection


@pytest.fixture
def photo(tmp_path):
    return generate_synthetic(tmp_path / "软件测试照片.png", 128, 96)


def upload(client, path, filename=None):
    response = client.post("/api/tasks", files={
        "file": (filename or path.name, path.read_bytes(), "image/png"),
    })
    assert response.status_code == 201, response.text
    return response.json()


def configure_small(client, task):
    config = deepcopy(task["config"])
    config["preprocessing"]["max_dimension"] = 96
    config["face"]["mode"] = "full_image"
    response = client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert response.status_code == 200, response.text
    return response.json()


def run_job(client, task_id, stage=None, **options):
    route = (f"/api/tasks/{task_id}/stages/{stage}/run" if stage
             else f"/api/tasks/{task_id}/run-all")
    response = client.post(route, json=options)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        polled = client.get(f"/api/jobs/{job_id}")
        assert polled.status_code == 200, polled.text
        job = polled.json()
        if job["status"] in {"completed", "failed"} and not client.get(f"/api/tasks/{task_id}").json()["busy"]:
            assert job["status"] == "completed", job
            return client.get(f"/api/tasks/{task_id}").json(), job
        time.sleep(.02)
    pytest.fail(f"Job {job_id} did not finish within 30 seconds")


def test_health_schema_and_static_frontend_are_served(client):
    assert client.get("/api/health").status_code == 200
    schema_response = client.get("/api/schema")
    assert schema_response.status_code == 200
    metadata = schema_response.json()
    assert {"schema", "defaults", "stages", "parameters"} <= metadata.keys()
    assert metadata["defaults"] == PipelineConfig().model_dump()
    assert metadata["schema"]["additionalProperties"] is False
    html = client.get("/")
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]
    assert "<html" in html.text.lower()


def test_upload_creates_isolated_persistent_task_and_exact_hash(client, photo):
    first = upload(client, photo)
    second = upload(client, photo, "second.png")
    assert first["id"] != second["id"]
    assert first["sha256"] == hashlib.sha256(photo.read_bytes()).hexdigest()
    assert (first["width"], first["height"]) == (128, 96)
    assert first["filename"] == photo.name
    assert not first["busy"]
    assert all(s["status"] in {"pending", "blocked"} for s in first["stages"].values())
    persisted = client.get(f"/api/tasks/{first['id']}")
    assert persisted.status_code == 200
    assert persisted.json()["sha256"] == first["sha256"]
    listed = client.get("/api/tasks").json()
    if isinstance(listed, dict):
        listed = listed["tasks"]
    assert {first["id"], second["id"]} <= {entry["id"] for entry in listed}
    original = client.get(first["original_url"])
    assert original.status_code == 200
    assert Image.open(io.BytesIO(original.content)).size == (128, 96)


@pytest.mark.parametrize("name,content,mime", [
    ("bad.png", b"this is not a PNG", "image/png"),
    ("bad.jpg", b"\xff\xd8\xff\xe0corrupted", "image/jpeg"),
    ("active.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"),
    ("code.py", b"print('not a photograph')", "image/png"),
])
def test_invalid_upload_rejected_without_creating_task(client, name, content, mime):
    response = client.post("/api/tasks", files={"file": (name, content, mime)})
    assert response.status_code in {400, 413, 415, 422}, response.text
    listed = client.get("/api/tasks").json()
    if isinstance(listed, dict):
        listed = listed["tasks"]
    assert listed == []


def test_decoded_tiny_image_rejected(client):
    contents = io.BytesIO()
    Image.fromarray(np.arange(12 * 12 * 3, dtype=np.uint8).reshape(12, 12, 3)).save(contents, format="PNG")
    response = client.post("/api/tasks", files={"file": ("tiny.png", contents.getvalue(), "image/png")})
    assert response.status_code in {400, 422}, response.text


def test_jpeg_with_mpo_container_is_accepted_as_camera_photo(client, photo):
    # Some phone cameras save a JPEG-compatible MPO container under .jpg.
    # Decode the actual contents; rejecting solely because PIL says MPO would
    # prevent otherwise valid real camera photographs from entering the UI.
    with Image.open(photo) as source:
        frame = source.convert("RGB")
    contents = io.BytesIO()
    frame.save(contents, format="MPO", save_all=True, append_images=[frame.copy()])
    with Image.open(io.BytesIO(contents.getvalue())) as decoded:
        assert decoded.format == "MPO" and decoded.n_frames == 2
    response = client.post("/api/tasks", files={"file": ("camera.jpg", contents.getvalue(), "image/jpeg")})
    assert response.status_code == 201, response.text
    task = response.json()
    assert (task["width"], task["height"]) == (128, 96)
    assert task["sha256"] == hashlib.sha256(contents.getvalue()).hexdigest()


@pytest.mark.parametrize("changes", [
    {"fracture": {"threshold": -1}},
    {"lithology": {"clustres": 2}},
    {"face": {"min_area_ratio": .9, "max_area_ratio": .2}},
    {"calibration": {"meters_per_pixel": .01, "planar_approximation_acknowledged": False}},
])
def test_config_validation_preserves_previous_configuration(client, photo, changes):
    task = upload(client, photo)
    config = deepcopy(task["config"])
    for section, values in changes.items():
        config[section].update(values)
    response = client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert response.status_code == 422, response.text
    current = client.get(f"/api/tasks/{task['id']}").json()
    assert current["config"] == task["config"]
    assert current["config_revision"] == task["config_revision"]


def test_single_stage_prepares_only_dependencies_and_can_reuse_cache(client, photo):
    task = configure_small(client, upload(client, photo))
    ready, _ = run_job(client, task["id"], "fracture_segmentation")
    for stage in ("preprocessing", "face_detection", "fracture_segmentation"):
        assert ready["stages"][stage]["status"] in {"completed", "warning"}
    for stage in ("lithology_segmentation", "trace_extraction", "geometry_calculation", "sketch_rendering", "result_export"):
        assert ready["stages"][stage]["status"] in {"pending", "blocked"}
    before = {s: d["execution_id"] for s, d in ready["stages"].items()}
    rerun, _ = run_job(client, task["id"], "fracture_segmentation")
    assert {s: d["execution_id"] for s, d in rerun["stages"].items()} == before
    assert len(rerun["history"]) == len(ready["history"])
    assert client.get(f"/api/tasks/{task['id']}/download").status_code == 409


def test_complete_run_roundtrip_and_download_are_current_and_machine_readable(client, photo):
    task = configure_small(client, upload(client, photo))
    ready, _ = run_job(client, task["id"])
    assert not ready["busy"]
    assert all(s["status"] in {"completed", "warning"} for s in ready["stages"].values())
    response = client.get(f"/api/tasks/{task['id']}/results")
    assert response.status_code == 200, response.text
    payload = response.json()
    result = PipelineResult.model_validate(payload["result"])
    assert result.statistics is not None
    assert result.statistics.total_length_m is None
    assert result.face.verified is False
    assert not payload["stale_stages"]
    downloaded = client.get(f"/api/tasks/{task['id']}/download")
    assert downloaded.status_code == 200, downloaded.text
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = archive.namelist()
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        required = {"results.json", "quality_report.json", "config_used.json", "parameters.csv", "geological_sketch.png", "fracture_mask.png"}
        assert required <= set(names)
        exported = PipelineResult.model_validate_json(archive.read("results.json"))
        # The persisted bundle adds CSV/hash/round-trip checks and its own file
        # catalog; the scientific snapshot and effective parameters must match.
        for field in ("schema_version", "algorithm_version", "coordinate_system", "status", "image", "face",
                      "lithology_regions", "fractures", "statistics", "calibration", "algorithms", "topology", "config"):
            assert getattr(exported, field) == getattr(result, field), field
        assert exported.quality.checks["csv_matches_result"] is True
        assert exported.quality.checks["results_json_roundtrip"] is True
        assert json.loads(archive.read("config_used.json")) == ready["config"]
        assert all(trace.length_m is None for trace in exported.fractures)


def test_unknown_resources_and_path_traversal_never_read_local_files(client):
    assert client.get("/api/tasks/not-a-task").status_code == 404
    assert client.get("/api/jobs/not-a-job").status_code == 404
    assert client.get("/api/tasks/../config.py/artifacts/results.json").status_code != 200
    assert client.get("/api/tasks/not-a-task/artifacts/%2E%2E%2Fconfig.py").status_code != 200


def test_http_config_change_marks_stale_and_history_is_explicit(client, photo):
    task = configure_small(client, upload(client, photo))
    first, _ = run_job(client, task["id"])
    fracture = first["stages"]["fracture_segmentation"]
    artifact = next(a for a in fracture["artifacts"] if a["name"] == "fracture_mask.png")
    before = client.get(artifact["url"])
    assert before.status_code == 200
    assert before.headers["cache-control"] == "no-store"
    config = deepcopy(first["config"])
    config["fracture"]["threshold"] = .5
    updated = client.put(f"/api/tasks/{task['id']}/config", json={"config": config})
    assert updated.status_code == 200, updated.text
    current = updated.json()
    assert current["stages"]["fracture_segmentation"]["status"] == "stale"
    assert current["stages"]["lithology_segmentation"]["status"] in {"completed", "warning"}
    assert client.get(artifact["url"]).status_code == 404
    history_url = artifact["url"] + "?history=" + artifact["execution_id"]
    historical = client.get(history_url)
    assert historical.status_code == 200
    assert historical.content == before.content
    assert client.get(f"/api/tasks/{task['id']}/download").status_code == 409
    data = client.get(f"/api/tasks/{task['id']}/results").json()
    assert "trace_extraction" in data["stale_stages"]
    assert data["result"]["fractures"] is None
    logs = client.get(f"/api/tasks/{task['id']}/logs")
    assert logs.status_code == 200 and logs.json()["logs"]


def test_http_busy_guard_rejects_overlapping_mutations(client, photo, monkeypatch):
    import face_mapping.stage_executor as executor

    task = configure_small(client, upload(client, photo))
    entered, release = threading.Event(), threading.Event()
    original = executor.execute_stage

    def waiting(stage, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(executor, "execute_stage", waiting)
    response = client.post(f"/api/tasks/{task['id']}/stages/preprocessing/run", json={})
    assert response.status_code == 202, response.text
    try:
        assert entered.wait(5)
        assert client.post(f"/api/tasks/{task['id']}/run-all", json={}).status_code == 409
        assert client.put(f"/api/tasks/{task['id']}/config", json={"config": task["config"]}).status_code == 409
        assert client.get(f"/api/tasks/{task['id']}").json()["busy"]
    finally:
        release.set()


def test_cross_origin_mutation_is_rejected(client, photo):
    response = client.post("/api/tasks", files={"file": ("face.png", photo.read_bytes(), "image/png")},
                           headers={"Origin": "https://unrelated.example"})
    assert response.status_code == 403
    assert client.get("/api/tasks").json()["tasks"] == []

