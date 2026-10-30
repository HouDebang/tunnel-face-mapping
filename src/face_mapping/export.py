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
    # Geometry submodule summaries are also flattened into the CSV for field
    # review and spreadsheet use.  The same values are present as nullable
    # fields on GeometricStatistics, so CSV rows remain directly auditable
    # against results.json.
    for metric, unit in (
        ("mean_roughness_index", "dimensionless"),
        ("mean_aperture_px", "px"),
        ("max_aperture_px", "px"),
        ("mean_aperture_m", "m"),
        ("max_aperture_m", "m"),
        ("image_strike_deg", "degree"),
        ("strike_azimuth_deg", "degree"),
        ("dip_deg", "degree"),
        ("attitude_confidence", "ratio"),
    ):
        add("summary", "all", metric, getattr(stat, metric) if stat else None,
            unit, stat.statistics_status if stat else "unavailable")
    for fracture in result.fractures or []:
        for metric, unit in (("length_px","px"),("length_m","m"),("endpoint_direction_deg","degree"),("principal_direction_deg","degree")):
            add("fracture", fracture.id, metric, getattr(fracture,metric), unit, fracture.status)
        add("fracture", fracture.id, "roughness_index", fracture.roughness_index, "dimensionless", fracture.roughness_status or fracture.status)
        add("fracture", fracture.id, "aperture_mean_px", fracture.aperture_mean_px, "px", fracture.aperture_status or fracture.status)
        add("fracture", fracture.id, "aperture_max_px", fracture.aperture_max_px, "px", fracture.aperture_status or fracture.status)
        add("fracture", fracture.id, "aperture_mean_m", fracture.aperture_mean_m, "m", fracture.aperture_status or fracture.status)
        add("fracture", fracture.id, "aperture_max_m", fracture.aperture_max_m, "m", fracture.aperture_status or fracture.status)
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


def _site_field_records():
    """Load the source-traceable 28-field record contract when available."""
    seed = Path(__file__).resolve().parents[2] / "configs" / "knowledge" / "site_records.json"
    try:
        payload = json.loads(seed.read_text(encoding="utf-8"))
        return list(payload.get("records") or [])
    except (OSError, ValueError, TypeError):
        return []


def _judgment_regions(judgment):
    if not isinstance(judgment, dict):
        return []
    regions = judgment.get("regions")
    if isinstance(regions, list):
        return regions
    if isinstance(regions, dict):
        return [dict(value, region_id=key) for key, value in regions.items() if isinstance(value, dict)]
    return []


