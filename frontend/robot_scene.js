// 两个三维视图：虚拟规划视图（复用 TunnelRenderer）与现场执行视图（GLB + 写实隧道）。
// 视图只负责"呈现"，位姿与流程全部来自 Mission。
import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { TunnelRenderer } from "./tunnel_renderer.js";
import { profilePoints } from "./tunnel_geometry.js";

const TEAL = 0x087e83;
const TEAL_LIGHT = 0x9fc9cf;
// 三路现场视角共用一个离屏渲染缓冲；4:3 在 1.2~1.8 的面板比例下裁切最少。
const RENDER_WIDTH = 1280;
const RENDER_HEIGHT = 720;
const RENDER_ASPECT = RENDER_WIDTH / RENDER_HEIGHT;

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function outlineSegments(width, height, segments = 30) {
  const points = profilePoints(width, height, segments);
  const segmentsList = [];
  for (let i = 0; i < points.length - 1; i++) segmentsList.push([points[i], points[i + 1]]);
  return segmentsList;
}

/** 点云地图：按里程切片生成，保证 setDrawRange 可以"由洞口向里"逐段显现。 */
function buildPointCloud({ length, width, height, slices = 260, perSlice = 84, seed = 20261030 }) {
  const rand = mulberry32(seed);
  const segments = outlineSegments(width, height, 26);
  const total = slices * perSlice;
  const positions = new Float32Array(total * 3);
  const colors = new Float32Array(total * 3);
  const base = new THREE.Color(0x7fb3bf);
  let i = 0;
  for (let s = 0; s < slices; s++) {
    const x = (s / (slices - 1)) * length;
    for (let k = 0; k < perSlice; k++) {
      const seg = segments[Math.floor(rand() * segments.length)];
      const t = rand();
      const u = seg[0][0] + (seg[1][0] - seg[0][0]) * t;
      const v = seg[0][1] + (seg[1][1] - seg[0][1]) * t;
      const jitter = (rand() - 0.5) * 0.09;
      positions[i * 3] = x + (rand() - 0.5) * 0.05;
      positions[i * 3 + 1] = Math.max(0, v + jitter);
      positions[i * 3 + 2] = u + jitter;
      const shade = 0.55 + rand() * 0.45;
      colors[i * 3] = base.r * shade;
      colors[i * 3 + 1] = base.g * shade;
      colors[i * 3 + 2] = base.b * shade;
      i++;
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.setDrawRange(0, 0);
  const material = new THREE.PointsMaterial({
    size: 0.055, vertexColors: true, transparent: true, opacity: 0.92,
    sizeAttenuation: true, depthWrite: false,
  });
  return { points: new THREE.Points(geometry, material), total, slices, perSlice };
}

/** 逐段可显现的隧道表面（建图"已建区域"）。 */
function buildMappedSurface({ length, width, height, slices = 120, segments = 26 }) {
  const outline = profilePoints(width, height, segments);
  const positions = [];
  const perSlice = (outline.length - 1) * 6;
  for (let s = 0; s < slices; s++) {
    const x0 = (s / slices) * length;
    const x1 = ((s + 1) / slices) * length;
    for (let i = 0; i < outline.length - 1; i++) {
      const [u0, v0] = outline[i];
      const [u1, v1] = outline[i + 1];
      positions.push(x0, v0, u0, x1, v0, u0, x1, v1, u1);
      positions.push(x0, v0, u0, x1, v1, u1, x0, v1, u1);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.computeVertexNormals();
  geometry.setDrawRange(0, 0);
  const material = new THREE.MeshBasicMaterial({
    color: 0x9ec6cd, transparent: true, opacity: 0.17, side: THREE.DoubleSide, depthWrite: false,
  });
  return { mesh: new THREE.Mesh(geometry, material), perSlice };
}

/** 简化机器人代理（虚拟视图用，避免两个 44 万面场景同时渲染）。 */
function buildRobotProxy() {
  const group = new THREE.Group();
  const body = new THREE.MeshStandardMaterial({ color: 0x33474f, roughness: 0.55, metalness: 0.35 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x1b2428, roughness: 0.85, metalness: 0.1 });
  const accent = new THREE.MeshStandardMaterial({ color: TEAL, roughness: 0.4, metalness: 0.3, emissive: 0x063b3e, emissiveIntensity: 0.6 });
  const lens = new THREE.MeshStandardMaterial({ color: 0x0a1417, roughness: 0.25, metalness: 0.6, emissive: 0x2b7d8a, emissiveIntensity: 0.8 });

  const box = (w, h, d, x, y, z, mat) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    mesh.position.set(x, y, z);
    group.add(mesh);
    return mesh;
  };
  // 履带（局部 −Z 为前进方向）
  box(0.17, 0.22, 0.90, -0.30, 0.11, 0, dark);
  box(0.17, 0.22, 0.90, 0.30, 0.11, 0, dark);
  // 车体
  box(0.56, 0.30, 0.78, 0, 0.40, 0, body);
  box(0.60, 0.06, 0.66, 0, 0.57, 0, accent);
  // 门架
  box(0.07, 0.78, 0.07, -0.235, 0.95, 0, body);
  box(0.07, 0.78, 0.07, 0.235, 0.95, 0, body);
  box(0.62, 0.09, 0.09, 0, 1.34, -0.18, body);
  // 三相机
  for (const x of [-0.215, 0, 0.215]) {
    box(0.12, 0.12, 0.16, x, 1.29, -0.22, lens);
  }
  // 补光
  box(0.10, 0.06, 0.06, -0.30, 1.38, -0.22, accent);
  box(0.10, 0.06, 0.06, 0.30, 1.38, -0.22, accent);

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.55, 0.62, 40),
    new THREE.MeshBasicMaterial({ color: TEAL, transparent: true, opacity: 0.75, side: THREE.DoubleSide })
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.02;
  group.add(ring);

  const nose = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.22, 12), new THREE.MeshBasicMaterial({ color: TEAL }));
  nose.rotation.x = -Math.PI / 2;
  nose.position.set(0, 0.42, -0.56);
  group.add(nose);

  return group;
}

