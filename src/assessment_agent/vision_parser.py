"""Vision Parser（R2a）：完整截图 -> 语义区域 + 可见文本 + 像素证据裁剪。

与旧版 ``VisionExtractor`` 的区别：
- 不再只抄写文字，而是输出**区域化结构**（题干文字 / 图形矩阵 / 选项 / 表格），
  统一使用原图像素坐标 ``[x, y, w, h]``（左上角原点）；
- OpenCV/PIL 负责按 bbox 裁剪并落盘（图形题保留像素证据，而不是只留 OCR 文本）；
- 严格的几何校验：越界 / 过小 / 重复 / 空白区域一律**丢弃并记录警告**，
  不 clamp、不虚构；无法可靠识别时抛 ``VisionParseError``（结构化失败）。

模型只负责语义定位与文本抽取；所有数值校验都在本地完成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .llm import ChatModel, LLMError
from .prompts import VISION_PARSE_SYSTEM, vision_parse_user_prompt
from .schemas import (
    DiagramGrid,
    DiagramRegion,
    ExtractionResult,
    OptionRegion,
    QuestionOption,
    TableRegion,
    TextRegion,
)

MIN_REGION_SIZE = 8
MIN_INK_RATIO = 0.002
_IOU_OVERLAP_LIMIT = 0.5


class VisionParseError(RuntimeError):
    """截图无法可靠结构化（不猜测、不补造，交由调用方降级或失败）。"""


# ------------------------------------------------------------------ 图像后端
def _load_gray(path: Path) -> Optional["Any"]:
    """读取灰度图（float32 0..255）；cv2 优先，PIL 兜底；都没有返回 None。"""
    try:
        import cv2

        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise VisionParseError(f"无法解码图像: {path}")
        return image.astype("float32")
    except ImportError:
        pass
    try:
        from PIL import Image
        import numpy as np

        with Image.open(path) as raw:
            return np.asarray(raw.convert("L"), dtype="float32")
    except ImportError as exc:  # pragma: no cover - 极端环境
        raise VisionParseError("缺少图像后端（需要 opencv-python-headless 或 Pillow）") from exc


def image_size(path: str | Path) -> Tuple[int, int]:
    """返回 (width, height) 像素尺寸。"""
    image_path = Path(path)
    if not image_path.is_file():
        raise VisionParseError(f"截图不存在: {image_path}")
    gray = _load_gray(image_path)
    height, width = gray.shape[:2]
    return int(width), int(height)


def _crop_and_save(source: Path, bbox: Sequence[int], target: Path) -> bool:
    """按 [x, y, w, h] 裁剪并保存；成功返回 True。越界在这里二次防线式拒绝。"""
    gray = _load_gray(source)
    height, width = gray.shape[:2]
    x, y, w, h = (int(value) for value in bbox)
    if x < 0 or y < 0 or x + w > width or y + h > height:
        return False
    crop = gray[y : y + h, x : x + w]
    if crop.size == 0:
        return False
    crop = crop.astype("uint8")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2

        return bool(cv2.imwrite(str(target), crop))
    except ImportError:
        pass
    try:
        from PIL import Image

        Image.fromarray(crop).save(target)
        return True
    except Exception:  # noqa: BLE001
        return False


def _ink_ratio(source: Path, bbox: Sequence[int]) -> float:
    """区域内深色像素占比（用于"几乎空白"告警）。"""
    gray = _load_gray(source)
    height, width = gray.shape[:2]
    x, y, w, h = (int(value) for value in bbox)
    if x < 0 or y < 0 or x + w > width or y + h > height:
        return 0.0
    crop = gray[y : y + h, x : x + w]
    if crop.size == 0:
        return 0.0
    return float((crop < 128).mean())


def _bbox_iou(left: Sequence[int], right: Sequence[int]) -> float:
    ax0, ay0, aw, ah = (int(value) for value in left)
    bx0, by0, bw, bh = (int(value) for value in right)
    ax1, ay1, bx1, by1 = ax0 + aw, ay0 + ah, bx0 + bw, by0 + bh
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return float(inter / union) if union > 0 else 0.0


# ------------------------------------------------------------------ 校验工具
def _as_bbox(raw: Any) -> Optional[List[int]]:
    """把模型给出的 bbox 归一为 4 个整数 [x, y, w, h]；结构不对返回 None。"""
    if isinstance(raw, dict):
        raw = [raw.get("x"), raw.get("y"), raw.get("w", raw.get("width")), raw.get("h", raw.get("height"))]
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        values = [int(round(float(item))) for item in raw]
    except (TypeError, ValueError):
        return None
    return values


def _validate_bbox(bbox: Optional[List[int]], width: int, height: int, name: str, warnings: List[str]) -> Optional[List[int]]:
    """越界 / 非正 / 过小 → 丢弃并记录警告（不 clamp，不虚构）。"""
    if bbox is None:
        warnings.append(f"{name}：bbox 结构非法，已丢弃该区域")
        return None
    x, y, w, h = bbox
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        warnings.append(f"{name}：bbox {bbox} 超出图像边界（{width}x{height}）或尺寸非法，已丢弃")
        return None
    if w < MIN_REGION_SIZE or h < MIN_REGION_SIZE:
        warnings.append(f"{name}：bbox 过小（{w}x{h}），可能是误检，已丢弃")
        return None
    return bbox


def _normalize_options(raw: Any) -> List[QuestionOption]:
    options: List[QuestionOption] = []
    if not isinstance(raw, list):
        return options
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            label = str(item.get("label") or chr(ord("A") + index)).strip().upper()
            text = str(item.get("text") or "").strip()
        else:
            label = chr(ord("A") + index)
            text = str(item).strip()
        label = label.rstrip(".、．)）")
        if label:
            options.append(QuestionOption(label=label, text=text))
    return options


_VALID_TYPES = {"single_choice", "multi_choice", "fill_blank", "free_response"}
_VALID_ROLES = {"stem_matrix", "stem_figure", "option_figure", "other"}


class VisionParser:
    """完整截图 -> 区域化 ``ExtractionResult``（schema v2）。"""

    def __init__(
        self,
        client: ChatModel,
        model: Optional[str] = None,
        *,
        crops_dir: Optional[Path] = None,
    ):
        self.client = client
        self.model = model
        self.crops_dir = Path(crops_dir) if crops_dir else None

    # ------------------------------------------------------------------ 入口
    def parse(self, image_path: str | Path, hint: str = "") -> ExtractionResult:
        path = Path(image_path)
        width, height = image_size(path)
        try:
            payload = self.client.complete_json(
                VISION_PARSE_SYSTEM,
                vision_parse_user_prompt(width, height, hint),
                image_path=str(path),
                model=self.model,
            )
        except LLMError as exc:
            raise VisionParseError(f"视觉解析调用失败: {exc}") from exc
        return self.from_payload(payload, image_path=path, image_width=width, image_height=height, model=self.model)

    # ------------------------------------------------------------------ 校验+落盘
    def from_payload(
        self,
        payload: Dict[str, Any],
        *,
        image_path: Path,
        image_width: int,
        image_height: int,
        model: Optional[str] = None,
    ) -> ExtractionResult:
        if not isinstance(payload, dict):
            raise VisionParseError("视觉模型输出不是 JSON 对象")

        region_keys = ("stem_text", "text_regions", "diagram_regions", "option_regions", "table_regions")
        if not any(key in payload for key in region_keys):
            # 模型未按 v2 区域协议输出（返回旧版纯文本 schema）：
            # 兼容为 schema v1，不虚构区域，下游按旧链路（整图/纯文本）处理
            from .vision import VisionExtractor

            legacy = VisionExtractor.from_payload(payload, model=model)
            legacy.parser = "legacy_payload"
            return legacy

        warnings: List[str] = []

        # ---------------- 题干文字区域
        text_regions: List[TextRegion] = []
        for index, item in enumerate(payload.get("text_regions") or []):
            if not isinstance(item, dict):
                continue
            region_id = str(item.get("id") or f"t{index + 1}")
            bbox = _validate_bbox(_as_bbox(item.get("bbox")), image_width, image_height, f"文字区域 {region_id}", warnings)
            if bbox is None:
                continue
            text_regions.append(TextRegion(region_id=region_id, bbox=bbox, text=str(item.get("text") or "").strip()))

        # ---------------- 题干图形区域（矩阵 / 单图）
        diagram_regions: List[DiagramRegion] = []
        for index, item in enumerate(payload.get("diagram_regions") or []):
            if not isinstance(item, dict):
                continue
            region_id = str(item.get("id") or f"d{index + 1}")
            bbox = _validate_bbox(_as_bbox(item.get("bbox")), image_width, image_height, f"图形区域 {region_id}", warnings)
            if bbox is None:
                continue
            role = str(item.get("role") or "stem_figure")
            if role not in _VALID_ROLES:
                warnings.append(f"图形区域 {region_id}：未知 role {role!r}，按 stem_figure 处理")
                role = "stem_figure"
            grid: Optional[DiagramGrid] = None
            raw_grid = item.get("grid")
            if isinstance(raw_grid, dict):
                try:
                    grid = DiagramGrid(rows=int(raw_grid["rows"]), cols=int(raw_grid["cols"]))
                except (KeyError, TypeError, ValueError):
                    warnings.append(f"图形区域 {region_id}：grid 标注非法，将由布局检测兜底")
            ink = _ink_ratio(image_path, bbox)
            if ink < MIN_INK_RATIO:
                warnings.append(f"图形区域 {region_id}：区域内几乎无墨迹（ink={ink:.4f}），可能是空区域，已丢弃")
                continue
            crop_path: Optional[str] = None
            if self.crops_dir is not None:
                target = self.crops_dir / f"diagram_{region_id}.png"
                if _crop_and_save(image_path, bbox, target):
                    crop_path = str(target)
                else:
                    warnings.append(f"图形区域 {region_id}：裁剪失败，未保留像素证据")
            diagram_regions.append(DiagramRegion(region_id=region_id, role=role, bbox=bbox, grid=grid, crop_path=crop_path))

        # ---------------- 选项区域（标号唯一 + 图形子区域）
        option_regions: List[OptionRegion] = []
        seen_labels: Dict[str, int] = {}
        for item in payload.get("option_regions") or []:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip().upper().rstrip(".、．)）")
            if not label:
                warnings.append("选项区域缺少标号，已丢弃")
                continue
            if label in seen_labels:
                warnings.append(f"选项标号 {label} 重复，保留第一个")
                continue
            bbox = _validate_bbox(_as_bbox(item.get("bbox")), image_width, image_height, f"选项 {label}", warnings)
            if bbox is None:
                continue
            has_figure = bool(item.get("has_figure", False))
            figure_bbox: Optional[List[int]] = None
            if has_figure:
                figure_bbox = _validate_bbox(
                    _as_bbox(item.get("figure_bbox") or item.get("bbox")),
                    image_width,
                    image_height,
                    f"选项 {label} 图形",
                    warnings,
                )
                if figure_bbox is not None and _ink_ratio(image_path, figure_bbox) < MIN_INK_RATIO:
                    warnings.append(f"选项 {label}：图形子区域几乎无墨迹，降级为纯文字选项")
                    has_figure, figure_bbox = False, None
            option_regions.append(
                OptionRegion(
                    label=label,
                    bbox=bbox,
                    text=str(item.get("text") or "").strip(),
                    has_figure=has_figure,
                    figure_bbox=figure_bbox,
                )
            )
            seen_labels[label] = len(option_regions) - 1

        # 选项图形子区域互检：重叠 > 0.5 视为重复检测，丢弃后者
        kept: List[OptionRegion] = []
        for option in option_regions:
            clash = next(
                (
                    other
                    for other in kept
                    if option.has_figure
                    and other.has_figure
                    and option.figure_bbox
                    and other.figure_bbox
                    and _bbox_iou(option.figure_bbox, other.figure_bbox) > _IOU_OVERLAP_LIMIT
                ),
                None,
            )
            if clash is not None:
                warnings.append(f"选项 {option.label} 与 {clash.label} 图形区域高度重叠，丢弃后者以避免重复计数")
                continue
            kept.append(option)
        option_regions = kept

        # 选项图形裁剪证据（优先 figure_bbox，不含标号文字）
        for option in option_regions:
            if not (option.has_figure and option.figure_bbox):
                continue
            if self.crops_dir is not None:
                target = self.crops_dir / f"option_{option.label}.png"
                if _crop_and_save(image_path, option.figure_bbox, target):
                    option.crop_path = str(target)
                else:
                    warnings.append(f"选项 {option.label}：图形裁剪失败")

        # ---------------- 表格区域（本轮只记录，不求解）
        table_regions: List[TableRegion] = []
        for index, item in enumerate(payload.get("table_regions") or []):
            if not isinstance(item, dict):
                continue
            region_id = str(item.get("id") or f"tb{index + 1}")
            bbox = _validate_bbox(_as_bbox(item.get("bbox")), image_width, image_height, f"表格 {region_id}", warnings)
            if bbox is None:
                continue
            cells = item.get("cells") if isinstance(item.get("cells"), list) else []
            table_regions.append(
                TableRegion(
                    region_id=region_id,
                    bbox=bbox,
                    rows=int(item.get("rows") or len(cells)),
                    cols=int(item.get("cols") or (len(cells[0]) if cells and isinstance(cells[0], list) else 0)),
                    cells=[[str(value) for value in row] for row in cells if isinstance(row, list)],
                )
            )

        # ---------------- 汇总：stem / options / 结构失败判定
        stem_text = str(payload.get("stem_text") or "").strip()
        if not stem_text and text_regions:
            stem_text = "\n".join(region.text for region in text_regions if region.text)
        options = [QuestionOption(label=region.label, text=region.text) for region in option_regions]
        if not options:
            options = _normalize_options(payload.get("options"))
        has_diagram = bool(diagram_regions) or any(region.has_figure for region in option_regions)

        if not stem_text and not diagram_regions:
            raise VisionParseError("未能可靠识别题干文字或图形区域（不补造内容），请检查截图质量或人工介入")

        question_type = str(payload.get("question_type") or "single_choice").strip()
        if question_type not in _VALID_TYPES:
            question_type = "single_choice"
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        skill_hint = payload.get("skill_hint")
        if isinstance(skill_hint, str) and skill_hint.strip().lower() in {"null", "none", ""}:
            skill_hint = None

        if confidence < 0.5:
            warnings.append(f"解析置信度偏低（{confidence:.2f}），建议人工核对")

        reading_order = [str(entry) for entry in (payload.get("reading_order") or [])]

        return ExtractionResult(
            stem=stem_text,
            options=options,
            question_type=question_type,
            skill_hint=(str(skill_hint).strip() if skill_hint else None),
            has_diagram=has_diagram,
            raw_text=stem_text,
            model=model,
            confidence=confidence,
            warnings=warnings,
            schema_version=2,
            image_width=image_width,
            image_height=image_height,
            text_regions=text_regions,
            diagram_regions=diagram_regions,
            option_regions=option_regions,
            table_regions=table_regions,
            reading_order=reading_order,
            parser="vision_parser",
        )
