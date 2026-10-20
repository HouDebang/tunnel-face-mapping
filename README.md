# 掌子面地质识别工作台与自动提取 MVP

版本记录与回退见 [GIT_GUIDE.md](GIT_GUIDE.md)。基准标签为 `baseline-20261020`，运行数据与真实工程照片继续保留在本机。

第二阶段新增本地 HTML 工作台。前端、API 和原有 CLI 共用 `stage_executor.py` 的实际算法阶段，支持上传、独立执行、调参、有效缓存、下游过期标记、历史结果和 ZIP 下载。第一阶段算法与候选属性约定继续适用。

## 启动可视化工作台

Windows 在项目根目录执行（已安装本机 `.venv` 时可直接运行最后一行）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m uvicorn face_mapping.api:app --host 127.0.0.1 --port 8000
```

然后在浏览器打开 **http://127.0.0.1:8000**。也可双击根目录 `start.bat` 启动服务；Ctrl+C 停止服务。默认单进程、单算法工作线程，请勿使用 `--workers` 多进程指向同一个工作区。不同照片可排队；同一照片运行时拒绝重复提交和参数修改。

上传 `examples/real_face_RK4_970.jpg` 即可复现实测。照片按内容验证，支持常规 JPEG、含 MPO 扩展的相机 JPEG 主图和 PNG；限制 32 MiB、4000 万像素，拒绝损坏、全黑/全白及空间常量图。JPEG/MPO 只分析主图，不进行多视图重建。上传后立即显示 EXIF 校正后的原图并保存源字节 SHA256。

工作台使用流程：上传照片 → 左侧选步骤 → 右侧调整参数 → 运行当前步骤或后续步骤 → 切换 Mask/叠加/骨架 → 查看底部对象与日志 → 导出成果。数值控件的类型、默认值和范围来自后端 `PipelineConfig` Schema；中文说明由后端提供。页面 `?task=<ID>` 和历史任务列表可恢复任务，浏览器内存不是任务存储。

裂缝 `threshold` 改变时，仅该分支及下游失效；岩性 `clusters` 改变时保留裂缝/迹线；尺度改变时保留所有识别 Mask 和迹线，只重新计算几何和展示/导出。每次执行使用独立 ID 和目录。缓存依据源文件实际哈希、当前阶段参数、相关 Python 源码及依赖版本、上游文件内容哈希和坐标约定；文件存在并不代表缓存有效。`outcome.json` 也纳入完整性验证。

过期成果不作为当前输出提供，也不会混入 ZIP。可用历史下拉框显式查看旧图与旧参数；历史视图明确只读。运行失败保留错误及历史成功文件；更正参数后可再次运行。服务重启将未完成作业标为中断失败，已完成历史仍可恢复。

单步自动准备其前置阶段；岩性和裂缝分支独立。几何、绘图与导出允许部分分支缺失，缺失对象/指标保持 null 并在报告中说明。素描图层开关、缩放、平移只改变浏览器展示，不调用识别算法。

任务存储默认 `outputs/workbench/<task-id>/`：源图和预览、原子写入的 `task.json`、`executions/<execution-id>/` 各阶段不可变结果。可用环境变量 `FACE_MAPPING_WORKSPACE` 指定其他本地根目录。该目录包含原图、配置、历史坐标和成果，应作为项目数据一同备份。服务只为所管理任务提供文件，不接受本地任意路径参数。

| API | 用途 |
|---|---|
| GET `/api/health`、`/api/schema` | 运行状态、统一配置和中文参数说明 |
| POST `/api/tasks`（multipart `file`） | 上传并建立任务 |
| GET `/api/tasks`、`/api/tasks/{id}` | 历史列表与完整当前状态 |
| GET `/api/tasks/{id}/stages` | 阶段状态与当前有效成果 |
| GET/PUT `/api/tasks/{id}/config` | 读取参数；PUT body 为 `{"config":完整配置}` |
| POST `/api/tasks/{id}/stages/{stage}/run` | 后台单步执行，body `{"force":false,"downstream":false}` |
| POST `/api/tasks/{id}/run-all` | 全流程后台执行 |
| GET `/api/jobs/{job_id}` | 轮询作业及阶段事件（包括 cache_hit） |
| GET `/api/tasks/{id}/results` | 当前有效科学数据及过期/缺失阶段列表 |
| GET `/api/tasks/{id}/history/{execution_id}` | 历史参数、数据和显式历史成果 URL |
| GET `/api/tasks/{id}/artifacts/{artifact_id}` | 不透明 ID 文件读取；历史需 `?history=执行ID` |
| GET `/api/tasks/{id}/logs`、`/download` | 运行日志与当前成果 ZIP |

执行接口返回 HTTP 202 和作业 ID。重复操作/过期 ZIP 返回 409，非法图像/参数返回 4xx 和结构化错误。下载过期成果时，前端先生成当前有效导出，再请求真实 ZIP；API 直接下载过期包会被拒绝。完整交互和测试记录见 [WORKBENCH_REPORT.md](WORKBENCH_REPORT.md)。本轮完整测试 78 项通过，另完成真实浏览器的上传、调参、失效、历史、失败恢复和 ZIP 下载验证，截图与记录在 `outputs/browser_validation/`。

新增核心文件：`frontend/index.html/style.css/app.js`，`api.py`，`workspace.py`，`stage_graph.py`，`parameter_ui.py`，`stage_executor.py`，以及 `tests/test_api.py`、`test_workspace.py`、`test_stage_executor.py`。`pipeline.py` 负责 CLI 顺序编排，不再另写识别算法。

后端实现参考 [FastAPI 文件上传](https://fastapi.tiangolo.com/tutorial/request-files/) 和 [静态资源文档](https://fastapi.tiangolo.com/tutorial/static-files/)。本地作业由受限线程池执行，使用持久状态轮询。

---

可在本地离线运行的单照片基线。读取照片后实际执行图像预处理、候选观测范围识别、视觉区域分割、暗线检测、骨架逐边追踪、几何计算和成果导出。无模型权重、无付费 API、无联网推理要求。

**结果是图像候选对象。** 岩性类别全部为 `unknown`；二维方向不是走向或倾角。自动 ROI 一律未经地质语义验证；回退为整图时，面积、密度均仅表示候选分析范围。

## 安装与一条命令运行

Windows PowerShell，在项目根目录执行。已建立 `.venv`，本机可直接使用其解释器。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m face_mapping --input examples/real_face_RK4_970.jpg --output outputs/my_real_run --config configs/default.yaml
```

