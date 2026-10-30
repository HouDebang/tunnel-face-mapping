// 机器人任务状态机（纯 JS，不依赖 DOM / Three.js）
// 交互模式与后续自动演示模式共用同一台机器，视图只订阅状态、不自己判断流程。
export const STATES = Object.freeze({
  S0: "S0", S1: "S1", S2: "S2", S3: "S3", S4: "S4", S5: "S5",
  S6: "S6", S7: "S7", S8: "S8", S9: "S9", S10: "S10", S11: "S11",
});

export const STATE_META = Object.freeze({
  S0:  { title: "待机", step: 1, pill: "pending",   note: "等待开始建图" },
  S1:  { title: "建图中", step: 1, pill: "running", note: "机器人正在扫描隧道空间" },
  S2:  { title: "建图完成", step: 1, pill: "completed", note: "环境地图已生成，可开始路径规划" },
  S3:  { title: "目标点选择", step: 2, pill: "running", note: "在虚拟规划视图中点击选择测量站位" },
  S4:  { title: "路径规划中", step: 2, pill: "running", note: "正在生成从当前位置到目标点的路径" },
  S5:  { title: "路径待确认", step: 2, pill: "warning", note: "请确认规划路径后再开始行进" },
  S6:  { title: "行进中", step: 3, pill: "running", note: "机器人正沿规划路径自主行进" },
  S7:  { title: "已到达", step: 3, pill: "completed", note: "已到达测量站位，可开始摄影测量" },
  S8:  { title: "采集中", step: 4, pill: "running", note: "三相机与补光正在采集掌子面影像" },
  S9:  { title: "采集完成", step: 4, pill: "completed", note: "影像采集完成，可生成识别与素描成果" },
  S10: { title: "成果处理", step: 4, pill: "running", note: "正在调用单张识别流水线" },
  S11: { title: "任务完成", step: 4, pill: "completed", note: "掌子面自动化素描成果已生成" },
});

// 守卫返回 null 表示放行，返回字符串表示拦截原因（直接显示给用户）。
const TRANSITIONS = [
  { from: "S0", event: "START_MAPPING", to: "S1" },
  { from: "S1", event: "MAPPING_DONE", to: "S2" },
  { from: "S2", event: "BEGIN_SELECT", to: "S3" },
  { from: "S3", event: "TARGET_SET", to: "S3" },
  { from: "S3", event: "CLEAR_TARGET", to: "S3" },
  { from: "S3", event: "CANCEL_SELECT", to: "S2" },
  {
    from: "S3", event: "GENERATE_PATH", to: "S4",
    guard: ctx => (ctx.target ? null : "请先在虚拟规划视图中点击选择目标点"),
  },
  { from: "S4", event: "PATH_READY", to: "S5" },
  {
    from: "S5", event: "CONFIRM_PATH", to: "S5",
    guard: ctx => (ctx.path ? null : "尚未生成路径"),
  },
  {
    from: "S5", event: "START_NAV", to: "S6",
    guard: ctx => (ctx.pathConfirmed ? null : "请先确认规划路径"),
  },
  { from: "S6", event: "ARRIVED", to: "S7" },
  {
    from: "S7", event: "START_CAPTURE", to: "S8",
    guard: ctx => (ctx.arrived ? null : "机器人尚未到达目标点"),
  },
  { from: "S8", event: "CAPTURE_DONE", to: "S9" },
  {
    from: "S9", event: "START_PROCESS", to: "S10",
    guard: ctx => (ctx.captured ? null : "采集尚未完成"),
  },
  { from: "S10", event: "PROCESS_DONE", to: "S11" },
];

export const PAUSABLE = new Set(["S1", "S6", "S8"]);

export class MissionMachine {
  constructor({ context = {}, onChange = () => {} } = {}) {
    this.context = { ...context };
    this.state = STATES.S0;
    this.paused = false;
    this.previous = null;
    this.lastEvent = null;
    this.history = [];
    this.onChange = onChange;
  }

  find(event, state = this.state) {
    return TRANSITIONS.find(item => item.from === state && item.event === event) || null;
  }

  /** 仅判断能否执行，不改变状态。返回 {ok, reason}。 */
  can(event) {
    if (event === "RESET") return { ok: true, reason: null };
    if (this.paused) return { ok: false, reason: "任务已暂停，请先继续" };
    const rule = this.find(event);
    if (!rule) return { ok: false, reason: `当前阶段不支持该操作（${event}）` };
    const reason = rule.guard ? rule.guard(this.context) : null;
    return reason ? { ok: false, reason } : { ok: true, reason: null };
  }

  /** 执行一次状态转换。payload 会并入上下文。 */
  send(event, payload = {}) {
    if (event === "RESET") return this.reset();
    const check = this.can(event);
    if (!check.ok) return { ok: false, reason: check.reason, state: this.state };
    const rule = this.find(event);
    const from = this.state;
    Object.assign(this.context, payload);
    this.previous = from;
    this.state = rule.to;
    this.lastEvent = event;
    this.history.push({ from, event, to: rule.to, at: Date.now() });
    if (this.history.length > 40) this.history.shift();
    this.onChange({ from, to: rule.to, event, paused: this.paused, context: this.context });
    return { ok: true, reason: null, state: this.state, from };
  }

  setContext(patch) {
    Object.assign(this.context, patch);
  }

  pause() {
    if (!PAUSABLE.has(this.state) || this.paused) return { ok: false, reason: "当前阶段不可暂停" };
    this.paused = true;
    this.onChange({ from: this.state, to: this.state, event: "PAUSE", paused: true, context: this.context });
    return { ok: true };
  }

  resume() {
    if (!this.paused) return { ok: false, reason: "任务未暂停" };
    this.paused = false;
    this.onChange({ from: this.state, to: this.state, event: "RESUME", paused: false, context: this.context });
    return { ok: true };
  }

  reset() {
    const from = this.state;
    this.state = STATES.S0;
    this.paused = false;
    this.previous = null;
    this.lastEvent = "RESET";
    this.context = { ...this.context, target: null, path: null, pathConfirmed: false, arrived: false, captured: false, processed: false };
    this.history.push({ from, event: "RESET", to: "S0", at: Date.now() });
    this.onChange({ from, to: "S0", event: "RESET", paused: false, context: this.context });
    return { ok: true, state: "S0" };
  }

  snapshot() {
    return {
      state: this.state,
      paused: this.paused,
      meta: STATE_META[this.state],
      context: { ...this.context },
      history: this.history.slice(-12),
    };
  }
}
