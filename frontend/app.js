"use strict";

// The browser only presents Python-produced artifacts and vectors. Recognition,
// measurements, configuration validation and cache decisions stay on the server.
const $ = id => document.getElementById(id);
const clone = value => JSON.parse(JSON.stringify(value));
const goodStatuses = new Set(["completed", "warning"]);
const labels = {pending:"未运行", running:"运行中", completed:"已完成", warning:"完成 · 质量警告", stale:"结果已过期", failed:"运行失败", blocked:"前置条件未满足", queued:"等待执行"};
const stageSpecs = [
  {id:"original", title:"原始照片", en:"SOURCE IMAGE", description:"保留照片、原始文件哈希与尺寸。所有处理结果均来自这张照片。"},
  {id:"preprocessing", title:"图像预处理", section:"preprocessing", en:"PREPROCESSING", description:"统一 EXIF 方向与工作尺寸，增强局部对比度，并保存原图与工作图之间的坐标变换。"},
  {id:"face_detection", title:"掌子面分割", section:"face", en:"FACE REGION", description:"提取候选分析范围。自动边界尚未经过地质验证，整图回退会明确提示。"},
  {id:"lithology_segmentation", title:"岩性区域分割", section:"lithology", en:"LITHOLOGY REGIONS", description:"依据颜色与空间特征划分候选区域。地质类别统一为 unknown，需人工参考标注验证。"},
  {id:"fracture_segmentation", title:"裂缝分割", section:"fracture", en:"FRACTURE SEGMENTATION", description:"增强暗线并过滤细长连通区域，提取可检查的候选裂隙 Mask。"},
  {id:"trace_extraction", title:"迹线识别", section:"vectorization", en:"TRACE EXTRACTION", description:"骨架化与图追踪生成候选迹线。完整坐标用于长度计算，简化坐标仅用于绘图。"},
  {id:"geometry_calculation", title:"几何参数计算", section:"calibration", en:"GEOMETRIC STATISTICS", description:"使用有效区域与未简化迹线计算长度、图像平面方向和 P21，保留坐标与标定条件。"},
  {id:"sketch_rendering", title:"综合地质素描", en:"GEOLOGICAL SKETCH", description:"将掌子面范围、岩性候选分区和裂隙迹线叠加到原图。图层开关仅改变显示。"},
  {id:"result_export", title:"成果导出", en:"RESULT EXPORT", description:"导出当前有效的图像、矢量、参数表、配置和质量报告；历史过期成果不会混入下载包。"}
];
const preferredImages = {
  preprocessing:["preprocessed.png","original.png"],
  face_detection:["face_overlay.png","face_boundary_overlay.png","face_mask.png"],
  lithology_segmentation:["lithology_overlay.png","lithology_mask.png"],
  fracture_segmentation:["fracture_overlay.png","fracture_mask.png","fracture_response.png"],
  trace_extraction:["vector_overlay.png","trace_overlay.png","fracture_traces_overlay.png","traces_overlay.png","skeleton.png","fracture_skeleton.png"],
  sketch_rendering:["geological_sketch.png"], result_export:["geological_sketch.png"]
};
const imageLabels = {"original.png":"原始照片", "preprocessed.png":"预处理图", "face_mask.png":"黑白 Mask", "face_overlay.png":"边界叠加", "face_boundary_overlay.png":"边界叠加", "lithology_mask.png":"区域标签 Mask", "lithology_overlay.png":"候选区域叠加", "fracture_mask.png":"黑白 Mask", "fracture_overlay.png":"候选裂缝叠加", "fracture_skeleton.png":"裂隙骨架", "skeleton.png":"裂隙骨架", "trace_overlay.png":"矢量迹线叠加", "fracture_traces_overlay.png":"矢量迹线叠加", "traces_overlay.png":"矢量迹线叠加", "geological_sketch.png":"综合地质素描"};
Object.assign(imageLabels,{"vector_overlay.png":"矢量迹线叠加","face_layer.png":"候选范围透明图层","lithology_layer.png":"候选分区透明图层","trace_layer.png":"迹线透明图层","trace_ids_layer.png":"迹线编号透明图层"});
const state = {task:null, schema:null, defaults:null, parameterMeta:{}, draft:null, dirty:false, stage:"original", tab:"summary", operation:false, polling:false, job:null, historical:null, historyId:"", result:null, outputChoice:"", renderKey:"", logs:null, tasks:[]};

