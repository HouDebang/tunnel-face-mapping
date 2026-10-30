"""Auditable lithology judgement on top of the lithology-region segmentation.

Design boundary (why this is a separate stage from segmentation):

* the segmentation result is a *recognition* product with its own execution id,
  cache key and geometry; it is reused by geometry, sketch and export;
* judgement is a *classification* product that changes whenever the classifier
  or the knowledge base changes, and may call a remote model;
* re-running judgement must never invalidate measured geometry, and re-running
  segmentation must not silently reuse an old judgement. Two stages with an
  explicit source-execution binding is the smallest way to keep both true.

The user-facing flow is still one step: the workbench runs segmentation (or
reuses a valid result) and then classify -> retrieve -> infer in one action.

Input contract: every region crop is cut from the segmentation result itself -
the label map produced by ``lithology_segmentation`` decides *which* pixels
belong to the region, the EXIF-corrected original photo supplies *what those
pixels look like*. Cropping the coloured overlay would feed palette colours to
the classifier, so it is only used as a fallback and always labelled as such.
"""
from __future__ import annotations

from datetime import datetime, timezone
import base64
from io import BytesIO
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

import numpy as np
from PIL import Image
from PIL import ImageDraw
from skimage.color import rgb2lab

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .knowledge_base import KnowledgeBase
from .workspace import WorkspaceError

DEFAULT_CLASSIFIER = "rules_litho_kb_v1"
CLASSIFIERS = {
    "rules_litho_kb_v1": {
        "title": "规则 + 知识库视觉档案（本地可运行）",
        "description": "在分割区域原图像素上测量颜色、纹理、定向性与颗粒尺度，与知识库 B 的视觉期望档案比对，输出候选岩性及其依据。",
        "status": "available",
    },
    "heuristic_color_texture_v1": {
        "title": "颜色/纹理视觉描述基线",
        "description": "只输出视觉描述（浅色致密、深色粗糙、混合纹理），不对应岩性名称。",
        "status": "available",
    },
    "vision_llm": {
        "title": "大模型视觉判别（需配置 API）",
        "description": "把区域裁剪与知识库证据提交给 OpenAI 兼容接口综合判断；未配置或网络不可达时保持 unknown。",
        "status": "requires_provider",
    },
}

# Sub-stages inside the user-facing lithology step. The first one is the real
# workspace stage (geometry + cache key); the next three are classification
# products that may be re-run independently of the measured geometry.
SUBSTAGES = [
    {"id": "segmentation", "title": "岩性分割", "provides": "分割区域几何与区域 Mask",
     "description": "复用岩性区域分割阶段成果：标签图定区域、原图取像素。", "depends_on": []},
    {"id": "algorithm_classification", "title": "算法判定", "provides": "每区域岩类概率与细候选",
     "description": "测量区域视觉特征，与知识库视觉档案比对，输出排序与概率（支持度）。",
     "depends_on": ["segmentation"]},
    {"id": "knowledge_llm", "title": "大模型调用知识库", "provides": "知识证据与大模型判读",
     "description": "按候选检索两套知识库，把区域裁剪、候选和证据提交 OpenAI 兼容接口。",
     "depends_on": ["algorithm_classification"]},
    {"id": "synthesis", "title": "综合判定", "provides": "确定性结论或需复核标记",
     "description": "合并算法概率与大模型结论；一致时给确定结论，冲突或证据不足时标记需复核。",
     "depends_on": ["algorithm_classification"]},
]
SUBSTAGE_IDS = tuple(item["id"] for item in SUBSTAGES)
PROBABILITY_TEMPERATURE = 0.15

# Feature weights and tolerances shared by the rule classifier.
FEATURE_SPEC = {
    "L": (0.22, 0.16),  # lightness (exposure-normalised)
    "C": (0.10, 0.08),  # chroma
    "G": (0.20, 0.14),  # texture roughness (fine-detail share)
    "S": (0.20, 0.13),  # strong-contrast speckle share
    "A": (0.12, 0.22),  # structure-tensor anisotropy
    "B": (0.06, 0.22),  # banding
    "Z": (0.10, 0.28),  # grain scale (log-normalised)
}
MIN_REGION_PIXELS = 400
MIN_LUMINANCE_SPREAD = 0.02


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except (OSError, ValueError, TypeError):
        return default


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProviderConfig(StrictModel):
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    embedding_model: str = Field(default="", max_length=200)
    # Empty means "connect directly": the Windows system proxy (Clash/VPN) would
    # otherwise change the egress IP and the campus gateway rejects it.
    proxy: str = Field(default="", max_length=300)
    temperature: float = Field(default=.1, ge=0, le=2)
    top_p: float = Field(default=.8, ge=0, le=1)
    max_tokens: int = Field(default=1200, ge=32, le=8000)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    # bearer   -> Authorization: Bearer <key>   (OpenAI default)
    # header   -> api-key: <key>                 (some campus gateways)
    # api_key  -> Authorization: Api-Key <key>   (agi.tongji.edu.cn style)
    auth_style: str = Field(default="bearer", pattern="^(bearer|header|api_key)$")


class ClassifyRequest(StrictModel):
    region_ids: list[str] | None = None
    model_id: str = DEFAULT_CLASSIFIER


class RetrieveRequest(StrictModel):
    region_ids: list[str] | None = None
    query: str = ""
    limit: int = Field(default=10, ge=1, le=100)


class InferRequest(StrictModel):
    region_ids: list[str] | None = None
    model_id: str = DEFAULT_CLASSIFIER
    provider_config_id: str = "default"


class RunAllRequest(InferRequest):
    # The UI uses the same request shape for single sub-stage reruns. Keeping
    # this flag in the shared contract lets the segmentation sub-stage forward
    # a force request to the workspace executor without triggering Pydantic's
    # extra-field rejection.
    force: bool = False


class CombinedRunRequest(RunAllRequest):
    """One user-facing action; segmentation remains an auditable prerequisite."""
    force_segmentation: bool = False


