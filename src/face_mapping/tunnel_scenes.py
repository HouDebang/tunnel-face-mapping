"""Independent illustrative tunnel scenes with immutable, local image snapshots.

Scene metres describe display placement only; they never calibrate the source
photograph or change its geological measurements. This local service follows
the workbench's single-process locking model.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
from typing import Literal
import uuid

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .export import file_sha256
from .workspace import WorkspaceError
from .tunnel_geology import (ConnectionCreate, ConnectionUpdate, GeologyBuild, GeologySettings,
                             build_geology, empty_geology, make_connection, validate_connection, validate_snapshot, _signature)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SceneSettings(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    chainage_prefix: str = Field(default="K", min_length=1, max_length=16)
    start_chainage_m: float = Field(default=0, ge=-10_000_000, le=10_000_000)
    end_chainage_m: float = Field(default=60, ge=-10_000_000, le=10_000_000)
    width_m: float = Field(default=10, ge=.1, le=200)
    height_m: float = Field(default=8, ge=.1, le=200)

    @field_validator("name", "chainage_prefix")
    @classmethod
    def meaningful_text(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("名称或里程前缀不能为空或包含控制字符")
        return value

    @model_validator(mode="after")
    def increasing_chainage(self):
        if not .1 <= self.end_chainage_m - self.start_chainage_m <= 100_000:
            raise ValueError("终点里程必须大于起点，示意段长应在 0.1～100000 米之间")
        return self


class SceneUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    chainage_prefix: str | None = Field(default=None, min_length=1, max_length=16)
    start_chainage_m: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    end_chainage_m: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    width_m: float | None = Field(default=None, ge=.1, le=200)
    height_m: float | None = Field(default=None, ge=.1, le=200)


class Placement(StrictModel):
    chainage_m: float = Field(ge=-10_000_000, le=10_000_000)
    label: str = Field(default="", max_length=120)
    display_width_m: float | None = Field(default=None, ge=.01, le=1000)
    display_height_m: float | None = Field(default=None, ge=.01, le=1000)
    offset_u_m: float = Field(default=0, ge=-1000, le=1000)
    offset_v_m: float = Field(default=0, ge=-1000, le=1000)
    rotation_deg: float = Field(default=0, ge=-360, le=360)
    flip_horizontal: bool = False
    visible: bool = True
    demo_copy: bool = False


class SectionCreate(Placement):
    task_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    source_kind: Literal["sketch", "original"] = "sketch"
    execution_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class SectionUpdate(StrictModel):
    chainage_m: float | None = Field(default=None, ge=-10_000_000, le=10_000_000)
    label: str | None = Field(default=None, max_length=120)
    display_width_m: float | None = Field(default=None, ge=.01, le=1000)
    display_height_m: float | None = Field(default=None, ge=.01, le=1000)
    offset_u_m: float | None = Field(default=None, ge=-1000, le=1000)
    offset_v_m: float | None = Field(default=None, ge=-1000, le=1000)
    rotation_deg: float | None = Field(default=None, ge=-360, le=360)
    flip_horizontal: bool | None = None
    visible: bool | None = None
    demo_copy: bool | None = None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(value):
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise WorkspaceError("场景或断面不存在", 404)
    return value


def _atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate(model, value):
    # ValidationError inside a sync handler otherwise becomes an HTTP 500.
    from pydantic import ValidationError
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(e["msg"] for e in exc.errors())) from exc


class TunnelSceneStore:
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = workspace.root / ".tunnel-scenes"
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()

    def _folder(self, scene_id):
        path = (self.root / _id(scene_id)).resolve()
        if path.parent != self.root.resolve():
            raise WorkspaceError("场景路径无效", 404)
        return path

    def _read(self, scene_id):
        path = self._folder(scene_id) / "scene.json"
        if not path.is_file():
            raise WorkspaceError("隧道场景不存在", 404)
        try:
            scene = json.loads(path.read_text(encoding="utf-8"))
            _validate(SceneSettings, {k: scene[k] for k in SceneSettings.model_fields})
            if scene["id"] != scene_id:
                raise ValueError("场景 ID 不匹配")
            for snapshot in scene.get("geology", {}).get("features", {}).values():
                validate_snapshot(snapshot)
            return scene
        except (ValueError, KeyError, OSError) as exc:
            raise WorkspaceError(f"隧道场景文件无法读取：{exc}", 409) from exc

    def _save(self, scene):
        scene["updated_at"] = _now()
        _atomic_json(self._folder(scene["id"]) / "scene.json", scene)
        return scene

    def list(self):
        with self.lock:
            summaries = []
            for path in self.root.glob("*/scene.json"):
                if not re.fullmatch(r"[0-9a-f]{32}", path.parent.name):
                    continue
                scene = self._read(path.parent.name)
                summaries.append({k: scene[k] for k in ("id", "name", "chainage_prefix", "start_chainage_m", "end_chainage_m", "width_m", "height_m", "updated_at", "illustrative")})
                summaries[-1]["section_count"] = len(scene["sections"])
            return sorted(summaries, key=lambda x: x["updated_at"], reverse=True)

    def create(self, settings):
        with self.lock:
            scene_id = uuid.uuid4().hex
            self._folder(scene_id).mkdir()
            scene = dict(id=scene_id, **settings.model_dump(), sections=[], schema_version="1.0", created_at=_now(),
                         coordinate_system="local_tunnel_m", coordinate_axes={"x": "increasing_chainage", "y": "up", "z": "section_image_right"},
                         illustrative=True, geometry_method="straight_illustrative_tunnel", alignment_status="illustrative_unregistered",
                         quality_notes=["直线隧道与断面尺寸仅用于流程及效果示意。", "断面显示米数不构成原照片尺度标定；未更改地质参数。", "照片朝向、投影及真实地理坐标尚未校核。"])
            scene["geology"] = empty_geology()
            return self._save(scene)

    def get(self, scene_id):
        with self.lock:
            return self._read(scene_id)

    @staticmethod
    def _range(scene, chainage):
        if not scene["start_chainage_m"] <= chainage <= scene["end_chainage_m"]:
            raise WorkspaceError("断面里程必须处于当前隧道起止里程之间", 422)

    @staticmethod
    def _position(scene, section):
        section["position_m"] = [section["chainage_m"] - scene["start_chainage_m"],
                                 scene["height_m"] * .4 + section["offset_v_m"], section["offset_u_m"]]

    def update(self, scene_id, patch):
        with self.lock:
            scene = self._read(scene_id)
            settings = {k: scene[k] for k in SceneSettings.model_fields}
            settings.update(patch.model_dump(exclude_unset=True))
            settings = _validate(SceneSettings, settings).model_dump()
            candidate = dict(scene, **settings)
            for section in candidate["sections"]:
                self._range(candidate, section["chainage_m"])
                self._position(candidate, section)
            return self._save(candidate)

    def _snapshot(self, scene_id, section_id, layer, path, expected_hash):
        if not path.is_file() or file_sha256(path) != expected_hash:
            raise WorkspaceError("源成果内容校验失败；请重新生成并检查后再添加断面", 409)
        destination = self._folder(scene_id) / f"{section_id}-{layer}.jpg"
        temporary = destination.with_suffix(".tmp")
        try:
            with Image.open(path) as source:
                dimensions = source.size
                image = source.convert("RGB")
                image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                image.save(temporary, format="JPEG", quality=90, optimize=True)
            if file_sha256(path) != expected_hash:
                raise WorkspaceError("冻结期间源成果发生变化，请重试", 409)
            os.replace(temporary, destination)
        except (OSError, ValueError) as exc:
            raise WorkspaceError(f"断面纹理无法解码或保存：{exc}", 409) from exc
        finally:
            temporary.unlink(missing_ok=True)
        return {"url": f"/api/tunnel-scenes/{scene_id}/sections/{section_id}/texture?layer={layer}",
                "sha256": file_sha256(destination), "source_sha256": expected_hash,
                "texture_width_px": image.width, "texture_height_px": image.height}, dimensions

    def add_section(self, scene_id, body):
        with self.lock, self.workspace.lock:
            scene = self._read(scene_id)
            self._range(scene, body.chainage_m)
            task = self.workspace.get_task(body.task_id)
            if body.source_kind == "original" and body.execution_id is not None:
                raise WorkspaceError("原照片无需阶段执行编号；请选择素描成果或清空 execution_id", 422)
            sketch_record = None
            if body.source_kind == "sketch":
                if body.execution_id:
                    sketch_record = self.workspace.history(body.task_id, body.execution_id)
                    if sketch_record["stage_id"] != "sketch_rendering" or sketch_record["status"] not in {"completed", "warning"}:
                        raise WorkspaceError("所选历史运行不是成功的地质素描成果", 409)
                else:
                    sketch_record = task["stages"]["sketch_rendering"]
                    if sketch_record["status"] not in {"completed", "warning"}:
                        raise WorkspaceError("尚无有效地质素描，请先运行素描生成步骤，或选择原照片展示", 409)
            section_id = uuid.uuid4().hex
            original_path, _ = self.workspace.artifact_path(body.task_id, "original")
            raw_path, _ = self.workspace.artifact_path(body.task_id, "source")
            if file_sha256(raw_path) != task["sha256"]:
                raise WorkspaceError("原输入照片的 SHA256 校验失败", 409)
            original, dimensions = self._snapshot(scene_id, section_id, "original", original_path, file_sha256(original_path))
            assets = {"original": original}
            execution_id = None
            if sketch_record:
                artifact = next((a for a in sketch_record["artifacts"] if a["name"] == "geological_sketch.png"), None)
                if artifact is None:
                    raise WorkspaceError("素描运行缺少 geological_sketch.png 成果", 409)
                execution_id = sketch_record["execution_id"]
                path, _ = self.workspace.artifact_path(body.task_id, artifact["id"], execution_id)
                assets["sketch"], dimensions = self._snapshot(scene_id, section_id, "sketch", path, artifact["sha256"])
            section = body.model_dump(exclude={"task_id", "source_kind", "execution_id"})
            section["label"] = body.label.strip() or f"掌子面 · {scene['chainage_prefix']} {body.chainage_m:g} m"
            ratio = dimensions[0] / dimensions[1]
            width, height = section["display_width_m"], section["display_height_m"]
            if width is None and height is None:
                width = min(scene["width_m"] * .72, scene["height_m"] * .7 * ratio)
                height = width / ratio
            elif width is None:
                width = height * ratio
            elif height is None:
                height = width / ratio
            section.update(id=section_id, display_width_m=width, display_height_m=height,
                           alignment_status="illustrative_unregistered", source=dict(task_id=body.task_id, execution_id=execution_id,
                           kind=body.source_kind, filename=task["filename"], input_sha256=task["sha256"],
                           image_width_px=dimensions[0], image_height_px=dimensions[1], assets=assets))
            _validate(Placement, {k: section[k] for k in Placement.model_fields})
            self._position(scene, section)
            scene["sections"].append(section)
            return self._save(scene)

    @staticmethod
    def _section(scene, section_id):
        _id(section_id)
        section = next((s for s in scene["sections"] if s["id"] == section_id), None)
        if section is None:
            raise WorkspaceError("断面不存在", 404)
        return section

    def update_section(self, scene_id, section_id, patch):
        with self.lock:
            scene = self._read(scene_id)
            section = self._section(scene, section_id)
            changes = {k: section[k] for k in Placement.model_fields}
            changes.update(patch.model_dump(exclude_unset=True))
            updated = _validate(Placement, changes).model_dump()
            # An existing display always has explicit dimensions; null is not a resize.
            if updated["display_width_m"] is None or updated["display_height_m"] is None:
                raise WorkspaceError("已有断面必须提供有效的显示宽高", 422)
            self._range(scene, updated["chainage_m"])
            by_id = {s["id"]: s for s in scene["sections"]}
            for connection in scene.get("geology", {}).get("connections", []):
                endpoints = {connection["from_section_id"], connection["to_section_id"]}
                if section_id in endpoints:
                    other = next(iter(endpoints - {section_id}))
                    if by_id[other]["chainage_m"] == updated["chainage_m"]:
                        raise WorkspaceError("此断面已有地质连接，不能改为另一端相同里程；请先删除该连接", 422)
            section.update(updated)
            self._position(scene, section)
            return self._save(scene)

    def remove_section(self, scene_id, section_id):
        with self.lock:
            scene = self._read(scene_id)
            section = self._section(scene, section_id)
            scene["sections"].remove(section)
            # Retain the full record and frozen textures for local recovery.
            scene.setdefault("removed_sections", []).append(dict(section, removed_at=_now()))
            if "geology" in scene:
                geology = scene["geology"]
                removed = [c for c in geology["connections"] if section_id in {c["from_section_id"], c["to_section_id"]}]
                geology["connections"] = [c for c in geology["connections"] if c not in removed]
                geology.setdefault("removed_connections", []).extend(dict(c, removed_at=_now(), reason="section_removed") for c in removed)
                feature = geology["features"].pop(section_id, None)
                if feature:
                    geology.setdefault("removed_features", {})[section_id] = feature
            return self._save(scene)

    def build_geology(self, scene_id, body):
        with self.lock, self.workspace.lock:
            return self._save(build_geology(self, self._read(scene_id), body))

    def add_connection(self, scene_id, body):
        with self.lock:
            scene = self._read(scene_id)
            validate_connection(scene, body)
            connection = make_connection(body)
            connections = scene["geology"]["connections"]
            if any(_signature(c) == _signature(connection) for c in connections):
                raise WorkspaceError("该对象连接已存在（含反向引用），请编辑已有连接", 409)
            connections.append(connection)
            return self._save(scene)

    def update_connection(self, scene_id, connection_id, patch):
        with self.lock:
            scene = self._read(scene_id)
            _id(connection_id)
            connection = next((c for c in scene.get("geology", {}).get("connections", []) if c["id"] == connection_id), None)
            if not connection:
                raise WorkspaceError("地质示意连接不存在", 404)
            changes = patch.model_dump(exclude_unset=True)
            if any(value is None for value in changes.values()):
                raise WorkspaceError("显隐与反向设置必须为布尔值", 422)
            connection.update(changes)
            return self._save(scene)

    def remove_connection(self, scene_id, connection_id):
        with self.lock:
            scene = self._read(scene_id)
            _id(connection_id)
            geology = scene.get("geology", {})
            connection = next((c for c in geology.get("connections", []) if c["id"] == connection_id), None)
            if not connection:
                raise WorkspaceError("地质示意连接不存在", 404)
            geology["connections"].remove(connection)
            geology.setdefault("removed_connections", []).append(dict(connection, removed_at=_now()))
            return self._save(scene)

    def update_geology_settings(self, scene_id, patch):
        with self.lock:
            scene = self._read(scene_id)
            # Explicit display changes can initialize settings for legacy scenes;
            # only build/reading source objects freezes any geological geometry.
            geology = scene.setdefault("geology", empty_geology())
            settings = dict(geology["settings"], **patch.model_dump(exclude_unset=True))
            geology["settings"] = _validate(GeologySettings, settings).model_dump()
            return self._save(scene)

    def texture(self, scene_id, section_id, layer):
        with self.lock:
            scene = self._read(scene_id)
            section = self._section(scene, section_id)
            asset = section["source"]["assets"].get(layer)
            path = self._folder(scene_id) / f"{_id(section_id)}-{layer}.jpg"
            if not asset or not path.is_file():
                raise WorkspaceError("该断面未冻结此图层；原照片不会冒充素描成果", 404)
            if file_sha256(path) != asset["sha256"]:
                raise WorkspaceError("冻结纹理 SHA256 校验失败", 409)
            return path


def register_tunnel_routes(app, get_workspace):
    """Register before the frontend's catch-all mount; lazy workspace creation."""
    store = None
    store_lock = threading.Lock()

    def current():
        nonlocal store
        with store_lock:
            if store is None:
                store = TunnelSceneStore(get_workspace())
            return store

    @app.get("/api/tunnel-scenes")
    def list_scenes():
        return {"scenes": current().list()}

    @app.post("/api/tunnel-scenes", status_code=201)
    def create_scene(body: SceneSettings):
        return current().create(body)

    @app.get("/api/tunnel-scenes/{scene_id}")
    def get_scene(scene_id: str):
        return current().get(scene_id)

    @app.put("/api/tunnel-scenes/{scene_id}")
    def update_scene(scene_id: str, body: SceneUpdate):
        return current().update(scene_id, body)

    @app.post("/api/tunnel-scenes/{scene_id}/sections", status_code=201)
    def add_section(scene_id: str, body: SectionCreate):
        return current().add_section(scene_id, body)

    @app.put("/api/tunnel-scenes/{scene_id}/sections/{section_id}")
    def update_section(scene_id: str, section_id: str, body: SectionUpdate):
        return current().update_section(scene_id, section_id, body)

    @app.delete("/api/tunnel-scenes/{scene_id}/sections/{section_id}")
    def remove_section(scene_id: str, section_id: str):
        return current().remove_section(scene_id, section_id)

    @app.get("/api/tunnel-scenes/{scene_id}/sections/{section_id}/texture")
    def texture(scene_id: str, section_id: str, layer: Literal["sketch", "original"] = Query(default="sketch")):
        return FileResponse(current().texture(scene_id, section_id, layer), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/tunnel-scenes/{scene_id}/export")
    def export_scene(scene_id: str):
        scene = current().get(scene_id)
        return Response(json.dumps(scene, ensure_ascii=False, indent=2, allow_nan=False), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="tunnel_scene_{scene_id[:8]}.json"'})

    @app.post("/api/tunnel-scenes/{scene_id}/geology/build")
    def build_geology_route(scene_id: str, body: GeologyBuild):
        return current().build_geology(scene_id, body)

    @app.post("/api/tunnel-scenes/{scene_id}/geology/connections", status_code=201)
    def add_connection(scene_id: str, body: ConnectionCreate):
        return current().add_connection(scene_id, body)

    @app.put("/api/tunnel-scenes/{scene_id}/geology/connections/{connection_id}")
    def update_connection(scene_id: str, connection_id: str, body: ConnectionUpdate):
        return current().update_connection(scene_id, connection_id, body)

    @app.delete("/api/tunnel-scenes/{scene_id}/geology/connections/{connection_id}")
    def remove_connection(scene_id: str, connection_id: str):
        return current().remove_connection(scene_id, connection_id)

    @app.put("/api/tunnel-scenes/{scene_id}/geology/settings")
    def update_geology_settings(scene_id: str, body: GeologySettings):
        return current().update_geology_settings(scene_id, body)
