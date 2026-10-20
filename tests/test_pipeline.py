"""End-to-end contract tests; synthetic passing is not geological validation."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageOps
import pytest

from face_mapping.config import PipelineConfig
from face_mapping.pipeline import run_pipeline
from face_mapping.schemas import PipelineResult
from tools.generate_synthetic import generate_synthetic


@pytest.fixture(scope="module")
def synthetic_input(tmp_path_factory):
    return generate_synthetic(tmp_path_factory.mktemp("input") / "synthetic.png", 320, 240)


@pytest.fixture(scope="module")
def completed_run(tmp_path_factory, synthetic_input):
    folder = tmp_path_factory.mktemp("runs") / "normal"
    config = PipelineConfig.model_validate({"preprocessing": {"max_dimension": 160}})
    result = run_pipeline(synthetic_input, folder, config)
    return result, folder


def test_synthetic_is_reproducible_and_explicitly_labeled(tmp_path):
    first = generate_synthetic(tmp_path / "a.png", 128, 96, seed=7)
    second = generate_synthetic(tmp_path / "b.png", 128, 96, seed=7)
    assert first.read_bytes() == second.read_bytes()
    meta = json.loads(first.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["synthetic"] is True
    assert meta["ground_truth_available"] is False
    assert meta["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()


def test_end_to_end_artifacts_and_data_roundtrip(completed_run):
    result, folder = completed_run
    assert result.status == "completed_with_warnings", result.quality.errors
    assert result.statistics is not None
    assert result.statistics.fracture_count > 0
    expected = {
        "original.png", "preprocessed.png", "face_mask.png", "face_region.json",
        "lithology_mask.png", "lithology_overlay.png", "lithology_regions.json",
        "fracture_mask.png", "fracture_overlay.png", "fracture_skeleton.png",
        "fracture_traces.json", "geological_sketch.png", "parameters.csv",
        "results.json", "quality_report.json", "manifest.json", "execution.log",
    }
    assert expected <= {p.name for p in folder.iterdir()}
    decoded = PipelineResult.model_validate_json((folder / "results.json").read_text(encoding="utf-8"))
    assert decoded.model_dump() == result.model_dump()
    assert decoded.coordinate_system == "original_image_pixels"
    assert [step.status for step in decoded.quality.steps] == ["ok"] * 9
    region_document = json.loads((folder / "lithology_regions.json").read_text(encoding="utf-8"))
    trace_document = json.loads((folder / "fracture_traces.json").read_text(encoding="utf-8"))
    assert region_document["regions"] == [r.model_dump() for r in result.lithology_regions]
    assert trace_document["fractures"] == [t.model_dump() for t in result.fractures]
    with Image.open(folder / "preprocessed.png") as working:
        assert working.size == (160, 120)
    for name in expected:
        if name.endswith(".png") and name != "preprocessed.png":
            with Image.open(folder / name) as image:
                assert image.size == (320, 240), name
    assert result.image.original_size == [320, 240]
    assert result.image.scale_x == result.image.scale_y == 0.5
    assert result.statistics.total_length_m is None
    assert result.statistics.observation_area_m2 is None
    assert result.statistics.p21_per_m is None
    assert all(region.category == "unknown" and region.area_m2 is None for region in result.lithology_regions)


def test_masks_partition_roi_and_geometry_matches_outputs(completed_run):
    result, folder = completed_run
    face = np.asarray(Image.open(folder / "face_mask.png")) > 0
    labels = np.asarray(Image.open(folder / "lithology_mask.png"))
    fracture = np.asarray(Image.open(folder / "fracture_mask.png")) > 0
    assert result.face.pixel_area == int(face.sum())
    assert np.all(labels[~face] == 0)
    assert np.all(labels[face] > 0)
    assert not np.any(fracture & ~face)
    coverage = np.zeros(face.shape, dtype=np.uint16)
    for region in result.lithology_regions:
        mask = np.asarray(Image.open(folder / region.mask_path)) > 0
        assert np.array_equal(mask, labels == region.label_value)
        assert region.pixel_area == int(mask.sum())
        assert region.area_ratio == pytest.approx(mask.sum() / face.sum())
        coverage += mask.astype(np.uint16)
    assert np.array_equal(coverage, face.astype(np.uint16))
    assert sum(region.area_ratio for region in result.lithology_regions) == pytest.approx(1.0)
    lengths = []
    for trace in result.fractures:
        coordinates = np.asarray(trace.points)
        assert (coordinates >= 0).all()
        assert (coordinates[:, 0] <= 320).all() and (coordinates[:, 1] <= 240).all()
        measured = np.hypot(*np.diff(coordinates, axis=0).T).sum()
        assert trace.length_px == pytest.approx(measured)
        assert trace.length_m is None
        assert trace.source and trace.detection_source
        for angle in (trace.endpoint_direction_deg, trace.principal_direction_deg):
            assert angle is None or 0 <= angle < 180
        lengths.append(measured)
    assert result.statistics.fracture_count == len(lengths)
    assert result.statistics.total_length_px == pytest.approx(sum(lengths))
    assert result.statistics.p21_per_px == pytest.approx(sum(lengths) / face.sum())
    assert result.topology["edge_count"] == result.topology["visited_edge_count"]
    assert result.face.verified is False
    assert "candidate" in result.statistics.statistics_status


def test_csv_values_independently_match_json_and_artifact_hashes(completed_run):
    result, folder = completed_run
    document = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    with (folder / "parameters.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary = {r["metric"]: r for r in rows if r["object_type"] == "summary"}
    for metric, row in summary.items():
        value = document["statistics"][metric]
        assert row["value"] == "" if value is None else float(row["value"]) == pytest.approx(value)
    traces = {t["id"]: t for t in document["fractures"]}
    regions = {r["region_id"]: r for r in document["lithology_regions"]}
    for row in rows:
        if row["object_type"] == "fracture":
            value = traces[row["object_id"]][row["metric"]]
        elif row["object_type"] == "region":
            value = regions[row["object_id"]][row["metric"]]
        else:
            continue
        assert row["value"] == "" if value is None else float(row["value"]) == pytest.approx(value)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for artifact in manifest["files"]:
        contents = (folder / artifact["path"]).read_bytes()
        assert artifact["bytes"] == len(contents)
        assert artifact["sha256"] == hashlib.sha256(contents).hexdigest()


def test_metric_calibration_uses_original_pixels(tmp_path, synthetic_input):
    scale = .003
    config = PipelineConfig.model_validate({
        "preprocessing": {"max_dimension": 160},
        "face": {"mode": "full_image"},
        "calibration": {"meters_per_pixel": scale, "planar_approximation_acknowledged": True},
    })
    result = run_pipeline(synthetic_input, tmp_path / "metric", config)
    assert result.status == "completed_with_warnings", result.quality.errors
    stats = result.statistics
    assert result.face.status == "full_image_fallback"
    assert stats.observation_area_px2 == 320 * 240
    assert stats.observation_area_m2 == pytest.approx(320 * 240 * scale**2)
    assert stats.total_length_m == pytest.approx(stats.total_length_px * scale)
    assert stats.p21_per_m == pytest.approx(stats.total_length_m / stats.observation_area_m2)
    for region in result.lithology_regions:
        assert region.area_m2 == pytest.approx(region.pixel_area * scale**2)
    for trace in result.fractures:
        assert trace.length_m == pytest.approx(trace.length_px * scale)


def test_exif_orientation_and_rounded_resize_preserve_canvas(tmp_path):
    rng = np.random.default_rng(3)
    source = Image.fromarray(rng.integers(20, 220, (79, 121, 3), dtype=np.uint8))
    exif = Image.Exif()
    exif[274] = 6
    image_path = tmp_path / "oriented.jpg"
    source.save(image_path, exif=exif, quality=95)
    with Image.open(image_path) as decoded:
        expected = np.asarray(ImageOps.exif_transpose(decoded))
    config = PipelineConfig.model_validate({"preprocessing": {"max_dimension": 48}, "face": {"mode": "full_image"}})
    output = tmp_path / "exif"
    result = run_pipeline(image_path, output, config)
    assert result.status == "completed_with_warnings", result.quality.errors
    assert result.image.source_size == [121, 79]
    assert result.image.original_size == [79, 121]
    assert result.image.exif_transposed is True
    assert np.array_equal(np.asarray(Image.open(output / "original.png")), expected)
    w, h = result.image.working_size
    assert max(w, h) == 48
    assert result.image.scale_x == w / 79 and result.image.scale_y == h / 121
    forward = np.asarray(result.image.original_to_working)
    inverse = np.asarray(result.image.working_to_original)
    assert np.allclose(inverse @ forward, np.eye(3))
    assert np.allclose(forward @ [79, 121, 1], [w, h, 1])
    assert Image.open(output / "face_mask.png").size == (79, 121)


class BrokenBackend:
    def predict(self, image, roi_mask, config):
        raise RuntimeError("injected backend failure for isolation test")


def test_partial_failure_does_not_fabricate_lithology_or_block_fractures(tmp_path, synthetic_input):
    result = run_pipeline(synthetic_input, tmp_path / "partial", PipelineConfig.model_validate({"preprocessing": {"max_dimension": 160}}), lithology_backend=BrokenBackend())
    assert result.status == "partial"
    assert result.lithology_regions is None
    assert result.statistics.lithology_region_count is None
    assert result.fractures is not None and len(result.fractures) > 0
    assert (tmp_path / "partial" / "fracture_traces.json").is_file()
    assert (tmp_path / "partial" / "geological_sketch.png").is_file()
    failed = {s.step for s in result.quality.steps if s.status == "failed"}
    assert failed == {"03a_lithology"}
    assert any("injected backend failure" in e for e in result.quality.errors)


def test_failed_fracture_backend_keeps_regions_and_nulls_geometry(tmp_path, synthetic_input):
    output = tmp_path / "failed_fracture"
    result = run_pipeline(synthetic_input, output, PipelineConfig.model_validate({"preprocessing": {"max_dimension": 160}}), fracture_backend=BrokenBackend())
    assert result.status == "partial"
    assert result.lithology_regions is not None and result.lithology_regions
    assert result.fractures is None and result.statistics is None
    assert (output / "lithology_mask.png").is_file()
    assert (output / "geological_sketch.png").is_file()
    assert not (output / "fracture_traces.json").exists()
    statuses = {step.step: step.status for step in result.quality.steps}
    assert statuses["03b_fracture_detection"] == "failed"
    assert statuses["03c_vectorization"] == statuses["04_geometry"] == "skipped"
    with (output / "parameters.csv").open(encoding="utf-8-sig", newline="") as handle:
        summary = [r for r in csv.DictReader(handle) if r["object_type"] == "summary"]
    assert summary and all(r["value"] == "" for r in summary)


@pytest.mark.parametrize("kind", ["missing", "corrupt", "black", "white"])
def test_invalid_inputs_report_failure_and_null_metrics(tmp_path, kind):
    path = tmp_path / f"{kind}.png"
    if kind == "corrupt":
        path.write_bytes(b"This is not an image.")
    elif kind in {"black", "white"}:
        Image.new("RGB", (64, 64), (0, 0, 0) if kind == "black" else (255, 255, 255)).save(path)
    output = tmp_path / "invalid"
    result = run_pipeline(path, output)
    assert result.status == "failed"
    assert result.image is None and result.statistics is None
    assert result.fractures is None and result.lithology_regions is None
    assert result.quality.errors[0].startswith("01_preprocessing:")
    assert (output / "results.json").is_file() and (output / "quality_report.json").is_file()
    with (output / "parameters.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and all(row["value"] == "" and row["status"] == "unavailable" for row in rows)


def test_cli_runs_and_reports_invalid_calibration(tmp_path, synthetic_input):
    output = tmp_path / "cli"
    command = [sys.executable, "-m", "face_mapping", "--input", str(synthetic_input), "--output", str(output)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert "completed_with_warnings" in completed.stdout
    assert PipelineResult.model_validate_json((output / "results.json").read_text(encoding="utf-8")).statistics is not None
    rejected = subprocess.run(command + ["--meters-per-pixel", ".002"], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert rejected.returncode == 2
    assert "planar" in rejected.stderr


def test_existing_outputs_are_not_overwritten(completed_run, synthetic_input):
    _, folder = completed_run
    before = (folder / "results.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_pipeline(synthetic_input, folder)
    assert (folder / "results.json").read_bytes() == before