class LithologyJudgmentStore:
    def __init__(self, workspace, project_root: Path):
        self.workspace = workspace
        self.root = workspace.root / ".lithology-judgment"
        self.root.mkdir(parents=True, exist_ok=True)
        self.knowledge = KnowledgeBase(workspace.root, project_root)

    # ------------------------------------------------------------ plumbing
    def _task_dir(self, task_id):
        return self.workspace._task_dir(task_id)

    def _path(self, task_id):
        path = self.root / "tasks" / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _config_path(self):
        return self.root / "providers.json"

    def get_config(self):
        data = _read_json(self._config_path(), {"default": {}})
        redacted = {}
        for key, value in data.items():
            redacted[key] = dict(value)
            if redacted[key].get("api_key"):
                redacted[key]["api_key_configured"] = True
                redacted[key].pop("api_key", None)
        return {"providers": redacted}

    def set_config(self, config_id: str, body: ProviderConfig):
        data = _read_json(self._config_path(), {})
        record = body.model_dump()
        # Never overwrite a stored secret when the frontend sends a redacted form.
        if not record["api_key"] and data.get(config_id, {}).get("api_key"):
            record["api_key"] = data[config_id]["api_key"]
        data[config_id] = record
        self._atomic(self._config_path(), data)
        cache = getattr(self, "_models_cache", None)
        if cache:
            cache.pop(config_id, None)
        return self.get_config()

    @staticmethod
    def _atomic(path, value):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------- segmentation source
    CACHE_TASKS = 4   # task.json + label map entries kept per process

    @staticmethod
    def _remember(cache, key, value):
        cache[key] = value
        while len(cache) > LithologyJudgmentStore.CACHE_TASKS:
            cache.pop(next(iter(cache)))

    def _regions(self, task_id):
        # One page entry used to reload task.json once per region (state,
        # provenance, label map). Memoise per task file version instead.
        task_path = self._task_dir(task_id) / "task.json"
        try:
            stat = task_path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        cache = getattr(self, "_regions_cache", None)
        if cache is None:
            cache = self._regions_cache = {}
        if stamp is not None:
            cached = cache.get(task_id)
            if cached and cached[0] == stamp:
                return cached[1]
        task = self.workspace._load(task_id)
        records = [r for r in task.get("history", [])
                   if r.get("stage_id") == "lithology_segmentation" and r.get("status") in {"completed", "warning"}]
        records.sort(key=lambda r: r.get("finished_at", ""), reverse=True)
        if not records:
            raise WorkspaceError("尚无有效岩性区域分割结果，请先运行岩性区域分割", 409)
        record = records[0]
        data = self.workspace._record_data(task, record)
        result = (task, record, data.get("lithology_regions") or [])
        if stamp is not None:
            self._remember(cache, task_id, (stamp, result))
        return result

    def _segmentation_dir(self, task_id):
        _, record, _ = self._regions(task_id)
        return self.workspace._task_dir(task_id) / record["directory"]

    def _label_map(self, task_id):
        """uint16 label image written by the segmentation stage, cached per task."""
        cache = getattr(self, "_label_cache", None)
        if cache is None:
            cache = self._label_cache = {}
        source = self._segmentation_dir(task_id) / "lithology_mask.png"
        key = str(source)
        entry = cache.get(key)
        if entry and entry[0] == source.stat().st_mtime_ns:
            return entry[1]
        labels = np.array(Image.open(source))
        if labels.ndim > 2:
            labels = labels[..., 0]
        self._remember(cache, key, (source.stat().st_mtime_ns, labels))
        return labels

    def _region_mask(self, task_id, region):
        labels = self._label_map(task_id)
        value = region.get("label_value")
        if value is None:
            return None
        return labels == int(value)

    def _original_image(self, task_id):
        """EXIF-corrected source photo; same coordinate system as the masks."""
        candidate = self._task_dir(task_id) / "upload_original.png"
        if candidate.is_file():
            return candidate
        return None

    # ---------------------------------------------------------- region crop
    def _crop_path(self, task_id, region):
        """Crop the segmentation region from the original photo (mask as stencil)."""
        destination = self._path(task_id) / "crops" / f"{region['region_id']}.jpg"
        source_image = self._original_image(task_id)
        mask = self._region_mask(task_id, region)
        if source_image is None or mask is None:
            return None
        try:
            if destination.is_file() and destination.stat().st_mtime_ns > source_image.stat().st_mtime_ns:
                return destination
            with Image.open(source_image) as image:
                if mask.shape != (image.height, image.width):
                    return None
                ys, xs = np.nonzero(mask)
                if xs.size == 0:
                    return None
                box = (max(0, int(xs.min()) - 6), max(0, int(ys.min()) - 6),
                       min(image.width, int(xs.max()) + 7), min(image.height, int(ys.max()) + 7))
                if box[2] <= box[0] or box[3] <= box[1]:
                    return None
                crop = image.convert("RGB").crop(box)
                stencil = Image.fromarray((mask[box[1]:box[3], box[0]:box[2]].astype(np.uint8) * 255))
                dimmed = Image.eval(crop, lambda value: int(value * 0.22))
                composed = Image.composite(crop, dimmed, stencil)
                composed.thumbnail((768, 768), Image.Resampling.LANCZOS)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(".tmp")
                composed.save(temporary, format="JPEG", quality=88, optimize=True)
                temporary.replace(destination)
                return destination
        except (OSError, ValueError, TypeError):
            return None

    def _region_provenance(self, task_id, region, record=None):
        """Where the crop pixels and the region geometry came from."""
        if record is None:
            _, record, _ = self._regions(task_id)
        mask = self._region_mask(task_id, region)
        coverage = None
        box = None
        if mask is not None and mask.any():
            ys, xs = np.nonzero(mask)
            box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
            coverage = round(float(mask.sum()) / max(1, (box[2] - box[0]) * (box[3] - box[1])), 4)
        return {
            "type": "lithology_segmentation_region",
            "segmentation_execution_id": record.get("execution_id"),
            "segmentation_status": record.get("status"),
            "segmentation_method": region.get("segmentation_method"),
            "label_map": "lithology_mask.png",
            "label_value": region.get("label_value"),
            "region_mask": region.get("mask_path"),
            "pixel_source": "upload_original.png",
            "pixel_area": region.get("pixel_area"),
            "bounding_box": box,
            "region_coverage_in_box": coverage,
            "note": "区域几何来自岩性区域分割成果，像素取自 EXIF 校正原图；未使用叠加图的调色板颜色。",
        }

    # ------------------------------------------------------------- features
    def _region_arrays(self, task_id, region):
        source_image = self._original_image(task_id)
        mask = self._region_mask(task_id, region)
        if source_image is None or mask is None:
            return None, None
        with Image.open(source_image) as image:
            if mask.shape != (image.height, image.width):
                return None, None
            ys, xs = np.nonzero(mask)
            if xs.size < MIN_REGION_PIXELS:
                return None, None
            box = (max(0, int(xs.min()) - 8), max(0, int(ys.min()) - 8),
                   min(image.width, int(xs.max()) + 9), min(image.height, int(ys.max()) + 9))
            crop = np.asarray(image.convert("RGB").crop(box), dtype=np.float32) / 255.0
            sub_mask = mask[box[1]:box[3], box[0]:box[2]]
        return crop, sub_mask

    @staticmethod
    def _grain_scale(gray, mask):
        """Characteristic grain size from octave-band contrast energy.

        Each band is the difference between two Gaussian blurs, so the energy
        of a band measures how much contrast lives at that scale. The
        energy-weighted centroid of the band scales is the "typical feature
        size" in pixels - a grain-size proxy rather than a measurement.
        """
        from scipy.ndimage import gaussian_filter

        previous = gaussian_filter(gray, 1.0)
        bands = []
        for sigma in (2.0, 4.0, 8.0, 16.0, 32.0):
            current = gaussian_filter(gray, sigma)
            detail = previous - current
            energy = float((detail[mask] ** 2).mean()) if mask.any() else 0.0
            bands.append((sigma, energy))
            previous = current
        total = sum(energy for _, energy in bands)
        if total <= 1e-12:
            return 4.0
        return float(sum(scale * energy for scale, energy in bands) / total)

    @staticmethod
    def _texture_roughness(gray, mask):
        """Fine-detail share: how much of the local contrast is high frequency."""
        from scipy.ndimage import gaussian_filter

        detail = gray - gaussian_filter(gray, 2.5)
        spread = float(np.std(gray[mask])) or 1e-6
        return float(np.clip(np.std(detail[mask]) / spread, 0, 1))

    def _visual_features(self, task_id, region):
        """Measure the segmentation region itself, not the coloured overlay.

        Two corrections keep the numbers comparable across photos:
        exposure (lightness is normalised to the region's own 95th percentile)
        and mask edges (gradient statistics only use eroded interior pixels, so
        the dimmed background cannot masquerade as texture).
        """
        from scipy.ndimage import binary_erosion

        crop, mask = self._region_arrays(task_id, region)
        if crop is None or mask.sum() < MIN_REGION_PIXELS:
            return None
        interior = binary_erosion(mask, iterations=3)
        if interior.sum() < MIN_REGION_PIXELS / 3:
            interior = mask
        lab = rgb2lab(crop)
        raw = lab[..., 0][mask] / 100.0
        reference = float(np.percentile(raw, 95)) or 1.0
        lightness = np.clip(lab[..., 0] / 100.0 / reference, 0, 1)
        values = lightness[mask]
        chroma = np.hypot(lab[..., 1], lab[..., 2])[mask] / 128.0
        gray = crop.mean(axis=2).astype(np.float32)
        gx = np.zeros_like(gray)
        gy = np.zeros_like(gray)
        gx[:, 1:] = np.diff(gray, axis=1)
        gy[1:, :] = np.diff(gray, axis=0)
        jxx = float((gx[interior] ** 2).mean())
        jyy = float((gy[interior] ** 2).mean())
        jxy = float((gx[interior] * gy[interior]).mean())
        trace = jxx + jyy
        disc = math.sqrt(max(0.0, (jxx - jyy) ** 2 + 4 * jxy ** 2))
        anisotropy = float(disc / trace) if trace > 1e-9 else 0.0
        angle = 0.5 * math.atan2(2 * jxy, jxx - jyy)
        median = float(np.median(values))
        speckle = float((np.abs(values - median) > 0.12).mean())
        banding = 0.0
        if mask.sum() > 64:
            ys, xs = np.nonzero(mask)
            projection = xs * math.cos(angle) + ys * math.sin(angle)
            span = float(np.ptp(projection)) or 1.0
            bins = np.clip(((projection - projection.min()) / span * 16).astype(np.int32), 0, 15)
            centred = values - median
            means = np.bincount(bins, weights=centred, minlength=16) / np.maximum(np.bincount(bins, minlength=16), 1)
            banding = float(np.clip(means.var() / max(1e-9, centred.var()), 0.0, 1.0))
        grain = self._grain_scale(gray, mask)
        roughness = self._texture_roughness(gray, mask)
        return {
            "L": round(float(np.clip(values.mean(), 0, 1)), 4),
            "C": round(float(np.clip(chroma.mean(), 0, 1)), 4),
            "G": round(float(np.clip(roughness, 0, 1)), 4),
            "S": round(float(np.clip(speckle, 0, 1)), 4),
            "A": round(float(np.clip(anisotropy, 0, 1)), 4),
            "B": round(float(np.clip(banding, 0, 1)), 4),
            "Z": round(float(np.clip(math.log2(max(grain, 2.0) / 4.0) / 4.0, 0, 1)), 4),
            "grain_px": round(float(grain), 2),
            "pixel_count": int(mask.sum()),
            "exposure_reference": round(reference, 3),
            "source": "lithology_segmentation/lithology_mask.png + upload_original.png",
            "source_status": "segmentation_result_crop",
            "features_version": "region_visual_v3",
        }

    # ---------------------------------------------------------- classifiers
    @staticmethod
    def _heuristic_candidates(features):
        brightness, contrast, edge_density = features["L"], features["S"], features["G"]
        profiles = {
            "visual_light_compact": 0.55 * (1 - min(1.0, abs(brightness - 0.72) / 0.72)) + 0.45 * (1 - min(1.0, abs(contrast - 0.12) / 0.25)),
            "visual_dark_rough": 0.55 * (1 - min(1.0, abs(brightness - 0.28) / 0.72)) + 0.45 * (1 - min(1.0, abs(edge_density - 0.24) / 0.5)),
            "visual_mixed_texture": 0.55 * (1 - min(1.0, abs(brightness - 0.5) / 0.5)) + 0.45 * (1 - min(1.0, abs(contrast - 0.24) / 0.35)),
        }
        return [{"label": label, "score": round(value, 5), "status": "candidate_only",
                 "category_status": "visual_descriptor"}
                for label, value in sorted(profiles.items(), key=lambda item: item[1], reverse=True)]

    def _kb_candidates(self, features):
        """Score the region against every profiled rock in knowledge base B."""
        catalog = self.knowledge.records("lithology", "", 400)
        scored = []
        for record in catalog.get("records", []):
            profile = record.get("visual_profile") or {}
            if not profile:
                continue
            total = 0.0
            matched = {}
            for key, (weight, tolerance) in FEATURE_SPEC.items():
                ideal = float(profile.get(key, 0.5))
                actual = float(features.get(key, 0.0))
                delta = (actual - ideal) / tolerance
                total += weight * delta * delta
                matched[key] = round(max(0.0, 1 - abs(actual - ideal) / (tolerance * 2)), 3)
            similarity = math.exp(-1.2 * total)
            scored.append({
                "lithology": record["standard_name"], "record_id": record["record_id"],
                "group": record.get("rock_group"), "subgroup": record.get("subgroup"),
                "archetype": profile.get("archetype"), "family": profile.get("family"),
                "similarity": round(similarity, 4),
                "feature_match": matched,
                "descriptors": profile.get("descriptors", []),
                "distinguishing": profile.get("distinguishing"),
                "confusable_with": profile.get("confusable_with", []),
                "profile_basis": profile.get("basis"),
            })
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored

    @staticmethod
    def _probabilities(scored):
        """Softmax over the whole catalogue: relative support, not a calibrated probability."""
        if not scored:
            return {}
        top = scored[0]["similarity"]
        weights = {item["record_id"]: math.exp((item["similarity"] - top) / PROBABILITY_TEMPERATURE)
                   for item in scored}
        total = sum(weights.values()) or 1.0
        return {record_id: weight / total for record_id, weight in weights.items()}

    @staticmethod
    def _rank_candidates(scored, limit=3, probabilities=None):
        """Normalise the top candidates so their shares read as relative support."""
        top = scored[:max(1, limit)]
        total = sum(item["similarity"] for item in top) or 1.0
        for item in top:
            item["score"] = round(item["similarity"] / total, 4)
            if probabilities:
                item["probability"] = round(probabilities.get(item["record_id"], 0.0), 4)
            item["status"] = "candidate_only"
            item["category_status"] = "lithology_candidate_from_visual_profile"
        return top

    @staticmethod
    def _family_candidates(scored, limit=3, probabilities=None):
        """Aggregate the per-rock scores into rock families.

        Rock variants inside one family are visually near-identical here, so a
        family-level answer is the honest reading of a photo-only comparison.
        """
        families = {}
        for item in scored:
            family = item.get("family") or "未分类"
            entry = families.setdefault(family, {"family": family, "similarity": 0.0,
                                                 "members": [], "probability": 0.0})
            entry["similarity"] = max(entry["similarity"], item["similarity"])
            entry["members"].append(item["lithology"])
            if probabilities:
                entry["probability"] += probabilities.get(item["record_id"], 0.0)
        ordered = sorted(families.values(),
                         key=lambda item: (item["probability"], item["similarity"]), reverse=True)
        remainder = sum(item["probability"] for item in ordered[limit:])
        ranked = ordered[:limit]
        if remainder > 0.001:
            ranked = ranked + [{"family": "其他岩类", "similarity": 0.0, "members": [],
                                "probability": remainder}]
        total = sum(item["similarity"] for item in ranked) or 1.0
        for item in ranked:
            item["score"] = round(item["similarity"] / total, 4)
            item["similarity"] = round(item["similarity"], 4)
            item["probability"] = round(item["probability"], 4)
            entry_members = [name for name in item["members"]]
            item["members"] = entry_members[:6]
        return ranked

    # -------------------------------------------------------------- actions
    def _judgement_support(self, task_id):
        state = self._read_state(task_id)
        return state

    def state(self, task_id):
        try:
            task, split_record, regions = self._regions(task_id)
        except WorkspaceError as exc:
            if exc.status_code != 409:
                raise
            # The combined step can load before segmentation. Keep it visible
            # and actionable instead of turning a prerequisite into a 409.
            task = self.workspace._load(task_id)
            return {
                "task_id": task_id, "status": "needs_segmentation", "segmentation_required": True,
                "regions": [], "model_id": self._read_state(task_id).get("model_id", DEFAULT_CLASSIFIER),
                "substages": self.substage_status(task_id),
                "models": CLASSIFIERS, "default_model": DEFAULT_CLASSIFIER,
                "knowledge": self.knowledge.summary(),
                "history": self._history_summary(self._read_state(task_id)),
                "warnings": [str(exc)],
            }
        state = self._read_state(task_id)
        output = []
        for region in regions:
            rid = region["region_id"]
            entry = dict(state.get("regions", {}).get(rid, {}))
            if entry.get("source_execution_id") and entry.get("source_execution_id") != split_record.get("execution_id"):
                entry = {"status": "stale", "source_execution_id": split_record.get("execution_id")}
            crop = self._crop_path(task_id, region)
            output.append({
                "region_id": rid, "pixel_area": region.get("pixel_area"),
                "area_ratio": region.get("area_ratio"), "polygon": region.get("polygon"),
                "crop_url": f"/api/tasks/{task_id}/lithology-judgment/regions/{rid}/crop" if crop else None,
                "source_execution_id": split_record.get("execution_id"),
                "input": self._region_provenance(task_id, region, split_record),
                **entry,
            })
        return {
            "task_id": task_id, "status": "ready" if regions else "unavailable",
            "segmentation_required": False,
            "segmentation": {
                "execution_id": split_record.get("execution_id"),
                "status": split_record.get("status"),
                "finished_at": split_record.get("finished_at"),
                "region_count": len(regions),
            },
            "regions": output, "model_id": state.get("model_id", DEFAULT_CLASSIFIER),
            "substages": self.substage_status(task_id),
            "models": CLASSIFIERS, "default_model": DEFAULT_CLASSIFIER,
            "knowledge": self.knowledge.summary(),
            "knowledge_overview": self.knowledge.overview(),
            "history": self._history_summary(state), "warnings": state.get("warnings", []),
        }

    @staticmethod
    def _history_summary(state, limit: int = 12):
        """Lightweight run log for the workbench.

        Full run payloads (candidate lists, per-region conclusions) stay in the
        task state file; embedding them here made every page entry transfer
        several megabytes.
        """
        runs = state.get("history", [])
        recent = [{key: run.get(key) for key in
                   ("run_id", "created_at", "substage", "model_id", "provider_config_id", "region_ids")
                   if run.get(key) is not None}
                  for run in runs[-limit:]]
        return {"total": len(runs), "runs": list(reversed(recent))}

    def classify(self, task_id, body: ClassifyRequest):
        task, split_record, regions = self._regions(task_id)
        selected = set(body.region_ids or [r["region_id"] for r in regions])
        state = self._read_state(task_id)
        state["model_id"] = body.model_id
        state.setdefault("regions", {})
        unavailable = body.model_id in {"", "unconfigured", "none"}
        rule_based = body.model_id == "rules_litho_kb_v1"
        heuristic = body.model_id in {"heuristic_color_texture_v1", "color_texture_candidate_v1"}
        vision = body.model_id == "vision_llm"
        for region in regions:
            if region["region_id"] not in selected:
                continue
            entry = state["regions"].setdefault(region["region_id"], {})
            entry["source_execution_id"] = split_record.get("execution_id")
            entry["classification_model"] = body.model_id
            # A different model must not inherit previous probabilities or
            # families; otherwise two models' outputs mix in one region card.
            for key in ("families", "probabilities", "probability_note",
                        "candidate_summary", "visual_profile"):
                entry.pop(key, None)
            if unavailable:
                entry.update(candidates=[], classification_status="unconfigured")
                continue
            if vision:
                entry.update(candidates=[], classification_status="needs_provider",
                             classification_note="大模型视觉判别需要 provider 配置与网络可达；先用本地模型产生候选。")
                continue
            if not (rule_based or heuristic):
                entry.update(candidates=[], classification_status="model_not_implemented")
                continue
            features = self._visual_features(task_id, region)
            if features is None:
                entry.update(candidates=[], classification_status="input_unavailable",
                             classification_note="区域过小或缺少分割 Mask，未生成候选。")
                continue
            entry["source_execution_id"] = split_record.get("execution_id")
            entry["visual_profile"] = features
            entry["input"] = self._region_provenance(task_id, region, split_record)
            if rule_based:
                scored = self._kb_candidates(features)
                probabilities = self._probabilities(scored)
                entry["probabilities"] = probabilities
                entry["candidates"] = self._rank_candidates(scored, 3, probabilities)
                entry["families"] = self._family_candidates(scored, probabilities=probabilities)
                family_probability = {item["family"]: item["probability"] for item in entry["families"]}
                for candidate in entry["candidates"]:
                    total = family_probability.get(candidate.get("family")) or 0.0
                    candidate["probability_in_family"] = (round(candidate["probability"] / total, 4)
                                                          if total else None)
                entry["probability_note"] = (
                    "概率为视觉档案相似度经 softmax（温度 0.15）归一到目录的结果，"
                    "表示相对支持度，不是统计校准概率。")
                entry["classification_status"] = "candidate_only"
                top = entry["candidates"][0] if entry["candidates"] else None
                family = entry["families"][0] if entry["families"] else None
                if top is None:
                    entry["candidate_summary"] = "该区域没有匹配到任何视觉档案，未给出候选。"
                else:
                    entry["candidate_summary"] = (
                        f"岩类候选：{family['family']}（相对份额 {family['score']:.0%}）；"
                        f"细候选 {top['lithology']}（相似度 {top['similarity']:.2f}）。"
                        f"依据为颜色/纹理/定向性/颗粒尺度的视觉档案比对，需现场或薄片复核。")
                if family and family["similarity"] < 0.45:
                    entry["classification_status"] = "insufficient_evidence"
                    entry["candidate_summary"] += " 最高相似度偏低，建议人工判读。"
            else:
                entry["candidates"] = self._heuristic_candidates(features)
                entry["families"] = []
                entry["probabilities"] = {}
                entry["classification_status"] = "candidate_only"
                entry["candidate_summary"] = "该模型只输出视觉描述（浅色致密/深色粗糙/混合纹理），不产生岩性类别与概率。"
        if unavailable:
            state["warnings"] = ["分类模型未配置，未生成岩性类别。"]
        elif rule_based:
            state["warnings"] = ["已按知识库 B 的视觉期望档案生成岩性候选；候选不等于经过地质验证的岩性结论。"]
        elif heuristic:
            state["warnings"] = ["已运行颜色/纹理候选基线；输出是分割区域的视觉描述，不构成岩性分类。"]
        elif vision:
            state["warnings"] = ["大模型视觉判别需要先配置可用的 OpenAI 兼容接口。"]
        else:
            state["warnings"] = [f"分类模型 {body.model_id} 尚未接入，未生成候选类别。"]
        statuses = [entry.get("classification_status") for entry in state["regions"].values()]
        usable = sum(1 for item in statuses if item in {"candidate_only", "insufficient_evidence"})
        self._set_substage(state, "algorithm_classification", {
            "status": "completed" if usable else "warning",
            "run_id": uuid.uuid4().hex,
            "model_id": body.model_id,
            "source_execution_id": split_record.get("execution_id"),
            "summary": {"regions": len(statuses), "with_candidates": usable, "model": body.model_id},
        })
        self._invalidate(state, "knowledge_llm", "synthesis")
        state["updated_at"] = _now()
        self._save_state(task_id, state)
        return self.state(task_id)

    def retrieve(self, task_id, body: RetrieveRequest):
        current = self.state(task_id)
        state = self._read_state(task_id)
        selected = set(body.region_ids or [r["region_id"] for r in current["regions"]])
        for region in current["regions"]:
            if region["region_id"] not in selected:
                continue
            entry = state.setdefault("regions", {}).setdefault(region["region_id"], {})
            names = [candidate.get("lithology") or candidate.get("label") for candidate in (entry.get("candidates") or [])]
            query = body.query or " ".join(name for name in names[:2] if name)
            evidence = self.knowledge.search(query, body.limit)
            entry["knowledge_evidence"] = evidence
            entry["knowledge_query"] = query
            entry["retrieval_status"] = "completed"
            entry["source_execution_id"] = region.get("source_execution_id")
        state["knowledge_query"] = body.query
        state["updated_at"] = _now()
        self._save_state(task_id, state)
        return self.state(task_id)

    # -------------------------------------------------------------- provider
    def _provider(self, config_id):
        data = _read_json(self._config_path(), {})
        return data.get(config_id) or {}

    @staticmethod
    def _opener(provider):
        """Direct connection unless an explicit proxy is configured."""
        proxy = str(provider.get("proxy") or "").strip()
        handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                   if proxy else urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(handler)

    @staticmethod
    def _extract_json(text):
        """Parse the first JSON object in a reply, tolerating fences/prose."""
        if not text:
            return None
        cleaned = str(text).strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
        try:
            return json.loads(cleaned)
        except ValueError:
            pass
        start = cleaned.find("{")
        if start < 0:
            return None
        depth = 0
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start:index + 1])
                    except ValueError:
                        return None
        return None

    @staticmethod
    def _auth_headers(provider):
        """Gateways differ in how the key is carried; keep all three styles."""
        key = provider.get("api_key", "")
        style = str(provider.get("auth_style", "bearer"))
        if style == "header":
            return {"api-key": key, "Content-Type": "application/json"}
        if style == "api_key":
            return {"Authorization": "Api-Key " + key, "Content-Type": "application/json"}
        return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    def _status_path(self):
        return self.root / "provider-status.json"

    def provider_status(self):
        return _read_json(self._status_path(), {})

    def _record_status(self, config_id, payload):
        data = _read_json(self._status_path(), {})
        data[config_id] = {"checked_at": _now(), **payload}
        self._atomic(self._status_path(), data)
        return payload

    def list_models(self, config_id, refresh: bool = False):
        """Read Model IDs from the OpenAI-compatible /models endpoint.

        The workbench shows this list on page entry, so a short-lived cache is
        used and the network timeout is kept small: an unreachable gateway must
        not stall the UI for 30 s.
        """
        provider = self._provider(config_id)
        if not provider.get("base_url") or not provider.get("api_key"):
            return {"status": "not_configured", "models": [], "message": "尚未填写 Base URL 或 API Key。"}
        cache = getattr(self, "_models_cache", None)
        if cache is None:
            cache = self._models_cache = {}
        signature = (provider.get("base_url"), provider.get("model"), provider.get("auth_style"))
        entry = cache.get(config_id)
        now = time.monotonic()
        if not refresh and entry and entry[0] == signature and now - entry[1] < 300:
            return entry[2]
        request = urllib.request.Request(
            str(provider["base_url"]).rstrip("/") + "/models",
            headers=self._auth_headers(provider), method="GET")
        try:
            with self._opener(provider).open(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            models = payload.get("data") if isinstance(payload, dict) else []
            ids = [item.get("id") for item in models if isinstance(item, dict) and item.get("id")]
            result = {"status": "ok", "models": ids, "count": len(ids)}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            result = {"status": "failed", "models": [], "http_status": exc.code, "message": detail}
        except Exception as exc:
            result = {"status": "failed", "models": [], "message": f"{type(exc).__name__}: {exc}"}
        cache[config_id] = (signature, time.monotonic(), result)
        return result

    def _region_image_data(self, task_id, region):
        """Bounded region crop for an OpenAI-compatible vision call."""
        source = self._crop_path(task_id, region)
        if source is None or not source.is_file():
            return None
        try:
            with Image.open(source) as image:
                buffer = BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
                return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except (OSError, ValueError, TypeError):
            return None

    def _call_provider(self, provider, task_id, region, candidates, evidence):
        base = str(provider.get("base_url", "")).rstrip("/")
        key = provider.get("api_key", "")
        model = provider.get("model", "")
        if not base or not key or not model:
            return None, "provider_not_configured"
        text = json.dumps({"region_id": region["region_id"], "candidates": candidates,
                           "knowledge": evidence}, ensure_ascii=False)
        content = [{"type": "text", "text": text}]
        image_data = self._region_image_data(task_id, region)
        if image_data:
            content.append({"type": "image_url", "image_url": {"url": image_data, "detail": "low"}})
        text_payload = text
        schema = {"type": "object", "additionalProperties": False, "properties": {
            "region_id": {"type": "string"}, "final_lithology": {"type": "string"},
            "category": {"type": "string"}, "reason": {"type": "string"},
            "confidence_level": {"type": "string"}, "status": {"type": "string"},
            "needs_review": {"type": "boolean"}, "candidates": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": {"type": "string"}}
        }, "required": ["region_id", "final_lithology", "category", "reason", "confidence_level",
                        "status", "needs_review", "candidates", "evidence"]}
        payload = {
            "model": model, "temperature": provider.get("temperature", .1),
            "top_p": provider.get("top_p", .8), "stream": False,
            "max_tokens": provider.get("max_tokens", 800),
            "messages": [
                {"role": "system", "content": "你是岩性候选判读助手。证据不足时必须返回 unknown 和 needs_review=true。只返回JSON。"},
                {"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "lithology_judgment", "strict": True, "schema": schema}},
        }
        if provider.get("reasoning_effort"):
            payload["reasoning_effort"] = provider["reasoning_effort"]

        opener = self._opener(provider)

        def request_once(body):
            request = urllib.request.Request(
                base + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=self._auth_headers(provider), method="POST")
            with opener.open(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))

        def describe(exc):
            detail = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            try:
                error = json.loads(body).get("error") or {}
                detail = error.get("message") if isinstance(error, dict) else str(error)
            except ValueError:
                detail = body[:200]
            return f"provider_call_failed: HTTP {exc.code}{(' · ' + detail) if detail else ''}"

        # Gateways differ: JSON Schema, JSON mode only, or no image support at
        # all. Try the strictest form first and degrade explicitly.
        text_only = {"type": "text", "text": text_payload}
        variants = [
            ("json_schema", payload),
            ("json_object", {**payload, "response_format": {"type": "json_object"}}),
            ("text_only", {**payload, "messages": [{"role": "system", "content": payload["messages"][0]["content"]},
                                                   {"role": "user", "content": [text_only]}],
                           "response_format": {"type": "json_object"}}),
        ]
        last_error = None
        for mode, body in variants:
            try:
                parsed = request_once(body)
                choice = (parsed.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                result = (self._extract_json(message.get("content"))
                          or self._extract_json(message.get("reasoning")))
                if result is None:
                    last_error = (f"provider_reply_unparsed: finish_reason="
                                  f"{choice.get('finish_reason')} content={str(message.get('content'))[:120]}")
                    continue
                result["provider_mode"] = mode
                result["image_attached"] = mode != "text_only"
                return result, None
            except urllib.error.HTTPError as exc:
                last_error = describe(exc)
                if exc.code not in {400, 422}:
                    return None, last_error
            except Exception as exc:
                last_error = f"provider_call_failed: {type(exc).__name__}: {exc}"
        return None, last_error or "provider_call_failed"

    def infer(self, task_id, body: InferRequest):
        """Sub-stage 3: knowledge retrieval + provider call (no final conclusion)."""
        current = self.state(task_id)
        selected = set(body.region_ids or [r["region_id"] for r in current["regions"]])
        provider = self._provider(body.provider_config_id)
        state = self._read_state(task_id)
        for region in current["regions"]:
            if region["region_id"] not in selected:
                continue
            entry = state.setdefault("regions", {}).setdefault(region["region_id"], {})
            entry["source_execution_id"] = region.get("source_execution_id")
            result, error = self._call_provider(provider, task_id, region, entry.get("candidates", []),
                                                entry.get("knowledge_evidence", self.knowledge.search("", 10)))
            if result:
                result.setdefault("region_id", region["region_id"])
                result.setdefault("final_lithology", result.get("category"))
                result.setdefault("candidates", entry.get("candidates", []))
                result.setdefault("evidence", entry.get("knowledge_evidence", []))
                result.setdefault("reason", "模型返回结构化结果")
                result.setdefault("confidence_level", "unknown")
                entry["llm_conclusion"] = {"status": "completed", **result}
                entry["llm_status"] = "completed"
            else:
                entry["llm_conclusion"] = {
                    "region_id": region["region_id"], "status": "unavailable",
                    "error": error or "unavailable",
                }
                entry["llm_status"] = error or "unavailable"
        run_id = uuid.uuid4().hex
        completed = sum(1 for rid in selected
                        if (state.get("regions", {}).get(rid, {}).get("llm_status") == "completed"))
        state["history"] = (state.get("history", []) + [{
            "run_id": run_id, "created_at": _now(), "substage": "knowledge_llm",
            "model_id": body.model_id, "provider_config_id": body.provider_config_id,
            "region_ids": sorted(selected),
            "results": {rid: state.get("regions", {}).get(rid, {}).get("llm_conclusion") for rid in sorted(selected)},
        }])[-50:]
        status = "completed" if completed == len(selected) else ("failed" if completed == 0 else "warning")
        self._set_substage(state, "knowledge_llm", {
            "status": status, "run_id": run_id, "provider_config_id": body.provider_config_id,
            "source_algorithm_run": (state.get("substages", {}).get("algorithm_classification", {}) or {}).get("run_id"),
            "summary": {"regions": len(selected), "with_llm_result": completed},
        })
        self._invalidate(state, "synthesis")
        state["updated_at"] = _now()
        self._save_state(task_id, state)
        return self.state(task_id) | {"run_id": run_id}

    def run_all(self, task_id, body: RunAllRequest):
        """Run the three classification sub-stages in order (segmentation must exist)."""
        self.run_substage(task_id, "algorithm_classification", body)
        state = self._read_state(task_id)
        has_candidates = any((entry.get("candidates") or []) for entry in (state.get("regions") or {}).values())
        if has_candidates:
            self.run_substage(task_id, "knowledge_llm", body)
        else:
            state = self._read_state(task_id)
            self._set_substage(state, "knowledge_llm", {
                "status": "blocked",
                "summary": {"regions": 0, "with_llm_result": 0},
                "note": "算法判定未产生候选，未提交大模型。",
            })
            self._save_state(task_id, state)
        return self.run_substage(task_id, "synthesis", body)["state"]

    def history(self, task_id, limit: int | None = None, full: bool = False):
        runs = self._read_state(task_id).get("history", [])
        if limit:
            runs = runs[-max(1, int(limit)):]
        if not full:
            runs = [{key: run.get(key) for key in
                     ("run_id", "created_at", "substage", "model_id", "provider_config_id", "region_ids")
                     if run.get(key) is not None} | ({"result_count": len(run.get("results") or {})}
                                                     if run.get("results") is not None else {})
                    for run in runs]
        return {"task_id": task_id, "runs": runs}

    def test_provider(self, config_id):
        """Probe the configured endpoint and report exactly what came back."""
        provider = self._provider(config_id)
        if not provider.get("base_url") or not provider.get("api_key") or not provider.get("model"):
            return {"status": "not_configured", "config_id": config_id,
                    "message": "尚未填写 Base URL、API Key 或模型名称。"}
        base = str(provider["base_url"]).rstrip("/")
        payload = {"model": provider["model"], "max_tokens": 8,
                   "temperature": 0, "top_p": provider.get("top_p", .8), "stream": False,
                   "messages": [{"role": "user", "content": "ping"}]}
        if provider.get("reasoning_effort"):
            payload["reasoning_effort"] = provider["reasoning_effort"]
        request = urllib.request.Request(
            base + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
            headers=self._auth_headers(provider), method="POST")
        opener = self._opener(provider)
        started = datetime.now(timezone.utc)
        try:
            with opener.open(request, timeout=60) as response:
                body = response.read().decode("utf-8", "replace")
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            echoed = ""
            try:
                echoed = json.loads(body).get("model", "")
            except ValueError:
                pass
            return self._record_status(config_id, {
                "status": "ok", "http_status": 200, "latency_seconds": round(elapsed, 3),
                "model_echo": echoed, "model": provider["model"],
                "message": f"接口连通，模型 {echoed or provider['model']} 已响应。"})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            message, code = detail, ""
            try:
                parsed = json.loads(detail)
                error = parsed.get("error") or {}
                if isinstance(error, dict):
                    message, code = error.get("message") or detail, str(error.get("code") or "")
                elif isinstance(error, str):
                    message = error
            except ValueError:
                pass
            return self._record_status(config_id, {
                "status": "failed", "http_status": exc.code, "gateway_code": code,
                "message": message, "model": provider["model"],
                "hint": self._failure_hint(message)})
        except Exception as exc:
            return self._record_status(config_id, {
                "status": "failed", "message": f"{type(exc).__name__}: {exc}",
                "hint": "无法建立连接；请检查本机网络、代理与 Base URL。"})

    # ------------------------------------------------------------ substages
    def _substages(self, state):
        entries = state.setdefault("substages", {})
        for sid in SUBSTAGE_IDS:
            entries.setdefault(sid, {"status": "pending"})
        return entries

    def _set_substage(self, state, sid, fields):
        entry = self._substages(state)[sid]
        entry.update(fields)
        entry["updated_at"] = _now()
        return entry

    def _invalidate(self, state, *sids):
        """Mark downstream sub-stages stale; their inputs are no longer current."""
        for sid in sids:
            entry = self._substages(state).get(sid) or {}
            if entry.get("status") in {"completed", "warning", "failed"}:
                entry["status"] = "stale"

    def _segmentation_state(self, task_id):
        """Read the real workspace stage so sub-stage 1 mirrors the pipeline."""
        try:
            task, record, regions = self._regions(task_id)
        except WorkspaceError:
            return {"status": "pending", "execution_id": None, "region_count": 0}
        stage = (task.get("stages") or {}).get("lithology_segmentation", {})
        return {
            "status": "completed" if stage.get("status") in {"completed", "warning"} else (stage.get("status") or "pending"),
            "stage_status": stage.get("status"),
            "execution_id": record.get("execution_id"),
            "finished_at": record.get("finished_at"),
            "method": record.get("method"),
            "region_count": len(regions),
        }

    def substage_status(self, task_id):
        state = self._read_state(task_id)
        entries = self._substages(state)
        segmentation = self._segmentation_state(task_id)
        algorithm = entries["algorithm_classification"]
        knowledge = entries["knowledge_llm"]
        synthesis = entries["synthesis"]

        def effective(entry, prerequisite_ok, run_matches=True):
            status = entry.get("status", "pending")
            if not prerequisite_ok:
                return "blocked"
            if status in {"completed", "warning", "failed"} and not run_matches:
                return "stale"
            return status

        algorithm_run = algorithm.get("run_id")
        knowledge_run = knowledge.get("run_id")
        algorithm_ok = segmentation["status"] == "completed"
        knowledge_ok = algorithm_ok and effective(
            algorithm, True, algorithm.get("source_execution_id") == segmentation["execution_id"]) in {"completed", "warning"}
        result = []
        for spec in SUBSTAGES:
            sid = spec["id"]
            if sid == "segmentation":
                entry = {"status": segmentation["status"], **segmentation}
            elif sid == "algorithm_classification":
                status = effective(algorithm, algorithm_ok,
                                   algorithm.get("source_execution_id") == segmentation["execution_id"])
                entry = {**algorithm, "status": status}
            elif sid == "knowledge_llm":
                status = effective(knowledge, knowledge_ok,
                                   knowledge.get("source_algorithm_run") == algorithm_run)
                entry = {**knowledge, "status": status}
            else:
                status = effective(synthesis, algorithm_ok,
                                   synthesis.get("source_algorithm_run") == algorithm_run
                                   and synthesis.get("source_knowledge_run") == knowledge_run)
                entry = {**synthesis, "status": status}
            result.append({
                "id": sid, "title": spec["title"], "description": spec["description"],
                "provides": spec["provides"], "depends_on": spec["depends_on"],
                "status": entry.get("status", "pending"),
                "summary": entry.get("summary"), "updated_at": entry.get("updated_at"),
                "source": {key: entry.get(key) for key in
                           ("execution_id", "run_id", "model_id", "provider_config_id") if entry.get(key)},
            })
        return {"substages": result, "segmentation": segmentation,
                "stale": [item["id"] for item in result if item["status"] == "stale"]}

    def _synthesize_region(self, entry):
        """Sub-stage 4: turn probabilities + model opinion into one conclusion."""
        families = entry.get("families") or []
        candidates = entry.get("candidates") or []
        llm = entry.get("llm_conclusion") or {}
        top = candidates[0] if candidates else None
        top_family = families[0] if families else None
        p_family = float((top_family or {}).get("probability") or 0)
        llm_name = str(llm.get("final_lithology") or llm.get("category") or "").strip()
        provider = self._provider("default")
        provider_ready = bool(provider.get("base_url") and provider.get("api_key") and provider.get("model"))
        llm_error = llm.get("error") or (
            "provider_not_configured" if not provider_ready else
            ("未运行" if llm.get("status") in {None, ""} else llm.get("status")))
        catalog = {item["standard_name"]: item for item in
                   self.knowledge.records("lithology", "", 400).get("records", [])}
        llm_family = None
        if llm_name and llm_name in catalog:
            llm_family = (catalog[llm_name].get("visual_profile") or {}).get("family")

        base = {
            "region_id": entry.get("region_id"),
            "algorithm": {
                "family": (top_family or {}).get("family"),
                "family_probability": round(p_family, 4),
                "top_lithology": (top or {}).get("lithology"),
                "top_probability": (top or {}).get("probability"),
                "candidates": candidates,
            },
            "llm": llm or None,
            "evidence": entry.get("knowledge_evidence") or [],
        }
        has_lithology = any(isinstance(item, dict) and item.get("lithology") for item in candidates)
        if candidates and not has_lithology and not llm_name:
            return {**base, "final_lithology": None, "category": None, "confidence_level": "none",
                    "status": "unavailable", "conclusion_status": "no_candidates", "needs_review": True,
                    "reason": ("当前分类模型只输出视觉描述，没有岩性类别；请切换到 rules_litho_kb_v1 "
                               "或配置可用的大模型后再判定。"), "basis": "候选不是岩性"}
        if not candidates:
            return {**base, "final_lithology": None, "category": None, "confidence_level": "none",
                    "status": "unavailable", "conclusion_status": "no_candidates", "needs_review": True,
                    "reason": (f"算法判定未产生候选（{entry.get('classification_status')}）；"
                               f"大模型：{llm_error or '未运行'}。"), "basis": "无可用特征或区域过小"}
        if llm_name and llm_name in catalog:
            agrees = bool(llm_family and top_family and llm_family == top_family["family"]) or \
                any(item.get("lithology") == llm_name for item in candidates)
            confidence = "high" if agrees and p_family >= 0.6 else ("medium" if agrees else "low")
            if agrees:
                return {**base, "final_lithology": llm_name, "category": llm_family or (top_family or {}).get("family"),
                        "confidence_level": confidence, "status": "confirmed", "conclusion_status": "confirmed",
                        "needs_review": False,
                        "reason": (f"大模型结论「{llm_name}」与算法岩类{llm_family or ''}"
                                   f"（概率 {p_family:.0%}）一致。"), "basis": "算法概率 + 知识库证据 + 大模型判读"}
            return {**base, "final_lithology": llm_name, "category": llm_family,
                    "confidence_level": "low", "status": "needs_review", "conclusion_status": "conflict",
                    "needs_review": True,
                    "reason": (f"大模型结论「{llm_name}」与算法岩类{(top_family or {}).get('family')}"
                               f"（概率 {p_family:.0%}）不一致，需人工裁决。"), "basis": "两路结论冲突"}
        if p_family >= 0.5:
            return {**base, "final_lithology": (top or {}).get("lithology"), "category": (top_family or {}).get("family"),
                    "confidence_level": "low", "status": "provisional", "conclusion_status": "provisional",
                    "needs_review": True,
                    "reason": (f"仅算法判定可用（{top_family.get('family')} 概率 {p_family:.0%}）；"
                               f"大模型未给出结论（{llm_error or '未运行'}），结论为暂定。"),
                    "basis": "仅算法概率，未经大模型与现场复核"}
        return {**base, "final_lithology": None, "category": (top_family or {}).get("family"),
                "confidence_level": "low", "status": "needs_review", "conclusion_status": "inconclusive",
                "needs_review": True,
                "reason": (f"算法最高岩类概率仅 {p_family:.0%}，证据不足；"
                           f"大模型：{llm_error or '未运行'}。"), "basis": "证据不足"}

    def run_substage(self, task_id, substage_id, body):
        if substage_id not in SUBSTAGE_IDS:
            raise WorkspaceError(f"未知子步骤：{substage_id}", 404)
        if substage_id == "segmentation":
            workspace = self.workspace
            task = workspace.get_task(task_id)
            stage = (task.get("stages") or {}).get("lithology_segmentation", {})
            if stage.get("status") in {"completed", "warning"} and not getattr(body, "force", False):
                return {"task_id": task_id, "substage": substage_id, "status": "completed",
                        "reused": True, "substages": self.substage_status(task_id)["substages"]}
            job = workspace.submit(task_id, "lithology_segmentation", bool(getattr(body, "force", False)), False)
            state = self._read_state(task_id)
            self._set_substage(state, "segmentation", {"status": "running", "job_id": job.get("job_id")})
            self._invalidate(state, "algorithm_classification", "knowledge_llm", "synthesis")
            self._save_state(task_id, state)
            return {"task_id": task_id, "substage": substage_id, "status": "submitted", "job": job,
                    "next": "等待分割完成后重新读取子步骤状态"}
        if substage_id == "algorithm_classification":
            result = self.classify(task_id, ClassifyRequest(region_ids=body.region_ids, model_id=body.model_id))
            return {"task_id": task_id, "substage": substage_id, "status": "completed", "state": result}
        if substage_id == "knowledge_llm":
            current = self.state(task_id)
            usable = [region for region in current["regions"]
                      if region.get("candidates") and region.get("classification_status") != "stale"]
            if not usable:
                raise WorkspaceError("请先运行「算法判定」，当前区域没有可提交的候选", 409)
            ids = body.region_ids or [region["region_id"] for region in usable]
            self.retrieve(task_id, RetrieveRequest(region_ids=ids))
            result = self.infer(task_id, InferRequest(region_ids=ids, model_id=body.model_id,
                                                      provider_config_id=body.provider_config_id))
            status = ((result.get("substages") or {}).get("substages") or [])
            state = self._read_state(task_id)
            knowledge_status = self._substages(state)["knowledge_llm"].get("status")
            return {"task_id": task_id, "substage": substage_id, "status": knowledge_status,
                    "state": result, "substages": status}
        # synthesis
        current = self.state(task_id)
        if not current["regions"]:
            raise WorkspaceError("尚无分割区域，请先运行「岩性分割」", 409)
        state = self._read_state(task_id)
        knowledge_run = (self._substages(state)["knowledge_llm"] or {}).get("run_id")
        algorithm_run = (self._substages(state)["algorithm_classification"] or {}).get("run_id")
        conclusions = {}
        for region in current["regions"]:
            rid = region["region_id"]
            entry = dict(state.get("regions", {}).get(rid) or {})
            entry["region_id"] = rid
            conclusion = self._synthesize_region(entry)
            state.setdefault("regions", {}).setdefault(rid, {})["final_judgment"] = conclusion
            conclusions[rid] = conclusion
        confirmed = sum(1 for item in conclusions.values() if item.get("conclusion_status") == "confirmed")
        self._set_substage(state, "synthesis", {
            "status": "completed" if conclusions else "warning",
            "run_id": uuid.uuid4().hex,
            "source_algorithm_run": algorithm_run, "source_knowledge_run": knowledge_run,
            "summary": {"regions": len(conclusions), "confirmed": confirmed,
                        "provisional": sum(1 for item in conclusions.values()
                                           if item.get("conclusion_status") == "provisional"),
                        "needs_human": sum(1 for item in conclusions.values()
                                           if item.get("conclusion_status") in {"conflict", "inconclusive"})},
        })
        state["history"] = (state.get("history", []) + [{
            "run_id": uuid.uuid4().hex, "created_at": _now(), "substage": "synthesis",
            "region_ids": sorted(conclusions), "results": conclusions,
        }])[-50:]
        state["updated_at"] = _now()
        self._save_state(task_id, state)
        return {"task_id": task_id, "substage": substage_id, "status": "completed",
                "state": self.state(task_id)}

    @staticmethod
    def _failure_hint(message):
        if "网络环境" in message:
            return ("网关看到的出口 IP 不在允许范围：若本机开着 Clash/VPN 等系统代理，"
                    "把「代理」留空（直连校园网/VPN 出口）再测；仍失败则需把出口 IP 加入白名单。"
                    "注意：同一密钥用错密钥试会返回「API密钥不存在」，两者要区分。")
        if "Invalid model" in message:
            return ("网关已到达模型路由，但模型名不在白名单内；请到平台控制台复制模型编码，"
                    "本项目只识别到 qwen2.5-72b-instruct。")
        if "密钥不存在" in message:
            return ("网关识别了鉴权方式（Authorization: Api-Key），但该密钥在该平台不存在或已失效；"
                    "请到平台重新生成密钥，或确认是否属于另一套环境。")
        if "密钥不能为空" in message:
            return ("该网关没有读到密钥：当前 Base URL 可能不支持这种鉴权头，请切换鉴权方式后重试。")
        if "密钥" in message or "key" in message.lower():
            return "网关未接受该密钥，请核对密钥与控制台中的模型权限。"
        return None

    # -------------------------------------------------------------- exports
    def export(self, task_id):
        payload = self.state(task_id)
        payload["exported_at"] = _now()
        payload["annotation_url"] = f"/api/tasks/{task_id}/lithology-judgment/annotation"
        return payload

    def _state_path(self, task_id):
        return self._path(task_id) / "state.json"

    def _read_state(self, task_id):
        path = self._state_path(task_id)
        cache = getattr(self, "_state_cache", None)
        if cache is None:
            cache = self._state_cache = {}
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        if stamp is not None:
            cached = cache.get(task_id)
            if cached and cached[0] == stamp:
                return cached[1]
        value = _read_json(path, {
            "schema_version": "1.1", "task_id": task_id, "regions": {}, "history": [],
            "provider_config_id": "default"})
        if stamp is not None:
            cache[task_id] = (stamp, value)
        return value

    def _save_state(self, task_id, state):
        self._atomic(self._state_path(task_id), state)
        cache = getattr(self, "_state_cache", None)
        if cache:
            cache.pop(task_id, None)

    def _annotation_path(self, task_id):
        destination = self._path(task_id) / "lithology_judgment_annotation.png"
        task, _, regions = self._regions(task_id)
        state = self._read_state(task_id)
        source = self._task_dir(task_id) / "upload_original.png"
        if not source.is_file():
            return None
        # Rendering a full-resolution label image costs seconds; reuse it until
        # the judgement state or the source photo changes.
        try:
            newest = max(self._state_path(task_id).stat().st_mtime_ns, source.stat().st_mtime_ns)
        except OSError:
            newest = None
        if destination.is_file() and newest is not None and destination.stat().st_mtime_ns >= newest:
            return destination
        with Image.open(source) as image:
            base = image.convert("RGBA")
            overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            colors = [(42, 145, 162, 92), (222, 142, 44, 92), (104, 152, 75, 92), (145, 96, 164, 92)]
            for index, region in enumerate(regions):
                color = colors[index % len(colors)]
                for polygon in region.get("polygon") or []:
                    points = [tuple(point) for point in polygon.get("exterior") or []]
                    if len(points) >= 3:
                        draw.polygon(points, fill=color, outline=(color[0], color[1], color[2], 230))
                entry = state.get("regions", {}).get(region["region_id"], {})
                final = entry.get("final_judgment") or {}
                candidates = entry.get("candidates") or []
                label = (final.get("category") or final.get("final_lithology")
                         or (candidates[0].get("lithology") if candidates else None) or "unknown")
                points = [point for polygon in (region.get("polygon") or []) for point in polygon.get("exterior") or []]
                if points:
                    x = sum(float(point[0]) for point in points) / len(points)
                    y = sum(float(point[1]) for point in points) / len(points)
                    draw.text((x, y), f"{region['region_id']}: {label}", fill=(20, 45, 52, 255))
            base = Image.alpha_composite(base, overlay).convert("RGB")
            temporary = destination.with_suffix(".tmp")
            base.save(temporary, format="PNG", compress_level=6)
            temporary.replace(destination)
        return destination


def register_lithology_judgment_routes(app, get_workspace):
    project_root = Path(__file__).resolve().parents[2]
    store = None

    def _revalidated_file(request, path, media_type):
        """File response with an ETag/If-None-Match check (304 when unchanged).

        Starlette 1.x no longer short-circuits conditional requests inside
        ``FileResponse``, so the comparison is done here for the large
        annotation PNG.
        """
        stat = path.stat()
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        headers = {"Cache-Control": "private, max-age=0, must-revalidate", "ETag": etag}
        candidate = (request.headers.get("if-none-match") or "").strip()
        if candidate == "*" or candidate.strip('"') == etag.strip('"'):
            return Response(status_code=304, headers=headers)
        return FileResponse(path, media_type=media_type, headers=headers)

    def current():
        nonlocal store
        if store is None:
            store = LithologyJudgmentStore(get_workspace(), project_root)
        return store

    @app.get("/api/tasks/{task_id}/lithology-judgment/state")
    def state(task_id: str): return current().state(task_id)

    @app.post("/api/tasks/{task_id}/lithology-judgment/classify")
    def classify(task_id: str, body: ClassifyRequest): return current().classify(task_id, body)

    @app.post("/api/tasks/{task_id}/lithology-judgment/retrieve")
    def retrieve(task_id: str, body: RetrieveRequest): return current().retrieve(task_id, body)

    @app.post("/api/tasks/{task_id}/lithology-judgment/infer")
    def infer(task_id: str, body: InferRequest): return current().infer(task_id, body)

    @app.post("/api/tasks/{task_id}/lithology-judgment/run-all")
    def run_all(task_id: str, body: RunAllRequest): return current().run_all(task_id, body)

    @app.post("/api/tasks/{task_id}/lithology-judgment/run-pipeline")
    def run_pipeline(task_id: str, body: CombinedRunRequest):
        workspace = get_workspace()
        task = workspace.get_task(task_id)
        stage = task.get("stages", {}).get("lithology_segmentation", {})
        if body.force_segmentation or stage.get("status") not in {"completed", "warning"}:
            job = workspace.submit(task_id, "lithology_segmentation", body.force_segmentation, False)
            return {
                "task_id": task_id, "status": "segmentation_submitted", "segmentation_required": True,
                "job": job, "next_action": "分割完成后再次调用本接口，或调用 /run-all 执行判别",
            }
        return current().run_all(task_id, RunAllRequest(region_ids=body.region_ids, model_id=body.model_id,
                                                        provider_config_id=body.provider_config_id))

    @app.get("/api/tasks/{task_id}/lithology-judgment/substages")
    def substages(task_id: str): return {"task_id": task_id, **current().substage_status(task_id)}

    @app.post("/api/tasks/{task_id}/lithology-judgment/substages/{substage_id}/run")
    def substage_run(task_id: str, substage_id: str, body: RunAllRequest):
        return current().run_substage(task_id, substage_id, body)

    @app.get("/api/tasks/{task_id}/lithology-judgment/history")
    def history(task_id: str, limit: int | None = None, full: bool = False):
        return current().history(task_id, limit=limit, full=full)

    @app.get("/api/tasks/{task_id}/lithology-judgment/export")
    def export(task_id: str):
        from fastapi.responses import JSONResponse
        return JSONResponse(current().export(task_id),
                           headers={"Content-Disposition": f'attachment; filename="lithology_judgment_{task_id[:8]}.json"'})

    @app.get("/api/tasks/{task_id}/lithology-judgment/annotation")
    def annotation(task_id: str, request: Request):
        path = current()._annotation_path(task_id)
        if path is None:
            raise WorkspaceError("尚无可生成的岩性判定图", 404)
        # Revalidate instead of re-downloading a multi-megabyte PNG: the file is
        # regenerated only when the judgement state changes, so its ETag/date
        # stay stable and repeat views answer 304.
        return _revalidated_file(request, path, "image/png")

    @app.get("/api/tasks/{task_id}/lithology-judgment/regions/{region_id}/crop")
    def crop(task_id: str, region_id: str):
        _, _, regions = current()._regions(task_id)
        region = next((item for item in regions if item.get("region_id") == region_id), None)
        if region is None:
            raise WorkspaceError("岩性区域不存在", 404)
        path = current()._crop_path(task_id, region)
        if path is None:
            raise WorkspaceError("该区域没有可生成的裁剪图", 404)
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=0, must-revalidate"})

    @app.get("/api/lithology-judgment/config")
    def config(): return current().get_config()

    @app.get("/api/lithology-judgment/provider-status")
    def provider_status(): return {"providers": current().provider_status()}

    @app.put("/api/lithology-judgment/config/{config_id}")
    def save_config(config_id: str, body: ProviderConfig): return current().set_config(config_id, body)

    @app.post("/api/lithology-judgment/test-provider/{config_id}")
    def test_provider(config_id: str): return current().test_provider(config_id)

    @app.get("/api/lithology-judgment/models/{config_id}")
    def list_models(config_id: str, refresh: bool = False): return current().list_models(config_id, refresh=refresh)

    @app.get("/api/knowledge/search")
    def search_knowledge(query: str = "", limit: int = 20): return current().knowledge.search(query, limit)

    @app.get("/api/knowledge/graph")
    def knowledge_graph(query: str = "", limit: int = 200, group: str | None = None,
                        source: str | None = None):
        return current().knowledge.graph(query, limit, group=group, source=source)

    @app.get("/api/knowledge/graph/status")
    def knowledge_graph_status(live: bool = True):
        return current().knowledge.graph_status(live=live)

    @app.post("/api/knowledge/graph/sync")
    def knowledge_graph_sync(query: str = "", limit: int = 900, group: str | None = None, replace: bool = True):
        return current().knowledge.sync_graph(query, limit, group=group, replace=replace)

    @app.get("/api/knowledge/overview")
    def knowledge_overview(): return current().knowledge.overview()

    @app.get("/api/knowledge/records")
    def knowledge_records(kind: str = "lithology", query: str = "", limit: int = 60, group: str | None = None):
        if kind not in {"site", "lithology"}:
            raise WorkspaceError("kind 必须为 site 或 lithology", 422)
        return current().knowledge.records(kind, query, limit, group=group)

    @app.get("/api/knowledge/record/{record_id}")
    def knowledge_record(record_id: str):
        record = current().knowledge.record(record_id)
        if record is None:
            raise WorkspaceError("知识库记录不存在", 404)
        return record

    @app.post("/api/knowledge/rebuild")
    def knowledge_rebuild():
        stores = current().knowledge.rebuild()
        return {"status": "rebuilt",
                "lithology": {"records": len(stores["lithology"].get("records", [])),
                              "profiled": stores["lithology"].get("profiled_count", 0)},
                "site": {"records": len(stores["site"].get("records", []))}}
