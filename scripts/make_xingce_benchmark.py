"""行测综合 held-out 测试集生成器：data/benchmark_xingce/（test-only，冻结）。

目的：**验证整个系统的性能**——覆盖全部 8 个已注册 Skill，题型分类学来自公开
行测学习资料（来源清单见 DATASET_CARD.md），含真实考试题型形态的能力缺口样本
与不可判定样本。纪律：

1. **不参与训练/调参**：本集是 held-out test，创建即冻结（sha256 manifest，
   重跑拒绝覆盖）；不按 Solver 结果筛题；金标在渲染前由构造确定（数学题=公式
   计算、数列题=生成规则、图形题=变换定义、真题=独立推导），rationale 逐题记录。
2. **来源与版权**：5 题为 2023 国考行测**考生回忆版**真题（官方不公布真题答案，
   题干文字自两个独立公开来源交叉校验；答案金标为本项目**独立计算**所得，
   不依赖任何机构答案表；仅内部评测用途，禁止再分发）。其余题目为按真实题型
   分类**原创构造**，不逐字拷贝受版权保护题库文本。
3. **防泄漏**：所有题干与既有 dev/train/其他 benchmark 做归一化近重复比对
   （构建时执行，结果写 manifest）。
4. **确定性**：无随机（显式规则+固定表）；输出稳定排序；同输入重跑同哈希。

运行：python -X utf8 scripts/make_xingce_benchmark.py [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import numpy as np  # noqa: E402

import make_figtrans_benchmark as ft  # noqa: E402  形状库/变换/渲染纯函数复用

DATA_DIR = ROOT / "data"
BENCH = DATA_DIR / "benchmark_xingce"
PAGE_W = 920
CELL = 48
FONT_CANDIDATES = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_PATH = next((c for c in FONT_CANDIDATES if Path(c).is_file()), None)
if FONT_PATH is None:  # pragma: no cover
    raise SystemExit("未找到可用字体")
CREATED = "2026-10-19"


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


# ------------------------------------------------------------------ 文本页渲染
def render_text_page(stem_lines: List[str], options: List[Tuple[str, str]],
                     material: Optional[List[str]] = None,
                     table: Optional[List[List[str]]] = None
                     ) -> Tuple[np.ndarray, dict]:
    """把材料/表格/题干/选项渲染成整页截图；返回 (ink 布尔图, regions 字典)。"""
    stem_lines = _wrap_all(stem_lines)
    material = _wrap_all(material) if material else material
    f_body, f_small = font(18), font(16)
    line_h = 30
    pad = 36
    h = pad
    plan: List[Tuple[str, Any, int]] = []
    for ln in material or []:
        plan.append(("line", ln, h)); h += line_h
    if table:
        row_h = 28
        tbl_bbox = [pad + 10, h + 8, 380, row_h * len(table) + 16]
        plan.append(("table", table, h)); h += row_h * len(table) + 26
    for ln in stem_lines:
        plan.append(("line", ln, h)); h += line_h
    h += 8
    opt_rows: List[Tuple[str, str, int]] = []
    for label, text in options:
        opt_rows.append((label, text, h)); h += line_h
    h += pad
    img = Image.new("L", (PAGE_W, h), 255)
    draw = ImageDraw.Draw(img)
    text_regions: List[dict] = []
    for kind, payload, y in plan:
        if kind == "line":
            draw.text((pad, y), payload, fill=0, font=f_body)
            wpx = draw.textlength(payload, font=f_body)
            text_regions.append({"id": "material" if (material and payload in material)
                                 else "stem", "bbox": [pad, y, int(wpx), 22],
                                 "text": payload})
        else:
            x0 = pad + 10
            for r, row in enumerate(payload):
                for c, cellv in enumerate(row):
                    x = x0 + c * 190
                    draw.rectangle([x, y + r * 28, x + 186, y + r * 28 + 26],
                                   outline=0, width=1)
                    draw.text((x + 8, y + r * 28 + 4), cellv, fill=0, font=f_small)
                    wpx = draw.textlength(cellv, font=f_small)
                    text_regions.append({"id": f"table_r{r}c{c}",
                                         "bbox": [x + 8, y + r * 28 + 4,
                                                  int(wpx), 20], "text": cellv})
            tbl_bbox = [x0, y, 190 * len(payload[0]), 28 * len(payload)]
    option_regions = []
    for label, text, y in opt_rows:
        s = f"{label}. {text}"
        draw.text((pad, y), s, fill=0, font=f_body)
        wpx = draw.textlength(s, font=f_body)
        option_regions.append({"label": label, "text": text, "has_figure": False,
                               "bbox": [pad, y, int(wpx), 22]})
    order = (["material"] * len(material or []) + (["table"] if table else [])
             + ["stem"] * len(stem_lines) + ["A", "B", "C", "D"])
    arr = 255 - np.array(img, dtype=np.uint8)  # ink 约定: True=墨
    regions = {
        "bbox_format": "pixel_xywh", "image_size": [PAGE_W, h],
        "text_regions": text_regions, "option_regions": option_regions,
        "reading_order": order, "stem_matrix_bbox": None,
        "stem_matrix_crop": None, "stem_matrix_grid": None,
    }
    if table:
        regions["table"] = {"bbox": tbl_bbox, "cells": table}
    arr = np.where(arr > 0, 1, 0)
    return arr, regions


def save_page(path: Path, ink: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    png = Image.fromarray(np.where(ink > 0, 0, 255).astype(np.uint8), mode="L")
    png.save(path)


# ------------------------------------------------------------------ 图形页渲染
def render_figure_page(cells: List[Any], rows: int, cols: int,
                       options: Dict[str, np.ndarray],
                       labels: List[str], intro_lines: List[str]) -> Tuple[np.ndarray, dict]:
    """上=题干网格（空格 '?'），中=题干文字，下=选项行（图形并排）。返回 (ink, regions)。"""
    M = 24
    stem_h, stem_w = rows * CELL, cols * CELL
    text_y = M + stem_h + 10
    f_body = font(18)
    tmp = Image.new("L", (10, 10)); dtmp = ImageDraw.Draw(tmp)
    text_h = max(30 * len(intro_lines), 0)
    opt_y = text_y + text_h + 18
    page_h = opt_y + CELL + M
    page = np.zeros((page_h, PAGE_W), dtype=bool)
    for i, cell in enumerate(cells):
        if cell is None:
            continue
        r, c = divmod(i, cols)
        y0, x0 = M + r * CELL, M + c * CELL
        page[y0:y0 + CELL, x0:x0 + CELL] = np.asarray(cell, dtype=bool)
    img = Image.fromarray(np.where(page, 0, 255).astype(np.uint8), mode="L")
    draw = ImageDraw.Draw(img)
    text_regions = []
    y = text_y
    for ln in intro_lines:
        draw.text((M, y), ln, fill=0, font=f_body)
        wpx = draw.textlength(ln, font=f_body)
        text_regions.append({"id": "stem", "bbox": [M, y, int(wpx), 22], "text": ln})
        y += 30
    for lnum in ["A", "B", "C", "D"]:
        if lnum in labels:
            draw.text((M, opt_y + 14), lnum, fill=0, font=f_body)
    page = np.array(img, dtype=np.uint8) < 128
    opt_regions = []
    x = M + 34
    for label in labels:
        cell = np.asarray(options[label], dtype=bool)
        page[opt_y:opt_y + CELL, x:x + CELL] |= cell
        opt_regions.append({"label": label, "bbox": [x, opt_y, CELL, CELL],
                            "text": "图形选项", "has_figure": True,
                            "figure_bbox": [x, opt_y, CELL, CELL],
                            "crop_path": f"regions/CROPOPT_{label}.png"})
        x += CELL + 34
    stem_bbox = [M, M, stem_w, stem_h]
    regions = {"bbox_format": "pixel_xywh", "image_size": [PAGE_W, page_h],
               "text_regions": text_regions, "option_regions": opt_regions,
               "reading_order": ["stem"] + labels,
               "stem_matrix_bbox": stem_bbox, "stem_matrix_grid": [rows, cols],
               "stem_matrix_crop": "regions/CROPSTEM.png"}
    return page, regions


# ------------------------------------------------------------------ 立方体展开图（空间重构）
def render_cube_page(qid: str) -> np.ndarray:
    """T 型展开图：中行四格标 1~4，第二格上下为 5/6。折后 1↔3、2↔4、5↔6 相对。"""
    S = 60
    W, H = PAGE_W, 4 * S + 80
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    f = font(24)
    x0, y0 = 300, 80
    faces = [(x0, y0 + S, "1"), (x0 + S, y0 + S, "2"), (x0 + 2 * S, y0 + S, "3"),
             (x0 + 3 * S, y0 + S, "4"), (x0 + S, y0, "5"), (x0 + S, y0 + 2 * S, "6")]
    for fx, fy, t in faces:
        d.rectangle([fx, fy, fx + S, fy + S], outline=0, width=2)
        tb = d.textbbox((0, 0), t, font=f)
        d.text((fx + (S - tb[2] + tb[0]) // 2, fy + (S - tb[3] + tb[1]) // 2 - 2), t,
               fill=0, font=f)
    stem_lines = wrap_text("下图为立方体的表面展开图，六个面分别标有 1~6。", 46)
    yy = 8
    for ln in stem_lines:
        d.text((36, yy), ln, fill=0, font=font(18))
        yy += 26
    return np.array(img, dtype=np.uint8) < 128


def save_page(path: Path, ink: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    png = Image.fromarray(np.where(ink, 0, 255).astype(np.uint8), mode="L")
    png.save(path)


def crop_save(ink: np.ndarray, path: Path, bbox: List[int]) -> None:
    x, y, w, h = bbox
    path.parent.mkdir(parents=True, exist_ok=True)
    sub = ink[y:y + h, x:x + w]
    png = Image.fromarray(np.where(sub, 0, 255).astype(np.uint8), mode="L")
    png.save(path)



# ------------------------------------------------------------------ 数值金标构造
def seq_gold(rule: str, known: List[Any], gold: Any) -> Dict[str, Any]:
    return {"rule": rule, "known": known, "gold": str(gold)}


def arith(expr: str, expected: Fraction) -> Fraction:
    """表达式求值并断言与构造者预期一致（金标由构造确定，双保险）。"""
    val = eval(expr, {"__builtins__": {}}, {"Fraction": Fraction})
    assert val == expected, f"{expr} = {val} != {expected}"
    return val


def fmt(x: Fraction | float | int) -> str:
    if isinstance(x, Fraction):
        return f"{x.numerator}/{x.denominator}" if x.denominator != 1 else str(x.numerator)
    if isinstance(x, float):
        s = f"{x:.10g}"
        return s
    return str(x)


def opts4(gold: str, distractors: List[str]) -> List[Tuple[str, str]]:
    labels = ["A", "B", "C", "D"]
    texts = [gold] + distractors
    assert len(set(texts)) == 4, f"选项必须互不相同: {texts}"
    return list(zip(labels, texts))


def opts_shuffled(gold: str, distractors: List[str], order: List[int]) -> List[Tuple[str, str]]:
    labels = ["A", "B", "C", "D"]
    texts = [gold] + distractors
    texts = [texts[i] for i in order]
    assert len(set(texts)) == 4, texts
    return list(zip(labels, texts))


def label_of(options: List[Tuple[str, str]], gold_text: str) -> str:
    hit = [l for l, t in options if t == gold_text]
    assert len(hit) == 1, f"金标文本必须唯一命中: {gold_text} {options}"
    return hit[0]


def wrap_text(s: str, width: int = 40) -> List[str]:
    out, cur = [], ""
    breaks = "。；?!？!，,："
    for ch in s:
        cur += ch
        if len(cur) >= width or (ch in breaks and len(cur) >= width * 3 // 5):
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return [o for o in out if o.strip()] or [""]


def _wrap_all(lines: List[str], width: int = 40) -> List[str]:
    out: List[str] = []
    for ln in lines:
        out.extend(wrap_text(ln, width))
    return out


# ================================================================== 装配/QA/冻结
SPLIT = "test"
CONSTRUCTED_PROV = {"kind": "original-construction",
                    "note": "按行测真题题型分类学（来源见 DATASET_CARD.md）原创构造；"
                            "非题库逐字拷贝；金标由构造规则确定"}


def assemble(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    img_dir = BENCH / "images" / SPLIT
    reg_dir = BENCH / "regions"
    for row in rows:
        qid = row["id"]
        internal = {k: v for k, v in list(row.items()) if k.startswith("_")}
        for k in internal:
            del row[k]
        row.setdefault("layout", "text")
        # 选项统一为 {label,text} dict（元组序列化会被 loader 的 list 分支误解析）
        row["options"] = [{"label": str(l), "text": str(t)} for (l, t) in row["options"]]
        if "_fig" in internal:
            spec = internal["_fig"]
            cells, grid, options, labels = spec["cells"], spec["grid"], spec["options"], sorted(spec["options"])
            intro = wrap_text(row["stem"])[:2] if row.get("stem") else []
            ink, regions = render_figure_page(cells, grid[0], grid[1], options, labels, intro)
            page_path = img_dir / f"{qid}.png"
            save_page(page_path, ink)
            row["image"] = f"benchmark_xingce/images/{SPLIT}/{qid}.png"
            row["grid"] = grid
            x, y, w, h = regions["stem_matrix_bbox"]
            crop_save(ink, reg_dir / qid / "stem.png", [x, y, w, h])
            regions["stem_matrix_crop"] = f"regions/{qid}/stem.png"
            for op in regions["option_regions"]:
                bx, by, bw, bh = op["bbox"]
                crop_save(ink, reg_dir / qid / f"option_{op['label']}.png", [bx, by, bw, bh])
                op["crop_path"] = f"regions/{qid}/option_{op['label']}.png"
            row["regions"] = regions
            row.setdefault("answerability", "")
        elif "_cube" in internal:
            ink = render_cube_page(qid)
            save_page(img_dir / f"{qid}.png", ink)
            row["image"] = f"benchmark_xingce/images/{SPLIT}/{qid}.png"
            row["regions"] = None
        else:
            material = internal.get("_material")
            table = internal.get("_table")
            stem_lines: List[str] = []
            for ln in wrap_text(row["stem"]):
                stem_lines.append(ln)
            if table is not None:
                intro_line = wrap_text(row["stem"])[0]
                ink, regions = render_text_page(stem_lines, [(o["label"], o["text"]) for o in row["options"]],
                                                material=material, table=table)
            else:
                ink, regions = render_text_page(stem_lines, [(o["label"], o["text"]) for o in row["options"]],
                                                material=material)
            if table is not None:
                row["layout"] = "table"
            elif material is not None:
                row["layout"] = "text-material"
            save_page(img_dir / f"{qid}.png", ink)
            row["image"] = f"benchmark_xingce/images/{SPLIT}/{qid}.png"
            row["regions"] = regions
            if table is not None:
                # 表格行把表格单元格文本补回 stem 装载文本（oracle 从 regions.table 读取）
                pass
        prov = internal.get("_provenance") or dict(CONSTRUCTED_PROV)
        row["provenance"] = prov
        row["source"] = "benchmark_xingce/test"
        row["created"] = CREATED
        out.append(row)
    return out


# ------------------------------------------------------------------ 标注 QA（与解题能力无关）
def qa_check(rows: List[dict]) -> List[str]:
    problems: List[str] = []
    seen_ids = set()
    for r in rows:
        qid = r["id"]
        if qid in seen_ids:
            problems.append(f"{qid}: id 重复")
        seen_ids.add(qid)
        labels = [o["label"] for o in r["options"]]
        if sorted(labels) != ["A", "B", "C", "D"]:
            problems.append(f"{qid}: 选项标号 {labels}")
        texts = [o["text"] for o in r["options"]]
        if r["skill"] != "figure_reasoning" and len(set(texts)) != 4:
            problems.append(f"{qid}: 选项文本有重复")
        ans = r.get("answer")
        if ans is not None and ans not in labels:
            problems.append(f"{qid}: 答案标号不在选项中")
        if ans is None and r.get("expected_status") not in ("failed", "unsupported"):
            problems.append(f"{qid}: 无金标但 expected_status={r.get('expected_status')}")
        img = DATA_DIR / r.get("image", "")
        if r.get("image") and not img.is_file():
            problems.append(f"{qid}: 题图缺失 {img}")
        reg = r.get("regions")
        if isinstance(reg, dict):
            W, H = reg["image_size"]
            for tb in reg.get("text_regions") or []:
                x, y, w, h = tb["bbox"]
                if not (0 <= x and 0 <= y and x + w <= W and y + h <= H):
                    problems.append(f"{qid}: text bbox 越界 {tb['bbox']}")
            for op in reg.get("option_regions") or []:
                x, y, w, h = op["bbox"]
                if not (0 <= x and 0 <= y and x + w <= W and y + h <= H):
                    problems.append(f"{qid}: option bbox 越界 {qid} {op['label']}")
                cp = op.get("crop_path")
                if cp:
                    p = BENCH / cp
                    if not p.is_file():
                        problems.append(f"{qid}: 选项裁剪缺失 {p}")
                    elif int((np.array(Image.open(p)) < 128).sum()) < 5:
                        problems.append(f"{qid}: 选项裁剪近空白 {p}")
            sc = reg.get("stem_matrix_crop")
            if sc:
                p = BENCH / sc
                if not p.is_file():
                    problems.append(f"{qid}: 题干裁剪缺失 {p}")
                elif int((np.array(Image.open(p)) < 128).sum()) < 5:
                    problems.append(f"{qid}: 题干裁剪近空白 {p}")
    return problems


# ------------------------------------------------------------------ 防泄漏近重复检查
def norm(s: str) -> str:
    return re.sub(r"[\s，。：；、？！“”\"'（）()\[\]{}:;,.?!·—\-]+", "", s).lower()


def leak_check(rows: List[dict]) -> Tuple[int, List[str]]:
    """近重复检查。纯文本行按 stem 规范化比对；带图行内容为图——只有 stem 与图像字节同时命中才算泄漏。"""
    hits: List[str] = []
    compared = 0

    def norm_img(rel: str) -> str:
        p = DATA_DIR / str(rel)
        try:
            return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except OSError:
            return ""

    ours_text = {norm(r["stem"]) for r in rows if not r.get("image")}
    ours_img = {}
    for r in rows:
        if r.get("image"):
            ours_img[norm(r["stem"])] = (r["id"], norm_img(r["image"]))
    for path in DATA_DIR.rglob("*.jsonl"):
        if "benchmark_xingce" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            other = norm(str(payload.get("stem") or ""))
            if not other:
                continue
            compared += 1
            for n in ours_text:
                if n and (n == other or (len(n) >= 14 and (n in other or other in n))):
                    hits.append(f"{path.name}: {payload.get('id')}")
            if other in ours_img:
                qid, my_sha = ours_img[other]
                other_img = payload.get("image") or payload.get("image_path") or ""
                if other_img and norm_img(str(other_img)) == my_sha and my_sha:
                    hits.append(f"{path.name}: {payload.get('id')} ~ {qid}（图同）")
    return compared, sorted(set(hits))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="测试集为一次性冻结集；仅在建集初期允许重写（记入 rebuild_log）")
    a = ap.parse_args()
    test_dir = BENCH / SPLIT
    mpath = BENCH / "manifest.json"
    if mpath.is_file() and not a.force:
        raise SystemExit("[xingce] 测试集已冻结（manifest 存在）——不得重生成或改写。")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_xingce_content as content
    rows = assemble(content.get_all())
    problems = qa_check(rows)
    if problems:
        for p in problems:
            print("  QA:", p)
        raise SystemExit(f"[xingce] QA 失败 {len(problems)} 项")
    compared, leaks = leak_check(rows)
    if leaks:
        for l in leaks[:20]:
            print("  LEAK:", l)
        raise SystemExit(f"[xingce] 泄漏检查失败：{len(leaks)} 处近重复")
    test_dir.mkdir(parents=True, exist_ok=True)
    jsonl = test_dir / "xingce.jsonl"
    body = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n"
    jsonl.write_text(body, encoding="utf-8")
    digest = sha(jsonl)
    counts: Dict[str, int] = {}
    skills: Dict[str, int] = {}
    for r in rows:
        counts[r["expected_status"]] = counts.get(r["expected_status"], 0) + 1
        skills[r["skill"]] = skills.get(r["skill"], 0) + 1
    man: Dict[str, Any] = {}
    if mpath.is_file():
        prev = json.loads(mpath.read_text(encoding="utf-8"))
        man.setdefault("rebuild_log", []).append(prev.get("frozen") and
                                                  {"frozen": prev.get("frozen"),
                                                   "sha256": prev["splits"][SPLIT]["sha256"]})
    files = {"xingce.jsonl": digest}
    for p in sorted(BENCH.rglob("*.png")):
        files[str(p.relative_to(BENCH))] = sha(p)
    man.update({
        "benchmark": "xingce_heldout",
        "version": "v1",
        "frozen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "splits": {SPLIT: {"file": f"{SPLIT}/xingce.jsonl", "count": len(rows),
                           "sha256": digest,
                           "policy": "held-out test：不参与任何训练/调参/阈值选择；"
                                     "首次计分运行即为 headline，禁止据此回写题目"}},
        "counts_by_skill": skills,
        "counts_by_expected_status": counts,
        "leak_check": {"corpus_lines_compared": compared, "hits": len(leaks),
                       "method": "归一化后精确匹配 + 14 字以上包含"},
        "sha256_files": files,
    })
    mpath.write_text(json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    print(f"[xingce] {SPLIT} {len(rows)} 题 sha={digest[:16]}")
    print(f"[xingce] by skill: {skills}")
    print(f"[xingce] by status: {counts}")
    print(f"[xingce] leak: compared={compared} hits={len(leaks)}")


if __name__ == "__main__":
    main()

