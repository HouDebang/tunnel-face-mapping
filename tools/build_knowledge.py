"""Build the versioned lithology / record-sheet knowledge seeds.

Reads the project sources (rock list DOCX, record-sheet sample workbook, T01
task package) and writes ``configs/knowledge/*.json``. The runtime store copies
these seeds when the project sources are not reachable.

Run from the project root:

    .\\.venv\\Scripts\\python.exe tools/build_knowledge.py
    .\\.venv\\Scripts\\python.exe tools/build_knowledge.py --check
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from face_mapping.knowledge_base import (  # noqa: E402
    KnowledgeBase, _sha256, _write_json, build_field_knowledge_model,
    import_field_knowledge, import_lithology_catalog,
)

SEED_DIR = ROOT / "configs" / "knowledge"
PROFILE_KEYS = ("L", "C", "G", "S", "A", "B", "Z")


def project_file(*parts):
    for base in (ROOT, ROOT.parent):
        candidate = base.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


def build():
    catalog_source = project_file("00原始数据", "20261026深圳侨城项目掌子面岩性库.docx")
    workbook = project_file("00原始数据", "地质素描记录表-样表.xlsx")
    t01 = project_file("01技术资料", "任务包", "T01_28字段摄影测量可获取性分类_最小任务包.txt")
    if catalog_source is None:
        raise SystemExit("未找到岩性库 DOCX，无法构建知识库 B。")

    catalog = import_lithology_catalog(catalog_source, [workbook, t01])
    site_records = import_field_knowledge(workbook)
    field_model = build_field_knowledge_model(site_records)
    site = {
        "schema_version": catalog["schema_version"],
        "kind": "site_records",
        "status": "available",
        "source": str(workbook) if workbook else "builtin_field_list",
        "source_refs": [{"path": str(path), "sha256": _sha256(path)}
                        for path in (workbook, t01) if path],
        "records": site_records,
        "categories": sorted({record["category"] for record in site_records}),
        "ontology": field_model["ontology"],
        "instance_layer": field_model["instances"],
        "notice": ("字段定义、选项值和可获取性分类来自项目样表与 T01 任务包；"
                   "空白或占位值不代表已验证现场事实。"),
    }
    _write_json(SEED_DIR / "lithology_catalog.json", catalog)
    _write_json(SEED_DIR / "site_records.json", site)
    return catalog, site


def check(catalog, site):
    problems = []
    rocks = catalog["records"]
    fields = site["records"]
    if len(fields) != 28:
        problems.append(f"字段数量应为 28，实际 {len(fields)}")
    if len({field['field'] for field in fields}) != 28:
        problems.append("字段名称存在重复")
    if len(catalog["groups"]) != 3:
        problems.append(f"岩类应为 3 组，实际 {len(catalog['groups'])}")
    if len(rocks) < 50:
        problems.append(f"岩性条目偏少：{len(rocks)}")
    if not all(rock.get("visual_profile") for rock in rocks):
        missing = [rock["standard_name"] for rock in rocks if not rock.get("visual_profile")]
        problems.append(f"缺少视觉档案：{'、'.join(missing)}")
    for rock in rocks:
        profile = rock.get("visual_profile") or {}
        keys = [key for key in PROFILE_KEYS if key in profile]
        if len(keys) != len(PROFILE_KEYS):
            problems.append(f"{rock['standard_name']} 档案字段不完整")
        if not rock.get("source_sha256"):
            problems.append(f"{rock['standard_name']} 缺少来源哈希")
    return problems


def main():
    parser = argparse.ArgumentParser(description="构建岩性 / 记录表知识库种子")
    parser.add_argument("--check", action="store_true", help="仅校验现有种子文件，不重写")
    args = parser.parse_args()
    if args.check:
        catalog = json.loads((SEED_DIR / "lithology_catalog.json").read_text(encoding="utf-8"))
        site = json.loads((SEED_DIR / "site_records.json").read_text(encoding="utf-8"))
    else:
        catalog, site = build()
    problems = check(catalog, site)
    print(f"岩性库：{len(catalog['records'])} 条记录，{len(catalog['groups'])} 个岩类，"
          f"{catalog.get('profiled_count', 0)} 条带视觉档案")
    print(f"记录表：{len(site['records'])} 个字段，{len(site['categories'])} 个可获取性类别")
    print("视觉档案依据：" + next((rock['profile_basis'] for rock in catalog['records'] if rock.get('profile_basis')), "—"))
    for group in catalog["groups"]:
        print(f"  - {group['name']}：{group.get('rock_count', 0)} 条")
    if problems:
        print("\n校验未通过：")
        for problem in problems:
            print("  ✗ " + problem)
        raise SystemExit(1)
    print("\n校验通过：字段、岩类、视觉档案与来源哈希齐全。")
    # Touch a runtime store so a running service picks the seeds up on restart.
    workspace = ROOT / "outputs" / "workbench"
    if workspace.is_dir():
        KnowledgeBase(workspace, ROOT).rebuild()
        print(f"已同步运行时知识库：{workspace / '.lithology-judgment' / 'knowledge'}")


if __name__ == "__main__":
    main()
