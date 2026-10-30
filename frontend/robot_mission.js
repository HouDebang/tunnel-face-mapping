// 任务控制器：机器人位姿、路径、进度计时全部在这里，两个三维视图只订阅、不各自计算。
import * as THREE from "three";
import { MissionMachine, STATE_META, STATES } from "./robot_state.js";

export const DEMO_SCENE = Object.freeze({
  id: "robot-demo-tunnel",
  name: "侨城东路隧道（示意）",
  chainage_prefix: "RK",
  start_chainage_m: 4970,
  end_chainage_m: 5030,
  width_m: 10,
  height_m: 8,
  sections: [],
  geology: null,
});

export const TUNING = Object.freeze({
  mappingSeconds: 12,
  captureSeconds: 6,
  vMax: 2.0,
  accel: 0.9,
  decel: 1.2,
});

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const smooth = t => { const x = clamp(t, 0, 1); return x * x * (3 - 2 * x); };

/** 里程 + 横向偏移 → 世界坐标（与隧道三维模块一致：+X 里程，+Z 右侧）。 */
export function tunnelToWorld(scene, chainage, u = 0) {
  return { x: chainage - scene.start_chainage_m, y: 0, z: u };
}
export function worldToTunnel(scene, x, z) {
  return { chainage: x + scene.start_chainage_m, u: z };
}
export function yawFromForward(fx, fz) {
  return Math.atan2(-fx, -fz);
}

/** 演示级路径规划：先回到隧道中心线，再在末段横向趋近目标。 */
export function planPath(from, to) {
  const dx = to.x - from.x;
  const dz = to.z - from.z;
  const distance = Math.hypot(dx, dz);
  const points = [];
  if (Math.abs(dx) < 0.6) {
    points.push(new THREE.Vector3(from.x, 0, from.z), new THREE.Vector3(to.x, 0, to.z));
  } else {
    const steps = Math.max(6, Math.round(Math.abs(dx) / 1.1));
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const x = from.x + dx * t;
      const toCenter = smooth(t / 0.28);
      const toTarget = smooth((t - 0.62) / 0.38);
      const z = from.z * (1 - toCenter) * (1 - toTarget) + to.z * toTarget;
      points.push(new THREE.Vector3(x, 0, z));
    }
    points[points.length - 1].z = to.z;
  }
  const curve = new THREE.CatmullRomCurve3(points, false, "centripetal", 0.5);
  const length = Math.max(curve.getLength(), 0.01);
  return {
    points: points.map(p => [round(p.x), 0, round(p.z)]),
    curve,
    length,
    straight: distance,
    method: "centerline_v1",
  };
}

function round(v) { return Math.round(v * 1000) / 1000; }

export class Mission {
  constructor({ scene = DEMO_SCENE, startChainage = 4972, startU = 0 } = {}) {
    this.scene = scene;
    this.startPose = { chainage: startChainage, u: startU };
    this.listeners = new Set();
    this.logs = [];
    this.machine = new MissionMachine({
      context: { sceneReady: false, target: null, path: null, pathConfirmed: false, arrived: false, captured: false, processed: false },
      onChange: change => { this.emit("state", change); },
    });
    this.robot = this.poseAtStart();
    this.target = null;
    this.path = null;
    this.mapping = { active: false, progress: 0, done: false };
    this.nav = { active: false, s: 0, speed: 0, done: false };
    this.capture = { active: false, progress: 0, done: false, frames: 0 };
    this.result = null;
    this.log("任务已创建，等待开始建图");
  }

  poseAtStart() {
    const { x, z } = tunnelToWorld(this.scene, this.startPose.chainage, this.startPose.u);
    return { x, z, y: 0, yaw: -Math.PI / 2, speed: 0, chainage: this.startPose.chainage, u: this.startPose.u };
  }

  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit(type, payload = {}) { for (const fn of this.listeners) fn(type, payload, this); }

  log(message) {
    const now = new Date();
    const time = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;
    this.logs.push({ time, message });
    if (this.logs.length > 60) this.logs.shift();
    this.emit("log", { time, message });
  }

  get state() { return this.machine.state; }
  get paused() { return this.machine.paused; }

  // ---------- 建图 ----------
  startMapping() {
    const check = this.machine.can("START_MAPPING");
    if (!check.ok) return check;
    this.mapping = { active: true, progress: 0, done: false };
    const res = this.machine.send("START_MAPPING", { sceneReady: true });
    if (res.ok) this.log("开始空间建图：机器人启动激光扫描");
    this.emit("mapping", this.mapping);
    return res;
  }

