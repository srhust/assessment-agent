"""R2b 视觉内容评测：从"结构存在"升级为"内容正确"。

与 R2a 的粗指标（题干相等 / 矩阵 IoU / 标号顺序）不同，这里逐项比较解析输出与
金标准**内容**，所有指标都显式给出分母；**必需区域漏检按 0 计入分母**，
不允许从均值中剔除（否则区域指标会因只统计命中的框而虚高）。

指标（均为每题可算、汇总时注明样本数）：

- stem_exact / stem_cer：题干 OCR 精确匹配；CER = 字符编辑距离 / 金标准字符数（≤1，可超过 1 时截断为 1）
- option_label_recall / option_label_fp：金标准标号被识别的比例；多出标号比例
- option_text_accuracy：按标号对齐后选项文本的精确匹配率（无图形的标参与）
- option_text_cer_mean：按标号对齐后的平均字符错误率
- option_iou_mean：**全部金标准选项框**（含漏框）的最大 IoU 均值，漏检记 0
- diagram_iou_mean：题干矩阵/图形区域：金标准区域被 IoU≥0.5 命中的 Recall 与平均 IoU（漏检记 0）
- diagram_recall：金标准图形区域被命中的比例
- region_detection_failure：本样本是否存在"必需区域漏检或 IoU<0.5"
- table_cell_accuracy：金标准表格单元格文本被原样解析的比例（分母=金标准单元格数）

匹配规则：先按标号精确对齐（A↔A），文本/图形 IoU 用贪心最佳匹配；
预测中多出的区域视为 FP，不参与 Recall 分母，但计入 option_label_fp。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

IOU_HIT = 0.5


def _compact(text: Any) -> str:
    import re

    return re.sub(r"\s+", "", str(text or ""))


def char_edit_distance(gold: str, pred: str) -> int:
    """Levenshtein（字符级，两行 DP）。"""
    if gold == pred:
        return 0
    previous = list(range(len(pred) + 1))
    for i, gch in enumerate(gold, start=1):
        current = [i]
        for j, pch in enumerate(pred, start=1):
            cost = 0 if gch == pch else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def cer(gold: str, pred: str) -> float:
    """字符错误率；金标准为空时：预测也为空记 0，否则记 1（无法定义分母，视为完全错）。"""
    g, p = _compact(gold), _compact(pred)
    if not g:
        return 0.0 if not p else 1.0
    return min(1.0, char_edit_distance(g, p) / len(g))


def bbox_iou(left: Sequence[float], right: Sequence[float]) -> float:
    """xywh 像素框 IoU；任一无效返回 0。"""
    if not left or not right or len(left) != 4 or len(right) != 4:
        return 0.0
    try:
        ax, ay, aw, ah = (float(v) for v in left)
        bx, by, bw, bh = (float(v) for v in right)
    except (TypeError, ValueError):
        return 0.0
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _norm_bbox(value: Any) -> Optional[List[float]]:
    if isinstance(value, dict):
        value = [value.get("x"), value.get("y"), value.get("w"), value.get("h")]
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def compare_extraction_detail(gold_regions: Dict[str, Any], extraction: Any) -> Dict[str, Any]:
    """逐内容项比较金标准与解析输出；extraction 为 None（解析失败）时全部记漏检。"""
    if gold_regions is None:
        return {}
    if extraction is None:
        gold_options = gold_regions.get("option_regions") or []
        return {
            "parse_failed": True,
            "stem_exact": False,
            "stem_cer": 1.0,
            "option_label_recall": 0.0,
            "option_label_fp": None,
            "option_text_accuracy": None,
            "option_text_cer_mean": None,
            "option_iou_mean": 0.0 if gold_options else None,
            "option_iou_scored": len(gold_options),
            "diagram_recall": 0.0,
            "diagram_iou_mean": 0.0,
            "diagram_iou_scored": 1 if gold_regions.get("stem_matrix_bbox") else 0,
            "region_detection_failure": True,
            "table_cell_accuracy": None,
            "table_cells_scored": 0,
            "grid_parse_correct": False if gold_regions.get("stem_matrix_grid") is not None else None,
            "crops_complete": False,
            "crops_scored": (len(gold_regions.get("option_regions") or [])
                             + (1 if gold_regions.get("stem_matrix_bbox") else 0)),
        }

    out: Dict[str, Any] = {"parse_failed": False}

    # ---- 题干文本 --------------------------------------------------------
    stem_exact = _compact(extraction.stem) == _compact(gold_regions.get("stem_text") or "")
    out["stem_exact"] = stem_exact
    out["stem_cer"] = round(cer(gold_regions.get("stem_text") or "", extraction.stem or ""), 4)

    # ---- 选项：按标号对齐 ----------------------------------------------
    gold_options: List[Dict[str, Any]] = list(gold_regions.get("option_regions") or [])
    # 几何与文本都在 OptionRegion（v2）；旧版抽取没有 option_regions 时退回 options 标签
    pred_options = list(getattr(extraction, "option_regions", None) or []) or list(extraction.options or [])
    gold_labels = [_compact(o.get("label")).upper() for o in gold_options]
    pred_map: Dict[str, Any] = {}
    for option in pred_options:
        key = _compact(getattr(option, "label", "")).upper()
        if key and key not in pred_map:
            pred_map[key] = option
    present = [label for label in gold_labels if label and label in pred_map]
    out["option_label_recall"] = round(len(present) / len(gold_labels), 4) if gold_labels else None
    extra = [key for key in pred_map if key not in gold_labels]
    out["option_label_fp"] = round(len(extra) / len(pred_map), 4) if pred_map else None

    text_pairs = [
        (o, pred_map[_compact(o.get("label")).upper()])
        for o in gold_options
        if _compact(o.get("label")).upper() in pred_map and str(o.get("text") or "").strip()
    ]
    if text_pairs:
        out["option_text_accuracy"] = round(
            sum(1 for g, p in text_pairs if _compact(g.get("text")) == _compact(getattr(p, "text", ""))) / len(text_pairs), 4
        )
        out["option_text_cer_mean"] = round(
            sum(cer(g.get("text") or "", getattr(p, "text", "") or "") for g, p in text_pairs) / len(text_pairs), 4
        )
    else:
        out["option_text_accuracy"] = None  # 无带文字的选项可比较
        out["option_text_cer_mean"] = None

    # ---- 逐选项框 IoU：全部金标准参与分母，漏框记 0 ----------------------
    pred_boxes = [_norm_bbox(getattr(o, "bbox", None)) for o in pred_options]
    pred_labels = [_compact(getattr(o, "label", "")).upper() for o in pred_options]
    option_ious: List[float] = []
    for gold in gold_options:
        gb = _norm_bbox(gold.get("bbox"))
        if gb is None:
            continue
        label = _compact(gold.get("label")).upper()
        best = 0.0
        # 优先同标号框；同标号缺失再取任意最佳（位置对但标错仍算部分证据）
        for pb, pl in zip(pred_boxes, pred_labels):
            if pb is not None and pl == label:
                best = max(best, bbox_iou(gb, pb))
        if best == 0.0:
            for pb in pred_boxes:
                if pb is not None:
                    best = max(best, bbox_iou(gb, pb))
        option_ious.append(best)
    out["option_iou_mean"] = round(sum(option_ious) / len(option_ious), 4) if option_ious else None
    out["option_iou_scored"] = len(option_ious)

    # ---- 图形区域 Recall / IoU（金标准：题干矩阵 + 各带图选项框）---------
    gold_boxes: List[List[float]] = []
    stem_bbox = _norm_bbox(gold_regions.get("stem_matrix_bbox"))
    if stem_bbox:
        gold_boxes.append(stem_bbox)
    diagram_regions = list(extraction.diagram_regions or [])
    diagram_ious: List[float] = []
    hits = 0
    for gb in gold_boxes:
        best = max((bbox_iou(gb, list(region.bbox)) for region in diagram_regions), default=0.0)
        diagram_ious.append(best)
        if best >= IOU_HIT:
            hits += 1
    if stem_bbox:
        out["diagram_recall"] = round(hits / len(gold_boxes), 4)
        out["diagram_iou_mean"] = round(sum(diagram_ious) / len(diagram_ious), 4)
        out["diagram_iou_scored"] = len(diagram_ious)
    else:
        out["diagram_recall"] = None
        out["diagram_iou_mean"] = None
        out["diagram_iou_scored"] = 0

    # ---- 必需区域漏检判定 ------------------------------------------------
    region_failure = bool(gold_regions.get("stem_matrix_bbox")) and out.get("diagram_recall", 1.0) != 1.0
    if gold_labels and out.get("option_label_recall", 1.0) != 1.0:
        region_failure = True
    out["region_detection_failure"] = region_failure

    # ---- 表格单元格 ------------------------------------------------------
    gold_table = gold_regions.get("table") or {}
    grid = gold_table.get("cells") or []
    gold_cells = [str(value) for row in grid for value in (row if isinstance(row, list) else [row])]
    if gold_cells:
        parsed_cells = [
            str(value)
            for table in (extraction.table_regions or [])
            for row in table.cells
            for value in row
        ]
        gold_norm = [_compact(c) for c in gold_cells]
        parsed_pool = [_compact(c) for c in parsed_cells]
        matched = 0
        available = list(parsed_pool)
        for cell in gold_norm:
            if cell and cell in available:
                matched += 1
                available.remove(cell)
        out["table_cell_accuracy"] = round(matched / len(gold_norm), 4)
    else:
        out["table_cell_accuracy"] = None
    # ---- R4c 追加：网格行列解析 + 裁剪完整性 ------------------------------
    gold_grid = gold_regions.get("stem_matrix_grid")
    if gold_grid is not None:
        def _grid_dims(g: Any) -> Optional[Tuple[int, int]]:
            if isinstance(g, dict):
                try:
                    return int(g.get("rows")), int(g.get("cols"))
                except (TypeError, ValueError):
                    return None
            if isinstance(g, (list, tuple)) and len(g) == 2:
                try:
                    return int(g[0]), int(g[1])
                except (TypeError, ValueError):
                    return None
            return None
        gold_dims = _grid_dims(gold_grid)
        pred_dims = None
        for region in (extraction.diagram_regions or []):
            if getattr(region, "grid", None) is not None:
                g = region.grid
                pred_dims = (int(getattr(g, "rows", 0) or 0), int(getattr(g, "cols", 0) or 0))
                break
        out["grid_parse_correct"] = (gold_dims == pred_dims) if gold_dims else None
    else:
        out["grid_parse_correct"] = None
    crop_paths: List[str] = []
    for option in (getattr(extraction, "option_regions", None) or []):
        if getattr(option, "crop_path", None):
            crop_paths.append(str(option.crop_path))
    for region in (extraction.diagram_regions or []):
        if getattr(region, "crop_path", None):
            crop_paths.append(str(region.crop_path))
    if crop_paths:
        from pathlib import Path as _P
        out["crops_complete"] = all(_P(cp).is_file() for cp in crop_paths)
        out["crops_scored"] = len(crop_paths)
    else:
        gold_crops = (len(gold_regions.get("option_regions") or [])
                      + (1 if gold_regions.get("stem_matrix_bbox") else 0))
        out["crops_complete"] = False if gold_crops else None
        out["crops_scored"] = gold_crops
    return out


def aggregate_vision(per_case: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总每题明细；每个指标都带分母（scored 数），漏检样本留在分母里。"""
    scored = [item for item in per_case if item]
    if not scored:
        return {}

    def _ratio(flags: List[bool]) -> Tuple[Optional[float], int]:
        if not flags:
            return None, 0
        return round(sum(1 for flag in flags if flag) / len(flags), 4), len(flags)

    def _mean(values: List[float]) -> Tuple[Optional[float], int]:
        if not values:
            return None, 0
        return round(sum(values) / len(values), 4), len(values)

    stem_exact, stem_exact_n = _ratio([bool(i.get("stem_exact")) for i in scored])
    stem_cers, stem_cer_n = _mean([float(i.get("stem_cer", 0.0)) for i in scored])
    recall_values = [v for v in (i.get("option_label_recall") for i in scored) if v is not None]
    label_recall, label_recall_n = _mean(recall_values)
    text_acc = [v for v in (i.get("option_text_accuracy") for i in scored) if v is not None]
    text_acc_mean, text_acc_n = _mean(text_acc)
    text_cers = [v for v in (i.get("option_text_cer_mean") for i in scored) if v is not None]
    text_cer_mean, text_cer_n = _mean(text_cers)
    opt_ious = [v for v in (i.get("option_iou_mean") for i in scored) if v is not None]
    opt_iou_mean, opt_iou_n = _mean(opt_ious)
    diagram_values = [v for v in (i.get("diagram_iou_mean") for i in scored) if v is not None]
    diagram_iou_mean, diagram_iou_n = _mean(diagram_values)
    recalls = [v for v in (i.get("diagram_recall") for i in scored) if v is not None]
    diagram_recall, diagram_recall_n = _mean(recalls)
    table_values = [v for v in (i.get("table_cell_accuracy") for i in scored) if v is not None]
    table_acc, table_acc_n = _mean(table_values)
    region_fail, region_fail_n = _ratio([bool(i.get("region_detection_failure")) for i in scored])

    def _pooled(mean_key: str, count_key: str):
        """R4f 修正：pooled = Σ(n_i·mean_i)/Σn_i（逐题按金标框数加权）。
        旧实现 Σmean_i/Σn_i 把每题均值又除以了一次该题选项数，系统性低估
        （三题×4 选项时约为真值的 1/4）。"""
        num, den = 0.0, 0
        for i in scored:
            m = i.get(mean_key)
            if m is None:
                continue
            n = int(i.get(count_key) or 0)
            if n <= 0:
                continue
            num += float(m) * n
            den += n
        return (round(num / den, 4) if den else None), den

    opt_pooled, opt_pooled_n = _pooled("option_iou_mean", "option_iou_scored")
    dia_pooled, dia_pooled_n = _pooled("diagram_iou_mean", "diagram_iou_scored")

    return {
        "cases": len(scored),
        "stem_exact_match": {"value": stem_exact, "denominator": stem_exact_n},
        "stem_cer": {"value": stem_cers, "denominator": stem_cer_n},
        "option_label_recall": {"value": label_recall, "denominator": label_recall_n},
        "option_text_accuracy": {"value": text_acc_mean, "denominator": text_acc_n},
        "option_text_cer_mean": {"value": text_cer_mean, "denominator": text_cer_n},
        "option_region_iou_mean": {
            "value": opt_iou_mean,
            "denominator": opt_iou_n,
            "note": "按题取均值；全部金标准选项框参与，漏框按 0 计入每题分母",
        },
        "option_region_iou_pooled": {
            "value": opt_pooled,
            "denominator": opt_pooled_n,
            "note": "逐题金标框数加权的框级均值 Σ(n_i·mean_i)/Σn_i（R4f 修正；旧口径 Σmean_i/Σn_i 已废弃）",
        },
        "diagram_region_recall": {"value": diagram_recall, "denominator": diagram_recall_n},
        "diagram_region_iou_mean": {
            "value": diagram_iou_mean,
            "denominator": diagram_iou_n,
            "note": "IoU≥0.5 记命中；漏检按 0 计入分母",
        },
        "diagram_region_iou_pooled": {
            "value": dia_pooled,
            "denominator": dia_pooled_n,
            "note": "同 option pooled 修正口径",
        },
        "table_cell_accuracy": {"value": table_acc, "denominator": table_acc_n},
        "region_detection_failure_rate": {"value": region_fail, "denominator": region_fail_n},
    }


