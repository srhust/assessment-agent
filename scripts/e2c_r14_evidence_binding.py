# -*- coding: utf-8 -*-
"""R14-E：消费哈希绑定（OptionEvidence ↔ Solver）——陈旧裁剪拒绝消费并页面重建。

缺陷实测（零 API）：R13G 落盘的 runs/<pid>/regions/option_*.png 是**旧缺陷
切分**的产物（overlay-12 C 16px 近空白、trans-07 A 96px 切半、mirror-04 D
缺文件）；生产 solve 优先消费 question.meta.vision 里的陈旧裁剪，定位器修
好后**从未被消费**——证据与消费脱钩。根修：消费前把每份裁剪与页面像素对
账（strip-label 后的内容哈希），陈旧即弃用重建；重建走**既有**格盒路径
（_localizer_fallback_inputs 的 ink+pad+间隙帽切分），不另造第二套几何。
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.option_evidence import strip_label_from_crop  # noqa: E402


def _content_sha(mask) -> str:
    return hashlib.sha256(np.ascontiguousarray(
        np.asarray(mask, dtype=bool)).tobytes()).hexdigest()[:16]


def band_label_boxes(mask: np.ndarray, n_opts: int) -> Optional[List[Tuple[int, int]]]:
    """取选项带的标签域列段（锚点命中才有；页级等距/一一对应已验证）。"""
    from assessment_agent.tools.figure_page_localizer import row_bands
    for (y0, y1) in reversed(row_bands(mask)):
        ab = None
        try:
            ab = localize_page(mask, n_options=n_opts).get("option_regions") or []
        except Exception:  # noqa: BLE001
            ab = None
        if ab and all(o.get("label_bbox") for o in ab):
            return [(o["label_bbox"][0], o["label_bbox"][0] + o["label_bbox"][2])
                    for o in ab]
        return None
    return None


def cell_strip_boxes(mask: np.ndarray, n_opts: int
                     ) -> Optional[List[Tuple[Tuple[int, int], Tuple[int, int]]]]:
    """重建用格盒：与生产 _localizer_fallback_inputs **同一几何公式**。"""
    res = localize_page(mask, n_options=n_opts)
    cells = res.get("option_regions") or []
    if len(cells) != n_opts:
        return None
    boxes: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
    n = len(cells)
    starts = [c["bbox_page"][0] for c in cells]
    ends = [c["bbox_page"][0] + c["bbox_page"][2] for c in cells]
    W = mask.shape[1]
    for i, c in enumerate(cells):
        ink_x0, ink_y0, iw, ih = c["bbox_page"]
        ink_x1 = ink_x0 + iw
        pad = 12
        left_cap = (ends[i - 1] + starts[i]) // 2 if i > 0 else 0
        right_cap = (ends[i] + starts[i + 1]) // 2 if i < n - 1 else W
        x0 = max(0, ink_x0 - pad) if i == 0 else max(left_cap, ink_x0 - pad)
        x1 = min(W, ink_x1 + pad) if i == n - 1 else min(right_cap, ink_x1 + pad)
        y0 = max(0, ink_y0 - int(ih * 0.5))
        y1 = min(mask.shape[0], ink_y0 + ih + int(ih * 0.5))
        x0 = min(x0, ink_x0)
        x1 = max(x1, ink_x1)
        y1 = max(y1, ink_y0 + ih)
        boxes.append(((x0, x1), (y0, y1)))
    return boxes


def binding_report(page_mask: np.ndarray, crop_paths: List[str],
                   n_opts: int, threshold: float = 0.0) -> Dict[str, Any]:
    """对账：每份消费裁剪 strip-label 后的内容哈希 ∈ 页面带单元 strip 哈希集合。

    页面哈希对**带内全列**做（旧标签启发式+行线互证），同时给出重建哈希
    （格盒+label_bbox 置零），供重建使用。
    """
    from assessment_agent.tools.figure_tools import load_mask
    crops = []
    for p in crop_paths:
        try:
            m = load_mask(p, threshold) if threshold else load_mask(p)
        except Exception as exc:  # noqa: BLE001
            crops.append({"path": str(p), "exists": False,
                          "error": type(exc).__name__})
            continue
        crops.append({"path": str(p), "exists": True,
                      "sha": _content_sha(strip_label_from_crop(m)),
                      "ink": int(m.sum())})
    res = localize_page(page_mask, n_options=n_opts)
    page_cells = res.get("option_regions") or []
    page_shas, rebuild_shas = set(), {}
    labels = "ABCDEFGH"
    anchor = bool(page_cells) and all(c.get("label_bbox") for c in page_cells)
    if anchor:
        rb = cell_strip_boxes(page_mask, n_opts) or []
        for i, c in enumerate(page_cells):
            lx0, ly0, lw_, lh_ = c["label_bbox"]
            full = np.asarray(page_mask, dtype=bool)
            strip_full = full.copy()
            strip_full[ly0:ly0 + lh_, lx0:lx0 + lw_] = False
            page_shas.add(_content_sha(strip_full[:, c["bbox_page"][0]:
                                                   c["bbox_page"][0] + c["bbox_page"][2]]))
            if i < len(rb):
                (x0, x1), (y0, y1) = rb[i]
                cropm = full[y0:y1, x0:x1].copy()
                cropm[ly0 - y0:ly0 - y0 + lh_, max(0, lx0 - x0):
                      max(0, lx0 - x0) + lw_] = False
                rebuild_shas[labels[i]] = (_content_sha(cropm), cropm, (x0, y0, x1, y1))
    stale = [c for c in crops if c.get("exists") and c["sha"] not in page_shas]
    missing = [c for c in crops if not c.get("exists")]
    return {"n_opts": n_opts, "anchor": anchor,
            "consumed": [{"sha": c.get("sha"), "ink": c.get("ink"),
                          "bound": c.get("exists", False)
                          and c.get("sha") in page_shas}
                         for c in crops],
            "page_cell_shas": sorted(page_shas),
            "stale": len(stale), "missing": len(missing),
            "rebuild": bool(stale or missing) and anchor,
            "rebuild_shas": {k: v[0] for k, v in rebuild_shas.items()},
            "rebuild_crops": rebuild_shas}


if __name__ == "__main__":
    # 自检：陈旧裁剪文件必须判 stale 并触发重建；重建哈希≠页面旧哈希
    for pid, n in {"ft-overlay-12": 4, "ft-trans-07": 3, "ft-mirror-04": 3}.items():
        pm = np.asarray(json.loads(Path(f"runs/r13g-{pid}/image/original.png"))
                        .exists() and True) if False else None
    from assessment_agent.tools.figure_tools import load_mask as _lm
    for pid, n in {"ft-overlay-12": 4, "ft-trans-07": 3, "ft-mirror-04": 3}.items():
        pm = _lm(f"runs/r13g-{pid}/image/original.png")
        paths = [str(ROOT / "runs" / f"r13g-{pid}" / "regions"
                     / f"option_{l}.png") for l in "ABCDEFGH"[:n]]
        rep = binding_report(pm, paths, n)
        rep.pop("rebuild_crops")
        print(json.dumps({k: v for k, v in rep.items()
                          if k != "rebuild_crops"}, ensure_ascii=False, indent=1)[:600])
        print("----")
