# 机器人交互工作台 V0.1 · 交付说明

> 第三业务模块「机器人交互 / Robot Operation」的首个可运行版本。
> 目标：在现有智能地质素描平台内，由用户亲自操作一台履带式摄影测量机器人，依次完成 **建图 → 规划 → 行进 → 采集**，并看到掌子面自动化素描成果。

---

## 1. 如何运行

```powershell
.\start.bat          # 或 .\.venv\Scripts\python.exe -m uvicorn --app-dir src face_mapping.api:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/robot.html>，或从任意页面的顶部导航进入「机器人交互」。

> **必须重启服务**：本次在 `api.py` 中新增了 `/robot-assets` 静态挂载（用于直接读取 `robot/` 下的 GLB）。旧进程不会自动加载该路由。

浏览器要求：支持 WebGL2（与隧道三维一致）。

---

## 2. 修改文件清单

| 类型 | 文件 | 说明 |
|---|---|---|
| 修改 | `frontend/index.html` | 主导航新增第三个入口 `#open-robot` |
| 修改 | `frontend/tunnel.html` | 顶栏新增「机器人交互」按钮 |
| 修改 | `src/face_mapping/api.py` | 新增 `/robot-assets` 挂载（6 行，位于 catch-all 之前） |
| 新增 | `frontend/robot.html` | 模块页面骨架（207 行） |
| 新增 | `frontend/robot.css` | 模块样式（复用 `style.css` 设计令牌） |
| 新增 | `frontend/robot.js` | 页面控制器与渲染函数（577 行） |
| 新增 | `frontend/robot_state.js` | 任务状态机（纯 JS，无 DOM/Three 依赖） |
| 新增 | `frontend/robot_mission.js` | 任务控制器：位姿、路径、计时、进度 |
| 新增 | `frontend/robot_scene.js` | 虚拟规划视图 + 现场执行视图 + GLB 加载 |
| 新增 | `frontend/robot_recognition.js` | 单张识别流水线适配层（HTTP 编排） |
| 新增 | `frontend/vendor/loaders/GLTFLoader.js` | three@0.186.1 官方加载器（本地化，无 CDN） |
| 新增 | `frontend/vendor/utils/BufferGeometryUtils.js` | GLTFLoader 依赖 |
| 新增 | `frontend/vendor/utils/SkeletonUtils.js` | GLTFLoader 依赖 |
| 新增 | `frontend/robot/assets/*` | 演示影像与预生成样例成果（约 1.5 MB） |
| 新增 | `docs/robot/*.png` | 验收截图 |

**未改动**：`frontend/app.js`、`frontend/tunnel.js`、`frontend/tunnel_renderer.js`、`frontend/tunnel_geometry.js`、`style.css`、`tunnel.css`，以及 `workspace.py` / `stage_executor.py` / `schemas.py` / `tunnel_scenes.py` / `export.py` 等后端模块。

---

## 3. 组件结构

原生 JS 没有组件框架，按**职责**拆成模块与渲染函数，对应关系如下：

| 设计中的组件 | 实际落点 |
|---|---|
| `RobotWorkspace` | `robot.html` + `.rb-workspace` 三栏栅格 |
| `MissionHeader` | `.topbar` + `.workspace-switcher` + `#stepper` |
| `MissionTaskPanel` | 左栏「任务信息 / 操作 / 执行记录」三张卡片 |
| `WorkflowStepper` | `renderStepper()`，01 建图 / 02 规划 / 03 行进 / 04 采集 |
| `VirtualPlanningView` | `VirtualView extends TunnelRenderer`（`robot_scene.js`） |
| `RealExecutionView` | `RealView`（`robot_scene.js`，一套场景 + 三路相机：固定监控 / 机载第一人称 / 融合第三人称） |
| `RobotStatusPanel` | 右栏「当前阶段 / 三组进度条 / 设备状态」 |
| `MappingPanel` `PlanningPanel` `NavigationPanel` `AcquisitionPanel` | `renderActions()` 按状态分支渲染的上下文按钮 + `renderStatus()` 的进度 |
| `ResultPanel` | 右栏成果缩略图 + 成果抽屉（`#drawer`，5 个页签） |

