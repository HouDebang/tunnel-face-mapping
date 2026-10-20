"""Local FastAPI workbench, backed by the same stage executor as the CLI."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import PipelineConfig
from .parameter_ui import ui_schema
from .workspace import MAX_UPLOAD_BYTES, Workspace, WorkspaceError


class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: PipelineConfig


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False
    downstream: bool = False


def create_app(workspace_root: Path | None = None) -> FastAPI:
    project = Path(__file__).resolve().parents[2]
    root = workspace_root or Path(os.environ.get("FACE_MAPPING_WORKSPACE",project/"outputs"/"workbench"))
    # Importing the ASGI module (e.g. for tests/CLI discovery) must not touch
    # a running server's default workspace or mark its jobs as interrupted.
    workspace = Workspace(root) if workspace_root is not None else None

    def get_workspace():
        nonlocal workspace
        if workspace is None:
            workspace = Workspace(root)
            app.state.workspace = workspace
        return workspace

    @asynccontextmanager
    async def lifespan(app):
        get_workspace()
        yield
        await run_in_threadpool(get_workspace().close)

    app = FastAPI(title="掌子面地质识别工作台",version="0.2.0",lifespan=lifespan)
    app.state.workspace = workspace
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost","testserver","[::1]"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        # Same-origin UI needs no CORS. Reject cross-site mutation of a local tool.
        origin = request.headers.get("origin")
        if origin and request.method in {"POST","PUT","PATCH","DELETE"}:
            from urllib.parse import urlsplit
            parsed=urlsplit(origin)
            if parsed.hostname not in {"127.0.0.1","localhost","::1","testserver"} or parsed.netloc!=request.headers.get("host"):
                return JSONResponse(status_code=403,content={"error":{"code":"origin_rejected","message":"只接受工作台同源请求。"}})
        response=await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path in {"/","/index.html","/style.css","/app.js"}:
            response.headers["Cache-Control"]="no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        return response

    @app.exception_handler(WorkspaceError)
    async def workspace_error(request,exc):
        return JSONResponse(status_code=exc.status_code,content={"error":{"code":"workspace_error","message":str(exc)}})

    @app.exception_handler(RequestValidationError)
    async def request_error(request,exc):
        # Pydantic error context may contain ValueError objects; retain readable fields.
        details=[{"loc":list(item["loc"]),"msg":item["msg"],"type":item["type"]} for item in exc.errors()]
        return JSONResponse(status_code=422,content={"error":{"code":"validation_error","message":"参数校验失败，请检查取值范围及平面标定条件。","details":details}})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc):
        return JSONResponse(status_code=exc.status_code,content={"error":{"code":"http_error","message":str(exc.detail)}})

    @app.exception_handler(Exception)
    async def internal_error(request,exc):
        import logging
        logging.getLogger("face_mapping.api").exception("API %s failed",request.url.path)
        return JSONResponse(status_code=500,content={"error":{"code":"internal_error","message":f"服务处理异常：{type(exc).__name__}: {exc}"}})

    @app.get("/api/health")
    def health():
        return {"status":"ok","version":"0.2.0","execution_model":"single_process_single_worker","upload_limit_bytes":MAX_UPLOAD_BYTES}

    @app.get("/api/schema")
    def schema():
        return ui_schema()

    @app.post("/api/tasks",status_code=201)
    async def upload(file: UploadFile = File(...)):
        data=bytearray()
        try:
            while block:=await file.read(1024*1024):
                data.extend(block)
                if len(data)>MAX_UPLOAD_BYTES:
                    raise WorkspaceError("照片超过 32 MiB 限制",413)
            return await run_in_threadpool(get_workspace().create_task,bytes(data),file.filename or "upload",file.content_type)
        finally:
            await file.close()

    @app.get("/api/tasks")
    def list_tasks():
        return {"tasks":get_workspace().list_tasks()}

    @app.get("/api/tasks/{task_id}")
    def task(task_id:str):
        return get_workspace().get_task(task_id)

    @app.get("/api/tasks/{task_id}/stages")
    def stages(task_id:str):
        return {"stages":list(get_workspace().get_task(task_id)["stages"].values())}

    @app.get("/api/tasks/{task_id}/config")
    def config(task_id:str):
        task=get_workspace().get_task(task_id)
        return {"config":task["config"],"revision":task["config_revision"]}

    @app.put("/api/tasks/{task_id}/config")
    def update_config(task_id:str,body:ConfigUpdate):
        return get_workspace().update_config(task_id,body.config.model_dump())

    @app.post("/api/tasks/{task_id}/stages/{stage_id}/run",status_code=202)
    def run_stage(task_id:str,stage_id:str,body:RunRequest=RunRequest()):
        return get_workspace().submit(task_id,stage_id,body.force,body.downstream)

    @app.post("/api/tasks/{task_id}/run-all",status_code=202)
    def run_all(task_id:str,body:RunRequest=RunRequest()):
        return get_workspace().submit(task_id,None,body.force)

    @app.get("/api/jobs/{job_id}")
    def job(job_id:str):
        return get_workspace().job(job_id)

    @app.get("/api/tasks/{task_id}/results")
    def results(task_id:str):
        return get_workspace().results(task_id)

    @app.get("/api/tasks/{task_id}/history/{execution_id}")
    def history(task_id:str,execution_id:str):
        return get_workspace().history(task_id,execution_id)

    @app.get("/api/tasks/{task_id}/artifacts/{artifact_id}")
    def artifact(task_id:str,artifact_id:str,history:str|None=None,download:bool=False):
        path,name=get_workspace().artifact_path(task_id,artifact_id,history)
        return FileResponse(path,filename=Path(name).name if download else None,content_disposition_type="attachment" if download else "inline",headers={"Cache-Control":"no-store"})

    @app.get("/api/tasks/{task_id}/logs")
    def logs(task_id:str):
        return get_workspace().logs(task_id)

    @app.get("/api/tasks/{task_id}/download")
    def download(task_id:str):
        path=get_workspace().download_path(task_id)
        return FileResponse(path,media_type="application/zip",filename=f"face_mapping_{task_id[:8]}.zip",headers={"Cache-Control":"no-store"})

    frontend = project/"frontend"
    if frontend.is_dir():
        app.mount("/",StaticFiles(directory=frontend,html=True),name="frontend")
    return app


app = create_app()
