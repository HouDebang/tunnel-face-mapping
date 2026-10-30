// 机器人交互工作台页面控制器：只做 DOM 渲染与事件分发，流程判断全部交给 Mission。
import { Mission, DEMO_SCENE } from "./robot_mission.js";
import { VirtualView, RealView } from "./robot_scene.js";
import { STATE_META, STATES } from "./robot_state.js";
import { analyzeFaceImage, loadSampleResult } from "./robot_recognition.js";

const $ = id => document.getElementById(id);
const mission = new Mission();
const ui = {
  targetConfirmed: false,
  processText: "",
  drawerTab: "raw",
  lastPaint: 0,
  prevS: 0,
  booted: false,
};

let virtualView = null;
let realView = null;
let toastTimer = null;

const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

function showError(message) {
  $("error-text").textContent = message;
  $("error-banner").hidden = false;
}

function clearError() { $("error-banner").hidden = true; }

function degrees(rad) {
  let deg = (rad * 180) / Math.PI;
  deg = ((deg % 360) + 360) % 360;
  return deg;
}

// ---------------------------------------------------------------- 顶部流程
function renderStepper() {
  const state = mission.state;
  const order = Object.values(STATES);
  const index = order.indexOf(state);
  const mappingDone = index >= order.indexOf("S2");
  const planningDone = index >= order.indexOf("S6");
  const navDone = index >= order.indexOf("S8");
  const captureDone = index >= order.indexOf("S11");
  const active = mission.paused ? null : STATE_META[state].step;
  const done = [mappingDone, planningDone, navDone, captureDone];
  document.querySelectorAll(".rb-step").forEach(step => {
    const n = Number(step.dataset.step);
    const isDone = done[n - 1];
    const isActive = !isDone && active === n;
    step.classList.toggle("is-done", isDone);
    step.classList.toggle("is-active", isActive);
    step.classList.toggle("is-paused", Boolean(mission.paused) && isActive);
    const label = step.querySelector(".rb-step-state");
    label.textContent = isDone ? "已完成" : isActive ? (mission.paused ? "已暂停" : "进行中") : "待执行";
  });
}

// ---------------------------------------------------------------- 左侧操作
function actionButton({ label, kind = "primary", disabled = false, reason = "", onClick, testid }) {
  const button = document.createElement("button");
  button.className = `button ${kind}`;
  button.textContent = label;
  button.disabled = disabled;
  if (testid) button.dataset.testid = testid;
  if (reason) button.title = reason;
  if (!disabled && onClick) button.addEventListener("click", onClick);
  return button;
}

