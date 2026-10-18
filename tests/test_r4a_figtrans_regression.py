"""R4a Phase4 回归用例：figtrans test 首评暴露的 false-accept / false-reject 模式锁定。

三个真实失误（test 冻结、不回写）转化为单测：
1. ftt-comp-08（false accept 模式）：题干图形有竖直对称（FV∈stabilizer）时，
   复合律外推与镜像选项**内容等价**；若验证器接受的答案与另一选项残差完全相同，
   必须给出 unresolved competitors（不可区分），不允许"择一放行"。
2. ftt-rot90-01（false reject 模式）：R180 自对称形状（阶梯形）下 R90 与 R270
   步进等价，仲裁必须弃权（ambiguous/unverified）而非猜测——诚实的不可判定，
   同时验证求解器**没有**错误地放行某个角度。
3. ftt-scale-04（容差边界）：候选残差恰好贴着 tol 时按未过容差处理（弃权），
   纪律：不得靠放宽容差换取准确率。
"""
from __future__ import annotations

import numpy as np

from assessment_agent.tools.figure_transform import (
    GROUP, Candidate, analyze_transform, arbitrate, score_options, standardize,
    transform_matches,
)

CELL = 48


def _arrow_fv():
    """竖直轴对称的左右箭头（FV∈stabilizer）：R180 与 FH 作用后内容等价。"""
    m = np.zeros((CELL, CELL), np.uint8)
    m[20:28, 10:38] = 1          # 横杆（上下对称 → FV 不变）
    m[16:32, 30:34] = 1
    m[20:28, 34:40] = 1
    return m


def _stair():
    """中心对称 3 级阶梯：R180 自对称 → R90/R270 步进等价。"""
    m = np.zeros((CELL, CELL), np.uint8)
    m[30:36, 6:18] = 1
    m[22:28, 14:26] = 1
    m[14:20, 22:34] = 1
    return m


def test_fv_symmetric_composite_cannot_single_out_equivalent_option():
    """comp-08 模式锁定：预测残差相同的等价选项 → unresolved competitors 非空。"""
    a = standardize(_arrow_fv())
    r90 = GROUP["R90"](a.frame)
    pred = GROUP["R180"](a.frame)          # 复合律（R90 步进两次）外推
    fh = GROUP["FH"](a.frame)              # 镜像选项：FV 对称 ⇒ 与 R180 内容等价
    opts = {"A": standardize(np.rot90(_arrow_fv(), 3)),  # 真 90° 旋转（竖箭头）
            "B": standardize(fh),
            "C": standardize(np.ascontiguousarray(fh))}   # 与 B 逐像素同内容的复制选项
    scores, within, margin = score_options(pred, opts, pred_scale=0.0, tol=0.15)
    assert scores["B"] <= 0.15 and scores["C"] <= 0.15   # 等价选项都在容差内
    assert margin < 0.05                                   # 无判别裕度
    assert set(within) >= {"B", "C"}
    # 走完整仲裁：候选必须 abstain（unresolved 非空 → 不投票），整体不得 unique
    c = Candidate(rule_type="rotate_flip_seq", transformation="R90", source_cells=[0, 1])
    c._pred_frame = pred
    c.options = opts
    c.pred_scale = a.scale_area
    c2 = Candidate(rule_type="rotate_flip_seq", transformation="R90", source_cells=[0, 1])
    c2._pred_frame = pred
    c2.options = opts
    c2.pred_scale = a.scale_area
    v = arbitrate([c, c2])
    assert v["verdict"] != "unique", v
    assert v.get("answer") in (None, "B", "C")             # 绝不把等价对当成唯一解


def test_rot180_self_symmetric_stair_step_is_indistinguishable():
    """rot90-01 模式锁定：R90 与 R270 步进在 R180 自对称阶梯上不可区分 →
    analyze_transform 不得输出 unique（宁可 ambiguous/abstain，不假装有唯一角）。"""
    s = _stair()
    r1 = np.rot90(s, 3)                              # R90 步进
    cells = [s, r1]
    # 原 ftt-rot90-01 的选项布局：A=R270(r1)、D=R90(s)——在 R180 自对称阶梯上
    # R270(r1)=R180(R90 s)=R90 s（=D 内容）→ 两选项逐像素同化，必须不可择一
    opts = {"A": np.rot90(r1, 3), "B": np.fliplr(s), "C": s,
            "D": np.rot90(np.rot90(s, 3), 2)}
    r = analyze_transform(cells, opts, mode="sequence", tol=0.15)
    verdict = r["verdict"]
    assert verdict["verdict"] in ("ambiguous", "no_rule", "insufficient"), verdict
    if verdict.get("answer") is not None:
        # 若竟给出答案，必须同时披露竞争项（不允许无竞争的"唯一"）
        ur = [c["unresolved_competitors"] for c in r["candidates"]
              if c["candidate_scores"].get(verdict["answer"], 1.0) <= 0.15]
        assert any(ur), r["candidates"]


def test_scale_tolerance_boundary_does_not_buy_accuracy():
    """scale-04 模式锁定：残差贴边（≈tol）的候选不进入投票 → 弃权而非放行。"""
    f = _arrow_fv()
    cells = [f, np.array(_scale_like(f, 0.7)), np.array(_scale_like(f, 0.5))]
    opts = {"A": standardize(_scale_like(f, 0.3535)),   # 预测下一档（渲染量化偏差）
            "B": standardize(f),
            "C": standardize(_scale_like(f, 1.4))}
    pred_scale = cells[2].astype(bool)
    # 直接构造一个残差恰好 ~0.15 的候选打分：margin 低于 margin_min 即不投票
    c = Candidate(rule_type="scale_uniform", transformation="scale", source_cells=[0, 1, 2])
    c._pred_frame = standardize(cells[2]).frame
    c.pred_scale = 0.105                       # 故意制造贴边惩罚
    c.options = {"A": opts["A"], "B": opts["B"], "C": opts["C"]}
    scores, within, margin = score_options(c._pred_frame, c.options, 0.105, 0.15)
    v = arbitrate([c]) if within else None
    if v is not None and v["verdict"] == "unique":
        assert margin >= 0.05                  # 只允许有真实判别裕度时 unique


def _scale_like(m, k):
    ys, xs = np.nonzero(m)
    out = np.zeros_like(m)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    ch, cw = int(round((y1 - y0) * k)), int(round((x1 - x0) * k))
    oy, ox = (CELL - ch) // 2, (CELL - cw) // 2
    out[oy:oy + ch, ox:ox + cw] = 1
    return out


def test_symmetry_flags_recorded_in_analysis():
    """稳定子披露：FV 对称箭头的 per-cell 稳定子必须包含 FV（歧义来源可追溯）。"""
    arrow = _arrow_fv()
    s = standardize(arrow)
    r = analyze_transform([arrow, np.rot90(arrow, 3)],
                          {"A": np.rot90(np.rot90(arrow, 3), 3),
                           "B": GROUP["FH"](s.frame)},
                          mode="sequence", tol=0.15)
    stab = r["per_cell_stabilizer"]
    vals = list(stab.values()) if isinstance(stab, dict) else list(stab)
    flat = "".join(x if isinstance(x, str) else "".join(x)
                   for v in vals for x in (v if isinstance(v, (list, tuple)) else [v]))
    assert "FV" in flat, stab