---

## 4. 核心状态机

文件：`frontend/robot_state.js`（纯 JS，约 140 行，可脱离页面单独测试）。

### 4.1 状态

| 状态 | 含义 | 对应步骤 |
|---|---|---|
| S0 | 待机 | 01 |
| S1 | 建图中 | 01 |
| S2 | 建图完成 | 01 |
| S3 | 目标点选择 | 02 |
| S4 | 路径规划中 | 02 |
| S5 | 路径待确认 | 02 |
| S6 | 行进中 | 03 |
| S7 | 已到达 | 03 |
| S8 | 采集中 | 04 |
| S9 | 采集完成 | 04 |
| S10 | 成果处理 | 04 |
| S11 | 任务完成 | 04 |

### 4.2 转换与守卫

| From | 事件 | 守卫 | To |
|---|---|---|---|
| S0 | START_MAPPING | — | S1 |
| S1 | MAPPING_DONE | — | S2 |
| S2 | BEGIN_SELECT | — | S3 |
| S3 | TARGET_SET / CLEAR_TARGET | — | S3 |
| S3 | CANCEL_SELECT | — | S2 |
| S3 | GENERATE_PATH | **必须已选目标点** | S4 |
| S4 | PATH_READY | — | S5 |
| S5 | CONFIRM_PATH | **必须已生成路径** | S5 |
| S5 | START_NAV | **必须已确认路径** | S6 |
| S6 | ARRIVED | — | S7 |
| S7 | START_CAPTURE | **必须已到达** | S8 |
| S8 | CAPTURE_DONE | — | S9 |
| S9 | START_PROCESS | **必须已采集** | S10 |
| S10 | PROCESS_DONE | — | S11 |
| 任意 | RESET | — | S0 |

**暂停不是状态，而是正交标志**（`paused`）。只有 S1/S6/S8 可暂停；恢复时把 `dt` 归零一帧，避免页面切后台再回来产生位移跳跃。

### 4.3 重置语义

`reset()` 清空：机器人位姿、目标点、路径、建图进度、采集进度、成果引用；**不删除**已写入 `outputs/` 的历史任务与样例成果。

---

## 5. 虚实双场景同步

```
Mission（唯一数据源）
  ├── robot {x, z, yaw, speed, chainage, u}
  ├── path  {points, curve, length, confirmed}
  ├── mapping / nav / capture 进度
  └── machine（状态机）
        │
        ├── subscribe → VirtualView.setRobot / setPath / setMapping / setCaptureCone
        └── subscribe → RealView.setRobot / setWheelSpin / setCapture
```

- 两个视图**只读**同一份 `Mission`，各自不计算位姿。
- 每帧由 `requestAnimationFrame` 统一推进：`mission.tick(dt)` → 两个视图同步渲染。
- 现场视图在 S6/S7 使用追尾机位（`lerp` 平滑），S8 切到侧后方采集机位。
- 未来接入真机时，只需把 `Mission` 的位姿来源换成设备适配器，视图与状态机不变。

---

## 6. 三维场景复用说明

| 复用项 | 方式 |
|---|---|
| 隧道几何与里程标签 | 继承 `TunnelRenderer`，直接调用 `setScene()` 生成外壳、网格、里程标注、方向箭头 |
| 里程 → 世界坐标 | `x = chainage − start_chainage_m`，`z = offset_u_m`，与隧道三维完全一致 |
| 相机/交互 | 继承 OrbitControls；新增透视/俯视/正视/侧视切换 |
| 断面图层 | 机器人场景不加载断面（`sections: []`），避免机器人穿模 |
| 机器人模型 | `robot/Tracked_Photogrammetry_Robot/*.glb` 通过新增 `/robot-assets` 挂载读取，不复制 10 MB 资产 |
| GLTFLoader | 从 three@0.186.1 官方包本地化到 `frontend/vendor/`，沿用现有 importmap，无 CDN、无构建 |

