# 第二阶段施工交付报告：掌子面地质识别可视化工作台

日期：2026-10-18。前后端已完成，已用指定真实照片完成浏览器联调。原有 CLI 继续可用。

## 1. 实际实现

- HTML/CSS/原生 JavaScript 三栏工作台：上传与原图预览、9 步导航、双图对比、Mask/叠加切换、缩放/平移/重置、保存图像、区域/迹线表、可折叠日志、历史任务和成果下载。
- FastAPI/Uvicorn 本地服务：照片内容/尺寸/格式校验、独立任务、后台作业、配置校验、阶段状态和文件读取、当前有效 ZIP 下载。兼容真实照片中的 JPEG/MPO 容器，只分析主图并记录说明。
- `stage_executor.py` 成为 CLI 和 Web 的唯一算法执行入口；识别、骨架、几何和绘图继续调用第一阶段模块。前端没有另写识别算法。
- 缓存依赖源文件内容、当前阶段参数、相关源码/运行依赖版本、上游成果内容及坐标约定；包括内部 outcome 文件的完整性检查。
- 调参按依赖失效，保留独立分支。尺度只影响几何和后续，识别结果无需重跑。阶段结果和配置快照按执行 ID 保存，不覆盖历史；过期结果不会作为当前成果提供或混入 ZIP。
- 同任务重复提交和运行中改参数返回冲突；服务使用单进程单算法线程，其他照片可排队。启动时将中断作业标为失败，可重新执行。

## 2. 最终结构

| 文件 | 职责 |
|---|---|
| `frontend/index.html` / `style.css` / `app.js` | 中文工作台、真实 API 调用和显示交互 |
| `src/face_mapping/api.py` | 静态页面、REST API、上传和后台作业入口 |
| `workspace.py` | 任务持久化、不可变运行记录、缓存/失效、作业与安全文件定位 |
| `stage_graph.py` | 阶段依赖、参数归属、实现指纹文件映射 |
| `parameter_ui.py` | 中文参数名称、说明、单位；约束仍来自 PipelineConfig |
| `stage_executor.py` | 八个共享阶段、结果聚合及导出 |
| `pipeline.py` | CLI 顺序编排和原有部分失败/数据契约兼容 |
| `tests/test_api.py` / `test_workspace.py` / `test_stage_executor.py` / `test_workspace_storage.py` | 新增接口、依赖、完整性、存储与导入隔离测试 |
| `start.bat` | Windows 双击启动入口 |
| `outputs/workbench/<任务ID>/` | 源图、任务索引、各阶段历史成果 |
| `outputs/browser_validation/` | 真实浏览器截图、下载 ZIP、核查记录 |

完整安装、API 表、参数和算法说明见 [README](README.md)。

## 3. 启动与使用

双击根目录 **`start.bat`**，再打开 **http://127.0.0.1:8000**。也可在根目录执行：

```powershell
# 新环境安装；本机已安装。
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

# 启动服务，保持此终端运行；Ctrl+C 停止。
.\.venv\Scripts\python.exe -m uvicorn face_mapping.api:app --host 127.0.0.1 --port 8000
```

可上传新图，也可从历史任务选择 `a78812a3aca14e079b101d58c6e113a4` 查看已完成的真实联调结果。联调演示最后保存的参数为 `fracture.threshold=0.60`、`lithology.clusters=5`；这是交互测试配置，不是经精度评价选出的推荐参数。默认 YAML 保持第一阶段默认值。

本轮 QA 启动的服务在收尾时停止；重新启动即可恢复持久任务。

原有 CLI 仍可运行：

```powershell
.\.venv\Scripts\python.exe -m face_mapping --input examples/real_face_RK4_970.jpg --output outputs/my_cli_run --config configs/default.yaml
```

## 4. API 与实际数据流

API 覆盖 `/api/health`、`/api/schema`、任务创建/列表/详情、配置 GET/PUT、单阶段和全流程运行、`/api/jobs/{id}` 轮询、当前结果、历史执行、日志、文件和 ZIP 下载。

执行接口先返回 HTTP 202；前端轮询实际状态。参数由 Pydantic 校验。文件只通过任务内不透明成果 ID 提供，不能传任意本地路径；当前读取拒绝过期成果，历史需显式执行 ID。文件响应 no-store，并通过不可变执行 URL 避免旧图缓存。

预处理 → 掌子面范围 → 岩性/裂缝两个分支；裂缝 → 迹线；几何聚合可用分支；绘图/导出保留缺失状态。依赖失败不会以旧成果填充。完整 JSON、CSV、PNG、配置和质量报告仍使用第一阶段 Schema 和原图像素坐标。

## 5. 实际测试与联调结果

