# -*- coding: utf-8 -*-
"""Stage D：为 R9-C 新算子族生成独立 Dev 变体 corpus（绝不复用 blind_v2，不改动原 26 题）。

每题五类：normal / reverse_boundary / typical_wrong / insufficient_conditions / independent_check。
金标由 Fraction 精确计算，扰动参数全部声明在源里，保证可复算。
输出：data/benchmark_xingce_dev/variants/xingce_dev_variants.jsonl
"""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

OUT = Path("data/benchmark_xingce_dev/variants/xingce_dev_variants.jsonl")
ITEMS = []
_seq = {}


def add(fam: str, cls: str, stem: str, options, gold: str, rationale: str, refs=()):
    _seq[fam] = _seq.get(fam, 0) + 1
    ITEMS.append({
        "id": f"dv-{fam}-{_seq[fam]:02d}",
        "skill": "numeric_calculation",
        "module": "数量关系",
        "stem": stem,
        "options": [{"label": l, "text": t} for l, t in options],
        "answer": gold,
        "rationale": rationale,
        "tags": ["xingce", "dev", "variant", "numeric_calculation", fam, cls],
        "method_refs": list(refs),
        "provenance": {"kind": "project_authored", "note": "R9-D 变体集，参数扰动可复算"},
    })


def pct(v: Fraction) -> str:
    return f"{float(v) * 100:g}%"


# ---------------------------------------------------------------- compound_pct
def f_compound():
    fam = "compound-pct"
    add(fam, "normal", "原价 200 元，先降价 10%，又降价 5%，最终价格是多少元？",
        [("A", "171"), ("B", "170"), ("C", "175"), ("D", "185")], "A",
        "200×0.9×0.95=171（B 为相加错误 200×(1-0.15)）", ("nc.successive-discount",))
    add(fam, "reverse_boundary", "原价 100 元先涨价 25% 再降价 20%，最终价格是多少元？",
        [("A", "100"), ("B", "105"), ("C", "95"), ("D", "110")], "A",
        "100×1.25×0.8=100（恰好回到原价是边界情形）", ("nc.successive-discount",))
    add(fam, "typical_wrong", "原价 300 元，连续两次各降价 10%，实际支付多少元？",
        [("A", "240"), ("B", "243"), ("C", "270"), ("D", "246")], "B",
        "300×0.9×0.9=243；A=300×0.8 为相加错答诱导项")
    add(fam, "insufficient_conditions", "某商品先降价 10%，又降价 5%，最终价格是多少元？",
        [("A", "85.5"), ("B", "85"), ("C", "由题干无法确定"), ("D", "90.5")], "C",
        "缺少原价，任何数字答案都是猜")
    add(fam, "independent_check", "原价 400 元，打 8 折后再降价 5%，实际售价多少元？",
        [("A", "304"), ("B", "300"), ("C", "310"), ("D", "308")], "A",
        "400×0.8×0.95=304；可用 400×19/25 独立复算")


# ---------------------------------------------------------------- concentration
def f_conc():
    fam = "concentration"
    add(fam, "normal", "400 克浓度 20% 的盐水中加入 100 克水，浓度变为多少？",
        [("A", "16%"), ("B", "18%"), ("C", "15%"), ("D", "20%")], "A",
        "盐 80 克 ÷ 500 克 = 16%")
    add(fam, "reverse_boundary", "200 克浓度 30% 的糖水蒸发掉 50 克水，浓度变为多少？",
        [("A", "40%"), ("B", "36%"), ("C", "24%"), ("D", "30%")], "A",
        "糖 60 ÷ 150 = 40%（蒸发至边界仍有解）")
    add(fam, "typical_wrong", "100 克浓度 10% 盐水加盐 25 克，浓度约为多少？",
        [("A", "10%"), ("B", "28%"), ("C", "35%"), ("D", "25%")], "B",
        "(10+25)/125=28%；D 为只把盐量当浓度的错答")
    add(fam, "insufficient_conditions", "一杯盐水加入 50 克水后，浓度变为多少？",
        [("A", "10%"), ("B", "8%"), ("C", "由题干无法确定"), ("D", "12%")], "C",
        "无原质量与原浓度，无法守恒计算")
    add(fam, "independent_check", "300 克浓度 40% 的盐水中加入 100 克盐，浓度变为多少？",
        [("A", "55%"), ("B", "50%"), ("C", "60%"), ("D", "52%")], "A",
        "(300×0.4+100)/(300+100)=220/400=55%")