**为加载 GLB 必须补的两件事**（原隧道场景是"无光照 + 按需渲染"）：

1. 增加 `HemisphereLight` + `DirectionalLight`，否则 PBR 材质全黑；
2. 机器人页启用连续渲染循环（`requestAnimationFrame`），静态页仍保持按需渲染。

**坐标约定**（与隧道三维一致）：+X 里程增大、+Y 向上、+Z 右侧；机器人局部 −Z 为前进方向，放置时 `rotation.y = atan2(−fx, −fz)`。

### 6.1 现场执行的三路视角

| 视角 | 相机 | 行为 | 实现要点 |
|---|---|---|---|
| 01 固定监控 | `monitorCamera` | 机位固定（隧道壁 4.55 m 高，朝洞口方向），**观察点不动、机器人动** | 独立透视相机，不参与交互；画面带 `CAM-01 + 时间码` OSD |
| 02 机载第一人称 | `fpvCamera` | 挂在 GLB 的 `Camera_Center` 节点上，随车体与云台实时运动 | 子节点相机，局部 z = −0.62 m（越过遮光罩，不拍到自己镜筒）；≈60° 视场 |
| 03 融合第三人称 | `camera` | 第三人称，可拖动旋转/缩放；行进与到达时自动追尾跟随 | `OrbitControls` + 逐帧 `lerp` 跟随；采集阶段切到侧后方机位 |

**性能做法**：三路共用同一个场景与同一个 WebGL 上下文——每帧渲染三次到离屏缓冲，再按 cover 规则拷贝到三块 2D 画布（`drawFeed()`）。这样只上传一份 44 万面几何体，也避免三个 GL 上下文。实测 1440×900 下稳定 **60 fps**。

**图层隔离**：相机视锥体放在 layer 1，监控与融合相机可见，机载相机不可见（否则第一人称里会看到"自己的视场框"）。

> 说明：第三路"融合"目前是**同一场景中的第三人称机位**，用于表达"由前两路合成的第三人称画面"这一产品概念；真正的多视角重建（NeRF / 稠密重建）不在本阶段范围内。

---

## 7. 模拟数据 vs 真实数据

| 数据 | 来源 | 说明 |
|---|---|---|
| 隧道几何 | 程序化生成 | 60 m 直线示意隧道，复用现有剖面函数 |
| 建图点云 | 程序化生成（固定种子） | 约 2.2 万点，按里程切片逐步显现，**非 SLAM** |
| 路径规划 | 确定性算法（中心线趋近） | **非真实导航规划器** |
| 机器人位姿 | 运动学积分 | **非真实定位** |
| 摄影测量三帧 | `frontend/robot/assets/capture_*.jpg` | **占位素材**，表达"三相机同步采集" |
| 正射影像 | `frontend/robot/assets/ortho.jpg` | 项目真实掌子面照片（1600×1200），**未做尺度标定** |
| 三维重建 | **不展示网格** | 抽屉中明确写"重建成果占位，未接入空三" |
| 识别 / 素描 / 28 字段表 | **真实调用现有流水线** | 上传正射影像 → `run-all` → 轮询 → 取 `geological_sketch.png` 与 `geological_sketch_form.json` |
| 岩性类别 | 未判别 | 界面只显示"候选分区（unknown）"，不编造岩石名称 |

识别服务不可用时，自动回退到 `frontend/robot/assets/sample_*.jpg|json`（本机用同一流水线预生成的真实样例），并在抽屉底部标注"成果来源：本机预生成样例"。

---

## 8. 测试结果