def _sketch_form_values(result: PipelineResult, judgment=None):
    stat = result.statistics
    regions = result.lithology_regions or []
    fractures = result.fractures or []
    judgment_regions = _judgment_regions(judgment)
    labels = []
    for region in judgment_regions:
        final = region.get("final_judgment") or {}
        candidates = region.get("candidates") or []
        label = final.get("final_lithology") or final.get("category")
        if not label and candidates:
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            label = first.get("lithology") or first.get("label")
        if label:
            labels.append(f"{region.get('region_id', '区域')}: {label}")
    region_label = "；".join(labels) if labels else None
    fracture_count = stat.fracture_count if stat else len(fractures)
    total_length = stat.total_length_px if stat else sum(float(item.length_px or 0) for item in fractures)
    density = stat.p21_per_px if stat else None
    face_status = getattr(result.face, "status", None) if result.face else None
    submodules = (stat.details.get("submodules", {}) if stat else {})
    roughness = submodules.get("roughness", {})
    aperture = submodules.get("aperture", {})
    attitude = submodules.get("layer_attitude", {})
    roughness_value = None
    if stat and stat.mean_roughness_index is not None:
        roughness_value = f"图像候选指数 {stat.mean_roughness_index:.4f}（未标定，不等同 JRC）"
    aperture_value = None
    if stat and stat.mean_aperture_m is not None:
        aperture_value = f"平均可见带宽 {stat.mean_aperture_m * 1000:.3f} mm（Mask 候选）"
    elif stat and stat.mean_aperture_px is not None:
        aperture_value = f"平均可见带宽 {stat.mean_aperture_px:.3f} px（缺少尺度标定）"
    attitude_value = None
    if stat and stat.image_strike_deg is not None:
        projected = "未提供方位元数据" if stat.strike_azimuth_deg is None else f"水平投影 {stat.strike_azimuth_deg:.2f}°"
        attitude_value = f"图像平面主方向 {stat.image_strike_deg:.2f}°；{projected}；倾角未定"
    description_parts = [f"图像候选分区 {len(regions)} 个"]
    if fracture_count is not None:
        description_parts.append(f"候选裂隙迹线 {fracture_count} 条")
    if total_length is not None:
        description_parts.append(f"迹线总长 {total_length:.2f} px")
    if density is not None:
        description_parts.append(f"像素 P21={density:.6f} px^-1")
    if face_status:
        description_parts.append(f"掌子面范围状态：{face_status}")
    if region_label:
        description_parts.append(f"岩性判定候选：{region_label}")
    return {
        "工程名称": None,
        "工程部位": None,
        "试验条件": None,
        "委托编号": None,
        "检测日期": None,
        "检测依据": None,
        "判定依据": None,
        "主要仪器设备名称": None,
        "设备编号": None,
        "岩土类型（名称）": region_label,
        "岩石硬度": None,
        "掌子面状态": "影像候选范围已生成，稳定性需现场确认" if result.face else None,
        "地质构造影响程度": f"检测到 {fracture_count} 条候选裂隙迹线，工程影响程度需地质判读" if fracture_count is not None else None,
        "间距（m）": None,
        "延伸性": f"候选迹线总长 {total_length:.2f} px；不等于完整延续性" if total_length is not None else None,
        "粗糙度": roughness_value,
        "张开性（mm）": aperture_value,
        "风化程度": None,
        "完整状态": None,
        "岩层产状": attitude_value,
        "围岩级别": None,
        "岩体含水状态/渗水量": None,
        "开挖工作面素描图": "geological_sketch.png",
        "地质简要描述": "；".join(description_parts),
        "附加声明": "本表自动填充项来自照片候选识别；需外部测试或地质复核的字段保持空值。",
        "记录人": None,
        "监理员": None,
        "签署日期": None,
    }


def geological_sketch_form(result: PipelineResult, judgment=None):
    """Build the 28-field sketch form without inventing unavailable attributes."""
    values = _sketch_form_values(result, judgment)
    judgment_regions = _judgment_regions(judgment)
    judgment_ready = bool(judgment_regions)
    rows = []
    for record in _site_field_records():
        field = record.get("field")
        value = values.get(field)
        auto = value is not None
        if field == "岩土类型（名称）" and value and not judgment_ready:
            auto = False
        if field in {"粗糙度", "张开性（mm）", "岩层产状"} and value:
            # These are useful candidate observations, but remain review items
            # until a calibrated profile, visible aperture, and confirmed
            # bedding/3-D orientation are supplied.
            auto = False
        status = "auto_filled" if auto else ("needs_external_input" if record.get("obtainability") not in {"影像辅助", "辅助生成", "组合成果生成"} else "candidate_or_review")
        source = "pipeline_candidate_result" if auto else ("knowledge_schema_only" if record else "unavailable")
        rows.append({
            "record_id": record.get("record_id"), "field": field,
            "category": record.get("category"), "obtainability": record.get("obtainability"),
            "value": value, "status": status, "source": source,
            "note": record.get("note"), "option_values": record.get("option_values") or [],
        })
    return {
        "schema_version": "geological_sketch_form_v1",
        "coordinate_system": result.coordinate_system,
        "status": "candidate_form_with_review" if rows else "unavailable",
        "notice": "自动填充字段来自当前成果；空值表示没有可靠影像证据或需要现场/管理资料。",
        "fields": rows,
    }


def write_geological_sketch_form(directory: Path, result: PipelineResult, judgment=None):
    form = geological_sketch_form(result, judgment)
    write_json(directory / "geological_sketch_form.json", form)
    with (directory / "geological_sketch_form.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["record_id", "field", "category", "obtainability", "value", "status", "source", "note"])
        writer.writeheader()
        for row in form["fields"]:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})
    return form


def build_manifest(output_dir: Path):
    entries = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name not in {"manifest.json", "execution.log"}:
            entries.append({"path":path.relative_to(output_dir).as_posix(),"bytes":path.stat().st_size,"sha256":file_sha256(path)})
    write_json(output_dir / "manifest.json", {"algorithm":"sha256", "files":entries, "excluded":["manifest.json","execution.log"]})
