"""Source-traceable local knowledge bases for lithology judgement.

Two stores are built from project material and kept as inspectable JSON:

* ``site_records``  - the 28 fields of the project's geological sketch record
  sheet, each with the photogrammetric obtainability class from the T01 task
  package and the option values found in the sample workbook.
* ``lithology_catalog`` - the project's lithology list (rock groups, parent
  names, rock names) plus a *visual expectation profile* per rock that the
  rule-based classifier scores against. Profiles are general petrographic
  expectations, not measurements taken on this site; every record carries
  ``profile_basis`` so the boundary stays visible.

Nothing here decides a rock name. The store only supplies expectations,
sources and retrieval, and every query result keeps its provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from .graph_store import Neo4jGraphStore

SCHEMA_VERSION = "2.3"

FIELD_DEFINITIONS = [
    ("工程名称", "管理信息"), ("工程部位", "管理信息"), ("试验条件", "管理信息"),
    ("委托编号", "管理信息"), ("检测日期", "管理信息"), ("检测依据", "管理信息"),
    ("判定依据", "管理信息"), ("主要仪器设备名称", "管理信息"), ("设备编号", "管理信息"),
    ("岩土类型（名称）", "影像辅助"), ("岩石硬度", "需外部测试或鉴定"), ("掌子面状态", "影像辅助"),
    ("地质构造影响程度", "影像辅助"), ("间距（m）", "条件可量测"), ("延伸性", "部分可量测"),
    ("粗糙度", "部分可量测"), ("张开性（mm）", "部分可量测"), ("风化程度", "影像辅助"),
    ("完整状态", "影像辅助"), ("岩层产状", "条件可量测"), ("围岩级别", "需综合评价"),
    ("岩体含水状态/渗水量", "需现场测量"), ("开挖工作面素描图", "组合成果生成"),
    ("地质简要描述", "辅助生成"), ("附加声明", "管理信息"), ("记录人", "管理信息"),
    ("监理员", "管理信息"), ("签署日期", "管理信息"),
]

# Short obtainability notes taken from T01_28字段摄影测量可获取性分类_最小任务包.txt.
FIELD_NOTES = {
    "工程名称": "项目资料带入，影像不提供。",
    "工程部位": "洞别、里程和台阶由任务记录带入；定位成果可辅助核验。",
    "试验条件": "施工及检测流程记录确认。",
    "委托编号": "委托资料带入。",
    "检测日期": "检测任务记录；照片时间仅供核验。",
    "检测依据": "项目采用的标准。",
    "判定依据": "项目采用的标准。",
    "主要仪器设备名称": "设备台账和采集记录。",
    "设备编号": "设备台账。",
    "岩土类型（名称）": "颜色、纹理和颗粒外观；岩性名称需地质资料或岩样鉴定。",
    "岩石硬度": "现场鉴定或强度试验，不能从几何模型直接测定。",
    "掌子面状态": "同一岩面多期影像可提供变形和掉块证据；自稳状态需工程判断。",
    "地质构造影响程度": "结构面和破碎带外观证据；影响程度需地质判读。",
    "间距（m）": "同组面确认且几何充分时测法向间距；只有迹线时仅输出二维表观间距。",
    "延伸性": "可见迹线长度、连续段和截断状态；不等于完整延续范围或原表等级。",
    "粗糙度": "真实结构面露面的起伏；需采样尺度、方向和噪声检核，不能直接给 JRC。",
    "张开性（mm）": "两壁边界可见且分辨率足够处的局部表观开口；充填性质另行确认。",
    "风化程度": "变色和表面破坏证据；正式等级需现场确认。",
    "完整状态": "裂隙分布和表面块度；不能以表面裂隙密度替代 RQD 或完整性系数。",
    "岩层产状": "确认真层理、有足够层面几何并完成定向后量测；节理产状不得替代岩层产状。",
    "围岩级别": "结合强度、完整性、地下水、主要软弱结构面及应力资料判定。",
    "岩体含水状态/渗水量": "干湿和滴流外观可辅助；L/min·10m 流量需实测。",
    "开挖工作面素描图": "摄影测量提供正射底图和几何定位；地质界线、迹线和符号需识别及复核。",
    "地质简要描述": "根据已核实的量测、地质判读及现场资料组织文字。",
    "附加声明": "模板及实际检测情况。",
    "记录人": "人员账号或签字流程。",
    "监理员": "监理确认或签字流程。",
    "签署日期": "签署流程记录。",
}

# The record sheet is represented as a small, explicit ontology.  The 28
# rows remain the public ``records`` API, while this model makes the field
# types and their evidence/derivation relationships queryable as a graph.
FIELD_ONTOLOGY_CLASSES = [
    {"id": "class:GeologicalSketchRecord", "label": "掌子面地质素描记录", "parent": "class:Entity"},
    {"id": "class:Field", "label": "记录字段", "parent": "class:Entity"},
    {"id": "class:AdministrativeField", "label": "管理信息字段", "parent": "class:Field"},
    {"id": "class:ImageObservationField", "label": "影像辅助字段", "parent": "class:Field"},
    {"id": "class:MeasurementField", "label": "量测字段", "parent": "class:Field"},
    {"id": "class:ExternalEvidenceField", "label": "外部测试/现场证据字段", "parent": "class:Field"},
    {"id": "class:DerivedResultField", "label": "成果生成字段", "parent": "class:Field"},
    {"id": "class:Entity", "label": "工程地质实体", "parent": None},
]
FIELD_ONTOLOGY_PROPERTIES = [
    {"id": "property:has_field", "label": "包含字段", "domain": "class:GeologicalSketchRecord", "range": "class:Field"},
    {"id": "property:instance_of", "label": "实例属于", "domain": "class:Entity", "range": "class:Entity"},
    {"id": "property:requires_evidence", "label": "需要证据", "domain": "class:Field", "range": "class:Field"},
    {"id": "property:supports", "label": "支持判读", "domain": "class:Field", "range": "class:Field"},
    {"id": "property:derived_from", "label": "派生自", "domain": "class:Field", "range": "class:Field"},
    {"id": "property:complements", "label": "互补", "domain": "class:Field", "range": "class:Field"},
]

# Directed field dependencies.  These are schema relationships, not claims
# that the image alone can provide every target field.
FIELD_RELATION_MAP = {
    "工程部位": [("supports", "开挖工作面素描图"), ("supports", "地质简要描述")],
    "岩土类型（名称）": [("supports", "岩石硬度"), ("supports", "风化程度"), ("supports", "围岩级别"), ("supports", "地质简要描述")],
    "掌子面状态": [("supports", "完整状态"), ("supports", "围岩级别"), ("complements", "地质构造影响程度")],
    "地质构造影响程度": [("requires_evidence", "岩层产状"), ("requires_evidence", "间距（m）"), ("requires_evidence", "延伸性"), ("requires_evidence", "粗糙度"), ("requires_evidence", "张开性（mm）"), ("supports", "围岩级别")],
    "岩层产状": [("supports", "地质构造影响程度"), ("supports", "开挖工作面素描图")],
    "间距（m）": [("supports", "完整状态"), ("supports", "围岩级别")],
    "延伸性": [("supports", "完整状态"), ("supports", "地质构造影响程度")],
    "粗糙度": [("supports", "地质构造影响程度"), ("complements", "张开性（mm）")],
    "张开性（mm）": [("supports", "地质构造影响程度"), ("complements", "粗糙度")],
    "风化程度": [("supports", "完整状态"), ("supports", "围岩级别")],
    "完整状态": [("supports", "围岩级别"), ("supports", "地质简要描述")],
    "岩体含水状态/渗水量": [("supports", "围岩级别"), ("supports", "地质简要描述")],
    "围岩级别": [("derived_from", "岩土类型（名称）"), ("derived_from", "岩石硬度"), ("derived_from", "完整状态"), ("derived_from", "岩体含水状态/渗水量"), ("derived_from", "地质构造影响程度")],
    "开挖工作面素描图": [("derived_from", "工程部位"), ("derived_from", "岩土类型（名称）"), ("derived_from", "地质构造影响程度"), ("derived_from", "岩层产状")],
    "地质简要描述": [("derived_from", "岩土类型（名称）"), ("derived_from", "掌子面状态"), ("derived_from", "完整状态"), ("derived_from", "围岩级别"), ("derived_from", "岩体含水状态/渗水量")],
}


def _field_class(category: str):
    return {
        "管理信息": "class:AdministrativeField",
        "影像辅助": "class:ImageObservationField",
        "条件可量测": "class:MeasurementField",
        "部分可量测": "class:MeasurementField",
        "需外部测试或鉴定": "class:ExternalEvidenceField",
        "需综合评价": "class:ExternalEvidenceField",
        "需现场测量": "class:ExternalEvidenceField",
        "组合成果生成": "class:DerivedResultField",
        "辅助生成": "class:DerivedResultField",
    }.get(category, "class:Field")


def build_field_knowledge_model(records):
    """Return ontology and instance layers for the 28 record-sheet fields."""
    by_name = {record.get("field"): record for record in records}
    ontology = {
        "id": "ontology:site-geological-sketch:v1",
        "label": "掌子面地质素描字段本体",
        "version": "1.0",
        "classes": FIELD_ONTOLOGY_CLASSES,
        "properties": FIELD_ONTOLOGY_PROPERTIES,
        "notice": "本体描述字段类型与证据关系；实例层保存项目记录表字段及其关系。",
    }
    instances = [{
        "id": "instance:geological-sketch-record-schema",
        "label": "项目掌子面地质素描记录表",
        "class": "class:GeologicalSketchRecord",
        "source": "项目地质素描记录表-样表.xlsx",
    }]
    relations = []
    for record in records:
        rid = f"field:{record['record_id']}"
        instances.append({"id": rid, "label": record["field"], "class": _field_class(record.get("category")),
                          "record_id": record["record_id"], "category": record.get("category"),
                          "obtainability": record.get("obtainability")})
        relations.append({"source": "instance:geological-sketch-record-schema", "target": rid, "predicate": "has_field"})
        relations.append({"source": rid, "target": _field_class(record.get("category")), "predicate": "instance_of"})
    for source, targets in FIELD_RELATION_MAP.items():
        if source not in by_name:
            continue
        for predicate, target in targets:
            if target not in by_name:
                continue
            relations.append({"source": f"field:{by_name[source]['record_id']}",
                              "target": f"field:{by_name[target]['record_id']}", "predicate": predicate})
    return {"ontology": ontology, "instances": {"records": instances, "relations": relations}}

# Visual expectation archetypes. Numeric keys are the same features the
# classifier measures on a segmentation region, all on a 0-1 scale:
#   L lightness (exposure-normalised), C chroma, G texture roughness,
#   S strong-contrast speckle share, A structure-tensor anisotropy,
#   B banding, Z grain scale (log-normalised).
ARCHETYPES = {
    "granite_medium":  {"L": .68, "C": .04, "G": 0.56, "S": .24, "A": .15, "B": .10, "Z": .45,
                        "descriptors": ["浅色", "中粗粒", "等粒花岗结构", "块状构造"],
                        "distinguishing": "灰白—肉红色，石英与长石颗粒均匀，暗色矿物呈散点状，无定向排列"},
    "granite_fine":    {"L": .66, "C": .04, "G": 0.70, "S": .18, "A": .18, "B": .08, "Z": .22,
                        "descriptors": ["浅色", "细粒", "等粒", "块状构造"],
                        "distinguishing": "颗粒细而均匀，肉眼难分辨单矿物，整体色浅"},
    "granite_coarse":  {"L": .70, "C": .04, "G": 0.48, "S": .28, "A": .13, "B": .10, "Z": .70,
                        "descriptors": ["浅色", "粗粒", "等粒", "块状构造"],
                        "distinguishing": "单矿物颗粒粗大，可见明显石英/长石晶体"},
    "granite_porphyritic": {"L": .64, "C": .05, "G": 0.62, "S": .34, "A": .15, "B": .10, "Z": .64,
                        "descriptors": ["斑状结构", "浅色基质", "粗大斑晶"],
                        "distinguishing": "粗大斑晶散布于细粒基质中，明暗反差强"},
    "granite_gneissose": {"L": .60, "C": .05, "G": 0.60, "S": .20, "A": .48, "B": .42, "Z": .45,
                        "descriptors": ["浅色", "片麻状构造", "定向排列"],
                        "distinguishing": "浅色矿物与暗色矿物成条带定向排列"},
    "schist":          {"L": .38, "C": .09, "G": 0.82, "S": .14, "A": .62, "B": .52, "Z": .18,
                        "descriptors": ["深色", "片状构造", "鳞片变晶"],
                        "distinguishing": "明显片理，云母类矿物呈定向鳞片，易沿面剥开"},
    "phyllite_slate":  {"L": .32, "C": .07, "G": 0.76, "S": .10, "A": .55, "B": .45, "Z": .10,
                        "descriptors": ["深色", "板状/千枚状构造", "细粒"],
                        "distinguishing": "极细粒定向构造，板理平整，断面呈丝绢光泽"},
    "quartzite":       {"L": .80, "C": .02, "G": 0.38, "S": .08, "A": .16, "B": .10, "Z": .30,
                        "descriptors": ["极浅色", "低彩度", "致密块状"],
                        "distinguishing": "灰白致密，石英为主，断口呈油脂光泽，几乎无暗色矿物"},
    "marble":          {"L": .78, "C": .03, "G": 0.30, "S": .08, "A": .12, "B": .08, "Z": .25,
                        "descriptors": ["浅色", "低纹理", "块状"],
                        "distinguishing": "浅色均质，糖粒状晶粒，常带方解石解理反光"},
    "crystalline_limestone": {"L": .72, "C": .04, "G": 0.46, "S": .10, "A": .18, "B": .15, "Z": .30,
                        "descriptors": ["浅灰", "细晶", "致密"],
                        "distinguishing": "浅灰致密，与大理岩视觉相近，需酸反应区分"},
    "carbonate_dolomite": {"L": .76, "C": .03, "G": 0.38, "S": .09, "A": .14, "B": .12, "Z": .28,
                        "descriptors": ["浅灰白", "低彩度", "致密"],
                        "distinguishing": "浅灰白致密，白云石化后略带黄褐风化色"},
    "metasandstone":   {"L": .62, "C": .07, "G": 0.64, "S": .16, "A": .30, "B": .28, "Z": .30,
                        "descriptors": ["中浅色", "变余砂状结构", "弱定向"],
                        "distinguishing": "保留砂状颗粒外形，轻微定向重结晶"},
    "migmatite_banded": {"L": .58, "C": .06, "G": 0.60, "S": .18, "A": .52, "B": .50, "Z": .45,
                        "descriptors": ["条带状", "明暗交替", "混合岩化"],
                        "distinguishing": "浅色花岗质条带与深色变质残留体交替成带"},
    "volcanic_felsic": {"L": .64, "C": .05, "G": 0.68, "S": .12, "A": .28, "B": .30, "Z": .12,
                        "descriptors": ["浅色", "隐晶质", "流纹构造"],
                        "distinguishing": "极细粒，流纹呈平行条带，可见气孔或杏仁体"},
    "pyroclastic":     {"L": .55, "C": .08, "G": 0.86, "S": .30, "A": .26, "B": .22, "Z": .45,
                        "descriptors": ["碎屑结构", "明暗斑杂", "火山角砾"],
                        "distinguishing": "大小碎屑杂乱分布，可有塑性玻屑与角砾轮廓"},
    "sandstone":       {"L": .62, "C": .08, "G": 0.68, "S": .14, "A": .28, "B": .32, "Z": .26,
                        "descriptors": ["中浅色", "砂状结构", "层理"],
                        "distinguishing": "可见砂粒与层理，颗粒间有胶结物"},
    "siltstone_mudstone": {"L": .46, "C": .06, "G": 0.60, "S": .10, "A": .32, "B": .36, "Z": .15,
                        "descriptors": ["中深色", "细粒", "薄层"],
                        "distinguishing": "细粒均质，层理薄而平直，断口平整"},
    "conglomerate":    {"L": .54, "C": .08, "G": 0.78, "S": .30, "A": .22, "B": .20, "Z": .60,
                        "descriptors": ["砾状结构", "粗大砾石", "基质胶结"],
                        "distinguishing": "明显砾石轮廓散布于基质中，砾径差别大"},
    "soil":            {"L": .52, "C": .12, "G": 0.26, "S": .08, "A": .10, "B": .08, "Z": .08,
                        "descriptors": ["松散", "无结构面", "颜色随含水变化"],
                        "distinguishing": "无岩块结构，颗粒松散或可塑，无定向构造"},
    "coal_carbonaceous": {"L": .18, "C": .03, "G": 0.52, "S": .10, "A": .30, "B": .30, "Z": .12,
                        "descriptors": ["黑色", "低彩度", "污手"],
                        "distinguishing": "黑色、低反光，常呈薄层或透镜状夹层"},
}

# rock name -> (archetype, overrides, extra descriptors, confusable group)
ARCHETYPE_FAMILIES = {
    "granite_medium": "花岗岩类", "granite_fine": "花岗岩类", "granite_coarse": "花岗岩类",
    "granite_porphyritic": "花岗岩类", "granite_gneissose": "花岗岩类",
    "schist": "片岩类", "phyllite_slate": "板岩/千枚岩类",
    "quartzite": "石英岩类", "marble": "大理岩类",
    "crystalline_limestone": "灰岩类", "carbonate_dolomite": "白云岩类",
    "metasandstone": "变质砂岩类", "migmatite_banded": "混合岩类",
    "volcanic_felsic": "火山岩类", "pyroclastic": "火山碎屑岩类",
    "sandstone": "砂岩类", "siltstone_mudstone": "泥质岩类",
    "conglomerate": "砾岩类", "soil": "土体", "coal_carbonaceous": "炭质岩类",
}

ROCK_PROFILES = {
    # 变质岩类
    "黑云片岩": ("schist", {}, [], ["云母石英片岩", "板岩"]),
    "云母石英片岩": ("schist", {"L": .40}, ["石英条带"], ["黑云片岩", "片麻状花岗岩"]),
    "石英岩": ("quartzite", {}, [], ["大理岩", "结晶灰岩"]),
    "变粒岩": ("metasandstone", {"Z": .30, "G": .55}, ["细粒变晶"], ["变质砂岩", "石英岩"]),
    "条带状混合岩": ("migmatite_banded", {}, [], ["混合花岗岩", "片麻状花岗岩"]),
    "花岗岩": ("granite_medium", {}, [], ["二长花岗岩", "花岗闪长岩"]),
    "混合花岗岩": ("granite_gneissose", {}, ["混合岩化"], ["片麻状花岗岩", "条带状混合岩"]),
    "变质长石石英砂岩": ("metasandstone", {"L": .58}, [], ["变质砂岩", "砂岩"]),
    "变质砂岩": ("metasandstone", {}, [], ["砂岩", "变粒岩"]),
    "千枚岩": ("phyllite_slate", {"L": .34}, ["千枚状构造"], ["板岩", "黑云片岩"]),
    "板岩": ("phyllite_slate", {"L": .26}, ["板状构造"], ["千枚岩"]),
    "结晶灰岩": ("crystalline_limestone", {}, [], ["大理岩", "白云质灰岩"]),
    "大理岩": ("marble", {}, [], ["结晶灰岩", "石英岩"]),
    # 花岗岩变种（按岩石学标准 / 暗色矿物 / 结构构造）
    "碱长花岗岩": ("granite_medium", {"C": .05}, ["钾长石偏红"], ["二长花岗岩"]),
    "钾长花岗岩": ("granite_medium", {"C": .05}, ["钾长石偏红"], ["碱长花岗岩", "二长花岗岩"]),
    "二长花岗岩": ("granite_medium", {}, ["钾长石与斜长石近等量"], ["碱长花岗岩", "花岗闪长岩"]),
    "花岗闪长岩": ("granite_medium", {"L": .52, "S": .22}, ["暗色矿物略多"], ["二长花岗岩", "角闪石花岗岩"]),
    "黑云母花岗岩": ("granite_medium", {"L": .48, "S": .22}, ["黑云母散点"], ["角闪石花岗岩", "二云母花岗岩"]),
    "白云母花岗岩": ("granite_medium", {"L": .68, "S": .12}, ["白云母银白反光"], ["二云母花岗岩", "石英岩"]),
    "二云母花岗岩": ("granite_medium", {"L": .60}, ["黑白云母共存"], ["黑云母花岗岩", "白云母花岗岩"]),
    "角闪石花岗岩": ("granite_medium", {"L": .48, "S": .24}, ["角闪石长柱状"], ["黑云母花岗岩", "花岗闪长岩"]),
    "紫苏花岗岩": ("granite_medium", {"L": .42, "S": .24}, ["暗色矿物偏多"], ["角闪石花岗岩"]),
    "细粒花岗岩": ("granite_fine", {}, [], ["中粒花岗岩", "流纹岩"]),
    "中粒花岗岩": ("granite_medium", {}, [], ["细粒花岗岩", "粗粒花岗岩"]),
    "粗粒花岗岩": ("granite_coarse", {}, [], ["中粒花岗岩", "似斑状花岗岩"]),
    "斑状花岗岩": ("granite_porphyritic", {}, [], ["似斑状花岗岩", "粗粒花岗岩"]),
    "似斑状花岗岩": ("granite_porphyritic", {"Z": .64}, [], ["斑状花岗岩"]),
    "晶洞花岗岩": ("granite_coarse", {"S": .26}, ["孔洞与晶簇"], ["粗粒花岗岩"]),
    "片麻状花岗岩": ("granite_gneissose", {}, [], ["混合花岗岩", "条带状混合岩"]),
    # 火成岩类
    "流纹岩": ("volcanic_felsic", {}, [], ["英安岩", "细粒花岗岩"]),
    "流纹质熔结凝灰岩": ("pyroclastic", {"B": .36}, ["熔结条带"], ["熔结凝灰岩", "流纹岩"]),
    "熔结凝灰岩": ("pyroclastic", {}, [], ["流纹质熔结凝灰岩", "火山碎屑岩"]),
    "火山碎屑岩": ("pyroclastic", {}, [], ["熔结凝灰岩", "火山角砾岩"]),
    "火山角砾岩": ("pyroclastic", {"Z": .72}, ["角砾轮廓"], ["集块岩", "砾岩"]),
    "集块岩": ("pyroclastic", {"Z": .88}, ["大块集块"], ["火山角砾岩", "砾岩"]),
    "英安岩": ("volcanic_felsic", {"L": .48}, [], ["流纹岩", "安山岩"]),
    # 沉积岩类
    "粉砂岩": ("sandstone", {"Z": .18, "L": .52}, [], ["泥质粉砂岩", "砂岩"]),
    "泥岩": ("siltstone_mudstone", {}, [], ["泥质粉砂岩", "炭质页岩"]),
    "白云质灰岩": ("carbonate_dolomite", {}, ["白云质"], ["白云岩", "结晶灰岩"]),
    "白云岩": ("carbonate_dolomite", {}, [], ["白云质灰岩", "大理岩"]),
    "石英砂岩": ("sandstone", {"L": .66, "C": .05}, ["石英颗粒为主"], ["砂岩", "石英岩"]),
    "砾岩": ("conglomerate", {}, [], ["含砾砂岩", "火山角砾岩"]),
    "含砾砂岩": ("sandstone", {"S": .24, "Z": .40}, ["含砾"], ["砾岩", "砂岩"]),
    "砂岩": ("sandstone", {}, [], ["石英砂岩", "粉砂岩"]),
    "泥质粉砂岩": ("siltstone_mudstone", {"Z": .22}, ["泥质"], ["粉砂岩", "泥岩"]),
    "炭质页岩": ("coal_carbonaceous", {"L": .22}, ["炭质"], ["煤岩", "泥岩"]),
    "煤岩": ("coal_carbonaceous", {"L": .12}, ["煤层"], ["炭质页岩"]),
    # 土体
    "砾质黏土": ("soil", {"S": .20}, ["含砾"], ["砂质黏土", "杂填土"]),
    "砂质黏土": ("soil", {}, [], ["砾质黏土", "粉细砂土"]),
    "砂土": ("soil", {"G": .32}, [], ["粉细砂土", "砂质黏土"]),
    "粉细砂土": ("soil", {"G": .28}, [], ["砂土", "淤泥"]),
    "黏土": ("soil", {"G": .22}, [], ["淤泥质黏土", "砂质黏土"]),
    "淤泥": ("soil", {"L": .35, "G": .18}, ["饱水软塑"], ["淤泥质黏土", "黏土"]),
    "淤泥质黏土": ("soil", {"L": .38}, [], ["淤泥", "黏土"]),
    "杂填土": ("soil", {"S": .26}, ["成分杂乱"], ["砾质黏土", "砂土"]),
}

VISUAL_PROFILE_BASIS = "通用岩石学视觉鉴定要点（非本项目实测），仅作为候选排序依据，需现场或薄片复核"
CATALOG_BASIS = "项目岩性库 DOCX 原文目录"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except (OSError, ValueError, TypeError):
        return default


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def _normalise_label(value: str):
    return re.sub(r"[\s（）()·/\\_-]", "", str(value or "")).lower()


# ---------------------------------------------------------------- docx / xlsx

def _docx_paragraphs(path: Path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for index, paragraph in enumerate(root.findall(".//w:p", ns), 1):
        text = "".join(item.text or "" for item in paragraph.findall(".//w:t", ns)).strip()
        if text:
            paragraphs.append({"paragraph": index, "text": text})
    return paragraphs


def _split_items(value: str):
    """Split Chinese list punctuation while preserving parenthesised notes."""
    items, buffer, depth = [], [], 0
    for char in value:
        if char in "（(":
            depth += 1
        elif char in "）)" and depth:
            depth -= 1
        if char in "、，,；;" and depth == 0:
            token = "".join(buffer).strip()
            if token:
                items.append(token)
            buffer = []
        else:
            buffer.append(char)
    token = "".join(buffer).strip()
    if token:
        items.append(token)
    return items


def _xlsx_strings(path: Path):
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return ["".join(item.itertext()).strip() for item in root.findall("m:si", ns)]


def _xlsx_rows(path: Path):
    """Read the compact sample workbook without requiring openpyxl."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()).strip() for item in root.findall("m:si", ns)]
        names = sorted(name for name in archive.namelist()
                       if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        if not names:
            return []
        root = ET.fromstring(archive.read(names[0]))
    rows = []
    for row in root.findall(".//m:row", ns):
        values = []
        for cell in row.findall("m:c", ns):
            value = cell.find("m:v", ns)
            text = value.text if value is not None and value.text else ""
            if cell.get("t") == "s" and text:
                try:
                    text = shared[int(text)]
                except (IndexError, ValueError):
                    pass
            inline = cell.find("m:is", ns)
            if inline is not None:
                text = "".join(inline.itertext()).strip()
            values.append((cell.get("r", ""), str(text).replace("\n", " ").strip()))
        rows.append(values)
    return rows


# ---------------------------------------------------------------- record sheet

def import_field_knowledge(source_path: str | Path | None = None):
    """Import the 28-field project schema, option values and obtainability notes."""
    path = Path(source_path) if source_path else None
    values, rows = [], []
    if path and path.is_file() and path.suffix.lower() == ".xlsx":
        try:
            values = _xlsx_strings(path)
            rows = _xlsx_rows(path)
        except (OSError, KeyError, ValueError, ET.ParseError):
            values, rows = [], []
    records = []
    for index, (name, category) in enumerate(FIELD_DEFINITIONS, 1):
        excerpt = next((value for value in values if name in value), "")
        options, target = [], _normalise_label(name)
        for row in rows:
            labels = [value for _, value in row]
            if not any(target in _normalise_label(value) for value in labels):
                continue
            for _, value in row:
                if not value or target in _normalise_label(value) or value in {"□", "£"}:
                    continue
                if value not in options and len(options) < 12:
                    options.append(value)
        records.append({
            "record_id": f"A-F{index:02d}", "field": name, "category": category,
            "obtainability": category, "note": FIELD_NOTES.get(name, ""),
            "excerpt": excerpt, "example_value": options[0] if options else excerpt,
            "option_values": options,
            "source": str(path) if path else "project_field_definition_v1",
            "source_kind": "project_sample_workbook" if path else "builtin_field_list",
            "updated_at": _now(),
        })
    return records


# ---------------------------------------------------------------- rock catalog

def parse_catalog_paragraphs(paragraphs):
    """Turn the project's rock-list paragraphs into groups, parents and rocks.

    The list is a plain outline, so it is parsed by section heading and by the
    "按…划分：" sub-headings rather than by guessing rock names from prose.
    """
    groups, rocks, current_group = [], [], ""
    granite_parent = "花岗岩"

    def add_group(name, paragraph):
        nonlocal current_group
        current_group = name
        if not any(group["name"] == name for group in groups):
            groups.append({"id": f"G{len(groups) + 1:02d}", "name": name, "source_paragraph": paragraph})

    def add_rock(name, group, subgroup="", parent=None, paragraph=None,
                 descriptors=None, aliases=None, record_type="rock"):
        name = re.sub(r"^[、，,。；;以及\s]+|[、，,。；;\s]+$", "", str(name)).strip()
        if not name or name in {"其中", "常见"}:
            return
        if any(item["standard_name"] == name and item["rock_group"] == group for item in rocks):
            return
        rocks.append({
            "record_id": f"B-R{len(rocks) + 1:03d}", "standard_name": name,
            "aliases": aliases or [], "rock_group": group, "subgroup": subgroup,
            "parent_name": parent, "record_type": record_type,
            "hierarchy_path": [group] + ([subgroup] if subgroup else []) + [name],
            "descriptors": descriptors or [],
            "source_paragraph": paragraph, "basis": CATALOG_BASIS,
        })

    for item in paragraphs:
        text, paragraph = item["text"], item["paragraph"]
        if re.match(r"^1[.、]", text) and "变质岩" in text:
            add_group("变质岩类", paragraph)
            continue
        if re.match(r"^2[.、]", text) and "火成岩" in text:
            add_group("火成岩类", paragraph)
            continue
        if re.match(r"^3[.、]", text) and "沉积岩" in text:
            add_group("沉积岩类", paragraph)
            continue
        if text.startswith("其中") and "花岗岩" in text:
            add_rock(granite_parent, current_group or "变质岩类", "花岗岩",
                     paragraph=paragraph, record_type="parent")
            continue
        if "按岩石学标准分类" in text or "按岩石学标准划分" in text:
            for token in _split_items(text.split("：", 1)[-1]):
                match = re.match(r"(.+?)（(.+?)）$", token.strip())
                add_rock(match.group(1) if match else token, current_group, "花岗岩·岩石学标准",
                         granite_parent, paragraph, aliases=[match.group(2)] if match else [])
            continue
        if "按次要暗色矿物划分" in text:
            for token in _split_items(text.split("：", 1)[-1]):
                add_rock(token, current_group, "花岗岩·暗色矿物", granite_parent, paragraph)
            continue
        if "按结构、构造划分" in text:
            for token in _split_items(text.split("：", 1)[-1]):
                match = re.match(r"(.+?)（(.+?)）$", token.strip())
                add_rock(match.group(1) if match else token.strip(), current_group,
                         "花岗岩·结构构造", granite_parent, paragraph,
                         descriptors=[match.group(2)] if match else [])
            continue
        if current_group == "火成岩类":
            body = text.split("为主，", 1)[-1] if "为主，" in text else text.split("：", 1)[-1]
            subgroup = "酸性-中酸性火山岩系"
            for token in _split_items(body):
                add_rock(token, current_group, subgroup, None, paragraph)
            continue
        if current_group == "沉积岩类":
            if "黏土" in text or "砂土" in text or "填土" in text:
                subgroup = "土体"
            else:
                subgroup = "沉积岩"
            for token in _split_items(text.split("：", 1)[-1]):
                add_rock(token, current_group, subgroup, None, paragraph)
            continue
        if current_group == "变质岩类":
            for token in _split_items(text):
                add_rock(token, current_group, "", None, paragraph)
            continue
    return groups, rocks


def _profile_for(name):
    entry = ROCK_PROFILES.get(name)
    if entry is None:
        return None
    archetype, overrides, descriptors, confusable = entry
    base = ARCHETYPES[archetype]
    profile = {key: round(float(overrides.get(key, base[key])), 4)
               for key in ("L", "C", "G", "S", "A", "B", "Z")}
    profile["archetype"] = archetype
    profile["family"] = ARCHETYPE_FAMILIES.get(archetype, "未分类")
    profile["descriptors"] = list(base["descriptors"]) + list(descriptors)
    profile["distinguishing"] = base["distinguishing"]
    profile["confusable_with"] = list(confusable)
    profile["basis"] = VISUAL_PROFILE_BASIS
    profile["profile_level"] = "rock"
    return profile


def import_lithology_catalog(source_path: str | Path, extra_sources=None):
    """Build the project lithology catalogue with visual expectation profiles."""
    path = Path(source_path)
    paragraphs = _docx_paragraphs(path)
    groups, rocks = parse_catalog_paragraphs(paragraphs)
    for group in groups:
        members = [rock for rock in rocks if rock["rock_group"] == group["name"]]
        group["rock_count"] = len(members)
        group["subgroups"] = sorted({rock["subgroup"] for rock in members if rock["subgroup"]})
    for rock in rocks:
        profile = _profile_for(rock["standard_name"])
        rock["visual_profile"] = profile
        rock["profile_basis"] = VISUAL_PROFILE_BASIS if profile else None
        rock["profile_status"] = "general_reference" if profile else "not_profiled"
        rock["source"] = str(path)
        rock["source_sha256"] = _sha256(path)
        rock["classification_status"] = "source_catalog_candidate"
        if profile is None:
            rock["note"] = "该名称来自项目岩性库目录，但没有可用的视觉期望档案；判别时不会被列为候选。"

    sources = [{"path": str(path), "sha256": _sha256(path), "role": "项目岩性库目录"}]
    for extra in extra_sources or []:
        extra = Path(extra)
        if extra.is_file():
            sources.append({"path": str(extra), "sha256": _sha256(extra), "role": "字段定义与可获取性分类"})

    return {
        "schema_version": SCHEMA_VERSION, "kind": "lithology_catalog", "status": "available",
        "source": str(path), "source_refs": sources,
        "groups": groups, "records": rocks,
        "categories": [group["name"] for group in groups],
        "archetypes": {name: {key: value for key, value in body.items()
                              if key in {"L", "C", "G", "S", "A", "B", "Z"}}
                       for name, body in ARCHETYPES.items()},
        "profiled_count": sum(1 for rock in rocks if rock["visual_profile"]),
        "notice": ("目录来自项目岩性库 DOCX 原文；视觉期望档案是通用岩石学要点，"
                   "不是本项目实测特征，也不能替代现场鉴定。"),
        "updated_at": _now(),
    }


# ---------------------------------------------------------------- store / query

def _tokens(query: str):
    return [token for token in re.split(r"[\s,，、;；:：/]+", str(query or "").strip()) if token]


def _record_text(record):
    parts = [str(record.get(key) or "") for key in ("field", "standard_name", "category", "obtainability", "note", "subgroup", "rock_group")]
    parts.extend(record.get("aliases") or [])
    parts.extend(record.get("descriptors") or [])
    profile = record.get("visual_profile") or {}
    parts.extend(profile.get("descriptors") or [])
    parts.append(str(profile.get("distinguishing") or ""))
    parts.append(str(record.get("example_value") or ""))
    return " ".join(parts).lower()


class KnowledgeBase:
    # A remote graph read used to happen on every workbench click.  The local
    # JSON projection is authoritative and byte-identical, so reads are served
    # from it by default and the Neo4j status probe is cached/persisted.
    STATUS_TTL_SECONDS = 30.0

    def __init__(self, root: Path, project_root: Path | None = None):
        self.root = Path(root) / ".lithology-judgment" / "knowledge"
        self.root.mkdir(parents=True, exist_ok=True)
        self.project_root = project_root or Path.cwd()
        self._graph_store = Neo4jGraphStore.from_env()
        self._status_cache: tuple[float, dict] | None = None
        self._ensure_defaults()

    # -- sources ---------------------------------------------------------
    def _project_path(self, *parts):
        for base in (self.project_root, self.project_root.parent):
            candidate = base.joinpath(*parts)
            if candidate.is_file():
                return candidate
        return None

    def source_paths(self):
        return {
            "catalog": self._project_path("00原始数据", "20261026深圳侨城项目掌子面岩性库.docx"),
            "workbook": self._project_path("00原始数据", "地质素描记录表-样表.xlsx"),
            "t01": self._project_path("01技术资料", "任务包", "T01_28字段摄影测量可获取性分类_最小任务包.txt"),
        }

    def rebuild(self):
        """Rebuild both stores from project sources (or from versioned seeds)."""
        paths = self.source_paths()
        catalog_source = paths["catalog"]
        workbook = paths["workbook"]
        t01 = paths["t01"]
        site_seed = self.project_root / "configs" / "knowledge" / "site_records.json"
        catalog_seed = self.project_root / "configs" / "knowledge" / "lithology_catalog.json"

        site_refs = []
        for path in (workbook, t01):
            if path and path.is_file():
                site_refs.append({"path": str(path), "sha256": _sha256(path)})
        seed_site = _read_json(site_seed, {})
        site_records = import_field_knowledge(workbook) if workbook else seed_site.get("records", [])
        if not workbook and seed_site.get("source_refs"):
            site_refs = list(seed_site.get("source_refs") or [])
        site = {
            "schema_version": SCHEMA_VERSION, "kind": "site_records", "status": "available" if site_records else "unavailable",
            "source": str(workbook) if workbook else (site_seed.name if site_records else "unavailable"),
            "source_refs": site_refs, "records": site_records,
            "categories": sorted({record["category"] for record in site_records}),
            "notice": ("字段定义、选项值和可获取性分类来自项目样表与 T01 任务包；"
                       "空白或占位值不代表已验证现场事实。"),
            "updated_at": _now(),
        }
        field_model = build_field_knowledge_model(site_records)
        site["ontology"] = field_model["ontology"]
        site["instance_layer"] = field_model["instances"]
        if catalog_source:
            catalog = import_lithology_catalog(catalog_source, [workbook, t01])
        else:
            catalog = _read_json(catalog_seed, {
                "schema_version": SCHEMA_VERSION, "kind": "lithology_catalog", "status": "unavailable",
                "source": "《20261026深圳侨城项目掌子面岩性库.docx》未在工程资料中找到。",
                "source_refs": [], "groups": [], "records": [], "categories": [],
            })
        _write_json(self.root / "site_records.json", site)
        _write_json(self.root / "lithology_catalog.json", catalog)
        return {"site": site, "lithology": catalog}

    def _ensure_defaults(self):
        site_path = self.root / "site_records.json"
        catalog_path = self.root / "lithology_catalog.json"
        site = _read_json(site_path, {})
        catalog = _read_json(catalog_path, {})
        paths = self.source_paths()
        signature = {key: (_sha256(path) if path else None) for key, path in paths.items()}
        stored = {ref.get("sha256") for ref in (catalog.get("source_refs") or [])}
        site_stored = {ref.get("sha256") for ref in (site.get("source_refs") or [])}
        stale_site = (site.get("schema_version") != SCHEMA_VERSION or not site.get("records")
                      or (signature["workbook"] and signature["workbook"] not in site_stored))
        stale_catalog = (catalog.get("schema_version") != SCHEMA_VERSION or not catalog.get("records")
                         or catalog.get("profiled_count", 0) == 0
                         or (signature["catalog"] and signature["catalog"] not in stored))
        if stale_site or stale_catalog:
            self.rebuild()
            return
        seeds = self.project_root / "configs" / "knowledge"
        if stale_site and (seeds / "site_records.json").is_file():
            _write_json(site_path, _read_json(seeds / "site_records.json", site))
        if stale_catalog and (seeds / "lithology_catalog.json").is_file():
            _write_json(catalog_path, _read_json(seeds / "lithology_catalog.json", catalog))

    # -- read ------------------------------------------------------------
    def _document(self, kind: str):
        filename = "site_records.json" if kind == "site" else "lithology_catalog.json"
        return _read_json(self.root / filename, {"records": []})

    def records(self, kind: str, query: str = "", limit: int = 20, group: str | None = None):
        document = self._document(kind)
        records = document.get("records", [])
        if group:
            records = [record for record in records
                       if group in {record.get("rock_group"), record.get("category")}]
        tokens = _tokens(query)
        scored = []
        for record in records:
            if not tokens:
                scored.append((1.0, record))
                continue
            text = _record_text(record)
            hits = sum(2.0 if token.lower() in text else 0.0 for token in tokens)
            if hits <= 0:
                continue
            name = str(record.get("field") or record.get("standard_name") or "").lower()
            if any(token.lower() in name for token in tokens):
                hits += 1.5
            if name and name == str(query or "").strip().lower():
                hits += 2.0
            scored.append((hits, record))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("record_id"))))
        limit = max(1, min(int(limit), 400))
        payload = {
            "kind": kind, "status": document.get("status", "available"),
            "source": document.get("source"), "source_refs": document.get("source_refs", []),
            "categories": document.get("categories", []),
            "groups": document.get("groups", []),
            "notice": document.get("notice"), "updated_at": document.get("updated_at"),
            "total": len(records),
            "matched": len(scored),
            "records": [record for _, record in scored[:limit]],
        }
        if query:
            payload["match_scores"] = {record["record_id"]: round(score, 2) for score, record in scored[:limit]}
        return payload

    def search(self, query: str = "", limit: int = 20):
        return {"site": self.records("site", query, limit), "lithology": self.records("lithology", query, limit)}

    def record(self, record_id: str):
        for kind in ("site", "lithology"):
            for record in self._document(kind).get("records", []):
                if record.get("record_id") == record_id:
                    return {"kind": kind, "record": record,
                            "source_refs": self._document(kind).get("source_refs", [])}
        return None

    def summary(self):
        """Cheap counts for workbench headers; never touches the network."""
        site = self._document("site")
        catalog = self._document("lithology")
        return {
            "lithology": {"status": catalog.get("status", "unavailable"),
                          "records": len(catalog.get("records", [])),
                          "profiled": catalog.get("profiled_count", 0)},
            "site": {"status": site.get("status", "unavailable"),
                     "records": len(site.get("records", []))},
            "built_at": catalog.get("updated_at"),
            "schema_version": catalog.get("schema_version"),
        }

    def overview(self):
        site = self._document("site")
        catalog = self._document("lithology")
        groups = catalog.get("groups", [])
        rocks = catalog.get("records", [])
        return {
            "kind": "overview",
            "built_at": catalog.get("updated_at"),
            "schema_version": catalog.get("schema_version"),
            "bases": [
                {"key": "lithology", "title": "知识库 B · 项目岩性分类库",
                 "status": catalog.get("status", "unavailable"),
                 "records": len(rocks), "profiled": catalog.get("profiled_count", 0),
                 "groups": [group["name"] for group in groups],
                 "source": catalog.get("source"), "source_refs": catalog.get("source_refs", []),
                 "notice": catalog.get("notice")},
                {"key": "site", "title": "知识库 A · 掌子面地质素描记录表",
                 "status": site.get("status", "unavailable"),
                 "records": len(site.get("records", [])),
                 "groups": site.get("categories", []),
                 "ontology": site.get("ontology", {}),
                 "instance_count": len((site.get("instance_layer") or {}).get("records", [])),
                 "relation_count": len((site.get("instance_layer") or {}).get("relations", [])),
                 "source": site.get("source"), "source_refs": site.get("source_refs", []),
                 "notice": site.get("notice")},
            ],
            # Never block the workbench on a remote database round trip: the
            # last probe is reused and refreshed by /api/knowledge/graph/status.
            "graph_store": self.graph_status(),
            "archetypes": catalog.get("archetypes", {}),
        }

    def graph(self, query: str = "", limit: int = 200, group: str | None = None,
              source: str | None = None):
        """Return a compact, layout-friendly graph of both knowledge bases.

        ``source="neo4j"`` reads the materialized projection (slow: one network
        round trip per call).  The default is the local JSON graph, which is the
        portable source of truth and therefore identical in content.
        """
        site = self.records("site", query, limit, group=group)
        lithology = self.records("lithology", query, limit, group=group)
        site_document = self._document("site")
        field_model = {
            "ontology": site_document.get("ontology") or build_field_knowledge_model(site_document.get("records", []))["ontology"],
            "instances": site_document.get("instance_layer") or build_field_knowledge_model(site_document.get("records", []))["instances"],
        }
        nodes, edges, seen = [], [], set()

        def add(node_id, label, node_type, **extra):
            if node_id in seen:
                return node_id
            seen.add(node_id)
            nodes.append({"id": node_id, "label": label, "type": node_type, **extra})
            return node_id

        site_root = add("kb:site", "掌子面地质素描记录表", "knowledge_base",
                        status=site["status"], source=site["source"], count=site["total"], layer="instance")
        catalog_root = add("kb:lithology", "项目岩性分类库", "knowledge_base",
                           status=lithology["status"], source=lithology["source"], count=lithology["total"])
        ontology_root = add(field_model["ontology"].get("id", "ontology:site-geological-sketch:v1"),
                            field_model["ontology"].get("label", "掌子面地质素描字段本体"), "ontology",
                            layer="ontology", version=field_model["ontology"].get("version"))
        # Keep the root record class as an explicit node.  Earlier versions
        # emitted an edge to this ID without materialising the target, which
        # made Neo4j correctly drop one dangling relationship on sync.
        record_class_id = "class:GeologicalSketchRecord"
        add(record_class_id, "地质素描记录", "ontology_class", layer="ontology")
        edges.append({"source": ontology_root, "target": record_class_id, "relation": "defines_class", "layer": "ontology"})
        for ontology_class in field_model["ontology"].get("classes", []):
            class_id = add(ontology_class["id"], ontology_class["label"], "ontology_class", layer="ontology", parent=ontology_class.get("parent"))
            edges.append({"source": ontology_root, "target": class_id, "relation": "defines_class", "layer": "ontology"})
            if ontology_class.get("parent"):
                edges.append({"source": class_id, "target": ontology_class["parent"], "relation": "subclass_of", "layer": "ontology"})
        for ontology_property in field_model["ontology"].get("properties", []):
            property_id = add(ontology_property["id"], ontology_property["label"], "ontology_property", layer="ontology",
                              domain=ontology_property.get("domain"), range=ontology_property.get("range"))
            edges.append({"source": ontology_root, "target": property_id, "relation": "defines_property", "layer": "ontology"})
        for instance in field_model["instances"].get("records", []):
            instance_id = instance["id"]
            add(instance_id, instance["label"], "field", layer="instance", class_id=instance.get("class"), record_id=instance.get("record_id"), category=instance.get("category"))
        for relation in field_model["instances"].get("relations", []):
            if relation["source"] not in seen or relation["target"] not in seen:
                continue
            edges.append({"source": relation["source"], "target": relation["target"],
                          "relation": relation["predicate"], "layer": "instance"})
        for document, root_id, kind in ((site, site_root, "site"), (lithology, catalog_root, "lithology")):
            for category in document.get("categories", []):
                category_id = add(f"{kind}:category:{category}", category, "category", kind=kind)
                edges.append({"source": root_id, "target": category_id, "relation": "has_category"})

        for record in site.get("records", []):
            node_id = add(f"field:{record['record_id']}", record["field"], "field", kind="site", record=record, layer="instance",
                          class_id=_field_class(record.get("category")))
            edges.append({"source": site_root, "target": node_id, "relation": "contains"})
            if record.get("category"):
                edges.append({"source": node_id, "target": f"site:category:{record['category']}",
                              "relation": "classified_as"})

        for record in lithology.get("records", []):
            node_id = add(f"rock:{record['record_id']}", record["standard_name"], "rock",
                          kind="lithology", record=record, group=record.get("rock_group"))
            edges.append({"source": catalog_root, "target": node_id, "relation": "contains"})
            if record.get("rock_group"):
                edges.append({"source": node_id, "target": f"lithology:category:{record['rock_group']}",
                              "relation": "belongs_to_group"})
            if record.get("parent_name"):
                parent = next((item for item in lithology.get("records", [])
                               if item["standard_name"] == record["parent_name"]), None)
                if parent:
                    edges.append({"source": f"rock:{parent['record_id']}", "target": node_id,
                                  "relation": "has_subtype"})
            profile = record.get("visual_profile") or {}
            if profile:
                profile_id = add(f"profile:{record['record_id']}", "视觉档案", "profile",
                                 kind="lithology", archetype=profile.get("archetype"),
                                 descriptors=profile.get("descriptors"))
                edges.append({"source": node_id, "target": profile_id, "relation": "has_visual_profile"})
            for other in record.get("aliases") or []:
                alias_id = add(f"alias:{record['record_id']}:{other}", other, "alias", kind="lithology")
                edges.append({"source": node_id, "target": alias_id, "relation": "also_called"})

        local_graph = {
            "query": query or "",
            "nodes": nodes[: max(240, min(int(limit), 900) * 4)],
            "edges": edges,
            "backend": "local_json",
            "knowledge_bases": {"site": site, "lithology": lithology},
        }
        remote_requested = source == "neo4j" or (
            source is None
            and os.environ.get("FACE_MAPPING_GRAPH_BACKEND", "local_json").lower() == "neo4j"
            and os.environ.get("FACE_MAPPING_GRAPH_READ_REMOTE", "0") == "1")
        if remote_requested:
            remote = self._graph_store.fetch(limit=max(240, min(int(limit), 900) * 4))
            if remote:
                remote["query"] = query or ""
                remote["knowledge_bases"] = {"site": site, "lithology": lithology}
                return remote
        return local_graph

    def graph_status(self, live: bool = False):
        """Return the Neo4j projection status.

        ``live=False`` (default) never opens a socket: it returns the cached or
        last persisted probe so page rendering stays instant.  ``live=True``
        probes the database at most once per ``STATUS_TTL_SECONDS``.
        """
        if not live:
            if self._status_cache:
                return self._status_cache[1]
            persisted = _read_json(self.root / "graph_status.json", None)
            if isinstance(persisted, dict) and persisted.get("backend"):
                return persisted
            return {"backend": "local", "configured": self._graph_store.configured,
                    "available": None,
                    "message": "尚未探测 Neo4j；打开知识库或同步图谱时探测。"}
        now = time.monotonic()
        if self._status_cache and now - self._status_cache[0] < self.STATUS_TTL_SECONDS:
            return self._status_cache[1]
        status = self._graph_store.status()
        self._remember_status(status)
        return status

    def _remember_status(self, status: dict):
        self._status_cache = (time.monotonic(), status)
        try:
            _write_json(self.root / "graph_status.json", {**status, "checked_at": _now()})
        except OSError:
            pass

    def sync_graph(self, query: str = "", limit: int = 900, group: str | None = None, replace: bool = True):
        # Always materialize from the portable source graph. If the backend is
        # already set to Neo4j, calling ``graph`` here would read the existing
        # remote projection and make a second sync unable to repair it.
        graph = self.graph(query, limit, group, source="local_json")
        result = self._graph_store.sync(graph, replace=replace)
        result["graph"] = {"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", [])),
                            "backend": graph.get("backend", "local_json")}
        self._remember_status({key: value for key, value in result.items()
                               if key in {"backend", "configured", "available", "uri", "database", "nodes", "edges", "message"}})
        return result