输出目录必须为空或不存在。重复试验换一个运行目录，避免旧文件混入新成果。退出码 `0` 表示闭环完成（可能带质量警告），`2` 表示部分失败、失败或参数错误。

精确复现本机安装版本：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

Python 3.10+ 可安装；本轮实测 Python 3.12。其他平台先使用 `python -m venv .venv` 和相应环境解释器。

## 合成样例与测试

```powershell
.\.venv\Scripts\python.exe tools/generate_synthetic.py --output examples/synthetic_face.png
.\.venv\Scripts\python.exe -m face_mapping --input examples/synthetic_face.png --output outputs/my_synthetic_run --config configs/default.yaml
.\.venv\Scripts\python.exe -m pytest -q --junitxml=outputs/test_results.xml
.\.venv\Scripts\python.exe tools/verify_run.py outputs/real_RK4_970
```

合成图只验证程序和计算。生成器的相邻 JSON 明确标注 synthetic；生成时没有将预置几何或答案 Mask 交给检测器。真实图来自项目原始资料，`examples/real_face_RK4_970.json` 记录路径、未标定状态和输入 SHA256；原始资料未修改。

最终样例成果：`outputs/synthetic_face/`、`outputs/real_RK4_970/`。测试执行记录：`outputs/test_results.xml`；独立文件复核记录：`outputs/verification_summary.json`。实测汇总见 `DELIVERY_REPORT.md`。

