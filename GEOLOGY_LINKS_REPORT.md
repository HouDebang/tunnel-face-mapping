# 跨断面地质连接示意

本轮在原有隧道三维页面内增加两类可切换、可同时显示的连接：候选裂隙迹线之间的橙色示意面，以及候选岩性区域外边界之间的蓝绿色示意面。左侧控制布局保留，模型与原照片及原识别统计独立。

## 使用

1. 沿用 `start.bat` 或 README 的 uvicorn 命令启动，进入“隧道三维”。
2. 建立至少两个不同里程的素描断面，打开左侧 **04 地质模型示意**。
3. 选择“裂隙连接 / 岩性界面 / 同时显示”，点击“连接相邻断面”。
4. 可调整透明度和源对象边界、修改自动配对上限或距离；“手动指定对应对象”内可先“读取源对象”而不自动配对，再选取两端对象添加连接，列表内可显隐、反向、移除。
5. 重开场景、导出 JSON 或下载截图。正视选中断面时临时隐藏连接面，切回透视继续查看模型。

同里程存在多个可见断面时，自动构建要求先隐藏重复断面，以免按任意顺序连错对象；手动指定仍需两个不同里程。仅原照片没有对应识别几何，不会被解析成虚构裂隙或岩性边界。

## 实现与数据

| 文件 | 职责 |
|---|---|
| `src/face_mapping/tunnel_geology.py` | 旧素描源版本查找、归一化曲线冻结、候选配对及连接契约 |
| `src/face_mapping/tunnel_scenes.py` | 场景内模型存储、构建与设置/连接 CRUD 路由 |
| `frontend/geology_geometry.js` | 弧长采样、曲线方向/闭环接缝对齐、二维至三维变换、连接面三角化 |
| `frontend/geology_renderer.js` | 双面透明连接面、源曲线及纵向辅助线 |
| `frontend/tunnel.html/css/js` | 左侧自动/手动管理与双色图例 |
| `tests/test_tunnel_geology.py` | 版本来源、持久化与边界验证 |
| `tools/test_geology_geometry.mjs` | 与 Three.js 实际场景变换交叉检查及曲线几何测试 |

模型保存在原 `scene.json.geology`。各断面源曲线保存于 `features[section_id]`，两类对象分别为 `fractures` 和 `lithology`，归一化坐标为原图连续画布坐标除以原图宽/高（不是除以宽/高减一）。原图中的上边朝上、左右翻转、平面内旋转、显示宽高和偏移，与照片平面使用同一变换。

场景只冻结展示所需曲线：每断面至多 60 条最长候选裂隙、20 个最大候选区域，曲线有采样上限。岩性多边形使用外环，孔洞不参与当前放样，并在说明中记录；不生成封闭实体或体积参数。两端曲线按弧长重采样，对齐开链方向或闭环起点后生成三角条带，用户可反向修正。帘幕只连接两端观察，没有中间里程观测约束。

自动配对按归一化图像位置/形状进行一对一贪心匹配，类别 `unknown` 和相同对象编号不会被当成岩性相同的证据。`match_cost` 是匹配代价，`illustrative_unverified` 是连接状态。自动重建保留手动连接；原识别任务、参数及原场景断面不因建模而重算。

后端入口位于 `/api/tunnel-scenes/{scene_id}/geology`：POST `/build`（`auto_connect:false` 时只冻结对象）；PUT `/settings`；POST `/connections`；PUT/DELETE `/connections/{connection_id}`。所有修改返回完整场景；原场景 JSON 导出自动包含模型。

## 验证入口

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=outputs/geology_validation/pytest.xml
node tools/test_tunnel_geometry.mjs
node tools/test_geology_geometry.mjs
node --check frontend/tunnel.js
node --check frontend/tunnel_renderer.js
```

## 实际验证（2026-10-20）

- 全量 Python 测试 **154 项通过，0 失败**，其中本轮新增 9 项。包含真实合成执行产物的双分支提取、旧素描历史源绑定、冻结后源重跑不变、缺失分支降级、六断面重叠后只读对象/手动配对、CRUD/重开、快照损坏、同里程和开闭类型校验。既有 TestClient/httpx 依赖保留一条弃用警告。
- Node 几何测试通过；对翻转和多个旋转角度，与 Three.js 实际 Object3D 变换矩阵逐点交叉核对。原隧道里程几何测试和前端语法检查通过。
- 独立建立“地质连接效果示意”，场景 `a020f85277e444338110366431fac4a4`。使用同一真实照片素描在 15/30/45 m 重复示意，冻结 198 个展示对象，生成 12 条候选裂隙连接和 12 处候选区域边界连接。**不是三个实际里程观测，也没有验证地质连续性。**
- 实际浏览器验证两类独立/同时显示，手动加入 F281 跨里程对应、反向、显隐、移除，独立读取对象，重启/刷新恢复，JSON 和带标识 PNG 下载；最终保留 24 个自动示意连接，浏览器无控制台错误。
- 导出 JSON 与持久场景一致，冻结几何哈希有效；全部 24 个导出连接都能生成有限坐标的三角网格。原来的六断面场景文件字节未改变，5 个源任务索引哈希也未改变。

本机记录：`outputs/geology_validation/pytest.xml`、`verification.json`、`geometry_audit.json`、`scene_export.json`、`fracture_view.jpg`、`lithology_view.jpg`、`both_view.jpg`、`model_export.png`。真实照片、模型数据和截图不提交至 Git。

初次读取旧素描几何需要保存的作业依赖事件、依赖文件和可复现的缓存键。缺少证据，或实现/运行环境已改变导致缓存键不能复现时，该分支报告不可用；不会猜用当前结果。已成功冻结的几何独立保存，后续源重跑不会替换它。

## 版本

开发分支 `codex/geology-links-demo`。开发前回退点 `pre-geology-links-20261020` 对应上一轮 `a1367a7`；原隧道功能分支 `codex/tunnel-3d-demo` 保留。验证前另存了原场景 JSON 和原任务索引哈希到 `outputs/geology_validation/`，该目录继续只保存在本机。