function makeNoiseTexture({ base = "#6b6f6c", variance = 26, cracks = 0, size = 256, repeat = [4, 4] } = {}) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const [r, g, b] = [1, 3, 5].map(i => parseInt(base.slice(i, i + 2), 16));
  const image = ctx.createImageData(size, size);
  const rand = mulberry32(97);
  for (let i = 0; i < size * size; i++) {
    const n = (rand() - 0.5) * variance;
    image.data[i * 4] = Math.max(0, Math.min(255, r + n));
    image.data[i * 4 + 1] = Math.max(0, Math.min(255, g + n));
    image.data[i * 4 + 2] = Math.max(0, Math.min(255, b + n));
    image.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  if (cracks) {
    ctx.strokeStyle = "rgba(20,18,16,.45)";
    for (let i = 0; i < cracks; i++) {
      ctx.lineWidth = 0.6 + rand() * 1.6;
      ctx.beginPath();
      let x = rand() * size, y = rand() * size;
      ctx.moveTo(x, y);
      for (let s = 0; s < 5; s++) {
        x += (rand() - 0.5) * 60;
        y += (rand() - 0.5) * 60;
        ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(repeat[0], repeat[1]);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

// ---------------------------------------------------------------------------
// 虚拟规划视图
// ---------------------------------------------------------------------------
export class VirtualView extends TunnelRenderer {
  constructor(container, { onPick } = {}) {
    super(container, { onSelect: () => {} });
    this.onPick = onPick;
    this.pickMode = false;
    this.overlay = new THREE.Group();
    this.markers = new THREE.Group();
    this.pathGroup = new THREE.Group();
    this.scene.add(this.overlay, this.markers, this.pathGroup);
    this.cloud = null;
    this.mapped = null;
    this.actor = null;
    this.scanRing = null;
    this.floorPicker = null;
    this.pathBase = null;
    this.pathProgress = null;
    this.captureCone = null;
    this.layers = { cloud: true, shell: true, path: true };
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x4a5a60, 1.15));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(18, 26, 16);
    this.scene.add(key);
    this._pointerDown = event => { this._start = [event.clientX, event.clientY]; };
    this._pointerUp = event => this._handlePick(event);
    this.renderer.domElement.addEventListener("pointerdown", this._pointerDown);
    this.renderer.domElement.addEventListener("pointerup", this._pointerUp);
  }

  async init(sceneData) {
    this.clearGroup(this.overlay);
    this.clearGroup(this.markers);
    this.clearGroup(this.pathGroup);
    this.cloud = null; this.mapped = null; this.pathBase = null; this.pathProgress = null; this.floorPicker = null; this.captureCone = null;
    await this.setScene(sceneData);
    const length = sceneData.end_chainage_m - sceneData.start_chainage_m;
    const { width_m: width, height_m: height } = sceneData;

    if (this.shell) this.shell.material.opacity = 0.06;

    this.cloud = buildPointCloud({ length, width, height });
    this.overlay.add(this.cloud.points);

    this.mapped = buildMappedSurface({ length, width, height });
    this.overlay.add(this.mapped.mesh);

    const ringPoints = profilePoints(width, height, 30).map(([u, v]) => new THREE.Vector3(0, v, u));
    this.scanRing = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(ringPoints),
      new THREE.LineBasicMaterial({ color: TEAL, transparent: true, opacity: 0.9 })
    );
    this.scanRing.visible = false;
    this.overlay.add(this.scanRing);

    this.actor = buildRobotProxy();
    this.scene.add(this.actor);

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(length, width * 1.2),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(length / 2, 0, 0);
    floor.userData.pickable = true;
    this.floorPicker = floor;
    this.scene.add(floor);

    this.setView("perspective");
    // 初始机位对准洞口附近（机器人起点），而不是整条 60 m 隧道的中点。
    this.camera.position.set(-7, 8.5, 13);
    this.controls.target.set(7, 2, 0);
    this.controls.update();
    this.render();
  }

  setLayers(next) {
    Object.assign(this.layers, next);
    if (this.cloud) this.cloud.points.visible = this.layers.cloud;
    if (this.mapped) this.mapped.mesh.visible = this.layers.cloud;
    if (this.shell) this.shell.visible = this.layers.shell && this.shellVisible;
    if (this.shellLines) this.shellLines.visible = this.layers.shell && this.shellVisible;
    if (this.pathGroup) this.pathGroup.visible = this.layers.path;
    this.render();
  }

  setSelectMode(on) {
    this.pickMode = Boolean(on);
    this.renderer.domElement.style.cursor = on ? "crosshair" : "";
  }

  _handlePick(event) {
    if (!this.pickMode || !this._start) return;
    if (Math.hypot(event.clientX - this._start[0], event.clientY - this._start[1]) > 5) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(pointer, this.camera);
    // 先打地面：相机常常在洞外，若先打外壳会命中近侧洞壁，落点偏到洞口。
    const hit = (this.floorPicker && raycaster.intersectObject(this.floorPicker, false)[0])
      || (this.shell && raycaster.intersectObject(this.shell, false)[0]);
    if (!hit) return;
    this.onPick?.({ x: hit.point.x, z: hit.point.z });
  }

  setRobot(pose) {
    if (!this.actor) return;
    this.actor.position.set(pose.x, 0, pose.z);
    this.actor.rotation.y = pose.yaw;
    this.render();
  }

  setMapping(progress) {
    if (!this.cloud || !this.mapped) return;
    const cloudCount = Math.floor(this.cloud.total * progress);
    this.cloud.points.geometry.setDrawRange(0, cloudCount);
    const slice = Math.floor(this.mapped.mesh.geometry.getAttribute("position").count / this.mapped.perSlice * progress);
    this.mapped.mesh.geometry.setDrawRange(0, Math.min(slice * this.mapped.perSlice, this.mapped.mesh.geometry.getAttribute("position").count));
    const length = this.sceneData ? this.sceneData.end_chainage_m - this.sceneData.start_chainage_m : 0;
    if (this.scanRing) {
      this.scanRing.visible = progress > 0.001 && progress < 0.999;
      this.scanRing.position.x = length * progress;
    }
    this.render();
  }

  setTarget(target) {
    this.clearGroup(this.markers);
    if (!target) { this.render(); return; }
    const group = new THREE.Group();
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.55, 0.78, 44),
      new THREE.MeshBasicMaterial({ color: 0xe0872f, transparent: true, opacity: 0.95, side: THREE.DoubleSide })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.03;
    group.add(ring);
    const inner = new THREE.Mesh(
      new THREE.CircleGeometry(0.28, 28),
      new THREE.MeshBasicMaterial({ color: 0xe0872f, transparent: true, opacity: 0.25, side: THREE.DoubleSide })
    );
    inner.rotation.x = -Math.PI / 2;
    inner.position.y = 0.03;
    group.add(inner);
    const pillar = new THREE.Mesh(
      new THREE.CylinderGeometry(0.035, 0.035, 2.4, 8),
      new THREE.MeshBasicMaterial({ color: 0xe0872f, transparent: true, opacity: 0.6 })
    );
    pillar.position.y = 1.2;
    group.add(pillar);
    const label = this.label("P1 测量站位", "#b4651c", 0.95);
    label.position.set(0, 2.7, 0);
    group.add(label);
    group.position.set(target.x, 0, target.z);
    this.markers.add(group);
    this.render();
  }

  setPath(path) {
    this.clearGroup(this.pathGroup);
    this.pathBase = null;
    this.pathProgress = null;
    if (!path) { this.render(); return; }
    const geometry = new THREE.TubeGeometry(path.curve, 220, 0.075, 8, false);
    const base = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
      color: TEAL_LIGHT, transparent: true, opacity: 0.45, depthWrite: false,
    }));
    const progressGeometry = new THREE.TubeGeometry(path.curve, 220, 0.085, 8, false);
    progressGeometry.setDrawRange(0, 0);
    const progress = new THREE.Mesh(progressGeometry, new THREE.MeshBasicMaterial({
      color: TEAL, transparent: true, opacity: 0.95, depthWrite: false,
    }));
    this.pathGroup.add(base, progress);

    const start = path.curve.getPointAt(0);
    const startRing = new THREE.Mesh(
      new THREE.RingGeometry(0.3, 0.42, 28),
      new THREE.MeshBasicMaterial({ color: TEAL, transparent: true, opacity: 0.8, side: THREE.DoubleSide })
    );
    startRing.rotation.x = -Math.PI / 2;
    startRing.position.set(start.x, 0.04, start.z);
    this.pathGroup.add(startRing);

    // 方向箭头
    const count = 5;
    for (let i = 1; i <= count; i++) {
      const t = i / (count + 1);
      const p = path.curve.getPointAt(t);
      const tangent = path.curve.getTangentAt(t);
      const arrow = new THREE.ArrowHelper(
        new THREE.Vector3(tangent.x, 0, tangent.z).normalize(),
        new THREE.Vector3(p.x, 0.18, p.z), 0.9, TEAL, 0.35, 0.2
      );
      this.pathGroup.add(arrow);
    }
    this.pathBase = base;
    this.pathProgress = progress;
    this.pathGroup.visible = this.layers.path;
    this.render();
  }

  setPathProgress(ratio) {
    if (!this.pathProgress) return;
    const index = this.pathProgress.geometry.getIndex();
    if (!index) return;
    const total = index.count;
    this.pathProgress.geometry.setDrawRange(0, Math.floor((total * Math.min(1, Math.max(0, ratio))) / 3) * 3);
    this.render();
  }

  /** 采集阶段：在虚拟视图中画出三台相机的视场范围（地面扇形 + 边线）。 */
  setCaptureCone(active, pose) {
    if (this.captureCone) {
      this.overlay.remove(this.captureCone);
      this.clearGroup(this.captureCone);
      this.captureCone = null;
    }
    if (!active || !pose) { this.render(); return; }
    const group = new THREE.Group();
    const yaw = pose.yaw;
    const right = [Math.cos(yaw), -Math.sin(yaw)];
    const depth = 7, half = (25 * Math.PI) / 180;
    const lines = [];
    const fan = [];
    for (const [offset, deg] of [[-0.215, 20], [0, 0], [0.215, -20]]) {
      const ax = pose.x + right[0] * offset;
      const az = pose.z + right[1] * offset;
      const base = yaw + (deg * Math.PI) / 180;
      const dirL = [-Math.sin(base + half), -Math.cos(base + half)];
      const dirR = [-Math.sin(base - half), -Math.cos(base - half)];
      const p1 = [ax + dirL[0] * depth, 0.07, az + dirL[1] * depth];
      const p2 = [ax + dirR[0] * depth, 0.07, az + dirR[1] * depth];
      lines.push([ax, 0.07, az], p1, p1, p2, p2, [ax, 0.07, az]);
      fan.push(ax, 0.07, az, p1[0], p1[1], p1[2], p2[0], p2[1], p2[2]);
    }
    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute("position", new THREE.Float32BufferAttribute(lines.flat(), 3));
    group.add(new THREE.LineSegments(lineGeometry, new THREE.LineBasicMaterial({ color: 0xe0872f, transparent: true, opacity: 0.85 })));
    const fanGeometry = new THREE.BufferGeometry();
    fanGeometry.setAttribute("position", new THREE.Float32BufferAttribute(fan, 3));
    group.add(new THREE.Mesh(fanGeometry, new THREE.MeshBasicMaterial({
      color: 0xe0872f, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false,
    })));
    this.captureCone = group;
    this.overlay.add(group);
    this.render();
  }

  dispose() {
    this.renderer.domElement.removeEventListener("pointerdown", this._pointerDown);
    this.renderer.domElement.removeEventListener("pointerup", this._pointerUp);
    super.dispose();
  }
}