function node(tag, attributes={}, children=[]) {
  const item = document.createElement(tag);
  for (const [key,value] of Object.entries(attributes)) {
    if (value === undefined || value === null) continue;
    if (key === "text") item.textContent = String(value);
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
function format(value, digits=2) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : new Intl.NumberFormat("zh-CN", {maximumFractionDigits:digits}).format(Number(value)); }
function date(value) { if(!value) return ""; const d=new Date(value); return Number.isNaN(d.valueOf()) ? String(value) : d.toLocaleString("zh-CN",{hour12:false}); }
function shortId(value) { return String(value || "—").slice(0,12); }
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
  if(chosen.error) warnings.unshift(`阶段执行失败：${chosen.error}`);
  if(!state.historical && currentStage().status==="stale") warnings.unshift("此结果已过期：输入或参数已改变，当前不提供有效图像与统计。请重新运行；历史结果可通过执行记录下拉框回查。");
  const face=currentData().face;
  if(face && (!face.verified || face.status==="full_image_fallback" || face.method==="full_image_fallback")) warnings.unshift(face.status==="full_image_fallback" || face.method==="full_image_fallback" ? "已使用整图回退 full_image_fallback。掌子面边界未验证，设备、衬砌和背景可能参与统计；当前结果均为候选结果。" : "自动掌子面范围尚未验证，区域内可能存在设备、衬砌或其他遮挡，当前统计属于候选结果。");
  return [...new Set(warnings.map(String))];
}

