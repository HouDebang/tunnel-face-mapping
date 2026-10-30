"use strict";

// The browser only presents Python-produced artifacts and vectors. Recognition,
// measurements, configuration validation and cache decisions stay on the server.
const $ = id => document.getElementById(id);
const clone = value => JSON.parse(JSON.stringify(value));
const goodStatuses = new Set(["completed", "warning"]);
const labels = {pending:"未运�, running:"运行�, completed:"已完�, warning:"完成 · 质量警告", stale:"结果已过�, failed:"运行失败", blocked:"前置条件未满�, queued:"等待执行"};
const stageSpecs = [
  {id:"original", title:"原始照片", en:"SOURCE IMAGE", description:"保留照片、原始文件哈希与尺寸。所有处理结果均来自这张照片�},
  {id:"preprocessing", title:"图像预处�, section:"preprocessing", en:"PREPROCESSING", description:"统一 EXIF 方向与工作尺寸，增强局部对比度，并保存原图与工作图之间的坐标变换�},
  {id:"face_detection", title:"掌子面分�, section:"face", en:"FACE REGION", description:"提取候选分析范围。自动边界尚未经过地质验证，整图回退会明确提示�},
  {id:"lithology_segmentation", title:"岩性分割与判定", section:"lithology", en:"LITHOLOGY / 4 SUB-STEPS", description:"本步骤内含四个子步骤：岩性分��算法判定（概率）�大模型调用知识库 �综合判定（确定结论或需复核）�},
  {id:"fracture_segmentation", title:"裂缝分割", section:"fracture", en:"FRACTURE SEGMENTATION", description:"增强暗线并过滤细长连通区域，提取可检查的候选裂�Mask�},
  {id:"trace_extraction", title:"迹线识别", section:"vectorization", en:"TRACE EXTRACTION", description:"骨架化与图追踪生成候选迹线。完整坐标用于长度计算，简化坐标仅用于绘图�},
  {id:"geometry_calculation", title:"几何参数计算", section:"calibration", en:"GEOMETRIC STATISTICS", description:"使用有效区域与未简化迹线计算长度、图像平面方向和 P21，保留坐标与标定条件�},
  {id:"sketch_rendering", title:"综合地质素描", en:"GEOLOGICAL SKETCH", description:"将掌子面范围、岩性候选分区和裂隙迹线叠加到原图。图层开关仅改变显示�},
  {id:"result_export", title:"成果导出", en:"RESULT EXPORT", description:"导出当前有效的图像、矢量、参数表、配置和质量报告；历史过期成果不会混入下载包�}
];
const preferredImages = {
  preprocessing:["preprocessed.png","original.png"],
  face_detection:["face_overlay.png","face_boundary_overlay.png","face_mask.png"],
  lithology_segmentation:["lithology_overlay.png","lithology_mask.png"],
  fracture_segmentation:["fracture_overlay.png","fracture_mask.png","fracture_response.png"],
  trace_extraction:["vector_overlay.png","trace_overlay.png","fracture_traces_overlay.png","traces_overlay.png","skeleton.png","fracture_skeleton.png"],
  sketch_rendering:["geological_sketch.png"], result_export:["geological_sketch.png"]
};
const imageLabels = {"original.png":"原始照片", "preprocessed.png":"预处理图", "face_mask.png":"黑白 Mask", "face_overlay.png":"边界叠加", "face_boundary_overlay.png":"边界叠加", "lithology_mask.png":"区域标签 Mask", "lithology_overlay.png":"候选区域叠�, "fracture_mask.png":"黑白 Mask", "fracture_overlay.png":"候选裂缝叠�, "fracture_skeleton.png":"裂隙骨架", "skeleton.png":"裂隙骨架", "trace_overlay.png":"矢量迹线叠加", "fracture_traces_overlay.png":"矢量迹线叠加", "traces_overlay.png":"矢量迹线叠加", "geological_sketch.png":"综合地质素描"};
Object.assign(imageLabels,{"vector_overlay.png":"矢量迹线叠加","face_layer.png":"候选范围透明图层","lithology_layer.png":"候选分区透明图层","trace_layer.png":"迹线透明图层","trace_ids_layer.png":"迹线编号透明图层"});
const state = {task:null, schema:null, defaults:null, parameterMeta:{}, draft:null, dirty:false, stage:"original", tab:"parameters", operation:false, polling:false, job:null, historical:null, historyId:"", result:null, outputChoice:"", renderKey:"", logs:null, tasks:[], judgment:null, judgmentLoading:false, judgmentLoadedAt:0, knowledgeOverview:null, knowledgeOverviewAt:0, knowledgeGraph:null, knowledgeGraphAt:0, knowledgeGraphStatus:null, providerConfig:null, providerConfigAt:0, providerStatus:null, providerStatusAt:0, providerModels:[], providerModelsAt:0, judgmentModel:"", llmModel:"", sketchFormEdits:{}, autoJudgmentAfterSegmentation:false,substep:"segmentation"};

function node(tag, attributes={}, children=[]) {
  const item = document.createElement(tag);
  for (const [key,value] of Object.entries(attributes)) {
    if (value === undefined || value === null) continue;
    if (key === "text") item.textContent = String(value);
    else if (key === "html") item.innerHTML = String(value);
    else if (key === "class") item.className = value;
    else if (key.startsWith("on")) item.addEventListener(key.slice(2).toLowerCase(),value);
    else if (["checked","disabled","selected","hidden"].includes(key)) item[key]=Boolean(value);
    else item.setAttribute(key,String(value));
  }
  for (const child of (Array.isArray(children) ? children : [children])) if (child !== null && child !== undefined) item.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return item;
}
function replace(target,children) { const element = typeof target === "string" ? $(target) : target; element.replaceChildren(...(Array.isArray(children)?children:[children])); }
function basename(name) { return String(name || "").replaceAll("\\","/").split("/").pop(); }
function format(value, digits=2) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "� : new Intl.NumberFormat("zh-CN", {maximumFractionDigits:digits}).format(Number(value)); }
function date(value) { if(!value) return ""; const d=new Date(value); return Number.isNaN(d.valueOf()) ? String(value) : d.toLocaleString("zh-CN",{hour12:false}); }
function shortId(value) { return String(value || "�).slice(0,12); }
function currentSpec() { return stageSpecs.find(stage=>stage.id===state.stage); }
function currentStage() { return state.task?.stages?.[state.stage] || {status:"pending",artifacts:[],data:{}}; }
function busy() { return state.operation || Boolean(state.task?.busy) || Boolean(state.job); }
function showError(error) { $("error-text").textContent=error?.message || String(error); $("error-dialog").hidden=false; }
let toastTimer;
function toast(message) { $("toast").textContent=message; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{$("toast").hidden=true;},4500); }
async function api(path,options={}) {
  const response=await fetch(path,{cache:"no-store",...options,headers:{...(options.body instanceof FormData ? {} : {"Content-Type":"application/json"}),...(options.headers || {})}});
  if (!response.ok) {
    const value=await response.json().catch(()=>({detail:response.statusText}));
    const detail=value.detail ?? value.error ?? value.message ?? value;
    const message=typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map(item=>`${(item.loc || []).join(".")}: ${item.msg || JSON.stringify(item)}`).join("\n") : JSON.stringify(detail);
    throw new Error(`HTTP ${response.status} · ${message}`);
  }
  return response.status===204 ? null : response.json();
}
function basePath() { return `/api/tasks/${encodeURIComponent(state.task.id)}`; }
function setPill(id,status,text) { const pill=$(id); pill.className=`status-pill ${status}`; pill.textContent=text || labels[status] || status; }
function allStages() { return Object.values(state.task?.stages || {}); }
function allArtifacts(includeSelected=true) {
  const collected=[];
  for (const stage of allStages()) if (goodStatuses.has(stage.status)) collected.push(...(stage.artifacts || []));
  if (includeSelected) collected.push(...(state.historical?.artifacts || currentStage().artifacts || []));
  const unique=new Map(); for (const artifact of collected) unique.set(artifact.id || artifact.url,artifact); return [...unique.values()];
}
function selectedArtifacts() { return state.historical?.artifacts || currentStage().artifacts || []; }
function imageArtifact(artifact) { return String(artifact.media_type || "").startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(artifact.name || ""); }
function lookupImage(names, artifacts=allArtifacts(false)) { for(const name of names) { const found=artifacts.find(item=>basename(item.name)===name && imageArtifact(item)); if(found) return found; } return null; }
function currentData() {
  // Each historical snapshot is self-contained. A historic export embeds its
  // assembled PipelineResult; historic sketches use their immutable PNG only.
  if(state.historical){const own=state.historical.data || {};return {...(own.result || {}),...own};}
  const data={};
  for(const stage of allStages()) if(goodStatuses.has(stage.status)) Object.assign(data,stage.data || {});
  if(state.result && !state.historical) for(const key of ["image","face","lithology_regions","fractures","statistics","topology","algorithms","quality"]) if(data[key]===undefined && state.result[key]!==undefined) data[key]=state.result[key];
  if(currentStage().data) Object.assign(data,currentStage().data);
  return data;
}
function selectedWarnings() {
  const chosen=state.historical || currentStage();
  const warnings=[...(chosen.warnings || []),...(chosen.data?.warnings || [])];
  if(chosen.error) warnings.unshift(`阶段执行失败�{chosen.error}`);
  if(!state.historical && currentStage().status==="stale") warnings.unshift("此结果已过期：输入或参数已改变，当前不提供有效图像与统计。请重新运行；历史结果可通过执行记录下拉框回查�);
  const face=currentData().face;
  if(face && (!face.verified || face.status==="full_image_fallback" || face.method==="full_image_fallback")) warnings.unshift(face.status==="full_image_fallback" || face.method==="full_image_fallback" ? "已使用整图回退 full_image_fallback。掌子面边界未验证，设备、衬砌和背景可能参与统计；当前结果均为候选结果� : "自动掌子面范围尚未验证，区域内可能存在设备、衬砌或其他遮挡，当前统计属于候选结果�);
  return [...new Set(warnings.map(String))];
}

class ImageViewer {
  constructor(prefix) {
    this.prefix=prefix; this.box=$(`${prefix}-viewport`); this.img=$(`${prefix}-image`); this.transform=this.img.parentElement; this.empty=this.box.querySelector(".viewer-empty"); this.zoom=1; this.panX=0; this.panY=0; this.url=""; this.loaded=false;
    this.img.addEventListener("load",()=>{this.loaded=true;this.empty.hidden=true;this.fit();this.showSize();if(this.prefix==="output") drawSketch();});
    this.img.addEventListener("error",()=>{this.loaded=false;this.empty.hidden=false;if(this.prefix==="output"){$("output-empty-title").textContent="结果图片读取失败";$("output-empty-description").textContent="请检查本地服务和执行日志，再重新选择该结果�;}});
    this.box.addEventListener("wheel",event=>{if(!this.loaded)return;event.preventDefault();this.zoom=Math.min(24,Math.max(.3,this.zoom*(event.deltaY<0?1.2:1/1.2)));this.apply();updateZoom();},{passive:false});
    this.box.addEventListener("pointerdown",event=>{if(!this.loaded)return;this.drag={x:event.clientX,y:event.clientY,px:this.panX,py:this.panY};this.box.setPointerCapture(event.pointerId);});
    this.box.addEventListener("pointermove",event=>{if(!this.drag)return;this.panX=this.drag.px+event.clientX-this.drag.x;this.panY=this.drag.py+event.clientY-this.drag.y;this.apply();});
    const release=()=>{this.drag=null;};this.box.addEventListener("pointerup",release);this.box.addEventListener("pointercancel",release);
    new ResizeObserver(()=>this.apply()).observe(this.box);
  }
  set(url) {
    if(this.url===url)return;this.url=url || "";this.loaded=false;this.zoom=1;this.panX=0;this.panY=0;this.empty.hidden=false;
    if(!url){this.img.removeAttribute("src");this.img.style.display="none";this.showSize();return;}
    this.img.style.display="block";this.img.src=url;
  }
  fit() {this.zoom=1;this.panX=0;this.panY=0;this.apply();updateZoom();}
  apply() {if(!this.loaded)return;const fit=Math.min(this.box.clientWidth/this.img.naturalWidth,this.box.clientHeight/this.img.naturalHeight);this.transform.style.width=`${this.img.naturalWidth}px`;this.transform.style.height=`${this.img.naturalHeight}px`;this.transform.style.transform=`translate(-50%,-50%) translate(${this.panX}px,${this.panY}px) scale(${fit*this.zoom})`;}
  showSize() {$(`${this.prefix}-dimensions`).textContent=this.loaded ? `${this.img.naturalWidth} × ${this.img.naturalHeight} px` : "�;}
}
const inputViewer=new ImageViewer("input"), outputViewer=new ImageViewer("output");
function updateZoom() {$("zoom-label").textContent=`${Math.round(outputViewer.zoom*100)}%`;}

function renderNavigation() {
  replace("stage-nav",stageSpecs.map((spec,index)=>{
    const status=spec.id==="original" ? (state.task?"completed":"pending") : spec.id==="lithology_segmentation" && state.judgment?.status && state.task?.stages?.[spec.id]?.status === "completed" ? state.judgment.status : state.task?.stages?.[spec.id]?.status || "pending";
    return node("button",{class:`stage-nav-button${state.stage===spec.id?" active":""}`,"data-testid":`stage-${spec.id}`,"data-stage":spec.id,"aria-current":state.stage===spec.id?"step":undefined,onclick:()=>selectStage(spec.id)},[
      node("span",{class:"stage-number",text:String(index).padStart(2,"0")}),node("span",{class:"stage-nav-copy"},[node("span",{class:"stage-nav-title",text:spec.title}),node("div",{class:`stage-nav-status ${status}`,text:spec.id==="original"&&state.task?"原始文件已保�:labels[status] || status})])
    ]);
  }));
}
function renderTaskHeader() {
  const task=state.task;
  $("open-tunnel").href=task ? `/tunnel.html?task=${encodeURIComponent(task.id)}` : "/tunnel.html";
  $("photo-name").textContent=task?.filename || "尚未加载照片";
  $("photo-name").title=task?.filename || "";
  $("photo-meta").textContent=task ? `${task.width} × ${task.height} px  ·  TASK ${shortId(task.id)}  ·  SHA256 ${String(task.sha256 || "").slice(0,12)}…` : "上传 JPG / PNG，开始一次新的识别任�;
  $("photo-meta").title=task ? `任务 ID�{task.id}\n文件 SHA256�{task.sha256}` : "";
  if(!task)setPill("overall-status","pending","等待输入");
  else if(busy())setPill("overall-status","running","任务处理�);
  else if(allStages().some(item=>item.status==="failed"))setPill("overall-status","failed","部分阶段失败");
  else if(allStages().some(item=>item.status==="stale"))setPill("overall-status","stale","存在过期结果");
  else if(allStages().filter(item=>goodStatuses.has(item.status)).length>=8)setPill("overall-status","warning","处理完成 · 候选成�);
  else setPill("overall-status","pending","可逐步执行");
  $("config-revision").textContent=state.historical?`REV ${state.historical.config_revision ?? "�} · 历史`:`REV ${task?.config_revision ?? "�}`;
}
function renderHistory() {
  const records=(state.task?.history || []).filter(item=>item.stage_id===state.stage || item.stage===state.stage);
  const select=$("execution-select"), old=state.historyId;
  replace(select,[node("option",{value:"",text:"当前配置 / 最新结�}),...records.slice().reverse().map(record=>node("option",{value:record.execution_id,text:`${date(record.created_at).slice(5)} · ${labels[record.status] || record.status} · ${shortId(record.execution_id)}`}))]);
  if(old && [...select.options].some(item=>item.value===old))select.value=old;
  select.disabled=!state.task || busy();
}
function renderHeading() {
  const spec=currentSpec(),index=stageSpecs.indexOf(spec), stage=currentStage();
  $("stage-eyebrow").textContent=`${String(index).padStart(2,"0")} / ${spec.en}`;
  $("stage-title").textContent=spec.title;$("stage-description").textContent=spec.description;
  setPill("stage-status",state.stage==="original"?(state.task?"completed":"pending"):state.historical?.status || stage.status);
  const warnings=selectedWarnings();$("quality-banner").hidden=!warnings.length;$("quality-banner").textContent=warnings.slice(0,3).join("\n");
  $("historical-banner").hidden=!state.historical;$("historical-banner").textContent=state.historical ? `正在查看历史执行 ${state.historyId} · ${date(state.historical.created_at)}。图像与数据属于当次执行，参数编辑已暂停。返回「当前配�/ 最新结果」可继续处理。` : "";
  $("stale-watermark").hidden=Boolean(state.historical) || stage.status!=="stale";
  $("execution-time").textContent=state.stage==="original" ? "原始文件 · 只读" : (state.historical || stage).execution_id ? `耗时 ${format((state.historical || stage).elapsed_seconds,2)} s · ${shortId((state.historical || stage).execution_id)}` : "尚无执行记录";
}
function availableOutputImages() {
  let images=selectedArtifacts().filter(imageArtifact);
  if(state.stage==="geometry_calculation"&&!state.historical) images=images.length?images:[lookupImage(["vector_overlay.png","trace_overlay.png","fracture_traces_overlay.png","traces_overlay.png"])].filter(Boolean);
  if(state.stage==="result_export" && !state.historical && !images.length && goodStatuses.has(currentStage().status))images=allArtifacts(false).filter(imageArtifact);
  const order=preferredImages[state.stage] || [];
  return images.sort((a,b)=>{const aIndex=order.indexOf(basename(a.name)),bIndex=order.indexOf(basename(b.name));return (aIndex<0?999:aIndex)-(bIndex<0?999:bIndex);});
}
function renderImages() {
  const task=state.task,spec=currentSpec();
  if(spec.custom){
    $("input-name").textContent="原始照片 / 区域裁剪见下方结�;inputViewer.set(task?.original_url || "");
    outputViewer.set("");$("output-label").textContent="判别结果";$("output-empty-title").textContent="岩性判别结果在下方显示";$("output-empty-description").textContent="区域裁剪、候选类别、知识证据和最终判定位于结果概览�;$("artifact-select").replaceChildren(node("option",{value:"",text:"结构化判定结�}));$("sketch-layers").hidden=true;return;
  }
  let input=task?.original_url || "", inputName="原始照片";
  const processed=lookupImage(["preprocessed.png"]),mask=lookupImage(["fracture_mask.png"]);
  if(!state.historical&&["face_detection","lithology_segmentation","fracture_segmentation"].includes(state.stage)&&processed){input=processed.url;inputName="预处理图";}
  if(!state.historical&&state.stage==="trace_extraction"&&mask){input=mask.url;inputName="裂缝 Mask";}
  if(state.historical)inputName="原始照片 · 历史参数见右�;
  $("input-name").textContent=inputName;inputViewer.set(input);
  const choices=availableOutputImages();
  // Historical PNGs are authoritative; never rebuild an old sketch with today's branches.
  const canSketch=state.stage==="sketch_rendering" && !state.historical && goodStatuses.has(currentStage().status) && Boolean(currentData().image);
  const options=[];
  if(state.stage==="original"&&task)options.push(node("option",{value:"original",text:"EXIF 方向统一后的原图"}));
  if(canSketch)options.push(node("option",{value:"layers",text:"可开关图层的叠加视图"}));
  const hasJudgment=state.stage==="lithology_segmentation" && !state.historical && judgmentRegions().some(region=>region.final_judgment || region.candidates?.length);
  if(hasJudgment)options.unshift(node("option",{value:"judgment_annotation",text:"岩性判定标注图"}));
  for(const artifact of choices)options.push(node("option",{value:artifact.id || artifact.url,text:imageLabels[basename(artifact.name)] || basename(artifact.name)}));
  const values=options.map(option=>option.value);
  if(!values.includes(state.outputChoice))state.outputChoice=values[0] || "";
  replace("artifact-select",options.length?options:[node("option",{value:"",text:state.stage==="original"?"等待照片":"尚无输出图像"})]);
  $("artifact-select").value=state.outputChoice;$("artifact-select").disabled=!options.length;
  const selected=choices.find(item=>(item.id || item.url)===state.outputChoice);
  const isLayers=state.outputChoice==="layers";
  const isJudgmentAnnotation=state.outputChoice==="judgment_annotation";
  outputViewer.set(isJudgmentAnnotation ? `${judgmentPath("/annotation")}?run=${encodeURIComponent(state.judgment?.run_id || "latest")}` : isLayers || state.outputChoice==="original" ? task?.original_url : selected?.url);
  $("sketch-layers").hidden=!canSketch || !isLayers;$("sketch-canvas").hidden=!isLayers;
  $("save-image").disabled=!state.outputChoice;
  $("output-label").textContent=isJudgmentAnnotation?"岩性判定标注图":isLayers?"图层叠加视图":"当前步骤结果";
  renderJudgmentLegend(isJudgmentAnnotation);
  if(!state.outputChoice){
    const stage=currentStage();$("output-empty-title").textContent=stage.status==="failed"?"当前步骤运行失败":stage.status==="running"?"Python 正在处理图像":state.stage==="result_export"?"成果包尚未生�:"当前步骤尚无图像输出";
    $("output-empty-description").textContent=stage.error || (state.stage==="geometry_calculation"?"统计数据在下方展示；先运行迹线识别可查看矢量叠加图�:state.task?`点击左侧「运行当前步骤」，自动准备所需前置结果。` : "上传照片后，按左侧流程逐步运行");
  }
  drawSketch();
}
function renderJudgmentLegend(visible) {
  const target=$("judgment-legend");
  if(!target)return;
  if(!visible || state.stage!=="lithology_segmentation" || !judgmentRegions().length){target.hidden=true;target.replaceChildren();return;}
  const palette=["#2a91a2","#de8e2c","#68984b","#9160a4"];
  target.hidden=false;
  const items=judgmentRegions().map((region,index)=>{
    const final=region.final_judgment || {};
    const candidate=(region.candidates || [])[0] || {};
    const label=final.final_lithology || final.category || candidate.lithology || candidate.label || "未判�;
    const status=judgmentStatusLabel(final.conclusion_status || final.status || region.classification_status);
    return node("div",{class:"judgment-legend-item"},[
      node("i",{class:"judgment-legend-swatch",style:`background:${palette[index%palette.length]}`}),
      node("strong",{text:region.region_id || `区域 ${index+1}`}),
      node("span",{text:label}),
      node("small",{text:status})
    ]);
  });
  target.replaceChildren(node("div",{class:"judgment-legend-title"},[node("strong",{text:"岩性判定图�}),node("span",{text:"颜色对应区域编号与当前判�})]),node("div",{class:"judgment-legend-items"},items));
}
function color(index) {return `hsl(${(index*137.508)%360} 66% 53%)`;}
function drawSketch() {
  const canvas=$("sketch-canvas");if(canvas.hidden || !outputViewer.loaded)return;
  const img=outputViewer.img,data=currentData();
  if(canvas.width!==img.naturalWidth || canvas.height!==img.naturalHeight){canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;}
  const ctx=canvas.getContext("2d");ctx.clearRect(0,0,canvas.width,canvas.height);
  const enabled={};document.querySelectorAll("[data-layer]").forEach(control=>enabled[control.dataset.layer]=control.checked);
  const width=Math.max(2,canvas.width/700),fontSize=Math.max(14,canvas.width/100);ctx.lineJoin="round";ctx.lineCap="round";
  function polygons(items) {
    ctx.beginPath();for(const polygon of items || [])for(const ring of [polygon.exterior || [],...(polygon.holes || [])]){if(!ring.length)continue;ctx.moveTo(...ring[0]);for(const point of ring.slice(1))ctx.lineTo(...point);ctx.closePath();}
  }
  if(enabled.lithology)for(const [index,region] of (data.lithology_regions || []).entries()){polygons(region.polygon);ctx.fillStyle=color(region.label_value || index+1);ctx.globalAlpha=.22;ctx.fill("evenodd");ctx.globalAlpha=.7;ctx.strokeStyle=color(region.label_value || index+1);ctx.lineWidth=width*.7;ctx.stroke();ctx.globalAlpha=1;}
  if(enabled.face && data.face){polygons(data.face.polygon);ctx.strokeStyle="#37eea2";ctx.lineWidth=width;ctx.stroke();}
  if(enabled.fractures){ctx.strokeStyle="#ff454c";ctx.lineWidth=width;for(const trace of data.fractures || []){const points=trace.display_points || trace.points || [];if(points.length<2)continue;ctx.beginPath();ctx.moveTo(...points[0]);for(const point of points.slice(1))ctx.lineTo(...point);ctx.stroke();}}
  if(enabled.labels){ctx.font=`${fontSize}px sans-serif`;ctx.lineWidth=Math.max(2,fontSize/6);ctx.strokeStyle="#19323b";ctx.fillStyle="white";
    const longest=[...(data.fractures || [])].sort((a,b)=>b.length_px-a.length_px).slice(0,80);
    if(enabled.fractures)for(const trace of longest){const points=trace.display_points || trace.points || [],point=points[Math.floor(points.length/2)];if(point){ctx.strokeText(trace.id,point[0]+4,point[1]);ctx.fillText(trace.id,point[0]+4,point[1]);}}
    if(enabled.lithology)for(const region of data.lithology_regions || []){const points=region.polygon?.[0]?.exterior || [];if(!points.length)continue;const x=points.reduce((sum,p)=>sum+p[0],0)/points.length,y=points.reduce((sum,p)=>sum+p[1],0)/points.length;ctx.strokeText(region.region_id,x,y);ctx.fillText(region.region_id,x,y);}
  }
  ctx.font=`${Math.max(13,fontSize*.8)}px sans-serif`;const lines=["CANDIDATE · 原图坐标 / 图像分区 unknown", "绿：候�ROI  /  彩色：候选分� /  红：候选迹�,state.task?.config?.calibration?.meters_per_pixel ? `标定�{state.task.config.calibration.meters_per_pixel} m / original px（平面近似）` : "尺度：像�· 图像方向不代表地质产�];
  const lh=Math.max(20,fontSize*1.15);ctx.fillStyle="#15333cbb";ctx.fillRect(0,0,canvas.width,lh*lines.length+18);ctx.fillStyle="white";lines.forEach((line,index)=>ctx.fillText(line,12,(index+1)*lh));
}

function resolveSchema(schema) {
  if(schema?.$ref)return state.schema.$defs?.[schema.$ref.split("/").pop()] || schema;
  if(schema?.anyOf){const item=schema.anyOf.find(value=>value.type!=="null");return {...resolveSchema(item),nullable:schema.anyOf.some(value=>value.type==="null"),default:schema.default};}
  return schema || {};
}
function fieldValue(section,key,field) {const config=state.historical?.config || state.historical?.config_snapshot || state.draft;const value=config?.[section]?.[key];return value===undefined?field.default:value;}
function markDirty() {state.dirty=true;$("dirty-note").hidden=false;renderControls();}
function judgmentPath(suffix="") { return `${basePath()}/lithology-judgment${suffix}`; }
function unwrapJudgment(value) { return value?.judgment || value?.state || value || {}; }
function cacheFresh(at, ttl=120000) { return Boolean(at) && Date.now()-at < ttl; }
// The step must paint immediately. Only the judgement state is required to
// render; knowledge counts, provider config/status and the model list are
// secondary, fetched in parallel and cached so repeat clicks stay instant.
async function loadJudgment({force=false}={}) {
  if(!state.task){state.judgmentLoading=false;return;}
  const taskId=state.task.id;
  state.judgmentLoading=true;
  try {
    const next=unwrapJudgment(await api(judgmentPath("/state")));
    if(state.task?.id!==taskId)return;
    state.judgment=next;state.judgmentLoadedAt=Date.now();
  } catch(error) {
    if(state.task?.id!==taskId)return;
    state.judgment={status:"unconfigured",regions:[],warnings:[`岩性判别服务尚未初始化�{error.message}`]};
  } finally {
    if(state.task?.id===taskId)state.judgmentLoading=false;
  }
  const jobs=[];
  if(force || !cacheFresh(state.knowledgeOverviewAt))jobs.push(api("/api/knowledge/overview").then(value=>{state.knowledgeOverview=value;state.knowledgeOverviewAt=Date.now();}).catch(()=>{}));
  if(force || !cacheFresh(state.providerConfigAt,600000))jobs.push(api("/api/lithology-judgment/config").then(value=>{state.providerConfig=value.providers?.default || {};state.providerConfigAt=Date.now();}).catch(()=>{}));
  if(force || !cacheFresh(state.providerStatusAt,60000))jobs.push(api("/api/lithology-judgment/provider-status").then(value=>{state.providerStatus=value.providers?.default || null;state.providerStatusAt=Date.now();}).catch(()=>{}));
  if(force || !cacheFresh(state.providerModelsAt,600000))jobs.push(api("/api/lithology-judgment/models/default").then(value=>{state.providerModels=value.models || [];state.providerModelsAt=Date.now();}).catch(()=>{state.providerModels=[];}));
  if(jobs.length)await Promise.all(jobs);
  if(state.task?.id!==taskId)return;
  if(state.stage==="lithology_segmentation" && judgmentRegions().some(region=>region.final_judgment || region.candidates?.length)){
    state.outputChoice="judgment_annotation";
  }
}
function judgmentRegions() { return Array.isArray(state.judgment?.regions) ? state.judgment.regions : []; }
function judgmentConfig() {
  const provider=state.providerConfig || state.judgment?.config?.provider;
  return {...(state.judgment?.config || {}), ...(provider ? {provider} : {})};
}
function judgmentLabel(value) { return value===null||value===undefined||value==="" ? "未判� : String(value); }
function judgmentStatusLabel(value) { return {unconfigured:"未配�,pending:"待执�,running:"运行�,completed:"已完�,warning:"完成 · 需核验",needs_review:"需要核�,failed:"运行失败",unavailable:"不可�,candidate_only:"候�· 需复核",insufficient_evidence:"证据不足",input_unavailable:"区域过小",needs_provider:"需配置大模�,stale:"来源已过�,model_not_implemented:"模型未接�,confirmed:"已确�,provisional:"暂定",conflict:"结论冲突",inconclusive:"证据不足",no_candidates:"无候�}[value] || labels[value] || value || "未执�; }
function judgmentEvidence(region) { return region.knowledge_evidence || region.evidence || region.knowledge || []; }
function judgmentEvidenceCount(value) { if(Array.isArray(value))return value.length; if(value&&typeof value==="object")return Object.values(value).reduce((sum,item)=>sum+(Array.isArray(item?.records)?item.records.length:Array.isArray(item)?item.length:0),0); return 0; }
function downloadJudgment(path,name) { const anchor=node("a",{href:path,download:name}); document.body.append(anchor); anchor.click(); anchor.remove(); }
function knowledgeBases() {
  const bases=state.knowledgeOverview?.bases || [];
  const rocks=bases.find(item=>item.key==="lithology"), fields=bases.find(item=>item.key==="site");
  return {rocks,fields};
}
function renderKnowledgeInline() {
  const {rocks,fields}=knowledgeBases();
  const rockCount=rocks?.records ?? state.knowledgeGraph?.knowledge_bases?.lithology?.total ?? 0;
  const fieldCount=fields?.records ?? state.knowledgeGraph?.knowledge_bases?.site?.total ?? 0;
  return node("div",{class:"judgment-knowledge-inline kb-inline-open"},[
    node("div",{class:"kb-inline-copy"},[
      node("strong",{text:"岩性判别知识库（已内嵌�}),
      node("p",{text:`知识�B 项目岩性分类库 ${rockCount} 条（�${rocks?.profiled ?? rockCount} 条视觉档案） · 知识�A 掌子面记录表 ${fieldCount} 个字段。`}),
      node("small",{text:"打开后可在当前页面查看岩性分组、视觉特征、字段可获取性分类与关系图谱，不会再跳转到独立页面�})
    ]),
    node("button",{class:"button secondary",type:"button","data-testid":"open-knowledge-graph",onclick:()=>openKnowledgePanel("graph")},"打开知识图谱")
  ]);
}
function kbElement(tag,attributes={},children=[]) { return node(tag,attributes,children); }
function kbProfileBars(profile) {
  const rows=[["亮度","L",profile.L],["彩度","C",profile.C],["纹理粗糙�,"G",profile.G],["颗粒反差","S",profile.S],["定向�,"A",profile.A],["条带�,"B",profile.B],["颗粒尺度","Z",profile.Z]];
  return kbElement("div",{class:"kb-profile"},rows.map(([label,key,value])=>kbElement("div",{class:"kb-profile-row"},[
    kbElement("span",{class:"kb-profile-label",text:`${label} ${key}`}),
    kbElement("span",{class:"kb-profile-track"},[kbElement("i",{style:`width:${Math.round(Math.max(0,Math.min(1,Number(value)||0))*100)}%`})]),
    kbElement("span",{class:"kb-profile-value",text:format(value,2)})
  ])));
}
function kbRockCard(record) {
  const profile=record.visual_profile || {};
  const expanded=state.kb?.selected===record.record_id;
  return kbElement("article",{class:`kb-rock${expanded?" expanded":""}`},[
    kbElement("button",{class:"kb-rock-head",type:"button",onclick:()=>{state.kb.selected=expanded?null:record.record_id;renderKnowledgeBody();}},[
      kbElement("span",{class:"kb-rock-name",text:record.standard_name}),
      kbElement("span",{class:"kb-chip",text:record.rock_group || "未分�}),
      record.subgroup?kbElement("span",{class:"kb-chip ghost",text:record.subgroup}):null,
      kbElement("span",{class:"kb-rock-toggle",text:expanded?"收起":"展开"})
    ]),
    (profile.descriptors || []).length?kbElement("p",{class:"kb-rock-descriptors",text:(profile.descriptors || []).join(" · ")}):null,
    expanded?kbElement("div",{class:"kb-rock-detail"},[
      profile.distinguishing?kbElement("p",{class:"kb-distinguishing",text:`识别要点�{profile.distinguishing}`}):null,
      kbProfileBars(profile),
      (profile.confusable_with || []).length?kbElement("p",{class:"kb-confusable",text:`易混�{profile.confusable_with.join("�)}`}):null,
      kbElement("p",{class:"kb-source",text:`来源�{record.basis || "项目岩性库"}（第 ${record.source_paragraph ?? "�} 段） · 视觉档案依据�{profile.basis || "�}`})
    ]):null
  ]);
}
function kbFieldRow(record) {
  const options=(record.option_values || []).slice(0,6).join("�);
  return kbElement("tr",{},[
    kbElement("td",{},[kbElement("strong",{text:record.field}),kbElement("small",{text:record.record_id})]),
    kbElement("td",{},[kbElement("span",{class:"kb-chip",text:record.obtainability || record.category})]),
    kbElement("td",{text:record.note || "�}),
    kbElement("td",{class:"kb-options",text:options || record.example_value || "�})
  ]);
}
const KB_GRAPH_TYPES = [
  {id:"knowledge_base",label:"知识�,color:"#0b7f86"},
  {id:"ontology",label:"本体",color:"#6f52a3"},
  {id:"ontology_class",label:"本体�,color:"#9273bd"},
  {id:"ontology_property",label:"本体属�,color:"#b08ed0"},
  {id:"category",label:"分类",color:"#c47b2c"},
  {id:"field",label:"记录表字�,color:"#4d8b9c"},
  {id:"rock",label:"岩�,color:"#318b70"},
  {id:"profile",label:"视觉档案",color:"#6d8290"},
  {id:"alias",label:"别名",color:"#a4774a"}
];
const KB_GRAPH_MODES = {
  all:{label:"全部",types:null},
  rocks:{label:"岩性库",types:["knowledge_base","category","rock","profile","alias"]},
  ontology:{label:"记录表本�,types:["knowledge_base","ontology","ontology_class","ontology_property","category","field"]}
};
function kbGraphColor(type) { return (KB_GRAPH_TYPES.find(item=>item.id===type) || {}).color || "#71858d"; }
function kbGraphRadius(node) { return Math.max(3.4, Math.min(12, 3.4 + Math.sqrt(node.__degree || 0) * 1.7)); }
function kbGraphLabel(node) { return String(node.label || node.id || ""); }
function kbGraphTypeLabel(type) { return (KB_GRAPH_TYPES.find(item=>item.id===type) || {}).label || type || "�; }
function knowledgeGraphView() {
  const graph=state.knowledgeGraph;
  if(!graph)return kbElement("p",{class:"kb-empty",text:"图谱尚未加载�});
  if(typeof ForceGraph!=="function"){
    return kbElement("div",{class:"kb-graph-view"},[
      kbElement("p",{class:"kb-hint",text:"力导向图谱组件（vendor/force-graph.min.js）未加载，已回退到内�SVG 布局�}),
      knowledgeGraphFallback(graph)
    ]);
  }
  const nodes=graph.nodes || [], edges=graph.edges || [];
  const signature=`${graph.backend || "local"}:${nodes.length}:${edges.length}`;
  const cached=state.kb.graphView;
  if(cached && cached.dataset.signature===signature && document.body.contains(cached)){
    // Search input and tab switches re-render the body; keep the canvas alive
    // and only refresh highlight/detail instead of restarting the layout.
    refreshKnowledgeGraphView();
    return cached;
  }
  return buildKnowledgeGraphView(graph, signature);
}
function buildKnowledgeGraphView(graph, signature) {
  const nodes=(graph.nodes || []).map(item=>({...item}));
  const nodeIds=new Set(nodes.map(item=>item.id));
  const links=(graph.edges || []).filter(edge=>nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map(edge=>({source:edge.source,target:edge.target,relation:edge.relation || "关联",layer:edge.layer || ""}));
  const degree=new Map(nodes.map(item=>[item.id,0]));
  const neighbours=new Map(nodes.map(item=>[item.id,new Set()]));
  const edgeIndex=new Map(nodes.map(item=>[item.id,[]]));
  for(const link of links){
    degree.set(link.source,(degree.get(link.source) || 0)+1);
    degree.set(link.target,(degree.get(link.target) || 0)+1);
    neighbours.get(link.source).add(link.target);
    neighbours.get(link.target).add(link.source);
    edgeIndex.get(link.source).push({relation:link.relation,direction:"out",other:link.target,layer:link.layer});
    edgeIndex.get(link.target).push({relation:link.relation,direction:"in",other:link.source,layer:link.layer});
  }
  nodes.forEach(item=>{item.__degree=degree.get(item.id) || 0;item.__match=false;});
  const byId=new Map(nodes.map(item=>[item.id,item]));
  const counts={};
  nodes.forEach(item=>{counts[item.type]=(counts[item.type] || 0)+1;});
  const kb=state.kb;
  kb.graphMode=KB_GRAPH_MODES[kb.graphMode] ? kb.graphMode : "all";
  kb.graphHidden=kb.graphHidden instanceof Set ? kb.graphHidden : new Set();
  if(kb.graphLabels===undefined)kb.graphLabels=true;
  kb.graphNodes=nodes;kb.graphLinks=links;kb.graphNeighbours=neighbours;kb.graphEdgeIndex=edgeIndex;kb.graphById=byId;
  kb.graphHover=null;kb.graphFitted=false;
  if(kb.selectedNode && !byId.has(kb.selectedNode))kb.selectedNode=null;

  const stage=kbElement("div",{class:"kb-graph-stage"});
  const host=kbElement("div",{class:"kb-graph-canvas-host"});
  const detail=kbElement("aside",{class:"kb-graph-detail",id:"kb-graph-detail"});
  const summary=kbElement("span",{class:"kb-graph-summary-text",id:"kb-graph-summary"});
  stage.append(host);
  const typeChips=kbElement("div",{class:"kb-graph-types"});
  const modeBar=kbElement("div",{class:"kb-graph-modes"});
  const toolbar=kbElement("div",{class:"kb-graph-toolbar"},[
    modeBar,
    kbElement("div",{class:"kb-graph-actions"},[
      kbElement("button",{class:"kb-graph-button",type:"button",title:"缩放以适应全部节点",onclick:()=>instance.zoomToFit(420,60)},"适应窗口"),
      kbElement("button",{class:"kb-graph-button",type:"button",title:"重新计算力导向布局",onclick:()=>{kb.graphFitted=false;instance.d3ReheatSimulation();}},"重新布局"),
      kbElement("button",{class:"kb-graph-button",type:"button",title:"显示或隐藏节点名�,onclick:event=>{kb.graphLabels=!kb.graphLabels;event.currentTarget.classList.toggle("active",kb.graphLabels);redraw();}},"名称")
    ])
  ]);
  const legend=kbElement("div",{class:"kb-graph-legend"});
  const hint=kbElement("div",{class:"kb-graph-hint",text:"拖拽节点 · 滚轮缩放 · 悬停高亮邻居 · 点击查看详情"});
  stage.append(legend,hint);
  const view=kbElement("div",{class:"kb-graph-view"},[
    kbElement("div",{class:"kb-graph-head"},[
      kbElement("div",{},[
        kbElement("strong",{text:"知识图谱"}),
        summary
      ]),
      kbElement("span",{class:"kb-graph-backend",id:"kb-graph-backend"})
    ]),
    toolbar,
    kbElement("div",{class:"kb-graph-body"},[stage,detail])
  ]);
  view.dataset.signature=signature;

  const visibleType=type=>!kb.graphHidden.has(type);
  const isDimmed=node=>{
    if(kb.graphHover){
      if(node===kb.graphHover)return false;
      const near=kb.graphNeighbours.get(kb.graphHover.id);
      if(near && near.has(node.id))return false;
      return true;
    }
    if(kb.graphSearchActive && !node.__match)return true;
    return false;
  };
  const showLabel=(node,scale)=>{
    if(node===kb.graphHover || node===kb.graphSelected || node.__match)return true;
    if(!kb.graphLabels)return false;
    if(scale>=1.45)return true;
    if(scale>=1.0)return ["knowledge_base","category","ontology_class","field","rock"].includes(node.type);
    if(scale>=0.75)return ["knowledge_base","category","ontology_class"].includes(node.type);
    return node.type==="knowledge_base";
  };
  const paintNode=(node,ctx,scale)=>{
    if(node.x===undefined)return;
    const radius=kbGraphRadius(node);
    const dim=isDimmed(node);
    const selected=node===kb.graphSelected;
    ctx.save();
    ctx.globalAlpha=dim?0.14:1;
    ctx.beginPath();ctx.arc(node.x,node.y,radius,0,Math.PI*2);
    ctx.fillStyle=kbGraphColor(node.type);ctx.fill();
    ctx.lineWidth=(selected?2.2:1.1)/scale;
    ctx.strokeStyle=selected?"#0c3b45":"rgba(255,255,255,.92)";ctx.stroke();
    if(node.__match){
      ctx.beginPath();ctx.arc(node.x,node.y,radius+3.5/scale,0,Math.PI*2);
      ctx.strokeStyle="#e0a12a";ctx.lineWidth=1.6/scale;ctx.stroke();
    }
    if(showLabel(node,scale)){
      const text=kbGraphLabel(node);
      const short=text.length>16?`${text.slice(0,15)}…`:text;
      ctx.font=`${10/scale}px "Segoe UI","Microsoft YaHei",sans-serif`;
      ctx.textAlign="center";ctx.textBaseline="bottom";
      ctx.lineWidth=3/scale;ctx.strokeStyle="rgba(251,253,253,.92)";ctx.lineJoin="round";
      ctx.strokeText(short,node.x,node.y-radius-2.5/scale);
      ctx.fillStyle=selected||node===kb.graphHover?"#123c46":"#31545e";
      ctx.fillText(short,node.x,node.y-radius-2.5/scale);
    }
    ctx.restore();
  };
  const paintPointer=(node,color,ctx)=>{
    const radius=kbGraphRadius(node)+2;
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(node.x,node.y,radius,0,Math.PI*2);ctx.fill();
  };
  const paintLink=link=>{
    if(!visibleType(link.source.type) || !visibleType(link.target.type))return "rgba(0,0,0,0)";
    const hover=kb.graphHover;
    const active=hover && (link.source===hover || link.target===hover);
    if(hover)return active?"rgba(28,94,105,.85)":"rgba(150,170,178,.12)";
    if(kb.graphSearchActive)return "rgba(150,170,178,.2)";
    return link.layer==="instance"?"rgba(120,150,158,.45)":"rgba(150,170,178,.32)";
  };
  const redraw=()=>{
    instance.nodeCanvasObject(paintNode);
    instance.linkColor(paintLink);
    instance.linkWidth(link=>link.source===kb.graphHover || link.target===kb.graphHover?1.5:0.7);
    instance.linkDirectionalArrowLength(link=>link.source===kb.graphHover || link.target===kb.graphHover?4:2.6);
  };
  kb.redraw=redraw;
  const instance=ForceGraph()(host)
    .backgroundColor("rgba(0,0,0,0)")
    .nodeId("id")
    .nodeVal(node=>kbGraphRadius(node))
    .nodeCanvasObject(paintNode)
    .nodePointerAreaPaint(paintPointer)
    .nodeVisibility(node=>visibleType(node.type))
    .linkVisibility(link=>visibleType(link.source.type) && visibleType(link.target.type))
    .linkColor(paintLink)
    .linkWidth(link=>link.source===kb.graphHover || link.target===kb.graphHover?1.5:0.7)
    .linkDirectionalArrowLength(link=>link.source===kb.graphHover || link.target===kb.graphHover?4:2.6)
    .linkDirectionalArrowRelPos(1)
    .linkCurvature(link=>link.layer==="instance"?0.1:0)
    .linkLabel(link=>`${kbGraphLabel(link.source)} �{link.relation}�${kbGraphLabel(link.target)}`)
    .onNodeHover(node=>{
      if(kb.graphHover===node)return;
      kb.graphHover=node;
      host.classList.toggle("hovering",Boolean(node));
      redraw();
    })
    .onNodeClick(node=>{
      kb.selectedNode=node.id;
      kb.selected=node.record?.record_id || null;
      kb.graphSelected=node;
      redraw();
      renderKnowledgeGraphDetail();
      if(node.type==="rock" && node.record)kb.selected=node.record.record_id;
    })
    .onBackgroundClick(()=>{
      kb.selectedNode=null;kb.graphSelected=null;redraw();renderKnowledgeGraphDetail();
    })
    .cooldownTicks(180)
    .warmupTicks(24)
    .d3AlphaDecay(0.022)
    .d3VelocityDecay(0.32)
    .onEngineStop(()=>{
      if(!kb.graphFitted){kb.graphFitted=true;instance.zoomToFit(500,70);}
    });
  kb.graphInstance=instance;
  kb.graphHost=host;
  kb.graphView=view;
  instance.graphData({nodes,links});
  const resize=()=>{
    const width=host.clientWidth || stage.clientWidth || 900;
    const height=host.clientHeight || 520;
    instance.width(width).height(height);
  };
  if(typeof ResizeObserver==="function"){
    kb.graphObserver?.disconnect();
    kb.graphObserver=new ResizeObserver(resize);
    kb.graphObserver.observe(host);
  }
  requestAnimationFrame(()=>{resize();instance.zoomToFit(0,70);});

  const renderModes=()=>{
    modeBar.replaceChildren(...Object.entries(KB_GRAPH_MODES).map(([id,meta])=>
      kbElement("button",{class:`kb-graph-mode${kb.graphMode===id?" active":""}`,type:"button",
        onclick:()=>{kb.graphMode=id;applyMode();refreshKnowledgeGraphView();}} ,meta.label)));
  };
  const applyMode=()=>{
    const meta=KB_GRAPH_MODES[kb.graphMode] || KB_GRAPH_MODES.all;
    kb.graphHidden=new Set(meta.types?KB_GRAPH_TYPES.map(item=>item.id).filter(id=>!meta.types.includes(id)):[]);
  };
  const renderTypeChips=()=>{
    typeChips.replaceChildren(...KB_GRAPH_TYPES.filter(item=>counts[item.id]).map(item=>{
      const hidden=kb.graphHidden.has(item.id);
      return kbElement("button",{class:`kb-graph-type${hidden?" off":""}`,type:"button",
        title:hidden?"点击显示该类�:"点击隐藏该类�,
        onclick:()=>{
          if(hidden)kb.graphHidden.delete(item.id);else kb.graphHidden.add(item.id);
          kb.graphMode="all";renderModes();renderTypeChips();refreshKnowledgeGraphView();
        }},[
        kbElement("i",{style:`background:${item.color}`}),
        kbElement("span",{text:item.label}),
        kbElement("small",{text:String(counts[item.id])})
      ]);
    }));
  };
  kb.renderKnowledgeGraphChrome=()=>{renderModes();renderTypeChips();};
  renderModes();renderTypeChips();
  refreshKnowledgeGraphView();
  requestAnimationFrame(()=>renderKnowledgeGraphDetail());
  return view;
}
function refreshKnowledgeGraphView() {
  const kb=state.kb;
  const instance=kb.graphInstance;
  if(!instance)return;
  const nodes=kb.graphNodes || [], links=kb.graphLinks || [];
  const visible=node=>!(kb.graphHidden instanceof Set && kb.graphHidden.has(node.type));
  const shown=nodes.filter(visible);
  const shownIds=new Set(shown.map(item=>item.id));
  const query=(kb.query || "").trim().toLowerCase();
  kb.graphSearchActive=Boolean(query);
  nodes.forEach(node=>{node.__match=kb.graphSearchActive && `${node.label} ${node.id} ${node.type}`.toLowerCase().includes(query);});
  const shownLinks=links.filter(link=>shownIds.has(link.source.id || link.source) && shownIds.has(link.target.id || link.target));
  const summary=document.getElementById("kb-graph-summary");
  if(summary)summary.textContent=`${shown.length} 个节�· ${shownLinks.length} 条关�{kb.graphSearchActive?` · 匹配 ${nodes.filter(item=>item.__match).length}`:""}`;
  const backend=document.getElementById("kb-graph-backend");
  if(backend)backend.textContent=`${state.knowledgeGraph?.backend==="neo4j"?"Neo4j 投影":"本地 JSON 投影"} · 力导向布局`;
  if(kb.renderKnowledgeGraphChrome)kb.renderKnowledgeGraphChrome();
  instance.nodeVisibility(node=>visible(node));
  instance.linkVisibility(link=>visible(link.source) && visible(link.target));
  kb.redraw?.();
  renderKnowledgeGraphDetail();
}
function renderKnowledgeGraphDetail() {
  const target=document.getElementById("kb-graph-detail");
  if(!target)return;
  const kb=state.kb;
  const node=(kb.graphNodes || []).find(item=>item.id===kb.selectedNode);
  if(!node){
    target.replaceChildren(
      kbElement("p",{class:"kb-hint",text:"点击节点查看详情；悬停节点高亮其邻居�}),
      kbElement("p",{class:"kb-hint",text:`�${(kb.graphNodes || []).length} 个节点�{(kb.graphLinks || []).length} 条关系。`})
    );
    return;
  }
  if(node.type==="rock" && node.record){
    target.replaceChildren(kbRockCard(node.record));
    return;
  }
  const edges=(kb.graphEdgeIndex?.get(node.id) || []).slice(0,12);
  target.replaceChildren(
    kbElement("div",{class:"kb-graph-detail-head"},[
      kbElement("i",{style:`background:${kbGraphColor(node.type)}`}),
      kbElement("strong",{text:kbGraphLabel(node)})
    ]),
    kbElement("p",{class:"kb-hint",text:`类型�{kbGraphTypeLabel(node.type)} · 层级�{node.layer || "�} · 连接 ${(kb.graphEdgeIndex?.get(node.id) || []).length} 条`}),
    kbElement("p",{class:"kb-source",text:`节点 ID�{node.id}`}),
    node.record_id?kbElement("p",{class:"kb-source",text:`记录 ID�{node.record_id}`}):null,
    edges.length?kbElement("div",{class:"kb-graph-rel-list"},edges.map(edge=>kbElement("span",{class:"kb-graph-rel",text:`${edge.direction==="out"?"�:"�} ${edge.relation} · ${kbGraphLabel(kb.graphById.get(edge.other) || {id:edge.other})}`}))):null
  );
}
function knowledgeGraphFallback(graph) {
  // Render the graph returned by the configured graph backend directly.  The
  // layout is intentionally deterministic and dependency-free so the same
  // Neo4j/local graph can be inspected without loading a second visualisation
  // library in the workbench.
  const allNodes=(graph.nodes || []).slice(0,240).map((item,index)=>({...item,_index:index}));
  const nodeById=Object.fromEntries(allNodes.map(item=>[item.id,item]));
  const edges=(graph.edges || []).filter(edge=>nodeById[edge.source] && nodeById[edge.target]).slice(0,600);
  const types=["knowledge_base","ontology","ontology_class","ontology_property","category","field","rock","profile","alias"];
  const palette={knowledge_base:"#0b7f86",ontology:"#6f52a3",ontology_class:"#9273bd",ontology_property:"#b08ed0",category:"#c47b2c",field:"#4d8b9c",rock:"#318b70",profile:"#6d8290",alias:"#a4774a"};
  const grouped=Object.fromEntries(types.map(type=>[type,[]]));
  allNodes.forEach(item=>(grouped[item.type] ||= []).push(item));
  const width=980,height=620,margin=38;
  // Start in type bands, then apply a small force relaxation. This keeps the
  // graph readable while making related nodes move toward one another.
  const positions=new Map();
  const activeTypes=types.filter(type=>(grouped[type]||[]).length);
  activeTypes.forEach((type,col)=>{
    const items=grouped[type]||[], x=margin+(col+0.5)*(width-2*margin)/Math.max(activeTypes.length,1);
    items.forEach((item,row)=>positions.set(item.id,{x,y:margin+(row+0.5)*(height-2*margin)/Math.max(items.length,1)}));
  });
  for(let iteration=0;iteration<48;iteration++){
    const delta=new Map(allNodes.map(item=>[item.id,{x:0,y:0}]));
    for(let i=0;i<allNodes.length;i++)for(let j=i+1;j<allNodes.length;j++){
      const a=positions.get(allNodes[i].id),b=positions.get(allNodes[j].id); if(!a||!b)continue;
      const dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+80,force=900/d2;
      delta.get(allNodes[i].id).x+=dx*force; delta.get(allNodes[i].id).y+=dy*force;
      delta.get(allNodes[j].id).x-=dx*force; delta.get(allNodes[j].id).y-=dy*force;
    }
    edges.forEach(edge=>{const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)return;
      const dx=b.x-a.x,dy=b.y-a.y,d=Math.max(Math.hypot(dx,dy),1),force=(d-72)*0.002;
      delta.get(edge.source).x+=dx/d*force;delta.get(edge.source).y+=dy/d*force;
      delta.get(edge.target).x-=dx/d*force;delta.get(edge.target).y-=dy/d*force;
    });
    positions.forEach((pos,id)=>{const d=delta.get(id);pos.x=Math.max(margin,Math.min(width-margin,pos.x+d.x));pos.y=Math.max(margin,Math.min(height-margin,pos.y+d.y));});
  }
  const selectedNode=allNodes.find(item=>item.id===state.kb?.selectedNode) || allNodes.find(item=>item.record?.record_id===state.kb?.selected);
  const edgeSvg=edges.map(edge=>{const a=positions.get(edge.source),b=positions.get(edge.target);return `<line class="kb-graph-edge" x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"><title>${escapeXml(edge.relation || "关系")}</title></line>`;}).join("");
  const nodeSvg=allNodes.map(item=>{const pos=positions.get(item.id),color=palette[item.type] || "#71858d",selected=selectedNode?.id===item.id;
    const showLabel=["knowledge_base","ontology","ontology_class","category","rock"].includes(item.type);
    return `<g class="kb-node${selected?" selected":""}" data-node-index="${item._index}" transform="translate(${pos.x.toFixed(1)},${pos.y.toFixed(1)})">
      <circle r="${selected?8:6}" fill="${color}" stroke="${selected?"#123f49":"#ffffff"}" stroke-width="${selected?3:1.5}"/><title>${escapeXml(item.label)} · ${escapeXml(item.type)}${item.id?` · ${escapeXml(item.id)}`:""}</title>
      ${showLabel?`<text class="kb-graph-label" x="9" y="4" fill="#294b55">${escapeXml(String(item.label||item.id).slice(0,22))}</text>`:""}</g>`;}).join("");
  const svg=`<svg class="kb-force-graph" viewBox="0 0 ${width} ${height}" role="img" aria-label="Neo4j 知识图谱节点与关�><defs><marker id="kb-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#b5c6cb"/></marker></defs>${edgeSvg}${nodeSvg}</svg>`;
  const fieldNodes=(graph.nodes || []).filter(item=>item.type==="field" && item.layer==="instance");
  const ontologyNodes=(graph.nodes || []).filter(item=>item.layer==="ontology");
  const fieldById=Object.fromEntries(fieldNodes.map(item=>[item.id,item.label]));
  const fieldEdges=(graph.edges || []).filter(item=>item.layer==="instance" && fieldById[item.source] && fieldById[item.target]);
  const ontologySummary=kbElement("div",{class:"kb-ontology-network"},[
    kbElement("div",{class:"kb-ontology-head"},[
      kbElement("strong",{text:"知识�A：本体层 / 实例�}),
      kbElement("span",{text:`本体�${ontologyNodes.length} · 字段实例 ${fieldNodes.length} · 字段关系 ${fieldEdges.length}`})
    ]),
    kbElement("p",{class:"kb-hint",text:"本体层定义记录、字段类别和证据属性；实例层保�28 个字段及字段之间的支持、派生、互补和证据依赖关系�}),
    fieldEdges.length?kbElement("div",{class:"kb-field-relation-list"},fieldEdges.slice(0,80).map(edge=>kbElement("span",{class:"kb-field-relation",text:`${fieldById[edge.source]} �${edge.relation} �${fieldById[edge.target]}`}))):null
  ]);
  const view=kbElement("div",{class:"kb-graph-view"},[
    ontologySummary,
    kbElement("div",{class:"kb-graph-wrap"},[
      kbElement("div",{class:"kb-graph-canvas"},[
        kbElement("div",{class:"kb-graph-summary",text:`${graph.backend === "neo4j" ? "Neo4j 图谱" : "本地 JSON 图谱"} · ${allNodes.length} 个节�· ${edges.length} 条关系`}),
        kbElement("div",{class:"kb-graph-legend"},activeTypes.map(type=>kbElement("span",{},[kbElement("i",{style:`background:${palette[type] || "#71858d"}`}),type]))),
        kbElement("div",{html:svg})
      ]),
      selectedNode?.type==="rock" && selectedNode.record?kbElement("aside",{class:"kb-graph-detail"},[kbRockCard(selectedNode.record)]):selectedNode?kbElement("aside",{class:"kb-graph-detail"},[
        kbElement("strong",{text:selectedNode.label || selectedNode.id}),
        kbElement("p",{class:"kb-hint",text:`类型�{selectedNode.type || "�} · 层级�{selectedNode.layer || "�}`}),
        kbElement("p",{class:"kb-source",text:`节点 ID�{selectedNode.id}`}),
        selectedNode.record_id?kbElement("p",{class:"kb-source",text:`记录 ID�{selectedNode.record_id}`}):null
      ]):kbElement("aside",{class:"kb-graph-detail"},[kbElement("p",{class:"kb-hint",text:"点击节点查看详情；悬停节点可查看 ID 与节点类型�})])
    ])
  ]);
  view.querySelectorAll(".kb-node").forEach(item=>item.addEventListener("click",()=>{
    const graphNode=allNodes[Number(item.dataset.nodeIndex)];
    state.kb.selectedNode=graphNode?.id || null;
    state.kb.selected=graphNode?.record?.record_id || null;
    renderKnowledgeBody();
  }));
  return view;
}
function escapeXml(value) { return String(value ?? "").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char])); }
function renderKnowledgeBody() {
  const body=$("kb-body"); if(!body)return;
  const tab=state.kb.tab, query=(state.kb.query || "").trim().toLowerCase();
  if(!(state.kb.rocks || []).length && !(state.kb.fields || []).length){
    body.replaceChildren(kbElement("p",{class:"kb-empty",text:"正在从本地服务载入知识库�}));
    return;
  }
  const filter=(record)=>{ if(!query)return true; return JSON.stringify(record).toLowerCase().includes(query); };
  if(tab==="rocks"){
    const records=(state.kb.rocks || []).filter(filter);
    const groups=[...new Set((state.kb.rocks || []).map(item=>item.rock_group))];
    body.replaceChildren(
      kbElement("div",{class:"kb-group-chips"},[
        kbElement("button",{class:`kb-chip ${state.kb.group?"":"active"}`,type:"button",onclick:()=>{state.kb.group="";renderKnowledgeBody();}},`全部 ${(state.kb.rocks || []).length}`),
        ...groups.map(name=>kbElement("button",{class:`kb-chip ${state.kb.group===name?"active":""}`,type:"button",onclick:()=>{state.kb.group=name;renderKnowledgeBody();}},`${name} ${(state.kb.rocks || []).filter(item=>item.rock_group===name).length}`))
      ]),
      kbElement("div",{class:"kb-rock-list"},records.filter(record=>!state.kb.group || record.rock_group===state.kb.group).map(kbRockCard)),
      kbElement("p",{class:"kb-footnote",text:"岩性名称与分组来自项目岩性库 DOCX 原文；视觉档案是通用岩石学鉴定要点，不是本项目实测特征�})
    );
    $("kb-count").textContent=`${records.length} / ${(state.kb.rocks || []).length} 条`;
  } else if(tab==="fields"){
    const records=(state.kb.fields || []).filter(filter);
    body.replaceChildren(
      kbElement("table",{class:"kb-table"},[
        kbElement("thead",{},[kbElement("tr",{},["字段","可获取�,"说明与最小来�,"样表示例�].map(text=>kbElement("th",{text})))]),
        kbElement("tbody",{},records.map(kbFieldRow))
      ]),
      kbElement("p",{class:"kb-footnote",text:"可获取性分类来�T01 任务包；管理信息由项目资料带入，影像不能替代现场测试与地质判读�})
    );
    $("kb-count").textContent=`${records.length} / ${(state.kb.fields || []).length} 个字段`;
  } else {
    body.replaceChildren(knowledgeGraphView());
    $("kb-count").textContent=`${(state.knowledgeGraph?.nodes || []).filter(item=>item.type==="rock").length} 个岩性节点`;
  }
}
async function openKnowledgePanel(tab="rocks") {
  state.kb={...(state.kb || {}),tab,query:state.kb?.query || "",group:state.kb?.group || "",
    selected:state.kb?.selected || null,rocks:state.kb?.rocks || [],fields:state.kb?.fields || []};
  let backdrop=document.getElementById("kb-backdrop");
  if(!backdrop){
    backdrop=kbElement("div",{class:"kb-backdrop",id:"kb-backdrop"},[
      kbElement("section",{class:"kb-drawer",role:"dialog","aria-modal":"true","aria-label":"岩性判别知识库"},[
        kbElement("header",{class:"kb-head"},[
          kbElement("div",{},[kbElement("div",{class:"panel-eyebrow",text:"KNOWLEDGE BASE / 知识�}),kbElement("h2",{text:"深圳侨城项目岩性知识库"}),kbElement("p",{id:"kb-meta",text:"正在加载知识库来源与统计�})]),
          kbElement("button",{class:"text-button",type:"button",id:"kb-close",onclick:closeKnowledgePanel},"关闭")
        ]),
        kbElement("div",{class:"kb-tabs",role:"tablist"},[
          kbElement("button",{class:"kb-tab",type:"button","data-kb-tab":"rocks",onclick:()=>switchKnowledgeTab("rocks")},"岩性分类库"),
          kbElement("button",{class:"kb-tab",type:"button","data-kb-tab":"fields",onclick:()=>switchKnowledgeTab("fields")},"记录表字�),
          kbElement("button",{class:"kb-tab",type:"button","data-kb-tab":"graph",onclick:()=>switchKnowledgeTab("graph")},"关系图谱")
        ]),
        kbElement("div",{class:"kb-toolbar"},[
          kbElement("input",{id:"kb-search",type:"search",placeholder:"搜索岩性名称、别名、识别要点或字段",oninput:event=>{state.kb.query=event.target.value;renderKnowledgeBody();}}),
          kbElement("span",{id:"kb-count",class:"kb-count",text:"�}),
          kbElement("span",{id:"kb-backend-status",class:"kb-count",text:"图谱：本�JSON"}),
          kbElement("button",{class:"button secondary",type:"button",onclick:syncKnowledgeGraph},"同步 Neo4j")
        ]),
        kbElement("div",{class:"kb-body",id:"kb-body"})
      ])
    ]);
    document.body.append(backdrop);
    backdrop.addEventListener("click",event=>{if(event.target===backdrop)closeKnowledgePanel();});
    document.addEventListener("keydown",event=>{if(event.key==="Escape" && document.getElementById("kb-backdrop"))closeKnowledgePanel();});
  }
  switchKnowledgeTab(tab);
  const meta=$("kb-meta");
  try {
    const [rocks,fields,overview]=await Promise.all([
      api("/api/knowledge/records?kind=lithology&limit=400"),
      api("/api/knowledge/records?kind=site&limit=60"),
      api("/api/knowledge/overview")
    ]);
    state.kb.rocks=rocks.records || [];
    state.kb.fields=fields.records || [];
    state.knowledgeOverview=overview;state.knowledgeOverviewAt=Date.now();
    // The graph is only needed once the graph tab is opened, and the backend
    // serves the local projection instantly; cache it so reopening is free.
    if(!state.knowledgeGraph || !cacheFresh(state.knowledgeGraphAt,300000)){
      state.knowledgeGraph=await api("/api/knowledge/graph?limit=400").catch(()=>state.knowledgeGraph);
      state.knowledgeGraphAt=Date.now();
    }
    const bases=overview.bases || [];
    const rockBase=bases.find(item=>item.key==="lithology") || {};
    const refs=(rockBase.source_refs || [])[0];
    meta.textContent=`知识�B ${rockBase.records ?? state.kb.rocks.length} 条岩性（${rockBase.profiled ?? 0} 条视觉档案） · 知识�A ${fields.total ?? state.kb.fields.length} 个字�· 构建版本 ${overview.schema_version || "�}${refs?` · 来源 SHA256 ${String(refs.sha256||"").slice(0,12)}…`:""}`;
    const backend=$("kb-backend-status");
    if(backend)backend.textContent=state.knowledgeGraphStatus?.available?"图谱：Neo4j 已连�:"图谱：本�JSON（可同步 Neo4j�;
    // Probing the remote database is a network round trip; never block the
    // drawer on it �the badge updates when the answer arrives.
    api("/api/knowledge/graph/status").then(status=>{
      state.knowledgeGraphStatus=status;
      const badge=$("kb-backend-status");
      if(badge)badge.textContent=status?.available?"图谱：Neo4j 已连�:"图谱：本�JSON（可同步 Neo4j�;
    }).catch(()=>{});
  } catch(error) {
    meta.textContent=`知识库加载失败：${error.message}`;
  }
  renderKnowledgeBody();
}
async function syncKnowledgeGraph() {
  await perform(async()=>{
    const result=await api("/api/knowledge/graph/sync",{method:"POST"});
    state.knowledgeGraphStatus=result;
    state.knowledgeGraph=await api("/api/knowledge/graph?limit=400");
    state.knowledgeGraphAt=Date.now();
    const backend=$("kb-backend-status");
    if(backend)backend.textContent=result.available?`图谱：Neo4j 已同�${result.source_nodes || 0} 节点`:`图谱同步未完成：${result.message || "Neo4j 不可�}`;
    renderKnowledgeBody();
    toast(result.synced?"知识图谱已同步到 Neo4j�:"Neo4j 未连接，当前仍使用本�JSON 图谱�);
  });
}
function switchKnowledgeTab(tab) {
  state.kb.tab=tab;
  document.querySelectorAll("[data-kb-tab]").forEach(button=>button.classList.toggle("active",button.dataset.kbTab===tab));
  const search=$("kb-search");
  if(search)search.placeholder=tab==="fields"?"搜索字段名称、可获取性分类或说明":"搜索岩性名称、别名、识别要点或易混岩�;
  renderKnowledgeBody();
}
function closeKnowledgePanel() {
  const backdrop=document.getElementById("kb-backdrop");
  if(backdrop)backdrop.remove();
}
function judgmentCrop(region) {
  const crop=region.crop_artifact || region.crop || region.crop_url;
  if(!crop)return null;
  if(typeof crop==="string")return crop;
  return crop.url || (crop.artifact_id ? `${basePath()}/artifacts/${encodeURIComponent(crop.artifact_id)}` : null);
}
function renderProviderStatus() {
  const status=state.providerStatus;
  if(!status)return node("p",{class:"judgment-provider-status muted",text:"大模型：尚未测试连通性。配置后点击「测试大模型连通性」记录网关返回�});
  const ok=status.status==="ok";
  const when=status.checked_at?` · ${date(status.checked_at)}`:"";
  return node("p",{class:`judgment-provider-status ${ok?"ok":"failed"}`},
    [`大模型：${ok?"可用":"不可�}${status.http_status?`（HTTP ${status.http_status}）`:""} · ${status.message || "无返回消�}${when}${status.hint?` —�${status.hint}`:""}`]);
}
function judgmentSubstages() {
  const data=state.judgment?.substages;
  if(Array.isArray(data?.substages))return data.substages;
  if(Array.isArray(data))return data;
  return [];
}
function substageStatusLabel(value) {
  return {pending:"未运�,running:"运行�,completed:"已完�,warning:"完成 · 有警�,failed:"失败",
    stale:"输入已变�,blocked:"前置未满�,submitted:"已提�}[value] || value || "未运�;
}
function substageSummary(item) {
  const summary=item.summary || {};
  if(item.id==="segmentation")return summary.region_count!==undefined?`${summary.region_count} 个区域`:(item.provides || "");
  if(item.id==="algorithm_classification"){
    return summary.regions?`${summary.regions} 个区�· ${summary.with_candidates ?? 0} 个有候选`:(item.provides || "");
  }
  if(item.id==="knowledge_llm"){
    return summary.regions?`${summary.regions} 个区�· ${summary.with_llm_result ?? 0} 个获得大模型结论`:(item.provides || "");
  }
  if(item.id==="synthesis"){
    return summary.regions?`已确�${summary.confirmed ?? 0} · 暂定 ${summary.provisional ?? 0} · 需人工 ${summary.needs_human ?? 0}`:(item.provides || "");
  }
  return item.provides || "";
}
async function runSubstage(id) {
  await perform(async()=>{
    await saveConfig();
    const payload={...judgmentPayload(),force:Boolean($("force-run")?.checked)};
    const response=await api(judgmentPath(`/substages/${id}/run`),{method:"POST",body:JSON.stringify(payload)});
    if(response.status==="submitted"){
      state.job=response.job?.job_id || response.job || null;
      toast("已提交岩性区域分割，完成后自动刷新子步骤状态�);
      render();
      await waitForJob(state.job);
      state.job=null;
      await refreshTask();
    }
    await loadJudgment();
    toast(`子步骤�{id}」已执行完成。`);
  });
}
function judgmentModels() {
  return state.judgment?.models || {
    rules_litho_kb_v1:{title:"规则 + 知识库视觉档案（本地可运行）"},
    heuristic_color_texture_v1:{title:"颜色/纹理视觉描述基线"},
    vision_llm:{title:"大模型视觉判别（需配置 API�}
  };
}
function judgmentSegmentationNote() {
  const seg=state.judgment?.segmentation;
  const regions=judgmentRegions();
  if(state.judgment?.segmentation_required)return `尚未运行岩性区域分割。点击下方「分割并判别全部区域」会先分割，再对每个区域执行候选分类、知识库检索与大模型综合（若已配置）。`;
  const ids=regions.map(item=>item.region_id).join("�);
  return `输入来自岩性区域分割成果：${seg?.region_count ?? regions.length} 个区域（${ids}），执行 ${shortId(seg?.execution_id)} · ${seg?.status === "warning" ? "完成（含质量警告� : "已完�}。区域几何取自分割标签图，像素取�EXIF 校正原图。`;
}
const SUBSTEP_META = {
  segmentation:{
    no:"01", title:"岩性分�,
    input:"预处理图 + 掌子面候选范�, output:"区域标签图、区域聚合几何与执行 ID",
    hint:"这一步决定「有哪些区域」。算法与阈值在「算法参数」页调整，判别不会重跑识别算法�
  },
  algorithm_classification:{
    no:"02", title:"算法判定",
    input:"每个分割区域的原图像素（标签图定区域�, output:"岩类概率、细候选概率� 维视觉特�,
    hint:"本地计算，不需要网络。概率是视觉档案相似度的相对支持度，不是统计校准概率�
  },
  knowledge_llm:{
    no:"03", title:"大模型调用知识库",
    input:"算法候�+ 两套知识库检索结�+ 区域裁剪", output:"知识证据、大模型判读原文（或不可用原因）",
    hint:"需要先在右上角「大模型」里配置可用的接口；不可用时本步骤会失败，但前两步结果保留�
  },
  synthesis:{
    no:"04", title:"综合判定",
    input:"算法岩类概率 + 大模型判�, output:"确定结论，或明确标记暂定/冲突/证据不足",
    hint:"只有两路一致才给确定结论；缺大模型时最高只能到「暂定」�
  }
};
function currentSubstep() {
  return SUBSTEP_META[state.substep] ? state.substep : "segmentation";
}
function substepStatusOf(id) {
  const item=judgmentSubstages().find(entry=>entry.id===id);
  return item || {id,status:"pending"};
}
function renderSubsteps() {
  const target=$("substeps-content");
  if(!target)return;
  if(state.stage!=="lithology_segmentation"){
    target.replaceChildren(node("div",{class:"empty-result",text:"本步骤没有子步骤。子步骤仅用于「岩性分割与判定」：岩性分��算法判定 �大模�+ 知识��综合判定�}));
    return;
  }
  if(!state.task){target.replaceChildren(node("div",{class:"empty-result",text:"上传照片并完成前置步骤后，可在这里按子步骤执行�}));return;}
  if(state.judgmentLoading && !state.judgment){
    target.replaceChildren(node("div",{class:"substep-loading",role:"status","aria-live":"polite"},[
      node("span",{class:"substep-spinner"}),
      node("span",{text:"正在读取岩性分割与判别状态�})
    ]));
    return;
  }
  const items=judgmentSubstages();
  const active=currentSubstep();
  const tabs=node("div",{class:"substep-tabs",role:"tablist","aria-label":"岩性子步骤"},Object.keys(SUBSTEP_META).map(id=>{
    const meta=SUBSTEP_META[id], state_=substepStatusOf(id).status;
    return node("button",{class:`substep-tab ${state_}${active===id?" active":""}`,type:"button",role:"tab",
      "aria-selected":String(active===id),"data-substep-tab":id,
      onclick:()=>{state.substep=id;renderSubsteps();}},[
      node("span",{class:"substep-tab-no",text:meta.no}),
      node("span",{class:"substep-tab-body"},[
        node("strong",{text:meta.title}),
        node("small",{text:substageStatusLabel(state_)})
      ])
    ]);
  }));
  target.replaceChildren(
    node("div",{class:"substep-bar"},[
      tabs,
      state.judgmentLoading?node("span",{class:"substep-refreshing",text:"刷新中�}):null,
      node("button",{class:"button primary",type:"button",disabled:busy(),"data-testid":"judgment-run-pipeline",onclick:()=>runPipelineJudgment()},"顺序运行全部")
    ]),
    renderSubstepDetail(active)
  );
  const done=items.filter(item=>["completed","warning"].includes(item.status)).length;
  $("substeps-count").textContent=items.length?` ${done}/${items.length}`:"";
}
function renderSubstepDetail(id) {
  const meta=SUBSTEP_META[id] || SUBSTEP_META.segmentation;
  const item=substepStatusOf(id);
  const blocked=item.status==="blocked";
  const contract=node("div",{class:"substep-contract"},[
    node("div",{class:"contract-row"},[node("span",{class:"contract-label",text:"输入"}),node("p",{text:meta.input})]),
    node("div",{class:"contract-row"},[node("span",{class:"contract-label",text:"输出"}),node("p",{text:meta.output})]),
    node("div",{class:"contract-row"},[node("span",{class:"contract-label",text:"状�}),
      node("p",{},[
        node("span",{class:`substage-status ${item.status}`,text:substageStatusLabel(item.status)}),
        item.summary?node("span",{class:"contract-summary",text:substageSummary(item)}):null
      ])])
  ]);
  const action=node("div",{class:"substep-actions"},[
    node("button",{class:"button primary",type:"button",disabled:busy()||blocked,onclick:()=>runSubstage(id)},
      blocked?"前置未满�:(item.status==="completed"||item.status==="warning"?"重跑本子步骤":"运行本子步骤")),
    node("span",{class:"substep-hint",text:meta.hint})
  ]);
  const body=id==="segmentation"?renderSegmentationSubstep()
    :id==="algorithm_classification"?renderAlgorithmSubstep()
    :id==="knowledge_llm"?renderKnowledgeLLMSubstep()
    :renderSynthesisSubstep();
  return node("div",{class:`substep-detail ${id}`},[contract,action,body]);
}
function renderSegmentationSubstep() {
  const seg=state.judgment?.segmentation || {};
  const regions=judgmentRegions();
  const config=state.draft?.lithology || {};
  const summary=[
    ["执行",shortId(seg.execution_id)],
    ["区域�,format(regions.length,0)],
    ["算法",config.algorithm || "�],
    ["初始类别�,format(config.clusters,0)],
    ["最小分区像�,format(config.min_region_pixels,0)]
  ];
  const algorithm=config.algorithm || "kmeans";
  const configFields=[
    ["algorithm","分割算法",config.algorithm,"select",["kmeans","slic"]],
    ...(algorithm==="kmeans"?[["clusters","初始区域�,config.clusters,"number"]]:[["slic_segments","SLIC 超像素数",config.slic_segments,"number"],["slic_compactness","SLIC 紧致�,config.slic_compactness,"number"]]),
    ["min_region_area_ratio","最小区域占�,config.min_region_area_ratio,"number"],
    ["min_region_pixels","最小区域像�,config.min_region_pixels,"number"],
    ["merge_color_distance","合并颜色距离",config.merge_color_distance,"number"],
    ["spatial_weight","空间权重",config.spatial_weight,"number"],
    ["max_regions","最大区域数",config.max_regions,"number"]
  ];
  const editor=node("div",{class:"substep-parameter-card"},[
    node("div",{class:"substep-parameter-head"},[node("strong",{text:"岩性分割参�}),node("small",{text:"修改后点击本子步骤运行�})]),
    node("div",{class:"substep-parameter-grid"},configFields.map(([key,label,value,type,options])=>{
      const input=type==="select"?node("select",{onchange:event=>{state.draft.lithology[key]=event.target.value;markDirty();renderSubsteps();}},(options||[]).map(option=>node("option",{value:option,text:option}))):node("input",{type:"number",value:value ?? "",step:"any",oninput:event=>{state.draft.lithology[key]=Number(event.target.value);markDirty();}});
      if(type==="select")input.value=value || options[0];
      return node("label",{},[node("span",{text:label}),input]);
    }))
  ]);
  const items=[editor,node("div",{class:"substep-metrics"},summary.map(([label,value])=>node("div",{class:"substep-metric"},[node("span",{text:label}),node("strong",{text:String(value ?? "�)})])))];
  if(seg.status==="warning")items.push(node("p",{class:"result-note",text:"本次分割带有质量警告：自动掌子面范围未验证，区域含设�碴堆干扰时请回看输入图�}));
  if(regions.length){
    items.push(node("div",{class:"region-thumbs"},regions.map(region=>node("figure",{class:"region-thumb"},[
      region.crop_url?node("img",{src:region.crop_url,alt:`${region.region_id} 区域裁剪`,loading:"lazy"}):node("div",{class:"region-thumb-empty",text:"无裁�}),
      node("figcaption",[node("strong",{text:region.region_id}),node("small",{text:`${format(region.pixel_area,0)} px`})])
    ]))));
    items.push(node("p",{class:"result-note",text:"区域几何来自分割标签图，像素取自 EXIF 校正原图；小区域�400 px）在下一步会标为「区域过小」�}));
  }
  return node("div",{class:"substep-body"},items);
}
function renderAlgorithmSubstep() {
  const disabled=busy()||Boolean(state.historical);
  const config=judgmentConfig(), models=judgmentModels();
  const current=config.model_id || state.judgment?.model_id || state.judgment?.default_model || "rules_litho_kb_v1";
  const select=node("select",{id:"judgment-model",disabled,"data-testid":"judgment-model",
    onchange:event=>{state.judgmentModel=event.target.value;}},
    Object.entries(models).map(([id,meta])=>node("option",{value:id,text:meta.title})));
  select.value=models[state.judgmentModel]?state.judgmentModel:(models[current]?current:"rules_litho_kb_v1");
  const rows=judgmentRegions().map(region=>{
    const family=(region.families || [])[0];
    const candidates=(region.candidates || []);
    const features=region.visual_profile || {};
    return [
      region.region_id,
      family?`${family.family} ${format((family.probability ?? 0)*100,0)}%`:"�,
      candidates.map(item=>{
        const name=item.lithology || item.label || item.category || "未命�;
        const probability=(item.probability_in_family ?? item.probability ?? item.score ?? 0);
        return `${name} ${format(probability*100,0)}%`;
      }).join("�) || "�,
      features.L!==undefined?`L ${format(features.L,2)} · 粒度 ${format(features.grain_px,1)} px`:"�,
      judgmentStatusLabel(region.classification_status)
    ];
  });
  return node("div",{class:"substep-body"},[
    node("div",{class:"substep-inline"},[
      node("label",{for:"judgment-model",text:"分类模型"}),
      select,
      node("button",{class:"text-button",type:"button",disabled,onclick:()=>runSubstage("algorithm_classification")},"重跑")
    ]),
    rows.length?table(["区域","岩类（概率）","细候选（概率，族内）","特征摘要","状�],rows):node("div",{class:"empty-result",text:"还没有分割区域，先运�01 岩性分割�}),
    judgmentRegions().length?node("p",{class:"result-note",text:"概率 = 视觉档案相似度经 softmax（温�0.15）归一到目录；同族变种在这张照片上通常不可分，所以先给岩类再给细候选�}):null
  ]);
}
function renderKnowledgeLLMSubstep() {
  const provider=judgmentConfig().provider || {};
  const modelOptions=[...new Set([...(state.providerModels || []),provider.model].filter(Boolean))];
  const modelSelect=node("select",{id:"llm-model-select",disabled:busy()||Boolean(state.historical),onchange:event=>{state.llmModel=event.target.value;}},
    (modelOptions.length?modelOptions:["未配�Model ID"]).map(model=>node("option",{value:model,text:model})));
  modelSelect.value=state.llmModel || provider.model || modelOptions[0] || "";
  const dialogue=judgmentRegions().filter(region=>region.llm_conclusion || region.knowledge_evidence).map(region=>{
    const llm=region.llm_conclusion || {};
    return node("details",{class:"llm-dialog-result"},[
      node("summary",{text:`${region.region_id} · ${llm.status==="completed"?"已返回模型结�:"未完�}`}),
      disclosure("结构化模型返�,llm),
      disclosure("知识库证�,region.knowledge_evidence || {})
    ]);
  });
  const rows=judgmentRegions().map(region=>{
    const llm=region.llm_conclusion || {};
    const evidence=region.knowledge_evidence || {};
    const count=Object.values(evidence).reduce((sum,item)=>sum+((item && item.records) ? item.records.length : 0),0);
    return [
      region.region_id,
      count?`${count} 条`:"�,
      llm.status==="completed"?"已返�:(llm.status || "未运�),
      llm.final_lithology || llm.category || "�,
      (llm.error || llm.reason || "").slice(0,60) || "�
    ];
  });
  return node("div",{class:"substep-body"},[
    node("div",{class:"judgment-grid"},[
      node("div",{class:"judgment-col"},[
        renderProviderCard(),
        node("div",{class:"substep-llm-config"},[
          node("label",{for:"llm-model-select",text:"本次调用模型（Model ID�}),
          modelSelect,
          node("button",{class:"button secondary",type:"button",disabled:busy()||Boolean(state.historical),onclick:()=>saveLLMModel()} ,"保存模型选择")
        ])
      ]),
      node("div",{class:"judgment-col"},[renderKnowledgeInline()])
    ]),
    rows.length?table(["区域","知识命中","大模型状�,"模型判读","说明"],rows):node("div",{class:"empty-result",text:"先运�02 算法判定，本步骤才有可提交的候选�}),
    dialogue.length?node("div",{class:"llm-dialog-list"},[node("h4",{text:"大模型结构化结果与证�}),...dialogue]):null,
    node("p",{class:"result-note",text:"本步骤把区域裁剪、算法候选与知识证据一起提交给 OpenAI 兼容接口；失败原因会原样记录，不会伪造判读�})
  ]);
}
function renderSynthesisSubstep() {
  const regions=judgmentRegions();
  const finals=regions.map(region=>region.final_judgment || {});
  const counts={confirmed:0,provisional:0,conflict:0,inconclusive:0,no_candidates:0};
  finals.forEach(final=>{if(counts[final.conclusion_status]!==undefined)counts[final.conclusion_status]+=1;});
  const rows=regions.map(region=>{
    const final=region.final_judgment || {};
    return [
      region.region_id,
      judgmentLabel(final.final_lithology || final.category),
      judgmentStatusLabel(final.conclusion_status || final.status),
      judgmentLabel(final.confidence_level),
      (final.reason || "�).slice(0,80)
    ];
  });
  return node("div",{class:"substep-body"},[
    node("div",{class:"substep-metrics"},[["已确�,counts.confirmed],["暂定",counts.provisional],["结论冲突",counts.conflict],["证据不足",counts.inconclusive+counts.no_candidates]].map(([label,value])=>node("div",{class:"substep-metric"},[node("span",{text:label}),node("strong",{text:format(value,0)})]))),
    node("div",{class:"judgment-actions"},[
      node("button",{class:"button secondary",type:"button",disabled:busy(),onclick:()=>runSubstage("synthesis")},"重跑综合判定"),
      node("button",{class:"button secondary",type:"button",disabled:busy(),onclick:()=>downloadJudgment(judgmentPath("/annotation"),"lithology_judgment_annotation.png")},"判定�),
      node("button",{class:"button secondary",type:"button",disabled:busy(),onclick:()=>downloadJudgment(judgmentPath("/export"),"lithology_judgment.json")},"导出 JSON")
    ]),
    rows.length?table(["区域","综合判定","结论状�,"置信","依据"],rows):node("div",{class:"empty-result",text:"先运�02�3，再做综合判定�}),
    node("p",{class:"result-note",text:"规则：大模型结论与算法岩类一��已确认；不一��结论冲突；只有算法且岩类概率 �0% �暂定；否则证据不足�})
  ]);
}
function renderProviderCard() {
  const provider=judgmentConfig().provider || {};
  const status=state.providerStatus;
  const statusText=status?`${status.status==="ok"?"可用":"不可�}${status.http_status?`（HTTP ${status.http_status}）`:""} · ${status.message || ""}`:"尚未测试连通�;
  return node("div",{class:"provider-card"},[
    node("div",{class:"provider-card-head"},[
      node("strong",{text:"大模型接�}),
      node("button",{class:"text-button",type:"button",onclick:openProviderPanel},"配置")
    ]),
    node("p",{class:"provider-card-model",text:`模型�{provider.model || "未配�} · 鉴权�{provider.auth_style || "bearer"} · Key ${provider.api_key_configured ? "已配� : "未配�}`}),
    node("p",{class:`provider-card-status ${status?.status || "none"}`,text:statusText}),
    status?.hint?node("small",{class:"provider-card-hint",text:status.hint}):null
  ]);
}
async function openProviderPanel() {
  try {
    const [config,status]=await Promise.all([
      api("/api/lithology-judgment/config"),
      api("/api/lithology-judgment/provider-status")
    ]);
    state.providerConfig=config.providers?.default || {};
    state.providerConfigAt=Date.now();
    state.judgment={...(state.judgment || {}),config:{...(state.judgment?.config || {}),provider:state.providerConfig}};
    state.providerStatus=status.providers?.default || state.providerStatus;
  } catch { /* keep whatever is already loaded */ }
  const provider=judgmentConfig().provider || {};
  let backdrop=document.getElementById("provider-backdrop");
  if(backdrop){backdrop.remove();}
  backdrop=node("div",{class:"kb-backdrop",id:"provider-backdrop"},[
    node("section",{class:"kb-drawer provider-drawer",role:"dialog","aria-modal":"true","aria-label":"大模型接口配�},[
      node("header",{class:"kb-head"},[
        node("div",{},[node("div",{class:"panel-eyebrow",text:"MODEL PROVIDER / 大模型接�}),
          node("h2",{text:"OpenAI 兼容接口配置"}),
          node("p",{text:"Key 只保存在本机后端�lithology-judgment/providers.json），读取接口只返回是否已配置。代理留空表示直连——本机若开着 Clash 等系统代理，出口 IP 会变，校园网关会按「网络环境不支持」拒绝。判别流程在接口不可用时仍会给出算法概率�})]),
        node("button",{class:"text-button",type:"button",onclick:()=>document.getElementById("provider-backdrop")?.remove()},"关闭")
      ]),
      node("div",{class:"provider-form"},[
        node("div",{class:"judgment-field"},[node("label",{for:"pv-base",text:"Base URL"}),node("input",{id:"pv-base",type:"url",value:provider.base_url || "",placeholder:"https://agi.tongji.edu.cn/v1"})]),
        node("div",{class:"judgment-field"},[node("label",{for:"pv-model",text:"模型编码（Model ID�}),node("input",{id:"pv-model",value:provider.model || "",placeholder:"DeepSeek-V4.1-Flash"})]),
        node("div",{class:"judgment-field"},[node("label",{for:"pv-embedding-model",text:"向量模型（可选）"}),node("input",{id:"pv-embedding-model",value:provider.embedding_model || "",placeholder:"留空自动寻找 Embedding 模型"})]),
        node("div",{class:"judgment-field"},[node("label",{for:"pv-key",text:"API Key（仅后端保存�}),node("input",{id:"pv-key",type:"password",value:"",placeholder:provider.api_key_configured ? "已配置，留空保持不变" : "未配�})]),
        node("div",{class:"judgment-field"},[node("label",{for:"pv-proxy",text:"代理（留�= 直连�}),node("input",{id:"pv-proxy",type:"text",value:provider.proxy || "",placeholder:"留空表示不经系统代理；如 http://127.0.0.1:7897"})]),
        node("div",{class:"judgment-field"},[node("label",{for:"pv-auth",text:"鉴权方式"}),(()=>{
          const select=node("select",{id:"pv-auth"},[
            node("option",{value:"bearer",text:"Authorization: Bearer（OpenAI 默认�}),
            node("option",{value:"header",text:"api-key 请求头（部分校园网关�}),
            node("option",{value:"api_key",text:"Authorization: Api-Key（同�AGI 网关�})]);
          select.value=provider.auth_style || "bearer";
          return select;
        })()]),
        node("div",{class:"judgment-inline-fields"},[
          node("label",{},["温度",node("input",{id:"pv-temp",type:"number",min:0,max:2,step:.1,value:provider.temperature ?? .1})]),
          node("label",{},["最大输�token",node("input",{id:"pv-max",type:"number",min:128,max:8000,step:1,value:provider.max_tokens ?? 1200})]),
          node("label",{},["Top-p",node("input",{id:"pv-top-p",type:"number",min:0,max:1,step:.05,value:provider.top_p ?? .8})]),
          node("label",{},["推理强度（可选）",node("input",{id:"pv-reasoning",value:provider.reasoning_effort || "",placeholder:"留空使用模型默认"})])
        ]),
        node("div",{class:"judgment-actions"},[
          node("button",{class:"button primary",type:"button",onclick:()=>saveProviderFromDrawer()},"保存配置"),
          node("button",{class:"button secondary",type:"button",onclick:()=>testProviderFromDrawer()},"测试连通�),
          node("button",{class:"button secondary",type:"button",onclick:()=>loadProviderModelsFromDrawer()},"读取模型列表")
        ]),
        node("p",{class:"judgment-provider-status",id:"pv-model-list",text:"模型列表：未读取"}),
        node("p",{class:"judgment-provider-status",id:"pv-status",
          text:state.providerStatus?`最近一次测试：${state.providerStatus.status==="ok"?"可用":"不可�}${state.providerStatus.http_status?`（HTTP ${state.providerStatus.http_status}）`:""} · ${state.providerStatus.message || ""}${state.providerStatus.hint?` —�${state.providerStatus.hint}`:""}`:"尚未测试�})
      ])
    ])
  ]);
  document.body.append(backdrop);
  backdrop.addEventListener("click",event=>{if(event.target===backdrop)backdrop.remove();});
}
async function saveProviderFromDrawer() {
  const body={
    base_url:$("pv-base")?.value.trim() || "",
    model:$("pv-model")?.value.trim() || "",
    embedding_model:$("pv-embedding-model")?.value.trim() || "",
    temperature:Number($("pv-temp")?.value || .1),
    max_tokens:Number($("pv-max")?.value || 1200),
    top_p:Number($("pv-top-p")?.value || .8),
    reasoning_effort:$("pv-reasoning")?.value.trim() || null,
    auth_style:$("pv-auth")?.value || "bearer",
    proxy:$("pv-proxy")?.value.trim() || ""
  };
  const key=$("pv-key")?.value.trim();
  if(key)body.api_key=key;
  await perform(async()=>{
    const result=await api("/api/lithology-judgment/config/default",{method:"PUT",body:JSON.stringify(body)});
    state.providerConfig=result.providers?.default || {};state.providerConfigAt=Date.now();
    state.judgment={...state.judgment,config:{...(state.judgment?.config || {}),provider:state.providerConfig}};
    state.providerModelsAt=0;
    const status=$("pv-status");if(status)status.textContent="配置已保存；API Key 只写入后端�;
    toast("大模型配置已保存�);
  });
}
async function loadProviderModelsFromDrawer() {
  await perform(async()=>{
    const result=await api("/api/lithology-judgment/models/default");
    const target=$("pv-model-list");
    if(target)target.textContent=result.status==="ok" ? `模型列表�{result.count}）：${(result.models || []).join("�) || "�}` : `模型列表读取失败�{result.message || "未知错误"}`;
    toast(result.status==="ok" ? `已读�${result.count} 个模�ID。` : "模型列表读取失败� );
  });
}
async function testProviderFromDrawer() {
  await perform(async()=>{
    const result=await api("/api/lithology-judgment/test-provider/default",{method:"POST",body:JSON.stringify({})});
    state.providerStatus=result;state.providerStatusAt=Date.now();
    const status=$("pv-status");
    if(status)status.textContent=[result.message || "", result.hint || ""].filter(Boolean).join(" —�");
    toast(result.status==="ok" ? "接口连通� : `接口不可用：${result.message || "未知原因"}`);
  });
}
async function saveLLMModel() {
  const provider=judgmentConfig().provider || {};
  const model=$("llm-model-select")?.value || state.llmModel || provider.model || "";
  if(!model){toast("请先读取或填�Model ID�);return;}
  await perform(async()=>{
    const body={base_url:provider.base_url || "",model,temperature:Number(provider.temperature ?? .1),top_p:Number(provider.top_p ?? .8),max_tokens:Number(provider.max_tokens ?? 1200),reasoning_effort:provider.reasoning_effort || null,auth_style:provider.auth_style || "bearer",proxy:provider.proxy || ""};
    const result=await api("/api/lithology-judgment/config/default",{method:"PUT",body:JSON.stringify(body)});
    state.llmModel=model;
    state.providerConfig=result.providers?.default || {};state.providerConfigAt=Date.now();
    state.judgment={...state.judgment,config:{...(state.judgment?.config || {}),provider:state.providerConfig}};
    state.providerModelsAt=0;
    toast(`已保存模�${model}，重�03 大模型调用后生效。`);
  });
}
function judgmentPayload() {
  const regionIds=judgmentRegions().map(region=>region.region_id).filter(Boolean);
  const select=$("judgment-model");
  if(select?.value)state.judgmentModel=select.value;
  // The model select only lives in the「算法判定」pane, so a run triggered from
  // another pane must fall back to the remembered choice, never to "unconfigured".
  const model=state.judgmentModel || judgmentConfig().model_id || state.judgment?.model_id
    || state.judgment?.default_model || "rules_litho_kb_v1";
  return {region_ids:regionIds,model_id:model,provider_config_id:judgmentConfig().provider_config_id || undefined};
}
async function saveJudgmentConfig() {
  const body={base_url:$("judgment-api-base")?.value.trim() || "",model:$("judgment-model-name")?.value.trim() || "",temperature:Number($("judgment-temperature")?.value || .2),max_tokens:Number($("judgment-max-tokens")?.value || 1200)};
  const key=$("judgment-api-key")?.value.trim();
  if(key)body.api_key=key;
  await perform(async()=>{const result=await api("/api/lithology-judgment/config/default",{method:"PUT",body:JSON.stringify(body)});state.providerConfig=result.providers?.default || {};state.providerConfigAt=Date.now();state.judgment={...state.judgment,config:{...(state.judgment?.config || {}),provider:state.providerConfig}};toast("岩性判别配置已保存；API Key 仅由后端保存�);});
}
async function testJudgmentProvider() { await perform(async()=>{const result=await api("/api/lithology-judgment/test-provider/default",{method:"POST",body:JSON.stringify({})});state.providerStatus=result;state.providerStatusAt=Date.now();const detail=result.http_status?`（HTTP ${result.http_status}${result.gateway_code?` / ${result.gateway_code}`:""}）`:"";toast(result.status==="ok"?result.message:`${result.message}${detail}${result.hint?` · ${result.hint}`:""}`);}); }
async function waitForJob(jobId) {
  for(let attempt=0;attempt<240;attempt+=1){
    const job=await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    if(["completed","failed"].includes(job.status)){if(job.status==="failed")throw new Error(job.error || "岩性区域分割失败，请查看该步骤日志�);return job;}
    await new Promise(resolve=>setTimeout(resolve,750));
  }
  throw new Error("等待岩性区域分割超时，请稍后刷新�);
}
async function runPipelineJudgment() {
  await perform(async()=>{
    await saveConfig();
    const payload={...judgmentPayload(),force_segmentation:$("force-run")?.checked || false};
    let response=await api(judgmentPath("/run-pipeline"),{method:"POST",body:JSON.stringify(payload)});
    if(response.status==="segmentation_submitted"){
      state.job=response.job?.job_id || response.job || null;
      toast("已提交岩性区域分割，完成后自动执行判别�);
      render();
      await waitForJob(state.job);
      state.job=null;
      await refreshTask();
      response=await api(judgmentPath("/run-pipeline"),{method:"POST",body:JSON.stringify({...payload,force_segmentation:false})});
    }
    state.judgment=unwrapJudgment(response);
    toast("岩性区域分割与判别已完成：候选、知识证据与判定状态已刷新�);
  });
}
async function runJudgment(path) { await perform(async()=>{state.judgment=unwrapJudgment(await api(judgmentPath(path),{method:"POST",body:JSON.stringify(judgmentPayload())}));toast(path==="/run-all"?"岩性判别已提交�:"岩性判别步骤已完成�);}); }
function renderJudgmentSummary(options={}) {
  const compact=Boolean(options.compact),regions=judgmentRegions(),finalized=regions.filter(region=>region.final_judgment?.category || region.final_judgment?.final_lithology || region.final_category).length,review=regions.filter(region=>region.status==="needs_review" || region.judgment_status==="needs_review" || region.final_judgment?.needs_review).length;
  const familyVotes={};
  for(const region of regions){for(const family of (region.families || []).slice(0,1)){familyVotes[family.family]=(familyVotes[family.family] || 0)+1;}}
  const topFamily=Object.entries(familyVotes).sort((a,b)=>b[1]-a[1])[0];
  const items=compact?[]:[metrics([["分割区域",format(regions.length,0),"�],["岩类候�,topFamily?`${topFamily[0]} × ${topFamily[1]}`:"�,""],["已形成判�,format(finalized,0),"�],["流程状�,judgmentStatusLabel(state.judgment?.status),""]])];
  if(!state.task)return [node("div",{class:"empty-result",text:"上传照片并完成岩性区域分割后，才能执行岩性判别�})];
  if(!regions.length)items.push(node("div",{class:"empty-result",text:"尚未获得岩性候选区域。请先完成“岩性区域分割”，再返回本步骤执行�}));
  if(state.judgment?.warnings?.length)items.push(node("ul",{class:"warning-list"},state.judgment.warnings.map(w=>node("li",{text:w}))));
  if(regions.length)items.push(node("div",{class:"judgment-actions"},[
    node("button",{class:"button secondary",type:"button",disabled:busy(),onclick:()=>downloadJudgment(judgmentPath("/annotation"),"lithology_judgment_annotation.png"),text:"下载岩性判定图"}),
    node("button",{class:"button secondary",type:"button",disabled:busy(),onclick:()=>downloadJudgment(judgmentPath("/export"),"lithology_judgment.json"),text:"导出结构化判�})
  ]));
  if(regions.length)items.push(node("div",{class:"judgment-region-grid"},regions.map(region=>{
    const final=region.final_judgment || {};
    const candidates=region.candidates || region.candidate_labels || [];
    const families=region.families || [];
    const evidence=judgmentEvidence(region);
    const crop=judgmentCrop(region);
    const input=region.input || {};
    const profile=region.visual_profile || {};
    const candidateText=candidates.length?candidates.map(item=>typeof item==="string"?item:`${item.lithology || item.label || item.category || "候�}${item.similarity!==undefined?`（相似度 ${format(item.similarity,2)}）`:""}`).join("�):"未生成候�;
    return node("article",{class:"judgment-region-card"},[
      node("div",{class:"judgment-region-head"},[node("strong",{text:region.region_id || "未知区域"}),node("span",{class:`judgment-badge ${region.status || region.judgment_status || region.classification_status || "pending"}`,text:judgmentStatusLabel(region.classification_status || region.judgment_status || region.status)})]),
      crop?node("img",{src:crop,alt:`${region.region_id} 区域裁剪图`,loading:"lazy"}):node("div",{class:"judgment-crop-empty",text:"暂无裁剪�}),
      node("p",{class:"judgment-input-note",text:`分割来源�{input.segmentation_execution_id?shortId(input.segmentation_execution_id):"当前分割成果"} · 像素 ${format(region.pixel_area,0)} · 区域占外接框 ${input.region_coverage_in_box!==undefined&&input.region_coverage_in_box!==null?`${format(input.region_coverage_in_box*100,0)} %`:"�}`}),
      families.length?node("div",{class:"judgment-families"},families.slice(0,3).map(item=>node("span",{class:"judgment-family",text:`${item.family} ${format((item.probability ?? item.score)*100,0)}%`}))):null,
      node("p",{class:"judgment-candidates",text:`细候选：${candidateText}`}),
      region.candidate_summary?node("p",{class:"judgment-summary-line",text:region.candidate_summary}):null,
      node("p",{class:"judgment-final"},[node("strong",{text:`综合判定�{judgmentLabel(final.final_lithology || final.category || region.final_category)}`}),` · ${judgmentStatusLabel(final.conclusion_status || final.status)} · 置信 ${judgmentLabel(final.confidence_level || final.confidence)}`]),
      final.reason?node("p",{class:"judgment-reason",text:final.reason}):null,
      node("p",{class:"judgment-evidence",text:`知识证据�{judgmentEvidenceCount(evidence)?judgmentEvidenceCount(evidence)+" �:"无匹配证�} · ${final.needs_review===false?"可供复核":"需进一步核�}`}),
      profile.features_version?disclosure("区域视觉特征（来自分割区域像素）",{features_version:profile.features_version,L:profile.L,C:profile.C,G:profile.G,S:profile.S,A:profile.A,B:profile.B,Z:profile.Z,grain_px:profile.grain_px,pixel_count:profile.pixel_count,exposure_reference:profile.exposure_reference,source:profile.source}):null,
      judgmentEvidenceCount(evidence)?disclosure("知识库检索证�,evidence):null,
      final.reason?node("details",{class:"detail-disclosure"},[node("summary",{text:"判定理由与来�}),node("p",{text:final.reason}),disclosure("支持判定证据",final.evidence || evidence)]):null,
      node("div",{class:"judgment-card-actions"},[node("button",{class:"text-button",type:"button",disabled:busy(),onclick:()=>runJudgmentRegion("/classify",region.region_id),text:"重跑分类"}),node("button",{class:"text-button",type:"button",disabled:busy(),onclick:()=>runJudgmentRegion("/run-all",region.region_id),text:"重跑判定"})])
    ]);
  })));
  items.push(disclosure("本次判别运行与配�,{run_id:state.judgment?.run_id || null,status:state.judgment?.status,model_id:judgmentConfig().model_id || null,provider_configured:Boolean(judgmentConfig().provider?.api_key_configured),knowledge_bases:state.judgment?.knowledge_bases || ["现场地质记录","项目岩性分类库"]}));
  return items;
}
async function runJudgmentRegion(path,regionId){await perform(async()=>{state.judgment=unwrapJudgment(await api(judgmentPath(path),{method:"POST",body:JSON.stringify({...judgmentPayload(),region_ids:[regionId]})}));toast(`${regionId} 判别结果已更新。`);});}
function renderJudgmentObjects(){
  const regions=judgmentRegions();
  if(!regions.length)return [node("div",{class:"empty-result",text:"当前没有可查看的岩性判别对象�})];
  return [table(["区域 ID","算法岩类（概率）","细候�,"综合判定","结论状�,"证据�],regions.map(region=>{
    const final=region.final_judgment || {};
    const family=(region.families || [])[0];
    const candidates=(region.candidates || final.candidates || []);
    return [
      region.region_id,
      family?`${family.family} ${format((family.probability ?? 0)*100,0)}%`:"�,
      candidates.map(item=>typeof item==="string"?item:item.lithology || item.label).join("�) || "�,
      judgmentLabel(final.final_lithology || final.category || region.final_category),
      judgmentStatusLabel(final.conclusion_status || final.status || region.classification_status),
      judgmentEvidenceCount(judgmentEvidence(region))
    ];
  }))];
}
function renderParameters() {
  const spec=currentSpec(),section=spec.section;$("parameter-title").textContent=spec.id==="original"?"输入信息":section?spec.title+"参数":"阶段信息";
  if(!state.task || !state.schema){replace("parameter-content",node("div",{class:"parameter-info"},[node("strong",{text:"从一张照片开�}),"上传照片后，这里将显示当前处理步骤的真实算法参数、范围与说明。所有识别均由本�Python 执行�]));renderControls();return;}
  if(!section){
    const text=spec.id==="sketch_rendering"?"绘图使用后端生成的候选区域与迹线；中间图层开关可单独显示不同对象。标注最多显示最长的 80 条迹线，全部坐标保存�JSON�:spec.id==="result_export"?"下载包只包含当前有效结果、当前配置与质量报告。若结果已过期，将先刷新导出阶段。缺少的分支会在报告中标记，不会以历史结果替代�:"原始文件独立存档；页面预览已统一 EXIF 方向。算法使用的原始坐标为方向统一后的照片像素坐标�;
    const info=node("div",{class:"parameter-info"},[node("strong",{text:spec.title}),text]);
    const rows=[["任务 ID",state.task.id],["原始尺寸",`${state.task.width} × ${state.task.height} px`],["创建时间",date(state.task.created_at)],["文件 SHA256",state.task.sha256]];
    replace("parameter-content",[info,...rows.map(([key,value])=>node("div",{class:"info-row"},[node("span",{text:key}),node("span",{class:key==="文件 SHA256"?"code":"",text:value})]))]);renderControls();return;
  }
  const definition=resolveSchema(state.schema.properties?.[section]),fields=definition.properties || {};
  const metadata=state.parameterMeta[section] || {},disabled=busy() || Boolean(state.historical);
  const controls=[];
  if(state.historical)controls.push(node("div",{class:"parameter-info",text:"正在查看历史执行。当前参数仅供参考，请切回当前结果后编辑�}));
  if(section==="calibration")controls.push(node("div",{class:"parameter-info",text:"比例基于方向统一后的原图像素。仅在近似平面与投影条件合理、且确认该条件时填写 m/px；留空则仅输出像素指标�}));
  for(const [key,raw] of Object.entries(fields)){
    const field=resolveSchema(raw),meta=metadata[key] || {},value=fieldValue(section,key,field),defaultValue=state.defaults?.[section]?.[key];
    if(meta.visible_when && !Object.entries(meta.visible_when).every(([dependency,allowed])=>allowed.includes(fieldValue(section,dependency,resolveSchema(fields[dependency])))))continue;
    const wrap=node("div",{class:"parameter-field"}),id=`param-${section}-${key}`;
    const heading=node("div",{class:"parameter-field-head"},[node("label",{for:id,text:meta.label || field.title || key})]);
    if(key!=="algorithm")heading.append(node("span",{class:"parameter-default",text:`默认 ${defaultValue===null?"�:typeof defaultValue==="boolean"?(defaultValue?"�:"�):defaultValue ?? "�}`}));
    wrap.append(heading);
    const row=node("div",{class:"parameter-input-row"});
    const save=value=>{state.draft[section][key]=value;markDirty();};
    let control;
    if(field.type==="boolean"){
      control=node("input",{type:"checkbox",id,checked:Boolean(value),disabled,"data-param":key,"data-testid":id,onchange:event=>save(event.target.checked)});
      row.append(node("label",{class:"parameter-toggle",for:id},[control,node("span",{text:key==="planar_approximation_acknowledged"?"确认平面与投影近似条�: "启用"})]));
    } else if(field.enum){
      control=node("select",{id,disabled,"data-param":key,"data-testid":id,onchange:event=>{save(event.target.value);if(key==="algorithm"||key==="mode")renderParameters();}},field.enum.map(option=>node("option",{value:option,text:meta.options?.[option]?.label || ({auto:"自动候选范�,full_image:"整图分析（未验证�})[option] || option})));control.value=value;row.append(control);
    } else if(["integer","number"].includes(field.type)){
      // JSON Schema allows arbitrary finite floats; a fabricated HTML decimal
      // step must not reject valid values. Strict exclusive bounds stay server-side.
      const min=field.minimum ?? (field.exclusiveMinimum===undefined?undefined:field.exclusiveMinimum+(field.type==="integer"?1:0));
      const max=field.maximum ?? (field.exclusiveMaximum===undefined?undefined:field.exclusiveMaximum-(field.type==="integer"?1:0));
      const step=field.type==="integer"?1:"any";
      control=node("input",{type:"number",id,value:value ?? "",min,max,step,disabled,placeholder:field.nullable?"留空：无标定":"","data-param":key,"data-testid":id});
      control.addEventListener("input",event=>{const text=event.target.value;save(text===""&&field.nullable?null:text===""?"":Number(text));if(slider&&text!=="")slider.value=text;});
      let slider=null;
      if(min!==undefined&&max!==undefined&&max<=100&&key!=="meters_per_pixel"){
        slider=node("input",{type:"range",min,max,step:field.type==="integer"?1:"any",value:value ?? min,disabled,"aria-label":`${meta.label || key} 滑块`,oninput:event=>{control.value=event.target.value;save(Number(event.target.value));}});row.append(slider);
      }
      row.append(control);if(meta.unit)row.append(node("span",{class:"unit",text:meta.unit}));
    } else {
      control=node("input",{type:"text",id,value:value ?? "",disabled,"data-param":key,"data-testid":id,placeholder:field.nullable?"可�:undefined,oninput:event=>save(event.target.value || (field.nullable?null:""))});row.append(control);
    }
    wrap.append(row,node("div",{class:"parameter-key",text:`${section}.${key}`}));
    const min=field.minimum ?? field.exclusiveMinimum,max=field.maximum ?? field.exclusiveMaximum;
    if(min!==undefined||max!==undefined)wrap.append(node("div",{class:"range-note",text:`范围�{min===undefined?"无限�:(field.exclusiveMinimum!==undefined?"> ":"�")+min}${max===undefined?"":"  /  "+(field.exclusiveMaximum!==undefined?"< ":"�")+max}${meta.unit?" "+meta.unit:""}`}));
    if(meta.description || field.description)wrap.append(node("small",{text:meta.description || field.description}));controls.push(wrap);
    if(meta.options?.[value]?.description)wrap.append(node("small",{text:meta.options[value].description}));
  }
  replace("parameter-content",controls);renderControls();
}
function renderControls() {
  const noTask=!state.task,isBusy=busy(),historical=Boolean(state.historical),original=state.stage==="original",section=Boolean(currentSpec().section);
  $("run-stage").disabled=noTask||isBusy||original||historical;$("run-downstream").disabled=noTask||isBusy||original||historical;
  $("run-all").disabled=noTask||isBusy;$("export-top").disabled=noTask||isBusy;
  $("save-config").disabled=noTask||isBusy||historical||!state.dirty;$("restore-defaults").disabled=noTask||isBusy||historical||!section;
  $("force-run").disabled=noTask||isBusy||historical||original;
  $("upload-input").disabled=isBusy;document.querySelector(".upload-button").style.opacity=isBusy?".5":"1";
  $("dirty-note").hidden=!state.dirty;$("run-stage").textContent=isBusy?"处理中，请稍候�:state.stage==="lithology_segmentation"?"�顺序运行全部子步�:"�运行当前步骤";
  $("task-select").disabled=isBusy;
  document.querySelectorAll("#console-body input,#console-body select").forEach(control=>control.disabled=isBusy||historical);
  document.querySelectorAll("[data-download-action]").forEach(control=>control.disabled=noTask||isBusy||historical);
}

function metrics(items) {return node("div",{class:"metric-grid"},items.map(([label,value,unit])=>node("div",{class:"metric"},[node("div",{class:"metric-label",text:label}),node("div",{class:"metric-value"},[value ?? "�,node("span",{class:"metric-unit",text:unit || ""})])])));}
function table(headers,rows) {return node("table",{class:"data-table"},[node("thead",{},node("tr",{},headers.map(text=>node("th",{text})))),node("tbody",{},rows.map(values=>node("tr",{},values.map(value=>node("td",{text:value ?? "�})))))]);}
function disclosure(title,value) {return node("details",{class:"detail-disclosure"},[node("summary",{text:title}),node("pre",{class:"json-block",text:JSON.stringify(value,null,2)})]);}
function renderSketchFormEditor() {
  const form=currentStage().data?.geological_sketch_form || currentData().geological_sketch_form;
  if(!form?.fields?.length)return node("div",{class:"empty-result",text:"当前导出尚未生成 28 字段素描表，请先运行成果导出�});
  const byField=Object.fromEntries(form.fields.map(field=>[field.field,field]));
  const valueOf=name=>{const field=byField[name]||{};return state.sketchFormEdits[field.record_id] ?? field.value ?? "";};
  const inputOf=(name,multiline=false)=>{const field=byField[name]||{};const props={class:`sketch-template-input ${field.status || "candidate_or_review"}`,value:valueOf(name),placeholder:field.status==="needs_external_input"?"需现场/管理资料":"可填写或修改",title:`${name} · ${field.source || "�}`,oninput:event=>{state.sketchFormEdits[field.record_id || name]=event.target.value;}};return node(multiline?"textarea":"input",multiline?{...props,rows:4}:props);};
  const cells=[];
  const add=(label,name,col,row,width=1,height=1,extra="",multiline=false)=>cells.push(node("div",{class:`sketch-template-cell ${extra}`,style:`grid-column:${col} / span ${width};grid-row:${row} / span ${height};`},[label?node("span",{class:"sketch-template-label",text:label}):null,name?inputOf(name,multiline):null]));
  const addBand=(text,col,row,width=1,height=1)=>cells.push(node("div",{class:"sketch-template-section-label",style:`grid-column:${col} / span ${width};grid-row:${row} / span ${height};`,text}));
  add("工程名称","工程名称",1,2,1,1,"sketch-label-cell");add(null,"工程名称",2,2,15);
  add("工程部位","工程部位",1,3,1,1,"sketch-label-cell");add(null,"工程部位",2,3,5);add("试验条件","试验条件",7,3,3,1,"sketch-label-cell");add(null,"试验条件",10,3,7);
  add("委托编号","委托编号",1,4,1,1,"sketch-label-cell");add(null,"委托编号",2,4,5);add("检测日�,"检测日�,7,4,3,1,"sketch-label-cell");add(null,"检测日�,10,4,7);
  add("检测依�,"检测依�,1,5,1,1,"sketch-label-cell");add(null,"检测依�,2,5,5);add("判定依据","判定依据",7,5,3,1,"sketch-label-cell");add(null,"判定依据",10,5,7);
  add("主要仪器设备名称","主要仪器设备名称",1,6,1,1,"sketch-label-cell");add(null,"主要仪器设备名称",2,6,5);add("设备编号","设备编号",7,6,3,1,"sketch-label-cell");add(null,"设备编号",10,6,7);
  addBand("岩性指�,1,7,1,3);add("岩土类型（名称）","岩土类型（名称）",2,7,2,1,"sketch-label-cell");add(null,"岩土类型（名称）",4,7,13);add("岩石硬度","岩石硬度",2,8,2,1,"sketch-label-cell");add(null,"岩石硬度",4,8,13);add("掌子面状�,"掌子面状�,2,9,2,1,"sketch-label-cell");add(null,"掌子面状�,4,9,13);
  addBand("岩体完整状�,1,10,1,7);add("地质构造影响程�,"地质构造影响程�,2,10,2,1,"sketch-label-cell");add(null,"地质构造影响程�,4,10,13,"sketch-check-cell");
  addBand("地质结构",2,11,1,4);add("间距（m�,"间距（m�,3,11,1,1,"sketch-label-cell");add(null,"间距（m�,4,11,13);add("延伸�,"延伸�,3,12,1,1,"sketch-label-cell");add(null,"延伸�,4,12,13);add("粗糙�,"粗糙�,3,13,1,1,"sketch-label-cell");add(null,"粗糙�,4,13,13);add("张开性（mm�,"张开性（mm�,3,14,1,1,"sketch-label-cell");add(null,"张开性（mm�,4,14,13);
  add("风化程度","风化程度",2,15,2,1,"sketch-label-cell");add(null,"风化程度",4,15,13);add("完整状�,"完整状�,2,16,2,1,"sketch-label-cell");add(null,"完整状�,4,16,13);
  add("岩层产状","岩层产状",1,17,1,1,"sketch-label-cell");add(null,"岩层产状",2,17,2);add("围岩级别","围岩级别",4,17,1,1,"sketch-label-cell");add(null,"围岩级别",5,17,12,"sketch-check-cell");
  add("岩体含水状�,"岩体含水状�渗水�,1,18,1,1,"sketch-label-cell");add("渗水量（L/min·10m�,"岩体含水状�渗水�,2,18,2,1,"sketch-label-cell");add(null,"岩体含水状�渗水�,4,18,13);
  addBand("开挖工作面素描图及地质简要描�,1,19,1,2);
  const sketchArtifact=allArtifacts(false).find(item=>basename(item.name)==="geological_sketch.png");
  cells.push(node("div",{class:"sketch-template-cell sketch-image-cell",style:"grid-column:2 / span 8;grid-row:19 / span 2;"},[node("span",{class:"sketch-template-label",text:"开挖工作面素描�}),sketchArtifact?node("img",{src:sketchArtifact.url,alt:"综合地质素描�}):node("div",{class:"sketch-image-empty",text:"运行成果导出后显示综合素描图"})]));
  add("地质简要描�,"地质简要描�,10,19,7,2,"sketch-description-cell",true);
  add("附加声明","附加声明",1,21,16,1,"sketch-footer-cell",true);add("记录�,"记录�,1,22,3,1,"sketch-footer-cell");add("监理�,"监理�,4,22,4,1,"sketch-footer-cell");add("签署日期","签署日期",8,22,9,1,"sketch-footer-cell");
  return node("div",{class:"sketch-form-editor"},[
    node("div",{class:"sketch-form-head"},[
      node("div",{},[node("strong",{text:"掌子面地质素描输出表"}),node("small",{text:"已按项目样表布局展示。系统候选结果已填入；空值表示缺少可靠证据或需要现场资料�})]),
      node("button",{class:"button secondary",type:"button",onclick:()=>downloadSketchFormDraft()} ,"下载当前填写�)
    ]),
    node("div",{class:"sketch-template-scroll"},[node("div",{class:"sketch-template"},[node("div",{class:"sketch-template-title",style:"grid-column:1 / span 16;grid-row:1;",text:"掌子面地质素描记录表"}),...cells])]),
    node("p",{class:"sketch-form-footnote",text:`坐标系：${form.coordinate_system || "original_image_pixels"} · 状态：${form.status || "candidate_form_with_review"} · 素描图、岩性候选和裂隙参数均来自当前有效成果。`})
  ]);
}
function downloadSketchFormDraft() {
  const form=currentStage().data?.geological_sketch_form || currentData().geological_sketch_form;
  if(!form?.fields?.length)return;
  const lines=[["record_id","field","category","obtainability","value","status","source"],...form.fields.map(field=>[
    field.record_id,field.field,field.category,field.obtainability,state.sketchFormEdits[field.record_id] ?? field.value ?? "",field.status,field.source
  ])];
  const csv="\ufeff"+lines.map(row=>row.map(value=>`"${String(value ?? "").replaceAll('"','""')}"`).join(",")).join("\n");
  const blob=new Blob([csv],{type:"text/csv;charset=utf-8"}),url=URL.createObjectURL(blob),anchor=node("a",{href:url,download:"geological_sketch_form_filled.csv"});
  document.body.append(anchor);anchor.click();anchor.remove();URL.revokeObjectURL(url);
}
function renderSummary() {
  const data=currentData(),stage=state.historical || currentStage(),items=[];
  if(!state.task)return [node("div",{class:"empty-result",text:"上传掌子面照片后，处理状态、质量信息和关键统计将在这里显示�})];
  if(state.stage!=="original" && !Object.keys(stage.data || {}).length){
    const reason=state.historical?"该次历史执行未生成有效统计，不能据此判断对象数量为零�:stage.status==="stale"?"当前步骤结果已过期，数量、面积和长度暂不可用。请重新运行，或从执行记录下拉框回查历史成果�:stage.status==="failed"?"当前步骤运行失败，尚无有效统计。请查看错误，调整参数后重新执行�:stage.status==="blocked"?"必要前置条件未满足，当前步骤尚无有效统计。请运行所需前置步骤�:stage.status==="running"?"当前阶段正在本地执行，暂不提供统计值；完成后将自动刷新�:"当前步骤尚未执行。运行时会自动准备必要的前置步骤，并复用有效缓存�;
    return [node("div",{class:"empty-result",text:reason}),...(stage.error?[node("p",{class:"result-note",text:stage.error})]:[])];
  }
  if(state.stage==="original")items.push(metrics([["原始图像宽度",format(state.task.width,0),"px"],["原始图像高度",format(state.task.height,0),"px"],["配置版本",state.task.config_revision ?? 0,"rev"],["阶段执行记录",format(state.task.history?.length || 0,0),"�]]));
  else if(state.stage==="preprocessing"){
    const image=data.image || {};items.push(metrics([["原图宽度",format(image.original_size?.[0],0),"px"],["工作图宽�,format(image.working_size?.[0],0),"px"],["X 缩放比例",format(image.scale_x,5),""],["EXIF 方向处理",image.exif_transposed?"已旋�:"无需旋转",""]]));
    items.push(disclosure("坐标变换与图像元数据",image));
  } else if(state.stage==="face_detection"){
    const face=data.face || {};items.push(metrics([["候选区域面�,format(face.pixel_area,0),"px²"],["图像面积占比",format(face.area_ratio*100),"%"],["人工验证",face.verified?"已验�:"未验�,""],["整图回退",face.status==="full_image_fallback"||face.method==="full_image_fallback"?"�:"�,""]]));items.push(node("p",{class:"result-note",text:`方法�{face.method || "�} · 状态：${face.status || "�}。面积来自原图坐标下 Mask 的精确像素计数。`}));
  } else if(state.stage==="lithology_segmentation"){
    const regions=data.lithology_regions || [];items.push(metrics([["候选区域数�,format(regions.length,0),"�],["分区总像素面�,format(regions.reduce((sum,item)=>sum+item.pixel_area,0),0),"px²"],["面积比例合计",format(regions.reduce((sum,item)=>sum+item.area_ratio,0)*100),"%"],["地质类别","unknown",""]]));items.push(node("p",{class:"result-note",text:"当前为图像特征分区，颜色与纹理差异不等于真实岩性差异。下方判别结果直接使用这些分割区域的裁剪图，不重新读取整张原图�}));
    items.push(node("div",{class:"judgment-inline-results"},[node("h3",{text:"区域岩性判�}),...renderJudgmentSummary()]));
  } else if(state.stage==="fracture_segmentation"){
    const own=stage.data || {},details=own.fracture_details || own.detection || own.algorithms?.fracture?.details || own.details || {};
    const pixels=own.fracture_pixel_count ?? own.fracture_pixel_area ?? own.mask_pixel_count ?? details.fracture_pixel_count ?? details.candidate_pixels ?? details.mask_pixels ?? own.fracture_mask_pixel_area;
    const count=details.kept_components ?? details.retained_components ?? details.components_kept ?? details.candidate_components ?? own.component_count;
    items.push(metrics([["候选裂缝像�,format(pixels,0),"px"],["保留连通区�,format(count,0),"�],["检测阈�,format((state.historical?.config || state.task.config)?.fracture?.threshold,3),""],["执行耗时",format(stage.elapsed_seconds),"s"]]));items.push(node("p",{class:"result-note",text:"暗线检测会受到岩石纹理、阴影、衬砌接缝及钻孔痕迹影响。候�Mask 尚未经过真实标注精度验证�}));
  } else if(state.stage==="trace_extraction"){
    const traces=data.fractures || [],topology=data.topology || {};
    items.push(metrics([["候选迹线段",format(traces.length,0),"�],["完整迹线总长",format(traces.reduce((sum,trace)=>sum+trace.length_px,0)),"px"],["端点�,format(topology.endpoints ?? topology.endpoint_count,0),"�],["分叉像素�,format(topology.junction_pixel_count ?? topology.branchpoints ?? topology.junction_count ?? topology.branchpoint_count,0),"�]]));items.push(disclosure("骨架拓扑与拆分规�,topology));
  } else if(state.stage==="sketch_rendering"&&state.historical){
    items.push(node("p",{class:"result-note",text:"历史综合素描以当次生成的 PNG 为准。此执行快照未嵌入几何统计；请在几何参数步骤选择对应历史执行查看，当前分支数据不会混入此历史视图�}),disclosure("当次绘图记录",stage.data || {}));
  } else {
    const stats=data.statistics || {};
    items.push(metrics([["候选迹线段",format(stats.fracture_count,0),"�],["总迹线长�,format(stats.total_length_px),"px"],["像素迹线密度 P21",format(stats.p21_per_px,6),"px⁻�],["候选分区数�,format(stats.lithology_region_count ?? data.lithology_regions?.length,0),"�]]));
    if(stats.total_length_m!==null&&stats.total_length_m!==undefined)items.push(node("p",{class:"result-note",text:`尺度标定结果：长�${format(stats.total_length_m,5)} m · 观测面积 ${format(stats.observation_area_m2,5)} m² · P21 ${format(stats.p21_per_m,6)} m⁻¹。基于原图平面与投影近似。`}));
    else items.push(node("p",{class:"result-note",text:"尚未提供有效尺度标定，米制长度、面积及 P21 保持 null。像�P21 与候�ROI 相关，不能直接视为工程掌子面参数�}));
    if(state.stage==="result_export"){
      items.push(renderSketchFormEditor());
      items.push(node("button",{class:"button primary download-result","data-download-action":"true",disabled:busy()||Boolean(state.historical),onclick:downloadPackage,text:"�下载当前有效成果�}));
    }
  }
  const warnings=selectedWarnings();if(warnings.length)items.push(node("ul",{class:"warning-list"},warnings.map(text=>node("li",{text}))));
  const config=state.historical?.config || state.historical?.config_snapshot || state.task.config;
  const algorithms=data.algorithms || {};
  items.push(disclosure("本步骤算法、参数与执行信息",{execution_id:stage.execution_id || null,status:stage.status,cache_key:stage.cache_key || null,algorithms,parameters:currentSpec().section?config?.[currentSpec().section]:{},coordinate_system:"original_image_pixels"}));
  return items;
}
function renderObjects() {
  const data=currentData(),regions=data.lithology_regions || [],traces=data.fractures || [];
  const items=[];
  if(state.stage==="face_detection"&&data.face){items.push(table(["属�,"�],[["范围面积",`${format(data.face.pixel_area,0)} px²`],["面积�,`${format(data.face.area_ratio*100)} %`],["验证状�,String(data.face.verified)],["方法",data.face.method],["状�,data.face.status]]),disclosure("候�ROI Polygon（原图像素）",data.face.polygon));}
  if(regions.length && ["lithology_segmentation","geometry_calculation","sketch_rendering","result_export"].includes(state.stage)){
    items.push(table(["区域 ID","类别","面积 / px²","面积占比","面积 / m²"],regions.map(region=>[region.region_id,region.category,format(region.pixel_area,0),`${format(region.area_ratio*100,3)} %`,format(region.area_m2,5)])),disclosure("区域 Polygon（含孔洞，原图像素）",regions.map(region=>({region_id:region.region_id,polygon:region.polygon}))));
    if(state.stage==="lithology_segmentation")items.push(...renderJudgmentObjects());
  }
  if(traces.length && ["trace_extraction","geometry_calculation","sketch_rendering","result_export"].includes(state.stage)){
    const limited=traces.slice(0,400);items.push(node("p",{class:"result-note",text:`�${format(traces.length,0)} 条候选迹线段；表格展示前 ${limited.length} 条。完整坐标与对象见迹�JSON。方向按图像 X 轴计算，轴向范围 [0°, 180°)。`}));
    items.push(table(["迹线 ID","长度 / px","长度 / m","端点方向 / °","主轴方向 / °","坐标点数"],limited.map(trace=>[trace.id,format(trace.length_px,3),format(trace.length_m,5),format(trace.endpoint_direction_deg),format(trace.principal_direction_deg),trace.points?.length])),disclosure("首条迹线坐标与端点拓�,traces[0]));
  }
  if(state.stage==="preprocessing"&&data.image)items.push(disclosure("完整图像元数�,data.image));
  if(!items.length)items.push(disclosure("本步骤结构化输出",(state.historical || currentStage()).data || {}));
  return items;
}
function renderFiles() {
  const artifacts=state.stage==="result_export"&&!state.historical?allArtifacts():selectedArtifacts();
  if(!artifacts.length)return [node("div",{class:"empty-result",text:"当前步骤尚无输出文件。执行完成后可在这里打开或保存真实成果�})];
  return [node("div",{class:"file-grid"},artifacts.map(artifact=>node("a",{class:"artifact-link",href:artifact.url,download:basename(artifact.name),target:"_blank",rel:"noopener"},[imageArtifact(artifact)?"�:"�,node("span",{text:basename(artifact.name),title:artifact.name}),node("small",{text:"�})]))),...(state.stage==="result_export"?[node("button",{class:"button primary download-result","data-download-action":"true",disabled:busy()||Boolean(state.historical),onclick:downloadPackage,text:"�下载完整成果 ZIP"})]:[])];
}
function showConsolePane(target) {
  for(const id of ["pane-parameters","pane-substeps","pane-results"]){
    const element=document.getElementById(id);
    if(element)element.hidden=id!==target;
  }
}
function renderResults() {
  const lithology=state.stage==="lithology_segmentation";
  const parameterTab=document.querySelector('[data-tab="parameters"]');
  if(parameterTab)parameterTab.hidden=lithology;
  document.querySelectorAll('[data-tab="files"],[data-tab="logs"]').forEach(button=>{button.hidden=true;});
  const tabButton=document.getElementById("tab-substeps");
  if(tabButton)tabButton.hidden=!lithology || !state.task;
  const badge=document.getElementById("substeps-count");
  if(badge){
    const items=judgmentSubstages();
    const done=items.filter(item=>["completed","warning"].includes(item.status)).length;
    badge.textContent=(lithology && items.length)?` ${done}/${items.length}`:"";
  }
  if(lithology && state.tab==="parameters")state.tab="substeps";
  if(["files","logs"].includes(state.tab))state.tab="summary";
  if(!["parameters","substeps","summary","objects"].includes(state.tab))state.tab="summary";
  if(state.tab==="substeps" && (!lithology || !state.task))state.tab="summary";
  document.querySelectorAll("[data-tab]").forEach(button=>{
    button.classList.toggle("active",button.dataset.tab===state.tab);
    button.setAttribute("aria-selected",String(button.dataset.tab===state.tab));
  });
  $("file-count").textContent=String((state.stage==="result_export"&&!state.historical?allArtifacts():selectedArtifacts()).length);
  if(state.tab==="parameters"){
    showConsolePane("pane-parameters");
    renderParameters();
  } else if(state.tab==="substeps"){
    showConsolePane("pane-substeps");
    renderSubsteps();
  } else {
    showConsolePane("pane-results");
    renderResultTab();
  }
  renderControls();
}
function renderResultTab() {
  if(state.tab==="summary")replace("result-content",renderSummary());
  else if(state.tab==="objects")replace("result-content",renderObjects());
  else if(state.tab==="files")replace("result-content",renderFiles());
  else {
    const record=state.historical || currentStage();
    const records=state.historical?[record]:(state.logs?.logs || []).filter(item=>item.stage_id===state.stage || item.stage===state.stage);
    const section=currentSpec().section;
    const text=records.length?records.map(entry=>JSON.stringify({execution_id:entry.execution_id,stage_id:entry.stage_id,status:entry.status,created_at:entry.created_at,elapsed_seconds:entry.elapsed_seconds,method:entry.method,config_revision:entry.config_revision,parameters:section?entry.config?.[section]:{},warnings:entry.warnings || [],error:entry.error},null,2)).join("\n\n") : record.execution_id ? JSON.stringify(record,null,2) : "尚无本步骤执行日志。每次阶段执行将记录状态、输入、有效参数、耗时与错误�;
    replace("result-content",[node("details",{class:"detail-disclosure",open:""},[node("summary",{text:state.historical?"当次历史执行日志（点击收起）":"任务执行日志（点击收起）"}),node("pre",{class:"log-block",text})]),...(record.error?[node("p",{class:"result-note",text:`${currentSpec().title}�{record.error}`})]:[])]);
  }
}
function render() {renderTaskHeader();renderNavigation();renderHeading();renderHistory();renderImages();renderResults();renderControls();}

async function fetchResults() {if(!state.task)return;try{const response=await api(`${basePath()}/results`);state.result=response.result || null;}catch{state.result=null;}}
function normalizeStage(id) { return id==="lithology_judgment" ? "lithology_segmentation" : id; }
async function loadTask(id) {
  if(state.dirty && state.task?.id!==id)toast("已切换任务，未保存的参数草稿未应用�);
  const task=await api(`/api/tasks/${encodeURIComponent(id)}`);state.task=task;state.draft=clone(task.config);state.dirty=false;state.historical=null;state.historyId="";state.outputChoice="";state.logs=null;state.result=null;state.stage="original";
  const url=new URL(location.href);url.searchParams.set("task",id);history.replaceState(null,"",url);localStorage.setItem("face-mapping-task",id);$("task-select").value=id;
  await fetchResults();if(state.stage==="lithology_segmentation")await loadJudgment();render();renderParameters();if(task.busy){state.job=typeof task.active_job==="string"?task.active_job:task.active_job?.job_id || null;startPolling();}
}
async function refreshTask() {
  if(!state.task)return;const id=state.task.id;const next=await api(basePath());if(state.task?.id!==id)return;
  const revisionChanged=next.config_revision!==state.task.config_revision;state.task=next;if(revisionChanged)state.result=null;
  if(!state.dirty && revisionChanged){state.draft=clone(next.config);renderParameters();}
  render();
}
async function refreshTasks() {
  const response=await api("/api/tasks");state.tasks=Array.isArray(response)?response:response.tasks || [];
  replace("task-select",[node("option",{value:"",text:"选择已保存的照片"}),...state.tasks.map(task=>node("option",{value:task.id,text:`${task.filename} · ${shortId(task.id)}`}))]);
  if(state.task)$("task-select").value=state.task.id;
}
function selectStage(id) {
  state.stage=normalizeStage(id);state.historical=null;state.historyId="";state.outputChoice="";state.logs=null;
  const status=state.task?.stages?.[state.stage]?.status;
  state.tab=state.stage==="lithology_segmentation"?"substeps":(status==="completed"||status==="warning")?"summary":"parameters";
  if(state.stage==="lithology_segmentation"){
    // Paint with whatever is already known (cached judgement or a skeleton),
    // then refresh in the background so the click never appears to hang.
    state.judgmentLoading=Boolean(state.task);
    render();renderParameters();
    loadJudgment().finally(()=>{render();renderParameters();});
  }
  else {render();renderParameters();}
}
async function saveConfig() {
  if(!state.dirty)return;
  for(const input of document.querySelectorAll("#console-body input"))if(!input.checkValidity()){input.reportValidity();throw new Error("请先修正当前面板中的参数值�);}
  const response=await api(`${basePath()}/config`,{method:"PUT",body:JSON.stringify({config:state.draft})});
  state.dirty=false;
  if(response.id && response.stages)state.task=response;else state.task=await api(basePath());
  state.draft=clone(state.task.config);state.result=null;state.logs=null;render();renderParameters();
}
async function perform(action) {
  if(busy())return;state.operation=true;renderControls();$("error-dialog").hidden=true;
  try{await action();}catch(error){showError(error);}finally{state.operation=false;render();renderParameters();}
}
async function run(stageId,{downstream=false,all=false}={}) {
  await perform(async()=>{
    await saveConfig();state.historical=null;state.historyId="";
    state.autoJudgmentAfterSegmentation=Boolean(all || stageId==="lithology_segmentation");
    const request=all?`${basePath()}/run-all`:`${basePath()}/stages/${encodeURIComponent(stageId)}/run`;
    const response=await api(request,{method:"POST",body:JSON.stringify({force:$("force-run").checked,...(all?{}:{downstream})})});
    state.job=response.job_id;await refreshTask();toast(all?"全流程任务已提交，执行状态将自动更新�:downstream?"已提交当前阶段及受影响的后续步骤�:"任务已提交，自动准备所需前置步骤�);startPolling();
  });
}
function startPolling() {
  if(state.polling)return;state.polling=true;
  const tick=async()=>{
    try{
      let job=null;if(state.job)job=await api(`/api/jobs/${encodeURIComponent(state.job)}`);
      await refreshTask();
      if(job && ["completed","failed"].includes(job.status) || !state.task?.busy && !state.job){
        const failed=job?.status==="failed";const runJudgmentAfter=state.autoJudgmentAfterSegmentation && !failed;state.autoJudgmentAfterSegmentation=false;state.job=null;state.polling=false;await fetchResults();render();renderParameters();await refreshTasks();
        if(failed)showError(new Error(job.error || "部分阶段执行失败，请查看对应步骤及日志�));else toast("计算已完成，当前结果已刷新�);
        if(runJudgmentAfter)await runCombinedJudgment();
        if(state.downloadAfterJob){state.downloadAfterJob=false;if(!failed)await obtainZip();}return;
      }
      if(!state.job && state.task?.active_job)state.job=typeof state.task.active_job==="string"?state.task.active_job:state.task.active_job.job_id;
      setTimeout(tick,900);
    }catch(error){state.polling=false;state.job=null;showError(error);render();renderParameters();}
  };
  setTimeout(tick,400);
}
async function runCombinedJudgment() {
  if(!state.task)return;
  try {
    await loadJudgment();
    if(!judgmentRegions().length){toast("岩性分割已完成，但没有可供判别的区域�);render();renderParameters();return;}
    state.operation=true;renderControls();
    state.judgment=unwrapJudgment(await api(judgmentPath("/run-all"),{method:"POST",body:JSON.stringify(judgmentPayload())}));
    toast("岩性区域分割与判别已完成，可查看候选、知识证据和最终判定�);
  } catch(error) { showError(error); }
  finally { state.operation=false;render();renderParameters(); }
}
async function upload(file) {
  if(!file)return;
  if(!/\.(jpg|jpeg|png)$/i.test(file.name)){showError(new Error("仅支�JPG、JPEG、PNG 照片�));return;}
  await perform(async()=>{const data=new FormData();data.append("file",file);const response=await api("/api/tasks",{method:"POST",body:data});const task=response.task || response;await refreshTasks();await loadTask(task.id);toast("照片已保存，原图已就绪。可逐步运行或执行全部步骤�);});
}
async function obtainZip() {
  const response=await fetch(`${basePath()}/download`,{cache:"no-store"});
  if(!response.ok){let message="成果包尚未就�;try{const body=await response.json();message=body.detail || message;}catch{}throw new Error(message);}
  const blob=await response.blob(),url=URL.createObjectURL(blob);const anchor=node("a",{href:url,download:`face_mapping_${shortId(state.task.id)}.zip`});document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);toast("当前有效成果包已下载。缺失模块与质量限制请查看质量报告�);
}
async function downloadPackage() {
  if(!state.task || busy())return;
  await perform(async()=>{
    await saveConfig();
    if(!goodStatuses.has(state.task.stages?.result_export?.status)){
      state.stage="result_export";state.outputChoice="";const response=await api(`${basePath()}/stages/result_export/run`,{method:"POST",body:JSON.stringify({force:false,downstream:false})});state.job=response.job_id;state.downloadAfterJob=true;await refreshTask();startPolling();toast("正在生成当前有效成果包，完成后自动下载�);
    }else await obtainZip();
  });
}
async function viewHistory(id) {
  if(!id){state.historyId="";state.historical=null;state.outputChoice="";render();renderParameters();return;}
  try{const response=await api(`${basePath()}/history/${encodeURIComponent(id)}`);state.historyId=id;state.historical=response.execution || response;state.outputChoice="";state.logs=null;render();renderParameters();}catch(error){showError(error);}
}
async function openLogs() {state.tab="logs";try{if(state.task)state.logs=await api(`${basePath()}/logs`);}catch(error){showError(error);}renderResults();}

$("upload-input").addEventListener("change",event=>{upload(event.target.files[0]);event.target.value="";});
$("task-select").addEventListener("change",event=>{if(event.target.value)loadTask(event.target.value).catch(showError);});
$("run-stage").addEventListener("click",()=>run(state.stage));
$("run-downstream").addEventListener("click",()=>run(state.stage,{downstream:true}));
$("run-all").addEventListener("click",()=>run(null,{all:true}));
$("export-top").addEventListener("click",downloadPackage);
$("save-config").addEventListener("click",()=>perform(async()=>{await saveConfig();toast("参数已保存，受影响的旧结果已标记过期�);}));
$("restore-defaults").addEventListener("click",()=>{const section=currentSpec().section;if(!section || !state.draft)return;state.draft[section]=clone(state.defaults[section]);markDirty();renderParameters();toast("已恢复本步骤默认参数，保存或运行后生效�);});
document.getElementById("show-logs")?.addEventListener("click",openLogs);
$("execution-select").addEventListener("change",event=>viewHistory(event.target.value));
$("artifact-select").addEventListener("change",event=>{state.outputChoice=event.target.value;renderImages();});
$("dismiss-error").addEventListener("click",()=>{$("error-dialog").hidden=true;});
document.querySelectorAll("[data-tab]").forEach(button=>button.addEventListener("click",()=>{state.tab=button.dataset.tab;if(state.tab==="logs")openLogs();else renderResults();}));
document.getElementById("open-knowledge")?.addEventListener("click",()=>openKnowledgePanel("rocks"));
document.getElementById("open-provider")?.addEventListener("click",openProviderPanel);
document.querySelectorAll("[data-layer]").forEach(control=>control.addEventListener("change",drawSketch));
$("reset-view").addEventListener("click",()=>{inputViewer.fit();outputViewer.fit();});
for(const [id,factor] of [["zoom-in",1.25],["zoom-out",.8]])$(id).addEventListener("click",()=>{for(const viewer of [inputViewer,outputViewer]){viewer.zoom=Math.max(.3,Math.min(24,viewer.zoom*factor));viewer.apply();}updateZoom();});
$("save-image").addEventListener("click",()=>{
  if(!outputViewer.loaded)return;
  if(state.outputChoice==="layers"){
    const canvas=document.createElement("canvas");canvas.width=outputViewer.img.naturalWidth;canvas.height=outputViewer.img.naturalHeight;const ctx=canvas.getContext("2d");ctx.drawImage(outputViewer.img,0,0);ctx.drawImage($("sketch-canvas"),0,0);canvas.toBlob(blob=>{const url=URL.createObjectURL(blob);const anchor=node("a",{href:url,download:"geological_sketch_view.png"});anchor.click();setTimeout(()=>URL.revokeObjectURL(url),10000);});
  }else{const artifact=availableOutputImages().find(item=>(item.id || item.url)===state.outputChoice);const anchor=node("a",{href:outputViewer.url,download:artifact?basename(artifact.name):"original.png"});anchor.click();}
});
const dropZone=$("drop-zone");
for(const eventName of ["dragenter","dragover"])dropZone.addEventListener(eventName,event=>{event.preventDefault();if(!busy())dropZone.classList.add("dragover");});
for(const eventName of ["dragleave","drop"])dropZone.addEventListener(eventName,event=>{event.preventDefault();dropZone.classList.remove("dragover");if(eventName==="drop"&&!busy())upload(event.dataTransfer.files[0]);});
window.addEventListener("beforeunload",event=>{if(state.dirty){event.preventDefault();event.returnValue="";}});

async function initialize() {
  render();renderParameters();
  try{
    const [health,schema]=await Promise.all([api("/api/health"),api("/api/schema")]);
    state.schema=schema.schema || schema.config_schema;state.defaults=schema.defaults;state.parameterMeta=schema.parameters || {};
    for(const definition of schema.stages || []){const spec=stageSpecs.find(item=>item.id===definition.id);if(spec && definition.section)spec.section=definition.section;}
    $("connection").classList.remove("offline");replace("connection",[node("i"),"本地服务在线"]);$("connection").title=JSON.stringify(health);
    await refreshTasks();const taskId=new URL(location.href).searchParams.get("task") || localStorage.getItem("face-mapping-task");
    if(taskId){try{await loadTask(taskId);}catch(error){localStorage.removeItem("face-mapping-task");showError(error);}}
    const tabParam=new URL(location.href).searchParams.get("tab");
    const stageParam=new URL(location.href).searchParams.get("stage");
    if(stageParam && stageSpecs.some(spec=>spec.id===stageParam))selectStage(stageParam);
    if(tabParam && ["parameters","substeps","summary","objects","files","logs"].includes(tabParam)){
      state.tab=tabParam;render();
    }
    const substepParam=new URL(location.href).searchParams.get("substep");
    if(substepParam && ["segmentation","algorithm_classification","knowledge_llm","synthesis"].includes(substepParam)){
      state.substep=substepParam;state.tab="substeps";render();
    }
    if(new URL(location.href).searchParams.get("provider"))await openProviderPanel();
    const kbPanel=new URL(location.href).searchParams.get("kb");
    const recordParam=new URL(location.href).searchParams.get("record");
    if(recordParam)state.kb={...(state.kb || {}),selected:recordParam};
    if(kbPanel)await openKnowledgePanel(kbPanel==="fields"?"fields":kbPanel==="graph"?"graph":"rocks");
  }catch(error){$("connection").classList.add("offline");replace("connection",[node("i"),"连接失败"]);showError(error);}
}
initialize();
