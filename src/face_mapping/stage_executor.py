"""The single implementation of every executable workflow stage.

CLI and workspace runners orchestrate this module; neither contains recognition
or geometry algorithms. Stage directories are immutable and contain only that
stage's artifacts. ``outcome.json`` is written by the runner after success.
"""
from __future__ import annotations

import importlib.metadata
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from skimage.segmentation import find_boundaries

from .backends import SegmentationBackend
from .config import PipelineConfig
from .coordinates import resize_labels, resize_mask
from .export import build_manifest, export_csv, file_sha256, verify_csv, write_json
from .geometry import (axial_direction_deg, calculate_statistics, mask_to_polygons,
                       polyline_length, principal_direction_deg)
from .schemas import (COORDINATE_SYSTEM, FaceRegion, FractureTrace, GeometricStatistics,
                      ImageMetadata, LithologyRegion, PipelineResult, QualityReport,
                      StepStatus)
from .visualization import (_font, fracture_overlay, lithology_overlay, region_color,
                            render_sketch, save_rgb)

STAGE_IDS = (
    "preprocessing", "face_detection", "lithology_segmentation",
    "fracture_segmentation", "trace_extraction", "geometry_calculation",
    "sketch_rendering", "result_export",
)
ANGLE_CONVENTION = "axial [0,180) degrees clockwise from image +x; not strike/dip"
CORE_ARTIFACTS = (
    "original.png", "preprocessed.png", "face_mask.png", "lithology_mask.png",
    "lithology_overlay.png", "lithology_regions.json", "fracture_mask.png",
    "fracture_overlay.png", "fracture_skeleton.png", "fracture_traces.json",
    "geological_sketch.png", "parameters.csv",
)


def _outcome(directory: Path) -> dict:
    return json.loads((Path(directory) / "outcome.json").read_text(encoding="utf-8"))


def _data(dependencies: dict[str, Path], stage: str) -> dict:
    if stage not in dependencies:
        raise ValueError(f"Required stage unavailable: {stage}")
    return _outcome(dependencies[stage])["data"]


def _rgb(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).copy()


def _mask(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image).copy() > 0


def _labels(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image).astype(np.int32)


def _validate_mask(mask, shape, name):
    array = np.asarray(mask)
    if array.shape != shape or not np.all((array == 0) | (array == 1)):
        raise ValueError(f"{name}: expected binary mask of shape {shape}")
    return array.astype(bool)


def _save_mask(path: Path, mask: np.ndarray):
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def _face_layer(mask: np.ndarray) -> Image.Image:
    padded = np.pad(mask, 1)
    boundary = find_boundaries(padded, mode="inner")[1:-1, 1:-1] & mask
    array = np.zeros((*mask.shape, 4), np.uint8)
    array[boundary] = (30, 255, 100, 255)
    return Image.fromarray(array, "RGBA")


def _lithology_layer(labels: np.ndarray) -> Image.Image:
    array = np.zeros((*labels.shape, 4), np.uint8)
    for label in np.unique(labels):
        if label > 0:
            array[labels == label] = (*region_color(int(label)), 62)
    array[find_boundaries(labels, mode="inner") & (labels > 0)] = (255, 220, 80, 255)
    return Image.fromarray(array, "RGBA")