function renderActions() {
  const host = $("actions");
  host.replaceChildren();
  const state = mission.state;
  const hint = $("action-hint");
  let text = STATE_META[state].note;
  let warn = false;

  const add = options => host.append(actionButton(options));

  if (mission.paused) {
    add({ label: "继续任务", kind: "primary", onClick: () => { mission.resume(); }, testid: "resume-task" });
    add({ label: "重置任务", kind: "danger", onClick: doReset, testid: "reset-task" });
    hint.textContent = "任务已暂停，点击「继续任务」恢复执行。";
    return;
  }

  if (state === "S0") {
    add({ label: "▷ 开始建图", onClick: () => { mission.startMapping(); }, testid: "start-mapping" });
    text = "点击「开始建图」，机器人将扫描隧道空间并生成环境点云地图。";
  } else if (state === "S1") {
    add({ label: "⏸ 暂停任务", kind: "secondary", onClick: () => { mission.pause(); }, testid: "pause-task" });
    text = "正在建图：虚拟规划视图中点云由洞口向里逐步生成。";
  } else if (state === "S2") {
    add({ label: "开始路径规划", onClick: () => { mission.beginSelect(); }, testid: "begin-planning" });
    text = "建图已完成。点击「开始路径规划」，然后在左侧三维场景中点击选择测量站位。";
  } else if (state === "S3") {
    if (!mission.target) {
      add({ label: "⌖ 在场景中点击选择目标", kind: "secondary", disabled: true, reason: "请在虚拟规划视图中点击隧道地面" });
      add({ label: "取消规划", kind: "secondary", onClick: () => { mission.cancelSelect(); ui.targetConfirmed = false; } });
      text = "请在「虚拟规划视图」中点击隧道地面选择目标位置（橙色环即为目标点）。";
      warn = true;
    } else if (!ui.targetConfirmed) {
      add({ label: "确认目标", onClick: () => { ui.targetConfirmed = true; renderActions(); toast(`目标点 P1 已确认：${mission.formatChainage(mission.target.chainage)}`); }, testid: "confirm-target" });
      add({ label: "重新选择", kind: "secondary", onClick: () => { mission.clearTarget(); ui.targetConfirmed = false; renderActions(); } });
      text = `已选目标 P1 · ${mission.formatChainage(mission.target.chainage)} · 横向 ${mission.target.u.toFixed(2)} m。确认后生成路径。`;
    } else {
      add({ label: "生成路径", onClick: () => { startPlanning(); }, testid: "generate-path" });
      add({ label: "重新选择", kind: "secondary", onClick: () => { mission.clearTarget(); ui.targetConfirmed = false; renderActions(); } });
      text = "目标已确认。点击「生成路径」计算从当前位置到目标点的行进路线。";
    }
  } else if (state === "S4") {
    add({ label: "路径生成中…", disabled: true });
    text = "正在根据目标点生成规划路径。";
  } else if (state === "S5") {
    if (!mission.path?.confirmed) {
      add({ label: "确认路径", onClick: () => { mission.confirmPath(); }, testid: "confirm-path" });
      text = `路径长度 ${mission.path ? mission.path.length.toFixed(1) : "—"} m，预计 ${mission.path ? mission.eta(mission.path.length).toFixed(0) : "—"} s。确认后可开始行进。`;
    } else {
      add({ label: "▷ 开始行进", onClick: () => { mission.startNavigation(); }, testid: "start-nav" });
      text = "路径已确认。点击「开始行进」，机器人将沿路径自主移动。";
    }
  } else if (state === "S6") {
    add({ label: "⏸ 暂停任务", kind: "secondary", onClick: () => { mission.pause(); }, testid: "pause-task" });
    text = "机器人正在行进：虚拟视图与现场视图同步更新位姿与履带动作。";
  } else if (state === "S7") {
    add({ label: "开始摄影测量", onClick: () => { mission.startCapture(); }, testid: "start-capture" });
    text = "机器人已到达测量站位。点击「开始摄影测量」启动三相机与补光。";
  } else if (state === "S8") {
    add({ label: "⏸ 暂停任务", kind: "secondary", onClick: () => { mission.pause(); }, testid: "pause-task" });
    text = "摄影测量进行中：三相机视锥与补光已开启，正在采集掌子面影像。";
  } else if (state === "S9") {
    add({ label: "生成素描成果", onClick: () => { runProcess(); }, testid: "start-process" });
    text = "影像采集完成。点击「生成素描成果」调用现有单张识别流水线。";
  } else if (state === "S10") {
    add({ label: "成果处理中…", disabled: true });
    text = ui.processText || "正在调用单张识别流水线生成素描成果。";
  } else if (state === "S11") {
    add({ label: "查看成果详情", onClick: () => openDrawer("sketch"), testid: "open-result" });
    add({ label: "重新识别", kind: "secondary", onClick: () => { runProcess(true); } });
    text = mission.result?.fallback
      ? "识别服务不可用，当前展示本机预生成样例成果（已标注来源）。"
      : "任务完成：素描图与 28 字段成果表已生成，可查看详情。";
    warn = Boolean(mission.result?.fallback);
  }

  if (state !== "S0") {
    add({ label: "↺ 重置任务", kind: "danger", onClick: doReset, testid: "reset-task" });
  }
  hint.textContent = text;
  hint.classList.toggle("warn", warn);
}