def render_region_overlay(
    image_path: str,
    gold_regions: Dict[str, Any],
    predicted_regions: Optional[Dict[str, Any]],
    out_path: str,
) -> Optional[str]:
    """原图上叠加金标准（红）与预测（绿）区域框，输出可视化 PNG。失败返回 None。"""
    try:
        from PIL import Image, ImageDraw

        image = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(image)

        def _boxes(payload: Dict[str, Any], color: Tuple[int, int, int], tag: str) -> None:
            stem_bbox = _norm_bbox(payload.get("stem_matrix_bbox"))
            if stem_bbox:
                x, y, w, h = stem_bbox
                draw.rectangle([x, y, x + w, y + h], outline=color, width=3)
                draw.text((x + 2, max(0, y - 12)), f"{tag}:stem", fill=color)
            for option in payload.get("option_regions") or []:
                bbox = _norm_bbox(option.get("bbox"))
                if not bbox:
                    continue
                x, y, w, h = bbox
                draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
                draw.text((x + 2, y + 2), f"{tag}:{option.get('label', '?')}", fill=color)
            for diagram in payload.get("diagram_regions") or []:
                bbox = _norm_bbox(diagram.get("bbox"))
                if not bbox:
                    continue
                x, y, w, h = bbox
                draw.rectangle([x, y, x + w, y + h], outline=color, width=2)

        _boxes(gold_regions or {}, (220, 40, 40), "gold")
        if predicted_regions:
            _boxes(predicted_regions, (30, 180, 60), "pred")
        image.save(out_path)
        return out_path
    except Exception:  # noqa: BLE001 - 可视化失败不应中断评测
        return None
