"""Stage DAG shared by scheduling, cache keys and the browser parameter schema."""
STAGES = [
    {"id":"preprocessing","title":"图像预处理","section":"preprocessing","depends_on":[],"optional_dependencies":[]},
    {"id":"face_detection","title":"掌子面分割","section":"face","depends_on":["preprocessing"],"optional_dependencies":[]},
    {"id":"lithology_segmentation","title":"岩性区域分割","section":"lithology","depends_on":["preprocessing","face_detection"],"optional_dependencies":[]},
    {"id":"fracture_segmentation","title":"裂缝分割","section":"fracture","depends_on":["preprocessing","face_detection"],"optional_dependencies":[]},
    {"id":"trace_extraction","title":"迹线识别","section":"vectorization","depends_on":["preprocessing","fracture_segmentation"],"optional_dependencies":[]},
    {"id":"geometry_calculation","title":"几何参数计算","section":"calibration","depends_on":["preprocessing","face_detection"],"optional_dependencies":["lithology_segmentation","trace_extraction"]},
    {"id":"sketch_rendering","title":"综合地质素描","section":None,"depends_on":["preprocessing"],"optional_dependencies":["face_detection","lithology_segmentation","trace_extraction","geometry_calculation"]},
    {"id":"result_export","title":"成果导出","section":None,"depends_on":["preprocessing"],"optional_dependencies":["face_detection","lithology_segmentation","fracture_segmentation","trace_extraction","geometry_calculation","sketch_rendering"]},
]
STAGE_IDS = tuple(stage["id"] for stage in STAGES)
SPEC = {stage["id"]: stage for stage in STAGES}
IMPLEMENTATION_FILES = {
    "preprocessing":["preprocessing.py","coordinates.py"],
    "face_detection":["face_detection.py","coordinates.py","geometry.py"],
    "lithology_segmentation":["lithology.py","coordinates.py","geometry.py","visualization.py","backends.py"],
    "fracture_segmentation":["fracture_detection.py","coordinates.py","visualization.py","backends.py"],
    "trace_extraction":["vectorization.py","geometry.py","visualization.py"],
    "geometry_calculation":["geometry.py"],
    "sketch_rendering":["visualization.py"],
    "result_export":["export.py"],
}


def affected_stages(stage_id):
    affected = {stage_id}
    for stage in STAGES:
        if affected.intersection(stage["depends_on"]+stage["optional_dependencies"]):
            affected.add(stage["id"])
    return [identifier for identifier in STAGE_IDS if identifier in affected]
