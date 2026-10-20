"""Legacy one-command runner over the same immutable stages as the workbench."""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from time import perf_counter

from .backends import SegmentationBackend
from .config import PipelineConfig
from .export import write_json
from .schemas import PipelineResult, StepStatus
from .stage_executor import assemble_result, execute_stage, write_result_bundle

LOG = logging.getLogger("face_mapping")


def run_pipeline(input_path: str | Path, output_dir: str | Path,
                 config: PipelineConfig | None = None, *,
                 lithology_backend: SegmentationBackend | None = None,
                 fracture_backend: SegmentationBackend | None = None) -> PipelineResult:
    """Process a photo with independent branch failures and old CLI output paths.

    Successful stages execute once in temporary immutable directories. The
    shared export stage assembles the final flat directory. A nonempty output is
    never reused, preventing stale results from being mistaken for current ones.
    """
    config = config or PipelineConfig()
    source, output = Path(input_path).resolve(), Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output directory must be empty: {output}. Use a new run directory.")
    output.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(output / "execution.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    dependencies: dict[str, Path] = {}
    steps: list[StepStatus] = []
    errors: list[str] = []
    plan = (
        ("preprocessing", "01_preprocessing", ()),
        ("face_detection", "02_face_detection", ("preprocessing",)),
        ("lithology_segmentation", "03a_lithology", ("preprocessing", "face_detection")),
        ("fracture_segmentation", "03b_fracture_detection", ("preprocessing", "face_detection")),
        ("trace_extraction", "03c_vectorization", ("preprocessing", "fracture_segmentation")),
        ("geometry_calculation", "04_geometry", ("preprocessing", "face_detection", "trace_extraction")),
        ("sketch_rendering", "05_rendering", ("preprocessing",)),
        ("result_export", "06_structured_export", ()),
    )
    try:
        with tempfile.TemporaryDirectory(prefix="face_mapping_stages_") as temporary:
            for stage, legacy_name, required in plan:
                started = perf_counter()
                description = str(source) if stage == "preprocessing" else "current original-coordinate stage artifacts"
                if not all(name in dependencies for name in required):
                    steps.append(StepStatus(step=legacy_name, status="skipped", input=description,
                                            error="Required upstream output unavailable"))
                    continue
                directory = Path(temporary) / stage
                LOG.info("%s started", legacy_name)
                try:
                    outcome = execute_stage(stage, source, directory, config, dependencies,
                                            lithology_backend=lithology_backend,
                                            fracture_backend=fracture_backend)
                    write_json(directory / "outcome.json", outcome)
                    dependencies[stage] = directory
                    steps.append(StepStatus(step=legacy_name, status="ok", input=description,
                                            output=outcome["artifacts"], elapsed_seconds=perf_counter() - started))
                    LOG.info("%s completed in %.3fs", legacy_name, perf_counter() - started)
                except Exception as exc:
                    error = f"{legacy_name}: {type(exc).__name__}: {exc}"
                    errors.append(error)
                    steps.append(StepStatus(step=legacy_name, status="failed", input=description,
                                            output=[p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()] if directory.exists() else [],
                                            elapsed_seconds=perf_counter() - started, error=error))
                    LOG.exception(error)
            export_dir = dependencies.get("result_export")
            directories = [export_dir] if export_dir is not None else list(dependencies.values())
            for directory in directories:
                for path in directory.rglob("*"):
                    if not path.is_file() or path.name == "outcome.json":
                        continue
                    target = output / path.relative_to(directory)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
            quality_started = perf_counter()
            steps.append(StepStatus(step="07_quality_checks", status="ok", input="saved artifacts and data contracts",
                                    output=["quality_report.json", "results.json", "manifest.json"]))
            result = assemble_result(source, config, dependencies, steps, errors)
            try:
                write_result_bundle(output, result)
                steps[-1].elapsed_seconds = perf_counter() - quality_started
                result.quality.steps = steps
                write_result_bundle(output, result)
            except Exception as exc:
                error = f"07_quality_checks: {type(exc).__name__}: {exc}"
                steps[-1].status, steps[-1].error = "failed", error
                result.quality.errors.append(error)
                result.status = result.quality.status = "partial" if result.image else "failed"
                result.quality.steps = steps
                write_json(output / "results.json", result)
                write_json(output / "quality_report.json", result.quality)
                LOG.exception(error)
            LOG.info("Run finished: %s", result.status)
            return result
    finally:
        handler.close()
        LOG.removeHandler(handler)
