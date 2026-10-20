"""File-backed immutable executions, content-addressed caches and local jobs.

This service deliberately supports one process and one algorithm worker. All
state transitions use a lock and atomic JSON replacement; output directories
are unique per execution. Successful history is never overwritten on failure.
"""
from __future__ import annotations

import hashlib
import io
import importlib.metadata
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from .config import PipelineConfig
from .export import file_sha256
from .stage_graph import IMPLEMENTATION_FILES, SPEC, STAGE_IDS, STAGES, affected_stages

VALID = {"completed","warning"}
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


class WorkspaceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    os.replace(temp,path)


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix="face-stage")
        self._futures = {}
        self._closed = False
        self.runtime_versions = {name:importlib.metadata.version(name) for name in ("numpy","opencv-python-headless","scikit-image","Pillow","pydantic")}
        # On a clean process restart, in-flight metadata cannot masquerade as live.
        for path in self.root.glob("*/task.json"):
            task = _read(path)
            if task.get("busy"):
                task["busy"] = False
                task["active_job"] = None
                for state in task["stages"].values():
                    if state["status"] == "running":
                        state.update(status="failed",error="服务重启中断了阶段；可重新执行，历史成功成果保留。")
                for job in task.get("jobs",{}).values():
                    if job["status"] in {"queued","running"}:
                        job.update(status="failed",error="服务重启，作业中断。",finished_at=_now())
                _atomic_json(path,task)

    def close(self):
        if not self._closed:
            self.pool.shutdown(wait=True,cancel_futures=False)
            self._closed = True

    def _task_dir(self, task_id):
        if not re.fullmatch(r"[0-9a-f]{32}",task_id):
            raise WorkspaceError("任务不存在",404)
        folder = (self.root/task_id).resolve()
        if folder.parent != self.root or not (folder/"task.json").is_file():
            raise WorkspaceError("任务不存在",404)
        return folder

    def _load(self, task_id):
        return _read(self._task_dir(task_id)/"task.json")

    def _save(self, task):
        # Coordinates live once in immutable outcomes, not repeatedly in the
        # mutable task index. This keeps large-photo status updates lightweight.
        for record in task.get("history",[]):
            outcome = self.root/task["id"]/record["directory"]/"outcome.json"
            if record.get("data") and outcome.is_file():
                record["data"] = {}
                record["data_in_outcome"] = True
        _atomic_json(self.root/task["id"]/"task.json",task)

    def _record_data(self, task, record):
        if record.get("data_in_outcome"):
            return _read(self.root/task["id"]/record["directory"]/"outcome.json").get("data",{})
        return record.get("data",{})

    def create_task(self, content: bytes, filename: str, content_type: str | None = None):
        if not content or len(content)>MAX_UPLOAD_BYTES:
            raise WorkspaceError("照片为空或超过 32 MiB 限制",413)
        filename = filename.replace("\\","/").split("/")[-1]
        extension = Path(filename).suffix.lower()
        if extension not in {".jpg",".jpeg",".png"}:
            raise WorkspaceError("只接受 JPG、JPEG、PNG 照片",415)
        if content_type not in {None,"","application/octet-stream","image/jpeg","image/jpg","image/png"}:
            raise WorkspaceError("上传 MIME 类型不是支持的照片类型",415)
        try:
            with Image.open(io.BytesIO(content)) as source:
                actual = source.format
                # Phone JPEGs may contain an MPF extension (Pillow calls it MPO).
                # It is a JPEG container; use its primary frame as the single photo.
                if actual not in {"JPEG","MPO","PNG"} or (extension==".png") != (actual=="PNG"):
                    raise WorkspaceError("文件后缀与实际图像格式不一致",415)
                if source.width*source.height>MAX_IMAGE_PIXELS or min(source.size)<16:
                    raise WorkspaceError("照片尺寸应至少 16×16，且不超过 4000 万像素",422)
                if content_type in {"image/jpeg","image/jpg","image/png"} and (content_type=="image/png") != (actual=="PNG"):
                    raise WorkspaceError("MIME 类型与图像内容不一致",415)
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGB")
                sample = np.asarray(oriented.resize((128,128))).astype(float)
                if sample.reshape(-1,3).std(axis=0).max()<.5 or sample.max()<3 or sample.min()>252:
                    raise WorkspaceError("照片接近全黑、全白或空间常量，不能分析",422)
                width,height = oriented.size
                task_id = uuid.uuid4().hex
                folder = self.root/task_id
                folder.mkdir()
                source_name = "source"+extension
                (folder/source_name).write_bytes(content)
                oriented.save(folder/"upload_original.png")
        except WorkspaceError:
            raise
        except (OSError,ValueError,Image.DecompressionBombError) as exc:
            raise WorkspaceError(f"照片无法完整解码：{exc}",422) from exc
        task = dict(id=task_id,filename=filename,sha256=hashlib.sha256(content).hexdigest(),width=width,height=height,
            source_name=source_name,source_format=actual,created_at=_now(),updated_at=_now(),config=PipelineConfig().model_dump(),config_revision=1,
            busy=False,active_job=None,stages={sid:dict(id=sid,status="pending",execution_id=None,error=None,last_attempt_id=None) for sid in STAGE_IDS},history=[],jobs={})
        with self.lock:
            self._save(task)
            return self._public(task)

    def _implementation_hash(self, stage_id):
        base = Path(__file__).parent
        names = IMPLEMENTATION_FILES[stage_id]+["stage_executor.py","schemas.py"]
        return _digest({"files":{name:file_sha256(base/name) if (base/name).is_file() else "absent" for name in names},"runtime":self.runtime_versions,"executor_contract":"stages_v2"})

    def _execution(self, task, execution_id):
        return next((entry for entry in task["history"] if entry["execution_id"]==execution_id),None)

    def _config_part(self, config, stage_id):
        if stage_id=="result_export":
            return config
        section = SPEC[stage_id]["section"]
        if stage_id=="sketch_rendering":
            section = "calibration"
        return config[section] if section else {}

    def _cache_key(self, task, stage_id, valid_records, states):
        spec = SPEC[stage_id]
        dependencies = {}
        for dependency in spec["depends_on"]+spec["optional_dependencies"]:
            record = valid_records.get(dependency)
            dependencies[dependency] = ({"cache_key":record["cache_key"],"artifacts":{a["name"]:a["sha256"] for a in record["artifacts"]}} if record else {"unavailable":states[dependency]["status"]})
        source = self.root/task["id"]/task["source_name"]
        return _digest({"stage":stage_id,"input_sha256":file_sha256(source),"parameters":self._config_part(task["config"],stage_id),
                        "implementation":self._implementation_hash(stage_id),"dependencies":dependencies,"coordinates":"original_image_pixel_centers_v1"})

    def _fresh(self, task):
        """Rehash artifacts before reuse; directory existence alone proves nothing."""
        valid_records, states = {}, {}
        for sid in STAGE_IDS:
            state = dict(task["stages"][sid])
            record = self._execution(task,state.get("execution_id"))
            if state["status"]=="running":
                states[sid] = state
                continue
            hard_ready = all(dep in valid_records for dep in SPEC[sid]["depends_on"])
            current_key = self._cache_key(task,sid,valid_records,states)
            intact = False
            if record:
                try:
                    intact = record["cache_key"]==current_key and all(file_sha256(self.root/task["id"]/a["relative_path"])==a["sha256"] for a in record["artifacts"])
                except (OSError,ValueError):
                    intact = False
            if record and intact and hard_ready and not (state["status"]=="failed" and state.get("last_attempt_key")==current_key):
                state["status"] = "warning" if record["warnings"] else "completed"
                state["error"] = None
                valid_records[sid] = record
            elif state["status"]=="failed" and state.get("last_attempt_key")==current_key:
                pass
            elif record:
                state["status"] = "stale"
            elif not hard_ready:
                state["status"] = "blocked"
            else:
                state["status"] = "pending"
            state["cache_key"] = current_key
            states[sid] = state
        return states, valid_records

    def _artifact_public(self, task, artifact, historical=False):
        result = {key:artifact[key] for key in ("id","name","stage_id","execution_id","media_type","sha256","bytes")}
        suffix = f"?history={artifact['execution_id']}" if historical else ""
        result["url"] = f"/api/tasks/{task['id']}/artifacts/{artifact['id']}"+suffix
        return result

    def _record_public(self,task,record,historical=False):
        item = {key:value for key,value in record.items() if key not in {"directory"}}
        item["data"] = self._record_data(task,record)
        item["artifacts"] = [self._artifact_public(task,a,historical) for a in record["artifacts"]]
        return item

    def _public(self, task):
        states, valid = self._fresh(task)
        public = {key:task[key] for key in ("id","filename","sha256","width","height","created_at","updated_at","config","config_revision","busy","active_job")}
        public["original_url"] = f"/api/tasks/{task['id']}/artifacts/original"
        public["stages"] = {}
        for sid,state in states.items():
            record = valid.get(sid)
            item = dict(state)
            item.update(elapsed_seconds=record.get("elapsed_seconds",0) if record else 0,warnings=record.get("warnings",[]) if record else [],
                artifacts=[self._artifact_public(task,a) for a in record["artifacts"]] if record else [],data=self._record_data(task,record) if record else {},method=record.get("method") if record else None)
            public["stages"][sid] = item
        # Large raw coordinate arrays are fetched through /history/{id} on demand.
        public["history"] = [{key:entry.get(key) for key in ("execution_id","stage_id","status","created_at","finished_at","elapsed_seconds","config_revision","error","cache_key")} for entry in reversed(task["history"])]
        return public

    def get_task(self, task_id):
        with self.lock:
            return self._public(self._load(task_id))

    def list_tasks(self):
        with self.lock:
            tasks = [_read(path) for path in self.root.glob("*/task.json")]
            return sorted([{key:task[key] for key in ("id","filename","width","height","created_at","updated_at","busy","active_job")} for task in tasks],key=lambda t:t["created_at"],reverse=True)

    def update_config(self, task_id, data):
        config = PipelineConfig.model_validate(data).model_dump()
        with self.lock:
            task = self._load(task_id)
            if task["busy"]:
                raise WorkspaceError("任务正在运行，请完成后再修改参数。",409)
            if config!=task["config"]:
                task["config"] = config
                task["config_revision"] += 1
                task["updated_at"] = _now()
                states,_ = self._fresh(task)
                task["stages"] = states
                self._save(task)
            return self._public(task)

    def history(self, task_id, execution_id):
        with self.lock:
            task = self._load(task_id)
            record = self._execution(task,execution_id)
            if not record:
                raise WorkspaceError("阶段运行记录不存在",404)
            return self._record_public(task,record,True)

    def artifact_path(self, task_id, artifact_id, history=None):
        with self.lock:
            task = self._load(task_id)
            folder = self._task_dir(task_id)
            if artifact_id in {"original","source"}:
                return (folder/("upload_original.png" if artifact_id=="original" else task["source_name"])),task["filename"] if artifact_id=="source" else "original.png"
            _, valid = self._fresh(task)
            records = [self._execution(task,history)] if history else valid.values()
            for record in records:
                if not record:
                    continue
                for artifact in record["artifacts"]:
                    if artifact["id"]==artifact_id:
                        path = (folder/artifact["relative_path"]).resolve()
                        if not path.is_relative_to(folder) or not path.is_file():
                            raise WorkspaceError("成果文件不存在",404)
                        return path,artifact["name"]
            raise WorkspaceError("成果不存在、已过期，或不属于当前有效运行。历史成果需显式指定 history。",404)

    def _dependencies(self, task):
        states,valid = self._fresh(task)
        return states,{sid:self.root/task["id"]/record["directory"] for sid,record in valid.items()},valid

    def results(self, task_id):
        from .stage_executor import assemble_result
        with self.lock:
            task = self._load(task_id)
            states,deps,_ = self._dependencies(task)
            result = assemble_result(self.root/task["id"]/task["source_name"],PipelineConfig.model_validate(task["config"]),deps)
            return {"result":result.model_dump(mode="json"),"stale_stages":[sid for sid,s in states.items() if s["status"]=="stale"],
                    "missing_stages":[sid for sid,s in states.items() if s["status"] not in VALID]}

    def submit(self, task_id, stage_id=None, force=False, downstream=False):
        if stage_id is not None and stage_id not in SPEC:
            raise WorkspaceError("未知处理阶段",404)
        with self.lock:
            task = self._load(task_id)
            if task["busy"]:
                raise WorkspaceError("此照片已有排队或运行中的作业，请勿重复提交。",409)
            if self._closed:
                raise WorkspaceError("执行器已关闭",503)
            job_id = uuid.uuid4().hex
            targets = list(STAGE_IDS) if stage_id is None else (affected_stages(stage_id) if downstream else [stage_id])
            job = dict(job_id=job_id,task_id=task_id,status="queued",created_at=_now(),targets=targets,error=None,events=[],config_revision=task["config_revision"])
            task["jobs"][job_id] = job
            task["active_job"] = job_id
            task["busy"] = True
            self._save(task)
            self._futures[job_id] = self.pool.submit(self._run_job,task_id,job_id,targets,force,stage_id)
            return dict(job)

    def job(self, job_id):
        with self.lock:
            for path in self.root.glob("*/task.json"):
                task = _read(path)
                if job_id in task.get("jobs",{}):
                    return task["jobs"][job_id]
        raise WorkspaceError("作业不存在",404)

    def _catalog(self, task, execution_id, sid, directory):
        items=[]
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            name = path.relative_to(directory).as_posix()
            items.append(dict(id=execution_id+"_"+hashlib.sha256(name.encode()).hexdigest()[:16],name=name,stage_id=sid,execution_id=execution_id,
                relative_path=path.relative_to(self.root/task["id"]).as_posix(),sha256=file_sha256(path),bytes=path.stat().st_size,
                media_type=mimetypes.guess_type(name)[0] or "application/octet-stream"))
        return items

    def _run_job(self, task_id, job_id, targets, force, selected):
        from .stage_executor import execute_stage
        attempted = {}
        try:
            with self.lock:
                task=self._load(task_id)
                task["jobs"][job_id]["status"]="running"
                self._save(task)

            def run(sid):
                if sid in attempted:
                    return attempted[sid]
                for dep in SPEC[sid]["depends_on"]+SPEC[sid]["optional_dependencies"]:
                    run(dep)
                with self.lock:
                    task=self._load(task_id)
                    states,deps,valid=self._dependencies(task)
                    own_force = force and (selected is None or sid==selected)
                    if sid in valid and not own_force:
                        task["jobs"][job_id]["events"].append({"stage_id":sid,"status":"cache_hit","execution_id":valid[sid]["execution_id"],"at":_now()})
                        self._save(task)
                        attempted[sid]=True
                        return True
                    if any(dep not in valid for dep in SPEC[sid]["depends_on"]):
                        task["stages"][sid].update(status="blocked",error="必需前置阶段未成功；未使用历史过期结果。")
                        task["jobs"][job_id]["events"].append({"stage_id":sid,"status":"blocked","at":_now()})
                        self._save(task)
                        attempted[sid]=False
                        return False
                    key=self._cache_key(task,sid,valid,states)
                    execution_id=uuid.uuid4().hex
                    directory=self.root/task_id/"executions"/execution_id
                    directory.mkdir(parents=True)
                    config=PipelineConfig.model_validate(task["config"])
                    record=dict(execution_id=execution_id,stage_id=sid,status="running",created_at=_now(),finished_at=None,
                        elapsed_seconds=0,config_revision=task["config_revision"],config=config.model_dump(),cache_key=key,
                        directory=directory.relative_to(self.root/task_id).as_posix(),artifacts=[],data={},warnings=[],checks={},method=None,error=None)
                    task["history"].append(record)
                    task["stages"][sid].update(status="running",last_attempt_id=execution_id,last_attempt_key=key,error=None)
                    task["jobs"][job_id]["events"].append({"stage_id":sid,"status":"running","execution_id":execution_id,"at":_now()})
                    self._save(task)
                started=time.perf_counter()
                try:
                    outcome=execute_stage(sid,self.root/task_id/task["source_name"],directory,config,deps)
                    _atomic_json(directory/"outcome.json",outcome)
                    if sid=="result_export":
                        with zipfile.ZipFile(directory/"geological_results.zip","w",compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
                            for path in sorted(directory.rglob("*")):
                                if path.is_file() and path.name not in {"geological_results.zip","outcome.json"}:
                                    archive.write(path,path.relative_to(directory).as_posix())
                    (directory/"stage.log").write_text(f"{sid} completed\nmethod={outcome.get('method')}\n"+"\n".join(outcome.get("warnings",[])),encoding="utf-8")
                    with self.lock:
                        task=self._load(task_id)
                        record=self._execution(task,execution_id)
                        record.update(status="warning" if outcome.get("warnings") else "completed",finished_at=_now(),elapsed_seconds=time.perf_counter()-started,
                            data=outcome.get("data",{}),warnings=outcome.get("warnings",[]),checks=outcome.get("checks",{}),method=outcome.get("method"),artifacts=self._catalog(task,execution_id,sid,directory))
                        task["stages"][sid].update(status=record["status"],execution_id=execution_id,error=None)
                        task["jobs"][job_id]["events"].append({"stage_id":sid,"status":record["status"],"execution_id":execution_id,"at":_now()})
                        self._save(task)
                    attempted[sid]=True
                except Exception as exc:
                    error=f"{sid}: {type(exc).__name__}: {exc}"
                    (directory/"stage.log").write_text(traceback.format_exc(),encoding="utf-8")
                    with self.lock:
                        task=self._load(task_id)
                        record=self._execution(task,execution_id)
                        record.update(status="failed",finished_at=_now(),elapsed_seconds=time.perf_counter()-started,error=error,artifacts=self._catalog(task,execution_id,sid,directory))
                        task["stages"][sid].update(status="failed",error=error,last_attempt_key=key)
                        task["jobs"][job_id]["events"].append({"stage_id":sid,"status":"failed","error":error,"execution_id":execution_id,"at":_now()})
                        self._save(task)
                    attempted[sid]=False
                return attempted[sid]

            for target in targets:
                run(target)
            with self.lock:
                task=self._load(task_id)
                errors=[sid for sid in targets if not attempted.get(sid)]
                task["jobs"][job_id].update(status="failed" if errors else "completed",error="失败或阻塞阶段："+", ".join(errors) if errors else None,finished_at=_now())
                self._save(task)
        except Exception as exc:
            with self.lock:
                task=self._load(task_id)
                task["jobs"][job_id].update(status="failed",error=f"执行器异常：{exc}",finished_at=_now())
                self._save(task)
        finally:
            with self.lock:
                task=self._load(task_id)
                task.update(busy=False,active_job=None,updated_at=_now())
                states,_=self._fresh(task)
                task["stages"]=states
                self._save(task)

    def download_path(self, task_id):
        with self.lock:
            task=self._load(task_id)
            if task["busy"]:
                raise WorkspaceError("作业仍在运行，完成后再导出。",409)
            _,valid=self._fresh(task)
            record=valid.get("result_export")
            if not record:
                raise WorkspaceError("当前配置没有有效成果包；请运行成果导出，过期历史结果不会混入。",409)
            return self.root/task_id/record["directory"]/"geological_results.zip"

    def logs(self,task_id):
        with self.lock:
            task=self._load(task_id)
            logs=[]
            for entry in reversed(task["history"]):
                # Logs carry provenance/parameters; full geometry is available
                # at the explicit execution-history endpoint.
                item={key:value for key,value in entry.items() if key not in {"directory","data"}}
                item["artifacts"]=[self._artifact_public(task,a,True) for a in entry["artifacts"]]
                logs.append(item)
            text="\n".join(f"[{entry['created_at']}] {entry['stage_id']} {entry['status']} {entry.get('error') or ''}" for entry in task["history"])
            return {"logs":logs,"text":text}
