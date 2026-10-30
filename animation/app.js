(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, p) => a + (b - a) * p;
  const inv = (a, b, v) => clamp((v - a) / (b - a));
  const smooth = (a, b, v) => {
    const p = inv(a, b, v);
    return p * p * (3 - 2 * p);
  };
  const easeOut = (p) => 1 - Math.pow(1 - p, 3);
  const easeInOut = (p) => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
  const pulse = (t, hz = 1, phase = 0) => .5 + .5 * Math.sin((t * hz + phase) * Math.PI * 2);
  const fmt = (n) => Math.round(n).toLocaleString("en-US");
  const setOp = (el, v) => { if (el) el.style.opacity = String(clamp(v)); };
  const setT = (el, v) => { if (el) el.style.transform = v; };

  function svg(tag, attrs = {}) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
    return el;
  }

  // deterministic PRNG so every frame is reproducible
  let seed = 0x2f6e2b1;
  const rnd = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const rr = (a, b) => a + (b - a) * rnd();

  const G = window.GEOMETRY || { regions: [], traces: [], trace_total: 3035, region_total: 10 };

  // ============================================================
  // DOM
  // ============================================================
  const liveStage = $("liveStage");
  const faceStage = $("faceStage");
  const analysisStage = $("analysisStage");
  const finalStage = $("finalStage");
  const hud = $("hud");
  const tunnelRibs = $("tunnelRibs");
  const wallSeams = $("wallSeams");
  const floorLines = $("floorLines");
  const tunnelLights = $("tunnelLights");
  const dustLayer = $("dustLayer");
  const foregroundDust = $("foregroundDust");
  const portal = $("portal");
  const groundFx = $("groundFx");
  const robotAura = $("robotAura");
  const robotWrap = $("robotWrap");
  const robotUse = $("robotUse");
  const facePhoto = $("facePhoto");
  const faceRobot = $("faceRobot");
  const cameraHud = $("cameraHud");
  const cameraCloseup = $("cameraCloseup");
  const captureStrip = $("captureStrip");
  const shutterFlash = $("shutterFlash");
  const faceLabels = $("faceLabels");
  const photoWindow = $("photoWindow");
  const orthoImg = $("orthoImg");
  const meshOverlay = $("meshOverlay");
  const viewCount = $("viewCount");
  const mapWindow = $("mapWindow");
  const mapSvg = $("mapSvg");
  const mapCloud = $("mapCloud");
  const mapGrid = $("mapGrid");
  const mapPath = $("mapPath");
  const mapTarget = $("mapTarget");
  const mapRobot = $("mapRobot");
  const mapScan = $("mapScan");
  const mapLabels = $("mapLabels");
  const cloudCount = $("cloudCount");
  const mapPct = $("mapPct");
  const mapStatus = $("mapStatus");
  const titleCard = $("titleCard");
  const chapterTag = $("chapterTag");
  const chapterNum = $("chapterNum");
  const chapterTitle = $("chapterTitle");
  const chapterEn = $("chapterEn");
  const telemetry = $("telemetry");
  const telMode = $("telMode");
  const telSpeed = $("telSpeed");
  const telDist = $("telDist");
  const subtitle = $("subtitle");
  const subText = $("subText");
  const bottomBar = $("bottomBar");
  const progressFill = $("progressFill");
  const progressHead = $("progressHead");
  const timecode = $("timecode");
  const arrivalToast = $("arrivalToast");
  const cutFlash = $("cutFlash");
  const fadeBlack = $("fadeBlack");
  const anaViewer = $("anaViewer");
  const viewerCanvas = $("viewerCanvas");
  const anaFace = $("anaFace");
  const anaSketch = $("anaSketch");
  const anaRegions = $("anaRegions");
  const anaTraces = $("anaTraces");
  const anaScan = $("anaScan");
  const viewerBadge = $("viewerBadge");
  const processPanel = $("processPanel");
  const processBar = $("processBar");
  const processPct = $("processPct");
  const tablePanel = $("tablePanel");
  const regionLegend = $("regionLegend");
  const regionCountLabel = $("regionCountLabel");
  const finalCopy = document.querySelector(".final-copy");
  const finalRobot = document.querySelector(".final-robot");
  const finalCards = [...document.querySelectorAll(".final-card")];
  const tableRows = [...document.querySelectorAll(".result-table tr")];
  const processLogs = [...document.querySelectorAll(".log-line")];

  // ============================================================
  // TIMELINE
  // ============================================================
  const SUBS = [
    [4.7, 9.6, "设备自主启动，实时构建隧道三维地图。"],
    [10.4, 13.7, "根据地图规划路径，锁定掌子面测量点。"],
    [14.3, 22.9, "履带式移动平台自主行进，精准抵达目标位置。"],
    [23.6, 26.7, "测量位置已到达，摄影测量装置开始工作。"],
    [27.8, 32.9, "多视角采集掌子面影像与几何信息。"],
    [33.5, 39.3, "现场数据重建为正射影像与三维纹理模型。"],
    [40.5, 46.9, "自动提取岩性分区与结构面迹线。"],
    [47.6, 52.7, "生成标准化掌子面素描图与成果表。"],
    [53.5, 58.9, "从自主采集到智能素描，形成完整闭环。"],
  ];

  const CHAPTERS = [
    [4.7, 10.0, "01", "开机与自主建图", "AUTONOMOUS MAPPING"],
    [10.0, 27.0, "02", "路径规划与自主行进", "PATH PLANNING & NAVIGATION"],
    [27.0, 40.0, "03", "摄影测量与三维重建", "PHOTOGRAMMETRY & RECONSTRUCTION"],
    [40.0, 53.0, "04", "自动化掌子面素描", "AUTOMATED FACE SKETCH"],
  ];

  // ============================================================
  // TUNNEL
  // ============================================================
  const RIB_N = 36;
  const ribs = [];
  let interiorPath, mouthPath, mouthHalo;

  function ribGeom(z, camX = 0, camY = 0) {
    const w = 300 + 2200 * Math.pow(z, 1.55);
    const h = 240 + 1420 * Math.pow(z, 1.55);
    const cx = 960 + camX * (1 - z);
    const cy = 475 + 55 * (1 - z) + camY;
    const floorY = cy + h * .38;
    return { z, w, h, cx, cy, floorY, top: cy - h };
  }

  function archD(g, close = false) {
    const xL = g.cx - g.w / 2, xR = g.cx + g.w / 2;
    let d = `M${xL.toFixed(1)} ${g.floorY.toFixed(1)} L${xL.toFixed(1)} ${g.cy.toFixed(1)} `;
    d += `C${xL.toFixed(1)} ${(g.cy - g.h * .56).toFixed(1)}, ${(g.cx - g.w * .29).toFixed(1)} ${g.top.toFixed(1)}, ${g.cx.toFixed(1)} ${g.top.toFixed(1)} `;
    d += `C${(g.cx + g.w * .29).toFixed(1)} ${g.top.toFixed(1)}, ${xR.toFixed(1)} ${(g.cy - g.h * .56).toFixed(1)}, ${xR.toFixed(1)} ${g.cy.toFixed(1)} `;
    d += `L${xR.toFixed(1)} ${g.floorY.toFixed(1)}`;
    if (close) d += " Z";
    return d;
  }

  function initTunnel() {
    interiorPath = svg("path", { fill: "url(#wallGrad)", opacity: ".94" });
    mouthPath = svg("path", { fill: "#020608", stroke: "rgba(119,169,177,.18)", "stroke-width": "2" });
    mouthHalo = svg("path", { fill: "none", stroke: "rgba(111,221,224,.18)", "stroke-width": "10", filter: "url(#blur14)" });
    tunnelRibs.appendChild(interiorPath);
    tunnelRibs.appendChild(mouthHalo);
    tunnelRibs.appendChild(mouthPath);

    for (let i = 0; i < RIB_N; i++) {
      const p = svg("path", { fill: "none", stroke: "#7797a0", "stroke-linecap": "round" });
      tunnelRibs.appendChild(p);
      ribs.push(p);
    }

    for (let i = 0; i < 11; i++) {
      wallSeams.appendChild(svg("path", { fill: "none", stroke: "rgba(137,181,190,.18)", "stroke-width": "1" }));
    }

    for (let i = 0; i < 18; i++) {
      floorLines.appendChild(svg("path", { fill: "none", stroke: "rgba(122,160,168,.16)", "stroke-width": "1" }));
    }
    for (let i = 0; i < 8; i++) {
      floorLines.appendChild(svg("path", { fill: "none", stroke: "rgba(106,147,155,.10)", "stroke-width": "1" }));
    }

    for (let i = 0; i < 8; i++) {
      const g = svg("g");
      g.appendChild(svg("ellipse", { fill: "url(#amberGlow)", opacity: ".2" }));
      g.appendChild(svg("ellipse", { fill: "url(#amberGlow)", opacity: ".10" }));
      g.appendChild(svg("rect", { fill: "#e9fbff", opacity: ".9", rx: "2" }));
      g.appendChild(svg("rect", { fill: "#fff4d9", opacity: ".65", rx: "1" }));
      tunnelLights.appendChild(g);
      g._glow = g.children[0];
      g._pool = g.children[1];
      g._bar = g.children[2];
      g._core = g.children[3];
    }

    // portal frame
    portal.innerHTML = `
      <defs>
        <radialGradient id="portalLight" cx="50%" cy="18%" r="58%">
          <stop offset="0%" stop-color="#d9f4f6" stop-opacity=".26"/>
          <stop offset="50%" stop-color="#86b3bd" stop-opacity=".06"/>
          <stop offset="100%" stop-color="#000" stop-opacity="0"/>
        </radialGradient>
      </defs>
      <rect width="1920" height="1080" fill="url(#portalLight)"/>
      <path d="M-150 1080 L-150 228 Q-150 -96 960 -96 Q2070 -96 2070 228 L2070 1080" fill="none" stroke="rgba(205,240,245,.28)" stroke-width="3"/>
      <path d="M-78 1080 L-78 238 Q-78 -24 960 -24 Q1998 -24 1998 238 L1998 1080" fill="none" stroke="rgba(178,221,229,.14)" stroke-width="1.5"/>
      <path d="M-150 228 Q-150 -96 960 -96 Q2070 -96 2070 228" fill="none" stroke="rgba(226,252,255,.20)" stroke-width="10" filter="url(#blur14)"/>
    `;

    // ground FX
    groundFx.innerHTML = `
      <path id="navLine" d="" fill="none" stroke="rgba(102,239,235,.9)" stroke-width="3" stroke-dasharray="14 16" stroke-linecap="round" opacity="0"/>
      <g id="navChevrons" opacity="0" fill="none" stroke="rgba(102,239,235,.72)" stroke-width="2">
        <path d="M936 850 L960 828 L984 850"/><path d="M936 815 L960 793 L984 815"/><path d="M936 780 L960 758 L984 780"/>
      </g>
      <g id="targetG" opacity="0">
        <ellipse cx="0" cy="0" rx="72" ry="22" fill="none" stroke="#ffb45c" stroke-width="2" opacity=".9"/>
        <ellipse cx="0" cy="0" rx="48" ry="14" fill="none" stroke="#ffd39a" stroke-width="1" opacity=".7"/>
        <ellipse cx="0" cy="0" rx="10" ry="3" fill="#ffb45c" opacity=".9"/>
        <path d="M0 -8 L0 -56" stroke="#ffb45c" stroke-width="1.2" opacity=".6"/>
        <text x="0" y="-70" text-anchor="middle" font-family="Bahnschrift, Consolas, monospace" font-size="10" letter-spacing="2" fill="#ffd39a">TARGET · 12.6 m</text>
      </g>
      <g id="arrivalG" opacity="0">
        <ellipse cx="0" cy="0" rx="118" ry="34" fill="none" stroke="#ffb45c" stroke-width="2" opacity=".8"/>
        <ellipse cx="0" cy="0" rx="92" ry="26" fill="none" stroke="#ffb45c" stroke-width="1" opacity=".5" stroke-dasharray="5 8"/>
      </g>
    `;

    // dust
    for (let i = 0; i < 95; i++) {
      const c = svg("circle", {
        cx: rr(0, 1920), cy: rr(80, 1040), r: rr(.5, 2.4),
        fill: i % 7 === 0 ? "#ffd9a8" : "#b9e6ec",
        opacity: 0,
      });
      dustLayer.appendChild(c);
      c._b = { x: +c.getAttribute("cx"), y: +c.getAttribute("cy"), d: rr(.15, 1), ph: rr(0, 6.28), sp: rr(.2, .9) };
    }
    for (let i = 0; i < 18; i++) {
      const c = svg("circle", {
        cx: rr(0, 1920), cy: rr(200, 1000), r: rr(2, 6),
        fill: "#d8f6f8", opacity: 0, filter: "url(#blur6)",
      });
      foregroundDust.appendChild(c);
      c._b = { x: +c.getAttribute("cx"), y: +c.getAttribute("cy"), ph: rr(0, 6.28), sp: rr(.2, .6) };
    }
  }

  function updateTunnel(t, travel, camX, camY) {
    const near = ribGeom(1.02, camX, camY);
    interiorPath.setAttribute("d", archD(near, true));
    const far = ribGeom(0.055, camX, camY);
    mouthPath.setAttribute("d", archD(far, true));
    mouthHalo.setAttribute("d", archD(far, false));

    for (let i = 0; i < ribs.length; i++) {
      const p = (i / RIB_N + travel) % 1;
      const z = Math.pow(1 - p, 1.35);
      const g = ribGeom(z, camX, camY);
      const el = ribs[i];
      if (z < 0.052) { el.setAttribute("opacity", "0"); continue; }
      el.setAttribute("d", archD(g, false));
      const op = .045 + .55 * Math.pow(z, 1.55);
      el.setAttribute("stroke", `rgba(126,171,180,${op.toFixed(3)})`);
      el.setAttribute("stroke-width", (0.7 + 4.2 * z).toFixed(2));
    }

    // wall seams
    const seamAngles = [Math.PI, Math.PI * 1.13, Math.PI * 1.27, Math.PI * 1.42, Math.PI * 1.57, Math.PI * 1.72, Math.PI * 1.87, Math.PI * 2];
    const seams = wallSeams.children;
    for (let s = 0; s < 8; s++) {
      const th = seamAngles[s];
      let d = "";
      for (let j = 0; j <= 20; j++) {
        const p = (j / 20 + travel * .92) % 1;
        const z = Math.pow(1 - p, 1.35);
        if (z < .06) continue;
        const g = ribGeom(z, camX, camY);
        const x = g.cx + Math.cos(th) * g.w / 2;
        const y = g.cy + Math.sin(th) * g.h;
        d += (d ? " L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      }
      seams[s].setAttribute("d", d);
    }
    // side walls
    const wallL = near.cx - near.w / 2, wallR = near.cx + near.w / 2;
    seams[8].setAttribute("d", `M${wallL} ${near.cy} L${wallL} ${near.floorY}`);
    seams[9].setAttribute("d", `M${wallR} ${near.cy} L${wallR} ${near.floorY}`);
    seams[10].setAttribute("d", `M${near.cx - near.w * .18} ${near.floorY} L960 525 M${near.cx + near.w * .18} ${near.floorY} L960 525`);
    for (let s = 0; s < 11; s++) seams[s].setAttribute("stroke", `rgba(137,181,190,${(0.06 + .1 * (s / 11)).toFixed(3)})`);

    // floor transverse + longitudinal
    const fl = floorLines.children;
    for (let j = 0; j < 18; j++) {
      const p = (j / 18 + travel * .94) % 1;
      const z = Math.pow(1 - p, 1.35);
      if (z < .07) { fl[j].setAttribute("opacity", "0"); continue; }
      const g = ribGeom(z, camX, camY);
      fl[j].setAttribute("d", `M${(g.cx - g.w / 2).toFixed(1)} ${g.floorY.toFixed(1)} L${(g.cx + g.w / 2).toFixed(1)} ${g.floorY.toFixed(1)}`);
      fl[j].setAttribute("opacity", String(.15 + .45 * z));
    }
    for (let j = 0; j < 8; j++) {
      const bx = 960 + (j - 3.5) * 250;
      fl[18 + j].setAttribute("d", `M960 522 L${bx} 1080`);
      fl[18 + j].setAttribute("opacity", String(.1 + .04 * (j % 2)));
    }

    // lights
    for (let i = 0; i < 8; i++) {
      const p = (i / 8 + travel * .9 + .08) % 1;
      const z = Math.pow(1 - p, 1.35);
      const g = tunnelLights.children[i];
      if (z < .12) { g.setAttribute("opacity", "0"); continue; }
      const gg = ribGeom(z, camX, camY);
      const lx = gg.cx;
      const ly = gg.cy - gg.h * .92;
      g.setAttribute("opacity", String(clamp(.18 + z * .9)));
      g._glow.setAttribute("cx", lx); g._glow.setAttribute("cy", ly);
      g._glow.setAttribute("rx", 26 + 90 * z); g._glow.setAttribute("ry", 14 + 34 * z);
      g._glow.setAttribute("opacity", String(.12 + .3 * z));
      g._pool.setAttribute("cx", lx); g._pool.setAttribute("cy", gg.floorY - 8);
      g._pool.setAttribute("rx", 40 + 200 * z); g._pool.setAttribute("ry", 8 + 34 * z);
      g._pool.setAttribute("opacity", String(.05 + .1 * z));
      g._bar.setAttribute("x", lx - (12 + 50 * z));
      g._bar.setAttribute("y", ly - (1 + 2 * z));
      g._bar.setAttribute("width", 24 + 100 * z);
      g._bar.setAttribute("height", 2 + 5 * z);
      g._core.setAttribute("x", lx - (6 + 25 * z));
      g._core.setAttribute("y", ly);
      g._core.setAttribute("width", 12 + 50 * z);
      g._core.setAttribute("height", 1 + 2.4 * z);
    }

    // dust
    const ds = dustLayer.children;
    for (let i = 0; i < ds.length; i++) {
      const c = ds[i], b = c._b;
      const x = b.x + Math.sin(t * .22 + b.ph) * 26 * b.d + Math.sin(t * .07 + b.ph * 2) * 18;
      const y = b.y + Math.cos(t * .17 + b.ph) * 14 * b.d - ((t * 5 * b.sp + b.ph * 30) % 60);
      c.setAttribute("cx", x.toFixed(1));
      c.setAttribute("cy", y.toFixed(1));
      c.setAttribute("r", (0.5 + 2.3 * b.d).toFixed(2));
      c.setAttribute("opacity", String((.08 + .28 * b.d) * (0.55 + .45 * Math.sin(t * .7 + b.ph))));
    }
    const fs = foregroundDust.children;
    for (let i = 0; i < fs.length; i++) {
      const c = fs[i], b = c._b;
      c.setAttribute("cx", (b.x + Math.sin(t * .12 + b.ph) * 50).toFixed(1));
      c.setAttribute("cy", (b.y + Math.cos(t * .1 + b.ph) * 24).toFixed(1));
      c.setAttribute("opacity", String(.035 + .045 * (0.5 + .5 * Math.sin(t * .35 + b.ph))));
    }

    portal.setAttribute("opacity", String(1 - smooth(5.5, 7.4, t)));
  }

  // ============================================================
  // ROBOT
  // ============================================================
  function updateRobot(t) {
    robotUse.setAttribute("x", "0");
    robotUse.setAttribute("y", "0");
    robotUse.setAttribute("width", "700");
    robotUse.setAttribute("height", "520");

    let x = 960, y = 892, s = .82, op = 0, ang = 0;
    if (t < 4.2) {
      op = 0;
    } else if (t < 10) {
      const p = easeInOut(inv(4.2, 10, t));
      s = lerp(.58, .83, p);
      y = lerp(846, 892, p);
      op = smooth(4.2, 5.2, t);
    } else if (t < 14) {
      s = .88; y = 892; op = 1;
    } else if (t < 23.2) {
      s = 1.02; y = 892 + Math.sin(t * 7.4) * 2.2; x = 960 + Math.sin(t * 1.7) * 4.5;
      op = 1; ang = Math.sin(t * 5.4) * .28;
    } else if (t < 27.1) {
      const p = easeInOut(inv(23.2, 26.2, t));
      s = lerp(1.02, .95, p); y = 892; op = 1;
      ang = Math.sin(t * 2.2) * .12 * (1 - p);
    } else {
      op = 0;
    }
    const vis = op * (1 - smooth(26.7, 27.35, t));
    robotWrap.setAttribute("opacity", vis.toFixed(3));
    robotWrap.setAttribute("transform",
      `translate(${x.toFixed(2)} ${y.toFixed(2)}) rotate(${ang.toFixed(3)}) scale(${s.toFixed(4)}) translate(-350 -480)`);
    robotWrap.style.filter = `brightness(1.16) contrast(1.04) drop-shadow(0 0 ${(16 + 10 * s).toFixed(0)}px rgba(84,226,229,.18))`;

    if (robotAura) {
      robotAura.setAttribute("opacity", String(vis * .9));
      const a = robotAura.children;
      a[0].setAttribute("cx", x.toFixed(1)); a[0].setAttribute("cy", (y - 90 * s).toFixed(1));
      a[0].setAttribute("rx", (300 * s).toFixed(1)); a[0].setAttribute("ry", (220 * s).toFixed(1));
      a[0].setAttribute("opacity", String(.07 + .05 * pulse(t, .35)));
      a[1].setAttribute("cx", x.toFixed(1)); a[1].setAttribute("cy", (y + 2).toFixed(1));
      a[1].setAttribute("rx", (260 * s).toFixed(1)); a[1].setAttribute("ry", (46 * s).toFixed(1));
      a[2].setAttribute("cx", x.toFixed(1)); a[2].setAttribute("cy", (y + 4).toFixed(1));
      a[2].setAttribute("rx", (150 * s).toFixed(1)); a[2].setAttribute("ry", (22 * s).toFixed(1));
    }

    const powered = t > 5.25;
    robotWrap.style.setProperty("--status", powered ? "#6ff7e7" : "#23343b");
    robotWrap.style.setProperty("--amber", powered ? "#ffb45c" : "#34291f");
    robotWrap.style.setProperty("--beaconGlow", powered ? String(.35 + .35 * pulse(t, .55)) : "0");

    // face-stage robot (same asset, lower-left composition)
    const fp = clamp(smooth(27.0, 28.0, t) * (1 - smooth(39.4, 40.0, t)));
    faceRobot.style.opacity = String(.92 * fp);
    faceRobot.style.transform = `translate(${(-6 * (1 - fp)).toFixed(1)}px, ${(8 * (1 - fp)).toFixed(1)}px)`;
    faceRobot.style.setProperty("--status", "#6ff7e7");
    faceRobot.style.setProperty("--amber", "#ffb45c");
    faceRobot.style.setProperty("--beaconGlow", String(.35 + .3 * pulse(t, .5)));

    // target ring
    const targetG = $("targetG"), arrivalG = $("arrivalG"), navLine = $("navLine"), chev = $("navChevrons");
    const targetVis = smooth(10.0, 10.9, t) * (1 - smooth(26.6, 27.2, t));
    targetG.setAttribute("opacity", String(targetVis));
    const arriveP = smooth(23.0, 25.2, t);
    const ty = lerp(792, 888, arriveP);
    targetG.setAttribute("transform", `translate(960 ${ty.toFixed(1)}) scale(${(1 + .05 * pulse(t, .65)).toFixed(3)})`);
    const ringPulse = .5 + .5 * pulse(t, .65);
    targetG.children[0].setAttribute("opacity", String(.45 + .5 * ringPulse));
    targetG.children[0].setAttribute("rx", String(68 + 12 * ringPulse));
    targetG.children[0].setAttribute("ry", String(20 + 4 * ringPulse));
    targetG.children[4].textContent = t > 24.0 ? "TARGET · ARRIVED" : "TARGET · 12.6 m";

    arrivalG.setAttribute("opacity", String(smooth(23.6, 24.4, t) * (1 - smooth(26.8, 27.3, t))));
    arrivalG.setAttribute("transform", `translate(960 892) scale(${(1 + .06 * pulse(t, .8)).toFixed(3)})`);
    arrivalG.children[0].setAttribute("opacity", String(.35 + .45 * pulse(t, .8)));

    const navVis = smooth(10.2, 11.2, t) * (1 - smooth(23.4, 24.2, t));
    navLine.setAttribute("opacity", String(navVis * .8));
    navLine.setAttribute("d", "M960 894 L960 790");
    navLine.setAttribute("stroke-dashoffset", String(-t * 28));
    chev.setAttribute("opacity", String(navVis * .65));
    for (let i = 0; i < chev.children.length; i++) {
      chev.children[i].setAttribute("opacity", String(.35 + .5 * (0.5 + .5 * Math.sin(t * 3 - i * 1.2))));
    }
  }

  // ============================================================
  // MAP WINDOW
  // ============================================================
  const mapPoints = [];
  let mapRobotY = 292;
  let mapTargetG;

  function initMap() {
    for (let x = 20; x <= 600; x += 40) mapGrid.appendChild(svg("line", { x1: x, y1: 10, x2: x, y2: 320 }));
    for (let y = 20; y <= 310; y += 32) mapGrid.appendChild(svg("line", { x1: 10, y1: y, x2: 610, y2: y }));

    for (let i = 0; i < 700; i++) {
      const z = Math.pow(rnd(), .78);
      const th = Math.PI + rnd() * Math.PI;
      const w = 58 + 238 * z;
      const h = 42 + 172 * z;
      const jitter = (1 + 4 * z) * 2.2;
      let x = 310 + Math.cos(th) * w / 2 + (rnd() - .5) * jitter;
      let y = 150 + Math.sin(th) * h / 2 + (rnd() - .5) * jitter + 18 * (1 - z);
      if (rnd() < .2) {
        x = 310 + (rnd() - .5) * w;
        y = 182 + 34 * z + rnd() * 18;
      }
      const c = svg("circle", {
        cx: x.toFixed(2), cy: y.toFixed(2), r: (.45 + 1.15 * z).toFixed(2),
        fill: rnd() < .12 ? "#ffcf8d" : "#61e9e8", opacity: 0,
      });
      mapCloud.appendChild(c);
      mapPoints.push({ el: c, order: rnd(), z, base: .2 + .7 * z });
    }

    mapPath.innerHTML = `
      <path d="M310 286 C296 244, 296 170, 310 112" fill="none" stroke="rgba(112,244,239,.95)" stroke-width="2.2" stroke-dasharray="6 6" stroke-linecap="round"/>
      <path d="M310 286 C296 244, 296 170, 310 112" fill="none" stroke="rgba(112,244,239,.18)" stroke-width="9" filter="url(#mapGlow)"/>
    `;
    mapPath.setAttribute("opacity", "0");

    mapTargetG = svg("g", { opacity: "0" });
    mapTargetG.appendChild(svg("circle", { cx: 310, cy: 112, r: 17, fill: "none", stroke: "#ffb45c", "stroke-width": "1.6" }));
    mapTargetG.appendChild(svg("circle", { cx: 310, cy: 112, r: 8, fill: "none", stroke: "#ffd39a", "stroke-width": "1" }));
    mapTargetG.appendChild(svg("circle", { cx: 310, cy: 112, r: 2.6, fill: "#ffb45c" }));
    mapTarget.appendChild(mapTargetG);

    mapRobot.innerHTML = `
      <g transform="translate(310 292)">
        <path d="M0 -9 L7 5 L0 2 L-7 5 Z" fill="#8ef7ef" stroke="#d9fffb" stroke-width=".6"/>
        <circle cx="0" cy="0" r="13" fill="none" stroke="rgba(142,247,239,.45)" stroke-width="1"/>
        <circle cx="0" cy="0" r="3" fill="#ffb45c"/>
      </g>
    `;

    mapScan.innerHTML = `
      <defs>
        <linearGradient id="scanGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#67f5ef" stop-opacity="0"/>
          <stop offset="80%" stop-color="#67f5ef" stop-opacity=".18"/>
          <stop offset="100%" stop-color="#67f5ef" stop-opacity=".55"/>
        </linearGradient>
      </defs>
      <rect x="10" y="0" width="600" height="26" fill="url(#scanGrad)" opacity="0"/>
      <line x1="10" y1="0" x2="610" y2="0" stroke="rgba(124,249,241,.85)" stroke-width="1"/>
    `;

    mapLabels.innerHTML = `
      <text x="18" y="306">ENTRY / 0 m</text>
      <text x="420" y="96">FACE / 12.6 m</text>
      <text x="328" y="205" fill="rgba(255,180,92,.85)">TARGET</text>
      <text x="18" y="34" fill="rgba(145,205,211,.55)">SLAM POINT CLOUD</text>
    `;
  }

  function updateMap(t) {
    let op = 0, scale = 1, ty = 0;
    if (t > 4.75 && t < 27.2) {
      op = smooth(4.75, 5.6, t) * (1 - smooth(26.3, 27.1, t));
      if (t > 10 && t < 14) scale = 1 + .018 * smooth(10, 11.2, t);
      if (t >= 14) scale = .9;
      ty = t >= 14 ? 0 : 0;
    }
    mapWindow.style.opacity = String(op);
    mapWindow.style.transform = `translateY(${ty}px) scale(${scale.toFixed(3)})`;

    const build = smooth(5.2, 9.5, t);
    for (let i = 0; i < mapPoints.length; i++) {
      const p = mapPoints[i];
      const v = p.order < build * 1.08;
      p.el.setAttribute("opacity", v ? String(p.base * (.45 + .55 * build)) : "0");
    }
    const count = 1284630 * build;
    cloudCount.textContent = fmt(count);
    mapPct.textContent = String(Math.round(build * 100));

    let status = "正在初始化";
    if (t >= 5.2) status = "点云构建中";
    if (t >= 9.5) status = "建图完成";
    if (t >= 10.2) status = "路径规划中";
    if (t >= 12.4) status = "目标锁定";
    if (t >= 14.0) status = "自主导航中";
    if (t >= 23.6) status = "已到达";
    mapStatus.textContent = status;

    const pathP = smooth(10.1, 12.0, t);
    mapPath.setAttribute("opacity", String(pathP * .95));
    const dashLen = 250;
    for (const p of mapPath.children) {
      p.setAttribute("stroke-dasharray", p === mapPath.children[0] ? "6 6" : String(dashLen));
      if (p === mapPath.children[1]) {
        p.setAttribute("stroke-dashoffset", String(dashLen * (1 - pathP)));
      }
    }
    const targetOp = smooth(10.2, 11.2, t) * (1 - smooth(26.4, 27.0, t));
    mapTargetG.setAttribute("opacity", String(targetOp));
    const pr = 1 + .18 * pulse(t, .7);
    mapTargetG.setAttribute("transform", `translate(310 112) scale(${pr.toFixed(3)}) translate(-310 -112)`);

    const prog = t < 14 ? 0 : easeInOut(inv(14, 23.4, t));
    mapRobotY = lerp(292, 116, prog);
    mapRobot.firstElementChild.setAttribute("transform", `translate(310 ${mapRobotY.toFixed(1)})`);
    mapRobot.setAttribute("opacity", String(smooth(4.9, 5.8, t)));

    const scanOn = t > 5.2 && t < 10.2;
    const scanRect = mapScan.children[0], scanLine = mapScan.children[1];
    const sy = 18 + 290 * ((t * .22) % 1);
    scanRect.setAttribute("y", String(sy - 26));
    scanRect.setAttribute("opacity", scanOn ? String(.6 * (1 - smooth(9.6, 10.2, t))) : "0");
    scanLine.setAttribute("y1", String(sy));
    scanLine.setAttribute("y2", String(sy));
    scanLine.setAttribute("opacity", scanOn ? String(.7 * (1 - smooth(9.6, 10.2, t))) : "0");
  }

  // ============================================================
  // PHOTOGRAMMETRY WINDOW
  // ============================================================
  function initMesh() {
    const cols = 26, rows = 19;
    let html = "";
    for (let j = 0; j <= rows; j++) {
      let d = "";
      for (let i = 0; i <= cols; i++) {
        const x = i / cols * 4096;
        const y = j / rows * 3072 + Math.sin(i * .55 + j * .7) * 16 + Math.cos(i * .21) * 10;
        d += (i ? " L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      }
      html += `<path d="${d}" fill="none" stroke="rgba(104,238,233,.42)" stroke-width="2.2"/>`;
    }
    for (let i = 0; i <= cols; i++) {
      let d = "";
      for (let j = 0; j <= rows; j++) {
        const x = i / cols * 4096 + Math.sin(j * .6 + i * .4) * 18;
        const y = j / rows * 3072;
        d += (j ? " L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      }
      html += `<path d="${d}" fill="none" stroke="rgba(104,238,233,.34)" stroke-width="2"/>`;
    }
    for (let j = 0; j <= rows; j += 2) {
      for (let i = 0; i <= cols; i += 2) {
        const x = i / cols * 4096;
        const y = j / rows * 3072 + Math.sin(i * .55 + j * .7) * 16 + Math.cos(i * .21) * 10;
        html += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.2" fill="#aefbf4" opacity=".7"/>`;
      }
    }
    meshOverlay.innerHTML = html;
  }

  function updatePhotoStage(t) {
    const faceOp = smooth(26.65, 27.55, t) * (1 - smooth(39.45, 40.15, t));
    faceStage.style.opacity = String(faceOp);
    faceStage.style.display = faceOp > .001 ? "block" : "none";
    liveStage.style.opacity = String(1 - smooth(39.4, 40.1, t));

    const fp = easeInOut(inv(27, 40, t));
    facePhoto.style.transform = `scale(${(1.085 - .055 * fp).toFixed(4)}) translate(${(-.6 * fp).toFixed(2)}%, ${(-.3 * fp).toFixed(2)}%)`;

    const camOn = smooth(27.6, 28.5, t) * (1 - smooth(32.6, 33.2, t));
    cameraHud.style.opacity = String(camOn * .95);
    cameraHud.style.transform = `scale(${(1.015 - .015 * camOn).toFixed(4)})`;

    const closeP = smooth(28.0, 29.0, t) * (1 - smooth(32.4, 33.1, t));
    cameraCloseup.style.opacity = String(closeP);
    cameraCloseup.style.transform = `translateX(${(50 * (1 - closeP)).toFixed(1)}px) scale(${(.92 + .08 * closeP).toFixed(3)})`;

    const stripP = smooth(29.4, 30.4, t) * (1 - smooth(39.0, 39.6, t));
    captureStrip.style.opacity = String(stripP);
    captureStrip.style.transform = `translateY(${(14 * (1 - stripP)).toFixed(1)}px)`;
    const views = 3 + 9 * smooth(29.6, 33.2, t);
    if (viewCount) viewCount.textContent = String(Math.round(views)).padStart(2, "0") + " / 12";

    const photoP = smooth(32.8, 33.7, t) * (1 - smooth(39.1, 39.8, t));
    photoWindow.style.opacity = String(photoP);
    photoWindow.style.transform = `translateY(${(18 * (1 - photoP)).toFixed(1)}px) scale(${(.97 + .03 * photoP).toFixed(3)})`;

    const tabs = [...photoWindow.querySelectorAll(".tab")];
    let active = 0;
    if (t >= 35.0) active = 1;
    if (t >= 37.3) active = 2;
    tabs.forEach((tab, i) => tab.classList.toggle("active", i === active));

    const meshP = smooth(34.9, 36.0, t);
    meshOverlay.style.opacity = String(meshP * (active >= 1 ? 1 : 0));
    const meshTag = photoWindow.querySelector(".mesh-tag");
    meshTag.style.opacity = String(meshP);
    orthoImg.style.filter = `saturate(${.82 - .16 * meshP}) contrast(${1.08 + .06 * meshP}) brightness(${.8 - .05 * meshP})`;
    const scan = photoWindow.querySelector(".scan-line");
    scan.style.top = String(8 + 84 * ((t * .22) % 1)) + "%";
    scan.style.opacity = String((1 - smooth(36.0, 37.0, t)) * .9);

    faceLabels.style.opacity = String(smooth(35.8, 36.8, t) * (1 - smooth(39.0, 39.6, t)));

    let flash = 0;
    for (const c of [28.7, 29.35, 30.0, 30.65, 31.3, 31.95, 32.6]) {
      flash = Math.max(flash, Math.max(0, 1 - Math.abs(t - c) / .085));
    }
    shutterFlash.style.opacity = String(flash * .16);
  }

  // ============================================================
  // ANALYSIS
  // ============================================================
  const regionPaths = [];
  const tracePaths = [];

  function initAnalysis() {
    // regions
    const palette = ["#37d6c4", "#5ba7ff", "#f0b45a", "#9a8cff", "#72d38a", "#ff8e72", "#52c7e8", "#d6d06a", "#c38bff", "#6fe0b0"];
    G.regions.forEach((r, idx) => {
      const pts = r.points.map(([x, y]) => [x * 4096, y * 3072]);
      if (pts.length < 3) return;
      let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
      for (let i = 1; i < pts.length; i++) d += ` L${pts[i][0].toFixed(1)} ${pts[i][1].toFixed(1)}`;
      d += " Z";
      const path = svg("path", {
        d, fill: r.color, "fill-opacity": ".22", stroke: r.color, "stroke-opacity": ".9",
        "stroke-width": "6", "stroke-linejoin": "round", opacity: 0,
      });
      anaRegions.appendChild(path);
      regionPaths.push(path);

      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
      const txt = svg("text", {
        x: cx.toFixed(1), y: cy.toFixed(1), "text-anchor": "middle", "dominant-baseline": "middle",
        "font-family": "Bahnschrift, Consolas, monospace", "font-size": "40",
        fill: "#f4feff", stroke: "#041014", "stroke-width": "12", "paint-order": "stroke",
        opacity: 0,
      });
      txt.textContent = r.id;
      anaRegions.appendChild(txt);
      regionPaths.push(txt);

      const sw = document.createElement("span");
      sw.innerHTML = `<i style="background:${r.color}"></i>${r.id}`;
      regionLegend.appendChild(sw);
    });
    regionCountLabel.textContent = `${G.region_total} 个候选区域`;

    // traces
    for (const f of G.traces) {
      const pts = f.points.map(([x, y]) => [x * 4096, y * 3072]);
      if (pts.length < 2) continue;
      let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
      let len = 0;
      for (let i = 1; i < pts.length; i++) {
        d += ` L${pts[i][0].toFixed(1)} ${pts[i][1].toFixed(1)}`;
        len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      }
      const path = svg("path", {
        d, fill: "none", stroke: "#ffb45c", "stroke-width": "5.2", "stroke-linecap": "round",
        "stroke-linejoin": "round", "stroke-dasharray": String(len), "stroke-dashoffset": String(len),
        opacity: ".92",
      });
      anaTraces.appendChild(path);
      tracePaths.push({ el: path, len });
    }
  }

  function updateAnalysis(t) {
    const op = smooth(39.75, 40.55, t) * (1 - smooth(52.85, 53.55, t));
    analysisStage.style.opacity = String(op);
    analysisStage.style.display = op > .001 ? "block" : "none";
    analysisStage.style.transform = `translateY(${(18 * (1 - smooth(39.75, 40.55, t))).toFixed(1)}px)`;

    const tableMode = smooth(48.0, 48.9, t);
    const vw = lerp(1120, 900, tableMode);
    const vh = lerp(890, 725, tableMode);
    const cw = lerp(1040, 820, tableMode);
    const ch = lerp(780, 610, tableMode);
    anaViewer.style.width = vw.toFixed(1) + "px";
    anaViewer.style.height = vh.toFixed(1) + "px";
    viewerCanvas.style.width = cw.toFixed(1) + "px";
    viewerCanvas.style.height = ch.toFixed(1) + "px";

    // processing panel
    const ppOp = 1 - smooth(47.8, 48.5, t);
    processPanel.style.opacity = String(ppOp);
    processPanel.style.transform = `translateX(${(18 * (1 - ppOp)).toFixed(1)}px)`;

    // table
    const tp = smooth(48.15, 49.0, t);
    tablePanel.style.opacity = String(tp);
    tablePanel.style.transform = `translateX(${(26 * (1 - tp)).toFixed(1)}px)`;
    tableRows.forEach((row, i) => {
      const p = smooth(48.35 + i * .09, 49.05 + i * .09, t);
      row.style.opacity = String(p);
      row.style.transform = `translateY(${(10 * (1 - p)).toFixed(1)}px)`;
    });

    // step progress
    let active = 1, pct = 8;
    if (t >= 41.4) { active = 2; pct = 32; }
    if (t >= 43.1) { active = 3; pct = 62; }
    if (t >= 45.0) { active = 4; pct = 84; }
    if (t >= 46.5) { active = 4; pct = 100; }
    processBar.style.width = pct + "%";
    processPct.textContent = pct + "%";
    [...processPanel.querySelectorAll(".process-item")].forEach((el, i) => {
      const step = i + 1;
      el.classList.toggle("done", step < active || pct >= 100);
      el.classList.toggle("active", step === active && pct < 100);
      const st = el.querySelector(".process-state");
      st.textContent = step < active || pct >= 100 ? "DONE" : step === active ? "RUN" : "WAIT";
    });

    // scan + badges
    const scanOn = t < 46.2;
    anaScan.style.opacity = scanOn ? String(.9 * (1 - smooth(45.4, 46.2, t))) : "0";
    anaScan.style.top = String(4 + 92 * ((t * .24) % 1)) + "%";

    const rp = smooth(41.6, 44.2, t);
    anaRegions.style.opacity = String(rp > .001 ? 1 : 0);
    regionPaths.forEach((el, i) => {
      const local = clamp((rp * 1.3 - (i % 20) * .022) / .55);
      el.setAttribute("opacity", String(local * (el.tagName === "text" ? .9 : .95)));
    });

    const trp = smooth(43.4, 46.3, t);
    anaTraces.style.opacity = String(trp > .001 ? 1 : 0);
    tracePaths.forEach((tr, i) => {
      const local = clamp((trp * 1.55 - (i % 55) * .012) / .5);
      tr.el.setAttribute("stroke-dashoffset", String(tr.len * (1 - local)));
      tr.el.setAttribute("opacity", String(.25 + .7 * local));
    });

    const skp = smooth(46.0, 47.3, t);
    anaSketch.style.opacity = String(skp);

    let badge = "原始影像";
    if (t >= 41.6) badge = "岩性视觉分区";
    if (t >= 43.4) badge = "结构面迹线提取";
    if (t >= 46.0) badge = "综合地质素描";
    viewerBadge.textContent = badge;

    if (t < 48) regionLegend.style.opacity = String(1 - smooth(47.4, 48.0, t));
    else regionLegend.style.opacity = String(1 - smooth(52.0, 52.8, t));

    processLogs.forEach((el, i) => {
      const p = smooth(41.0 + i * .95, 41.7 + i * .95, t);
      el.style.opacity = String(p);
      el.style.transform = `translateY(${(5 * (1 - p)).toFixed(1)}px)`;
    });

    const clock = 40 * 60 + Math.floor(t - 40);
    const mm = String(Math.floor(clock / 60)).padStart(2, "0");
    const ss = String(clock % 60).padStart(2, "0");
    $("anaClock").textContent = `00:${mm}:${ss}`;
  }

  // ============================================================
  // FINAL + HUD
  // ============================================================
  function updateFinal(t) {
    const op = smooth(52.9, 53.8, t) * (1 - smooth(59.2, 60, t));
    finalStage.style.opacity = String(op);
    finalStage.style.display = op > .001 ? "block" : "none";
    const p = easeInOut(inv(53.0, 54.4, t));
    finalCopy.style.opacity = String(smooth(53.2, 54.2, t));
    finalCopy.style.transform = `translateY(${(26 * (1 - p)).toFixed(1)}px)`;
    finalRobot.style.opacity = String(smooth(53.3, 54.6, t));
    finalRobot.style.transform = `translateY(${(30 * (1 - smooth(53.3, 54.6, t))).toFixed(1)}px)`;
    finalCards.forEach((card, i) => {
      const cp = smooth(53.7 + i * .34, 54.5 + i * .34, t);
      card.style.opacity = String(cp);
      card.style.transform = `translateY(${(24 * (1 - cp)).toFixed(1)}px)`;
    });
  }

  function updateHud(t) {
    const hudOp = 1 - smooth(53.0, 53.8, t);
    hud.style.opacity = String(hudOp);

    titleCard.style.opacity = String(smooth(.55, 1.2, t) * (1 - smooth(3.8, 4.5, t)));
    titleCard.style.transform = `translateY(${(20 * (1 - smooth(.55, 1.2, t))).toFixed(1)}px)`;

    const chapP = smooth(4.6, 5.3, t) * (1 - smooth(52.5, 53.2, t));
    chapterTag.style.opacity = String(chapP);
    let chap = CHAPTERS[0];
    for (const c of CHAPTERS) if (t >= c[0] && t < c[1]) chap = c;
    chapterNum.textContent = chap[2];
    chapterTitle.textContent = chap[3];
    chapterEn.textContent = chap[4];

    const telP = smooth(5.2, 6.1, t) * (1 - smooth(52.6, 53.2, t));
    telemetry.style.opacity = String(telP);
    let mode = "STANDBY", speed = 0, dist = 12.6;
    if (t >= 5.2) { mode = "MAPPING"; }
    if (t >= 10.2) { mode = "PLANNING"; }
    if (t >= 14.0) { mode = "AUTO NAV"; speed = .82; dist = lerp(12.6, .2, easeInOut(inv(14, 23.2, t))); }
    if (t >= 23.4) { mode = "ARRIVED"; speed = 0; dist = 0; }
    if (t >= 27.2) { mode = "PHOTOGRAM"; speed = 0; dist = 0; }
    if (t >= 40.2) { mode = "PROCESSING"; }
    telMode.textContent = mode;
    telSpeed.innerHTML = speed.toFixed(2) + " <i>m/s</i>";
    telDist.innerHTML = dist.toFixed(1) + " <i>m</i>";

    let sub = null;
    for (const s of SUBS) if (t >= s[0] && t <= s[1]) sub = s;
    if (sub) {
      subText.textContent = sub[2];
      const p = smooth(sub[0], sub[0] + .35, t) * (1 - smooth(sub[1] - .35, sub[1], t));
      subtitle.style.opacity = String(p);
    } else {
      subtitle.style.opacity = "0";
    }

    const prog = clamp(t / 60);
    progressFill.style.width = (prog * 100).toFixed(3) + "%";
    progressHead.style.left = (prog * 100).toFixed(3) + "%";
    bottomBar.style.opacity = String(smooth(4.9, 5.6, t) * (1 - smooth(52.7, 53.3, t)));
    const sec = Math.floor(t);
    timecode.textContent = `00:${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;

    const toastP = smooth(23.9, 24.5, t) * (1 - smooth(26.3, 26.9, t));
    arrivalToast.style.opacity = String(toastP);
    arrivalToast.style.transform = `translate(-50%, ${lerp(14, 0, toastP).toFixed(1)}px)`;

    let flash = 0;
    for (const c of [10.0, 27.0, 40.0, 53.0]) {
      flash = Math.max(flash, Math.max(0, 1 - Math.abs(t - c) / .14) * .28);
    }
    cutFlash.style.opacity = String(flash);

    const fade = Math.max(1 - smooth(0, .95, t), smooth(59.15, 60, t));
    fadeBlack.style.opacity = String(clamp(fade));
  }

  // ============================================================
  // SEEK
  // ============================================================
  let lastT = 0;
  window.seek = (time) => {
    const t = clamp(Number(time) || 0, 0, 60);
    lastT = t;

    let travel = .12 + .012 * (t / 14);
    if (t >= 14) {
      const p = clamp(t - 14, 0, 9.2);
      travel = .132 + .095 * p;
      if (t > 23.2) travel = .132 + .095 * 9.2;
    }
    const camX = Math.sin(t * .55) * 8 + (t > 14 && t < 24 ? Math.sin(t * 1.7) * 4 : 0);
    const camY = Math.sin(t * 1.2) * 2.2 + (t > 14 && t < 24 ? Math.sin(t * 8.1) * 1.1 : 0);

    if (t < 40.3) updateTunnel(t, travel, camX, camY);
    updateRobot(t);
    updateMap(t);
    updatePhotoStage(t);
    updateAnalysis(t);
    updateFinal(t);
    updateHud(t);
  };

  window.__lastT = () => lastT;

  // ============================================================
  // BOOT
  // ============================================================
  async function boot() {
    initTunnel();
    initMap();
    initMesh();
    initAnalysis();
    // ensure custom properties are available inside <use> shadow trees
    robotWrap.style.setProperty("--status", "#23343b");
    robotWrap.style.setProperty("--amber", "#34291f");
    robotWrap.style.setProperty("--beaconGlow", "0");
    faceRobot.style.setProperty("--status", "#6ff7e7");
    faceRobot.style.setProperty("--amber", "#ffb45c");
    faceRobot.style.setProperty("--beaconGlow", ".4");

    if (document.fonts && document.fonts.ready) {
      try { await document.fonts.ready; } catch (_) {}
    }
    const imgs = [...document.images];
    await Promise.all(imgs.map((img) => img.decode ? img.decode().catch(() => {}) : Promise.resolve()));
    window.seek(0);
    // two frames to settle filters/layout
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    window.ready = true;
  }

  boot();
})();