// ---------------------------------------------------------------- 右侧状态
function renderStatus() {
  const meta = STATE_META[mission.state];
  const pill = $("stage-pill");
  pill.className = `status-pill ${meta.pill}`;
  pill.textContent = mission.paused ? "已暂停" : meta.title;
  $("stage-name").textContent = `${mission.state} · ${meta.title}${mission.paused ? "（暂停）" : ""}`;

  const mappingPct = Math.round(mission.mapping.progress * 100);
  $("mapping-pct").textContent = `${mappingPct}%`;
  $("mapping-bar").style.width = `${mappingPct}%`;
  $("mapping-bar").classList.toggle("done", mission.mapping.done);
  $("mapping-note").textContent = mission.mapping.done
    ? `已覆盖 ${DEMO_SCENE.end_chainage_m - DEMO_SCENE.start_chainage_m} m · 点云地图就绪`
    : mission.mapping.active ? `扫描至 ${mission.formatChainage(DEMO_SCENE.start_chainage_m + (DEMO_SCENE.end_chainage_m - DEMO_SCENE.start_chainage_m) * mission.mapping.progress)}` : "未开始";

  const navPct = mission.path ? Math.round((mission.nav.s / mission.path.length) * 100) : 0;
  $("nav-pct").textContent = `${navPct}%`;
  $("nav-bar").style.width = `${navPct}%`;
  $("nav-bar").classList.toggle("done", mission.nav.done);
  $("nav-note").textContent = mission.nav.done
    ? "已到达目标位置并停车"
    : mission.path ? `已行进 ${mission.nav.s.toFixed(1)} / ${mission.path.length.toFixed(1)} m` : "未开始";

  const capturePct = Math.round(mission.capture.progress * 100);
  $("capture-pct").textContent = `${capturePct}%`;
  $("capture-bar").style.width = `${capturePct}%`;
  $("capture-bar").classList.toggle("done", mission.capture.done);
  $("capture-note").textContent = mission.capture.done
    ? "3 帧原始影像 + 正射影像已入库"
    : mission.capture.active ? `已采集 ${mission.capture.frames} / 3 帧` : "未开始";

  $("kv-pos").textContent = `X ${mission.robot.x.toFixed(2)} · Z ${mission.robot.z.toFixed(2)}`;
  $("kv-chainage").textContent = mission.formatChainage(mission.robot.chainage);
  $("kv-yaw").textContent = `${degrees(mission.robot.yaw).toFixed(1)}°`;
  $("kv-speed").textContent = `${mission.robot.speed.toFixed(2)} m/s`;
  $("kv-path").textContent = mission.path ? `${mission.path.length.toFixed(1)} m · ${mission.path.confirmed ? "已确认" : "待确认"}` : "—";
  $("kv-target").textContent = mission.target ? `P1 · ${mission.formatChainage(mission.target.chainage)}` : "—";
  $("kv-state").innerHTML = `<span class="status-pill ${meta.pill}">${mission.paused ? "已暂停" : meta.title}</span>`;

  const realState = $("real-state");
  const live = $("real-state").parentElement;
  live.classList.remove("on", "busy", "warn");
  if (mission.state === "S1") { realState.textContent = "建图中"; live.classList.add("busy"); }
  else if (mission.state === "S6") { realState.textContent = "行进中"; live.classList.add("on"); }
  else if (mission.state === "S8") { realState.textContent = "采集中"; live.classList.add("busy"); }
  else if (mission.state === "S10") { realState.textContent = "处理中"; live.classList.add("busy"); }
  else if (mission.paused) { realState.textContent = "已暂停"; live.classList.add("warn"); }
  else if (mission.state === "S11") { realState.textContent = "任务完成"; live.classList.add("on"); }
  else { realState.textContent = "待机"; }

  $("real-foot").textContent = mission.state === "S8"
    ? "停车 · 三相机采集中"
    : mission.state === "S6" ? "行进中 · 履带驱动"
    : mission.state === "S7" || mission.state === "S9" || mission.state === "S10" || mission.state === "S11" ? "停车 · 装置待机"
    : "停车 · 装置关闭";
}