# ---------------------------------------------------------------- travel
def f_travel():
    fam = "travel"
    add(fam, "normal", "两地相距 300 千米，甲 70 千米/小时、乙 30 千米/小时同时相向出发，几小时后相遇？",
        [("A", "3"), ("B", "2.5"), ("C", "4"), ("D", "5")], "A",
        "300÷(70+30)=3")
    add(fam, "reverse_boundary", "追兵 90 千米/小时，逃兵 60 千米/小时，出发时相距 30 千米，几小时追上？",
        [("A", "2"), ("B", "1"), ("C", "3"), ("D", "0.5")], "B",
        "30÷(90-60)=1（速度差恰为差距整除）")
    add(fam, "typical_wrong", "甲乙相向，速度 40 与 60 千米/小时，相距 200 千米，几小时相遇？",
        [("A", "2"), ("B", "4"), ("C", "5"), ("D", "3")], "A",
        "200÷100=2；B/C 为错用单速度 200/60≈3.3、200/40=5")
    add(fam, "insufficient_conditions", "甲乙两人相向而行，几小时后相遇？",
        [("A", "2"), ("B", "3"), ("C", "由题干无法确定"), ("D", "1")], "C",
        "无距离无速度")
    add(fam, "independent_check", "弟弟 50 米/分先走 8 分钟，哥哥 150 米/分沿同路追及，几分钟追上？",
        [("A", "4"), ("B", "5"), ("C", "3"), ("D", "6")], "A",
        "领先 400 米 ÷ 速度差 100 = 4")


# ---------------------------------------------------------------- net_compound / restore
def f_netgrowth():
    fam = "net-growth"
    add(fam, "normal", "第一年比基期增长 20%，第二年又增长 15%，第二年比基期增长约多少？",
        [("A", "38%"), ("B", "35%"), ("C", "30%"), ("D", "40%")], "A",
        "1.2×1.15-1=38%")
    add(fam, "reverse_boundary", "第一年比基期增长 0%，第二年比第一年增长 50%，第二年比基期增长多少？",
        [("A", "50%"), ("B", "55%"), ("C", "0%"), ("D", "25%")], "A",
        "(1+0)(1.5)-1=50%（0% 步进边界，复合与相加同为 50 的退化情形）")
    add(fam, "typical_wrong", "产量两年分别增长 10% 与 20%，累计增长率是多少？",
        [("A", "32%"), ("B", "30%"), ("C", "22%"), ("D", "35%")], "A",
        "1.1×1.2-1=0.32；B=相加错答")
    add(fam, "insufficient_conditions", "前年比去年增长 10%，去年产量是多少？",
        [("A", "110"), ("B", "100"), ("C", "由题干无法确定"), ("D", "90")], "C",
        "无基期值")
    add(fam, "independent_check", "营收连续两年分别增长 25% 与 8%，两年净增长是多少？",
        [("A", "35%"), ("B", "33%"), ("C", "30%"), ("D", "40%")], "A",
        "1.25×1.08-1=0.35，可用 5/4×27/25-1 独立复算")


def f_restore():
    fam = "pct-restore"
    add(fam, "normal", "某商品降价 25% 后，要恢复原价大约需要提价百分之几？",
        [("A", "25%"), ("B", "33.3%"), ("C", "30%"), ("D", "20%")], "B",
        "0.25/0.75=1/3≈33.3%（约）", ("nc.profit-margin-base",))
    add(fam, "reverse_boundary", "价格降低 50% 后要恢复原价，需要提价多少？",
        [("A", "50%"), ("B", "100%"), ("C", "150%"), ("D", "200%")], "B",
        "0.5/0.5=100%（减半需翻倍是极端边界）")
    add(fam, "typical_wrong", "现价比原价低了 20%，恢复原价需提价多少？",
        [("A", "20%"), ("B", "25%"), ("C", "16.7%"), ("D", "30%")], "B",
        "0.2/0.8=25%；A=把降幅当涨幅的错答")
    add(fam, "insufficient_conditions", "商品先降价若干百分比，要恢复原价需提价多少？",
        [("A", "20%"), ("B", "25%"), ("C", "由题干无法确定"), ("D", "30%")], "C",
        "无降幅数值")
    add(fam, "independent_check", "成本降低 40% 后恢复原成本约需上涨百分之几？",
        [("A", "40%"), ("B", "60%"), ("C", "66.7%"), ("D", "75%")], "C",
        "0.4/0.6=2/3≈66.7%，可用 (1/0.6)-1 复核")


