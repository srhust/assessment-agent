"""R4a 图形变换基准生成器：合成九类变换题（旋转/镜像/组合/平移/缩放/九宫格/叠加/歧义）。

设计纪律：
- 题目按**变换规律网格**人工模板化编写，期望答案在渲染前由规律定义确定，
  绝不按 Solver 是否解对筛选样本；
- 歧义题（对称不可区分 / 双律冲突 / 画布失配）作为诚实 failed 样本保留（expected=null）；
- dev 可调参迭代；test 在 dev 完成后 `--publish-test` 一次性生成并冻结 sha；
- 每题输出：整图 + 题干矩阵裁剪 + 每选项裁剪 + regions 金标（像素 bbox）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("需要 opencv-python-headless") from exc

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "benchmark_figtrans"
CELL = 48
CREATED = "2026-10-18"


# ---------------------------------------------------------------- 形状库（48×48）

def _canvas() -> np.ndarray:
    return np.zeros((CELL, CELL), dtype=np.uint8)


def hbar(y0: int, x0: int) -> np.ndarray:
    m = _canvas()
    m[y0:y0 + 8, x0:x0 + 20] = 1
    return m


def vbar(x0: int, y0: int) -> np.ndarray:
    m = _canvas()
    m[y0:y0 + 28, x0:x0 + 8] = 1
    return m


def shape_tee() -> np.ndarray:
    m = _canvas()
    m[10:14, 10:34] = 1
    m[10:32, 10:14] = 1
    return m


def shape_arrow() -> np.ndarray:
    m = _canvas()
    pts = np.array([[9, 8], [39, 20], [9, 32], [17, 20]], np.int32)
    cv2.fillPoly(m, [pts], 1)
    m[17:23, 4:20] = 1
    return m


def shape_stair() -> np.ndarray:
    m = _canvas()
    m[30:36, 6:18] = 1
    m[22:28, 14:26] = 1
    m[14:20, 22:34] = 1
    return m


def shape_flag() -> np.ndarray:
    m = _canvas()
    m[8:36, 10:14] = 1
    m[8:20, 14:32] = 1
    m[12:16, 32:38] = 1
    return m


def shape_bar() -> np.ndarray:
    m = _canvas()
    m[20:28, 14:34] = 1
    return m


def shape_rect() -> np.ndarray:
    m = _canvas()
    m[12:36, 10:38] = 1
    m[16:32, 14:34] = 0
    return m


def shape_dots() -> np.ndarray:
    m = _canvas()
    cv2.circle(m, (15, 15), 5, 1, -1)
    cv2.circle(m, (32, 30), 7, 1, -1)
    return m


SHAPES = {"tee": shape_tee, "arrow": shape_arrow, "stair": shape_stair,
          "flag": shape_flag, "bar": shape_bar, "rect": shape_rect,
          "dots": shape_dots}


# ---------------------------------------------------------------- 画布级变换
# （在原 48×48 画布上操作：画布一致，尺度/位置为相对坐标维度）

def rot90cw(m: np.ndarray) -> np.ndarray:
    return np.rot90(m, 3)


def fliplr(m: np.ndarray) -> np.ndarray:
    return np.fliplr(m).copy()


def flipud(m: np.ndarray) -> np.ndarray:
    return np.flipud(m).copy()


def scale(m: np.ndarray, k: float) -> np.ndarray:
    ys, xs = np.nonzero(m)
    if not len(xs):
        return m
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = m[y0:y1, x0:x1]
    nh, nw = max(2, int(round(crop.shape[0] * k))), max(2, int(round(crop.shape[1] * k)))
    r = cv2.resize(crop.astype(np.uint8), (nw, nh), interpolation=cv2.INTER_NEAREST)
    out = _canvas()
    oy, ox = (CELL - nh) // 2, (CELL - nw) // 2
    out[oy:oy + nh, ox:ox + nw] = r
    return out


def translate(m: np.ndarray, dy: int, dx: int) -> np.ndarray:
    ys, xs = np.nonzero(m)
    out = _canvas()
    if not len(xs):
        return out
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = m[y0:y1, x0:x1]
    h, w = crop.shape
    cy, cx = (CELL - h) // 2 + dy, (CELL - w) // 2 + dx
    cy, cx = max(0, min(CELL - h, cy)), max(0, min(CELL - w, cx))
    out[cy:cy + h, cx:cx + w] = crop
    return out


def xor(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return ((a > 0) ^ (b > 0)).astype(np.uint8)


def bor(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.logical_or(a, b).astype(np.uint8)


# ---------------------------------------------------------------- 渲染

def _png(cells, rows, cols):
    img = np.full((rows * CELL, cols * CELL), 255, dtype=np.uint8)   # 白底
    for i, cell in enumerate(cells):
        if cell is None:
            continue
        r, c = divmod(i, cols)
        img[r * CELL:(r + 1) * CELL, c * CELL:(c + 1) * CELL] = np.where(cell, 0, 255)
    return img


def save_png(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), img)


def option_img(m: np.ndarray) -> np.ndarray:
    return np.where(m, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 题目模板

def seq_item(qid, name, cells, options, answer, group, tags, expected="solved",
             ans_label=None):
    """一行 N 格（末格空白）。options: {label: mask}；answer 为 label（expected=solved）或 None。"""
    rows, cols = 1, len(cells) + 1
    labels = list(options.keys())
    return dict(
        id=qid, name=name, rows=rows, cols=cols, cells=cells, options=options,
        labels=labels, answer=ans_label or answer, expected=expected, group=group,
        tags=["figtrans", "R4a", group] + tags,
    )


def grid_item(qid, name, cells8, options, answer, group, tags, expected="solved"):
    rows, cols = 3, 3
    cells = cells8 + [None]
    return dict(id=qid, name=name, rows=rows, cols=cols, cells=cells, options=options,
                labels=list(options.keys()), answer=answer, expected=expected,
                group=group, tags=["figtrans", "R4a", group] + tags)


def build_items(which: str):
    T, A, S, F, B, R, D = (shape_tee(), shape_arrow(), shape_stair(), shape_flag(),
                           shape_bar(), shape_rect(), shape_dots())
    items = []
    if which == "dev":
        # --- 离散旋转 ---
        items.append(seq_item("ft-rot90-01", "顺时针 90° 步进",
                              [T, rot90cw(T), rot90cw(rot90cw(T))],
                              {"A": rot90cw(rot90cw(rot90cw(T))), "B": T,
                               "C": rot90cw(rot90cw(T)), "D": fliplr(T)},
                              "A", "rotate90", ["序列"]))
        items.append(seq_item("ft-rot180-02", "180° 步进",
                              [S, rot90cw(rot90cw(S))],
                              {"A": rot90cw(rot90cw(rot90cw(rot90cw(S)))), "B": rot90cw(S),
                               "C": fliplr(S)},
                              "A", "rotate180", ["序列", "恒等回落"]))
        items.append(seq_item("ft-rot270-03", "逆时针 90° 步进",
                              [F, rot90cw(F), rot90cw(rot90cw(F))],
                              {"A": rot90cw(rot90cw(rot90cw(F))), "B": F, "C": flipud(F)},
                              "A", "rotate270", ["序列"]))
        # --- 镜像 ---
        items.append(seq_item("ft-mirror-04", "水平镜像交替",
                              [T, fliplr(T)],
                              {"A": T, "B": fliplr(T), "C": rot90cw(T)},
                              "A", "mirror", ["序列", "周期2"]))
        items.append(seq_item("ft-mirror-05", "垂直镜像",
                              [A, flipud(A)],
                              {"A": fliplr(A), "B": A, "C": flipud(fliplr(A))},
                              "B", "mirror", ["序列"]))
        # --- 组合（元素=副对角镜像，等效 镜像∘旋转）---
        FA = lambda m: rot90cw(flipud(m))
        items.append(seq_item("ft-comp-06", "镜像+旋转组合律",
                              [F, FA(F)],
                              {"A": FA(FA(F)), "B": rot90cw(F), "C": flipud(F)},
                              "A", "composition", ["序列", "组合"]))
        # --- 相对平移 ---
        items.append(seq_item("ft-trans-07", "帧内右移",
                              [translate(B, 0, -6), translate(B, 0, 0), translate(B, 0, 6)],
                              {"A": translate(B, 0, 12), "B": translate(B, 0, -12),
                               "C": rot90cw(B)},
                              "A", "translate", ["序列"]))
        items.append(seq_item("ft-trans-08", "帧内上移",
                              [translate(D, 6, 0), translate(D, 0, 0), translate(D, -6, 0)],
                              {"A": translate(D, -12, 0), "B": D, "C": scale(D, 1.5)},
                              "A", "translate", ["序列"]))
        # --- 等比缩放 ---
        items.append(seq_item("ft-scale-09", "等比放大",
                              [scale(T, 0.44), scale(T, 0.667), scale(T, 1.0)],
                              {"A": scale(T, 1.5), "B": scale(T, 0.44), "C": fliplr(T)},
                              "A", "scale", ["序列", "相对面积"]))
        # --- 九宫格：行内一致变换 ---
        row90 = [T, rot90cw(T), rot90cw(rot90cw(T))]
        row90b = [rot90cw(T), rot90cw(rot90cw(T)), rot90cw(rot90cw(rot90cw(T)))]
        items.append(grid_item("ft-grid-10", "九宫格行内 R90 梯度",
                               row90 + row90b + [rot90cw(rot90cw(T)), rot90cw(rot90cw(rot90cw(T)))],
                               {"A": rot90cw(rot90cw(rot90cw(rot90cw(T)))),
                                "B": rot90cw(T), "C": flipud(T), "D": fliplr(T)},
                               "A", "grid3", ["行列一致"]))
        r1 = [S, fliplr(S), S]
        r2 = [F, fliplr(F), F]
        r3 = [D, fliplr(D)]
        items.append(grid_item("ft-grid-11", "九宫格行内水平镜像",
                               r1 + r2 + r3,
                               {"A": rot90cw(D), "B": D, "C": fliplr(D)},
                               "B", "grid3", ["镜像周期2"]))
        # --- 叠加（每行前两格用不同形状，排除 镜像/旋转 律干扰）---
        T2 = shape_tee()
        B2 = shape_bar()
        # overlay-12：十字相叠（交集 8×8 → xor/or 画布残差 ≈0.2，可判别）
        p1a, p1b = hbar(10, 10), vbar(20, 4)
        p2a, p2b = hbar(18, 14), vbar(24, 8)
        p3a, p3b = hbar(14, 12), vbar(22, 6)
        items.append(grid_item("ft-overlay-12", "去同存异叠加",
                               [p1a, p1b, xor(p1a, p1b), p2a, p2b, xor(p2a, p2b), p3a, p3b],
                               {"A": xor(p3a, p3b), "B": bor(p3a, p3b),
                                "C": np.logical_and(p3a, p3b).astype(np.uint8), "D": p3a},
                               "A", "overlay", ["xor"]))
        q1a, q1b = translate(T2, -12, -4), translate(B2, -12, 4)
        q2a, q2b = translate(S, 12, -2), translate(F, 12, 6)
        q3a, q3b = translate(D, 0, -4), translate(B2, 0, 4)
        items.append(grid_item("ft-overlay-13", "并集叠加",
                               [q1a, q1b, bor(q1a, q1b), q2a, q2b, bor(q2a, q2b), q3a, q3b],
                               {"A": xor(q3a, q3b), "B": bor(q3a, q3b),
                                "C": np.logical_and(q3a, q3b).astype(np.uint8), "D": q3b},
                               "B", "overlay", ["or"]))
        # --- 歧义诚实样本（expected failed）---
        items.append(seq_item("ft-sym-14", "中心对称图形角度不可判",
                              [R, R, R],
                              {"A": R, "B": rot90cw(rot90cw(R))},   # 同一内容 → 不可区分
                              None, "ambiguous", ["对称不可区分", "诚实failed"],
                              expected="failed"))
        items.append(grid_item("ft-conflict-15", "全同格+双同内容选项不可择一",
                               [R, R, R, R, R, R, R, R],
                               {"A": R, "B": rot90cw(rot90cw(R)), "C": scale(R, 0.6)},
                               None, "ambiguous", ["多律稳定子", "诚实failed"],
                               expected="failed"))
        items.append(seq_item("ft-canvas-16", "选项画布失配降级",
                              [T, rot90cw(T), rot90cw(rot90cw(T))],
                              {"A": np.pad(rot90cw(rot90cw(rot90cw(T))), 8),  # 64×64 画布
                               "B": T},
                              None, "degraded", ["canvas_mismatch", "诚实failed"],
                              expected="failed"))
    if which == "test":
        # 独立测试集：不同形状组合 + 未调参方向（首评即终评）
        items.append(seq_item("ftt-rot90-01", "顺时针步进",
                              [S, rot90cw(S), rot90cw(rot90cw(S))],
                              {"A": rot90cw(rot90cw(rot90cw(S))), "B": fliplr(S),
                               "C": S, "D": rot90cw(S)},
                              "A", "rotate90", ["test"]))
        items.append(seq_item("ftt-mirror-02", "水平镜像",
                              [F, fliplr(F)],
                              {"A": rot90cw(F), "B": F, "C": flipud(F)},
                              "B", "mirror", ["test"]))
        items.append(seq_item("ftt-trans-03", "左移",
                              [translate(B, 0, 6), translate(B, 0, 0), translate(B, 0, -6)],
                              {"A": translate(B, 0, -12), "B": rot90cw(B), "C": B},
                              "A", "translate", ["test"]))
        items.append(seq_item("ftt-scale-04", "等比缩小",
                              [scale(F, 1.0), scale(F, 0.7), scale(F, 0.5)],
                              {"A": scale(F, 0.35), "B": scale(F, 1.0), "C": fliplr(F)},
                              "A", "scale", ["test"]))
        items.append(grid_item("ftt-grid-05", "行内镜像",
                               [S, fliplr(S), S, T, fliplr(T), T, D, fliplr(D)],
                               {"A": D, "B": fliplr(D), "C": rot90cw(D)},
                               "A", "grid3", ["test"]))
        items.append(grid_item("ftt-overlay-06", "xor 叠加",
                               [translate(B, -10, -4), translate(T, -10, 4),
                                xor(translate(B, -10, -4), translate(T, -10, 4)),
                                translate(S, 10, -2), translate(F, 10, 6),
                                xor(translate(S, 10, -2), translate(F, 10, 6)),
                                translate(D, 0, -4), translate(B, 0, 4)],
                               {"A": xor(translate(D, 0, -4), translate(B, 0, 4)),
                                "B": translate(D, 0, -4), "C": translate(B, 0, 4)},
                               "A", "overlay", ["test"]))
        items.append(seq_item("ftt-rot180-07", "180 交替",
                              [T, rot90cw(rot90cw(T))],
                              {"A": T, "B": rot90cw(T), "C": fliplr(T)},
                              "A", "rotate180", ["test"]))
        items.append(seq_item("ftt-comp-08", "组合律",
                              [A, rot90cw(fliplr(A))],
                              {"A": rot90cw(rot90cw(fliplr(rot90cw(fliplr(A))))),
                               "B": fliplr(A), "C": A},
                              "A", "composition", ["test"]))
        items.append(seq_item("ftt-sym-09", "对称歧义诚实样本",
                              [B, B],
                              {"A": B, "B": flipud(B)},
                              None, "ambiguous", ["test", "诚实failed"], expected="failed"))
        rb = shape_bar()
        items.append(grid_item("ftt-conflict-10", "全同格+双同内容选项",
                               [rb, rb, rb, rb, rb, rb, rb, rb],
                               {"A": rb, "B": rot90cw(rot90cw(rb)), "C": flipud(rb)},
                               None, "ambiguous", ["test", "诚实failed"], expected="failed"))
    return items


# ---------------------------------------------------------------- 落盘

def publish(which: str, freeze: bool) -> None:
    items = build_items(which)
    split_dir = OUT / which
    imgs = OUT / "images" / which
    regions_root = OUT / "regions"
    rows = []
    for it in items:
        qid = it["id"]
        stem_img = _png(it["cells"], it["rows"], it["cols"])
        full = np.full((it["rows"] * CELL + 40, it["cols"] * CELL + 40), 255, np.uint8)
        full[20:20 + it["rows"] * CELL, 20:20 + it["cols"] * CELL] = stem_img
        stem_path = regions_root / qid / "stem.png"
        save_png(stem_path, stem_img)
        save_png(imgs / f"{qid}.png", full)
        opt_regions = []
        x = 20
        for i, label in enumerate(it["labels"]):
            op = regions_root / qid / f"option_{label}.png"
            save_png(op, option_img(it["options"][label]))
            opt_regions.append({"label": label, "bbox": [x, it["rows"] * CELL + 60, CELL, CELL],
                                "text": "图", "has_figure": True,
                                "figure_bbox": [x, it["rows"] * CELL + 60, CELL, CELL],
                                "crop_path": f"regions/{qid}/option_{label}.png"})
            x += CELL
        row = dict(
            id=qid, skill="figure_reasoning", question_type="single_choice",
            stem=f"{it['name']}。按规律，问号处应填入哪个图形？",
            options=[{"label": l, "text": "图"} for l in it["labels"]],
            answer=it["answer"], expected_status=it["expected"],
            tags=it["tags"], group=it["group"], layout="figtrans",
            image=f"benchmark_figtrans/images/{which}/{qid}.png",
            grid=[it["rows"], it["cols"]],
            regions=dict(bbox_format="pixel_xywh",
                         image_size=[int(full.shape[1]), int(full.shape[0])],
                         stem_matrix_bbox=[20, 20, it["cols"] * CELL, it["rows"] * CELL],
                         stem_matrix_grid=[it["rows"], it["cols"]],
                         stem_matrix_crop=f"regions/{qid}/stem.png",
                         option_regions=opt_regions),
            source=dict(provenance="synthetic-template", created=CREATED,
                        note="变换规律模板渲染；期望在渲染前由规律定义确定"),
        )
        rows.append(row)
    raw = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n"
    split_dir.mkdir(parents=True, exist_ok=True)
    target = split_dir / "figtrans.jsonl"
    if freeze and target.exists():
        raise SystemExit(f"test 已冻结，禁止重写：{target}")
    target.write_bytes(raw.encode("utf-8"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    mpath = OUT / "manifest.json"
    man = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {
        "name": "benchmark_figtrans",
        "data_class": "synthetic-template（图形变换规律模板渲染；期望在运行前定义；歧义题如实 failed）",
        "created_at": CREATED, "splits": {}, "dev_history": [],
        "independence_declaration": (
            "test 在 dev 完成后生成并冻结，只允许首次且唯一一次评测；不按 Solver 结果筛题；"
            "歧义/降级题保留为诚实 failed。"),
    }
    if which == "dev":
        prev = man["splits"].get("dev")
        if prev and prev.get("sha256") != digest:
            man.setdefault("dev_history", []).append(prev)
    man["splits"][which] = {
        "file": f"{which}/figtrans.jsonl", "count": len(rows), "sha256": digest,
        "policy": "可调参；重生成记 dev_history" if which == "dev"
                  else "冻结：首次且唯一一次评测；不得回写",
    }
    mpath.write_text(json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    counts = {}
    for r in rows:
        counts[r["expected_status"]] = counts.get(r["expected_status"], 0) + 1
    print(f"[figtrans] {which} {len(rows)} 题 sha={digest[:16]} {counts}")


if __name__ == "__main__":
    publish("dev", freeze=False)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish-test", action="store_true")
    a = ap.parse_args()
    if a.publish_test:
        publish("test", freeze=True)