  // ---------- 规划 ----------
  beginSelect() {
    const res = this.machine.send("BEGIN_SELECT");
    if (res.ok) this.log("进入目标点选择：请在虚拟规划视图中点击隧道地面");
    return res;
  }

  setTarget(x, z) {
    const { chainage, u } = worldToTunnel(this.scene, x, z);
    const maxU = this.scene.width_m / 2 - 1.2;
    this.target = {
      id: "P1",
      x, z: clamp(z, -maxU, maxU),
      chainage,
      u: clamp(u, -maxU, maxU),
      yaw: Math.PI / 2,
    };
    this.target.z = this.target.u;
    this.machine.setContext({ target: this.target });
    this.emit("target", this.target);
    this.log(`目标点 P1 已选：${this.formatChainage(chainage)} · 横向 ${this.target.u.toFixed(2)} m`);
    return this.target;
  }

  clearTarget() {
    this.target = null;
    this.machine.setContext({ target: null });
    this.machine.send("CLEAR_TARGET");
    this.emit("target", null);
    return null;
  }

  cancelSelect() {
    this.clearTarget();
    const res = this.machine.send("CANCEL_SELECT");
    if (res.ok) this.log("已取消目标点选择");
    return res;
  }

  generatePath() {
    const check = this.machine.can("GENERATE_PATH");
    if (!check.ok) return check;
    this.machine.send("GENERATE_PATH");
    this.log("正在生成规划路径");
    this.emit("planning", { phase: "start" });
    return { ok: true };
  }

  /** 由页面在短暂"规划中"动画后调用，真正计算并进入待确认。 */
  commitPath() {
    if (this.state !== STATES.S4) return { ok: false, reason: "当前不在规划阶段" };
    const from = { x: this.robot.x, z: this.robot.z };
    const planned = planPath(from, { x: this.target.x, z: this.target.z });
    this.path = { ...planned, confirmed: false, createdAt: Date.now() };
    this.machine.setContext({ path: this.path, pathConfirmed: false });
    const res = this.machine.send("PATH_READY");
    if (res.ok) this.log(`路径已生成：${planned.length.toFixed(1)} m，预计 ${this.eta(planned.length).toFixed(0)} s`);
    this.emit("path", this.path);
    return res;
  }

  confirmPath() {
    const res = this.machine.send("CONFIRM_PATH");
    if (res.ok) {
      this.path.confirmed = true;
      this.machine.setContext({ pathConfirmed: true });
      this.log("规划路径已确认，可开始行进");
      this.emit("path", this.path);
    }
    return res;
  }

  eta(length) { return length / TUNING.vMax + TUNING.vMax / TUNING.accel; }

  // ---------- 行进 ----------
  startNavigation() {
    const check = this.machine.can("START_NAV");
    if (!check.ok) return check;
    const res = this.machine.send("START_NAV");
    if (!res.ok) return res;
    this.nav = { active: true, s: 0, speed: 0, done: false };
    this.log("机器人开始沿规划路径自主行进");
    this.emit("nav", this.nav);
    return res;
  }

  // ---------- 采集 ----------
  startCapture() {
    const check = this.machine.can("START_CAPTURE");
    if (!check.ok) return check;
    const res = this.machine.send("START_CAPTURE");
    if (!res.ok) return res;
    this.capture = { active: true, progress: 0, done: false, frames: 0 };
    this.log("摄影测量开始：三相机与补光已启动");
    this.emit("capture", this.capture);
    return res;
  }

  beginProcess() {
    const check = this.machine.can("START_PROCESS");
    if (!check.ok) return check;
    const res = this.machine.send("START_PROCESS");
    if (res.ok) this.log("开始调用单张识别流水线生成素描成果");
    return res;
  }

  completeProcess(result) {
    this.result = result;
    const res = this.machine.send("PROCESS_DONE", { processed: true });
    if (res.ok) this.log(result?.fallback ? "识别服务不可用，已载入本机样例成果" : "素描成果已生成");
    this.emit("result", result);
    return res;
  }

  // ---------- 控制 ----------
  _arrive() {
    this.nav.active = false;
    this.nav.done = true;
    this.nav.speed = 0;
    this.nav.s = this.path.length;
    this.robot.speed = 0;
    this.robot.x = this.target.x;
    this.robot.z = this.target.z;
    this.robot.chainage = this.target.chainage;
    this.robot.u = this.target.u;
    this.machine.send("ARRIVED", { arrived: true });
    this.log("已到达测量站位，机器人停车");
    this.emit("nav", this.nav);
  }