function renderLog() {
  const host = $("log");
  if (!mission.logs.length) {
    host.innerHTML = '<li class="rb-log-empty">尚无记录</li>';
    return;
  }
  host.innerHTML = mission.logs.slice(-12).map(item => `<li><time>${esc(item.time)}</time><span>${esc(item.message)}</span></li>`).join("");
  host.scrollTop = host.scrollHeight;
}

function renderResults() {
  const result = mission.result;
  const setThumb = (imgId, emptyId, url) => {
    const img = $(imgId);
    const empty = $(emptyId);
    if (url) {
      img.src = url;
      img.hidden = false;
      empty.style.display = "none";
    } else {
      img.removeAttribute("src");
      img.hidden = true;
      empty.style.display = "flex";
    }
  };
  setThumb("thumb-raw", "thumb-raw-empty", mission.capture.done ? "/robot/assets/capture_1.jpg" : null);
  setThumb("thumb-ortho", "thumb-ortho-empty", mission.capture.done ? "/robot/assets/ortho.jpg" : null);
  setThumb("thumb-mesh", "thumb-mesh-empty", mission.capture.done ? "/robot/assets/ortho.jpg" : null);
  setThumb("thumb-sketch", "thumb-sketch-empty", result?.sketchUrl || null);
  $("open-result").disabled = !result;
  $("result-note").textContent = result
    ? result.fallback
      ? "识别服务不可用，当前为本机预生成样例成果；表格与图像均标注来源。"
      : `识别任务 ${result.taskId?.slice(0, 8)} · 分区 ${result.summary.regionCount} 个 · 迹线 ${result.summary.fractureCount} 条`
    : "采集完成后可查看原始影像、正射影像、重建预览与素描成果。";
}

function renderAll() {
  renderStepper();
  renderActions();
  renderStatus();
  renderResults();
}

// ---------------------------------------------------------------- 任务动作
async function doReset() {
  ui.targetConfirmed = false;
  ui.processText = "";
  ui.prevS = 0;
  mission.reset();
  if (virtualView) {
    await virtualView.init(DEMO_SCENE);
    virtualView.setMapping(0);
    virtualView.setRobot(mission.robot);
    virtualView.setSelectMode(false);
    virtualView.setCaptureCone(false);
  }
  if (realView) realView.reset();
  renderAll();
  toast("任务已重置");
}

function startPlanning() {
  const res = mission.generatePath();
  if (!res.ok) { showError(res.reason); return; }
  ui.targetConfirmed = true;
  setTimeout(() => {
    const committed = mission.commitPath();
    if (!committed.ok) showError(committed.reason);
  }, 650);
}

async function runProcess(force = false) {
  if (!force && mission.state !== "S9") return;
  if (mission.state === "S11") {
    // 重新识别：回到成果处理阶段
    mission.machine.state = "S9";
    mission.machine.setContext({ processed: false });
    mission.emit("state", { from: "S11", to: "S9", event: "RETRY" });
  }
  const res = mission.beginProcess();
  if (!res.ok) { showError(res.reason); return; }
  ui.processText = "正在上传掌子面影像…";
  renderActions();
  try {
    const response = await fetch("/robot/assets/ortho.jpg", { cache: "no-store" });
    if (!response.ok) throw new Error("演示影像不可用");
    const blob = await response.blob();
    const result = await analyzeFaceImage({
      blob,
      filename: "tunnel_face_ortho.jpg",
      onProgress: progress => {
        ui.processText = progress.text;
        if (mission.state === "S10") renderActions();
      },
    });
    mission.completeProcess(result);
    toast("素描成果已生成");
  } catch (error) {
    const sample = await loadSampleResult().catch(() => null);
    mission.completeProcess({
      fallback: true,
      error: error.message,
      sketchUrl: sample?.sketchUrl || null,
      lithologyUrl: sample?.lithologyUrl || null,
      fractureUrl: sample?.fractureUrl || null,
      form: sample?.form || null,
      summary: sample?.summary || { regionCount: 0, fractureCount: 0, warnings: [] },
      taskId: null,
      executionId: null,
      inputSha256: sample?.inputSha256 || null,
    });
    showError(`识别服务调用失败：${error.message}。已载入本机样例成果，页面仍可完成演示。`);
    toast("已切换到本机样例成果");
  } finally {
    ui.processText = "";
    renderAll();
  }
}

