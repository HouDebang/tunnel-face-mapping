"""Large geometry belongs to immutable outcomes, not mutable task indices."""
import json
import time
import os
import subprocess
import sys
from face_mapping.workspace import Workspace
from tools.generate_synthetic import generate_synthetic


def test_geometry_survives_lightweight_index_and_reopen(tmp_path):
    source=generate_synthetic(tmp_path/"face.png",160,120)
    root=tmp_path/"managed"
    workspace=Workspace(root)
    task=workspace.create_task(source.read_bytes(),source.name,"image/png")
    job=workspace.submit(task["id"],"trace_extraction")
    deadline=time.monotonic()+30
    while workspace.get_task(task["id"])["busy"] and time.monotonic()<deadline:
        time.sleep(.02)
    assert workspace.job(job["job_id"])["status"]=="completed"
    current=workspace.get_task(task["id"])
    traces=current["stages"]["trace_extraction"]
    assert traces["data"]["fractures"]
    stored=json.loads((root/task["id"]/"task.json").read_text(encoding="utf8"))
    for record in stored["history"]:
        assert record["data"]=={}
        assert record["data_in_outcome"] is True
    workspace.close()
    reopened=Workspace(root)
    restored=reopened.history(task["id"],traces["execution_id"])
    assert restored["data"]["fractures"]==traces["data"]["fractures"]
    assert reopened.get_task(task["id"])["stages"]["trace_extraction"]["status"] in {"completed","warning"}
    reopened.close()


def test_asgi_import_cannot_interrupt_a_live_workspace(tmp_path):
    source=generate_synthetic(tmp_path/"face.png",128,96)
    root=tmp_path/"managed"
    workspace=Workspace(root)
    task=workspace.create_task(source.read_bytes(),source.name,"image/png")
    workspace.close()
    path=root/task["id"]/"task.json"
    document=json.loads(path.read_text(encoding="utf8"))
    document["busy"]=True
    document["active_job"]="running-job"
    document["jobs"]["running-job"]={"status":"running","job_id":"running-job"}
    path.write_text(json.dumps(document),encoding="utf8")
    before=path.read_bytes()
    env=dict(os.environ,FACE_MAPPING_WORKSPACE=str(root))
    result=subprocess.run([sys.executable,"-c","import face_mapping.api; assert face_mapping.api.app.state.workspace is None"],env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert path.read_bytes()==before
