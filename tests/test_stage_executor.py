"""Cross-runner stage contracts and calibration isolation."""
import numpy as np
from PIL import Image
import pytest

from face_mapping.backends import FaceDetectionResult, LithologySegmentationResult
from face_mapping.config import PipelineConfig
from face_mapping.export import write_json
from face_mapping.stage_executor import assemble_result, execute_stage
from tools.generate_synthetic import generate_synthetic


def persist_stage(stage, source, root, config, dependencies, **kwargs):
    directory = root / stage
    outcome = execute_stage(stage, source, directory, config, dependencies, **kwargs)
    write_json(directory / "outcome.json", outcome)
    dependencies[stage] = directory
    return outcome


def test_working_face_roi_is_persisted_without_inverse_resampling(tmp_path, monkeypatch):
    source = generate_synthetic(tmp_path / "source.png", 121, 79)
    config = PipelineConfig.model_validate({"preprocessing": {"max_dimension": 48}})
    dependencies = {}
    root = tmp_path / "stages"
    persist_stage("preprocessing", source, root, config, dependencies)
    width, height = (48, 31)
    roi = np.zeros((height, width), bool)
    roi[2:-3, 3:-4] = True
    roi[5:12:2, 6:24:3] = False
    monkeypatch.setattr("face_mapping.face_detection.detect_face", lambda image, conf:
                        FaceDetectionResult(mask=roi, method="test_roi", status="candidate"))
    persist_stage("face_detection", source, root, config, dependencies)
    seen = []

    class Recorder:
        def predict(self, image, roi_mask, conf):
            seen.append(roi_mask.copy())
            return LithologySegmentationResult(method="test_partition", labels=roi_mask.astype(np.int32))

    persist_stage("lithology_segmentation", source, root, config, dependencies, lithology_backend=Recorder())
    assert np.array_equal(seen[0], roi)
    assert Image.open(root / "face_detection" / "face_working_mask.png").size == (width, height)
    assert Image.open(root / "face_detection" / "face_mask.png").size == (121, 79)


def test_calibration_only_geometry_and_partial_export_remain_truthful(tmp_path):
    source = generate_synthetic(tmp_path / "source.png", 160, 120)
    config = PipelineConfig.model_validate({"face": {"mode": "full_image"}})
    dependencies = {}
    root = tmp_path / "stages"
    for stage in ("preprocessing", "face_detection", "lithology_segmentation", "fracture_segmentation", "trace_extraction"):
        persist_stage(stage, source, root, config, dependencies)
    raw_lithology = (dependencies["lithology_segmentation"] / "lithology_regions.json").read_bytes()
    calibrated = config.model_copy(update={"calibration": config.calibration.model_copy(update={
        "meters_per_pixel": .002, "planar_approximation_acknowledged": True})})
    outcome = persist_stage("geometry_calculation", source, root, calibrated, dependencies)
    assert outcome["data"]["statistics"]["observation_area_m2"] == pytest.approx(160 * 120 * .002 ** 2)
    assert all(r["area_m2"] == pytest.approx(r["pixel_area"] * .002 ** 2) for r in outcome["data"]["lithology_regions"])
    assert (dependencies["lithology_segmentation"] / "lithology_regions.json").read_bytes() == raw_lithology
    assert all(r.area_m2 is None for r in assemble_result(source, config,
               {k: v for k, v in dependencies.items() if k != "geometry_calculation"}).lithology_regions)
    partial = {key: dependencies[key] for key in ("preprocessing", "face_detection", "lithology_segmentation")}
    persist_stage("geometry_calculation", source, tmp_path / "partial", calibrated, partial)
    snapshot = assemble_result(source, calibrated, partial)
    assert snapshot.statistics is None and snapshot.fractures is None
    assert snapshot.lithology_regions and all(r.area_m2 is not None for r in snapshot.lithology_regions)
    persist_stage("sketch_rendering", source, tmp_path / "partial", calibrated, partial)
    exported = persist_stage("result_export", source, tmp_path / "partial", calibrated, partial)
    assert exported["data"]["result"]["statistics"] is None
    assert not (partial["result_export"] / "fracture_traces.json").exists()
    assert any("missing_modules" in warning for warning in exported["warnings"])


def test_stage_cannot_overwrite_existing_directory(tmp_path):
    directory = tmp_path / "immutable"
    directory.mkdir()
    (directory / "sentinel.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        execute_stage("preprocessing", tmp_path / "missing.png", directory, PipelineConfig(), {})
    assert (directory / "sentinel.txt").read_text(encoding="utf-8") == "keep"
