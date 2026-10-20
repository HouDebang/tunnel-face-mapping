"""Strict JSON, normalized CSV and artifact hashes."""
import csv
import hashlib
import json
from pathlib import Path
from typing import Any
from .schemas import PipelineResult


def write_json(path: Path, value: Any):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parameter_rows(result: PipelineResult):
    rows = []
    def add(kind, identifier, metric, value, unit, status):
        rows.append(dict(object_type=kind, object_id=identifier, metric=metric, value=value, unit=unit, status=status))
    # A failed geometry stage exports null scalars, never invented zero counts.
    stat = result.statistics
    units = {"fracture_count":"count", "lithology_region_count":"count", "total_length_px":"px", "observation_area_px2":"px2", "p21_per_px":"px^-1", "total_length_m":"m", "observation_area_m2":"m2", "p21_per_m":"m^-1"}
    for metric, unit in units.items():
        add("summary", "all", metric, getattr(stat, metric) if stat else None, unit, stat.statistics_status if stat else "unavailable")
    for fracture in result.fractures or []:
        for metric, unit in (("length_px","px"),("length_m","m"),("endpoint_direction_deg","degree"),("principal_direction_deg","degree")):
            add("fracture", fracture.id, metric, getattr(fracture,metric), unit, fracture.status)
    for region in result.lithology_regions or []:
        for metric, unit in (("pixel_area","px2"),("area_ratio","ratio"),("area_m2","m2")):
            add("region", region.region_id, metric, getattr(region,metric),unit,region.status)
    return rows


def export_csv(path: Path, result: PipelineResult):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["object_type","object_id","metric","value","unit","status"])
        writer.writeheader()
        writer.writerows(parameter_rows(result))


def verify_csv(path: Path, result: PipelineResult) -> bool:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        actual = list(csv.DictReader(handle))
    expected = parameter_rows(result)
    if len(actual) != len(expected):
        return False
    for row, target in zip(actual, expected):
        for key, value in target.items():
            if value is None:
                if row[key] != "":
                    return False
            elif isinstance(value,(int,float)):
                if float(row[key]) != value:
                    return False
            elif row[key] != str(value):
                return False
    return True


def build_manifest(output_dir: Path):
    entries = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name not in {"manifest.json", "execution.log"}:
            entries.append({"path":path.relative_to(output_dir).as_posix(),"bytes":path.stat().st_size,"sha256":file_sha256(path)})
    write_json(output_dir / "manifest.json", {"algorithm":"sha256", "files":entries, "excluded":["manifest.json","execution.log"]})