// ---------------------------------------------------------------- 成果抽屉
const TAB_TITLES = { raw: "原始影像", ortho: "正射影像", mesh: "三维重建", sketch: "素描图", form: "28 字段表" };

function openDrawer(tab = "raw") {
  ui.drawerTab = tab;
  $("drawer").hidden = false;
  renderDrawer();
}

function closeDrawer() { $("drawer").hidden = true; }

function renderDrawer() {
  const result = mission.result;
  document.querySelectorAll("#drawer-tabs button").forEach(button => {
    button.classList.toggle("active", button.dataset.tab === ui.drawerTab);
  });
  const body = $("drawer-body");
  const source = $("drawer-source");
  const trace = $("drawer-trace");

  if (!result) {
    body.innerHTML = '<div class="rb-empty">尚未生成成果。请先完成采集与成果处理。</div>';
    source.textContent = "—";
    trace.textContent = "—";
    return;
  }

  const metrics = `
    <div class="rb-metrics">
      <div class="rb-metric"><span>候选岩性分区</span><b>${esc(result.summary.regionCount)}</b></div>
      <div class="rb-metric"><span>候选结构面迹线</span><b>${esc(result.summary.fractureCount)}</b></div>
      <div class="rb-metric"><span>迹线总长 (px)</span><b>${result.summary.totalLengthPx != null ? Number(result.summary.totalLengthPx).toFixed(1) : "—"}</b></div>
      <div class="rb-metric"><span>P21 (px⁻¹)</span><b>${result.summary.p21 != null ? Number(result.summary.p21).toFixed(6) : "—"}</b></div>
    </div>`;

  if (ui.drawerTab === "raw") {
    body.innerHTML = `<div class="rb-split">
      <figure class="rb-figure"><img src="/robot/assets/capture_1.jpg" alt="采集帧 1"><figcaption>采集帧 01 · 相机 CAM-L（演示素材）</figcaption></figure>
      <div>
        <figure class="rb-figure"><img src="/robot/assets/capture_2.jpg" alt="采集帧 2"><figcaption>采集帧 02 · CAM-C</figcaption></figure>
        <figure class="rb-figure" style="margin-top:12px"><img src="/robot/assets/capture_3.jpg" alt="采集帧 3"><figcaption>采集帧 03 · CAM-R</figcaption></figure>
      </div>
    </div><p class="rb-hint">三帧为演示占位素材，用于表达"三相机同步采集"的产品流程；真实设备接入后由相机采集结果替换。</p>`;
  } else if (ui.drawerTab === "ortho") {
    body.innerHTML = `${metrics}
      <figure class="rb-figure"><img src="/robot/assets/ortho.jpg" alt="掌子面正射影像">
      <figcaption>掌子面正射影像（演示素材，来自项目真实掌子面照片；未做尺度标定，不用于量测）。</figcaption></figure>
      <div class="rb-split" style="margin-top:14px">
        <figure class="rb-figure"><img src="${esc(result.lithologyUrl || "")}" alt="岩性候选分区叠加"><figcaption>候选岩性分区（图像特征分区，类别为 unknown）</figcaption></figure>
        <figure class="rb-figure"><img src="${esc(result.fractureUrl || "")}" alt="候选结构面叠加"><figcaption>候选结构面迹线（算法候选，需人工核验）</figcaption></figure>
      </div>`;
  } else if (ui.drawerTab === "mesh") {
    body.innerHTML = `${metrics}
      <div class="rb-mesh-note">
        <strong>三维重建成果占位</strong>
        <p>本阶段未接入空三 / 稠密重建，因此不展示虚构网格。真实重建成果应包含：带尺度的三维网格、纹理与相机位姿。</p>
        <p style="margin-top:8px">当前可用的真实成果为正射影像与候选识别结果；重建成果将在接入摄影测量软件后补充。</p>
      </div>
      <figure class="rb-figure" style="margin-top:14px"><img src="/robot/assets/ortho.jpg" alt="重建参考影像">
      <figcaption>参考影像：正射影像（非三维网格）</figcaption></figure>`;
  } else if (ui.drawerTab === "sketch") {
    body.innerHTML = result.sketchUrl
      ? `<figure class="rb-figure"><img src="${esc(result.sketchUrl)}" alt="掌子面地质素描">
          <figcaption>综合地质素描：掌子面范围、候选岩性分区与候选结构面迹线叠加。</figcaption></figure>
         ${result.fallback ? '<p class="rb-hint warn">当前为本机预生成样例成果（识别服务不可用），非本次实时识别。</p>' : ""}`
      : '<div class="rb-empty">尚未生成素描图。</div>';
  } else {
    const fields = result.form?.fields || [];
    if (!fields.length) {
      body.innerHTML = '<div class="rb-empty">尚未生成 28 字段成果表。</div>';
    } else {
      const statusText = status => status === "auto_filled" ? "自动填充" : status === "candidate_or_review" ? "候选待复核" : "需外部资料";
      const tagClass = status => status === "auto_filled" ? "auto" : status === "candidate_or_review" ? "review" : "external";
      body.innerHTML = `<table class="rb-table"><thead><tr><th style="width:30%">字段</th><th>值</th><th style="width:20%">状态</th></tr></thead><tbody>
        ${fields.map(field => `<tr>
          <td>${esc(field.field)}</td>
          <td class="val ${field.value ? "" : "blank"}">${esc(field.value || "—")}</td>
          <td><span class="rb-tag ${tagClass(field.status)}">${statusText(field.status)}</span></td>
        </tr>`).join("")}
      </tbody></table>
      <p class="rb-hint">空值表示没有可靠影像证据或需要现场/管理资料，系统不会用推测值填充。</p>`;
    }
  }

  source.textContent = result.fallback
    ? "成果来源：本机预生成样例（识别服务不可用）"
    : `成果来源：单张识别流水线 · 任务 ${result.taskId?.slice(0, 12) || "—"}`;
  trace.textContent = result.inputSha256
    ? `追溯：execution ${result.executionId ? result.executionId.slice(0, 10) : "—"} · sha256 ${result.inputSha256.slice(0, 16)}…`
    : "追溯：—";
}

