"""R4b P0-1：完整页面截图基准生成器（benchmark_figshots）。

修复 R4a figtrans 的 P0 缺陷：题图 `image` 只含题干矩阵，选项单独存放，标注的
option bbox 超出主图——image_only 端到端无法完成选择题识别。本脚本渲染**真实
页面**：中性题干文字（无规律名称提示）+ 带网格线的题干矩阵 + 选项行（字母标号
+ 图形格）。全部 bbox 严格位于图片内部；regions 金标裁剪**直接从完整页面像素
切出**（像素级一致性由测试断言）。

与 benchmark_figtrans 同源题面模板（复用 make_figtrans_benchmark 的 build_items，
期望状态在渲染前由规律模板定义，不按 Solver 结果筛题；原冻结集不覆盖不动）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import make_figtrans_benchmark as mfb  # noqa: E402

OUT = ROOT / "data" / "benchmark_figshots"
CELL = mfb.CELL
MARGIN = 24
HEADER_H = 40
GAP = 24
OPT_LABEL_W = 22
GAP_OPTS = 12
CREATED = "2026-10-18"
PROMPT = "根据题目图形中蕴含的规律，选择最符合规律的一项补全图形。"


def _font(size: int):
    for cand in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyh.ttf",
                 "C:/Windows/Fonts/simhei.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default()  # type: ignore[return-value]


def _draw_page(it: Dict[str, Any]) -> Tuple[np.ndarray, Dict[str, Any]]:
    """渲染完整页面 → (page uint8 HxW, bbox 信息)。内容像素与 figtrans 完全同源。"""
    rows, cols = it["rows"], it["cols"]
    stem_img = mfb._png(it["cells"], rows, cols)          # rows*CELL × cols*CELL
    labels = list(it["options"].keys())
    grid_w, grid_h = cols * CELL, rows * CELL
    opts_w = len(labels) * (OPT_LABEL_W + CELL + 4) + (len(labels) - 1) * GAP_OPTS
    width = MARGIN * 2 + max(grid_w, opts_w, 300)
    height = MARGIN + HEADER_H + grid_h + GAP + CELL + MARGIN
    page = Image.new("L", (width, height), 255)
    arr = np.array(page)   # 可写副本（np.asarray 对 PIL 图像只读）

    gx0, gy0 = MARGIN, MARGIN + HEADER_H
    arr[gy0:gy0 + grid_h, gx0:gx0 + grid_w] = stem_img

    oy = gy0 + grid_h + GAP
    ox0 = MARGIN
    opt_bbox: Dict[str, List[int]] = {}
    for i, lab in enumerate(labels):
        lx = ox0 + i * (OPT_LABEL_W + CELL + 4 + GAP_OPTS)
        opt_bbox[lab] = [lx + OPT_LABEL_W + 4, oy, CELL, CELL]
    page = Image.fromarray(arr)
    dr = ImageDraw.Draw(page)
    f15, f17 = _font(15), _font(17)
    dr.text((MARGIN, MARGIN + 8), PROMPT, fill=0, font=f15)
    # R4b：不绘制装饰性灰色网格/边框——二值化阈值下灰线可能被识别为墨迹，
    # 破坏金标裁剪与整页像素语义的一致性（figtrans regions 无框）。真实页面的
    # 选项归属由字母标号承担。
    for i, lab in enumerate(labels):
        x, y, w, h = opt_bbox[lab]
        dr.text((x - OPT_LABEL_W, y + CELL // 2 - 9), f"{lab}.", fill=0, font=f17)
    page_arr = np.array(page)
    for i, lab in enumerate(labels):
        x, y, w, h = opt_bbox[lab]
        content = mfb.option_img(it["options"][lab])
        page_arr[y:y + h, x:x + w] = np.minimum(page_arr[y:y + h, x:x + w], content)
    bboxes = {"page": [width, height],
              "stem": [gx0, gy0, grid_w, grid_h], "options": opt_bbox}
    return page_arr, bboxes


#: 结构不适用样本：canvas-16 的考点是选项裁剪画布 64×64 对题干 48×48 的尺寸
#: 异常——整页基准所有格统一 48 网格渲染，画布失配输入在页面结构上无法表达
#: （裁剪即形变，规律语义被渲染管道改写）。降级/异常输入测试保留在
#: benchmark_figtrans（Oracle 回归集），此处如实剔除并记入 manifest.deviations
#: ——不是删除难题：该题在 figtrans 中本就是 expected failed 诚实样本。
STRUCT_INELIGIBLE = {"ft-canvas-16"}


def _rows_for(which: str) -> List[Dict[str, Any]]:
    items = [it for it in mfb.build_items(which) if it["id"] not in STRUCT_INELIGIBLE]
    rows: List[Dict[str, Any]] = []
    for it in items:
        page, bb = _draw_page(it)
        qid = it["id"]
        reg = f"regions/{which}/{qid}"
        opt_regions = [{"label": lab, "bbox": bb["options"][lab], "text": "图",
                        "has_figure": True, "figure_bbox": bb["options"][lab],
                        "crop_path": f"{reg}/option_{lab}.png"}
                       for lab in it["options"]]
        rows.append(dict(
            id=qid, skill="figure_reasoning", question_type="single_choice",
            stem=PROMPT,
            options=[{"label": l, "text": "图"} for l in it["options"]],
            answer=it["answer"], expected_status=it["expected"],
            tags=it["tags"] + ["完整截图", "R4b"], group=it["group"],
            layout="figshots",
            image=f"benchmark_figshots/images/{which}/{qid}.png",
            grid=[it["rows"], it["cols"]],
            regions=dict(bbox_format="pixel_xywh",
                         image_size=[int(bb["page"][0]), int(bb["page"][1])],
                         stem_matrix_bbox=bb["stem"],
                         stem_matrix_grid=[it["rows"], it["cols"]],
                         stem_matrix_crop=f"{reg}/stem.png",
                         option_regions=opt_regions),
            source=dict(provenance="synthetic-full-page", created=CREATED,
                        note="完整页面截图（题干+选项+标号）；期望由规律模板在渲染前定义"),
        ))
    return rows


def _emit_images(which: str, rows: List[Dict[str, Any]]) -> None:
    items = {it["id"]: it for it in mfb.build_items(which)}
    for row in rows:
        it = items[row["id"]]
        page, _bb = _draw_page(it)
        # jsonl 的 image 相对 data/；落盘相对本基准目录
        img_path = OUT / row["image"].split("benchmark_figshots/")[1]
        img_path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(page).save(img_path)
        reg = OUT / "regions" / which / row["id"]
        reg.mkdir(parents=True, exist_ok=True)
        sx, sy, sw, sh = row["regions"]["stem_matrix_bbox"]
        Image.fromarray(page[sy:sy + sh, sx:sx + sw]).save(reg / "stem.png")
        for info in row["regions"]["option_regions"]:
            x, y, w, h = info["bbox"]
            Image.fromarray(page[y:y + h, x:x + w]).save(
                reg / f"option_{info['label']}.png")


def publish(which: str, freeze: bool) -> None:
    rows = _rows_for(which)
    split_dir = OUT / which
    target = split_dir / "figshots.jsonl"
    if freeze and target.exists():
        raise SystemExit(f"test 已冻结，禁止重写：{target}")
    raw = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n"
    split_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw.encode("utf-8"))
    _emit_images(which, rows)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    mpath = OUT / "manifest.json"
    man = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {
        "name": "benchmark_figshots",
        "data_class": "synthetic-full-page（完整页面截图基准；期望渲染前定义；不按求解结果筛题）",
        "contains_real_exam": False, "contains_pii": False,
        "independence_declaration":
            "与 benchmark_figtrans 同源题面模板；页面包含题干+全部选项+字母标号，"
            "全部 bbox 位于图片内；金标裁剪由页面像素直接切出（像素一致性测试锁定）。"
            "本集用于 image_only 端到端；benchmark_figtrans 继续作为 Oracle 回归集。",
        "splits": {},
    }
    man["deviations"] = {
        "excluded": sorted(STRUCT_INELIGIBLE),
        "reason": "异常裁剪尺寸考点在整页统一网格结构下不可表达；保留于 benchmark_figtrans",
    }
    man["splits"][which] = {"file": f"{which}/figshots.jsonl",
                            "count": len(rows), "sha256": digest,
                            "frozen": bool(freeze)}
    mpath.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[figshots] {which} {len(rows)} 题 sha={digest[:16]}")


def main() -> None:
    if "--publish-test" in sys.argv:
        publish("test", freeze=True)
    else:
        publish("dev", freeze=False)
        print("（dev 可重复生成；test 用 --publish-test 一次性冻结）")


if __name__ == "__main__":
    main()