// ---------------------------------------------------------------------------
// 现场执行视图
// ---------------------------------------------------------------------------
export class RealView {
  /**
   * 现场执行的三路视角共用同一套场景与同一个 WebGL 上下文：
   *   01 固定监控（观察点不动）  02 机载第一人称  03 融合第三人称（可交互）
   * 每帧渲染三次到离屏画布，再按 cover 方式拷贝到三块 2D 画布，避免三份几何体与三个 GL 上下文。
   */
  constructor(containers) {
    this.targets = {};
    for (const key of ["monitor", "fpv", "fused"]) {
      const element = containers[key];
      const canvas = document.createElement("canvas");
      canvas.className = "rb-feed";
      canvas.dataset.feed = key;
      element.append(canvas);
      this.targets[key] = { element, canvas, context: canvas.getContext("2d") };
    }
    this.container = containers.fused;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d1417);
    this.scene.fog = new THREE.Fog(0x0d1417, 16, 62);

    // 融合视角：第三人称，可拖动；行进时自动跟随。
    this.camera = new THREE.PerspectiveCamera(45, RENDER_ASPECT, 0.05, 300);
    // 监控视角：固定在隧道壁上，只随机器人位置改变画面内容。
    this.monitorCamera = new THREE.PerspectiveCamera(48, RENDER_ASPECT, 0.1, 300);
    this.monitorCamera.position.set(-1.5, 4.55, -3.15);
    this.monitorCamera.lookAt(6, 0.3, 0);
    // 机载视角：加载模型后挂到 Camera_Center 节点上。
    this.fpvCamera = new THREE.PerspectiveCamera(38, RENDER_ASPECT, 0.05, 200);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.renderer.setSize(RENDER_WIDTH, RENDER_HEIGHT, false);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.camera.layers.enable(1);
    this.monitorCamera.layers.enable(1);

