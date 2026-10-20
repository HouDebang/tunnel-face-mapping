"""Re-open a run and independently audit its persisted masks/data/hash ledger."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from face_mapping.schemas import PipelineResult


def verify_run(folder: Path) -> dict:
    folder = Path(folder)
    result = PipelineResult.model_validate_json((folder/"results.json").read_text(encoding="utf-8"))
    checks = {}
    if result.image is not None:
        size = tuple(result.image.original_size)
        checks["original_canvas"] = Image.open(folder/"original.png").size==size
        for name in ("face_mask.png","lithology_mask.png","fracture_mask.png","fracture_skeleton.png","lithology_overlay.png","fracture_overlay.png","geological_sketch.png"):
            if (folder/name).exists():
                checks[f"canvas:{name}"] = Image.open(folder/name).size==size
    if result.face is not None:
        face = np.asarray(Image.open(folder/"face_mask.png"))>0
        checks["face_area"] = int(face.sum())==result.face.pixel_area
        if result.lithology_regions is not None:
            labels = np.asarray(Image.open(folder/"lithology_mask.png"))
            coverage = np.zeros(face.shape,np.uint16)
            for region in result.lithology_regions:
                mask = np.asarray(Image.open(folder/region.mask_path))>0
                checks[f"region:{region.region_id}"] = int(mask.sum())==region.pixel_area and np.array_equal(mask,labels==region.label_value) and not np.any(mask & ~face)
                coverage += mask
            checks["disjoint_exact_roi_coverage"] = np.array_equal(coverage,face.astype(np.uint16))
            checks["area_ratios"] = math.isclose(sum(r.area_ratio for r in result.lithology_regions),1,abs_tol=1e-10)
    if result.fractures is not None:
        total = 0.
        used_edges = set()
        duplicate = False
        for trace in result.fractures:
            points = np.asarray(trace.points)
            length = float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum())
            checks[f"length:{trace.id}"] = math.isclose(length,trace.length_px,rel_tol=1e-12)
            for a,b in zip(trace.points,trace.points[1:]):
                edge = tuple(sorted((tuple(a),tuple(b))))
                if edge in used_edges:
                    duplicate = True
                used_edges.add(edge)
            total += length
        checks["no_repeated_retained_edge"] = not duplicate
        checks["trace_length_sum"] = math.isclose(total,result.statistics.total_length_px,rel_tol=1e-12)
    with (folder/"parameters.csv").open(encoding="utf-8-sig",newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary = {row["metric"]:row["value"] for row in rows if row["object_type"]=="summary"}
    if result.statistics is not None:
        for key,value in summary.items():
            expected = getattr(result.statistics,key)
            checks[f"csv:{key}"] = value=="" if expected is None else float(value)==expected
    manifest = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    checks["hash_manifest"] = all(hashlib.sha256((folder/item["path"]).read_bytes()).hexdigest()==item["sha256"] and (folder/item["path"]).stat().st_size==item["bytes"] for item in manifest["files"])
    report = {"run":str(folder.resolve()),"run_status":result.status,"passed":all(checks.values()),"checks":checks,"check_count":len(checks)}
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",type=Path)
    parser.add_argument("--details",action="store_true",help="Print all per-object checks")
    args=parser.parse_args()
    report=verify_run(args.folder)
    displayed = report if args.details else {k:v for k,v in report.items() if k!="checks"}
    if not args.details:
        displayed["failed_checks"] = [key for key,value in report["checks"].items() if not value]
    print(json.dumps(displayed,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__=="__main__":
    main()