// ---------------------------------------------------------------- 视图同步
function syncViews(dt) {
  if (!virtualView || !realView) return;
  const pose = mission.robot;
  virtualView.setRobot(pose);
  if (mission.path) virtualView.setPathProgress(mission.path.length ? mission.nav.s / mission.path.length : 0);

  const moving = mission.state === "S6" && !mission.paused;
  const delta = Math.max(0, mission.nav.s - ui.prevS);
  ui.prevS = mission.nav.s;
  realView.setRobot(pose, { follow: mission.state === "S6" || mission.state === "S7" });
  realView.controls.enabled = !(mission.state === "S6" || mission.state === "S7");
  if (moving && delta > 0) realView.setWheelSpin(delta, dt);
  realView.updateCamera();
  realView.render();
}

let lastFrame = performance.now();
function loop(now) {
  const dt = Math.min((now - lastFrame) / 1000, 0.1);
  lastFrame = now;
  mission.tick(dt);
  syncViews(dt);
  if (now - ui.lastPaint > 120) {
    ui.lastPaint = now;
    renderStatus();
  }
  requestAnimationFrame(loop);
}

// ---------------------------------------------------------------- 事件绑定
function bind() {
  document.querySelectorAll("#virtual-tools .rb-mini").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll("#virtual-tools .rb-mini").forEach(item => item.classList.remove("active"));
      button.classList.add("active");
      virtualView?.setView(button.dataset.view);
    });
  });

  $("layer-cloud").addEventListener("change", event => virtualView?.setLayers({ cloud: event.target.checked }));
  $("layer-shell").addEventListener("change", event => virtualView?.setLayers({ shell: event.target.checked }));
  $("layer-path").addEventListener("change", event => virtualView?.setLayers({ path: event.target.checked }));

  $("open-result").addEventListener("click", () => openDrawer("sketch"));
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer").addEventListener("click", event => { if (event.target === $("drawer")) closeDrawer(); });
  $("drawer-tabs").addEventListener("click", event => {
    const button = event.target.closest("button[data-tab]");
    if (!button) return;
    ui.drawerTab = button.dataset.tab;
    renderDrawer();
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeDrawer(); });
  $("error-close").addEventListener("click", clearError);

  window.addEventListener("resize", () => { virtualView?.resize(); realView?.resize(); });
}