def _trace_layers(size, fractures: list[FractureTrace]) -> tuple[Image.Image, Image.Image]:
    width, height = size
    layer = Image.new("RGBA", (width, height))
    ids = Image.new("RGBA", (width, height))
    draw, id_draw = ImageDraw.Draw(layer), ImageDraw.Draw(ids)
    font_size = max(10, min(24, width // 70))
    font = _font(font_size)
    labeled = {t.id for t in sorted(fractures, key=lambda t: t.length_px, reverse=True)[:80]}
    for trace in fractures:
        points = [tuple(point) for point in trace.display_points]
        if len(points) > 1:
            draw.line(points, fill=(255, 25, 35, 255), width=max(1, width // 650))
        if points and trace.id in labeled:
            x, y = points[len(points) // 2]
            id_draw.text((min(x, max(0, width - 6 * font_size)), min(y, height - font_size - 2)),
                         trace.id, fill=(255, 255, 255, 255), font=font,
                         stroke_width=1, stroke_fill=(0, 0, 0, 255))
    return layer, ids


def _trace_model(record: dict) -> FractureTrace:
    return FractureTrace.model_validate({key: value for key, value in record.items()
                                       if key in FractureTrace.model_fields})


def _trace_document(fractures, topology):
    return {"coordinate_system": COORDINATE_SYSTEM, "angle_convention": ANGLE_CONVENTION,
            "topology": topology, "fractures": [t.model_dump() for t in fractures]}


def _region_document(regions, algorithm):
    return {"coordinate_system": COORDINATE_SYSTEM,
            "regions": [r.model_dump() for r in regions], "algorithm": algorithm}


def assemble_result(input_path: str | Path, config: PipelineConfig,
                    dependencies: dict[str, Path], step_statuses=None,
                    errors: list[str] | None = None) -> PipelineResult:
    """Build a current snapshot using only the dependency directories supplied.

    Missing or failed branches are null, never pulled from an older run. A
    geometry result owns calibration; recognition-stage records stay in pixels.
    """
    outcomes = {stage: _outcome(directory) for stage, directory in dependencies.items()
                if stage in STAGE_IDS and stage != "result_export"}
    quality = QualityReport()
    for stage in STAGE_IDS:
        outcome = outcomes.get(stage)
        if outcome:
            quality.warnings.extend(outcome.get("warnings", []))
            quality.checks.update(outcome.get("checks", {}))
    quality.errors.extend(errors or [])
    if step_statuses is not None:
        quality.steps = [s if isinstance(s, StepStatus) else StepStatus.model_validate(s)
                         for s in step_statuses]
    else:
        quality.steps = [StepStatus(step=stage, status="ok" if stage in outcomes else "skipped",
                                   input=str(input_path) if stage == "preprocessing" else "current dependency artifacts",
                                   output=outcomes[stage].get("artifacts", []) if stage in outcomes else [],
                                   error=None if stage in outcomes else "No current successful stage result")
                         for stage in STAGE_IDS if stage != "result_export"]
    missing = [stage for stage in STAGE_IDS[:-1] if stage not in outcomes]
    if missing:
        quality.warnings.append("missing_modules: " + ", ".join(missing))
    source = {stage: outcome["data"] for stage, outcome in outcomes.items()}
    image = source.get("preprocessing", {}).get("image")
    face = source.get("face_detection", {}).get("face")
    geometry = source.get("geometry_calculation", {})
    regions = geometry.get("lithology_regions", source.get("lithology_segmentation", {}).get("lithology_regions"))
    traces = geometry.get("fractures", source.get("trace_extraction", {}).get("fractures"))
    algorithms = {}
    for data in source.values():
        algorithms.update(data.get("algorithms", {}))
    status = "failed" if image is None else "partial" if missing or quality.errors else "completed_with_warnings"
    if config.calibration.meters_per_pixel is not None:
        quality.warnings.append("Metric values depend on supplied scale and uniform planar projection approximation; perspective/distortion is not corrected.")
    quality.status = status
    quality.warnings = list(dict.fromkeys(quality.warnings))
    for key in ("face_nonempty", "face_verified", "full_image_fallback", "regions_inside_roi",
                "regions_cover_roi_without_overlap", "region_mask_area_consistent", "fracture_mask_nonempty",
                "all_skeleton_edges_visited_once", "trace_coordinates_in_bounds", "trace_lengths_valid"):
        quality.checks.setdefault(key, None)
    return PipelineResult(status=status, image=image, face=face, lithology_regions=regions,
                          fractures=[_trace_model(t) for t in traces] if traces is not None else None,
                          statistics=geometry.get("statistics"), calibration=config.calibration.model_dump(),
                          algorithms=algorithms, topology=source.get("trace_extraction", {}).get("topology"),
                          config=config.model_dump(), quality=quality)


def write_result_bundle(output: Path, result: PipelineResult) -> None:
    """Persist and verify the current aggregate. Used by export and CLI finalization."""
    versions = {}
    for name in ("numpy", "opencv-python-headless", "scikit-image", "Pillow", "pydantic", "PyYAML"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    write_json(output / "environment.json", versions)
    write_json(output / "config_used.json", result.config)
    export_csv(output / "parameters.csv", result)
    result.quality.checks["csv_matches_result"] = verify_csv(output / "parameters.csv", result)
    result.quality.checks["core_artifacts_complete"] = all((output / name).is_file() for name in CORE_ARTIFACTS)
    invariants = ("csv_matches_result", "regions_inside_roi", "regions_cover_roi_without_overlap",
                  "region_mask_area_consistent", "trace_coordinates_in_bounds", "trace_lengths_valid",
                  "all_skeleton_edges_visited_once", "overlay_canvas_matches_original")
    if any(result.quality.checks.get(name) is False for name in invariants):
        raise ValueError("Consistency check failed; inspect quality.checks")
    result.artifacts = sorted({p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
                              | {"results.json", "quality_report.json", "manifest.json"})
    write_json(output / "results.json", result)
    reread = PipelineResult.model_validate_json((output / "results.json").read_text(encoding="utf-8"))
    result.quality.checks["results_json_roundtrip"] = reread.model_dump() == result.model_dump()
    write_json(output / "results.json", result)
    write_json(output / "quality_report.json", result.quality)
    build_manifest(output)


def execute_stage(stage_id: str, input_path: str | Path, output_dir: str | Path,
                  config: PipelineConfig, dependencies: dict[str, Path], *,
                  lithology_backend: SegmentationBackend | None = None,
                  fracture_backend: SegmentationBackend | None = None) -> dict[str, Any]:
    """Execute a stage into an empty directory, raising a precise failure.

    Returned values are JSON serializable. Dependencies are immutable successful
    stage directories, each with its runner-persisted ``outcome.json``.
    """
    if stage_id not in STAGE_IDS:
        raise ValueError(f"Unknown stage: {stage_id}")
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Stage output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    dependencies = {key: Path(value) for key, value in dependencies.items()}
    data: dict[str, Any] = {}
    warnings: list[str] = []
    checks: dict[str, bool | None] = {}
    method = ""

    if stage_id == "preprocessing":
        from .preprocessing import load_image
        original, working, meta, warnings = load_image(input_path, config.preprocessing)
        save_rgb(output / "original.png", original)
        save_rgb(output / "preprocessed.png", working)
        sx, sy = meta["scale_x"], meta["scale_y"]
        known = {key: meta[key] for key in ("source_size", "original_size", "working_size", "scale_x", "scale_y", "exif_transposed")}
        image = ImageMetadata(input_path=str(Path(input_path).resolve()), input_sha256=file_sha256(Path(input_path)),
                              **known, working_to_original=[[1 / sx, 0, 0], [0, 1 / sy, 0], [0, 0, 1]],
                              original_to_working=[[sx, 0, 0], [0, sy, 0], [0, 0, 1]], details=meta.get("details", {}))
        write_json(output / "image_metadata.json", image)
        data["image"] = image.model_dump()
        checks["input_readable_nonconstant"] = True
        method = "pillow_exif_resize_clahe_v1"

    elif stage_id == "face_detection":
        from .face_detection import detect_face
        image = ImageMetadata.model_validate(_data(dependencies, "preprocessing")["image"])
        working = _rgb(dependencies["preprocessing"] / "preprocessed.png")
        face = detect_face(working, config.face)
        warnings.extend(face.warnings)
        small_mask = _validate_mask(face.mask, working.shape[:2], "face")
        mask = resize_mask(small_mask, image.original_size)
        area = int(mask.sum())
        if not area:
            raise ValueError("Empty face mask")
        ratio = area / mask.size
        if not config.face.min_area_ratio <= ratio <= config.face.max_area_ratio and face.status != "full_image_fallback":
            warnings.append(f"ROI area ratio outside configured range: {ratio:.4f}")
        _save_mask(output / "face_mask.png", mask)
        # Never reconstruct the working ROI from its original-size resampling.
        _save_mask(output / "face_working_mask.png", small_mask)
        layer = _face_layer(mask)
        layer.save(output / "face_layer.png")
        original = Image.fromarray(_rgb(dependencies["preprocessing"] / "original.png")).convert("RGBA")
        Image.alpha_composite(original, layer).convert("RGB").save(output / "face_overlay.png")
        record = FaceRegion(pixel_area=area, area_ratio=ratio, polygon=mask_to_polygons(mask),
                            method=face.method, status=face.status, verified=face.verified, details=face.details)
        write_json(output / "face_region.json", record)
        data["face"] = record.model_dump()
        checks.update(face_nonempty=True, face_verified=face.verified, full_image_fallback=face.status == "full_image_fallback")
        if not face.verified:
            warnings.append("ROI unverified: densities and area fractions describe candidate analysis area, not qualified tunnel-face parameters.")
        method = face.method

    elif stage_id == "lithology_segmentation":
        from .lithology import LithologyBaseline
        image = ImageMetadata.model_validate(_data(dependencies, "preprocessing")["image"])
        face = FaceRegion.model_validate(_data(dependencies, "face_detection")["face"])
        working = _rgb(dependencies["preprocessing"] / "preprocessed.png")
        roi = _mask(dependencies["face_detection"] / "face_working_mask.png")
        face_mask = _mask(dependencies["face_detection"] / "face_mask.png")
        segmentation = (lithology_backend or LithologyBaseline()).predict(working, roi, config.lithology)
        warnings.extend(segmentation.warnings)
        raw = np.asarray(segmentation.labels)
        if raw.shape != roi.shape or not np.issubdtype(raw.dtype, np.integer) or np.any(raw < 0):
            raise ValueError("Backend must return nonnegative integer labels at working resolution")
        if np.any(raw[~roi] != 0) or np.any(raw[roi] == 0):
            raise ValueError("Lithology labels must exactly cover ROI and leave exterior 0")
        mapped = resize_labels(raw, image.original_size)
        mapped[~face_mask] = 0
        unique = [n for n in np.unique(mapped) if n > 0]
        if len(unique) > 65535:
            raise ValueError("Too many regions for uint16 label mask")
        labels = np.zeros(mapped.shape, np.int32)
        for new, old in enumerate(unique, 1):
            labels[mapped == old] = new
        Image.fromarray(labels.astype(np.uint16)).save(output / "lithology_mask.png")
        (output / "lithology_masks").mkdir()
        regions = []
        for label in range(1, len(unique) + 1):
            mask = labels == label
            area = int(mask.sum())
            mask_path = f"lithology_masks/R{label:03d}.png"
            _save_mask(output / mask_path, mask)
            regions.append(LithologyRegion(region_id=f"R{label:03d}", label_value=label,
                           polygon=mask_to_polygons(mask), pixel_area=area, area_ratio=area / face.pixel_area,
                           segmentation_method=segmentation.method, mask_path=mask_path))
        algorithm = {"method": segmentation.method, "parameters": segmentation.parameters, "details": segmentation.details}
        data.update(lithology_regions=[r.model_dump() for r in regions], algorithms={"lithology": algorithm})
        write_json(output / "lithology_regions.json", _region_document(regions, algorithm))
        save_rgb(output / "lithology_overlay.png", lithology_overlay(_rgb(dependencies["preprocessing"] / "original.png"), labels))
        _lithology_layer(labels).save(output / "lithology_layer.png")
        checks.update(regions_inside_roi=bool(np.all(labels[~face_mask] == 0)),
                      regions_cover_roi_without_overlap=bool(np.all(labels[face_mask] > 0)),
                      region_mask_area_consistent=sum(r.pixel_area for r in regions) == face.pixel_area,
                      region_area_ratios_sum_one=abs(sum(r.area_ratio for r in regions) - 1) < 1e-10,
                      no_abnormally_small_regions=all(r.pixel_area >= max(config.lithology.min_region_pixels,
                          config.lithology.min_region_area_ratio * int(roi.sum())) / (image.scale_x * image.scale_y) - 1 for r in regions))
        if not checks["no_abnormally_small_regions"]:
            warnings.append("Small residual image regions remain, e.g. disconnected ROI components; review masks.")
        method = segmentation.method

    elif stage_id == "fracture_segmentation":
        from .fracture_detection import FractureBaseline
        image = ImageMetadata.model_validate(_data(dependencies, "preprocessing")["image"])
        _data(dependencies, "face_detection")
        working = _rgb(dependencies["preprocessing"] / "preprocessed.png")
        roi = _mask(dependencies["face_detection"] / "face_working_mask.png")
        detected = (fracture_backend or FractureBaseline()).predict(working, roi, config.fracture)
        warnings.extend(detected.warnings)
        raw = _validate_mask(detected.mask, roi.shape, "fracture")
        if np.any(raw & ~roi):
            raise ValueError("Fracture backend mask extends outside ROI")
        mask = resize_mask(raw, image.original_size) & _mask(dependencies["face_detection"] / "face_mask.png")
        _save_mask(output / "fracture_mask.png", mask)
        save_rgb(output / "fracture_overlay.png", fracture_overlay(_rgb(dependencies["preprocessing"] / "original.png"), mask))
        data["algorithms"] = {"fracture": {"method": detected.method, "parameters": detected.parameters, "details": detected.details}}
        data["fracture_pixel_count"] = int(mask.sum())
        checks["fracture_mask_nonempty"] = bool(mask.any())
        if not mask.any():
            warnings.append("No candidate fracture pixels: zero detections does not establish absence of fractures.")
        method = detected.method

    elif stage_id == "trace_extraction":
        from .vectorization import SOURCE, vectorize_fractures
        image = ImageMetadata.model_validate(_data(dependencies, "preprocessing")["image"])
        detected = _data(dependencies, "fracture_segmentation")
        mask = _mask(dependencies["fracture_segmentation"] / "fracture_mask.png")
        vectorized = vectorize_fractures(mask, config.vectorization.min_length_px, config.vectorization.simplify_tolerance_px)
        _save_mask(output / "fracture_skeleton.png", vectorized["skeleton"])
        topology = vectorized["graph"]
        checks["all_skeleton_edges_visited_once"] = topology["edge_count"] == topology["visited_edge_count"]
        if not checks["all_skeleton_edges_visited_once"]:
            raise ValueError("Skeleton edge traversal is incomplete or repeated")
        fractures = []
        for record in vectorized["traces"]:
            fractures.append(FractureTrace(**record, detection_source=detected["algorithms"]["fracture"]["method"],
                             length_px=polyline_length(record["points"]), endpoint_direction_deg=axial_direction_deg(record["points"]),
                             principal_direction_deg=principal_direction_deg(record["points"])))
        data.update(fractures=[dict(t.model_dump(), raw_points=t.points) for t in fractures], topology=topology)
        write_json(output / "fracture_traces.json", _trace_document(fractures, topology))
        layer, ids = _trace_layers(image.original_size, fractures)
        layer.save(output / "trace_layer.png")
        ids.save(output / "trace_ids_layer.png")
        original = Image.fromarray(_rgb(dependencies["preprocessing"] / "original.png")).convert("RGBA")
        Image.alpha_composite(original, layer).convert("RGB").save(output / "vector_overlay.png")
        method = SOURCE

    elif stage_id == "geometry_calculation":
        _data(dependencies, "preprocessing")
        face = FaceRegion.model_validate(_data(dependencies, "face_detection")["face"])
        mask = _mask(dependencies["face_detection"] / "face_mask.png")
        lithology = _data(dependencies, "lithology_segmentation") if "lithology_segmentation" in dependencies else {}
        regions = [LithologyRegion.model_validate(record) for record in lithology.get("lithology_regions", [])] if lithology else None
        scale = config.calibration.meters_per_pixel
        if regions is not None:
            for region in regions:
                region.area_m2 = region.pixel_area * scale ** 2 if scale is not None else None
            write_json(output / "lithology_regions.json", _region_document(regions, lithology["algorithms"]["lithology"]))
        data["lithology_regions"] = [r.model_dump() for r in regions] if regions is not None else None
        if "trace_extraction" not in dependencies:
            # Missing detections are unavailable, not an empty list of observations.
            data.update(statistics=None, fractures=None)
            warnings.append("Fracture trace branch unavailable: fracture counts, lengths and density are null.")
        else:
            trace_data = _data(dependencies, "trace_extraction")
            traces = [_trace_model(record).model_dump() for record in trace_data["fractures"]]
            labels = _labels(dependencies["lithology_segmentation"] / "lithology_mask.png") if lithology else None
            statistics = calculate_statistics(traces, mask, labels, scale, face.verified)
            statistics["lithology_region_count"] = len(regions) if regions is not None else None
            fractures = [_trace_model(record) for record in traces]
            height, width = mask.shape
            checks.update(trace_coordinates_in_bounds=all(0 <= p[0] <= width and 0 <= p[1] <= height for t in fractures for p in t.points),
                          trace_lengths_valid=all(np.isfinite(t.length_px) and t.length_px > 0 for t in fractures),
                          trace_ids_unique=len({t.id for t in fractures}) == len(fractures))
            if not all(checks.values()):
                raise ValueError("Invalid fracture trace geometry")
            data.update(statistics=GeometricStatistics.model_validate(statistics).model_dump(),
                        fractures=[t.model_dump() for t in fractures])
            write_json(output / "fracture_traces.json", _trace_document(fractures, trace_data["topology"]))
        write_json(output / "geometric_statistics.json", data)
        method = "unsimplified_polyline_exact_mask_geometry_v1"

    elif stage_id == "sketch_rendering":
        _data(dependencies, "preprocessing")
        original = _rgb(dependencies["preprocessing"] / "original.png")
        result = assemble_result(input_path, config, dependencies)
        face_mask = _mask(dependencies["face_detection"] / "face_mask.png") if "face_detection" in dependencies else None
        labels = _labels(dependencies["lithology_segmentation"] / "lithology_mask.png") if "lithology_segmentation" in dependencies else None
        save_rgb(output / "geological_sketch.png", render_sketch(original, face_mask, labels, result.fractures,
                 config.calibration.meters_per_pixel, result.face.status if result.face else "unavailable"))
        layers = {}
        for stage, names in (("face_detection", ("face_layer.png",)),
                             ("lithology_segmentation", ("lithology_layer.png",)),
                             ("trace_extraction", ("trace_layer.png", "trace_ids_layer.png"))):
            if stage in dependencies:
                for name in names:
                    source = dependencies[stage] / name
                    if source.is_file():
                        shutil.copyfile(source, output / name)
                        layers[name.removesuffix(".png")] = name
        checks["overlay_canvas_matches_original"] = Image.open(output / "geological_sketch.png").size == (original.shape[1], original.shape[0])
        data["layers"] = layers
        data["available_modules"] = [stage for stage in STAGE_IDS if stage in dependencies]
        method = "pillow_original_canvas_layers_v1"

    elif stage_id == "result_export":
        # Ordered copy means geometry owns calibrated JSON without changing masks.
        for stage in STAGE_IDS[:-1]:
            if stage not in dependencies:
                continue
            directory = dependencies[stage]
            for relative in _outcome(directory).get("artifacts", []):
                source = directory / relative
                if source.is_file():
                    target = output / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        result = assemble_result(input_path, config, dependencies)
        write_result_bundle(output, result)
        data["result"] = result.model_dump()
        warnings = result.quality.warnings
        checks = result.quality.checks
        method = "schema_csv_json_sha256_export_v1"

    artifacts = sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file())
    return {"data": data, "artifacts": artifacts, "warnings": warnings, "checks": checks, "method": method}
