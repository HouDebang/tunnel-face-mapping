# MVP 施工交付报告

交付日期：2026-10-18。当前工程已完成端到端闭环；真实识别精度尚未通过人工标注验证。

## 1. 完成的功能

从空目录建立 Python 包、隔离运行环境、严格配置与数据契约。实际实现图像校验与 EXIF 校正、缩放和 CLAHE、自动观测区域候选与显式整图回退、LAB/纹理/空间特征分区与相邻合并、Blackhat/Hessian 暗线候选、骨架化、分叉逐边追踪、长度/方向/面积/密度计算、综合素描与分层图、JSON/CSV/质量报告/哈希清单。

所有识别结果来自运行时图像计算。无外部模型权重或 API 依赖。未标定照片的米制量全部为 null；岩性类别全部 unknown。异常按步骤记录，依赖失败时不填造数值，独立分支可继续运行。

## 2. 项目结构

核心代码在 `src/face_mapping/`，识别、几何、绘图、导出及执行器分模块。`backends.py` 定义统一接口和区域/裂隙两类返回类型，`schemas.py` 定义全部持久对象。`configs/default.yaml` 集中算法参数；`tests/` 包含三组自动化测试；`tools/` 提供合成输入与独立成果复核；`examples/` 保存输入及来源。完整文件职责与数据定义见 [README](README.md)。

## 3. 实际运行方法

在当前项目根目录执行 PowerShell：

```powershell
# 本轮已经安装。新环境先执行这两行。
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

# 一条命令处理真实照片；输出目录必须为空或不存在。
.\.venv\Scripts\python.exe -m face_mapping --input examples/real_face_RK4_970.jpg --output outputs/my_real_run --config configs/default.yaml

# 自动化测试与成果独立重读。
.\.venv\Scripts\python.exe -m pytest -q --junitxml=outputs/test_results.xml
.\.venv\Scripts\python.exe tools/verify_run.py outputs/real_RK4_970
```

尺度、调参、精确版本复现及模型注入命令见 README。`requirements-lock.txt` 记录本轮实测依赖版本；`.venv` 为 Python 3.12 环境。

## 4. 实际测试结果与成果

最终执行 `pytest`：**42 passed in 2.58s**。其中输入/配置/坐标 9 项、几何 19 项、集成 14 项。`pip check`：No broken requirements found。

覆盖水平/垂直/45°/折线、分叉、闭环、断开分量、逐边无重复、简化不改长度、矩形与孔洞面积、EXIF、非整数像素中心缩放、序列化、标定换算、无效图像、两个识别分支分别失败、CLI 和拒绝混入已有成果。独立审计发现并修正了非整数最近邻缩放的像素中心偏移，已有回归测试。

| 输入 | 图像尺寸 | 区域数 | 候选迹线段数 | 总迹线长度 | ROI | 状态 |
|---|---:|---:|---:|---:|---|---|
| 合成测试图 | 640×480 | 4 | 12 | 1341.119 px | 220584 px²；heuristic_candidate，未验证 | completed_with_warnings |
| RK4+970 真实照片 | 4096×3072 | 10 | 3035 | 98882.994 px | 12582912 px²；full_image_fallback，未验证 | completed_with_warnings |

真实照片工作尺寸为 1280×960。以上数目是当前参数下、分叉拆段和短段过滤后的图路径数，不代表真实地质裂隙数量。两组照片均没有可靠尺度输入，米制长度/面积/密度均为 null。真实整图候选密度为 0.0078585143 px⁻¹，不能作为已验证掌子面 P21。

最终成果目录：

- [真实照片综合素描](outputs/real_RK4_970/geological_sketch.png)
- [真实照片主结果](outputs/real_RK4_970/results.json)、[参数 CSV](outputs/real_RK4_970/parameters.csv)、[质量报告](outputs/real_RK4_970/quality_report.json)
- [合成样例综合素描](outputs/synthetic_face/geological_sketch.png)、[主结果](outputs/synthetic_face/results.json)
- [JUnit 测试记录](outputs/test_results.xml)、[独立复核记录](outputs/verification_summary.json)
- [缺失输入失败示范](outputs/failure_missing_input/results.json)：状态 failed，fractures/statistics 为 null，日志定位 preprocessing，依赖步骤 skipped。

两个正式运行目录包含全部要求的图像、Mask、矢量 JSON、统计 CSV 和质量报告；另有独立区域 Mask、骨架、配置、环境版本和 SHA256 manifest。独立磁盘复核：合成样例 **38/38**，真实照片 **3067/3067**，逐文件 hash、逐条长度、区域面积与覆盖、CSV/JSON 数值全部通过。该复核只证明软件成果一致，不证明识别精度。

## 5. 当前识别能力与已观察限制

视觉区域由颜色、纹理和空间特征分组，类别保持 unknown。裂隙由局部暗线增强、响应门限、连通域细长性和宽度等规则产生；实际统计使用完整骨架点列，面积用原图 Mask 像素计数，绘图简化不参与长度计算。

已人工查看输入及输出：真实照片顶部喷混凝土衬砌和下部碴堆未被自动 ROI 可靠排除，因此明确回退整图。衬砌接缝、碴堆纹理及部分阴影被提为候选裂隙，连续结构也可能被切成多个短段；存在较多误检与碎片。候选区域也可能反映光照和表面颜色差异，不能据此认定岩性边界。较宽、弱对比或不连续裂隙可能漏检。

当前更适合作为可检查、可调参、可替换的流程基线。工程闭环完成；未达到或宣称工程级地质识别精度。

## 6. 后续验证入口

通过 `SegmentationBackend.predict(image, roi_mask, config)` 和 `run_pipeline(..., lithology_backend=..., fracture_backend=...)` 接入视觉大模型、SAM 或专用分割模型；满足相同标签/Mask 契约即可复用全部几何和导出。未来类名入口为 VLMSegmentationBackend、FoundationSegmentationBackend、SpecializedSegmentationBackend，本轮未假装实现这些模型。

使用同一冻结真实照片集、同一人工可靠 ROI、同一原图坐标与尺度、同一后处理参数进行比较。应补充人工裂隙与边界标注，评价 ROI IoU、像素 F1、明确容差的骨架匹配及区域边界误差；有可靠标定和 ROI 后再评价长度、密度误差。按照片/工况分组留出测试集，不能用合成图通过情况替代真实精度。

附：自动审批拒绝清理早期试跑目录（工具仅返回 blocked by policy）。因此保留 `outputs/synthetic_default/`、`outputs/real_RK4_970_default/`，它们来自坐标修正前的开发检查，**不属于最终成果**；最终成果以本报告链接为准。