// ---------------------------------------------------------------- 初始化
async function boot() {
  mission.subscribe((type, payload) => {
    if (type === "state") {
      if (payload?.to === "S3") virtualView?.setSelectMode(true);
      if (payload?.to && payload.to !== "S3") virtualView?.setSelectMode(false);
      if (payload?.to === "S7") { realView?.setCapture(false); realView?.setStatus("idle"); }
      if (payload?.to === "S8") {
        realView?.setCapture(true);
        realView?.setStatus("capture");
        virtualView?.setCaptureCone(true, mission.robot);
      }
      if (payload?.to === "S9") virtualView?.setCaptureCone(false);
      if (payload?.to === "S6") realView?.setStatus("moving");
      if (payload?.to === "S1") realView?.setStatus("mapping");
      if (payload?.to === "S9") {
        renderResults();
        setTimeout(() => { if (mission.state === "S9") runProcess(); }, 900);
      }
      renderAll();
    } else if (type === "mapping") {
      virtualView?.setMapping(payload.progress);
      renderStatus();
    } else if (type === "target") {
      virtualView?.setTarget(payload);
      renderActions();
    } else if (type === "path") {
      virtualView?.setPath(payload);
      renderStatus();
      renderActions();
    } else if (type === "capture") {
      if (payload.done) realView?.setCapture(false);
      renderStatus();
      renderResults();
    } else if (type === "log") {
      renderLog();
    } else if (type === "reset") {
      ui.prevS = 0;
    } else if (type === "result") {
      renderResults();
      renderAll();
    }
  });

  $("kv-tunnel").textContent = DEMO_SCENE.name;
  $("kv-range").textContent = `${mission.formatChainage(DEMO_SCENE.start_chainage_m)} ~ ${mission.formatChainage(DEMO_SCENE.end_chainage_m)}`;
  $("mission-title").textContent = "侨城东路 · 掌子面智能素描";

  // 等一帧布局，确保三维容器已有尺寸；用定时器而不是 rAF，避免页面在后台时初始化被挂起。
  await new Promise(resolve => setTimeout(resolve, 60));

  try {
    virtualView = new VirtualView($("view-virtual"), {
      onPick: ({ x, z }) => {
        if (mission.state !== "S3") return;
        mission.setTarget(x, z);
        ui.targetConfirmed = false;
        renderActions();
        toast(`已选择目标点 P1：${mission.formatChainage(mission.target.chainage)}`);
      },
    });
    await virtualView.init(DEMO_SCENE);
    virtualView.setRobot(mission.robot);
    virtualView.setMapping(0);
  } catch (error) {
    showError(`虚拟规划视图初始化失败：${error.message}`);
  }

  try {
    realView = new RealView($("view-real"));
    await realView.loadRobot();
    realView.setStatus("idle");
  } catch (error) {
    showError(`现场执行视图初始化失败：${error.message}`);
  }

  bind();
  renderAll();
  renderLog();
  // 调试/验收钩子：控制台可直接查看任务状态，不影响正常流程。
  window.__robot = { mission, ui, views: { virtualView, realView } };
  requestAnimationFrame(loop);
  ui.booted = true;
}

boot();
