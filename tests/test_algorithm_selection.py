"""Selectable methods must execute, survive export, and invalidate only their branch."""
from copy import deepcopy
import json
import time

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from face_mapping.api import create_app
from face_mapping.config import PipelineConfig, load_config
from face_mapping.parameter_ui import ui_schema
from tools.generate_synthetic import generate_synthetic


@pytest.mark.parametrize("section", ["face", "lithology", "fracture"])
def test_unknown_algorithm_is_rejected(section):
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate({section: {"algorithm": "uninstalled_model"}})


def test_algorithm_schema_and_legacy_configuration(tmp_path):
    schema = ui_schema()
    definitions = schema["schema"]["$defs"]
    for section, config_name in [("face", "FaceConfig"), ("lithology", "LithologyConfig"), ("fracture", "FractureConfig")]:
        assert set(definitions[config_name]["properties"]["algorithm"]["enum"]) == set(schema["parameters"][section]["algorithm"]["options"])
    legacy = {"face": {"mode": "full_image"}, "lithology": {"clusters": 3}, "fracture": {"threshold": .4}}
    restored = PipelineConfig.model_validate(legacy)
    assert restored.face.algorithm == "central_texture"
    assert restored.lithology.algorithm == "kmeans"
    assert restored.fracture.algorithm == "blackhat_hessian"
    path = tmp_path / "methods.yaml"
    path.write_text("face:\n  algorithm: grabcut\nlithology:\n  algorithm: slic\nfracture:\n  algorithm: frangi\n", encoding="utf-8")
    assert load_config(path).fracture.algorithm == "frangi"
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate({"fracture": {"algorithm": "frangi", "frangi_sigma_min": 5, "frangi_sigma_max": 2}})
    # Switching away from Frangi must not leave hidden, inactive scale fields
    # blocking the selected Blackhat method.
    assert PipelineConfig.model_validate({"fracture": {"algorithm": "blackhat", "frangi_sigma_min": 5, "frangi_sigma_max": 2}}).fracture.algorithm == "blackhat"


def _run(client, task_id, stage=None):
    route = f"/api/tasks/{task_id}/stages/{stage}/run" if stage else f"/api/tasks/{task_id}/run-all"
    response = client.post(route, json={"downstream": True})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        current = client.get(f"/api/tasks/{task_id}").json()
        if job["status"] in {"completed", "failed"} and not current["busy"]:
            assert job["status"] == "completed", job
            return current
        time.sleep(.02)
    pytest.fail("Selectable algorithm job did not finish")


def test_switch_methods_executes_real_backends_and_preserves_history(tmp_path):
    root = tmp_path / "tasks"
    source = generate_synthetic(tmp_path / "sample.png", 128, 96)
    with TestClient(create_app(workspace_root=root)) as client:
        response = client.post("/api/tasks", files={"file": (source.name, source.read_bytes(), "image/png")})
        assert response.status_code == 201
        task = response.json()
        task_id = task["id"]
        config = deepcopy(task["config"])
        config["face"]["mode"] = "full_image"
        assert client.put(f"/api/tasks/{task_id}/config", json={"config": config}).status_code == 200
        first = _run(client, task_id)
        initial_lithology = first["stages"]["lithology_segmentation"]["execution_id"]
        old_masks = {path: path.read_bytes() for path in root.rglob("fracture_mask.png")}
        assert old_masks

        for algorithm, expected in [("frangi", "clahe_frangi_pca_v1"), ("blackhat", "clahe_blackhat_pca_v1")]:
            config["fracture"]["algorithm"] = algorithm
            changed = client.put(f"/api/tasks/{task_id}/config", json={"config": config}).json()
            assert {sid for sid, state in changed["stages"].items() if state["status"] == "stale"} == {
                "fracture_segmentation", "trace_extraction", "geometry_calculation", "sketch_rendering", "result_export"}
            current = _run(client, task_id, "fracture_segmentation")
            assert current["stages"]["fracture_segmentation"]["method"] == expected
            assert current["stages"]["lithology_segmentation"]["execution_id"] == initial_lithology
            result = client.get(f"/api/tasks/{task_id}/results").json()["result"]
            assert result["algorithms"]["fracture"]["method"] == expected
            assert all(trace["detection_source"] == expected for trace in result["fractures"])

        previous_fracture = current["stages"]["fracture_segmentation"]["execution_id"]
        config["lithology"]["algorithm"] = "slic"
        changed = client.put(f"/api/tasks/{task_id}/config", json={"config": config}).json()
        assert changed["stages"]["fracture_segmentation"]["status"] in {"completed", "warning"}
        current = _run(client, task_id, "lithology_segmentation")
        assert current["stages"]["lithology_segmentation"]["method"] == "slic_lab_adjacency_v1"
        assert current["stages"]["fracture_segmentation"]["execution_id"] == previous_fracture

        config["face"].update(algorithm="grabcut", mode="auto")
        assert client.put(f"/api/tasks/{task_id}/config", json={"config": config}).status_code == 200
        current = _run(client, task_id)
        face = current["stages"]["face_detection"]["data"]["face"]
        assert face["method"] == "central_seed_grabcut_v1" or face["details"].get("attempted_method") == "central_seed_grabcut_v1"
        assert face["verified"] is False
        exported = list((root / task_id).rglob("config_used.json"))
        assert any(json.loads(path.read_text(encoding="utf-8"))["face"]["algorithm"] == "grabcut" for path in exported)
        assert all(path.read_bytes() == before for path, before in old_masks.items())
        for section in ("face", "lithology", "fracture"):
            invalid = deepcopy(config)
            invalid[section]["algorithm"] = "unknown_method"
            assert client.put(f"/api/tasks/{task_id}/config", json={"config": invalid}).status_code == 422
