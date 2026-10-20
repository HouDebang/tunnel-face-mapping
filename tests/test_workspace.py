"""Cache, dependency and failure semantics of immutable workbench executions."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import zipfile

import pytest

from face_mapping.workspace import Workspace, WorkspaceError
from tools.generate_synthetic import generate_synthetic


VALID = {"completed", "warning"}
STAGES = ("preprocessing", "face_detection", "lithology_segmentation",
          "fracture_segmentation", "trace_extraction", "geometry_calculation",
          "sketch_rendering", "result_export")


@pytest.fixture
def workspace(tmp_path):
    instance = Workspace(tmp_path / "managed")
    yield instance
    instance.close()


@pytest.fixture
def task(workspace, tmp_path):
    path = generate_synthetic(tmp_path / "face.png", 128, 96)
    task = workspace.create_task(path.read_bytes(), path.name, "image/png")
    config = deepcopy(task["config"])
    config["preprocessing"]["max_dimension"] = 96
    config["face"]["mode"] = "full_image"
    return workspace.update_config(task["id"], config)


def finish(workspace, task_id, stage=None, *, expect="completed", **options):
    job = workspace.submit(task_id, stage_id=stage, **options)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        polled = workspace.job(job["job_id"])
        task = workspace.get_task(task_id)
        if polled["status"] in {"completed", "failed"} and not task["busy"]:
            assert polled["status"] == expect, polled
            return task, polled
        time.sleep(.02)
    pytest.fail(f"Job {job['job_id']} did not finish within 30 seconds")


def ids(task):
    return {sid: state["execution_id"] for sid, state in task["stages"].items()}


def change(workspace, task, section, **fields):
    config = deepcopy(task["config"])
    config[section].update(fields)
    return workspace.update_config(task["id"], config)


def test_upload_limits_and_mime_do_not_trust_filename(workspace, tmp_path, monkeypatch):
    import face_mapping.workspace as module

    image = generate_synthetic(tmp_path / "image.png", 128, 96).read_bytes()
    for filename, content_type in (("image.png", "text/html"), ("image.jpg", "image/jpeg")):
        with pytest.raises(WorkspaceError) as rejected:
            workspace.create_task(image, filename, content_type)
        assert rejected.value.status_code == 415
    monkeypatch.setattr(module, "MAX_UPLOAD_BYTES", len(image) - 1)
    with pytest.raises(WorkspaceError) as rejected:
        workspace.create_task(image, "image.png", "image/png")
    assert rejected.value.status_code == 413
    monkeypatch.setattr(module, "MAX_UPLOAD_BYTES", len(image) + 1)
    monkeypatch.setattr(module, "MAX_IMAGE_PIXELS", 128 * 96 - 1)
    with pytest.raises(WorkspaceError) as rejected:
        workspace.create_task(image, "image.png", "image/png")
    assert rejected.value.status_code == 422
    assert workspace.list_tasks() == []


def test_lithology_change_keeps_independent_fracture_results(workspace, task):
    first, _ = finish(workspace, task["id"])
    before = ids(first)
    changed = change(workspace, first, "lithology", clusters=first["config"]["lithology"]["clusters"] + 1)
    for stage in ("preprocessing", "face_detection", "fracture_segmentation", "trace_extraction"):
        assert changed["stages"][stage]["status"] in VALID
        assert changed["stages"][stage]["execution_id"] == before[stage]
    for stage in ("lithology_segmentation", "geometry_calculation", "sketch_rendering", "result_export"):
        assert changed["stages"][stage]["status"] == "stale"
        assert changed["stages"][stage]["artifacts"] == []
    rerun, _ = finish(workspace, task["id"], "lithology_segmentation", downstream=True)
    assert all(s["status"] in VALID for s in rerun["stages"].values())
    for stage in ("fracture_segmentation", "trace_extraction"):
        assert ids(rerun)[stage] == before[stage]
    assert ids(rerun)["lithology_segmentation"] != before["lithology_segmentation"]


def test_fracture_change_hides_stale_artifacts_preserves_explicit_history(workspace, task):
    first, _ = finish(workspace, task["id"])
    before = ids(first)
    old_artifact = next(a for a in first["stages"]["fracture_segmentation"]["artifacts"] if a["name"] == "fracture_mask.png")
    old_path, _ = workspace.artifact_path(task["id"], old_artifact["id"])
    old_bytes = old_path.read_bytes()
    changed = change(workspace, first, "fracture", threshold=.5)
    assert changed["stages"]["lithology_segmentation"]["status"] in VALID
    assert ids(changed)["lithology_segmentation"] == before["lithology_segmentation"]
    for stage in ("fracture_segmentation", "trace_extraction", "geometry_calculation", "sketch_rendering", "result_export"):
        assert changed["stages"][stage]["status"] == "stale"
    with pytest.raises(WorkspaceError) as stale:
        workspace.artifact_path(task["id"], old_artifact["id"])
    assert stale.value.status_code == 404
    historical, _ = workspace.artifact_path(task["id"], old_artifact["id"], history=old_artifact["execution_id"])
    assert historical.read_bytes() == old_bytes
    assert "?history=" in workspace.history(task["id"], old_artifact["execution_id"])["artifacts"][0]["url"]
    assert workspace.results(task["id"])["result"]["fractures"] is None
    with pytest.raises(WorkspaceError) as download:
        workspace.download_path(task["id"])
    assert download.value.status_code == 409
    rerun, _ = finish(workspace, task["id"], "fracture_segmentation", downstream=True)
    assert ids(rerun)["lithology_segmentation"] == before["lithology_segmentation"]
    assert ids(rerun)["fracture_segmentation"] != before["fracture_segmentation"]
    assert historical.read_bytes() == old_bytes
    with zipfile.ZipFile(workspace.download_path(task["id"])) as archive:
        result = json.loads(archive.read("results.json"))
        assert result["config"]["fracture"]["threshold"] == .5
        assert json.loads(archive.read("config_used.json")) == rerun["config"]


def test_calibration_only_recomputes_measurement_and_display_not_detectors(workspace, task, monkeypatch):
    import face_mapping.stage_executor as executor

    first, _ = finish(workspace, task["id"])
    before = ids(first)
    called = []
    original = executor.execute_stage

    def tracked(stage, *args, **kwargs):
        called.append(stage)
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(executor, "execute_stage", tracked)
    changed = change(workspace, first, "calibration", meters_per_pixel=.002,
                     planar_approximation_acknowledged=True, description="test scalar")
    assert set(s for s, state in changed["stages"].items() if state["status"] == "stale") == {
        "geometry_calculation", "sketch_rendering", "result_export"}
    rerun, _ = finish(workspace, task["id"])
    assert set(called) == {"geometry_calculation", "sketch_rendering", "result_export"}
    for stage in STAGES[:5]:
        assert ids(rerun)[stage] == before[stage]
    result = workspace.results(task["id"])["result"]
    stats = result["statistics"]
    assert stats["total_length_m"] == pytest.approx(stats["total_length_px"] * .002)
    assert stats["observation_area_m2"] == pytest.approx(stats["observation_area_px2"] * .002**2)
    assert all(r["area_m2"] == pytest.approx(r["pixel_area"] * .002**2) for r in result["lithology_regions"])
    assert all(t["length_m"] == pytest.approx(t["length_px"] * .002) for t in result["fractures"])


def test_cache_hit_has_no_extra_execution_and_results_do_not_mutate(workspace, task):
    first, _ = finish(workspace, task["id"])
    snapshots = [workspace.get_task(task["id"]) for _ in range(3)]
    assert all(all(s["status"] in VALID for s in snapshot["stages"].values()) for snapshot in snapshots)
    rerun, job = finish(workspace, task["id"])
    assert ids(rerun) == ids(first)
    assert len(rerun["history"]) == len(first["history"])
    assert {event["status"] for event in job["events"]} == {"cache_hit"}
    result_before = workspace.results(task["id"])["result"]
    assert workspace.results(task["id"])["result"] == result_before


def test_artifact_content_tamper_invalidates_reuse_and_only_affected_branch(workspace, task):
    first, _ = finish(workspace, task["id"])
    artifact = next(a for a in first["stages"]["fracture_segmentation"]["artifacts"] if a["name"] == "fracture_mask.png")
    path, _ = workspace.artifact_path(task["id"], artifact["id"])
    contents = bytearray(path.read_bytes())
    contents[-1] ^= 1  # Same length and filename: existence/size checks cannot catch this.
    path.write_bytes(contents)
    state = workspace.get_task(task["id"])
    assert state["stages"]["fracture_segmentation"]["status"] == "stale"
    assert state["stages"]["trace_extraction"]["status"] == "stale"
    assert state["stages"]["lithology_segmentation"]["status"] in VALID
    rerun, _ = finish(workspace, task["id"], "fracture_segmentation")
    assert ids(rerun)["fracture_segmentation"] != ids(first)["fracture_segmentation"]
    assert ids(rerun)["lithology_segmentation"] == ids(first)["lithology_segmentation"]


def test_implementation_version_hash_is_part_of_cache(workspace, task, monkeypatch):
    first, _ = finish(workspace, task["id"])
    implementation = workspace._implementation_hash
    monkeypatch.setattr(workspace, "_implementation_hash", lambda sid: "new-fracture-version" if sid == "fracture_segmentation" else implementation(sid))
    changed = workspace.get_task(task["id"])
    assert changed["stages"]["fracture_segmentation"]["status"] == "stale"
    assert changed["stages"]["trace_extraction"]["status"] == "stale"
    assert changed["stages"]["lithology_segmentation"]["status"] in VALID
    rerun, _ = finish(workspace, task["id"], "fracture_segmentation")
    assert ids(rerun)["fracture_segmentation"] != ids(first)["fracture_segmentation"]


def test_internal_outcome_json_is_integrity_protected(workspace, task):
    first, _ = finish(workspace, task["id"], "fracture_segmentation")
    outcome = next(a for a in first["stages"]["fracture_segmentation"]["artifacts"] if a["name"] == "outcome.json")
    path, _ = workspace.artifact_path(task["id"], outcome["id"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["method"] = "tampered-unverified-method"
    path.write_text(json.dumps(document), encoding="utf-8")
    current = workspace.get_task(task["id"])
    assert current["stages"]["fracture_segmentation"]["status"] == "stale"
    assert current["stages"]["preprocessing"]["status"] in VALID


def test_managed_source_content_hash_invalidates_all_stages(workspace, task, tmp_path):
    first, _ = finish(workspace, task["id"])
    replacement = generate_synthetic(tmp_path / "different.png", 128, 96, seed=9)
    original, _ = workspace.artifact_path(task["id"], "source")
    original.write_bytes(replacement.read_bytes())
    changed = workspace.get_task(task["id"])
    assert all(state["status"] == "stale" for state in changed["stages"].values())
    assert ids(changed) == ids(first)
    assert workspace.results(task["id"])["result"]["statistics"] is None


def test_task_roundtrip_restores_configuration_history_and_cache(workspace, task):
    first, _ = finish(workspace, task["id"], "fracture_segmentation")
    workspace.close()
    restored = Workspace(workspace.root)
    try:
        reopened = restored.get_task(task["id"])
        assert reopened["config"] == first["config"]
        assert reopened["history"] == first["history"]
        assert ids(reopened) == ids(first)
        assert reopened["stages"]["fracture_segmentation"]["status"] in VALID
        rerun, _ = finish(restored, task["id"], "fracture_segmentation")
        assert len(rerun["history"]) == len(first["history"])
    finally:
        restored.close()


def test_busy_guard_rejects_duplicate_and_config_but_other_task_queues(workspace, task, tmp_path, monkeypatch):
    import face_mapping.stage_executor as executor

    entered, release = threading.Event(), threading.Event()
    original = executor.execute_stage

    def waiting(stage, *args, **kwargs):
        entered.set()
        assert release.wait(10), "Test did not release stage worker"
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(executor, "execute_stage", waiting)
    second_image = generate_synthetic(tmp_path / "second.png", 128, 96, seed=10)
    second = workspace.create_task(second_image.read_bytes(), "second.png", "image/png")
    first_job = workspace.submit(task["id"], "preprocessing")
    try:
        assert entered.wait(5)
        with pytest.raises(WorkspaceError) as duplicate:
            workspace.submit(task["id"], "preprocessing")
        assert duplicate.value.status_code == 409
        with pytest.raises(WorkspaceError) as config:
            workspace.update_config(task["id"], task["config"])
        assert config.value.status_code == 409
        queued = workspace.submit(second["id"], "preprocessing")
        assert workspace.get_task(second["id"])["busy"]
        assert workspace.job(queued["job_id"])["status"] == "queued"
    finally:
        release.set()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and any(workspace.get_task(t)["busy"] for t in (task["id"], second["id"])):
        time.sleep(.02)
    assert workspace.job(first_job["job_id"])["status"] == "completed"
    assert workspace.job(queued["job_id"])["status"] == "completed"
    assert workspace.get_task(task["id"])["sha256"] != workspace.get_task(second["id"])["sha256"]


def test_failed_retry_keeps_success_history_but_never_serves_it_as_current(workspace, task, monkeypatch):
    import face_mapping.stage_executor as executor

    first, _ = finish(workspace, task["id"])
    old_id = ids(first)["fracture_segmentation"]
    old_record = workspace.history(task["id"], old_id)
    old_artifact = next(a for a in old_record["artifacts"] if a["name"] == "fracture_mask.png")
    original = executor.execute_stage

    def fail_fractures(stage, *args, **kwargs):
        if stage == "fracture_segmentation":
            raise RuntimeError("intentional test detector failure")
        return original(stage, *args, **kwargs)

    monkeypatch.setattr(executor, "execute_stage", fail_fractures)
    failed, _ = finish(workspace, task["id"], "fracture_segmentation", force=True, expect="failed")
    assert failed["stages"]["fracture_segmentation"]["status"] == "failed"
    assert "intentional test detector failure" in failed["stages"]["fracture_segmentation"]["error"]
    assert workspace.history(task["id"], old_id)["status"] in VALID
    assert workspace.artifact_path(task["id"], old_artifact["id"], history=old_id)[0].is_file()
    with pytest.raises(WorkspaceError):
        workspace.artifact_path(task["id"], old_artifact["id"])
    current = workspace.results(task["id"])["result"]
    assert current["fractures"] is None
    assert current["lithology_regions"] is not None
    assert current["statistics"] is None
    # Export retries optional branches but must still report the missing detector.
    partial, _ = finish(workspace, task["id"], "result_export")
    with zipfile.ZipFile(workspace.download_path(task["id"])) as archive:
        exported = json.loads(archive.read("results.json"))
        assert exported["fractures"] is None
        assert exported["statistics"] is None
        assert exported["status"] == "partial"
        assert "fracture_mask.png" not in archive.namelist()
        assert "fracture_traces.json" not in archive.namelist()
    monkeypatch.setattr(executor, "execute_stage", original)
    recovered, _ = finish(workspace, task["id"], "fracture_segmentation", force=True)
    assert recovered["stages"]["fracture_segmentation"]["status"] in VALID
    assert ids(recovered)["fracture_segmentation"] != old_id


def test_task_and_artifact_boundaries_are_enforced(workspace, task, tmp_path):
    first, _ = finish(workspace, task["id"], "preprocessing")
    path = generate_synthetic(tmp_path / "separate.png", 128, 96, seed=8)
    other = workspace.create_task(path.read_bytes(), "separate.png", "image/png")
    artifact = first["stages"]["preprocessing"]["artifacts"][0]
    with pytest.raises(WorkspaceError) as cross_task:
        workspace.artifact_path(other["id"], artifact["id"])
    assert cross_task.value.status_code == 404
    for task_id in ("..", "../../secret", "a" * 31, "a" * 33):
        with pytest.raises(WorkspaceError) as traversal:
            workspace.get_task(task_id)
        assert traversal.value.status_code == 404
    with pytest.raises(WorkspaceError):
        workspace.artifact_path(task["id"], "../../config.py")