    this.controls = new OrbitControls(this.camera, this.targets.fused.canvas);
    this.controls.enableDamping = false;
    this.controls.minDistance = 1.2;
    this.controls.maxDistance = 40;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.addEventListener("change", () => this.render());
    // 相机挂一盏弱补光，保证机器人停在灯带之间时画面仍然可读。
    this.cameraLight = new THREE.PointLight(0xbfd8e2, 5, 16, 2);
    this.camera.add(this.cameraLight);
    this.scene.add(this.camera);
    this.robot = null;
    this.robotPose = { x: 2, z: 0, yaw: -Math.PI / 2 };
    this.follow = false;
    this.cameras = [];
    this.frustums = null;
    this.ledMaterials = [];
    this.wheels = [];
    this.stateLight = null;
    this.observer = new ResizeObserver(() => this.resize());
    for (const target of Object.values(this.targets)) this.observer.observe(target.element);
    this.buildEnvironment();
    this.resize();
    this.camera.position.set(-3.2, 2.0, 3.4);
    this.controls.target.set(2.5, 0.9, 0);
    this.controls.update();
  }

  buildEnvironment() {
    const scene = this.scene;
    scene.add(new THREE.HemisphereLight(0xa8c2cc, 0x222c30, 1.15));
    const ambient = new THREE.AmbientLight(0xffffff, 0.45);
    scene.add(ambient);

    // 隧道从洞口外侧 8 m 开始，保证"站在洞内看向掌子面"的机位不会看到黑色虚空。
    const x0 = -8, x1 = 60, length = x1 - x0, width = 10, height = 8;
    this.length = length;
    this.x0 = x0;
    this.x1 = x1;

    // 隧道内壁
    const outline = profilePoints(width, height, 34);
    const positions = [];
    for (let i = 0; i < outline.length - 1; i++) {
      const [u0, v0] = outline[i];
      const [u1, v1] = outline[i + 1];
      positions.push(x0, v0, u0, x1, v0, u0, x1, v1, u1);
      positions.push(x0, v0, u0, x1, v1, u1, x0, v1, u1);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    const concrete = makeNoiseTexture({ base: "#8f938d", variance: 34, size: 256, repeat: [14, 3] });
    const wallMaterial = new THREE.MeshStandardMaterial({
      color: 0xa8aba4, roughness: 0.96, metalness: 0.02, map: concrete, side: THREE.DoubleSide,
    });
    scene.add(new THREE.Mesh(geometry, wallMaterial));

    // 地面
    const floorTexture = makeNoiseTexture({ base: "#4b4f4d", variance: 24, size: 256, repeat: [18, 4] });
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(length, width),
      new THREE.MeshStandardMaterial({ color: 0x7b7f7b, roughness: 0.98, map: floorTexture })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.set(x0 + length / 2, 0.005, 0);
    scene.add(floor);

    // 黄色警示条
    for (const z of [-width / 2 + 0.55, width / 2 - 0.55]) {
      const stripe = new THREE.Mesh(
        new THREE.PlaneGeometry(length, 0.22),
        new THREE.MeshStandardMaterial({ color: 0xd8b23a, roughness: 0.8, emissive: 0x4a3a08, emissiveIntensity: 0.4 })
      );
      stripe.rotation.x = -Math.PI / 2;
      stripe.position.set(x0 + length / 2, 0.02, z);
      scene.add(stripe);
    }

    // 衬砌环向肋
    const ribMaterial = new THREE.LineBasicMaterial({ color: 0x3b464a, transparent: true, opacity: 0.9 });
    for (let x = x0; x <= x1; x += 6) {
      const points = outline.map(([u, v]) => new THREE.Vector3(x, v, u));
      scene.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points), ribMaterial));
    }

    // 掌子面
    const faceTexture = makeNoiseTexture({ base: "#7d6a52", variance: 46, cracks: 26, size: 512, repeat: [1, 1] });
    const face = new THREE.Mesh(
      new THREE.PlaneGeometry(width * 0.94, height * 0.92),
      new THREE.MeshStandardMaterial({ color: 0xb59b76, roughness: 1, map: faceTexture })
    );
    face.rotation.y = -Math.PI / 2;
    face.position.set(x1 - 0.25, height * 0.46, 0);
    scene.add(face);
    this.faceMesh = face;

    // 作业灯
    for (const x of [-4, 8, 20, 32, 44, 56]) {
      const lamp = new THREE.Mesh(
        new THREE.BoxGeometry(0.5, 0.12, 0.18),
        new THREE.MeshStandardMaterial({ color: 0xfff2d0, emissive: 0xffd79a, emissiveIntensity: 2.4 })
      );
      lamp.position.set(x, height - 0.55, width / 2 - 0.35);
      scene.add(lamp);
      const light = new THREE.PointLight(0xffd9a8, 34, 34, 1.8);
      light.position.set(x, height - 1.0, width / 2 - 1.2);
      scene.add(light);
    }

    // 洞口框
    const portal = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(outline.map(([u, v]) => new THREE.Vector3(0, v, u))),
      new THREE.LineBasicMaterial({ color: 0x7d9aa4 })
    );
    scene.add(portal);
  }

  async loadRobot(url = "/robot-assets/Tracked_Photogrammetry_Robot/Tracked_Photogrammetry_Robot.glb") {
    const loader = new GLTFLoader();
    const gltf = await loader.loadAsync(url);
    const root = gltf.scene;
    root.traverse(object => {
      // 三相机在 GLB 中是带子网格的节点，不一定是 Mesh，因此先按名字收集。
      if (/^Camera_(Left|Center|Right)$/.test(object.name)) this.cameras.push(object);
      if (!object.isMesh) return;
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of materials) {
        if (!material) continue;
        if (material.emissive && (material.emissive.r + material.emissive.g + material.emissive.b) > 0.02) {
          this.ledMaterials.push(material);
          material.emissiveIntensity = Math.max(material.emissiveIntensity || 0, 0.4);
        }
      }
      if (/Drive_Wheel|Idler_Wheel|Road_Wheel|Return_Roller/.test(object.name)) this.wheels.push(object);
    });
    this.robot = root;
    this.robot.position.set(this.robotPose.x, 0, this.robotPose.z);
    this.robot.rotation.y = this.robotPose.yaw;
    this.scene.add(root);

    // 车体补光：让深色机器人在隧道里保持可辨识的轮廓。
    const glow = new THREE.PointLight(0x9fe8f5, 2.6, 5.5, 2);
    glow.position.set(0, 1.05, 0.2);
    root.add(glow);

    // 车头聚光灯随机体移动，第一人称画面才不会是黑的。
    this.headlight = new THREE.SpotLight(0xfff3d8, 85, 32, 0.62, 0.5, 1.1);
    this.headlight.position.set(0, 1.32, -0.45);
    this.headlight.target.position.set(0, 0.35, -10);
    root.add(this.headlight, this.headlight.target);

    // 机载第一人称：挂到中置相机节点上，镜头略高于遮光罩，避免拍到自己的镜筒。
    const cameraNode = root.getObjectByName("Camera_Center");
    if (cameraNode) {
      this.fpvCamera.position.set(0, 0.02, -0.62);
      cameraNode.add(this.fpvCamera);
    } else {
      this.fpvCamera.position.set(0, 1.31, -0.62);
      root.add(this.fpvCamera);
    }

    // 状态灯柱：找 Elec_StatusTower
    const tower = root.getObjectByName("Elec_StatusTower");
    if (tower) {
      this.stateLight = new THREE.PointLight(0x62d6a0, 0, 4, 2);
      this.stateLight.position.set(0, 1.5, 0);
      tower.add(this.stateLight);
    }

    this.rebuildFrustums();
    this.render();
  }

  buildFrustums() {
    const group = new THREE.Group();
    const half = Math.tan((52 * Math.PI / 180) / 2);
    const depth = 4.2;
    const positions = [];
    const fans = [];
    for (const camera of this.cameras) {
      const origin = new THREE.Vector3();
      camera.getWorldPosition(origin);
      const quaternion = camera.getWorldQuaternion(new THREE.Quaternion());
      const forward = new THREE.Vector3(0, 0, -1).applyQuaternion(quaternion);
      const right = new THREE.Vector3(1, 0, 0).applyQuaternion(quaternion);
      const up = new THREE.Vector3(0, 1, 0).applyQuaternion(quaternion);
      const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) =>
        origin.clone()
          .addScaledVector(forward, depth)
          .addScaledVector(right, half * depth * sx)
          .addScaledVector(up, half * depth * sy)
      );
      for (const corner of corners) {
        positions.push(origin.x, origin.y, origin.z, corner.x, corner.y, corner.z);
      }
      for (let i = 0; i < 4; i++) {
        const a = corners[i], b = corners[(i + 1) % 4];
        positions.push(a.x, a.y, a.z, b.x, b.y, b.z);
      }
      // 地面投影扇形：从侧后方也能清楚看到三台相机照向哪里。
      const flat = new THREE.Vector3(forward.x, 0, forward.z);
      if (flat.lengthSq() < 1e-6) flat.set(1, 0, 0);
      flat.normalize();
      const flatRight = new THREE.Vector3(flat.z, 0, -flat.x);
      const apex = new THREE.Vector3(origin.x, 0.07, origin.z);
      const left = apex.clone().addScaledVector(flat, depth * 1.1).addScaledVector(flatRight, -half * depth * 1.05);
      const rightCorner = apex.clone().addScaledVector(flat, depth * 1.1).addScaledVector(flatRight, half * depth * 1.05);
      fans.push(apex.x, apex.y, apex.z, left.x, left.y, left.z, rightCorner.x, rightCorner.y, rightCorner.z);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    group.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: 0x9fe8f5, transparent: true, opacity: 0.4 })));

    const fanGeometry = new THREE.BufferGeometry();
    fanGeometry.setAttribute("position", new THREE.Float32BufferAttribute(fans, 3));
    group.add(new THREE.Mesh(fanGeometry, new THREE.MeshBasicMaterial({
      color: 0x7fd4e0, transparent: true, opacity: 0.14, side: THREE.DoubleSide, depthWrite: false,
    })));
    return group;
  }

  setRobot(pose, { follow = null } = {}) {
    this.robotPose = pose;
    if (this.robot) {
      this.robot.position.set(pose.x, 0, pose.z);
      this.robot.rotation.y = pose.yaw;
    }
    if (follow !== null) this.follow = follow;
  }

  setWheelSpin(distance, dt) {
    const radius = 0.07;
    const delta = distance / radius;
    for (const wheel of this.wheels) wheel.rotation.x -= delta;
  }

  setCapture(active) {
    if (active) this.rebuildFrustums();
    if (this.frustums) this.frustums.visible = active;
    for (const material of this.ledMaterials) {
      material.emissiveIntensity = active ? 3.2 : 0.35;
    }
    if (this.stateLight) this.stateLight.intensity = active ? 6 : 0;
    if (this.faceMesh) {
      this.faceMesh.material.emissive = new THREE.Color(0x6b4318);
      this.faceMesh.material.emissiveIntensity = active ? 0.55 : 0;
      this.faceMesh.material.needsUpdate = true;
    }
    if (active) this.frameCapture(); else this.follow = false;
    this.render();
  }

  /** 采集时切到侧后方机位，让三相机视锥与补光进入画面。 */
  frameCapture() {
    if (!this.robot) return;
    const base = new THREE.Vector3(this.robotPose.x, 0, this.robotPose.z);
    // 机位固定在隧道内（横向夹紧），避免相机跑到衬砌外侧变成全黑。
    const z = Math.max(-3.2, Math.min(3.2, base.z - 2.2));
    this.follow = false;
    this.camera.position.set(base.x - 3.8, 2.15, z);
    this.controls.target.set(base.x + 2.6, 1.05, base.z);
    this.controls.enabled = true;
    this.controls.update();
  }

  rebuildFrustums() {
    if (this.frustums) {
      this.scene.remove(this.frustums);
      this.frustums.traverse(object => {
        object.geometry?.dispose();
        object.material?.dispose();
      });
      this.frustums = null;
    }
    this.frustums = this.buildFrustums();
    this.frustums.visible = false;
    // 视锥只在监控与融合视角里出现；机载相机自己不该看到"自己的视场框"。
    this.frustums.traverse(object => object.layers.set(1));
    this.scene.add(this.frustums);
  }

  setStatus(kind) {
    if (!this.stateLight) return;
    const colors = { idle: 0x62d6a0, mapping: 0x4fc3d9, moving: 0x62d6a0, capture: 0x4fc3d9, fault: 0xd9534f };
    this.stateLight.color.setHex(colors[kind] || 0x62d6a0);
    this.stateLight.intensity = kind === "idle" ? 0.6 : 3.2;
  }

  reset() {
    this.robotPose = { x: 2, z: 0, yaw: -Math.PI / 2 };
    if (this.robot) {
      this.robot.position.set(this.robotPose.x, 0, this.robotPose.z);
      this.robot.rotation.y = this.robotPose.yaw;
    }
    this.follow = false;
    this.setCapture(false);
    this.setStatus("idle");
    this.camera.position.set(-3.2, 2.0, 3.4);
    this.controls.target.set(2.5, 0.9, 0);
    this.controls.update();
    this.render();
  }

  updateCamera() {
    if (!this.follow || !this.robot) return;
    const yaw = this.robotPose.yaw;
    const back = new THREE.Vector3(0, 0, 1).applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
    const forward = new THREE.Vector3(0, 0, -1).applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
    const base = new THREE.Vector3(this.robotPose.x, 0, this.robotPose.z);
    const desired = base.clone().addScaledVector(back, 4.6).add(new THREE.Vector3(0, 2.1, 0));
    this.camera.position.lerp(desired, 0.09);
    const target = base.clone().addScaledVector(forward, 2.0).add(new THREE.Vector3(0, 1.0, 0));
    this.controls.target.lerp(target, 0.12);
    this.camera.lookAt(this.controls.target);
    this.controls.update();
  }

  render() {
    if (!this.renderer) return;
    this.drawFeed("monitor", this.monitorCamera);
    this.drawFeed("fpv", this.fpvCamera);
    this.drawFeed("fused", this.camera);
  }

  /** 渲染一次并拷贝到目标 2D 画布。
   *  主画面与画中画比例不同：在共享缓冲里按目标比例取子视口，避免拉伸或裁掉视野。 */
  drawFeed(key, camera) {
    const target = this.targets[key];
    if (!target || !camera) return;
    const { canvas, context } = target;
    const cw = canvas.width, ch = canvas.height;
    if (!cw || !ch) return;

    const source = this.renderer.domElement;
    const bw = source.width, bh = source.height;
    const targetAspect = cw / ch;
    const bufferAspect = bw / bh;

    let vw = bw, vh = bh;
    if (Math.abs(targetAspect - bufferAspect) > 0.02) {
      if (targetAspect < bufferAspect) { vw = Math.round(bh * targetAspect); vh = bh; }
      else { vw = bw; vh = Math.round(bw / targetAspect); }
      vw = Math.min(vw, bw);
      vh = Math.min(vh, bh);
    }

    camera.aspect = targetAspect;
    camera.updateProjectionMatrix();

    const scissored = vw !== bw || vh !== bh;
    this.renderer.setViewport(0, 0, vw, vh);
    this.renderer.setScissorTest(scissored);
    this.renderer.setScissor(0, 0, vw, vh);
    this.renderer.render(this.scene, camera);
    // WebGL 视口原点在左下，drawImage 原点在左上。
    context.drawImage(source, 0, bh - vh, vw, vh, 0, 0, cw, ch);
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const mainKey = this.activeKey || "fused";
    const mainElement = this.targets[mainKey]?.element;
    const mw = Math.max(1, mainElement?.clientWidth || 800);
    const mh = Math.max(1, mainElement?.clientHeight || 450);
    const bw = Math.min(1600, Math.max(480, Math.round(mw * dpr)));
    const bh = Math.min(1200, Math.max(270, Math.round(mh * dpr)));
    this.renderer.setSize(bw, bh, false);
    for (const target of Object.values(this.targets)) {
      const width = Math.max(1, target.element.clientWidth);
      const height = Math.max(1, target.element.clientHeight);
      target.canvas.width = Math.round(width * dpr);
      target.canvas.height = Math.round(height * dpr);
    }
    this.render();
  }

  dispose() {
    this.observer.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    for (const target of Object.values(this.targets)) target.canvas.remove();
  }
}
