# -*- coding: utf-8 -*-
"""R10-D2：全新排版/尺寸/间距/干扰元素验证集（金标由程序化组装时记录）。

不从 figshots_dev 直接复用原页面（工单要求"不止 figshots_dev"）：以金标单元
crop 为原料，**重新组装**成新页面布局，金坐标在粘贴时记录，与任何历史渲染无关。
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "data" / "benchmark_figshots"
OUT_DIR = ROOT / "data" / "benchmark_figshots_perturb"
CELL = 64


def _load_item_cells(it: Dict[str, Any]) -> Dict[str, Any]:
    reg = it["regions"]
    stem = Image.open(FIG / reg["stem_matrix_crop"]).convert("L")
    opts = {o["label"]: Image.open(FIG / o["crop_path"]).convert("L")
            for o in reg["option_regions"]}
    return {"stem": stem, "opts": opts, "reg": reg}


def _binarize(im: Image.Image) -> Image.Image:
    return im.point(lambda p: 0 if p < 128 else 255)


def _paste(page: Image.Image, cell: Image.Image, x: int, y: int) -> List[int]:
    page.paste(cell, (x, y))
    return [x, y, cell.width, cell.height]


def build(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rng = random.Random(1021)
    out: List[Dict[str, Any]] = []
    for it in items:
        data = _load_item_cells(it)
        stem, opts = data["stem"], data["opts"]
        labels = sorted(opts)
        # --- T1 尺寸缩放（页面单元 0.75×/1.25×），间距宽
        for scale, tag in ((0.75, "small"), (1.25, "large")):
            st = stem.resize((max(8, int(stem.width * scale)), max(8, int(stem.height * scale))))
            page = Image.new("L", (520, 300), 255)
            sb = _paste(page, st, 60, 40)
            ob, x = [], 40
            for lb in labels:
                c = opts[lb].resize((max(8, int(opts[lb].width * scale)),
                                     max(8, int(opts[lb].height * scale))))
                ob.append((lb, _paste(page, c, x, 180)))
                x += int(CELL * scale) + 46
            out.append(_emit(it, tag, page, sb, ob, scale))
        # --- T2 全新排版：选项竖排两列
        page = Image.new("L", (420, 340), 255)
        sb = _paste(page, stem, 30, 30)
        ob = []
        for i, lb in enumerate(labels):
            row, col = divmod(i, 2)
            ob.append((lb, _paste(page, opts[lb], 40 + col * 150, 150 + row * 80)))
        out.append(_emit(it, "vertical", page, sb, ob, 1.0))
        # --- T3 干扰元素：边缘噪条 + 散点 + 单元间细连接线（金标坐标不变）
        page = Image.new("L", (480, 260), 255)
        sb = _paste(page, stem, 50, 60)
        ob, x = [], 46
        for lb in labels:
            ob.append((lb, _paste(page, opts[lb], x, 170)))
            x += CELL + 40
        px = page.load()
        for _ in range(240):  # 散点噪声
            px[rng.randrange(2, 8) if rng.random() < 0.5 else page.width - rng.randrange(2, 8),
               rng.randrange(0, page.height)] = 0
        for yy in range(20, 240, 46):  # 左缘竖噪条
            for xx in range(1, 3):
                px[xx, yy] = 0
        out.append(_emit(it, "distractor", page, sb, ob, 1.0))
        # --- T4 极紧间距（选项格间隙 6px）
        page = Image.new("L", (int(len(labels) * CELL + (len(labels) - 1) * 6) + 60, 240), 255)
        sb = _paste(page, stem, 30, 40)
        ob, x = [], 30
        for lb in labels:
            ob.append((lb, _paste(page, opts[lb], x, 150)))
            x += CELL + 6
        out.append(_emit(it, "tight", page, sb, ob, 1.0))
    return out


def _emit(it, tag, page, sb, ob, scale) -> Dict[str, Any]:
    pid = f"{it['id']}--{tag}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ipath = OUT_DIR / "images" / f"{pid}.png"
    ipath.parent.mkdir(parents=True, exist_ok=True)
    page.save(ipath)
    regions = {"image_size": [page.width, page.height],
               "stem_matrix_bbox": sb,
               "stem_matrix_grid": it["regions"].get("stem_matrix_grid") or [1, 1],
               "option_regions": [{"label": lb, "bbox": bb, "has_figure": True}
                                  for lb, bb in ob]}
    return {"id": pid, "group": it["group"], "image": str(ipath.relative_to(ROOT / "data")),
            "options": it["options"], "regions": regions,
            "source_item": it["id"], "transform": tag, "scale": scale,
            "stem_matrix_grid": it["regions"].get("stem_matrix_grid")}


def main() -> None:
    rows = [json.loads(l) for l in
            (FIG / "dev" / "figshots.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    picked = [r for r in rows if r["id"] in
              ("ft-rot90-01", "ft-mirror-04", "ft-grid-10", "ft-scale-09",
               "ft-trans-07", "ft-overlay-13")]
    items = build(picked)
    corpus = {"name": "R10D_PERTURB", "items": items,
              "note": "金标坐标由组装时程序化记录；页面为全新排布，不复用历史渲染"}
    (ROOT / "evidence" / "R10D_PERTURB_CORPUS.json").write_text(
        json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"built {len(items)} perturbation pages from {len(picked)} sources")


if __name__ == "__main__":
    main()