  pause() {
    const res = this.machine.pause();
    if (res.ok) this.log("任务已暂停");
    return res;
  }

  resume() {
    const res = this.machine.resume();
    if (res.ok) this.log("任务已继续");
    return res;
  }

  reset() {
    this.robot = this.poseAtStart();
    this.target = null;
    this.path = null;
    this.mapping = { active: false, progress: 0, done: false };
    this.nav = { active: false, s: 0, speed: 0, done: false };
    this.capture = { active: false, progress: 0, done: false, frames: 0 };
    this.result = null;
    this.machine.reset();
    this.log("任务已重置：场景、路径与采集状态恢复初始");
    this.emit("reset", {});
  }

  formatChainage(chainage) {
    const prefix = this.scene.chainage_prefix || "K";
    const km = Math.floor(chainage / 1000);
    const rest = chainage - km * 1000;
    return `${prefix}${km}+${rest.toFixed(1).padStart(5, "0")}`;
  }

  // ---------- 每帧推进 ----------
  tick(dt) {
    if (this.paused) return;
    const step = Math.min(dt, 0.1);

    if (this.mapping.active) {
      this.mapping.progress = Math.min(1, this.mapping.progress + step / TUNING.mappingSeconds);
      if (this.mapping.progress >= 1) {
        this.mapping.active = false;
        this.mapping.done = true;
        this.machine.send("MAPPING_DONE");
        this.log("建图完成：环境点云地图已生成");
        this.emit("mapping", this.mapping);
      } else {
        this.emit("mapping", this.mapping);
      }
    }

    if (this.nav.active && this.path) {
      const total = this.path.length;
      let remaining = Math.max(total - this.nav.s, 0);
      if (remaining <= 0.03) {
        this._arrive();
      } else {
        const limit = Math.min(
          TUNING.vMax,
          Math.sqrt(2 * TUNING.accel * Math.max(this.nav.s, 0.05)),
          Math.sqrt(2 * TUNING.decel * remaining)
        );
        this.nav.speed += clamp(limit - this.nav.speed, -TUNING.decel * step * 1.6, TUNING.accel * step);
        this.nav.speed = clamp(this.nav.speed, 0, TUNING.vMax);
        this.nav.s = Math.min(total, this.nav.s + this.nav.speed * step);
        const t = clamp(this.nav.s / total, 0, 1);
        const point = this.path.curve.getPointAt(t);
        const tangent = this.path.curve.getTangentAt(Math.min(t, 0.999));
        this.robot.x = point.x;
        this.robot.z = point.z;
        this.robot.yaw = yawFromForward(tangent.x, tangent.z);
        this.robot.speed = this.nav.speed;
        this.robot.chainage = point.x + this.scene.start_chainage_m;
        this.robot.u = point.z;
        remaining = Math.max(total - this.nav.s, 0);
        if (remaining <= 0.03) this._arrive();
        else this.emit("nav", this.nav);
      }
    }

    if (this.capture.active) {
      this.capture.progress = Math.min(1, this.capture.progress + step / TUNING.captureSeconds);
      this.capture.frames = Math.min(3, Math.floor(this.capture.progress * 3.2));
      if (this.capture.progress >= 1) {
        this.capture.active = false;
        this.capture.done = true;
        this.capture.frames = 3;
        this.machine.send("CAPTURE_DONE", { captured: true });
        this.log("影像采集完成：3 帧原始影像 + 正射影像");
        this.emit("capture", this.capture);
      } else {
        this.emit("capture", this.capture);
      }
    }

    this.emit("tick", { dt: step });
  }

  snapshot() {
    const pct = this.path ? clamp(this.nav.s / this.path.length, 0, 1) : 0;
    return {
      state: this.state,
      meta: STATE_META[this.state],
      paused: this.paused,
      robot: { ...this.robot },
      target: this.target ? { ...this.target } : null,
      path: this.path ? { length: this.path.length, confirmed: this.path.confirmed, points: this.path.points } : null,
      mapping: { ...this.mapping },
      nav: { ...this.nav, percent: pct, remaining: this.path ? Math.max(this.path.length - this.nav.s, 0) : 0 },
      capture: { ...this.capture },
      result: this.result,
      logs: this.logs.slice(-8),
    };
  }
}