### 8.1 端到端流程（真实浏览器 · Edge headless · 1440×900 · GPU）

| 步骤 | 结果 |
|---|---|
| 进入 `/robot.html` | 通过，S0 待机，仅「开始建图」可用 |
| 开始建图 → S2 | 通过，建图进度 0→100%，点云逐步生成 |
| 点击场景选点 | 通过，射线拾取命中 `RK4+982.7`，生成 P1 标记 |
| 确认目标 → 生成路径 | 通过，S3→S4→S5，路径 12.3 m / 预计 8 s |
| 确认路径 → 开始行进 | 通过，S6，两视图同步移动，履带轮系转动 |
| 到达停车 | 通过，S7，`nav=100%`，速度归零 |
| 开始采集 | 通过，S8，三视锥 + 补光 + 进度 0→100% |
| 生成素描成果 | 通过，S9→S10→S11，真实任务 `bd7b94a0e5c1` |
| 成果抽屉 | 通过，素描图与 28 字段表（29 行含表头，6 项自动填充） |
| 重置 | 通过，S0，目标/路径/进度清空，两视图复位 |
| 三路现场视角 | 通过：监控机位固定、FPV 随车体移动、融合视角可拖动，1440×900 下 60 fps |
| 控制台错误 | **0** |

### 8.2 状态机断言（浏览器内执行）

23 项断言全部通过，覆盖：初始状态、非法操作拦截（未建图规划 / 未选点生成路径 / 未确认路径行进 / 未到达采集）、暂停冻结与恢复推进、到达判定、重置清理。

### 8.3 后端回归

`pytest -q` → **174 passed**（新增 `/robot-assets` 挂载未影响任何既有用例）。

### 8.4 既有页面回归

`/` 与 `/tunnel.html` 截图正常，导航新增第三项后布局未变。

截图见 `docs/robot/`。

---

## 9. 尚未实现 / 已知限制

| 项 | 现状 | 建议 |
|---|---|---|
| 真实 SLAM | 程序化点云 + 裁剪揭示 | 接入点云/栅格地图后替换 `buildPointCloud` |
| 真实路径规划 | 中心线趋近 | 替换 `planPath()`，`NavigationPath` 契约不变 |
| 真实定位 | 运动学积分 | 实现 `RobotAdapter`（`getState/sendGoal`）替换数据源 |
| 三维重建 | 仅占位说明 | 接入空三成果后新增 `mesh` 字段与加载器 |
| 融合视角 | 同场景第三人称机位（非真实多视角重建） | 如需真实融合，接入 NeRF / 稠密重建管线 |
| 相机内外参 | 未标定 | 数据对象预留 `intrinsics: null` |
| 工程绝对坐标 | 无 | 场景仍是 `local_tunnel_m`，界面标注"示意" |
| 多测量点 | 单点 P1 | 状态机已支持"完成后回到 S2"，需扩展点位列表 |
| 任务持久化 | 仅内存 | 建议按 `tunnel_scenes` 模式新增 `robot_missions.py`（`.robot-missions/<id>/mission.json`） |
| 演示时间轴 | 未实现 | 建议 cue 列表 + 同一状态机驱动（见架构报告 §5） |
| 移动端 | 未适配 | 已有 1024/720 断点，需真机验证 |

---

## 10. 关键文件位置速查

| 内容 | 位置 |
|---|---|
| 状态机 | `frontend/robot_state.js` |
| 位姿 / 路径 / 计时 | `frontend/robot_mission.js` |
| 双场景渲染 | `frontend/robot_scene.js` |
| 识别适配 | `frontend/robot_recognition.js` |
| 页面渲染与事件 | `frontend/robot.js` |
| 样式 | `frontend/robot.css` |
| GLB 路由 | `src/face_mapping/api.py`（`/robot-assets`） |
| 设计说明 | `ROBOT_INTERACTION_ARCHITECTURE_REPORT.md` |