## 尺度标定

没有尺度时，米制字段为 JSON `null`、CSV 空字段。不能根据照片中的物品推测尺度。若具有可靠参考，并接受表面近似平面、近似正视、均匀投影尺度，可执行：

```powershell
# 0.002 仅示范参数格式，必须替换成实际标定值。
.\.venv\Scripts\python.exe -m face_mapping --input path/to/photo.jpg --output outputs/scaled_run --config configs/default.yaml --meters-per-pixel 0.002 --assume-planar
```

比例单位为 **米 / EXIF 校正后的原始像素**，不是工作图像像素。仅传比例、不承认平面近似时，程序拒绝运行。也可在 YAML 的 `calibration` 设置。程序不进行透视校正、镜头畸变校正或三维姿态反演，标定数值的可靠性由输入证据决定。

## 算法与集中配置

配置由 Pydantic 验证，未知字段、非法范围、非有限数字均报错。全部可调参数位于 `configs/default.yaml`。固定算法常量和实际生效阈值也写入 `results.json.algorithms` / `face_region.json.details`。

在左侧选择 **02 掌子面分割、03 岩性区域分割或 04 裂缝分割**，参数栏顶部可下拉选择算法，只显示当前方法相关参数。选好后点击“运行当前步骤”会自动保存；点击“运行受影响的后续步骤”会继续更新相关统计与素描。原默认方法保留。历史结果不会覆盖，切换方法会使该步骤和相关下游过期，需重新运行。选择“整图分析”时不执行自动掌子面算法。

| 下拉菜单 | 配置键与可选值 |
|---|---|
| 掌子面提取算法 | `face.algorithm`: `central_texture`（默认）、`grabcut` |
| 区域分割算法 | `lithology.algorithm`: `kmeans`（默认）、`slic` |
| 裂隙检测算法 | `fracture.algorithm`: `blackhat_hessian`（默认）、`blackhat`、`frangi` |

| 阶段 | 实际实现 | 主要调节项 |
|---|---|---|
| 预处理 | Pillow 验证/完整解码、EXIF 转正、RGB；等比例限制长边；可选 LAB CLAHE | max_dimension、enhance、clahe_clip/grid |
| 观测范围 | 原中央 LAB/纹理连通域；或中心前景、边缘背景种子初始化的 GrabCut 迭代图割，最长分析边 640 像素 | algorithm、mode、min/max_area_ratio、allow_full_image_fallback、grabcut_iterations/margin |
| 候选区域 | 原 LAB＋纹理＋位置 K-means；或 SLIC 超像素；均拆分为 4 连通区域并合并相邻小区域/近色区域 | algorithm、clusters、slic_segments/compactness、min_region_pixels/area_ratio、merge_color_distance、spatial_weight、max_regions、random_seed |
| 候选裂隙 | CLAHE/Gaussian 后选择 Blackhat＋Hessian、独立 Blackhat 或 Frangi 多尺度暗线响应，再共用连通域面积、长度、细长性及宽度过滤 | algorithm、clahe_clip、blackhat_radius、frangi_sigma_min/max/scale_count、threshold、min_component_area、min_line_length、min_elongation、noise_sigma、line_response_percentile、max_width_ratio |
| 矢量化 | 原图 Mask 骨架化、像素图逐边追踪、端点/分叉拆段、闭环、短段过滤、独立显示简化 | min_length_px、simplify_tolerance_px |

识别参数中的距离和面积用 **工作像素**；矢量长度过滤和显示简化用 **原始像素**。同一照片改变工作分辨率可能改变候选识别，应固定配置后再比较方法。

