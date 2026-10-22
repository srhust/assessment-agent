"""R4a 阶段2/3：图形变换核心（离散旋转/镜像/平移/缩放/组合/九宫格/叠加/对称歧义）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.tools.figure_transform import (  # noqa: E402
    FRAME, GROUP, Candidate, analyze_transform, compose, residual, score_options,
    stabilizer, standardize, transform_matches)


def box(x0, y0, w, h, hw=40):
    m = np.zeros((hw, hw), np.uint8)
    m[y0:y0 + h, x0:x0 + w] = 1
    return m


def rot(a):
    """原始画布上的视觉顺时针 90°（不 fit）：同卷格间 scale_area 保持一致。"""
    return np.rot90(a, 3)


def L():
    """┌ 形（横臂 20、竖臂 22 不等长）：稳定子平凡，无对称捷径。"""
    return box(8, 8, 20, 6) + box(8, 8, 6, 22)


# ---------------------------------------------------------------- 标准表示

def test_standardize_keeps_relative_position_and_area():
    left = box(2, 18, 8, 4)
    right = box(30, 18, 8, 4)
    sl, sr = standardize(left), standardize(right)
    assert sl.center[0] < 0.35 < sr.center[0]        # 帧内相对坐标保留
    assert abs(sl.scale_area - sr.scale_area) < 1e-6  # 同尺寸不同位置：相对面积一致
    small, big = box(18, 18, 4, 4), box(8, 8, 24, 24)
    assert standardize(small).scale_area < standardize(big).scale_area / 4  # 等比缩放可比


def test_compose_table_is_group():
    # D4 复合抽查 + 封闭性
    assert compose("R90", "R90") == "R180"
    assert compose("R270", "R90") == "I"
    assert compose("FH", "R90") in GROUP
    for a in GROUP:
        assert compose("I", a) == a and compose(a, "I") == a


# ---------------------------------------------------------------- 变换与对称

def test_rotation_90_is_reversible_by_270():
    f = L()
    r90 = GROUP["R90"](standardize(f).frame)
    back = GROUP["R270"](r90)
    assert residual(back, r90) > 0.3          # R270∘R90 ≠ R90
    ident = GROUP["I"](standardize(f).frame)
    assert residual(GROUP["R270"](GROUP["R90"](ident)), ident) <= 0.05


def test_mirror_symmetry_stabilizer_detects_indistinguishability():
    bar = box(12, 17, 16, 6)                  # 中心对称横条：R180 后与自身一致
    sh = standardize(bar)
    st = stabilizer(sh)
    assert {"I", "R180", "FH", "FV"} <= set(st)   # 显式不可区分集
    assert transform_matches(sh, sh) == st         # 匹配集=稳定子：不得断言唯一角度


def test_asymmetric_shape_has_trivial_stabilizer():
    sh = standardize(L())
    assert stabilizer(sh) == ["I"]


# ---------------------------------------------------------------- 序列规律

def test_sequence_rotation_unique_answer():
    t = L()
    s1, s2, s3, s4 = t, rot(t), rot(rot(t)), rot(rot(rot(t)))
    r = analyze_transform([s1, s2, s3], {"A": s4, "B": t, "C": rot(rot(t))})
    assert r["verdict"]["verdict"] == "unique" and r["verdict"]["answer"] == "A"
    best = r["candidates"][0]
    for fld in ("rule_type", "transformation", "source_cells", "candidate_scores",
                "geometric_residual", "discriminative_margin", "confidence",
                "unresolved_competitors"):
        assert fld in best                      # 阶段3候选字段完备


def test_sequence_scale_relative_area():
    s = [box(18, 18, 4, 4), box(16, 16, 8, 8), box(12, 12, 16, 16)]
    nxt = box(4, 4, 32, 32)
    r = analyze_transform(s, {"A": nxt, "B": s[0], "C": s[2]})
    assert r["verdict"]["verdict"] == "unique" and r["verdict"]["answer"] == "A"
    scale_c = [c for c in r["candidates"] if c["rule_type"] == "scale"]
    assert scale_c and scale_c[0]["transformation"].startswith("scale_area")


def test_sequence_translate_rule_recorded():
    p = [box(2, 18, 8, 4), box(14, 18, 8, 4), box(26, 18, 8, 4)]
    r = analyze_transform(p, {"A": p[0], "B": box(18, 2, 4, 8)})
    kinds = {c["rule_type"] for c in r["candidates"]}
    assert "translate" in kinds                  # 平移律有审计记录
    # R14-D 收紧（人工纪律"两表示同错不算正确性证明"）：内容恒定的序列在
    # 形状归一空间退化，I/F 族候选是假证据，已整体弃权；平移预测越界时
    # 如实不作答。旧断言 unique A 实为退化形状恒等放行（trans-07 错误
    # accept 同型），方向为收紧而非放松。
    assert r["verdict"]["verdict"] != "unique"
    assert r["verdict"]["answer"] is None


def test_composition_mirror_then_rotate():
    s0 = standardize(L()).frame                 # 统一 64 画布上的组合律（镜像后旋转）
    step = lambda f: GROUP["R90"](GROUP["FH"](f))
    s1 = step(s0)
    s2 = step(s1)
    r = analyze_transform([s0, s1], {"A": s2, "B": s1, "C": s0})
    comp = compose("R90", "FH")
    assert r["verdict"]["verdict"] in ("unique", "ambiguous")
    if r["verdict"]["verdict"] == "unique":
        # 预测内容必须等于复合元素 step(s1)（若复合与某单元素等价则同名，仍指向唯一匹配）
        assert r["verdict"]["answer"] == "A"
        assert comp in GROUP


# ---------------------------------------------------------------- 九宫格/叠加

def test_grid3_row_col_conflict_is_ambiguous():
    # 行律=顺时针步进（┌ 无对称、唯一），行间重复 → 列律=恒等；两律指向不同选项
    s0 = standardize(L()).frame
    s1 = GROUP["R270"](s0)
    s2 = GROUP["R270"](s1)
    cells = [s0, s1, s2, s0, s1, s2, s0, s1]        # 空格在 (2,2)
    r = analyze_transform(cells, {"A": GROUP["R270"](s2), "B": GROUP["R270"](s0),
                                  "C": s1}, mode="grid3")
    assert r["verdict"]["verdict"] in ("ambiguous", "no_rule")
    assert r["verdict"].get("answer") is None        # 绝不多数投票作答


def test_grid3_overlay_xor():
    xor_f = lambda x, y: ((x > 0) ^ (y > 0)).astype(np.uint8)
    a, b = box(6, 6, 24, 8), box(14, 6, 8, 24)   # 大重叠区：xor≠or≠and
    c3 = xor_f(a, b)
    d, e = box(8, 24, 24, 8), box(24, 14, 8, 18)
    f3 = xor_f(d, e)
    g, h = box(4, 4, 12, 32), box(4, 4, 32, 12)
    cells = [a, b, c3, d, e, f3, g, h]
    ans = xor_f(g, h)
    r = analyze_transform(cells, {"A": ans, "B": np.logical_or(g, h).astype(np.uint8),
                                  "C": np.logical_and(g, h).astype(np.uint8)},
                          mode="grid3")
    assert r["verdict"]["verdict"] == "unique" and r["verdict"]["answer"] == "A"
    ov = [c for c in r["candidates"] if c["rule_type"] == "overlay_grid_row"]
    assert ov and ov[0]["transformation"] == "overlay_xor"


# ---------------------------------------------------------------- 纪律与降级

def test_label_and_position_blindness():
    """相同内容换标签：答案标签随内容走，不依赖 A/B/C 顺序或位置。"""
    s0 = standardize(L()).frame
    s1 = GROUP["R270"](s0)
    s2 = GROUP["R270"](s1)
    nxt = GROUP["R270"](s2)
    opts_ab = {"A": nxt, "B": s0}
    opts_ba = {"B": s0, "A": nxt}          # 定义顺序颠倒
    r1 = analyze_transform([s0, s1, s2], opts_ab)
    r2 = analyze_transform([s0, s1, s2], opts_ba)
    assert r1["verdict"]["verdict"] == "unique" and r1["verdict"]["answer"] == "A"
    assert r2["verdict"]["answer"] == "A"  # 只看内容，不看声明顺序
    # 同一答案内容改挂到 C 标签：跟着变 C
    r3 = analyze_transform([s0, s1, s2], {"C": nxt, "B": s0})
    assert r3["verdict"]["answer"] == "C"


def test_unresolvable_symmetry_competitors_reported():
    """旋转对称题面 + 两个不可区分选项 → unresolved_competitors 非空且不 unique。

    R14-D：原构造 [bar,bar,bar] 是内容恒等序列——退化形状族已被弃权（不再
    产生候选），该形态由下方 degenerate 断言单独锁定；competitors 语义改用
    **非恒定**对称序列（一致 R90 步长）复现。"""
    t = L()
    s0 = standardize(t).frame
    s1 = GROUP["R90"](s0)
    s2 = GROUP["R90"](s1)
    seq = [s0, s1, s2]
    nxt = GROUP["R90"](s2)
    r = analyze_transform(seq, {"A": nxt, "B": np.roll(nxt, 2, axis=0)})
    assert r["verdict"]["verdict"] != "unique"
    assert any(c["unresolved_competitors"] for c in r["candidates"])
    # 恒定序列退化：形状族弃权、无候选、不作答
    bar = box(12, 17, 16, 6)
    rd = analyze_transform([bar, bar, bar], {"A": bar, "B": np.roll(bar, 2, axis=0)})
    assert rd["verdict"]["verdict"] != "unique"
    assert rd["verdict"]["answer"] is None


def test_canvas_mismatch_degrades_scale_dimension():
    """格间画布尺寸不一致（裁剪异常）→ 尺度维度不可信，整体降级不作答。"""
    t = L()
    s0 = standardize(t).frame                    # 64×64 画布
    small = box(1, 5, 8, 3, hw=12)               # 12×12 画布（面积比 1600/144≫1.6）
    r = analyze_transform([small, small, small], {"A": s0, "B": standardize(small).frame})
    assert any("canvas_mismatch" in f for f in r["symmetry_unresolvable"])
    assert r["verdict"].get("answer") is None


def test_blank_cell_honest_insufficient():
    r = analyze_transform([L(), np.zeros((40, 40), np.uint8)], {"A": L()})
    assert r["status"] == "insufficient"
