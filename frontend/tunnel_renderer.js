import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { sectionPosition, profilePoints, formatChainage, niceStep } from "./tunnel_geometry.js";
import { GeologyLayer } from "./geology_renderer.js";

// All coordinates below describe an illustrative display, not a survey model.
// World +X increases chainage; +Y is up; +Z is right looking toward +X.
// Image planes face the viewer approaching from lower chainage.
export class TunnelRenderer {
  constructor(container, { onSelect = () => {} } = {}) {
    this.container = container;
    this.onSelect = onSelect;
    this.sceneData = null;
    this.selectedId = null;
    this.layer = "sketch";
    this.view = "perspective";
    this.shellVisible = true;
    this.shellOpacity = .12;
    this.sectionOpacity = 1;
    this.generation = 0;
    this.textureCache = new Map();
    this.sectionMeshes = new Map();
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf3f7fa);
    this.camera = new THREE.PerspectiveCamera(42, 1, .05, 10000);
    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    } catch (error) {
      throw new Error(`无法启动三维显示，请使用支持 WebGL2 的浏览器。${error.message}`);
    }
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.domElement.setAttribute("aria-label", "示意隧道与按里程排列的掌子面三维视图");
    this.renderer.domElement.setAttribute("role", "img");
    this.renderer.domElement.dataset.testid = "tunnel-canvas";
    this.renderer.domElement.style.cssText = "display:block;width:100%;height:100%;touch-action:none";
    container.append(this.renderer.domElement);
    this.status = document.createElement("div");
    this.status.className = "tunnel-renderer-status";
    this.status.setAttribute("role", "status");
    this.status.hidden = true;
    container.append(this.status);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.minDistance = .5;
    this.controls.addEventListener("change", () => this.render());
    this.environment = new THREE.Group();
    this.sections = new THREE.Group();
    this.scene.add(this.environment, this.sections);
    this.geology = new GeologyLayer(this);
    this.raycaster = new THREE.Raycaster();
    this.pointerDown = event => { this.pointerStart = [event.clientX, event.clientY]; };
    this.pointerUp = event => this.pick(event);
    this.renderer.domElement.addEventListener("pointerdown", this.pointerDown);
    this.renderer.domElement.addEventListener("pointerup", this.pointerUp);
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(container);
    this.camera.position.set(-30, 25, 45);
    this.controls.target.set(20, 3, 0);
    this.controls.update();
    this.resize();
  }

  clearGroup(group) {
    group.traverse(object => {
      object.geometry?.dispose();
      const materials = object.material ? (Array.isArray(object.material) ? object.material : [object.material]) : [];
      for (const material of materials) {
        if (object.userData.labelTexture) material.map?.dispose();
        material.dispose();
      }
    });
    group.clear();
  }

  line(points, color = 0x77939e, opacity = .5, closed = false) {
    const geometry = new THREE.BufferGeometry().setFromPoints(points.map(p => new THREE.Vector3(...p)));
    const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
    return closed ? new THREE.LineLoop(geometry, material) : new THREE.Line(geometry, material);
  }

  label(text, color = "#395761", scale = 1) {
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 112;
    const context = canvas.getContext("2d");
    context.fillStyle = "rgba(255,255,255,.94)";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = color;
    context.font = '500 36px "Microsoft YaHei", sans-serif';
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(text, canvas.width / 2, canvas.height / 2, canvas.width - 28);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false });
    const sprite = new THREE.Sprite(material);
    sprite.scale.set(6.4 * scale, 1.12 * scale, 1);
    sprite.userData.labelTexture = true;
    sprite.renderOrder = 20;
    return sprite;
  }

  buildTunnel(data) {
    const length = data.end_chainage_m - data.start_chainage_m;
    const width = data.width_m, height = data.height_m;
    const gridSize = Math.max(length * 1.2, width * 4, 10);
    const grid = new THREE.GridHelper(gridSize, 30, 0xd1dce2, 0xe0e8ed);
    grid.position.set(length / 2, -.08, 0);
    grid.material.transparent = true;
    grid.material.opacity = .65;
    this.environment.add(grid);
    const outline = profilePoints(width, height);
    const vertices = [];
    for (let i = 0; i < outline.length - 1; i++) {
      const [u0, v0] = outline[i], [u1, v1] = outline[i + 1];
      vertices.push(0,v0,u0, length,v0,u0, length,v1,u1, 0,v0,u0, length,v1,u1, 0,v1,u1);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
    geometry.computeVertexNormals();
    this.shell = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x78a7b4,
      transparent: true, opacity: this.shellOpacity, side: THREE.DoubleSide, depthWrite: false }));
    this.environment.add(this.shell);
    this.shellLines = new THREE.Group();
    const step = niceStep(length);
    const ticks = [0];
    for (let value = step; value < length - step * .01; value += step) ticks.push(value);
    ticks.push(length);
    for (const x of ticks) {
      this.shellLines.add(this.line(outline.map(([u,v]) => [x,v,u]), 0x4f8997, .38));
      const label = this.label(formatChainage(data.start_chainage_m + x, data.chainage_prefix), "#5e7881", .72);
      label.position.set(x, -.6, -width * .69);
      this.environment.add(label);
    }
    for (const index of [0, 1, 2, 10, 18, 26, 34]) {
      if (outline[index]) {
        const [u,v] = outline[index];
        this.shellLines.add(this.line([[0,v,u],[length,v,u]], 0x6a98a5, .3));
      }
    }
    this.environment.add(this.shellLines);
    this.environment.add(this.line([[0,.035,0],[length,.035,0]], 0x087e83, .75));
    const arrow = new THREE.ArrowHelper(new THREE.Vector3(1,0,0),
      new THREE.Vector3(length * .74, .07, width * .69), Math.max(.8, Math.min(length * .2, 6)), 0x087e83, .7, .35);
    this.environment.add(arrow);
    const direction = this.label("里程增大 →", "#087e83", .8);
    direction.position.set(length * .8, .6, width * .85);
    this.environment.add(direction);
    this.shell.visible = this.shellVisible;
    this.shellLines.visible = this.shellVisible;
  }

  async setScene(data) {
    const version = ++this.generation;
    const changedScene = this.sceneData?.id !== data?.id;
    this.sceneData = data;
    this.clearGroup(this.environment);
    this.clearGroup(this.sections);
    this.sectionMeshes.clear();
    this.status.hidden = true;
    if (changedScene) {
      for (const promise of this.textureCache.values()) promise.then(texture => texture.dispose()).catch(() => {});
      this.textureCache.clear();
    }
    if (!data) { this.geology.update(null); this.render(); return; }
    this.buildTunnel(data);
    const count = new Map();
    for (const section of data.sections) {
      const group = new THREE.Group();
      group.position.set(...sectionPosition(data, section));
      group.rotation.y = -Math.PI / 2;
      group.visible = section.visible;
      const imageGroup = new THREE.Group();
      imageGroup.rotation.z = section.rotation_deg * Math.PI / 180;
      const geometry = new THREE.PlaneGeometry(section.display_width_m, section.display_height_m);
      const material = new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide,
        transparent: true, opacity: this.sectionOpacity, depthWrite: this.sectionOpacity > .95 });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.scale.x = section.flip_horizontal ? -1 : 1;
      mesh.userData.sectionId = section.id;
      imageGroup.add(mesh);
      const frame = this.line([
        [-section.display_width_m / 2,-section.display_height_m / 2,.012],
        [section.display_width_m / 2,-section.display_height_m / 2,.012],
        [section.display_width_m / 2,section.display_height_m / 2,.012],
        [-section.display_width_m / 2,section.display_height_m / 2,.012],
      ], section.id === this.selectedId ? 0x087e83 : 0x81969d, 1, true);
      imageGroup.add(frame);
      group.add(imageGroup);
      this.sections.add(group);
      const stack = count.get(section.chainage_m) || 0;
      count.set(section.chainage_m, stack + 1);
      const label = this.label(`${formatChainage(section.chainage_m, data.chainage_prefix)}${section.demo_copy ? " · 示意" : ""}`,
        section.id === this.selectedId ? "#087e83" : "#395761");
      label.position.set(group.position.x, data.height_m + 1 + stack * 1.25, section.offset_u_m);
      label.visible = section.visible;
      this.sections.add(label);
      this.sectionMeshes.set(section.id, {mesh, frame, group, label, section});
    }
    this.geology.update(data);
    if (changedScene) this.setView("perspective");
    this.render();
    await this.applyTextures(version);
  }

  async loadTexture(url) {
    if (!this.textureCache.has(url)) {
      const promise = new THREE.TextureLoader().loadAsync(url).then(texture => {
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.anisotropy = Math.min(4, this.renderer.capabilities.getMaxAnisotropy());
        return texture;
      });
      this.textureCache.set(url, promise);
      promise.catch(() => this.textureCache.delete(url));
    }
    return this.textureCache.get(url);
  }

  async applyTextures(version = this.generation) {
    const failures = [];
    const originals = [];
    await Promise.all([...this.sectionMeshes.values()].map(async entry => {
      const assets = entry.section.source.assets;
      const asset = assets[this.layer] || assets.original;
      if (this.layer === "sketch" && !assets.sketch && assets.original) originals.push(entry.section.label || entry.section.id);
      if (!asset?.url) { failures.push(entry.section.label || entry.section.id); return; }
      try {
        const texture = await this.loadTexture(asset.url);
        if (version !== this.generation || this.disposed) return;
        entry.mesh.material.map = texture;
        entry.mesh.material.needsUpdate = true;
      } catch { failures.push(entry.section.label || entry.section.id); }
    }));
    if (version !== this.generation || this.disposed) return;
    this.status.hidden = failures.length === 0 && originals.length === 0;
    if (failures.length) this.status.textContent = `断面图片读取失败：${failures.join("、")}。请检查来源或重新加入断面。`;
    else if (originals.length) this.status.textContent = `以下断面仅绑定原始照片，当前按照片展示：${originals.join("、")}。`;
    this.render();
    if (failures.length) throw new Error(this.status.textContent);
  }

  selectSection(id) {
    this.selectedId = id;
    for (const [sectionId, entry] of this.sectionMeshes) {
      entry.frame.material.color.setHex(sectionId === id ? 0x009a9f : 0x81969d);
      entry.frame.material.opacity = sectionId === id ? 1 : .65;
    }
    this.geology.updateVisibility(this.sceneData, this.view, id);
    if (this.view === "front") this.setView("front");
    else this.render();
  }

  setLayer(layer) {
    this.layer = layer;
    return this.applyTextures(++this.generation);
  }
  setShellVisible(value) { this.shellVisible = Boolean(value); if (this.shell) this.shell.visible = this.shellVisible; if (this.shellLines) this.shellLines.visible = this.shellVisible; this.render(); }
  setShellOpacity(value) { this.shellOpacity = Math.max(0, Math.min(1, Number(value))); if (this.shell) this.shell.material.opacity = this.shellOpacity; this.render(); }
  setSectionOpacity(value) {
    this.sectionOpacity = Math.max(0, Math.min(1, Number(value)));
    for (const entry of this.sectionMeshes.values()) {
      entry.mesh.material.opacity = this.sectionOpacity;
      entry.mesh.material.depthWrite = this.sectionOpacity > .95;
      entry.mesh.material.needsUpdate = true;
    }
    this.render();
  }

  setView(view) {
    if (!this.sceneData) return;
    this.view = view;
    const data = this.sceneData;
    const length = data.end_chainage_m - data.start_chainage_m;
    const selected = this.sectionMeshes.get(this.selectedId);
    for (const entry of this.sectionMeshes.values()) {
      // Inspect the selected face without nearer image planes covering it.
      const visible = entry.section.visible && (view !== "front" || !selected || entry === selected);
      entry.group.visible = visible; entry.label.visible = visible;
    }
    this.geology.updateVisibility(data, view, this.selectedId);
    const center = view === "front" && selected ? selected.group.position.clone() : new THREE.Vector3(length / 2, data.height_m * .35, 0);
    const span = Math.max(length * .55, data.width_m * 1.5, data.height_m * 1.6);
    this.camera.far = Math.max(2000, span * 30);
    this.camera.updateProjectionMatrix();
    this.controls.maxDistance = span * 15;
    this.controls.target.copy(center);
    if (view === "front") this.camera.position.copy(center).add(new THREE.Vector3(-Math.max(data.width_m, data.height_m) * 2, 0, 0));
    else if (view === "side") this.camera.position.copy(center).add(new THREE.Vector3(0, span * .15, span * 2.3));
    else if (view === "top") this.camera.position.copy(center).add(new THREE.Vector3(0, span * 2.3, span * .015));
    else this.camera.position.copy(center).add(new THREE.Vector3(-span * .95, span * .62, span * 1.15));
    this.camera.lookAt(center);
    this.controls.update();
    this.render();
  }

  pick(event) {
    if (!this.pointerStart || Math.hypot(event.clientX - this.pointerStart[0], event.clientY - this.pointerStart[1]) > 5) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const pointer = new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    this.raycaster.setFromCamera(pointer, this.camera);
    const meshes = [...this.sectionMeshes.values()].filter(entry => entry.group.visible && this.sectionOpacity > .02).map(entry => entry.mesh);
    const hit = this.raycaster.intersectObjects(meshes, false)[0];
    if (hit) { this.selectSection(hit.object.userData.sectionId); this.onSelect(hit.object.userData.sectionId); }
  }

  resize() {
    if (this.disposed) return;
    const width = Math.max(1, this.container.clientWidth), height = Math.max(1, this.container.clientHeight);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.render();
  }
  render() { if (!this.disposed) this.renderer.render(this.scene, this.camera); }
  downloadScreenshot() {
    this.render();
    const canvas = document.createElement("canvas"), source = this.renderer.domElement;
    canvas.width = source.width; canvas.height = source.height;
    const context = canvas.getContext("2d");
    context.drawImage(source, 0, 0);
    const size = Math.max(14, Math.round(canvas.width / 65));
    context.fillStyle = "rgba(255,255,255,.94)";
    const hasGeology = this.sceneData?.geology?.connections?.length > 0;
    context.fillRect(0, canvas.height - size * (hasGeology ? 5 : 3.6), canvas.width, size * (hasGeology ? 5 : 3.6));
    context.fillStyle = "#395761";
    context.font = `${size}px "Microsoft YaHei", sans-serif`;
    context.fillText("流程示意 · 非测量模型 · 断面显示尺寸不构成图像标定", size, canvas.height - size * 2, canvas.width - size * 2);
    const containsDemo = this.sceneData?.sections.some(section => section.demo_copy);
    context.fillText(containsDemo ? "含同源重复示意断面，不能据此推断地质连续性。" : "直线隧道 · 按输入里程摆放 · 朝向与投影尚未校核", size, canvas.height - size * .65, canvas.width - size * 2);
    if (hasGeology) context.fillText("橙：候选裂隙连接 / 蓝绿：候选区域边界连接 · 跨断面对应与延伸面均未验证", size, canvas.height - size * 3.4, canvas.width - size * 2);
    canvas.toBlob(blob => {
      if (!blob) return;
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = "tunnel-illustrative-view.png"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }, "image/png");
  }
  dispose() {
    this.disposed = true;
    this.generation++;
    this.observer.disconnect();
    this.controls.dispose();
    this.renderer.domElement.removeEventListener("pointerdown", this.pointerDown);
    this.renderer.domElement.removeEventListener("pointerup", this.pointerUp);
    this.clearGroup(this.environment); this.clearGroup(this.sections);
    this.geology.dispose();
    for (const promise of this.textureCache.values()) promise.then(texture => texture.dispose()).catch(() => {});
    this.renderer.dispose(); this.renderer.domElement.remove(); this.status.remove();
  }
}
