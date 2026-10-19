"""行测 held-out 测试集内容表（金标由构造确定，渲染/装载见 make_xingce_benchmark.py）。

每个模块返回 row dict 列表；`_` 前缀键为渲染指令（装载前剥离）。
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import make_figtrans_benchmark as ft

from make_xingce_benchmark import (opts_shuffled, label_of, font, PAGE_W, CELL)

# ------------------------------------------------------------------ 数字推理
def ns_rows() -> List[dict]:
    def mk(qid, seq, gold, distr, order, rule, why, extra_tags=()):
        options = opts_shuffled(gold, distr, order)
        return {"id": qid, "skill": "number_series",
                "stem": f"找出数列规律，选择下一项：{seq}，（ ）",
                "options": options, "answer": label_of(options, gold),
                "expected_status": "solved", "group": f"g-ns-{rule}",
                "tags": ["xingce", "number_series", rule, *extra_tags],
                "rationale": why}
    return [
        mk("xn-ns-01", "9, 20, 42, 86", "174", ["172", "176", "178"], [0, 1, 2, 3],
           "multi-diff2", "逐级差分 11,22,44,(88)，二级差等比；86+88=174"),
        mk("xn-ns-02", "1, 3, 4, 7, 11", "18", ["17", "19", "20"], [3, 0, 2, 1],
           "recurrence-sum", "和递推 a(n)=a(n-1)+a(n-2)：7+11=18"),
        mk("xn-ns-03", "0, 9, 26, 65", "124", ["122", "126", "128"], [2, 0, 3, 1],
           "power-alter", "n³±1 交替：1-1,8+1,27-1,64+1,125-1=124"),
        mk("xn-ns-04", "2, 5, 11, 23, 47", "95", ["93", "97", "99"], [1, 0, 2, 3],
           "double-plus1", "a(n+1)=2·a(n)+1：47×2+1=95"),
        mk("xn-ns-05", "1, 4, 3, 9, 5, 16, 7", "25", ["20", "24", "30"], [0, 2, 1, 3],
           "group-interleave", "奇位 1,3,5,7；偶位 4,9,16,(25)=2²,3²,4²,5²"),
        mk("xn-ns-06", "2, 6, 12, 20, 30", "42", ["40", "44", "46"], [3, 1, 0, 2],
           "n-n+1", "n(n+1)：6×7=42"),
        mk("xn-ns-07", "1/2, 3/4, 5/8, 7/16", "9/32", ["9/18", "9/24", "11/32"], [2, 0, 1, 3],
           "fraction", "分子 1,3,5,7,(9)；分母 2,4,8,16,(32) → 9/32（分数数列超出整数规律族，兼测覆盖）",
           extra_tags=("capability-gap-hint",)),
        mk("xn-ns-08", "2, 5, 10, 17, 26", "37", ["35", "36", "38"], [0, 1, 3, 2],
           "diff-arith", "一级差 3,5,7,9,(11)：26+11=37"),
        mk("xn-ns-09", "1, 2, 6, 24, 120", "720", ["480", "600", "840"], [1, 2, 0, 3],
           "recurrence-product", "阶乘 n!：6!=720"),
        mk("xn-ns-10", "4, 6, 10, 16, 24", "34", ["32", "36", "38"], [3, 0, 2, 1],
           "multi-diff", "一级差 2,4,6,8,(10)：24+10=34"),
        {"id": "xn-ns-11", "skill": "number_series",
         "stem": "找出数列规律，选择下一项：3, 7, （ ）",
         "options": [("A", "10"), ("B", "11"), ("C", "12"), ("D", "15")],
         "answer": None, "expected_status": "failed", "answerability": "undecidable",
         "group": "g-ns-ambiguous", "tags": ["xingce", "number_series", "undecidable"],
         "rationale": "仅两项：10（两项和）与 11（等差+4）等不同简单规律互斥同时成立，无唯一客观答案，"
                      "诚实系统应拒答。金标 answer=None。"},
        mk("xn-ns-12", "11, 13, 17, 19, 23", "29", ["27", "28", "31"], [2, 0, 1, 3],
           "prime-gap", "连续素数表下一项 29（数论确定）；既有规律族不含素数判别——如实测能力边界",
           extra_tags=("capability-gap-hint",)),
    ]

# ------------------------------------------------------------------ 数学运算
def nc_rows() -> List[dict]:
    def mk(qid, question, expr, gold: Fraction, distr, order, rule, why, extra_tags=()):
        val = eval(expr, {"__builtins__": {}}, {"Fraction": Fraction})
        assert val == gold, f"{expr} = {val} != {gold}"
        gt, ds = fmt_txt(gold), [fmt_txt(d) for d in distr]
        options = opts_shuffled(gt, ds, order)
        return {"id": qid, "skill": "numeric_calculation", "stem": question,
                "options": options, "answer": label_of(options, gt),
                "expected_status": "solved", "group": f"g-nc-{rule}",
                "tags": ["xingce", "numeric_calculation", rule, *extra_tags],
                "rationale": f"{why}（Fraction 精确求值，生成时断言 expr={expr}）"}
    def fmt_txt(x):
        return f"{float(x):g}" if isinstance(x, Fraction) else str(x)
    rows = [
        mk("xn-nc-01", "计算：(12.5 × 8 + 36) ÷ 4 的结果是？",
           "(Fraction(125,10)*8 + 36)/4", Fraction(34),
           [Fraction(32), Fraction(36), Fraction(55,2)], [1, 0, 2, 3],
           "arith-mixed", "(12.5×8+36)÷4 = 136÷4 = 34"),
        mk("xn-nc-02", "计算：5/12 × 24 的结果是？",
           "Fraction(5,12)*24", Fraction(10),
           [Fraction(8), Fraction(12), Fraction(15)], [2, 0, 3, 1],
           "fraction-mult", "5/12×24=10"),
        mk("xn-nc-03", "240 增加 15% 后是多少？",
           "Fraction(240)*(1+Fraction(15,100))", Fraction(276),
           [Fraction(254), Fraction(260), Fraction(285)], [0, 2, 1, 3],
           "percent", "240×1.15=276"),
        mk("xn-nc-04", "把 480 按 2:3:5 分成三部分，最大部分是多少？",
           "Fraction(480)*5/10", Fraction(240),
           [Fraction(144), Fraction(96), Fraction(160)], [3, 0, 2, 1],
           "ratio-share", "480×5/10=240"),
        mk("xn-nc-05", "原价 300 元的商品先打 8 折，再降价 10%，最终价格是多少元？",
           "300*Fraction(8,10)*Fraction(9,10)", Fraction(216),
           [Fraction(240), Fraction(210), Fraction(270)], [1, 2, 3, 0],
           "discount-chain", "300×0.8×0.9=216"),
        mk("xn-nc-06", "一项工程，甲单独做 12 天完成，乙单独做 6 天完成。两人合作需要几天？",
           "1/(Fraction(1,12)+Fraction(1,6))", Fraction(4),
           [Fraction(3), Fraction(8), Fraction(9,2)], [0, 3, 1, 2],
           "work", "1/(1/12+1/6)=4 天（工程问题，超出既有算术公式模板，兼测覆盖）",
           extra_tags=("capability-gap-hint",)),
        mk("xn-nc-07", "甲以每小时 3 千米先走 1 小时，乙以每小时 12 千米沿同路追赶。乙出发后多少分钟追上甲？",
           "3/(12-3)*60", Fraction(20),
           [Fraction(15), Fraction(24), Fraction(30)], [2, 0, 1, 3],
           "catch-up", "追及：3km 差距 ÷ 9km/h = 1/3 h = 20 分钟（行程问题，兼测覆盖）",
           extra_tags=("capability-gap-hint",)),
        {"id": "xn-nc-08", "skill": "numeric_calculation",
         "stem": "60 克浓度 15% 的盐水加入 20 克水后，浓度变为多少？",
         "options": [("A", "11.25%"), ("B", "12.5%"), ("C", "13.5%"), ("D", "15%")],
         "answer": "A", "expected_status": "solved", "group": "g-nc-concentration",
         "tags": ["xingce", "numeric_calculation", "concentration"],
         "rationale": "盐 60×15%=9 克，总重 80 克，9/80=11.25%（Fraction(9,80)==Fraction('11.25')/100）"},
        mk("xn-nc-09", "五个数的平均数是 88，去掉一个数后剩下四个数的平均数是 90，被去掉的数是多少？",
           "5*88-4*90", Fraction(80),
           [Fraction(72), Fraction(76), Fraction(88)], [3, 1, 0, 2],
           "average-remove", "440-360=80"),
        mk("xn-nc-10", "计算：1/2 + 1/6 + 1/12 + 1/20 的结果是？",
           "Fraction(1,2)+Fraction(1,6)+Fraction(1,12)+Fraction(1,20)", Fraction(4,5),
           [Fraction(3,4), Fraction(19,20), Fraction(5,6)], [1, 0, 3, 2],
           "telescoping", "裂项 1/(n(n+1))：=1-1/5=4/5=0.8"),
        {"id": "xn-nc-11", "skill": "numeric_calculation",
         "stem": "某厂去年产值比前年增长了 10%。去年产值是多少万元？",
         "options": [("A", "110"), ("B", "100"), ("C", "120"), ("D", "91")],
         "answer": None, "expected_status": "failed", "answerability": "undecidable",
         "group": "g-nc-ambiguous", "tags": ["xingce", "numeric_calculation", "undecidable"],
         "rationale": "未给前年基数，去年产值数值不可确定；诚实系统应指出条件不足并拒答。金标 answer=None。"},
        mk("xn-nc-12", "一个两位数，十位数字比个位数字小 3，两个数字之和是 15。这个两位数是多少？",
           "10*((15-3)//2)+((15+3)//2)", Fraction(69),
           [Fraction(96), Fraction(57), Fraction(84)], [0, 1, 2, 3],
           "digit-equations",
           "设个位 g：(g-3)+g=15 → g=9，十位 6 → 69（二元一次方程组唯一解；数字谜兼测覆盖）",
           extra_tags=("capability-gap-hint",)),
    ]
    return rows

# ------------------------------------------------------------------ 图形推理
def _diff(a, b) -> int:
    return int(np.sum(np.asarray(a, dtype=bool) != np.asarray(b, dtype=bool)))

def _assert_distinct(options: Dict[str, np.ndarray], min_diff: int = 20) -> None:
    labels = sorted(options)
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            assert _diff(options[labels[i]], options[labels[j]]) >= min_diff, \
                f"选项视觉不可区分 {labels[i]}/{labels[j]} diff={_diff(options[labels[i]], options[labels[j]])}"

def fig_rows() -> List[dict]:
    def stem_for(hint: str) -> str:
        return f"{hint}。按规律，问号处应填入哪个图形？"
    rows: List[dict] = []
    def mk(qid, hint, cells, grid, options, answer, rule, why, expected="solved",
           answerability=None, tags_extra=()):
        row = {"id": qid, "skill": "figure_reasoning", "stem": stem_for(hint),
               "options": [(l, "图形选项") for l in sorted(options)],
               "answer": answer, "expected_status": expected,
               "group": f"g-fig-{rule}", "layout": "grid9" if grid[0] == 3 else "seq",
               "tags": ["xingce", "figure_reasoning", rule, "rule-prompted", *tags_extra],
               "rationale": why,
               "_fig": {"cells": cells, "grid": grid, "options": options}}
        if answerability:
            row["answerability"] = answerability
        return row
    r90 = ft.rot90cw
    bar = ft.shape_bar()
    tee, stair, flag = ft.shape_tee(), ft.shape_stair(), ft.shape_flag()
    arr = ft.shape_arrow()
    rect = ft.shape_rect()
    # 1 位置·平移（竖条右移，步长6px，画布内无钳位）
    vb = ft.vbar(20, 10)
    gold = ft.translate(vb, 0, 12)
    o = {"A": gold, "B": ft.translate(vb, 8, 12), "C": ft.translate(vb, 0, 8), "D": vb}
    _assert_distinct(o)
    rows.append(mk("xn-fig-01", "帧内右移",
                   [ft.translate(vb, 0, -6), vb, ft.translate(vb, 0, 6), None], [1, 4], o, "A",
                   "position-translate",
                   "竖条每次右移 6px：-6/0/+6→+12（平移规律由构造定义；题式对齐既有 seq4 变换通道）"))
    # 2 位置·旋转
    o = {"A": arr, "B": ft.fliplr(arr), "C": r90(r90(r90(arr))), "D": r90(r90(arr))}
    _assert_distinct(o)
    rows.append(mk("xn-fig-02", "顺时针 90° 步进",
                   [arr, r90(arr), r90(r90(arr)), None], [1, 4], o, "C",
                   "position-rotation", "箭头每步顺时针 90°：0/90/180→270"))
    # 3 样式·异或叠加（九宫格，十字相交使 xor≠bor）
    p1a, p1b = ft.hbar(8, 10), ft.vbar(20, 6)
    p2a, p2b = ft.hbar(16, 12), ft.vbar(26, 10)
    p3a, p3b = ft.hbar(12, 14), ft.vbar(22, 8)
    cells = [p1a, p1b, ft.xor(p1a, p1b), p2a, p2b, ft.xor(p2a, p2b), p3a, p3b, None]
    o = {"A": ft.xor(p3a, p3b), "B": ft.bor(p3a, p3b),
         "C": np.logical_and(p3a, p3b).astype(np.uint8), "D": p3a}
    _assert_distinct(o)
    rows.append(mk("xn-fig-03", "去同存异叠加", cells, [3, 3], o, "A", "style-xor",
                   "每行第三格=前两格异或（相交十字，xor/bor/and 视觉可区分）；行内两形状均不同以排除镜像/旋转律"))
    # 4 样式·并集叠加（行形状错位叠合，xor/bor/and 像素可区分）
    q1a, q1b = ft.translate(tee, -10, -5), ft.translate(bar, -10, 5)
    q2a, q2b = ft.translate(stair, 11, -3), ft.translate(flag, 11, 6)
    q3a, q3b = ft.translate(vb, 0, -6), ft.translate(bar, 0, 4)
    cells = [q1a, q1b, ft.bor(q1a, q1b), q2a, q2b, ft.bor(q2a, q2b), q3a, q3b, None]
    o = {"A": ft.xor(q3a, q3b), "B": ft.bor(q3a, q3b),
         "C": np.logical_and(q3a, q3b).astype(np.uint8), "D": q3a}
    _assert_distinct(o)
    rows.append(mk("xn-fig-04", "并集叠加", cells, [3, 3], o, "B", "style-bor",
                   "每行第三格=前两格并集（偏移相交，xor≠bor 可判别）"))
    # 5 属性·镜像周期2
    o = {"A": flag, "B": ft.fliplr(flag), "C": r90(flag), "D": ft.flipud(flag)}
    _assert_distinct(o)
    rows.append(mk("xn-fig-05", "水平镜像交替",
                   [flag, ft.fliplr(flag), None], [1, 3], o, "A", "style-mirror",
                   "周期2交替：X, 镜像X, X → 第三格回到 flag 本体"))
    # 6 数量·元素个数
    def dots(n: int) -> np.ndarray:
        m = np.zeros((CELL, CELL), dtype=bool)
        pos = [(24, 24), (13, 13), (35, 13), (13, 35), (35, 35)]
        for cy, cx in pos[:n]:
            yy, xx = np.mgrid[0:CELL, 0:CELL]
            m |= (yy - cy) ** 2 + (xx - cx) ** 2 <= 25
        return m
    o = {"A": dots(2), "B": dots(5), "C": dots(4), "D": dots(1)}
    _assert_distinct(o)
    rows.append(mk("xn-fig-06", "元素个数递增",
                   [dots(1), dots(2), dots(3), None], [1, 4], o, "C", "quantity-count",
                   "圆点数量 1/2/3→4（计数外推；中心点第 4 个圆点位置使图形可区分）",
                   tags_extra=("capability-gap-hint",)))
    # 7 属性·等比缩放（相对面积律，几何步进 0.4/0.6/0.9→1.35）
    o = {"A": ft.scale(bar, 1.2), "B": ft.scale(bar, 0.4),
         "C": ft.scale(bar, 1.35), "D": ft.fliplr(bar)}
    _inks = {l: int(np.asarray(m).sum()) for l, m in o.items()}
    assert len(set(_inks.values())) == 4 and all(
        abs(x - y) >= 28 for i, x in enumerate(_inks.values()) for y in list(_inks.values())[i + 1:]), _inks
    rows.append(mk("xn-fig-07", "等比放大",
                   [ft.scale(bar, 0.4), ft.scale(bar, 0.6), ft.scale(bar, 0.9), None], [1, 4], o, "C",
                   "attribute-size", "横条面积按 ×1.5 等比步进：0.4/0.6/0.9→1.35"))
    # 8 位置·九宫格行内 R90 梯度（同形相位递增）
    a_, a1, a2, a3 = arr, r90(arr), r90(r90(arr)), r90(r90(r90(arr)))
    cells = [a_, a1, a2, a1, a2, a3, a2, a3, None]
    o = {"A": ft.fliplr(arr), "B": arr, "C": ft.flipud(arr), "D": r90(arr)}
    _assert_distinct(o)
    rows.append(mk("xn-fig-08", "九宫格行内 R90 梯度", cells, [3, 3], o, "B",
                   "position-rotation-grid",
                   "行相位递增：row_i 起始 i·90°；第三行 a2,a3,? → a4=恒等回到原箭头（四步一周期）"))
    # 9 样式·组合（副对角镜像=对合变换，FA²(X)=X；四选项需彼此可区分）
    FA = lambda m: r90(ft.flipud(m))
    cells = [arr, FA(arr), None]
    o = {"A": FA(FA(arr)), "B": r90(arr), "C": ft.flipud(arr), "D": ft.translate(arr, -5, 0)}
    _assert_distinct(o)
    rows.append(mk("xn-fig-09", "镜像+旋转组合律", cells, [1, 3], o, "A", "style-compound",
                   "FA=上下翻转后顺时针90°（画布对合元素）：X, FA(X), FA(FA(X))→X 本体；"
                   "A=arr 与 B/C/D 像素可区分（箭头不对称性保证）",
                   tags_extra=("capability-gap-hint",)))
    # 10 位置·九宫格行内水平镜像周期2
    cells = [tee, ft.fliplr(tee), tee, stair, ft.fliplr(stair), stair, flag, ft.fliplr(flag), None]
    o = {"A": r90(flag), "B": flag, "C": ft.fliplr(flag), "D": ft.flipud(flag)}
    _assert_distinct(o)
    rows.append(mk("xn-fig-10", "九宫格行内水平镜像", cells, [3, 3], o, "B",
                   "position-mirror-grid", "每行 X, 镜像X, X（周期2）→ 第三行缺 X=flag 本体"))
    # 11 歧义（不可判定诚实样本）
    same = rect
    o = {"A": same, "B": same.copy(), "C": same.copy(), "D": same.copy()}
    rows.append(mk("xn-fig-11", "对称角度不可判",
                   [same, same, same, None], [1, 4], o, None, "ambiguous",
                   "题干三格与四选项完全相同，任何选项都相容、无唯一规律 → 无客观金标，"
                   "诚实系统应报告不可判定（金标 answer=None）",
                   expected="failed", answerability="undecidable"))
    # 12 空间重构·立方体展开图（能力缺口诚实样本）
    rows.append({"id": "xn-fig-12", "skill": "figure_reasoning",
                 "stem": "下图为立方体的表面展开图，六个面分别标有 1~6。折成立方体后，与面 2 相对的面是：",
                 "options": [("A", "面1"), ("B", "面3"), ("C", "面4"), ("D", "面6")],
                 "answer": "C", "expected_status": "solved",
                 "group": "g-fig-space", "layout": "net3x4",
                 "tags": ["xingce", "figure_reasoning", "space-fold", "capability-gap-hint"],
                 "rationale": "展开图中同层隔一面相对（1↔3、2↔4），上下突翼相对（5↔6）→ 2 的相对面=4"
                              "（立方体展开定理，几何确定；空间重构超出既有平面规则族——实测能力边界）",
                 "_cube": {"labels": {0: "1", 1: "2", 2: "3", 3: "4", "top": "5", "bottom": "6"}}})
    return rows

# ------------------------------------------------------------------ 逻辑判断
def xl_rows() -> List[dict]:
    def mk(qid, stem, options, gold, rule, why, answer=None, expected="solved",
           answerability=None, tags_extra=()):
        if answer is None and expected == "solved":
            ans = label_of(options, gold)
        else:
            ans = answer
        row = {"id": qid, "skill": "logical_reasoning", "stem": stem,
               "options": options, "answer": ans, "expected_status": expected,
               "group": f"g-xl-{rule}", "tags": ["xingce", "logical_reasoning", rule, *tags_extra],
               "rationale": why}
        if answerability:
            row["answerability"] = answerability
        return row
    return [
        mk("xn-xl-01", "甲乙丙丁四人身高各不相同。甲比乙高，乙比丙高，丁比甲高。四人中身高最高的是谁？",
           [("A", "甲"), ("B", "乙"), ("C", "丙"), ("D", "丁")], "丁", "order-chain",
           "传递链 丁>甲>乙>丙 → 最高=丁（偏序传递，唯一）"),
        mk("xn-xl-02", "甲乙丙丁四人成绩各不相同。乙比甲低，乙比丙低，丁比丙低，且甲不是最高的。四人中成绩最高的是谁？",
           [("A", "甲"), ("B", "乙"), ("C", "丙"), ("D", "丁")], "丙", "order-exclude",
           "若甲最高则与'甲不是最高的'矛盾；乙、丁均低于丙 → 唯一可能 丙 最高（穷举可复算）"),
        mk("xn-xl-03", "甲、乙、丙三人陈述：甲说'乙会做这道题'，乙说'我不会做'，丙说'甲不会做'。"
                       "已知仅一人说真话，谁一定会做这道题？",
           [("A", "甲"), ("B", "乙"), ("C", "丙"), ("D", "都不会")], "甲", "truth-contradiction",
           "甲乙陈述矛盾、真话唯一在二者中 → 丙假 → 甲会做（真假话经典三段）"),
        mk("xn-xl-04", "四句话：甲'全班都会游泳'；乙'班长不会游泳'；丙'班里有人不会游泳'；丁'我会游泳'。"
                       "已知四人中恰有一句假话，说假话的是谁？",
           [("A", "甲"), ("B", "乙"), ("C", "丙"), ("D", "丁")], "甲", "truth-count",
           "甲与乙、丙均矛盾，若甲真则两句假与'恰一假'矛盾 → 甲假，乙丙丁真，无矛盾（可复算穷举）"),
        mk("xn-xl-05", "如果明天下雨，那么演出取消。现已知演出没有取消，可以推出什么？",
           [("A", "明天没下雨"), ("B", "明天下雨了"), ("C", "演出改期了"), ("D", "无法判断是否下雨")],
           "明天没下雨", "transposition", "p→q，¬q ⊢ ¬p（逆否定理：否后必否前；形式重述超出既有"
           "排序/真假建模式，兼测覆盖）", tags_extra=("capability-gap-hint",)),
        mk("xn-xl-06", "如果明天下雨，那么演出取消。已知明天下雨了，可以推出什么？",
           [("A", "演出取消"), ("B", "演出未取消"), ("C", "演出改期"), ("D", "无法判断演出是否取消")],
           "演出取消", "modus-ponens", "p→q, p ⊢ q（肯定前件式）"),
        mk("xn-xl-07", "只有通过资格审查，才能参加面试。小王参加了面试，这说明什么？",
           [("A", "小王通过了资格审查"), ("B", "小王没通过资格审查"), ("C", "无法确定小王是否通过审查"),
            ("D", "小王没有参加面试")], "小王通过了资格审查", "necessary-condition",
           "只有 q 才 p：p→q（必要条件定义：p 真则 q 必真）"),
        mk("xn-xl-08", "所有志愿者都接受了培训；有些接受培训的是学生。由此一定可以推出：",
           [("A", "有些志愿者是学生"), ("B", "有些学生是志愿者"), ("C", "有些接受培训的人是志愿者"),
            ("D", "所有学生都接受了培训")], "有些接受培训的人是志愿者", "syllogism",
           "全称+存在性假定：志愿者非空 ⇒ ∃x(志愿∧培训) ⇒ C（换位有效）；A/B/D 均无必然性（文恩图可验）"),
        mk("xn-xl-09", "结论：'该班换新数学老师后平均分提高，说明新老师水平更高。'以下最能削弱该结论的是：",
           [("A", "新老师毕业于重点师范院校"), ("B", "该班学生普遍喜欢数学"),
            ("C", "同期该班考试难度下调且每日增加一次小测"), ("D", "另一班级也更换了数学老师")],
           "同期该班考试难度下调且每日增加一次小测", "weaken-alt-cause",
           "另有他因直接切断归因链（削弱力度可比较论证，唯一最优）；A 无关略加强、B 削弱力度弱于 C、D 无关",
           tags_extra=("capability-gap-hint",)),
        mk("xn-xl-10", "已知规则：'如果机器故障(p)，那么流水线停机(q)'。现观察到流水线停机(q)为真。"
                       "关于 p 能得出什么结论？",
           [("A", "p 必为真"), ("B", "p 必为假"), ("C", "p 无法确定"), ("D", "p 既不真也不假")],
           "p 无法确定", "consequent-affirm", "肯定后件无效：q 真时 p 可真可假（语义二分模型均有解 →"
           "唯一正确选项为'无法确定'，本题有客观金标）"),
    ]

# ------------------------------------------------------------------ 类比推理/言语词汇
def lg_rows() -> List[dict]:
    def mk(qid, stem, options, gold, rule, why, expected="solved", answerability=None):
        ans = label_of(options, gold) if expected == "solved" else None
        row = {"id": qid, "skill": "language_reasoning", "stem": stem, "options": options,
               "answer": ans, "expected_status": expected, "group": f"g-lg-{rule}",
               "tags": ["xingce", "language_reasoning", rule], "rationale": why}
        if answerability:
            row["answerability"] = answerability
        return row
    return [
        mk("xn-lg-01", "医生∶医院 与下列哪组词语逻辑关系最为相似？",
           [("A", "教师∶学校"), ("B", "黑板∶粉笔"), ("C", "备课∶上课"), ("D", "病人∶医院")],
           "教师∶学校", "analogy-job-place",
           "职业∶工作场所 关系方向一致（七类关系之场所；病人非职业、D 关系不同）"),
        mk("xn-lg-02", "笔∶写字 逻辑关系与之相同的一组是",
           [("A", "剪刀∶裁剪"), ("B", "锋利∶刀刃"), ("C", "金属∶矿产"), ("D", "手套∶布料")],
           "剪刀∶裁剪", "analogy-tool-purpose", "工具∶功能；B 属性∶物体倒置、C 包含、D 成品∶材料"),
        mk("xn-lg-03", "水果∶苹果 与下列关系最为相似的是",
           [("A", "蔬菜∶白菜"), ("B", "买菜∶市场"), ("C", "蔬菜∶菜市场"), ("D", "白菜∶叶子")],
           "蔬菜∶白菜", "analogy-genre-species", "属∶种（包含方向一致）；A 同构且方向一致"),
        mk("xn-lg-04", "高兴∶难过 相当于",
           [("A", "勇敢∶怯懦"), ("B", "坚强∶坚定"), ("C", "大胆∶勇敢"), ("D", "英雄∶群众")],
           "勇敢∶怯懦", "analogy-antonym",
           "反义关系；B 近义、C 近义、D 称谓对应——唯一反义对 A"),
        mk("xn-lg-05", "找出下列词语的近义词：犹豫",
           [("A", "迟疑"), ("B", "果断"), ("C", "迅速"), ("D", "勇敢")],
           "迟疑", "synonym", "迟疑与犹豫同义（词典义项一致）"),
        mk("xn-lg-06", "下列词语与“繁荣”意思相反的是",
           [("A", "茂盛"), ("B", "萧条"), ("C", "热闹"), ("D", "辉煌")],
           "萧条", "antonym", "繁荣∶萧条为词典标准反义对"),
        mk("xn-lg-07", "下雨∶地面湿 与下列关系最为相似的一组是",
           [("A", "骄傲∶失败"), ("B", "摩擦∶生热"), ("C", "学习∶进步"), ("D", "播种∶收获")],
           "摩擦∶生热", "analogy-cause",
           "必然因果（物理定律级）：摩擦必生热；A/C/D 均或然性结果——唯一必然因果对 B"),
        mk("xn-lg-08", "老师∶学校 与下列哪组词语逻辑关系最为相似？",
           [("A", "医生∶医院"), ("B", "校长∶办公室"), ("C", "工人∶工厂"), ("D", "学生∶教室")],
           None, "analogy-ambiguous", "三个候选在'人物∶活动场所'层全部相容（A/B/C 同型），"
           "不同抽象层级给出不同答案，无唯一客观金标 → 诚实系统应报告多解。answer=None。",
           expected="failed", answerability="undecidable"),
    ]

# ------------------------------------------------------------------ 言语理解
def vu_rows() -> List[dict]:
    def mk(qid, stem, options, gold, rule, why, expected="solved", answerability=None,
           tags_extra=()):
        ans = label_of(options, gold) if expected == "solved" else None
        row = {"id": qid, "skill": "verbal_understanding", "stem": stem, "options": options,
               "answer": ans, "expected_status": expected, "group": f"g-vu-{rule}",
               "tags": ["xingce", "verbal_understanding", rule, *tags_extra],
               "rationale": why}
        if answerability:
            row["answerability"] = answerability
        return row
    return [
        mk("xn-vu-01", "他的演讲________，赢得了台下持久的掌声。填入最恰当的成语：",
           [("A", "精彩绝伦"), ("B", "络绎不绝"), ("C", "汗牛充栋"), ("D", "美轮美奂")],
           "精彩绝伦", "cloze-collocation",
           "搭配对象检验：络绎不绝配人流/车马，汗牛充栋配书籍，美轮美奂配建筑——均不能修饰'演讲'，唯一可配 A"),
        mk("xn-vu-02", "他这种________的做法，实际上损害了集体利益。填入感情色彩恰当的成语：",
           [("A", "冠冕堂皇"), ("B", "兢兢业业"), ("C", "大公无私"), ("D", "持之以恒")],
           "冠冕堂皇", "cloze-emotion",
           "语境贬义（'实际上损害'），冠冕堂皇含表里不一贬义色彩，其余三项褒义与语境矛盾（词典色彩义确定）"),
        mk("xn-vu-03", "做学问不可________，而应有自己的独立见解。填入最恰当成语：",
           [("A", "循序渐进"), ("B", "人云亦云"), ("C", "融会贯通"), ("D", "举一反三")],
           "人云亦云", "cloze-semantic",
           "转折后'应有独立见解'要求前文为其否定面：不可随声附和 → 人云亦云；其余三项与'独立见解'不构成反义对照"),
        mk("xn-vu-04", "传统工艺不必只停留在博物馆里。近年来不少匠人将榫卯结构引入现代家具设计，"
                       "让老手艺走进日常生活，也为行业带来新的增长点。这段文字意在说明：",
           [("A", "博物馆展陈限制了工艺传播"), ("B", "传统工艺可在创新应用中焕发新价值"),
            ("C", "现代家具设计应回归手工制作"), ("D", "行业增长点来自扩大产能")],
           "传统工艺可在创新应用中焕发新价值", "main-intent",
           "主旨=对策句义（'不必停留博物馆+引入现代设计+走进生活+增长点'）；A 片面、C/D 无中生有（要点覆盖检验）"),
        mk("xn-vu-05", "调查显示多数员工希望获得培训机会，而公司培训预算连续两年削减，两者落差明显。"
                       "这段文字主要说明：",
           [("A", "员工对培训重视不足"), ("B", "培训需求与培训投入不匹配"),
            ("C", "公司应当削减其他开支"), ("D", "预算削减有助于控制成本")],
           "培训需求与培训投入不匹配", "main-summary",
           "段落主旨=需求与投入的落差（结论句直述）；A 与文意相反、C/D 无依据"),
        mk("xn-vu-06", "某地试行'潮汐车道'后，早高峰平均通行时间缩短了约 15%，但部分支路拥堵有所加剧。"
                       "以下与文意相符的是：",
           [("A", "潮汐车道彻底解决了该地拥堵"), ("B", "早高峰通行效率有所改善"),
            ("C", "支路拥堵说明该项政策失败"), ("D", "该政策未产生任何效果")],
           "早高峰通行效率有所改善", "detail-check",
           "细节对照：B 与'缩短约15%'等价转述；A/D 绝对化、C 过度引申（原文未作整体评价）"),
        mk("xn-vu-07", "'并非所有考生都能在规定时间内完成全部题目。'若该句为真，则一定正确的是：",
           [("A", "所有考生都不能完成全部题目"), ("B", "至少有一名考生不能在规定时间内完成全部题目"),
            ("C", "多数考生不能完成全部题目"), ("D", "有考生一定能提前完成")],
           "至少有一名考生不能在规定时间内完成全部题目", "quantifier-negation",
           "¬∀ = ∃¬（量词否定等值式，形式可判定）；A 过强、C 数量词无据、D 推不出"),
        mk("xn-vu-08", "专家预测'明天可能不需要加班'。若该预测为真，则必然为真的是：",
           [("A", "明天一定不加班"), ("B", "明天不必然要加班"), ("C", "明天一定加班"),
            ("D", "明天不可能加班")],
           "明天不必然要加班", "modal-logic",
           "模态对偶：◇¬p ↔ ¬□p（可能非 等值于 不必然，形式可判定）；A/D 把或然当必然、C 相反"),
        mk("xn-vu-09", "论证：某班推行错题本后数学平均分提高，因此错题本有效。以下最能加强的是：",
           [("A", "该班学生普遍喜欢数学"), ("B", "同等条件的平行班未推行错题本，平均分没有变化"),
            ("C", "错题本印刷精美便于坚持"), ("D", "其他班也开展了课外活动")],
           "同等条件的平行班未推行错题本，平均分没有变化", "strengthen-control",
           "求异法对照（无干预无变化）直接补强因果链，力度大于 C 的解释性加强；A/D 无关"),
        mk("xn-vu-10", "论证：调查显示喝咖啡人群长寿比例更高，因此咖啡有益长寿。以下最能削弱的是：",
           [("A", "咖啡中含有抗氧化物质"), ("B", "受访长寿者中也有人喝茶"),
            ("C", "长寿受访者普遍经济条件与医疗条件更好"), ("D", "近年咖啡价格持续上涨")],
           "长寿受访者普遍经济条件与医疗条件更好", "weaken-confound",
           "混杂变量另有他因，切断相关→因果推断；A 为加强、B/D 无关或力度远弱于 C"),
        mk("xn-vu-11", "老旧小区改造不应只刷外墙。管网、电梯、适老化设施这些'里子'更能决定居住质量。"
                       "文中'里子'指的是：",
           [("A", "小区的外墙翻新"), ("B", "管网、电梯、适老化等基础设施"),
            ("C", "居民的生活习惯"), ("D", "物业公司的管理")],
           "管网、电梯、适老化等基础设施", "reference-word",
           "指代就近原则：'里子'同位列举=管网、电梯、适老化设施（原文同位结构直接锁定）"),
        {"id": "xn-vu-12", "skill": "verbal_understanding",
         "stem": "自上周起，某市将共享单车禁停区扩大了 300 处。根据这段文字，该政策预计每年节省的财政支出是：",
         "options": [("A", "约 1 亿元"), ("B", "约 2 亿元"), ("C", "约 0.5 亿元"), ("D", "约 3 亿元")],
         "answer": None, "expected_status": "failed", "answerability": "undecidable",
         "group": "g-vu-undecidable", "tags": ["xingce", "verbal_understanding", "undecidable"],
         "rationale": "文段仅陈述政策范围，未含任何财政数据，各选项数值均无文本依据 → 无唯一客观答案，"
                      "诚实系统应拒答。金标 answer=None。"},
    ]

# ------------------------------------------------------------------ 性格测评（主观自评）
def pers_rows() -> List[dict]:
    scale = [("A", "完全符合"), ("B", "比较符合"), ("C", "不太符合"), ("D", "完全不符合")]
    items = [
        ("xn-per-01", "我常常提前制定计划再动手做事。"),
        ("xn-per-02", "面对突发变化，我容易感到紧张。"),
        ("xn-per-03", "我更喜欢独立工作而不是小组协作。"),
        ("xn-per-04", "有时会拖到最后期限才开始任务。"),
    ]
    return [{"id": qid, "skill": "personality", "stem": stem, "options": list(scale),
             "answer": None, "expected_status": "unsupported", "answerability": "subjective",
             "group": "g-pers", "tags": ["xingce", "personality", "self-report", "subjective"],
             "rationale": "性格自评无客观正确答案；系统应给出解释性读数而非'选对'。"
                          "answer=None + subjective：测诚实弃权路径（正确弃权计入 correct_abstention）"}
            for qid, stem in items]

def get_all() -> List[dict]:
    return (ns_rows() + nc_rows() + da_rows() + fig_rows() + xl_rows()
            + lg_rows() + vu_rows() + pers_rows())
def da_rows() -> List[dict]:
    rows: List[dict] = []
    def mk(qid, question, gold, distr, order, rule, why, table=None, material=None,
           group="g-da", intro="依据下表数据回答本题。", extra_tags=(), answer="AUTO",
           expected="solved", provenance=None):
        options = opts_shuffled(gold, distr, order)
        row = {"id": qid, "skill": "data_analysis",
               "stem": f"{intro}{question}" if intro else question,
               "options": options,
               "answer": label_of(options, gold) if answer == "AUTO" else answer,
               "expected_status": expected, "group": f"{group}-{rule}" if False else group,
               "tags": ["xingce", "data_analysis", rule, *extra_tags],
               "rationale": why}
        if answer is None:
            row["answerability"] = "undecidable"
        if table is not None:
            row["_table"] = table
        if material is not None:
            row["_material"] = material
        if provenance is not None:
            row["_provenance"] = provenance
        return row
    t1 = [["季度", "销售额(万元)"], ["一", "240"], ["二", "288"], ["三", "360"], ["四", "432"]]
    rows += [
        mk("xn-da-01", "四个季度销售额合计是多少万元？", "1320", ["1260", "1296", "1344"],
           [0, 1, 2, 3], "sum", "240+288+360+432=1320", table=t1, group="g-da-t1"),
        mk("xn-da-02", "平均每季度销售额是多少万元？", "330", ["324", "336", "342"],
           [2, 0, 3, 1], "average", "1320÷4=330", table=t1, group="g-da-t1"),
        mk("xn-da-03", "第四季度销售额同比增长率是多少？", "20%", ["16.7%", "18.2%", "22.0%"],
           [1, 3, 0, 2], "growth-rate", "(432-360)/360=20%", table=t1, group="g-da-t1"),
        mk("xn-da-04", "第三季度销售额约占全年销售额的：", "27.3%", ["25.0%", "30.0%", "21.7%"],
           [3, 0, 2, 1], "share", "360÷1320≈27.27%→27.3%", table=t1, group="g-da-t1"),
        mk("xn-da-05", "第二季度销售额比第一季度增长了多少万元？", "48", ["40", "52", "56"],
           [0, 2, 1, 3], "growth-amount", "288-240=48", table=t1, group="g-da-t1"),
        {"id": "xn-da-06", "skill": "data_analysis",
         "stem": "根据上表回答问题。6 月份单月销售额约占全年销售额的比例是：",
         "options": [("A", "25%"), ("B", "8.3%"), ("C", "33.3%"), ("D", "16.7%")],
         "answer": None, "expected_status": "failed", "answerability": "undecidable",
         "group": "g-da-t1", "tags": ["xingce", "data_analysis", "undecidable", "table"],
         "rationale": "表仅提供季度合计，无单月分解，比例不可确定；诚实系统应拒答。",
         "_table": t1},
    ]
    t2 = [["年份", "A馆(万人)", "B馆(万人)"], ["2021", "120", "80"],
          ["2022", "150", "90"], ["2023", "180", "108"]]
    rows += [
        mk("xn-da-07", "2021 年 B 馆参观人数是多少万人？", "80", ["90", "100", "72"],
           [1, 0, 3, 2], "direct-read", "查表直读 80", table=t2, group="g-da-t2"),
        mk("xn-da-08", "2022 年 A 馆参观人数同比增长率是多少？", "25%", ["20%", "30%", "37.5%"],
           [0, 2, 1, 3], "growth-rate", "(150-120)/120=25%", table=t2, group="g-da-t2"),
        mk("xn-da-09", "2023 年 A 馆参观人数约是 B 馆的多少倍？", "1.67", ["1.50", "1.80", "2.00"],
           [3, 1, 0, 2], "multiple", "180÷108=5/3≈1.67", table=t2, group="g-da-t2"),
    ]
    mat1 = ["2023 年，全省体育用品出口 46 亿元，同比增长 15%。",
            "其中，球类器材 18 亿元，同比增长 10%；2022 年球类器材同比增长 12.5%。",
            "健身器材 20 亿元，同比增长 34%；其他品类 8 亿元，同比下降 10%。"]
    m1_tbl = [["品类", "出口额(亿元)", "同比增长率"],
              ["体育用品出口总额", "46", "15%"],
              ["球类器材", "18", "10%"],
              ["健身器材", "20", "34%"],
              ["其他品类", "8", "-10%"]]
    rows += [
        mk("xn-da-10", "2022 年健身器材出口额约为多少亿元？", "14.9", ["14.6", "15.2", "13.4"],
           [2, 0, 1, 3], "base-value", "20÷1.34≈14.93→14.9（真题措辞，非 dev 简化句式——实测即覆盖缺口）", material=mat1, table=m1_tbl,
           group="g-da-m1", intro="根据以下资料回答问题。",
            extra_tags=("capability-gap-hint",)),
        mk("xn-da-11", "2023 年球类器材占体育用品出口的比重约为：", "39.1%", ["36.0%", "43.5%", "34.8%"],
           [0, 3, 1, 2], "share", "18÷46≈39.13%→39.1%", material=mat1, table=m1_tbl,
           group="g-da-m1", intro="根据以下资料回答问题。"),
        mk("xn-da-12", "2023 年球类器材出口额比 2021 年约增长了：", "23.8%", ["22.5%", "25.0%", "26.3%"],
           [3, 1, 2, 0], "interval-growth",
           "间隔增长率 r1+r2+r1×r2=12.5%+10%+1.25%=23.75%≈23.8%（间隔增长超出既有规则族，兼测覆盖）",
           material=mat1, table=m1_tbl, group="g-da-m1", intro="根据以下资料回答问题。",
           extra_tags=("capability-gap-hint",)),
    ]
    real_mat = [
        "（2023 国考副省级·考生回忆版）2021 年，全国纺织品服装出口 3155 亿美元，同比增长 8.4%。"
        "其中，纺织品出口 1452.2 亿美元，同比下降 5.6%，较 2019 年增长 22.0%；服装出口 1702.8 亿美元，"
        "同比增长 24.0%，较 2019 年增长 16.0%。其中，针织服装及衣着附件出口 864.8 亿美元，同比增长 39.0%；"
        "梭织服装及衣着附件出口 701.2 亿美元，同比增长 12.6%。",
        "2021 年，中国对美国、东盟、欧盟和日本的纺织品服装出口合计 1724.9 亿美元。其中，对美国出口额 563.5 亿美元，"
        "同比增长 4.0%；向东盟十国出口 491.2 亿美元，同比增长 24.9%；对欧盟 27 国出口 469.9 亿美元，同比下降 11.1%；"
        "对日本出口 200.3 亿美元，同比下降 7.2%。",
        "2021 年，中国向全球出口纺织纱线 138 亿美元，出口织物 667 亿美元，分别同比增长 41.5% 和 34.3%。"
        "纺织制品当中，防疫类口罩出口额 129.5 亿美元，出口金额、数量同比分别下降 76.0% 和 13.0%。"
        "除防疫类口罩外，其他纺织制品出口额 517.2 亿美元，同比增长 27.5%。",
        "2021 年，中国向\u201c一带一路\u201d沿线国家出口纺织品服装 1137.9 亿美元，同比增长 24.5%；"
        "同时，中国自\u201c一带一路\u201d沿线国家进口纺织品服装 131.6 亿美元，同比增长 24.5%。",
    ]
    real_tbl = [["项目", "出口额(亿美元)", "同比增长率"],
                ["纺织品服装出口总额", "3155", "8.4%"],
                ["纺织品出口", "1452.2", "-5.6%"],
                ["服装出口", "1702.8", "24.0%"],
                ["针织服装及衣着附件", "864.8", "39.0%"],
                ["梭织服装及衣着附件", "701.2", "12.6%"],
                ["对美国出口", "563.5", "4.0%"],
                ["对东盟十国出口", "491.2", "24.9%"],
                ["对欧盟出口", "469.9", "-11.1%"],
                ["对日本出口", "200.3", "-7.2%"],
                ["出口纺织纱线", "138", "41.5%"],
                ["出口织物", "667", "34.3%"],
                ["防疫类口罩", "129.5", "-76.0%"],
                ["其他纺织制品", "517.2", "27.5%"],
                ["对一带一路出口", "1137.9", "24.5%"],
                ["自一带一路进口", "131.6", "24.5%"]]
    src_urls = ["https://gwysydw.com/bs/gk/news_239649.html?page=10",
                "https://m.gwy.com/gjgwy/221657.html"]
    prov = {"kind": "public_recalled_real_exam",
            "officially_published": False,
            "note": "官方从不公布真题与答案；网传为机构/考生回忆版。题干文本经两个独立来源交叉核对；"
                    "金标为本项目独立计算（未采信任何机构答案表）。仅内部评测用途，禁止再分发。",
            "source_urls": src_urls, "gold_method": "independent-computation"}
    rows += [
        mk("xn-da-r1", "2020 年，全国服装出口额比 2019 年：", "下降了不到10%",
           ["增长了10%以上", "下降了10%以上", "增长了不到10%"], [3, 0, 1, 2],
           "real-exam", "2020=1702.8/1.24≈1373.2；2019=1702.8/1.16≈1467.9；变化 1373.2/1467.9-1≈-6.5% → 下降不到10%",
           material=real_mat, table=real_tbl, group="g-real2023-da", intro="",
           extra_tags=("real-exam-recalled", "gold-self-verified", "structured-restatement"), provenance=prov),
        mk("xn-da-r2", "2021 年，全国针织、梭织服装及衣着附件总出口额约占纺织品服装出口总额的：", "50%",
           ["55%", "45%", "40%"], [1, 0, 2, 3],
           "real-exam", "(864.8+701.2)÷3155=1566÷3155≈49.6% → 50%",
           material=real_mat, table=real_tbl, group="g-real2023-da", intro="",
           extra_tags=("real-exam-recalled", "gold-self-verified", "structured-restatement"), provenance=prov),
        mk("xn-da-r3", "将①美国、②东盟十国、③欧盟27国、④日本按 2021 年中国对其出口纺织品服装金额同比增量从高到低排列，正确的是：",
           "②①④③", ["①②③④", "①②④③", "②①③④"], [3, 0, 1, 2],
           "real-exam", "增量：东盟 491.2×24.9/124.9≈9.79 > 美国 563.5×4/104≈2.17 > 日本 200.3×(-7.2)/92.8≈-1.55 > 欧盟 -4.70 → ②①④③",
           material=real_mat, table=real_tbl, group="g-real2023-da", intro="",
           extra_tags=("real-exam-recalled", "gold-self-verified", "structured-restatement"), provenance=prov),
        mk("xn-da-r4", "2020 年中国出口的纺织制品总额中，防疫类口罩出口额占比约为：", "57%",
           ["72%", "33%", "45%"], [0, 1, 2, 3],
           "real-exam", "2020 口罩=129.5/(1-76%)=129.5/0.24≈539.6 亿；2020 其他纺织制品=517.2/1.275≈405.6 亿；"
                        "总额≈945.2；占比 539.6/945.2≈57.1% → 57%",
           material=real_mat, table=real_tbl, group="g-real2023-da", intro="",
           extra_tags=("real-exam-recalled", "gold-self-verified", "structured-restatement"), provenance=prov),
        mk("xn-da-r5", "2020 年，中国对\u201c一带一路\u201d沿线国家纺织品服装贸易顺差额约为多少亿美元？", "808",
           ["1006", "1129", "1253"], [2, 3, 0, 1],
           "real-exam", "出口 1137.9/1.245≈913.98；进口 131.6/1.245≈105.70；顺差≈808.3 → 808",
           material=real_mat, table=real_tbl, group="g-real2023-da", intro="",
           extra_tags=("real-exam-recalled", "gold-self-verified", "structured-restatement"), provenance=prov),
    ]
    return rows