最终完整测试：**78 passed，27.40 秒**。记录为 [JUnit XML](outputs/workbench_test_results.xml)。包括原有 42 项回归和新增 36 项接口、阶段、缓存、任务隔离、MPO、历史、内容篡改、失败、轻量索引及 ASGI 导入隔离测试。

`node --check frontend/app.js` 通过；`pip check` 无破损依赖。测试中有一条第三方 Starlette/httpx 弃用提示，不影响本轮通过结果。

使用 Codex 浏览器自动化工具实际操作页面，而非仅调用 API。测试默认较窄视图及 1440×1000 桌面视图。18 项浏览器检查已记录在 [validation_summary.json](outputs/browser_validation/validation_summary.json)：

| 验收场景 | 实际观察 |
|---|---|
| A 新照片处理 | 通过页面上传 4096×3072 JPEG/MPO，逐步运行预处理、ROI、分区、裂缝、迹线、几何、素描、导出 |
| B 裂缝微调 | 0.20→0.30 保存成功，岩性仍有效，迹线/几何/素描/导出过期；重跑后新执行 URL 正确刷新 |
| B 参数行为复核 | 0.20 和 0.30 均低于实际分位门限 0.348657，Mask 相同；提高到 0.60 后候选像素从 1123110 降至 385751 |
| C 岩性微调 | clusters 4→5，区域 Mask 内容哈希改变；裂缝和迹线的执行 ID 保持不变，相关统计/素描/导出更新 |
| D 历史恢复 | 页面刷新及服务重启后恢复任务；显式查看旧分区、旧参数，历史参数只读；再切回当前任务 |
| E 失败恢复 | 独立任务关闭整图回退时真实定位失败，页面显示模块与原因且无虚构统计；开启回退后成功，失败和成功记录均保留 |
| 查看控件 | 开关图层、放大至125%、拖动35px、重置均有效；后台执行数与执行 ID 均未变化 |
| 下载 | 浏览器真实触发 ZIP 下载；37 个文件，CRC 通过，配置为当前0.60/5；最终浏览器控制台无脚本错误 |

最终真实图候选统计：10 个 unknown 区域，1111 段候选迹线，完整迹线总长 36433.338 px。没有有效尺度，全部米制参数保持 null。观测范围为 full_image_fallback，verified=false；这些数值仅用于联调核查。

下载包中持久成果独立复核 **1143/1143** 通过，包含逐条长度、区域 Mask 面积/覆盖、JSON/CSV、无重复保留边和 SHA256；记录见 [export_verification.json](outputs/browser_validation/export_verification.json)。这证明软件成果一致，不能证明地质识别精度。

主要交付：

- [工作台截图](outputs/browser_validation/workbench_desktop.jpg)
- [浏览器实际下载的完整 ZIP](outputs/browser_validation/real_face_results.zip)
- [下载内容与 hash 核查](outputs/browser_validation/download_audit.json)
- [调参失效截图](outputs/browser_validation/threshold_stale.jpg)
- [失败截图](outputs/browser_validation/failure_recovery_before.jpg) / [恢复截图](outputs/browser_validation/failure_recovery_after.jpg)

联调发现并修复了浮点控件步长错位、历史视图混入当前数据、缺失结果误显零值、向量图文件名不匹配、MPO JPEG 上传拒绝、API 导入干扰运行作业等实际问题。完整坐标保存在不可变 outcome，任务索引不再反复嵌入大型点列；已有重新打开和数据完整性回归测试。

## 6. 当前限制

识别算法仍为第一阶段基线。真实照片中衬砌接缝、碴堆、阴影和岩石纹理仍会误检，候选 ROI 未可靠排除背景，区域类别不代表真实岩性。提高阈值只改变检测行为，不证明精度提高。

本地单进程、单算法线程，不支持多个服务进程同时写同一工作区。历史结果持续保留，暂未实现历史清理 UI。任务中断后重新执行，不自动从中途继续。单比例标定保留平面/均匀投影近似要求，不做透视或三维校正。

## 7. 后续模型和精度入口

继续使用 `SegmentationBackend.predict(image, roi_mask, config)`；`execute_stage()` 与 CLI 仍支持 Baseline 替换注入，替换算法后通过实现/版本指纹使相关缓存失效。Web 本轮固定使用本地 Baseline，没有假装接入外部权重或 VLM。

固定真实照片集、人工可靠 ROI、裂隙 Mask/迹线及区域参考边界后，沿用同一原图坐标、尺度和后处理比较后端。评价 ROI IoU、像素 F1、注明容差的骨架匹配及区域边界误差；可靠 ROI/标定后再评价长度和密度误差。本轮未进行标注精度评价。
