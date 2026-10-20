# 隧道三维流程示意 — 施工与验证

日期：2026-10-20。基于现有掌子面识别工作台增加独立三维页面，使用简化直线隧道承载按里程摆放的素描图片。控制栏在左侧。该版本用于流程和展示，不要求精确隧道，不将显示尺寸用作图像标定。

## 已完成

- 在单张识别顶部加入“单张识别 / 隧道三维”入口，保留当前任务链接。
- 创建、修改和重开本机场景，设置名称、里程前缀、起止里程和示意宽高。
- 引用已有任务的有效综合素描或原始照片，按累计里程添加断面；无有效素描返回可定位错误。
- 左侧列表与三维点击选取，显隐、移除、名称、位置、显示尺寸、偏移、旋转和翻转保存。
- 原图/素描切换、轨道旋转/缩放/平移、透视/正视/侧视/俯视、外壳及透明度控制。正视选中断面临时隔离其他图片以避免遮挡。
- 同一成果的三断面示意排布；页面、列表与导出截图均注明示意属性。
- JSON 导出、PNG 截图，场景原子写入、参数校验、独立存储和来源哈希。

## 数据与文件

| 文件 | 职责 |
|---|---|
| `frontend/tunnel.html` / `tunnel.css` / `tunnel.js` | 独立页面、左侧控制和场景 API 调用 |
| `frontend/tunnel_renderer.js` | Three.js 隧道壳、图片断面、选取、相机和截图 |
| `frontend/tunnel_geometry.js` | 位置变换、示意断面轮廓、里程格式化与刻度 |
| `frontend/vendor/` | 本地 Three.js 0.186.1、OrbitControls、MIT 许可证与来源清单 |
| `src/face_mapping/tunnel_scenes.py` | 场景契约、持久化、冻结预览和路由 |
| `src/face_mapping/api.py` | 静态页挂载前注册新路由 |
| `tests/test_tunnel_scenes.py` | 后端场景集成与边界测试 |
| `tools/test_tunnel_geometry.mjs` | 浏览器几何纯函数测试 |

场景目录：`outputs/workbench/.tunnel-scenes/<scene-id>/scene.json` 及 `<section-id>-original.jpg` / `-sketch.jpg`。场景坐标系为局部显示米：X 沿里程增大、Y 向上、Z 为朝里程增大方向观看时的右侧。断面中心为 `[里程-起点, 0.4×隧道高+竖向偏移, 横向偏移]`。图片默认保持宽高比，用户修改属于展示变换。

素描断面绑定源任务 ID、成功执行 ID、输入 SHA256、原成果 SHA256、冻结纹理 SHA256；预览最大边 1024 px。源成果不被改写，纹理冻结后不随源任务重新计算而变化。移除只改变场景列表，并在 `removed_sections` 保留记录和纹理。JSON 导出含元数据与本地纹理 URL；迁移和备份应连同场景目录保存。

接口前缀 `/api/tunnel-scenes`：GET 列表、POST 创建；`/{id}` GET/PUT；`/{id}/sections` POST；`/{id}/sections/{section-id}` PUT/DELETE；`.../texture?layer=sketch|original` GET；`/{id}/export` GET。显式历史素描绑定可通过 section POST 的 `execution_id` 使用，页面默认选择最新有效成果。

## 启动与验证命令

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m uvicorn face_mapping.api:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/tunnel.html
```

新环境先按 README 建立 Python 虚拟环境。沿用原有 `start.bat`，无需新增后端依赖或 npm 构建。

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=outputs/tunnel_validation/pytest.xml
node tools/test_tunnel_geometry.mjs
node --check frontend/tunnel.js
node --check frontend/tunnel_renderer.js
```

## 验证结果

后端新增 10 项测试覆盖创建/修改/移除、服务重开持久化、真实管道素描冻结、指定历史执行、仅原图、无素描失败、非法 ID/范围/参数、源文件及纹理哈希错误，以及原任务索引和几何结果不变。全量 Python 测试 **145 项通过**。唯一依赖警告来自现有 Starlette TestClient/httpx 兼容弃用提示，不影响本轮测试。

Node 几何测试通过：累计里程到局部位置、示意轮廓边界、整公里进位、小数/负里程及刻度选择。前端语法检查通过。

实际浏览器使用已有真实照片 `1#斜进BPRK19+179.jpg` 的有效素描，建立“掌子面三维流程示意”，将同一成果摆在示意里程 15、30、45 m。**这些是同源重复展示，不是三个实际里程的观测记录。** 验证创建、三断面显示、编辑偏移/翻转、刷新重开、选中正视、图层切换、显隐、截图与 JSON 下载。验证文件保留在 `outputs/tunnel_validation/`，不会提交真实照片至 Git。

实际场景 ID：`a461cff450db40cea23ad10a7c12aadd`。`verification.json` 核对了导出 JSON 与持久场景一致、6 张活动预览的哈希和尺寸、前端库清单哈希，以及 5 个原任务索引字节不变。`pytest.xml` 保存 145 项零失败记录，`tunnel_browser.jpg` 保存最终页面，`exported_view.png` 为浏览器实际下载的带水印截图，`final_scene_export.json` 为最终场景导出。浏览器控制台未发现错误或警告。

## 当前边界与后续入口

本报告对应首轮断面展示。后续已增设带来源与未验证状态的裂隙/区域边界连接示意，详见 [GEOLOGY_LINKS_REPORT.md](GEOLOGY_LINKS_REPORT.md)。累计里程只是摆放输入，真实线路、姿态和照片投影尚未校核。后续可在场景 schema 增加线路控制点和断面姿态，再替换位置计算及隧道网格；现有识别模块保持独立。不能直接把不同图像中均为 `unknown` 的分区当作相同岩性。

## 版本与回退

功能分支：`codex/tunnel-3d-demo`。开发前回退点：`pre-tunnel-3d-20261020`（当时的 main，`16a6781`）。原基准标签 `baseline-20261020` 保留。确认工作区干净后，可用 `git switch main` 回到本次功能前的版本，随后重启服务；场景和既有识别数据继续保留在本地输出目录。