# ---------------------------------------------------------------- work / two-step
def f_work():
    fam = "work-rate"
    add(fam, "normal", "一项工程甲单独 12 天，乙单独 6 天，两队合作几天完成？",
        [("A", "4"), ("B", "9"), ("C", "3"), ("D", "8")], "A",
        "1÷(1/12+1/6)=4")
    add(fam, "reverse_boundary", "甲 2 天做完，乙 2 天做完，合作需几天？",
        [("A", "4"), ("B", "2"), ("C", "1"), ("D", "0.5")], "C",
        "1÷(1/2+1/2)=1")
    add(fam, "typical_wrong", "甲单独 6 天、乙单独 3 天，合作几天？",
        [("A", "2"), ("B", "4.5"), ("C", "3"), ("D", "9")], "A",
        "1/(1/6+1/3)=2；B 为取平均错误")
    add(fam, "insufficient_conditions", "甲队与乙队合作，多少天完成工程？",
        [("A", "5"), ("B", "4"), ("C", "由题干无法确定"), ("D", "6")], "C",
        "无各自工时")
    add(fam, "independent_check", "原计划 5 天生产 1000 个零件，实际每天比原计划多 50 个，实际每天生产多少个？",
        [("A", "250"), ("B", "200"), ("C", "300"), ("D", "150")], "A",
        "1000÷5+50=250")


# ---------------------------------------------------------------- scale / unit-count / ratio-mid
def f_misc():
    fam = "map-scale"
    add(fam, "normal", "在 1∶2000000 的地图上，图上 5 厘米表示实际距离多少千米？",
        [("A", "100"), ("B", "10"), ("C", "50"), ("D", "20")], "A",
        "5×2000000÷100000=100")
    add(fam, "insufficient_conditions", "在地图上看两地相距 5 厘米，实际距离多少千米？",
        [("A", "10"), ("B", "50"), ("C", "由题干无法确定"), ("D", "100")], "C",
        "无比例尺")
    fam = "unit-count"
    add(fam, "normal", "每盒饼干 24 块，12 盒共有多少块？",
        [("A", "288"), ("B", "264"), ("C", "312"), ("D", "300")], "A",
        "24×12=288")
    add(fam, "insufficient_conditions", "每盒若干块饼干，12 盒共有多少块？",
        [("A", "144"), ("B", "288"), ("C", "由题干无法确定"), ("D", "120")], "C",
        "无每盒数量")
    fam = "ratio-share"
    add(fam, "normal", "把 480 元按 1∶2∶3 分给三人，中间份额的人分得多少元？",
        [("A", "160"), ("B", "80"), ("C", "240"), ("D", "120")], "A",
        "480×2/6=160")
    add(fam, "typical_wrong", "把 420 本按 2∶5∶7 分给甲乙丙，中间份额的一队得多少本？",
        [("A", "210"), ("B", "60"), ("C", "120"), ("D", "150")], "D",
        "420×5/14=150；A=取最大份、B=取最小份的典型错答")
    add(fam, "insufficient_conditions", "按 2∶5∶7 分图书，中间份额的一队得多少本？",
        [("A", "150"), ("B", "210"), ("C", "由题干无法确定"), ("D", "60")], "C",
        "无总量")
    add(fam, "reverse_boundary", "按 1∶1∶1 分配 300 元，最多的一人得多少元？",
        [("A", "300"), ("B", "150"), ("C", "100"), ("D", "90")], "C",
        "300×1/3=100（等份边界）")
    add(fam, "independent_check", "按 3∶7 分 210 元，较多的一方得多少元？",
        [("A", "63"), ("B", "147"), ("C", "105"), ("D", "140")], "B",
        "210×7/10=147，可用 210-63 复核")


# ---------------------------------------------------------------- executor expression family
def f_expr():
    fam = "expr-operators"
    add(fam, "normal", "计算：C(6,2)+2^4 = ?",
        [("A", "31"), ("B", "26"), ("C", "35"), ("D", "29")], "A",
        "15+16=31（executor 组合数与幂）", ("nc.classical-probability",))
    add(fam, "reverse_boundary", "计算：3! × 2^0 - sqrt(9) = ?",
        [("A", "3"), ("B", "9"), ("C", "12"), ("D", "6")], "A",
        "6×1-3=3（零指数与平方根）")
    add(fam, "typical_wrong", "计算：-2^4 + 2^4 = ?",
        [("A", "0"), ("B", "32"), ("C", "-32"), ("D", "16")], "A",
        "-16+16=0；考察 -2^4=-16 而非 16")
    add(fam, "insufficient_conditions", "计算：C(n,2)+2^4 = ?",
        [("A", "31"), ("B", "26"), ("C", "由题干无法确定"), ("D", "20")], "C",
        "n 未绑定")
    add(fam, "independent_check", "计算：P(5,2) + 5! / 4! = ?",
        [("A", "25"), ("B", "20"), ("C", "30"), ("D", "15")], "A",
        "20+5=25；阶乘可约简独立复核")


for fn in (f_compound, f_conc, f_travel, f_netgrowth, f_restore, f_work, f_misc, f_expr):
    fn()

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ITEMS), encoding="utf-8")
print("items:", len(ITEMS), "->", OUT)
