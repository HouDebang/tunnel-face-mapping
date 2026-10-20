"""Frozen image geometry and explicitly unverified cross-section suggestions.

These curves describe display geometry only. Matching cost is a reproducible
image-space heuristic, never geological confidence or a physical measurement.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from .export import file_sha256
from .stage_graph import SPEC
from .workspace import WorkspaceError


KINDS = {"fracture": "fractures", "lithology": "lithology"}
STAGES = {"fracture": "trace_extraction", "lithology": "lithology_segmentation"}
ARTIFACTS = {"fracture": "fracture_traces.json", "lithology": "lithology_regions.json"}
STATEMENT = "跨断面连线与曲面仅为流程示意，未验证属于同一裂隙或同一岩性界面；匹配代价不是置信度。"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class GeologyBuild(StrictModel):
    kinds: list[Literal["fracture", "lithology"]] = Field(default_factory=lambda: ["fracture", "lithology"], min_length=1, max_length=2)
    max_connections_per_pair: int = Field(default=6, ge=1, le=20)
    max_match_distance: float = Field(default=.5, ge=0, le=2)
    auto_connect: bool = True


class ConnectionCreate(StrictModel):
    kind: Literal["fracture", "lithology"]
    from_section_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    to_section_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    from_object_id: str = Field(pattern=r"^[A-Za-z0-9_:.\-]{1,128}$")
    to_object_id: str = Field(pattern=r"^[A-Za-z0-9_:.\-]{1,128}$")
    reverse_to: bool = False


class ConnectionUpdate(StrictModel):
    visible: bool | None = None
    reverse_to: bool | None = None


class GeologySettings(StrictModel):
    mode: Literal["none", "fracture", "lithology", "both"] = "both"
    opacity: float = Field(default=.35, ge=.05, le=1)
    show_source_lines: bool = True


def empty_geology():
    return {"schema_version": "1.0", "status": "illustrative_unverified", "features": {}, "connections": [],
            "settings": GeologySettings().model_dump(), "warnings": [STATEMENT]}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _snapshot_hash(snapshot):
    return _digest({key: value for key, value in snapshot.items() if key != "snapshot_sha256"})


def validate_snapshot(snapshot):
    if snapshot.get("snapshot_sha256") != _snapshot_hash(snapshot):
        raise WorkspaceError("冻结几何 SHA256 校验失败；请检查场景文件并恢复版本", 409)


def _artifact_path(workspace, task_id, artifact):
    folder = workspace._task_dir(task_id)
    path = (folder / artifact["relative_path"]).resolve()
    if not path.is_relative_to(folder) or not path.is_file() or file_sha256(path) != artifact["sha256"]:
        raise ValueError(f"依赖成果 {artifact['name']} 路径或 SHA256 校验失败")
    return path


def _sample(points, closed, maximum=64):
    """Keep exact short curves; otherwise sample continuous arclength for display."""
    cleaned = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("曲线坐标必须为二维")
        p = [float(point[0]), float(point[1])]
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in p):
            raise ValueError("几何坐标超出原始图像连续画布或不是有限数")
        if not cleaned or p != cleaned[-1]:
            cleaned.append(p)
    if closed and len(cleaned) > 1 and cleaned[-1] == cleaned[0]:
        cleaned.pop()
    if len(cleaned) < (3 if closed else 2):
        raise ValueError("曲线点数不足")
    if len(cleaned) <= maximum:
        return cleaned
    path = cleaned + [cleaned[0]] if closed else cleaned
    distances = [0.0]
    for a, b in zip(path, path[1:]):
        distances.append(distances[-1] + math.dist(a, b))
    if distances[-1] <= 0:
        raise ValueError("曲线长度为零")
    result, index = [], 1
    for i in range(maximum):
        distance = distances[-1] * i / (maximum if closed else maximum - 1)
        while index < len(distances) - 1 and distances[index] < distance:
            index += 1
        fraction = (distance - distances[index - 1]) / max(distances[index] - distances[index - 1], 1e-20)
        result.append([path[index - 1][j] + fraction * (path[index][j] - path[index - 1][j]) for j in (0, 1)])
    return result


def _bound_record(store, section):
    """Resolve exact dependency executions from the bound sketch's job events.

    Cache keys currently omit explicit dependency IDs. Job events retain them:
    recursively completed/cache-hit dependencies precede sketch's running event.
    We additionally recompute the entire sketch key with their hashes. An old
    implementation/runtime which cannot reproduce that key is unavailable.
    """
    source = section["source"]
    if source["kind"] != "sketch" or not source.get("execution_id"):
        raise ValueError("原照片断面没有绑定素描版本，不能提供地质几何")
    workspace = store.workspace
    task = workspace._load(source["task_id"])
    if task["sha256"] != source["input_sha256"]:
        raise ValueError("绑定原输入 SHA256 不一致")
    raw = workspace.root / task["id"] / task["source_name"]
    if not raw.is_file() or file_sha256(raw) != source["input_sha256"]:
        raise ValueError("原输入照片 SHA256 校验失败")
    records = {r["execution_id"]: r for r in task["history"]}
    sketch = records.get(source["execution_id"])
    if not sketch or sketch["stage_id"] != "sketch_rendering" or sketch["status"] not in {"completed", "warning"}:
        raise ValueError("绑定素描历史执行不可用")
    artifact = next((a for a in sketch["artifacts"] if a["name"] == "geological_sketch.png"), None)
    expected = source["assets"].get("sketch", {}).get("source_sha256")
    if not artifact or artifact["sha256"] != expected:
        raise ValueError("绑定素描版本与冻结纹理来源 SHA256 不一致")
    path = _artifact_path(workspace, task["id"], artifact)
    if file_sha256(path) != expected:
        raise ValueError("源素描文件 SHA256 校验失败")
    events = None
    for job in task.get("jobs", {}).values():
        for index, event in enumerate(job.get("events", [])):
            if event.get("execution_id") == sketch["execution_id"] and event.get("status") == "running":
                events = job["events"][:index]
                break
        if events is not None:
            break
    if events is None:
        raise ValueError("缺少绑定素描的依赖执行事件，无法证明几何版本一致")
    dependencies, states = {}, {}
    required = SPEC["sketch_rendering"]["depends_on"] + SPEC["sketch_rendering"]["optional_dependencies"]
    for event in events:
        stage = event.get("stage_id")
        if stage not in required:
            continue
        status = event["status"]
        record = records.get(event.get("execution_id"))
        if status in {"cache_hit", "completed", "warning"} and record and record["status"] in {"completed", "warning"}:
            dependencies[stage] = record
            states[stage] = {"status": record["status"]}
        elif status in {"failed", "blocked"}:
            dependencies.pop(stage, None)
            states[stage] = {"status": status}
    if not all(stage in states for stage in required):
        raise ValueError("绑定素描的依赖事件不完整，无法校验依赖哈希")
    for record in dependencies.values():
        for item in record["artifacts"]:
            p = _artifact_path(workspace, task["id"], item)
            if file_sha256(p) != item["sha256"]:
                raise ValueError(f"依赖 {record['stage_id']} 成果 SHA256 校验失败")
    historical_task = dict(task, config=sketch["config"])
    if workspace._cache_key(historical_task, "sketch_rendering", dependencies, states) != sketch["cache_key"]:
        raise ValueError("绑定素描依赖 cache_key 无法复现（版本、依赖或运行环境已变化）")
    return task, sketch, dependencies


def _extract(kind, record, width, height, warnings):
    data = record["data"]
    features = []
    if kind == "fracture":
        objects = sorted(data.get("fractures") or [], key=lambda x: x.get("length_px", 0), reverse=True)
        if len(objects) > 60:
            warnings.append(f"裂隙共 {len(objects)} 条，仅冻结最长 60 条作为示意。")
        for obj in objects[:60]:
            try:
                points = obj.get("raw_points") or obj["points"]
                sampled = _sample([[p[0] / width, p[1] / height] for p in points], obj.get("is_closed", False))
                features.append(dict(id=obj["id"], label=obj["id"], points=sampled, closed=bool(obj.get("is_closed", False)),
                                     source_object_id=obj["id"], source_execution_id=record["execution_id"], source_method=obj.get("detection_source") or obj.get("source")))
                if len(points) > 64:
                    warnings.append(f"{obj['id']} 超过 64 点，已等弧长采样供显示；原始长度统计未改变。")
            except (KeyError, TypeError, ValueError) as exc:
                warnings.append(f"裂隙 {obj.get('id', '?')} 几何不可用：{exc}")
    else:
        objects = sorted(data.get("regions") or data.get("lithology_regions") or [], key=lambda x: x.get("pixel_area", 0), reverse=True)
        if len(objects) > 20:
            warnings.append(f"岩性候选区域共 {len(objects)} 个，仅冻结面积最大的 20 个区域。")
        for obj in objects[:20]:
            for index, polygon in enumerate(obj.get("polygon") or []):
                try:
                    # Pixel-cell boundaries lie in [0,W] × [0,H], like the texture canvas.
                    outer = polygon["exterior"]
                    points = _sample([[p[0] / width, p[1] / height] for p in outer], True)
                    identifier = f"{obj['region_id']}:P{index}"
                    features.append(dict(id=identifier, label=identifier, points=points, closed=True, category="unknown",
                                         source_object_id=obj["region_id"], source_execution_id=record["execution_id"],
                                         source_method=obj.get("segmentation_method"), pixel_area=obj["pixel_area"]))
                    if polygon.get("holes"):
                        warnings.append(f"{identifier} 含 {len(polygon['holes'])} 个内孔，当前仅连接外环，不表示实体或体积。")
                    if len(outer) > 64:
                        warnings.append(f"{identifier} 外环已等弧长采样至 64 点，仅供示意显示。")
                except (KeyError, TypeError, ValueError) as exc:
                    warnings.append(f"岩性区域 {obj.get('region_id', '?')} 外环不可用：{exc}")
    return features


def _freeze(store, section, previous, kinds):
    if previous:
        validate_snapshot(previous)
    result = copy.deepcopy(previous) if previous else {"fractures": [], "lithology": [], "warnings": [], "provenance": {
        "task_id": section["source"]["task_id"], "sketch_execution_id": section["source"].get("execution_id"),
        "input_sha256": section["source"]["input_sha256"], "coordinate_system": "normalized_original_image_canvas",
        "sketch_source_sha256": section["source"]["assets"].get("sketch", {}).get("source_sha256"),
        "normalization": "u=x/image_width_px, v=y/image_height_px; x right, y down", "branches": {}}}
    pending = [kind for kind in kinds if result["provenance"]["branches"].get(kind, {}).get("status") != "available"]
    if not pending:
        return result
    try:
        task, sketch, dependencies = _bound_record(store, section)
    except (ValueError, KeyError, OSError, WorkspaceError) as exc:
        for kind in pending:
            result["provenance"]["branches"][kind] = {"status": "unavailable", "reason": str(exc)}
            result["warnings"].append(f"{kind} unavailable：{exc}")
        result["snapshot_sha256"] = _snapshot_hash(result)
        return result
    for kind in pending:
        record = dependencies.get(STAGES[kind])
        if not record:
            reason = "绑定素描版本没有可用的该分支依赖"
            result["provenance"]["branches"][kind] = {"status": "unavailable", "reason": reason}
            result["warnings"].append(f"{kind} unavailable：{reason}")
            continue
        artifact = next((a for a in record["artifacts"] if a["name"] == ARTIFACTS[kind]), None)
        if artifact is None:
            result["provenance"]["branches"][kind] = {"status": "unavailable", "reason": "依赖几何 JSON 缺失"}
            result["warnings"].append(f"{kind} unavailable：依赖几何 JSON 缺失")
            continue
        # Read the hashed JSON, rather than trusting duplicate data in task.json.
        path = _artifact_path(store.workspace, task["id"], artifact)
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("coordinate_system") != "original_image_pixels":
            raise WorkspaceError("几何 JSON 坐标定义不受支持", 409)
        frozen_record = dict(record, data=document)
        result[KINDS[kind]] = _extract(kind, frozen_record, section["source"]["image_width_px"], section["source"]["image_height_px"], result["warnings"])
        result["provenance"]["branches"][kind] = {"status": "available", "execution_id": record["execution_id"],
            "cache_key": record["cache_key"], "artifact_sha256": artifact["sha256"], "artifact_name": artifact["name"],
            "sketch_cache_key": sketch["cache_key"], "dependency_verification": "job_execution_ids_and_sketch_cache_key"}
        if not result[KINDS[kind]]:
            result["warnings"].append(f"{kind}：该版本没有可用于连接的曲线。")
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    result["snapshot_sha256"] = _snapshot_hash(result)
    return result


def _cost(a, b):
    def descriptor(feature):
        points = feature["points"]
        center = [sum(p[j] for p in points) / len(points) for j in (0, 1)]
        length = sum(math.dist(p, q) for p, q in zip(points, points[1:]))
        if feature["closed"]:
            length += math.dist(points[-1], points[0])
        return center, length
    ca, la = descriptor(a)
    cb, lb = descriptor(b)
    return math.dist(ca, cb) + .1 * abs(la - lb) / max(la, lb, 1e-12)


def _signature(connection):
    endpoints = sorted([(connection["from_section_id"], connection["from_object_id"]), (connection["to_section_id"], connection["to_object_id"])])
    return connection["kind"], tuple(endpoints)


def validate_connection(scene, body):
    if body.from_section_id == body.to_section_id:
        raise WorkspaceError("连接必须引用两个不同断面", 422)
    sections = {s["id"]: s for s in scene["sections"]}
    objects = []
    for section_id, object_id in ((body.from_section_id, body.from_object_id), (body.to_section_id, body.to_object_id)):
        if section_id not in sections:
            raise WorkspaceError("连接引用的断面不存在", 422)
        snapshot = scene.get("geology", {}).get("features", {}).get(section_id)
        if not snapshot:
            raise WorkspaceError("请先构建示意以冻结所选断面的几何", 409)
        validate_snapshot(snapshot)
        feature = next((f for f in snapshot[KINDS[body.kind]] if f["id"] == object_id), None)
        if feature is None:
            raise WorkspaceError("连接引用的地质对象不存在或种类不一致", 422)
        objects.append(feature)
    if objects[0]["closed"] != objects[1]["closed"]:
        raise WorkspaceError("开曲线与闭合曲线不能配对，请选择相同开闭类型的对象", 422)
    if sections[body.from_section_id]["chainage_m"] == sections[body.to_section_id]["chainage_m"]:
        raise WorkspaceError("连接必须位于不同里程，重叠断面不能形成沿隧道的示意面", 422)


def make_connection(body, method="manual", cost=None):
    return dict(id=uuid.uuid4().hex, **body.model_dump(), method=method, match_cost=cost,
                visible=True, status="illustrative_unverified")


def build_geology(store, scene, body):
    scene = copy.deepcopy(scene)
    geology = scene.setdefault("geology", empty_geology())
    kinds = list(dict.fromkeys(body.kinds))
    warnings = [STATEMENT]
    visible = [s for s in scene["sections"] if s["visible"]]
    if not visible:
        raise WorkspaceError("至少需要一个可见断面才能读取源对象", 409)
    if body.auto_connect and len(visible) < 2:
        raise WorkspaceError("至少需要两个可见、不同里程且绑定素描几何的断面才能构建示意", 409)
    for section in visible:
        # Independently frozen textures must remain intact even after source reruns.
        for layer in section["source"]["assets"]:
            store.texture(scene["id"], section["id"], layer)
        snapshot = _freeze(store, section, geology["features"].get(section["id"]), kinds)
        geology["features"][section["id"]] = snapshot
        warnings.extend(f"{section['label']}：{w}" for w in snapshot["warnings"])
    if not body.auto_connect:
        warnings.append("本次只冻结源对象，未自动建立或替换连接；可手动选择不同里程的对象。")
        geology["warnings"] = list(dict.fromkeys(warnings))
        geology["build_parameters"] = body.model_dump()
        return scene
    usable_any = [s for s in visible if any(geology["features"][s["id"]][KINDS[k]] for k in kinds)]
    if len({s["chainage_m"] for s in usable_any}) < 2:
        detail = "; ".join(warnings[1:5])
        raise WorkspaceError("不足两个不同里程的可用几何断面。" + detail, 409)
    duplicates = len({s["chainage_m"] for s in usable_any}) != len(usable_any)
    if duplicates:
        raise WorkspaceError("同里程有多个可见断面，请先隐藏重复断面或手动指定连接", 409)
    retained = [c for c in geology["connections"] if c["kind"] not in kinds or c["method"] == "manual"]
    signatures = {_signature(c) for c in retained}
    for kind in kinds:
        usable = sorted([s for s in visible if geology["features"][s["id"]][KINDS[kind]]], key=lambda s: s["chainage_m"])
        if len(usable) < 2:
            warnings.append(f"{kind}：不足两个可用断面，本次未生成此分支的连接。")
            continue
        for left, right in zip(usable, usable[1:]):
            if left["source"]["input_sha256"] == right["source"]["input_sha256"]:
                warnings.append(f"{left['label']} → {right['label']} 使用同一原始照片；属于重复源效果演示，不能当作独立地质观测。")
            a = geology["features"][left["id"]][KINDS[kind]]
            b = geology["features"][right["id"]][KINDS[kind]]
            candidates = sorted((_cost(x, y), x["id"], y["id"]) for x in a for y in b if x["closed"] == y["closed"])
            used_a, used_b, count = set(), set(), 0
            for existing in retained:
                if existing["kind"] != kind:
                    continue
                if existing["from_section_id"] == left["id"] and existing["to_section_id"] == right["id"]:
                    used_a.add(existing["from_object_id"])
                    used_b.add(existing["to_object_id"])
                elif existing["from_section_id"] == right["id"] and existing["to_section_id"] == left["id"]:
                    used_a.add(existing["to_object_id"])
                    used_b.add(existing["from_object_id"])
            for cost, aid, bid in candidates:
                if count >= body.max_connections_per_pair or cost > body.max_match_distance:
                    break
                if aid in used_a or bid in used_b:
                    continue
                connection = make_connection(ConnectionCreate(kind=kind, from_section_id=left["id"], to_section_id=right["id"],
                                             from_object_id=aid, to_object_id=bid), "auto_nearest_normalized_v1", cost)
                if _signature(connection) in signatures:
                    continue
                retained.append(connection)
                signatures.add(_signature(connection))
                used_a.add(aid)
                used_b.add(bid)
                count += 1
            if count == 0:
                warnings.append(f"{kind} · {left['label']} → {right['label']}：没有满足当前代价阈值及开闭曲线规则的新匹配。")
    geology["connections"] = retained
    geology["warnings"] = list(dict.fromkeys(warnings))
    geology["build_parameters"] = body.model_dump()
    return scene