响应阈值取 `max(threshold, 正响应的 line_response_percentile 分位值)`。两种 Blackhat 方法另要求最小暗线对比；Frangi 直接使用多尺度暗脊响应，不以 Blackhat 作为门限。各算法独立归一化，同一阈值数值不表示相同检出水平。增大门限/分位数、最小面积或最小长度通常减少候选。它们不保证消除阴影、钻孔、接缝、岩石纹理等误检。较宽、较亮或断续的实际裂隙可能漏检。

GrabCut 假设照片中心属于岩面、边缘属于背景；中心有设备或岩面触及图框时可能截错。SLIC 极小或不连续 ROI 产生未分配像素时，以明确记录的连通岛回退补足覆盖，不把它当作已验证岩性。以上方法均可在现有本机依赖上运行，无需模型权重或联网推理。算法依据：[OpenCV GrabCut](https://docs.opencv.org/4.x/d8/d83/tutorial_py_grabcut.html)、[scikit-image SLIC](https://scikit-image.org/docs/stable/api/skimage.segmentation.html#skimage.segmentation.slic)、[scikit-image Frangi](https://scikit-image.org/docs/stable/api/skimage.filters.html#skimage.filters.frangi)。

自动 ROI 无法可靠定位时，`full_image_fallback` 明确记录方法、原因、尝试状态，`verified=false`。设 `allow_full_image_fallback: false` 可要求定位失败即停止依赖阶段。设 `mode: full_image` 可明确选择整图候选分析。

## 坐标、拓扑与统计定义

- 所有成果 Mask、Overlay、素描和矢量都用 EXIF 转正后的原图。仅 `preprocessed.png` 使用工作尺寸。`original.png` 是方向校正后的无损 PNG 解码结果，源文件字节由输入 hash 标识。
- 原点左上、x 向右、y 向下；像素中心为 `(column+0.5,row+0.5)`，画布边界 `[0,W]×[0,H]`。Mask 用精确最近邻像素中心规则映射回原图；坐标矩阵分别记录 x/y 实际缩放，包含整数尺寸取整差异。无裁剪。
- 岩性标签图为 uint16 PNG，0 是背景；`label_value` 与 `R001` 等一一对应。每个区域另存独立二值 Mask，边界包含 exterior / holes，允许多部件。默认 Polygon 精确沿像素单元格边界；面积仍直接计数 Mask，避免简化误差。
- 骨架用八邻域图。若对角连接存在正交中间像素，则去掉对角捷径；所有 `degree != 2` 像素拆段，相邻分叉像素不任意收缩。纯闭环保留重复闭合点。每个无向边只访问一次，过滤掉的边也计入账本。
- “裂隙数量”是过滤后的**候选图路径段数**，不是人工地质结构面条数。所有原始点列保存在 `points`；`display_points` 只用于绘图。长度为完整原始折线欧氏距离总和。
- 端点方向与 PCA 方向均为轴向 `[0,180)` 度，从图像 +x 轴沿向下方向计正角。闭合迹线端点方向为 null；无唯一 PCA 主轴时也为 null。
- `P21_px = sum(length_px) / face_pixel_area`，单位 `px^-1`。有标定 s 时，长度乘 s、面积乘 s²，`P21_m = sum(length_m) / area_m2`。未验证 ROI 时 `statistics_status=candidate`。
- 图层 ID 完整保存在 JSON。为避免覆盖照片，素描只标注最长的至多 80 条 F ID，并在图例明确数量；区域 ID 按实际区域内位置标注。所有原始迹线仍绘出。

## 工程结构

```text
configs/default.yaml          集中参数
src/face_mapping/
  __main__.py                CLI 与退出码
  config.py / schemas.py     参数验证、持久数据契约
  backends.py                统一推理接口及区域/裂隙返回类型
  preprocessing.py           图像验证、EXIF、缩放、增强
  coordinates.py             统一像素中心变换与 Mask/标签重采样
  face_detection.py          候选 ROI 与显式回退
  lithology.py               视觉特征分区、相邻合并
  fracture_detection.py      暗线候选检测与形态筛选
  vectorization.py           骨架图、逐边追踪和账目
  geometry.py                Polygon、长度、方向、面积、密度
  visualization.py           独立图层及综合图绘制
  export.py                  JSON、CSV、SHA256
  pipeline.py                分阶段执行与局部失败隔离
tests/                       几何、输入坐标与集成测试
tools/generate_synthetic.py   可复现合成输入
tools/verify_run.py           从磁盘独立重读并复核成果
examples/                    真实与合成照片，及来源说明
outputs/                     每次运行独立目录
```

单次运行包括需求列出的全部文件，另有 `image_metadata.json`、`face_region.json`、独立区域 Mask、`fracture_skeleton.png`、`config_used.json`、`environment.json`、`execution.log`、`manifest.json`。JSON 为 UTF-8，CSV 为 UTF-8 BOM，便于 Windows Excel 读取。

## 状态与失败

每步记录 input/output、ok/failed/skipped、耗时、错误。识别返回空裂隙 Mask 是合法的零候选结果，同时报告警告；它不证明没有裂隙。识别器抛异常则该路结果和依赖统计为 null。岩性失败不阻断裂隙分支；裂隙失败仍输出岩性图及可绘制的综合图。输入失败仍保存结构化错误、空指标 CSV 和日志。配置错误和拒绝覆盖已有目录在开始处理前报错。

质量报告检查 ROI、区域覆盖及面积、短残余区域、骨架边账目、点坐标和长度、画布尺寸、CSV 对齐、完整性及 Pydantic 重读。`face_verified=false`、`full_image_fallback=true` 等是状态事实，不能将所有布尔字段简单当成必须 true 的通过项。

## 替换模型与精度验证入口

`backends.SegmentationBackend.predict(image, roi_mask, config)` 接收 uint8 RGB 和同尺寸 bool ROI，返回 `LithologySegmentationResult(labels=...)` 或 `FractureSegmentationResult(mask=...)`，携带 method、parameters、warnings、details。正整数标签必须互斥且完整覆盖 ROI，裂隙 Mask 必须在 ROI 内。管道会在边界验证这些约束。

```python
from face_mapping.pipeline import run_pipeline
from face_mapping.config import PipelineConfig

run_pipeline("photo.jpg", "outputs/model_trial", PipelineConfig(),
             lithology_backend=my_lithology_backend,
             fracture_backend=my_fracture_backend)
```

未来 `VLMSegmentationBackend`、`FoundationSegmentationBackend`、`SpecializedSegmentationBackend` 均实现该协议。本轮只实现两个真实 Baseline，未伪装安装这些模型。接外部模型时应记录权重 URL/hash、许可、依赖、版本、提示词/调用参数；不可用时显式抛错或由调用方明确选 Baseline，不暗中换结果。后处理、几何和导出无需改动。

精度评价需建立冻结的真实照片集，保留原始 hash、EXIF 坐标、人工有效 ROI、遮挡 Mask、裂隙 Mask/迹线及岩性参考边界；训练/调参/测试按照片或工况分组，避免同一掌子面近重复图泄漏。固定同一 ROI、尺度、工作尺寸、几何规则后比较不同后端：ROI 用 IoU；裂隙用像素 Precision/Recall/F1 和注明容差的骨架匹配；区域用匹配后的 IoU/边界 F1，不能将无监督区域编号当作岩性类别；长度/密度误差只在人工可靠 ROI 和有效标定下计算。记录漏检、误检、光照、设备和碴堆场景。本轮未完成这类标注精度评价。

算法 API 依据：[OpenCV 形态学文档](https://docs.opencv.org/4.x/d9/d61/tutorial_py_morphological_ops.html)、[scikit-image 骨架化文档](https://scikit-image.org/docs/stable/api/skimage.morphology.html#skimage.morphology.skeletonize)。