class ImageViewer {
  constructor(prefix) {
    this.prefix=prefix; this.box=$(`${prefix}-viewport`); this.img=$(`${prefix}-image`); this.transform=this.img.parentElement; this.empty=this.box.querySelector(".viewer-empty"); this.zoom=1; this.panX=0; this.panY=0; this.url=""; this.loaded=false;
    this.img.addEventListener("load",()=>{this.loaded=true;this.empty.hidden=true;this.fit();this.showSize();if(this.prefix==="output") drawSketch();});
    this.img.addEventListener("error",()=>{this.loaded=false;this.empty.hidden=false;if(this.prefix==="output"){$("output-empty-title").textContent="结果图片读取失败";$("output-empty-description").textContent="请检查本地服务和执行日志，再重新选择该结果。";}});
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
  showSize() {$(`${this.prefix}-dimensions`).textContent=this.loaded ? `${this.img.naturalWidth} × ${this.img.naturalHeight} px` : "—";}
}
const inputViewer=new ImageViewer("input"), outputViewer=new ImageViewer("output");
function updateZoom() {$("zoom-label").textContent=`${Math.round(outputViewer.zoom*100)}%`;}

function renderNavigation() {
  replace("stage-nav",stageSpecs.map((spec,index)=>{
    const status=spec.id==="original" ? (state.task?"completed":"pending") : state.task?.stages?.[spec.id]?.status || "pending";
    return node("button",{class:`stage-nav-button${state.stage===spec.id?" active":""}`,"data-testid":`stage-${spec.id}`,"data-stage":spec.id,"aria-current":state.stage===spec.id?"step":undefined,onclick:()=>selectStage(spec.id)},[
      node("span",{class:"stage-number",text:String(index).padStart(2,"0")}),node("span",{class:"stage-nav-copy"},[node("span",{class:"stage-nav-title",text:spec.title}),node("div",{class:`stage-nav-status ${status}`,text:spec.id==="original"&&state.task?"原始文件已保存":labels[status] || status})])
    ]);
  }));
}
function renderTaskHeader() {
  const task=state.task;
  $("open-tunnel").href=task ? `/tunnel.html?task=${encodeURIComponent(task.id)}` : "/tunnel.html";
  $("photo-name").textContent=task?.filename || "尚未加载照片";
  $("photo-name").title=task?.filename || "";
  $("photo-meta").textContent=task ? `${task.width} × ${task.height} px  ·  TASK ${shortId(task.id)}  ·  SHA256 ${String(task.sha256 || "").slice(0,12)}…` : "上传 JPG / PNG，开始一次新的识别任务";
  $("photo-meta").title=task ? `任务 ID：${task.id}\n文件 SHA256：${task.sha256}` : "";
  if(!task)setPill("overall-status","pending","等待输入");
  else if(busy())setPill("overall-status","running","任务处理中");
  else if(allStages().some(item=>item.status==="failed"))setPill("overall-status","failed","部分阶段失败");
  else if(allStages().some(item=>item.status==="stale"))setPill("overall-status","stale","存在过期结果");
  else if(allStages().filter(item=>goodStatuses.has(item.status)).length>=8)setPill("overall-status","warning","处理完成 · 候选成果");
  else setPill("overall-status","pending","可逐步执行");
  $("config-revision").textContent=state.historical?`REV ${state.historical.config_revision ?? "—"} · 历史`:`REV ${task?.config_revision ?? "—"}`;
}
function renderHistory() {
  const records=(state.task?.history || []).filter(item=>item.stage_id===state.stage || item.stage===state.stage);
  const select=$("execution-select"), old=state.historyId;
  replace(select,[node("option",{value:"",text:"当前配置 / 最新结果"}),...records.slice().reverse().map(record=>node("option",{value:record.execution_id,text:`${date(record.created_at).slice(5)} · ${labels[record.status] || record.status} · ${shortId(record.execution_id)}`}))]);
  if(old && [...select.options].some(item=>item.value===old))select.value=old;
  select.disabled=!state.task || busy();
}
function renderHeading() {
  const spec=currentSpec(),index=stageSpecs.indexOf(spec), stage=currentStage();
  $("stage-eyebrow").textContent=`${String(index).padStart(2,"0")} / ${spec.en}`;
  $("stage-title").textContent=spec.title;$("stage-description").textContent=spec.description;
  setPill("stage-status",state.stage==="original"?(state.task?"completed":"pending"):state.historical?.status || stage.status);
  const warnings=selectedWarnings();$("quality-banner").hidden=!warnings.length;$("quality-banner").textContent=warnings.slice(0,3).join("\n");
  $("historical-banner").hidden=!state.historical;$("historical-banner").textContent=state.historical ? `正在查看历史执行 ${state.historyId} · ${date(state.historical.created_at)}。图像与数据属于当次执行，参数编辑已暂停。返回「当前配置 / 最新结果」可继续处理。` : "";
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
  const task=state.task,spec=currentSpec();let input=task?.original_url || "", inputName="原始照片";
  const processed=lookupImage(["preprocessed.png"]),mask=lookupImage(["fracture_mask.png"]);
  if(!state.historical&&["face_detection","lithology_segmentation","fracture_segmentation"].includes(state.stage)&&processed){input=processed.url;inputName="预处理图";}
  if(!state.historical&&state.stage==="trace_extraction"&&mask){input=mask.url;inputName="裂缝 Mask";}
  if(state.historical)inputName="原始照片 · 历史参数见右侧";
  $("input-name").textContent=inputName;inputViewer.set(input);
  const choices=availableOutputImages();
  // Historical PNGs are authoritative; never rebuild an old sketch with today's branches.
  const canSketch=state.stage==="sketch_rendering" && !state.historical && goodStatuses.has(currentStage().status) && Boolean(currentData().image);
  const options=[];
  if(state.stage==="original"&&task)options.push(node("option",{value:"original",text:"EXIF 方向统一后的原图"}));
  if(canSketch)options.push(node("option",{value:"layers",text:"可开关图层的叠加视图"}));
  for(const artifact of choices)options.push(node("option",{value:artifact.id || artifact.url,text:imageLabels[basename(artifact.name)] || basename(artifact.name)}));
  const values=options.map(option=>option.value);
  if(!values.includes(state.outputChoice))state.outputChoice=values[0] || "";
  replace("artifact-select",options.length?options:[node("option",{value:"",text:state.stage==="original"?"等待照片":"尚无输出图像"})]);
  $("artifact-select").value=state.outputChoice;$("artifact-select").disabled=!options.length;
  const selected=choices.find(item=>(item.id || item.url)===state.outputChoice);
  const isLayers=state.outputChoice==="layers";
  outputViewer.set(isLayers || state.outputChoice==="original" ? task?.original_url : selected?.url);
  $("sketch-layers").hidden=!canSketch || !isLayers;$("sketch-canvas").hidden=!isLayers;
  $("save-image").disabled=!state.outputChoice;
  $("output-label").textContent=isLayers?"图层叠加视图":"当前步骤结果";
  if(!state.outputChoice){
    const stage=currentStage();$("output-empty-title").textContent=stage.status==="failed"?"当前步骤运行失败":stage.status==="running"?"Python 正在处理图像":state.stage==="result_export"?"成果包尚未生成":"当前步骤尚无图像输出";
    $("output-empty-description").textContent=stage.error || (state.stage==="geometry_calculation"?"统计数据在下方展示；先运行迹线识别可查看矢量叠加图。":state.task?`点击左侧「运行当前步骤」，自动准备所需前置结果。` : "上传照片后，按左侧流程逐步运行");
  }
  drawSketch();
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
  ctx.font=`${Math.max(13,fontSize*.8)}px sans-serif`;const lines=["CANDIDATE · 原图坐标 / 图像分区 unknown", "绿：候选 ROI  /  彩色：候选分区  /  红：候选迹线",state.task?.config?.calibration?.meters_per_pixel ? `标定：${state.task.config.calibration.meters_per_pixel} m / original px（平面近似）` : "尺度：像素 · 图像方向不代表地质产状"];
  const lh=Math.max(20,fontSize*1.15);ctx.fillStyle="#15333cbb";ctx.fillRect(0,0,canvas.width,lh*lines.length+18);ctx.fillStyle="white";lines.forEach((line,index)=>ctx.fillText(line,12,(index+1)*lh));
}

function resolveSchema(schema) {
  if(schema?.$ref)return state.schema.$defs?.[schema.$ref.split("/").pop()] || schema;
  if(schema?.anyOf){const item=schema.anyOf.find(value=>value.type!=="null");return {...resolveSchema(item),nullable:schema.anyOf.some(value=>value.type==="null"),default:schema.default};}
  return schema || {};
}
function fieldValue(section,key,field) {const config=state.historical?.config || state.historical?.config_snapshot || state.draft;const value=config?.[section]?.[key];return value===undefined?field.default:value;}
function markDirty() {state.dirty=true;$("dirty-note").hidden=false;renderControls();}
function renderParameters() {
  const spec=currentSpec(),section=spec.section;$("parameter-title").textContent=spec.id==="original"?"输入信息":section?spec.title+"参数":"阶段信息";
  if(!state.task || !state.schema){replace("parameter-content",node("div",{class:"parameter-info"},[node("strong",{text:"从一张照片开始"}),"上传照片后，这里将显示当前处理步骤的真实算法参数、范围与说明。所有识别均由本地 Python 执行。"]));renderControls();return;}
  if(!section){
    const text=spec.id==="sketch_rendering"?"绘图使用后端生成的候选区域与迹线；中间图层开关可单独显示不同对象。标注最多显示最长的 80 条迹线，全部坐标保存在 JSON。":spec.id==="result_export"?"下载包只包含当前有效结果、当前配置与质量报告。若结果已过期，将先刷新导出阶段。缺少的分支会在报告中标记，不会以历史结果替代。":"原始文件独立存档；页面预览已统一 EXIF 方向。算法使用的原始坐标为方向统一后的照片像素坐标。";
    const info=node("div",{class:"parameter-info"},[node("strong",{text:spec.title}),text]);
    const rows=[["任务 ID",state.task.id],["原始尺寸",`${state.task.width} × ${state.task.height} px`],["创建时间",date(state.task.created_at)],["文件 SHA256",state.task.sha256]];
    replace("parameter-content",[info,...rows.map(([key,value])=>node("div",{class:"info-row"},[node("span",{text:key}),node("span",{class:key==="文件 SHA256"?"code":"",text:value})]))]);renderControls();return;
  }
  const definition=resolveSchema(state.schema.properties?.[section]),fields=definition.properties || {};
  const metadata=state.parameterMeta[section] || {},disabled=busy() || Boolean(state.historical);
  const controls=[];
  if(state.historical)controls.push(node("div",{class:"parameter-info",text:"正在查看历史执行。当前参数仅供参考，请切回当前结果后编辑。"}));
  if(section==="calibration")controls.push(node("div",{class:"parameter-info",text:"比例基于方向统一后的原图像素。仅在近似平面与投影条件合理、且确认该条件时填写 m/px；留空则仅输出像素指标。"}));
  for(const [key,raw] of Object.entries(fields)){
    const field=resolveSchema(raw),meta=metadata[key] || {},value=fieldValue(section,key,field),defaultValue=state.defaults?.[section]?.[key];
    if(meta.visible_when && !Object.entries(meta.visible_when).every(([dependency,allowed])=>allowed.includes(fieldValue(section,dependency,resolveSchema(fields[dependency])))))continue;
    const wrap=node("div",{class:"parameter-field"}),id=`param-${section}-${key}`;
    const heading=node("div",{class:"parameter-field-head"},[node("label",{for:id,text:meta.label || field.title || key})]);
    if(key!=="algorithm")heading.append(node("span",{class:"parameter-default",text:`默认 ${defaultValue===null?"空":typeof defaultValue==="boolean"?(defaultValue?"是":"否"):defaultValue ?? "—"}`}));
    wrap.append(heading);
    const row=node("div",{class:"parameter-input-row"});
    const save=value=>{state.draft[section][key]=value;markDirty();};
    let control;
    if(field.type==="boolean"){
      control=node("input",{type:"checkbox",id,checked:Boolean(value),disabled,"data-param":key,"data-testid":id,onchange:event=>save(event.target.checked)});
      row.append(node("label",{class:"parameter-toggle",for:id},[control,node("span",{text:key==="planar_approximation_acknowledged"?"确认平面与投影近似条件": "启用"})]));
    } else if(field.enum){
      control=node("select",{id,disabled,"data-param":key,"data-testid":id,onchange:event=>{save(event.target.value);if(key==="algorithm"||key==="mode")renderParameters();}},field.enum.map(option=>node("option",{value:option,text:meta.options?.[option]?.label || ({auto:"自动候选范围",full_image:"整图分析（未验证）"})[option] || option})));control.value=value;row.append(control);
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
      control=node("input",{type:"text",id,value:value ?? "",disabled,"data-param":key,"data-testid":id,placeholder:field.nullable?"可选":undefined,oninput:event=>save(event.target.value || (field.nullable?null:""))});row.append(control);
    }
    wrap.append(row,node("div",{class:"parameter-key",text:`${section}.${key}`}));
    const min=field.minimum ?? field.exclusiveMinimum,max=field.maximum ?? field.exclusiveMaximum;
    if(min!==undefined||max!==undefined)wrap.append(node("div",{class:"range-note",text:`范围：${min===undefined?"无限制":(field.exclusiveMinimum!==undefined?"> ":"≥ ")+min}${max===undefined?"":"  /  "+(field.exclusiveMaximum!==undefined?"< ":"≤ ")+max}${meta.unit?" "+meta.unit:""}`}));
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
  $("dirty-note").hidden=!state.dirty;$("run-stage").textContent=isBusy?"处理中，请稍候…":"▷ 运行当前步骤";
  $("task-select").disabled=isBusy;
  document.querySelectorAll("#parameter-content input,#parameter-content select").forEach(control=>control.disabled=isBusy||historical);
  document.querySelectorAll("[data-download-action]").forEach(control=>control.disabled=noTask||isBusy||historical);
}

function metrics(items) {return node("div",{class:"metric-grid"},items.map(([label,value,unit])=>node("div",{class:"metric"},[node("div",{class:"metric-label",text:label}),node("div",{class:"metric-value"},[value ?? "—",node("span",{class:"metric-unit",text:unit || ""})])])));}
function table(headers,rows) {return node("table",{class:"data-table"},[node("thead",{},node("tr",{},headers.map(text=>node("th",{text})))),node("tbody",{},rows.map(values=>node("tr",{},values.map(value=>node("td",{text:value ?? "—"})))))]);}
function disclosure(title,value) {return node("details",{class:"detail-disclosure"},[node("summary",{text:title}),node("pre",{class:"json-block",text:JSON.stringify(value,null,2)})]);}
function renderSummary() {
  const data=currentData(),stage=state.historical || currentStage(),items=[];
  if(!state.task)return [node("div",{class:"empty-result",text:"上传掌子面照片后，处理状态、质量信息和关键统计将在这里显示。"})];
  if(state.stage!=="original" && !Object.keys(stage.data || {}).length){
    const reason=state.historical?"该次历史执行未生成有效统计，不能据此判断对象数量为零。":stage.status==="stale"?"当前步骤结果已过期，数量、面积和长度暂不可用。请重新运行，或从执行记录下拉框回查历史成果。":stage.status==="failed"?"当前步骤运行失败，尚无有效统计。请查看错误，调整参数后重新执行。":stage.status==="blocked"?"必要前置条件未满足，当前步骤尚无有效统计。请运行所需前置步骤。":stage.status==="running"?"当前阶段正在本地执行，暂不提供统计值；完成后将自动刷新。":"当前步骤尚未执行。运行时会自动准备必要的前置步骤，并复用有效缓存。";
    return [node("div",{class:"empty-result",text:reason}),...(stage.error?[node("p",{class:"result-note",text:stage.error})]:[])];
  }
  if(state.stage==="original")items.push(metrics([["原始图像宽度",format(state.task.width,0),"px"],["原始图像高度",format(state.task.height,0),"px"],["配置版本",state.task.config_revision ?? 0,"rev"],["阶段执行记录",format(state.task.history?.length || 0,0),"次"]]));
  else if(state.stage==="preprocessing"){
    const image=data.image || {};items.push(metrics([["原图宽度",format(image.original_size?.[0],0),"px"],["工作图宽度",format(image.working_size?.[0],0),"px"],["X 缩放比例",format(image.scale_x,5),""],["EXIF 方向处理",image.exif_transposed?"已旋正":"无需旋转",""]]));
    items.push(disclosure("坐标变换与图像元数据",image));
  } else if(state.stage==="face_detection"){
    const face=data.face || {};items.push(metrics([["候选区域面积",format(face.pixel_area,0),"px²"],["图像面积占比",format(face.area_ratio*100),"%"],["人工验证",face.verified?"已验证":"未验证",""],["整图回退",face.status==="full_image_fallback"||face.method==="full_image_fallback"?"是":"否",""]]));items.push(node("p",{class:"result-note",text:`方法：${face.method || "—"} · 状态：${face.status || "—"}。面积来自原图坐标下 Mask 的精确像素计数。`}));
  } else if(state.stage==="lithology_segmentation"){
    const regions=data.lithology_regions || [];items.push(metrics([["候选区域数量",format(regions.length,0),"个"],["分区总像素面积",format(regions.reduce((sum,item)=>sum+item.pixel_area,0),0),"px²"],["面积比例合计",format(regions.reduce((sum,item)=>sum+item.area_ratio,0)*100),"%"],["地质类别","unknown",""]]));items.push(node("p",{class:"result-note",text:"当前为图像特征分区，颜色与纹理差异不等于真实岩性差异。完整区域编号、面积与边界可在「对象数据」查看。"}));
  } else if(state.stage==="fracture_segmentation"){
    const own=stage.data || {},details=own.fracture_details || own.detection || own.algorithms?.fracture?.details || own.details || {};
    const pixels=own.fracture_pixel_count ?? own.fracture_pixel_area ?? own.mask_pixel_count ?? details.fracture_pixel_count ?? details.candidate_pixels ?? details.mask_pixels ?? own.fracture_mask_pixel_area;
    const count=details.kept_components ?? details.retained_components ?? details.components_kept ?? details.candidate_components ?? own.component_count;
    items.push(metrics([["候选裂缝像素",format(pixels,0),"px"],["保留连通区域",format(count,0),"个"],["检测阈值",format((state.historical?.config || state.task.config)?.fracture?.threshold,3),""],["执行耗时",format(stage.elapsed_seconds),"s"]]));items.push(node("p",{class:"result-note",text:"暗线检测会受到岩石纹理、阴影、衬砌接缝及钻孔痕迹影响。候选 Mask 尚未经过真实标注精度验证。"}));
  } else if(state.stage==="trace_extraction"){
    const traces=data.fractures || [],topology=data.topology || {};
    items.push(metrics([["候选迹线段",format(traces.length,0),"段"],["完整迹线总长",format(traces.reduce((sum,trace)=>sum+trace.length_px,0)),"px"],["端点数",format(topology.endpoints ?? topology.endpoint_count,0),"个"],["分叉像素数",format(topology.junction_pixel_count ?? topology.branchpoints ?? topology.junction_count ?? topology.branchpoint_count,0),"个"]]));items.push(disclosure("骨架拓扑与拆分规则",topology));
  } else if(state.stage==="sketch_rendering"&&state.historical){
    items.push(node("p",{class:"result-note",text:"历史综合素描以当次生成的 PNG 为准。此执行快照未嵌入几何统计；请在几何参数步骤选择对应历史执行查看，当前分支数据不会混入此历史视图。"}),disclosure("当次绘图记录",stage.data || {}));
  } else {
    const stats=data.statistics || {};
    items.push(metrics([["候选迹线段",format(stats.fracture_count,0),"段"],["总迹线长度",format(stats.total_length_px),"px"],["像素迹线密度 P21",format(stats.p21_per_px,6),"px⁻¹"],["候选分区数量",format(stats.lithology_region_count ?? data.lithology_regions?.length,0),"个"]]));
    if(stats.total_length_m!==null&&stats.total_length_m!==undefined)items.push(node("p",{class:"result-note",text:`尺度标定结果：长度 ${format(stats.total_length_m,5)} m · 观测面积 ${format(stats.observation_area_m2,5)} m² · P21 ${format(stats.p21_per_m,6)} m⁻¹。基于原图平面与投影近似。`}));
    else items.push(node("p",{class:"result-note",text:"尚未提供有效尺度标定，米制长度、面积及 P21 保持 null。像素 P21 与候选 ROI 相关，不能直接视为工程掌子面参数。"}));
    if(state.stage==="result_export")items.push(node("button",{class:"button primary download-result","data-download-action":"true",disabled:busy()||Boolean(state.historical),onclick:downloadPackage,text:"↓ 下载当前有效成果包"}));
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
  if(state.stage==="face_detection"&&data.face){items.push(table(["属性","值"],[["范围面积",`${format(data.face.pixel_area,0)} px²`],["面积比",`${format(data.face.area_ratio*100)} %`],["验证状态",String(data.face.verified)],["方法",data.face.method],["状态",data.face.status]]),disclosure("候选 ROI Polygon（原图像素）",data.face.polygon));}
  if(regions.length && ["lithology_segmentation","geometry_calculation","sketch_rendering","result_export"].includes(state.stage)){
    items.push(table(["区域 ID","类别","面积 / px²","面积占比","面积 / m²"],regions.map(region=>[region.region_id,region.category,format(region.pixel_area,0),`${format(region.area_ratio*100,3)} %`,format(region.area_m2,5)])),disclosure("区域 Polygon（含孔洞，原图像素）",regions.map(region=>({region_id:region.region_id,polygon:region.polygon}))));
  }
  if(traces.length && ["trace_extraction","geometry_calculation","sketch_rendering","result_export"].includes(state.stage)){
    const limited=traces.slice(0,400);items.push(node("p",{class:"result-note",text:`共 ${format(traces.length,0)} 条候选迹线段；表格展示前 ${limited.length} 条。完整坐标与对象见迹线 JSON。方向按图像 X 轴计算，轴向范围 [0°, 180°)。`}));
    items.push(table(["迹线 ID","长度 / px","长度 / m","端点方向 / °","主轴方向 / °","坐标点数"],limited.map(trace=>[trace.id,format(trace.length_px,3),format(trace.length_m,5),format(trace.endpoint_direction_deg),format(trace.principal_direction_deg),trace.points?.length])),disclosure("首条迹线坐标与端点拓扑",traces[0]));
  }
  if(state.stage==="preprocessing"&&data.image)items.push(disclosure("完整图像元数据",data.image));
  if(!items.length)items.push(disclosure("本步骤结构化输出",(state.historical || currentStage()).data || {}));
  return items;
}
function renderFiles() {
  const artifacts=state.stage==="result_export"&&!state.historical?allArtifacts():selectedArtifacts();
  if(!artifacts.length)return [node("div",{class:"empty-result",text:"当前步骤尚无输出文件。执行完成后可在这里打开或保存真实成果。"})];
  return [node("div",{class:"file-grid"},artifacts.map(artifact=>node("a",{class:"artifact-link",href:artifact.url,download:basename(artifact.name),target:"_blank",rel:"noopener"},[imageArtifact(artifact)?"▧":"≡",node("span",{text:basename(artifact.name),title:artifact.name}),node("small",{text:"↓"})]))),...(state.stage==="result_export"?[node("button",{class:"button primary download-result","data-download-action":"true",disabled:busy()||Boolean(state.historical),onclick:downloadPackage,text:"↓ 下载完整成果 ZIP"})]:[])];
}
function renderResults() {
  document.querySelectorAll("[data-tab]").forEach(button=>{button.classList.toggle("active",button.dataset.tab===state.tab);button.setAttribute("aria-selected",String(button.dataset.tab===state.tab));});
  $("file-count").textContent=String((state.stage==="result_export"&&!state.historical?allArtifacts():selectedArtifacts()).length);
  if(state.tab==="summary")replace("result-content",renderSummary());
  else if(state.tab==="objects")replace("result-content",renderObjects());
  else if(state.tab==="files")replace("result-content",renderFiles());
  else {
    const record=state.historical || currentStage();
    const records=state.historical?[record]:(state.logs?.logs || []).filter(item=>item.stage_id===state.stage || item.stage===state.stage);
    const section=currentSpec().section;
    const text=records.length?records.map(entry=>JSON.stringify({execution_id:entry.execution_id,stage_id:entry.stage_id,status:entry.status,created_at:entry.created_at,elapsed_seconds:entry.elapsed_seconds,method:entry.method,config_revision:entry.config_revision,parameters:section?entry.config?.[section]:{},warnings:entry.warnings || [],error:entry.error},null,2)).join("\n\n") : record.execution_id ? JSON.stringify(record,null,2) : "尚无本步骤执行日志。每次阶段执行将记录状态、输入、有效参数、耗时与错误。";
    replace("result-content",[node("details",{class:"detail-disclosure",open:""},[node("summary",{text:state.historical?"当次历史执行日志（点击收起）":"任务执行日志（点击收起）"}),node("pre",{class:"log-block",text})]),...(record.error?[node("p",{class:"result-note",text:`${currentSpec().title}：${record.error}`})]:[])]);
  }
  renderControls();
}
function render() {renderTaskHeader();renderNavigation();renderHeading();renderHistory();renderImages();renderResults();renderControls();}

async function fetchResults() {if(!state.task)return;try{const response=await api(`${basePath()}/results`);state.result=response.result || null;}catch{state.result=null;}}
async function loadTask(id) {
  if(state.dirty && state.task?.id!==id)toast("已切换任务，未保存的参数草稿未应用。");
  const task=await api(`/api/tasks/${encodeURIComponent(id)}`);state.task=task;state.draft=clone(task.config);state.dirty=false;state.historical=null;state.historyId="";state.outputChoice="";state.logs=null;state.result=null;state.stage="original";
  const url=new URL(location.href);url.searchParams.set("task",id);history.replaceState(null,"",url);localStorage.setItem("face-mapping-task",id);$("task-select").value=id;
  await fetchResults();render();renderParameters();if(task.busy){state.job=typeof task.active_job==="string"?task.active_job:task.active_job?.job_id || null;startPolling();}
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
function selectStage(id) {state.stage=id;state.historical=null;state.historyId="";state.outputChoice="";state.logs=null;render();renderParameters();}
async function saveConfig() {
  if(!state.dirty)return;
  for(const input of document.querySelectorAll("#parameter-content input"))if(!input.checkValidity()){input.reportValidity();throw new Error("请先修正当前面板中的参数值。");}
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
    const request=all?`${basePath()}/run-all`:`${basePath()}/stages/${encodeURIComponent(stageId)}/run`;
    const response=await api(request,{method:"POST",body:JSON.stringify({force:$("force-run").checked,...(all?{}:{downstream})})});
    state.job=response.job_id;await refreshTask();toast(all?"全流程任务已提交，执行状态将自动更新。":downstream?"已提交当前阶段及受影响的后续步骤。":"任务已提交，自动准备所需前置步骤。");startPolling();
  });
}
function startPolling() {
  if(state.polling)return;state.polling=true;
  const tick=async()=>{
    try{
      let job=null;if(state.job)job=await api(`/api/jobs/${encodeURIComponent(state.job)}`);
      await refreshTask();
      if(job && ["completed","failed"].includes(job.status) || !state.task?.busy && !state.job){
        const failed=job?.status==="failed";state.job=null;state.polling=false;await fetchResults();render();renderParameters();await refreshTasks();
        if(failed)showError(new Error(job.error || "部分阶段执行失败，请查看对应步骤及日志。"));else toast("计算已完成，当前结果已刷新。");
        if(state.downloadAfterJob){state.downloadAfterJob=false;if(!failed)await obtainZip();}return;
      }
      if(!state.job && state.task?.active_job)state.job=typeof state.task.active_job==="string"?state.task.active_job:state.task.active_job.job_id;
      setTimeout(tick,900);
    }catch(error){state.polling=false;state.job=null;showError(error);render();renderParameters();}
  };
  setTimeout(tick,400);
}
async function upload(file) {
  if(!file)return;
  if(!/\.(jpg|jpeg|png)$/i.test(file.name)){showError(new Error("仅支持 JPG、JPEG、PNG 照片。"));return;}
  await perform(async()=>{const data=new FormData();data.append("file",file);const response=await api("/api/tasks",{method:"POST",body:data});const task=response.task || response;await refreshTasks();await loadTask(task.id);toast("照片已保存，原图已就绪。可逐步运行或执行全部步骤。");});
}
async function obtainZip() {
  const response=await fetch(`${basePath()}/download`,{cache:"no-store"});
  if(!response.ok){let message="成果包尚未就绪";try{const body=await response.json();message=body.detail || message;}catch{}throw new Error(message);}
  const blob=await response.blob(),url=URL.createObjectURL(blob);const anchor=node("a",{href:url,download:`face_mapping_${shortId(state.task.id)}.zip`});document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);toast("当前有效成果包已下载。缺失模块与质量限制请查看质量报告。");
}
async function downloadPackage() {
  if(!state.task || busy())return;
  await perform(async()=>{
    await saveConfig();
    if(!goodStatuses.has(state.task.stages?.result_export?.status)){
      state.stage="result_export";state.outputChoice="";const response=await api(`${basePath()}/stages/result_export/run`,{method:"POST",body:JSON.stringify({force:false,downstream:false})});state.job=response.job_id;state.downloadAfterJob=true;await refreshTask();startPolling();toast("正在生成当前有效成果包，完成后自动下载。");
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
$("save-config").addEventListener("click",()=>perform(async()=>{await saveConfig();toast("参数已保存，受影响的旧结果已标记过期。");}));
$("restore-defaults").addEventListener("click",()=>{const section=currentSpec().section;if(!section || !state.draft)return;state.draft[section]=clone(state.defaults[section]);markDirty();renderParameters();toast("已恢复本步骤默认参数，保存或运行后生效。");});
$("show-logs").addEventListener("click",openLogs);
$("execution-select").addEventListener("change",event=>viewHistory(event.target.value));
$("artifact-select").addEventListener("change",event=>{state.outputChoice=event.target.value;renderImages();});
$("dismiss-error").addEventListener("click",()=>{$("error-dialog").hidden=true;});
document.querySelectorAll("[data-tab]").forEach(button=>button.addEventListener("click",()=>{state.tab=button.dataset.tab;if(state.tab==="logs")openLogs();else renderResults();}));
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
  }catch(error){$("connection").classList.add("offline");replace("connection",[node("i"),"连接失败"]);showError(error);}
}
initialize();
