"""Verify the Python runtime and the running workbench schema.

Run without arguments after starting the service to distinguish a stale
uvicorn process or an old installed package from the current source tree.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def local_check() -> dict:
    import face_mapping
    from face_mapping.config import PipelineConfig

    return {
        "python": sys.executable,
        "face_mapping": str(Path(face_mapping.__file__).resolve()),
        "geometry_field": "geometry" in PipelineConfig.model_fields,
        "geometry_fields": list(PipelineConfig.model_fields["geometry"].annotation.model_fields)
        if "geometry" in PipelineConfig.model_fields
        and hasattr(PipelineConfig.model_fields["geometry"].annotation, "model_fields")
        else [],
    }


def fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Workbench base URL")
    parser.add_argument("--source-only", action="store_true", help="Only verify the local Python import")
    args = parser.parse_args()

    local = local_check()
    print(json.dumps({"local": local}, ensure_ascii=False, indent=2))
    if not local["geometry_field"]:
        return 2
    if args.source_only:
        return 0

    try:
        health_status, health = fetch_json(args.url.rstrip("/") + "/api/health")
        schema_status, schema = fetch_json(args.url.rstrip("/") + "/api/schema")
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(json.dumps({"server_error": str(exc)}, ensure_ascii=False, indent=2))
        return 3

    server_geometry = bool(schema.get("schema", {}).get("properties", {}).get("geometry"))
    report = {
        "server_status": health_status,
        "server_health": health,
        "schema_status": schema_status,
        "server_geometry_field": server_geometry,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if server_geometry else 4


if __name__ == "__main__":
    raise SystemExit(main())
