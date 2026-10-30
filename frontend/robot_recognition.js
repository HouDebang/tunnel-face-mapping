// 识别适配层：把"机器人采集到的一张掌子面影像"接到现有单张识别流水线。
// 不重写任何识别算法，只做 HTTP 编排与结果整理；失败时回退到本机预生成样例。
const STAGE_TEXT = {
  preprocessing: "图像预处理",
  face_detection: "掌子面分割",
  lithology_segmentation: "岩性区域分割",
  fracture_segmentation: "裂缝分割",
  trace_extraction: "迹线识别",
  geometry_calculation: "几何参数计算",
  sketch_rendering: "综合地质素描",
  result_export: "成果导出",
};

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  let data = null;
  try { data = await response.json(); } catch { /* 非 JSON 响应按错误处理 */ }
  if (!response.ok) {
    const message = data?.error?.message || data?.detail || `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return data;
}

/** 上传前把影像缩到长边 1000 px：既保留可读细节，也把几何阶段耗时压到可接受范围。 */
export async function prepareUpload(blob, maxSide = 1000) {
  const bitmap = await createImageBitmap(blob);
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  if (scale >= 1) return { blob, width: bitmap.width, height: bitmap.height };
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const scaled = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.92));
  return { blob: scaled, width: canvas.width, height: canvas.height };
}

function findArtifact(task, name) {
  for (const stage of Object.values(task.stages || {})) {
    for (const artifact of stage.artifacts || []) {
      if (artifact.name === name) return artifact;
    }
  }
  return null;
}

/**
 * 提交一次完整识别：上传 → run-all → 轮询 → 取结果与产物。
 * onProgress({phase, text, percent}) 用于驱动页面进度。
 */
export async function analyzeFaceImage({ blob, filename = "face.jpg", onProgress = () => {}, timeoutMs = 300000 }) {
  onProgress({ phase: "upload", text: "上传掌子面影像", percent: 4 });
  const prepared = await prepareUpload(blob);
  const payload = new FormData();
  payload.append("file", prepared.blob, filename);
  const task = await jsonRequest("/api/tasks", { method: "POST", body: payload });
  onProgress({ phase: "queued", text: "已创建识别任务", percent: 10 });

  const submission = await jsonRequest(`/api/tasks/${task.id}/run-all`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force: false }),
  });
  const jobId = submission.job_id;
  const started = Date.now();
  let lastStage = "";

  while (true) {
    if (Date.now() - started > timeoutMs) throw new Error("识别超时，请稍后重试");
    await new Promise(resolve => setTimeout(resolve, 1100));
    const job = await jsonRequest(`/api/jobs/${encodeURIComponent(jobId)}`);
    const current = await jsonRequest(`/api/tasks/${task.id}`);
    const running = Object.entries(current.stages || {}).find(([, stage]) => stage.status === "running");
    if (running && running[0] !== lastStage) {
      lastStage = running[0];
      onProgress({ phase: "stage", text: `正在${STAGE_TEXT[lastStage] || lastStage}`, percent: 20 });
    }
    if (job.status === "failed") throw new Error(job.error || "识别流水线执行失败");
    if (job.status === "completed") break;
  }

  onProgress({ phase: "collect", text: "读取识别结果", percent: 78 });
  const [detail, results] = await Promise.all([
    jsonRequest(`/api/tasks/${task.id}`),
    jsonRequest(`/api/tasks/${task.id}/results`),
  ]);

  const sketch = findArtifact(detail, "geological_sketch.png");
  const lithology = findArtifact(detail, "lithology_overlay.png");
  const fracture = findArtifact(detail, "fracture_overlay.png");
  const formArtifact = findArtifact(detail, "geological_sketch_form.json");
  let form = null;
  if (formArtifact) {
    try {
      const response = await fetch(formArtifact.url, { cache: "no-store" });
      if (response.ok) form = await response.json();
    } catch { /* 表格失败不影响图与统计 */ }
  }

  const result = results.result || {};
  const statistics = result.statistics || {};
  onProgress({ phase: "done", text: "识别完成", percent: 100 });

  return {
    fallback: false,
    taskId: task.id,
    executionId: sketch?.execution_id || null,
    inputSha256: detail.sha256 || null,
    upload: { width: prepared.width, height: prepared.height },
    sketchUrl: sketch?.url || null,
    lithologyUrl: lithology?.url || null,
    fractureUrl: fracture?.url || null,
    form,
    summary: {
      status: result.status || detail.stages?.result_export?.status || "unknown",
      regionCount: (result.lithology_regions || []).length,
      fractureCount: statistics.fracture_count ?? (result.fractures || []).length,
      totalLengthPx: statistics.total_length_px ?? null,
      p21: statistics.p21_per_px ?? null,
      observationAreaPx2: statistics.observation_area_px2 ?? null,
      warnings: (result.quality?.warnings || []).slice(0, 6),
    },
  };
}

/** 识别服务不可用时，载入本机预生成的真实样例成果（明确标注来源）。 */
export async function loadSampleResult() {
  const [form, summary] = await Promise.all([
    fetch("/robot/assets/sample_form.json", { cache: "no-store" }).then(r => (r.ok ? r.json() : null)),
    fetch("/robot/assets/sample_summary.json", { cache: "no-store" }).then(r => (r.ok ? r.json() : null)),
  ]);
  return {
    fallback: true,
    taskId: null,
    executionId: null,
    inputSha256: summary?.input_sha256 || null,
    sketchUrl: "/robot/assets/sample_sketch.jpg",
    lithologyUrl: "/robot/assets/sample_lithology.jpg",
    fractureUrl: "/robot/assets/sample_fracture.jpg",
    form,
    summary: {
      status: summary?.status || "sample",
      regionCount: summary?.region_count ?? 0,
      fractureCount: summary?.fracture_count ?? 0,
      totalLengthPx: summary?.statistics?.total_length_px ?? null,
      p21: summary?.statistics?.p21_per_px ?? null,
      observationAreaPx2: summary?.statistics?.observation_area_px2 ?? null,
      warnings: summary?.quality?.warnings || [],
    },
  };
}
