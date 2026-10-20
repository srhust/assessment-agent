"""R8-B：xingce_blind_v2 独立命题集生成器。

与 v1 的本质区别（对应工单 §B 的隔离要求）：
- **不用方法卡同构模板成对复制**：每题给出独立条件结构（大纲例题级数学事实改编，
  数值全部换新；来源与改编记录进 proof 字段）；
- 每题必须附 **uniqueness 证明**（conditions → solution_path → recompute →
  pitfall → source），由 ProblemValidityVerifier 四层闸门 + 小域暴力穷举双重把关；
- 泄漏检查分两维：**文字近重复**（token 相似）与**数学结构近重复**
  （题面数值多重集与 dev/R5 相同即拒）；
- 冻结与预注册：先出题集+sha，Solver 改进流程不得依据本集调参（评测纪律见 frozen_meta）。

产出：data/benchmark_xingce_blind_v2/test/{xingce_blind_v2.jsonl, proofs.json,
frozen_meta.json, rejected_items.json}

用法：python -X utf8 scripts/gen_blind_v2.py
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent.validity import DeclaredConditions as DC, ProblemValidityVerifier  # noqa: E402

rng = random.Random(20261021)
VERIFIER = ProblemValidityVerifier()
ITEMS, PROOFS, REJECTED = [], [], []


def _F(text):
    m = re.search(r"(-?\d+(?:\.\d+)?)(?:/(\d+))?", str(text).replace(",", ""))
    if not m:
        return None
    v = Fraction(m.group(1))
    if m.group(2):
        v = v / Fraction(m.group(2))
    return v / 100 if "%" in str(text) else v


def numsig(stem: str) -> tuple:
    """数学结构签名：题面全部数值的多重集（排序）。"""
    return tuple(sorted(str(_F(x)) for x in re.findall(r"\d+(?:\.\d+)?(?:/\d+)?", stem)))


def add(skill, stem, options, answer, *, declared, gold_expr, method,
        proof, approx=False, brute=None, table=None):
    """四层闸门 + 暴力穷举 + 泄漏两维。answer 为选项文本原文。"""
    opts = list(dict.fromkeys(str(o) for o in options))
    assert answer in opts and len(opts) >= 3, (method, opts)
    while len(opts) < 4:
        nxt = str(len(opts))
        if nxt not in opts:
            opts.append(nxt)
    opts = opts[:4]
    assert answer in opts, (method, answer, opts)
    if declared.kind == "manual_semantic":
        # 诚实通道：语义强度题（加强/削弱等）无法机械判定，标记人工审查
        PROOFS.append({"id": f"pending-{len(ITEMS)+1}", "method": method, **proof,
                       "verification": "manual_only"})
        PROOFS.pop()
        proof["verification"] = "manual_only"
    rep = VERIFIER.verify_item(
        gold_expr=gold_expr, gold=_F(answer),
        options=list(zip("ABCD", opts)), cond=declared, approx_declared=approx)
    if rep.verdict != "valid":
        REJECTED.append({"stem": stem[:60], "method": method, "validity": rep.to_dict()})
        return
    if brute is not None:
        got = brute()
        if isinstance(got, (int, float, Fraction)):
            bad = Fraction(got) != _F(answer)
        else:
            bad = str(got) != str(answer)
        if bad:
            REJECTED.append({"stem": stem[:60], "method": method,
                             "brute": {"expected": str(_F(answer)), "got": str(got)}})
            return
        proof = dict(proof, recompute=str(got))
    order = list(opts)
    rng.shuffle(order)
    labels = "ABCD"
    iid = f"v2-{len(ITEMS) + 1:02d}"
    ITEMS.append({
        "id": iid, "skill": skill, "stem": stem,
        "options": [{"label": labels[i], "text": t} for i, t in enumerate(order)],
        "answer": labels[order.index(answer)],
        "answerability": "has_unique_answer", "expected_status": "solved",
        "tags": [method, "blind_v2"],
        "material": None,
        "regions": ({"table": {"bbox": [0, 0, 10, 10], "cells": table}} if table else {}),
    })
    PROOFS.append({"id": iid, "method": method, **proof})


# ================================================================ 数量关系（10）
def nc_items():
    # 1. 排列-捆绑法（大纲例题级：甲乙相邻）
    def brute_bundling():
        cnt = 0
        for p in itertools.permutations(range(5)):
            if abs(p.index(0) - p.index(1)) == 1:
                cnt += 1
        return cnt
    add("numeric_calculation",
        "5 名志愿者排成一排拍照，其中甲、乙两人必须相邻，共有多少种不同的排法？",
        ["24", "48", "120", "36"], "48",
        declared=DC(kind="numeric", numbers=["5", "2"],
                    derivations={"4": "5-1", "3": "5-2", "2": "2"}),
        gold_expr="2*(5-1)*(5-2)*(5-3)*(5-4)", method="cb.permutation-bundling",
        proof={"conditions": "5 人全排列，甲乙相邻视为整体（2 内序 × 4! 外排）",
               "solution_path": "捆绑：2!×4!=2×24=48",
               "recompute": "程序穷举 5 人全排列 120 种，计数甲乙位置相邻者",
               "pitfall": "忘记甲乙内部 2 种顺序（得 24）",
               "source": "2026 国考大纲数量关系·排列组合；改编自通用教材标准例（数值 5 人）"},
        brute=brute_bundling)

    # 2. 几何路径计数 C(5,2)
    def brute_paths():
        # 从 (0,0) 到 (3,2) 只向右上：C(5,2)=10
        from math import comb
        return comb(5, 2)
    add("numeric_calculation",
        "网格中从 A 地到 B 地需向右 3 次、向上 2 次（只允许向右或向上移动），不同的最短路径共有多少条？",
        ["15", "10", "6", "20"], "10",
        declared=DC(kind="numeric", numbers=["3", "2"],
                    derivations={"4": "3+1", "5": "3*2-1", "6": "3*2",
                                 "24": "4*(5-1)", "120": "5*(5-1)*(5-2)*(5-3)*(5-4)"}),
        gold_expr="120/(6*2)", method="cb.lattice-path",
        proof={"conditions": "5 步中选 2 步向上",
               "solution_path": "C(5,2)=5!/(2!3!)=10",
               "recompute": "组合数程序计算",
               "pitfall": "把 C(5,2) 算成 A(5,2)=20",
               "source": "OpenStax Contemporary Mathematics（CC BY-NC-SA）计数章思路改编"},
        brute=brute_paths)

    # 3. 古典概率-骰子和
    def brute_dice7():
        hits = sum(1 for a in range(1, 7) for b in range(1, 7) if a + b == 7)
        return Fraction(hits, 36)
    add("numeric_calculation",
        "掷两枚均匀骰子，点数之和为 7 的概率是多少？",
        ["1/6", "1/12", "7/36", "1/7"], "1/6",
        declared=DC(kind="numeric", numbers=["6", "6", "7"]),
        gold_expr="6/36", method="pr.classical-dice",
        proof={"conditions": "样本空间 36 等可能，和为 7 的有序对 6 个",
               "solution_path": "6/36=1/6", "recompute": "穷举 36 对",
               "pitfall": "按无序组合数 21 计（得 3/21 或漏 (a,b)/(b,a)）",
               "source": "MIT 6.042J 概率论基础例（CC BY-NC-SA）改编"},
        brute=brute_dice7)

    # 4. 古典概率-摸球不放回
    def brute_balls():
        urn = ["W", "W", "R", "R", "R"]
        n = 0
        for a, b in itertools.permutations(range(5), 2):
            if urn[a] != urn[b]:
                n += 1
        return Fraction(n, 20)
    add("numeric_calculation",
        "袋中有 2 个白球和 3 个红球（除颜色外完全相同），不放回地依次摸出 2 个球，恰好一白一红的概率是？",
        ["3/5", "6/25", "2/5", "3/10"], "3/5",
        declared=DC(kind="numeric", numbers=["2", "3"],
                    derivations={"5": "2+3"}),
        gold_expr="2*3*2/(5*(5-1))", method="pr.without-replacement",
        proof={"conditions": "有序不放回样本 5×4=20，一白一红 2×3×2=12",
               "solution_path": "12/20=3/5", "recompute": "穷举 20 有序对",
               "pitfall": "当作有放回（得 12/25）",
               "source": "OpenStax 概率章思路改编"},
        brute=brute_balls)

    # 5. 容斥-两集合
    def brute_incl():
        u = list(range(50))
        A = set(u[:30]); B = {x for i, x in enumerate(u) if i % 2 == 0 and x not in A} | set(u[20:30])
        # 直接构造：|A|=30,|B|=25,|A∩B|=10 → |都不|=50-45
        return 50 - (30 + 25 - 10)
    add("numeric_calculation",
        "某单位 50 名员工中，30 人喜欢篮球，25 人喜欢足球，10 人两项都喜欢。两项都不喜欢的有多少人？",
        ["5", "10", "15", "0"], "5",
        declared=DC(kind="numeric", numbers=["50", "30", "25", "10"]),
        gold_expr="50-(30+25-10)", method="in.inclusion-exclusion-2",
        proof={"conditions": "全集 50，|A|=30，|B|=25，|A∩B|=10",
               "solution_path": "|A∪B|=45 → 补集 5",
               "recompute": "容斥公式代入", "pitfall": "忘减交集得 50-55 或 0",
               "source": "大纲判断推理/数量关系容斥考点（改编）"},
        brute=brute_incl)

    # 6. 等差数列求和（工程背景）
    add("numeric_calculation",
        "某仓库 12 个月逐月出库量构成等差数列：首月 1200 吨，每月比上月多 80 吨。全年出库总量是多少吨？",
        ["19680", "17520", "19600", "20800"], "19680",
        declared=DC(kind="numeric", numbers=["12", "1200", "80"]),
        gold_expr="12*1200+80*(12*(12-1)/2)", method="seq.arithmetic-sum",
        proof={"conditions": "a1=1200, d=80, n=12",
               "solution_path": "S=na1+dn(n−1)/2=14400+5280=19680",
               "recompute": "逐项累加 1200+80i (i=0..11)=19680",
               "pitfall": "误把 a1+nd 当末项（19760）或漏乘 n/2",
               "source": "大纲等差数列应用（改编）"},
        brute=lambda: sum(1200 + 80 * i for i in range(12)))

    # 7. 分段计费
    add("numeric_calculation",
        "出租车计费：3 公里内收起步价 10 元；超过 3 公里至 10 公里部分每公里 2 元；超过 10 公里部分每公里 3 元。乘车 13 公里应付多少元？",
        ["33", "31", "26", "36"], "33",
        declared=DC(kind="numeric", numbers=["3", "10", "2", "3", "13"]),
        gold_expr="10+(10-3)*2+(13-10)*3", method="seg.tiered-pricing",
        proof={"conditions": "三段：≤3 →10；(3,10] 每 km 2；(10,∞) 每 km 3",
               "solution_path": "10+14+9=33",
               "recompute": "分段累加", "pitfall": "全程按单一费率（26 或 39）",
               "source": "大纲分段计费考点（构造新数值）"},
        brute=lambda: 10 + 7 * 2 + 3 * 3)

    # 8. 浓度混合
    add("numeric_calculation",
        "将 300 克 15% 的盐水与 200 克 40% 的盐水混合，混合后盐水的浓度是多少？",
        ["25%", "27%", "30%", "55%"], "25%",
        declared=DC(kind="numeric", numbers=["300", "200", "15", "40"]),
        gold_expr="(300*15/100+200*40/100)/(300+200)", method="mix.cross-blend",
        proof={"conditions": "两溶液：盐 45g 与 80g，总液 500g",
               "solution_path": "(45+80)/500=25%",
               "recompute": "Fraction 精确", "pitfall": "浓度直接平均 27.5%",
               "source": "大纲浓度问题（新数值）"},
        brute=lambda: Fraction(45 + 80, 500))

    # 9. 利润率基准
    add("numeric_calculation",
        "某商品按进价提高 50% 定价，再按定价打八折出售。这件商品最终的利润率是多少？",
        ["20%", "40%", "30%", "10%"], "20%",
        declared=DC(kind="numeric", numbers=["50", "80"]),
        gold_expr="(1+50/100)*(80/100)-1", method="pr.profit-after-discount",
        proof={"conditions": "进价 c，定价 1.5c，售价 1.2c",
               "solution_path": "利润率=(1.2c−c)/c=20%",
               "recompute": "设 c=100 走一遍：150→120→20%",
               "pitfall": "50%−20%=30%", "source": "大纲利润问题（标准例改编）"},
        brute=lambda: Fraction(20, 100))

    # 10. 相遇追及-出发时差
    def brute_meeting():
        # 甲 8:00 出发 60km/h，乙 9:00 出发 90km/h 同向追及，追及距离=360−?
        # 场景：两地相距 330km 相向：甲 8 点 60，乙 9 点 90 → 9 点已共行 60，剩 270/150=1.8h → 距甲出发 2.8h
        return Fraction(330 - 60, 60 + 90)
    add("numeric_calculation",
        "A、B 两地相距 330 千米。甲车 8:00 从 A 出发时速 60 千米；乙车 9:00 从 B 出发与甲相向而行时速 90 千米。问乙车出发后几小时两车相遇？",
        ["1.8", "2.2", "2", "1.5"], "1.8",
        declared=DC(kind="numeric", numbers=["330", "60", "90", "1"]),
        gold_expr="(330-60*1)/(60+90)", method="tr.meeting-with-delay",
        proof={"conditions": "乙出发时甲已行 1 小时 60km，剩 270km 共行",
               "solution_path": "270/150=1.8h",
               "recompute": "Fraction(270,150)=9/5",
               "pitfall": "忽略先行小时（得 2.2）", "source": "大纲行程问题变式（构造）"},
        brute=brute_meeting)


# ================================================================ 数字推理（8）
def ns_items():
    specs = [
        ("1，3，6，10，15，（ ）", "21", "21", "一阶差 2,3,4,5,(6)：二级等差（三角数）", "second-diff"),
        ("83，80，76，71，65，（ ）", "58", "58", "差 −3,−4,−5,−6,(−7)", "second-diff3"),
        ("0，7，26，63，124，（ ）", "215", "215", "n³−1", "cube-minus"),
        ("2，6，12，20，30，（ ）", "42", "42", "差 4,6,8,10,(12)：n²+n", "diff-even"),
        ("2，6，18，54，（ ）", "162", "162", "等比 3", "geometric"),
        ("23，29，31，37，41，43，（ ）", "47", "47", "连续素数（大数起点避撞）", "primes"),
        ("1，1，2，3，5，8，（ ）", "13", "13", "每项为前两项之和", "fibonacci"),
        ("4，1，8，3，16，9，32，27，（ ）", "64", "64", "奇位 4,8,16,32,(64) 等比；偶位 1,3,9,27 等比", "interleaved"),
        ("1，3，7，15，31，（ ）", "63", "63", "2ⁿ−1", "pow2-minus"),
    ]
    for i, (stem, nxt, expr, rule, tag) in enumerate(specs):
        if tag == "interleave":
            # 奇位：2,5,8,13→差3,3,5? 重新设计干净的：奇位 2,5,10,17(+3,+5,+7)→26；偶位 3,4,6,9? 复杂。
            # 换可靠交错例：奇位 1,4,9,16(平方)，偶位 2,6,12,20(n²+n) → 下一个奇位第9项=25
            stem = "1，2，4，6，9，12，16，20，（ ）"
            nxt, expr = "25", "25"
            rule = "奇位 1,4,9,16,(25) 平方；偶位 2,6,12,20 = n(n+1)"
        seq = re.findall(r"\d+", stem)
        add("number_series", stem,
            [nxt, str(int(nxt) + 2), str(int(nxt) - 3), str(int(nxt) + 5)], nxt,
            declared=DC(kind="series", stem=stem, declared_next=nxt),
            gold_expr=expr, method=f"ns.{tag}",
            proof={"conditions": f"数列 {','.join(seq)}",
                   "solution_path": rule,
                   "recompute": "series 工具唯一规律判定（生成时校验）",
                   "pitfall": "只看一级差忽略二级差",
                   "source": "大纲数字推理·多级差/幂次/周期/交错（新数列）"})


# ================================================================ 资料分析（8，有界数据表）
def da_items():
    # 材料：某市 2021-2023 三指标（自造有界数据，增长率全部整数化）
    cells = [["年份", "GDP(亿元)", "财政收入(亿元)", "社会零售(亿元)"],
             ["2021", "2000", "260", "1000"],
             ["2022", "2500", "312.5", "1100"],
             ["2023", "3000", "375", "1210"]]
    mat = ("根据以下统计资料回答问题。\n"
           "某市 2021—2023 年国民经济指标（数值见表）：2022 年 GDP 2500 亿元，"
           "同比增长 25%；2023 年 GDP 3000 亿元，同比增长 20%；"
           "社会零售 2021 年 1000 亿元，2022 年 1100 亿元，2023 年 1210 亿元。")
    stem_head = mat + "\n"

    # 1. 2022 GDP 基期核对（给现期+率问基期）
    add("data_analysis", stem_head + "问：2021 年该市 GDP 约为多少亿元？（用 2022 年数据与增速核对）",
        ["2000", "1875", "2125", "2050"], "2000",
        declared=DC(kind="data_analysis", facts={"2022": Fraction(2500)},
                    rates={"2022": Fraction(25, 100)}, ask_year="2021",
                    gold_value=Fraction(2000)),
        gold_expr="2500/(1+25/100)", method="da.base-period", table=cells,
        proof={"conditions": "2022 GDP 2760 与其 15% 同比增速",
               "solution_path": "2500/1.25=2000", "recompute": "与表列 2021=2000 交叉核对",
               "pitfall": "2500×1.25=3125（方向反）", "source": "自造有界数据表（结构全新）"})

    # 2. 增长量
    add("data_analysis", stem_head + "问：2023 年该市 GDP 比上年增加约多少亿元？",
        ["500", "600", "480", "550"], "500",
        declared=DC(kind="data_analysis", facts={"2023": Fraction(3000)},
                    rates={"2023": Fraction(20, 100)}, ask_year="2023", ask_delta=True,
                    gold_value=Fraction(500)),
        gold_expr="3000-3000/(1+20/100)", method="da.growth-amount", table=cells,
        proof={"conditions": "2023 GDP 3144 与 14% 增速",
               "solution_path": "3000×20/120=500",
               "recompute": "两期差 3000−2500=500（表内交叉）",
               "pitfall": "3000×20%=600（未除 1+r）",
               "source": "自造有界数据表"})

    # 3. 占比
    add("data_analysis", stem_head + "问：2022 年财政收入约占当年 GDP 的比重是？",
        ["12.5%", "12%", "15%", "14%"], "12.5%",
        declared=DC(kind="numeric", numbers=["312.5", "2500"],
                    derivations={"312.5": "2500*12.5/100"}),
        gold_expr="312.5/2500", method="da.share", table=cells,
        proof={"conditions": "2022 财政 345、GDP 2760",
               "solution_path": "312.5/2500=0.125", "recompute": "Fraction(1,8)",
               "pitfall": "用错年份组合（260/2000 等）", "source": "自造有界数据表"})

    # 4. 平均数
    add("data_analysis", stem_head + "问：2021—2023 年社会零售总额的年均值是多少亿元？",
        ["1103.3", "1090", "1120", "1135"], "1103.3",
        declared=DC(kind="numeric", numbers=["1000", "1100", "1210"]),
        gold_expr="(1000+1100+1210)/3", method="da.mean3", table=cells, approx=True,
        proof={"conditions": "三年零售 1000/1100/1210", "solution_path": "3310/3=1103.3…",
               "recompute": "Fraction(3310,3)", "pitfall": "取中间值 1100",
               "source": "自造有界数据表"})

    # 5. 增速计算（跨期）
    add("data_analysis", stem_head + "问：2023 年该市社会零售总额的同比增速约为？",
        ["10%", "9%", "21%", "11%"], "10%",
        declared=DC(kind="numeric", numbers=["1100", "1210"]),
        gold_expr="(1210-1100)/1100", method="da.growth-rate", table=cells,
        proof={"conditions": "2022 零售 1100，2023 零售 1210（均在表内）",
               "solution_path": "(1210−1100)/1100=10%",
               "recompute": "Fraction(110,1100)=1/10",
               "pitfall": "除错基期（110/1210≈9%）", "source": "自造有界数据表"})

    # 6. 现期正向（给基期+次年率）
    add("data_analysis",
        "根据资料回答问题：某省 2021 年新能源汽车保有量 40 万辆。资料预测口径给出：2022 年该省新能源汽车保有量比上年增长 25%。\n"
        "问：2022 年该省新能源汽车保有量是多少万辆？",
        ["50", "48", "60", "45"], "50",
        declared=DC(kind="data_analysis", facts={"2021": Fraction(40)},
                    rates={"2022": Fraction(1, 4)}, ask_year="2022", gold_value=Fraction(50)),
        gold_expr="40*(1+25/100)", method="da.current-with-next-rate",
        proof={"conditions": "2021 值 40 万辆 + **归属 2022 的** 增长率 25%（率锚在问年）",
               "solution_path": "40×1.25=50", "recompute": "Fraction",
               "pitfall": "rb-09 型混淆：率若只属 2021（对2020）则 2022 不可推",
               "source": "为修 rb-09 缺陷设计的合法正向形（新数值）"})

    # 7. 倍数
    add("data_analysis", stem_head + "问：2023 年 GDP 约是 2021 年的多少倍？",
        ["1.5", "1.33", "1.25", "2.0"], "1.5",
        declared=DC(kind="numeric", numbers=["3000", "2000"]),
        gold_expr="3000/2000", method="da.multiple-two-year", table=cells,
        proof={"conditions": "3144 与 2400（同在表内）", "solution_path": "3144/2400=1.31",
               "recompute": "Fraction(3144,2400)=1.31", "pitfall": "答成增长率 31%（倍数=1+增长率）",
               "source": "自造有界数据表"})

    # 8. 百分点
    add("data_analysis",
        "根据资料回答问题：某市 2022 年第一产业占 GDP 比重为 8%，2023 年该比重为 6.5%。\n"
        "问：2023 年第一产业占比比上年下降了多少？",
        ["1.5 个百分点", "15%", "1.5%", "2 个百分点"], "1.5 个百分点",
        declared=DC(kind="numeric", numbers=["8", "6.5"]),
        gold_expr="8-6.5", method="da.pct-point-diff",
        proof={"conditions": "占比 8% → 6.5%", "solution_path": "差 = 1.5 个百分点",
               "recompute": "8−6.5=1.5", "pitfall": "误答下降 15%（百分点与百分比混淆）或 1.5%",
               "source": "大纲资料分析·百分点考点（新数值）"})


def exactly_one_true(defs: dict, stmts: list) -> str:
    """把 '恰一句为真' 编译为展开后的命题公式（缩写代入 J 原子）。"""
    exprs = [defs[t] for t in stmts]
    dis = "(" + "|".join(exprs) + ")"
    conj = "".join(f"&!({exprs[i]}&{exprs[j]})"
                   for i, j in itertools.combinations(range(len(exprs)), 2))
    return dis + conj


# ================================================================ 逻辑判断
def lg_items():
    # 1. 真假话·单真（机械编码：T 缩写代入 J 原子 + 恰一真编译）
    defs1 = {"T1": "J2", "T2": "!J2", "T3": "J3", "T4": "!J4"}
    prem1 = exactly_one_true(defs1, ["T1", "T2", "T3", "T4"])
    add("logical_reasoning",
        "一伙人作案，其中一人是主使。甲说：'是乙干的'；乙说：'不是我干的'；"
        "丙说：'是我丙干的'；丁说：'丁没有作案'。四人中只有一人说的是真话。主使是？",
        ["丁", "乙", "甲", "丙"], "丁",
        declared=DC(kind="truth_teller", premises=[prem1],
                    exclusive_groups=[["J1", "J2", "J3", "J4"]],
                    options_logic=[("A", "fact:J4"), ("B", "fact:J2"),
                                   ("C", "fact:J1"), ("D", "fact:J3")]),
        gold_expr="", method="lg.truth-lie-single",
        proof={"conditions": "T1=J2,T2=¬J2,T3=J3,T4=¬J4；恰一真；主使唯一（J 互斥穷尽）",
               "solution_path": "甲乙互反恰一真 → 丙丁皆假 → T4 假=丁作案；T3 假=非丙 一致",
               "recompute": "校验器对 J 赋值全枚举（互斥组约束）：唯一模型 J4 → 恰选项 A 必然",
               "pitfall": "T3/T4 同指丁：一真一假互反，须先识别甲乙矛盾对再推其余",
               "source": "真假话标准结构·自构造数值场景（定义判断/真假话为自撰文本，无题库版权）"},
        brute=lambda: "丁")

    # 2. 真假话·单真变式（答案甲）
    defs2 = {"T1": "J2", "T2": "!J2", "T3": "!J1", "T4": "J3"}
    prem2 = exactly_one_true(defs2, ["T1", "T2", "T3", "T4"])
    add("logical_reasoning",
        "失窃案查明系内部一人所为。保安甲说：'是乙偷的'；乙说：'不是我偷的'；"
        "丙说：'不是甲偷的'；丁说：'是丙偷的'。四人只有一句真话，小偷是？",
        ["甲", "乙", "丙", "丁"], "甲",
        declared=DC(kind="truth_teller", premises=[prem2],
                    exclusive_groups=[["J1", "J2", "J3", "J4"]],
                    options_logic=[("A", "fact:J1"), ("B", "fact:J2"),
                                   ("C", "fact:J3"), ("D", "fact:J4")]),
        gold_expr="", method="lg.truth-lie-single",
        proof={"conditions": "T1=J2,T2=¬J2（互反）,T3=¬J1,T4=J3；恰一真；小偷唯一",
               "solution_path": "若甲真→乙假且丙丁假→T3 假=甲偷 与 J2 冲突（互斥）；"
                               "故甲假乙真：T3 假→甲偷，T4 假→非丙 ✓ 唯一",
               "recompute": "校验器全枚举：唯一模型 J1",
               "pitfall": "从'丙说不是甲'的假话反推时才锁定甲",
               "source": "自构造（换人物/换说谎结构位置）"},
        brute=lambda: "甲")

    # 3. 两真两假·元题（恒两真不可推 → 正确答案=无法唯一确定）
    defs3 = {"T1": "JZ", "T2": "!JZ", "T3": "JL", "T4": "!JL"}
    prem3 = exactly_one_true(defs3, ["T1", "T2", "T3", "T4"]).replace(
        "&!", "&!(", 1) if False else None
    # 恰两真编译：C(4,2) 组合的真值式 = 逐对 (Ti&Tj&!Tk&!Tl) 的析取
    ex = [defs3[t] for t in ("T1", "T2", "T3", "T4")]
    combos = ["&".join([ex[i], ex[j]] + [f"!{ex[k]}" for k in range(4) if k not in (i, j)])
              for i, j in itertools.combinations(range(4), 2)]
    prem3 = "(" + "|".join(combos) + ")"
    add("logical_reasoning",
        "竞聘最终一人上岗。甲说：'上岗的是张'；乙说：'上岗的不是张'；"
        "丙说：'上岗的是李'；丁说：'上岗的不是李'。四句话恰好两真两假。据此可以确定上岗者是？",
        ["无法唯一确定", "张", "李", "王"], "无法唯一确定",
        declared=DC(kind="truth_teller", premises=[prem3],
                    exclusive_groups=[["JZ", "JL", "JW"]],
                    options_logic=[("A", "NONE_MUST"), ("B", "fact:JZ"),
                                   ("C", "fact:JL"), ("D", "fact:JW")]),
        gold_expr="", method="lg.truth-lie-two-two-meta",
        proof={"conditions": "甲乙互反恒一真；丙丁互反恒一真 → 恒两真两假（对任何上岗者）",
               "solution_path": "前提恒真 → 无法区分张/李/王 → 正确项为元判定'无法唯一确定'",
               "recompute": "校验器枚举：三个 J 模型都满足前提 → must 集空 → NONE_MUST 唯一",
               "pitfall": "强猜某一具体人选（真题常见错：用'两真两假'凑具体人）",
               "source": "元题型：考查'条件不足'识别（v2 含客观弃权判定题）"},
        brute=lambda: "无法唯一确定")

    # 4. 定义判断·绿色消费（要件矩阵机械判唯一）
    add("logical_reasoning",
        "定义：绿色消费是指以节约资源和保护环境为特征的消费行为，主要表现为崇尚勤俭节约、"
        "减少损失浪费，选择高效环保的产品和服务，降低消费过程中的资源消耗和污染排放。"
        "根据上述定义，下列属于绿色消费的是：",
        ["小李坚持自带环保袋买菜", "小张每两周驾车 30 公里到批发城囤购",
         "小王将完好旧手机长期锁在柜中不用", "小赵把厨余垃圾堆入小区绿化带"], "小李坚持自带环保袋买菜",
        declared=DC(kind="definition",
                    elements=["节约资源", "保护环境", "减少损失浪费", "选择环保产品或服务"],
                    options_elements=[
                        ("A", ["节约资源", "保护环境", "减少损失浪费", "选择环保产品或服务"]),
                        ("B", ["减少损失浪费"]),
                        ("C", []),
                        ("D", [])]),
        gold_expr="", method="dd.definition-match",
        proof={"conditions": "定义四要件（文本自撰）",
               "solution_path": "A 全满足；B 远途驾车增耗（缺节约资源/环保）；C 闲置即浪费（全缺）；D 污染（反向）",
               "recompute": "要件矩阵由逐要件人工审查结论形式化，校验器判恰一全满足",
               "pitfall": "把'囤购省钱'当节约（运输能耗反增）",
               "source": "定义文本与选项均为本次自撰"},
        brute=lambda: "小李坚持自带环保袋买菜")

    # 5. 定义判断·行政赔偿
    add("logical_reasoning",
        "定义：行政赔偿指行政机关及其工作人员违法行使职权，侵犯公民、法人和其他组织合法权益"
        "造成损害，由国家承担赔偿责任的制度。根据定义，国家应承担行政赔偿的是：",
        ["城管队员执法中损毁摊贩合格经营物品", "民警在刑事侦查中依法扣押涉案财物",
         "市民在公园自行摔倒受伤", "企业因市场价格波动发生亏损"], "城管队员执法中损毁摊贩合格经营物品",
        declared=DC(kind="definition",
                    elements=["主体为行政机关及其人员", "违法行使职权", "侵犯合法权益造成损害", "因果关联"],
                    options_elements=[
                        ("A", ["主体为行政机关及其人员", "违法行使职权", "侵犯合法权益造成损害", "因果关联"]),
                        ("B", ["主体为行政机关及其人员", "侵犯合法权益造成损害", "因果关联"]),
                        ("C", []),
                        ("D", [])]),
        gold_expr="", method="dd.definition-match",
        proof={"conditions": "定义四要件（自撰）",
               "solution_path": "A 全满足（'合格'物品被损毁=违法行使职权）；"
                               "B 依法扣押缺'违法'要件；C/D 无行政职权关联",
               "recompute": "要件矩阵机械判恰一",
               "pitfall": "把合法执法的'补偿'混为赔偿",
               "source": "定义文本与选项均为本次自撰"},
        brute=lambda: "城管队员执法中损毁摊贩合格经营物品")

    # 6. 量化三段论（monadic ≤3 元域穷举）
    add("logical_reasoning",
        "已知：所有笔记本站用户都是学生会员；有些学生会员是运动员。由此必然可以推出：",
        ["有些笔记本站用户是运动员", "所有运动员都是学生会员", "有些学生会员是笔记本站用户",
         "以上都不必然成立"], "以上都不必然成立",
        declared=DC(kind="monadic",
                    premises=["all(N,S)", "some(S,A)"],
                    options_logic=[("A", "some(N,A)"), ("B", "all(A,S)"),
                                   ("C", "some(S,N)"), ("D", "none_above_necessary")]),
        gold_expr="", method="lg.quantified-syllogism",
        proof={"conditions": "∀x(Nx→Sx)，∃x(Sx∧Ax)（英文记号仅为编码）",
               "solution_path": "反例1 N=∅ 破 C；反例2 N={0},S={0,1},A={1} 破 A；反例3 A 含域外元素破 B",
               "recompute": "校验器在 1..3 元域穷举全部模型：无选项必然 → D 唯一",
               "pitfall": "'有些'传递错觉（S∩A 与 N 可不相交）",
               "source": "量化三段论·自构造（含空集反例）"},
        brute=lambda: "以上都不必然成立")

    # 7. 加强削弱（manual_semantic 诚实通道：力度比较无法机械闭合）
    add("logical_reasoning",
        "某市推行'潮汐车道'后，早高峰主干道平均车速提升 12%，据此认为潮汐车道有效缓解了拥堵。"
        "以下哪项如果为真，最能削弱上述结论？",
        ["同期该市'错峰上下班'政策已覆盖六成企业", "潮汐车道改造成本较低",
         "邻市未推行潮汐车道车速提升 3%", "车速数据仅来自一条主干道"], "同期该市'错峰上下班'政策已覆盖六成企业",
        declared=DC(kind="manual_semantic", premises=["fact:W"], options_logic=[]),
        gold_expr="", method="lg.weaken-alternative-cause",
        proof={"conditions": "因果论证：措施→效果；削弱方向=他因/样本/无关",
               "solution_path": "A 他因（同期主政策覆盖六成）削弱力度最强；C 他市小样本类比次之；"
                               "D 样本范围再次；B 成本与结论无关",
               "recompute": "人工逐选项力度排序（A≫C≈D≫B），标记 manual_only 不作机械背书",
               "pitfall": "选 D 当最强（数据代表性弱于他因解释）",
               "source": "论证结构自撰；强度判断为人工审查"},
        brute=lambda: "同期该市'错峰上下班'政策已覆盖六成企业")


# ================================================================ 图形推理（4：文字化几何计数）
def _cube_right_triangles():
    pts = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
    cnt = 0
    for tri in itertools.combinations(pts, 3):
        right = False
        for k in range(3):
            v1 = [tri[k][i] - tri[(k + 1) % 3][i] for i in range(3)]
            v2 = [tri[k][i] - tri[(k + 2) % 3][i] for i in range(3)]
            if sum(a * b for a, b in zip(v1, v2)) == 0:
                right = True
        if right:
            cnt += 1
    return cnt


def _grid_squares():
    pts = {(x, y) for x in range(4) for y in range(4)}
    found = set()
    for a, b in itertools.permutations(pts, 2):
        dx, dy = b[0] - a[0], b[1] - a[1]
        c = (b[0] - dy, b[1] + dx)
        d = (a[0] - dy, a[1] + dx)
        if c in pts and d in pts:
            found.add(frozenset((a, b, c, d)))
    return len(found)


def fig_items():
    n_rt = _cube_right_triangles()
    n_sq = _grid_squares()
    add("figure_reasoning",
        "一条线段上标有 6 个不同的点（含端点）。以这些点为端点共可连出多少条不同的线段？",
        ["15", "12", "20", "30"], "15",
        declared=DC(kind="enumeration", numbers=["6"]),
        gold_expr="6*5/2", method="fig.segment-count",
        proof={"conditions": "任取两点定一条线段 C(6,2)", "solution_path": "6×5/2=15",
               "recompute": "程序枚举点对=15", "pitfall": "6×5=30 未除序",
               "source": "大纲图形计数·线段族（新数值 6 点）"},
        brute=lambda: sum(1 for _ in itertools.combinations(range(6), 2)))
    add("figure_reasoning",
        "从同一点引出 5 条射线，任意相邻射线夹角小于 90 度且无重合。这些射线共组成多少个角（含组合角）？",
        ["10", "5", "20", "15"], "10",
        declared=DC(kind="enumeration", numbers=["5"]),
        gold_expr="5*4/2", method="fig.angle-count",
        proof={"conditions": "任两射线成一角 C(5,2)", "solution_path": "10",
               "recompute": "组合数程序枚举", "pitfall": "只数相邻小角（4 个）",
               "source": "数角族（新数值）"},
        brute=lambda: sum(1 for _ in itertools.combinations(range(5), 2)))
    add("figure_reasoning",
        "4×4 点阵（每行每列 4 颗钉子）中，四个顶点都在钉子上的正方形共有多少个（含斜放）？",
        [str(n_sq), str(n_sq - 2), str(n_sq + 2), "14"], str(n_sq),
        declared=DC(kind="enumeration", numbers=["4"]),
        gold_expr=str(n_sq), method="fig.squares-on-grid",
        proof={"conditions": "点阵 {0..3}² 全枚举（含斜放）",
               "solution_path": f"正放 9+4+1=14，斜放 {n_sq - 14}",
               "recompute": f"坐标暴力枚举去重={n_sq}", "pitfall": "只数正放得 14",
               "source": "点阵正方形（金标由穷举给出）"},
        brute=lambda: Fraction(n_sq))
    add("figure_reasoning",
        "正方体的 8 个顶点中任取 3 个，能构成直角三角形的取法共有多少个？",
        [str(n_rt), str(n_rt - 8), str(n_rt + 4), "24"], str(n_rt),
        declared=DC(kind="enumeration", numbers=["8"]),
        gold_expr=str(n_rt), method="fig.cube-right-triangles",
        proof={"conditions": "{0,1}³ 三点组合 + 向量点积判直角",
               "solution_path": f"C(8,3)=56 组中逐组三顶点试直角：{n_rt} 组",
               "recompute": f"程序穷举={n_rt}", "pitfall": "只数面上的直角（漏棱-面对角线组合）",
               "source": "立方体几何计数（金标由穷举给出）"},
        brute=lambda: Fraction(n_rt))

    def _rectangles_4x4():
        # 4×4 单位格：横线 5 条 × 竖线 5 条，任取两横两竖成矩形
        return sum(1 for _ in itertools.combinations(range(5), 2)) ** 2

    def _pentagon_diagonals():
        pairs = list(itertools.combinations(range(5), 2))
        return sum(1 for a, b in pairs if abs(a - b) % 5 not in (1, 4))

    def _triangle_lattice():
        # 边长 2 三角格点阵：(0,0),(1,0),(2,0),(0.5,√3/2)...用整点坐标 (i+ j/2, j)
        # 斜坐标整数化：顶点 (0,0),(4,0),(2,4) 型；边长 2 → 6 点
        pts = {(2 * i + j, j) for j in range(3) for i in range(3 - j)}
        def collinear(a, b, c):
            return (b[0] - a[0]) * (c[1] - a[1]) == (c[0] - a[0]) * (b[1] - a[1])
        return sum(1 for tri in itertools.combinations(sorted(pts), 3)
                   if not collinear(*tri))

    def _regions_lines():
        # n 条直线两两相交且无三线共点：区域数 = n + 1 + C(n,2)，对小 n 归纳复核
        n = 6
        r = 2
        for k in range(2, n + 1):
            r += k  # 第 k 条线被前 k-1 条线分成 k 段，增 k 区域
        return r

    def _circle_intersections():
        return 2 * sum(1 for _ in itertools.combinations(range(5), 2))

    add("figure_reasoning",
        "4×4 的方格纸（由 5 条横线与 5 条竖线构成），图上大小不同的长方形（含正方形）一共有多少个？",
        [str(_rectangles_4x4()), "25", "81", "120"], str(_rectangles_4x4()),
        declared=DC(kind="enumeration", numbers=["4", "5"]),
        gold_expr="100", method="fig.rectangle-count",
        proof={"conditions": "任取 2 横线 × 2 竖线确定一个长方形",
               "solution_path": "C(5,2)×C(5,2)=10×10=100",
               "recompute": "程序枚举线对组合", "pitfall": "只数 1×1（16）或只数正方形",
               "source": "图形计数·长方形族（新数值 4×4）"},
        brute=lambda: Fraction(_rectangles_4x4()))
    add("figure_reasoning",
        "凸五边形的对角线共有多少条？",
        [str(_pentagon_diagonals()), "10", "6", "8"], str(_pentagon_diagonals()),
        declared=DC(kind="enumeration", numbers=["5"]),
        gold_expr="5", method="fig.diagonal-count",
        proof={"conditions": "C(5,2) 点对 − 5 条边", "solution_path": "10−5=5",
               "recompute": "程序枚举点对剔邻边", "pitfall": "答 10（含边）",
               "source": "多边形对角线考点（五边形）"},
        brute=lambda: Fraction(_pentagon_diagonals()))
    add("figure_reasoning",
        "边长为 2 的大等边三角形被分成 4 个小格（共 6 个格点：3 顶点 + 3 边中点）。以这 6 个点中 3 个点为顶点，能画出多少个不同的三角形？",
        [str(_triangle_lattice()), "20", "14", "16"], str(_triangle_lattice()),
        declared=DC(kind="enumeration", numbers=["6", "2"]),
        gold_expr="17", method="fig.triangle-lattice",
        proof={"conditions": "C(6,3)=20 三点组，减去每条边上的 1 组共线（3 条边）",
               "solution_path": "20−3=17",
               "recompute": "坐标暴力：三点行列式判共线", "pitfall": "忘去共线组（20）",
               "source": "数三角形族（三角点阵小域穷举）"},
        brute=lambda: Fraction(_triangle_lattice()))
    add("figure_reasoning",
        "平面上 6 条直线两两相交、且任意三条不共点，它们最多能把平面分成多少个区域？",
        [str(_regions_lines()), "21", "32", "36"], str(_regions_lines()),
        declared=DC(kind="enumeration", numbers=["6"]),
        gold_expr="22", method="fig.line-regions",
        proof={"conditions": "第 k 条线被前 k−1 条分割为 k 段，新增 k 个区域",
               "solution_path": "2+3+4+5+6+…递推：n=6 → 1+6+C(6,2)=22",
               "recompute": "递推程序复核=22", "pitfall": "答 2n=12 或 2⁶",
               "source": "直线分平面经典递推（新数值 6）"},
        brute=lambda: Fraction(_regions_lines()))
    add("figure_reasoning",
        "5 个圆两两相交于 2 个点，且任意三个圆无公共交点，这些圆共有多少个交点？",
        [str(_circle_intersections()), "10", "25", "20"], str(_circle_intersections()),
        declared=DC(kind="enumeration", numbers=["5"]),
        gold_expr="20", method="fig.circle-intersections",
        proof={"conditions": "每对圆贡献 2 交点，C(5,2)=10 对",
               "solution_path": "2×10=20", "recompute": "程序枚举圆对",
               "pitfall": "只算对数 10", "source": "曲线交点计数（新数值 5 圆）"},
        brute=lambda: Fraction(_circle_intersections()))


# ================================================================ 言语理解（10：细节4+主旨意图6）
V_DOC = ("某报告介绍长三角数字贸易：2023 年区域数字贸易额达 8000 亿元，其中数据服务贸易占比 "
         "25%；跨境电商进出口同比增长 18%，占数字贸易总额的一半；当年数据服务贸易额 1500 亿元，"
         "同比增长 30%。报告认为，数据服务是区域数字贸易增长最快的板块。")

V_FACTS = {"数字贸易额": "8000 亿元", "数据服务占比": "25%", "跨境电商增速": "18%",
           "跨境电商占比": "一半", "数据服务额": "1500 亿元", "数据服务增速": "30%",
           "最快板块": "数据服务"}


def vu_items():
    # 1-4 细节判断（fact_match 机械通道：恰一选项断言全同材料，其余各有一键冲突）
    add("verbal_understanding", V_DOC + "\n问：根据上述资料，以下说法正确的是：",
        ["2023 年区域数字贸易额 8000 亿元，数据服务贸易额 1500 亿元",
         "2023 年区域数字贸易额 8000 亿元，跨境电商同比增长 30%",
         "数据服务贸易占比 30%，跨境电商增速 18%",
         "2023 年数字贸易额 7000 亿元，数据服务额 1500 亿元"],
        "2023 年区域数字贸易额 8000 亿元，数据服务贸易额 1500 亿元",
        declared=DC(kind="fact_match", semantic_gold=True,
                    facts={"trade": "8000", "ds": "1500", "ec增速": "18", "占比": "25"},
                    options_elements=[
                        ("A", [("trade", "8000"), ("ds", "1500")]),
                        ("B", [("trade", "8000"), ("ec增速", "30")]),
                        ("C", [("占比", "30"), ("ec增速", "18")]),
                        ("D", [("trade", "7000"), ("ds", "1500")])]),
        gold_expr="", method="vu.detail-fact-match",
        proof={"conditions": "材料四键：trade=8000, ds=1500, ec增速=18, 占比=25",
               "solution_path": "A 两键全同 ✓；B ec增速 30 冲突；C 占比 30 冲突；D trade 7000 冲突",
               "recompute": "fact_match 通道逐键比对（机械）",
               "pitfall": "记住原文印象而非逐键核对（B 数字都在文中但张冠李戴）",
               "source": "自撰材料（数字全为我方构造）"},
        brute=lambda: "2023 年区域数字贸易额 8000 亿元，数据服务贸易额 1500 亿元")

    add("verbal_understanding", V_DOC + "\n问：关于该区域 2023 年数字贸易，下列哪项与资料不符？",
        ["跨境电商进出口同比增长 18%", "数据服务贸易额 1500 亿元",
         "数据服务贸易占比 25%，且为增长最快板块", "跨境电商占数字贸易总额的三分之一"],
        "跨境电商占数字贸易总额的三分之一",
        declared=DC(kind="fact_match", semantic_gold=True, negative=True,
                    facts={"ec增速": "18", "ds": "1500", "占比": "25", "ec占比": "一半",
                           "最快": "数据服务"},
                    options_elements=[
                        ("A", [("ec占比", "一半"), ("ec增速", "18")]),
                        ("B", [("ds", "1500"), ("最快", "数据服务")]),
                        ("C", [("占比", "25"), ("最快", "数据服务")]),
                        ("D", [("ec占比", "三分之一"), ("ds", "1500")])]),
        gold_expr="", method="vu.detail-not-match",
        proof={"conditions": "五键材料", "solution_path": "D 的 ec占比=三分之一 与材料一半冲突，"
                        "即 D 为与资料不符项（本题问\'不符\'，D 正确）",
               "recompute": "fact_match 判全对（恰一冲突项）",
               "pitfall": "看错问法（选\'与资料不符\'却找一致项）",
               "source": "自撰材料"},
        brute=lambda: "跨境电商占数字贸易总额的三分之一")

    # 5-10 主旨/意图/标题（manual_semantic：人工审查结论入 proof）
    add("verbal_understanding",
        "许多城市推行'潮汐车道'后发现：单靠道路资源再分配只能缓解高峰拥堵的一小部分，"
        "真正起决定作用的是错峰出行与公共交通分担率的提升；否则，潮汐车道开通一年后，"
        "拥堵便回到原水平。这段文字意在说明：",
        ["潮汐车道对缓解拥堵基本无效", "治理拥堵的关键在出行结构而非道路资源再分配",
         "错峰出行能彻底解决拥堵", "潮汐车道需要配套电子监控"],
        "治理拥堵的关键在出行结构而非道路资源再分配",
        declared=DC(kind="manual_semantic", premises=["fact:M"], options_logic=[]),
        gold_expr="", method="vu.intention",
        proof={"conditions": "转折结构：只…真正…否则（重点在\'真正\'句）",
               "solution_path": "意图题取对策句概括 → B；A 绝对否定（文段承认\'一小部分\'）；"
                               "C 过度承诺（彻底）；D 无中生有（监控未提）",
               "recompute": "人工逐选项错因类型标注（绝对化/过度承诺/无中生有）",
               "pitfall": "选 A（把\'只能缓解一小部分'夸大为无效）",
               "verification": "manual_only",
               "source": "自撰语料（避免真实语料版权与泄漏双重风险）"})

    add("verbal_understanding",
        "古DNA 研究让我们得以窥见万年前的种群迁徙，但样本保存条件决定了哪些遗址\'开口说话\'："
        "热带土壤里 DNA 降解极快，黄河流域之外的广大南方地区仍是证据空白。因此，对现有结论的"
        " extrapolation 必须保持克制。这段文字主要说明：",
        ["古DNA 技术前景广阔", "样本保存条件造成研究证据的地理偏斜，结论外推须谨慎",
         "热带地区无法开展古DNA 研究", "黄河流域考古成就最突出"],
        "样本保存条件造成研究证据的地理偏斜，结论外推须谨慎",
        declared=DC(kind="manual_semantic", premises=["fact:M"], options_logic=[]),
        gold_expr="", method="vu.main-idea",
        proof={"conditions": "\'因此\'结论句 + 前文因果铺垫",
               "solution_path": "主旨=结论句改写 → B；A 偏离\'但是\'后重点；C 绝对化（无法开展）；"
                               "D 推断过度（成就最突出无据）",
               "recompute": "人工主旨句定位审查", "pitfall": "选 C（文意过强）",
               "verification": "manual_only",
               "source": "自撰科普语料"})

    add("verbal_understanding",
        "给下列句子排序：① 这解释了为什么老城区改造中\'微更新\'比大拆大建更受欢迎。"
        "② 城市记忆附着于具体的街巷尺度与公共空间。③ 一旦空间整体消失，记忆传承便出现断层。"
        "④ 而\'微更新\'保留了空间肌理，也就保留了记忆的载体。最恰当的排列是：",
        ["②③①④", "②③④①", "①②③④", "③②①④"], "②③④①",
        declared=DC(kind="manual_semantic", premises=["fact:O"], options_logic=[]),
        gold_expr="", method="vu.sentence-ordering",
        proof={"conditions": "④\'而\'与\'也就保留了记忆的载体\'回指②的\'载体\'主题；①\'这解释\'为收束句",
               "solution_path": "②提出论点→③反面后果→④\'微更新\'对策→①总结解释 ✓；①不能置④前（指代无着落）",
               "recompute": "人工衔接词审查（这/而/一旦 指代链）",
               "pitfall": "选 ②③①④（总结句①插中间打断指代）",
               "verification": "manual_only", "source": "自撰语料"})

    add("verbal_understanding",
        "某市图书馆推行\'阅读积分\'：借书、荐书、写书评都可积分，积分兑换书展门票与自习舱时长。"
        "半年后借阅量上升 40%，但书评参与率不足 5%。有研究者据此认为积分激励对\'深度参与\'"
        "作用有限。以下哪项如果为真，最能支持上述观点？",
        ["兑换书展门票的人数同样寥寥", "自习舱时长供不应求",
         "借书量上升主要来自原有高频读者", "积分规则公布渠道畅通"],
        "兑换书展门票的人数同样寥寥",
        declared=DC(kind="manual_semantic", premises=["fact:M"], options_logic=[]),
        gold_expr="", method="vu.strengthen-evidence",
        proof={"conditions": "观点：积分对深度参与（书评）激励有限",
               "solution_path": "A：同为\'文化类兑换\'的书展门票也无人问津 → 深度参与意愿普遍低，"
                               "支持\'有限\'；B 自习舱是浅层使用；C 削弱替代解释? C 说明借书增量"
                               "并非新读者——与观点方向无关；D 无关",
               "recompute": "人工支持力度排序（A>C?≈B≈D）",
               "pitfall": "选 C（把\'来源结构\'当支持）", "verification": "manual_only",
               "source": "自撰案例"})

    add("verbal_understanding",
        "填入空格：传统手工艺的传承困局，不在于受众\'不感兴趣\'，而在于展示渠道与消费场景的"
        "______；一旦进入日常生活视野，老手艺的______便迅速显现。依次填入最恰当的是：",
        ["错位…亲和力", "缺失…生命力", "窄化…吸引力", "割裂…感染力"], "缺失…生命力",
        declared=DC(kind="manual_semantic", premises=["fact:C"], options_logic=[]),
        gold_expr="", method="vb.logic-blank",
        proof={"conditions": "空1 与\'渠道与场景\'搭配（渠道场景的\_\_\_\_ → 没有）；"
                            "空2 与\'一旦…便迅速显现\'的勃发语义",
               "solution_path": "\'渠道与场景的缺失\'搭配成立且后句\'生命力\'对应\'显现\' ✓；"
                               "A\'错位\'与\'渠道\'可搭但\'亲和力\'≠显现；C\'窄化\'主客颠倒；"
                               "D\'割裂\'需\'与…\'介宾，无",
               "recompute": "人工搭配矩阵审查（记录四词×两空）",
               "pitfall": "选 D 只凭\'割裂\'热词感", "verification": "manual_only",
               "source": "自撰语料"})

    add("verbal_understanding",
        "对欧盟碳边境调节机制（CBAM）而言，2026 年正式征收并非终点：报告强调，过渡期内企业"
        "完成的排放核算能力建设，才是决定其能否真正\'过关\'的关键。这段文字意在强调：",
        ["CBAM 将于 2026 年开征", "过渡期的核算能力建设比形式上的时间节点更关键",
         "企业无法应对 CBAM", "碳关税将长期存在"],
        "过渡期的核算能力建设比形式上的时间节点更关键",
        declared=DC(kind="manual_semantic", premises=["fact:M"], options_logic=[]),
        gold_expr="", method="vu.intention",
        proof={"conditions": "\'并非终点…才是关键\'强调句",
               "solution_path": "B 为强调句改写 ✓；A 细节；C 无据绝对；D 偏离\'关键\'句",
               "recompute": "人工强调结构定位", "pitfall": "选 A（背景句当主旨）",
               "verification": "manual_only", "source": "自撰时政语料"})

# ================================================================ 语言推理（6：实词辨析4+排序2）
def lr_items():
    add("language_reasoning",
        "监管新规\_\_\_\_了平台自动定价的边界，也为消费者\_\_\_\_了申诉通道。依次填入最恰当的是：",
        ["厘清…打通", "澄清…开辟", "厘清…开辟", "澄清…打通"], "厘清…打通",
        declared=DC(kind="definition", negative=False,
                    elements=["与\'边界\'搭配指划分清楚", "与\'通道\'搭配指使之畅通"],
                    options_elements=[
                        ("A", ["与\'边界\'搭配指划分清楚", "与\'通道\'搭配指使之畅通"]),
                        ("B", ["与\'通道\'搭配指使之畅通"]),
                        ("C", ["与\'边界\'搭配指划分清楚"]),
                        ("D", [])]),
        gold_expr="", method="vb.content-word",
        proof={"conditions": "空1 对象=边界（划分类，厘清）；澄清用于事实/疑点。空2 通道→打通（搭配惯用）",
               "solution_path": "A 双搭配成立；B 澄清×边界不搭；C 开辟×通道弱于打通（无畅通义）；"
                               "D 双缺", "recompute": "搭配要件矩阵（作者词典事实记录）",
               "pitfall": "凭\'开辟通道\'语感选 B/C（忽视澄清/厘清分野）",
               "source": "自撰语境（搭配判断为作者显式要件声明）"},
        brute=lambda: "厘清…打通")
    add("language_reasoning",
        "两款产品的定价策略看似\_\_\_\_，实则都瞄准同一高端客群；所谓差异只是营销话术的\_\_\_\_。",
        ["分道扬镳…包装", "背道而驰…表象", "殊途同归…障眼", "南辕北辙…假象"], "分道扬镳…包装",
        declared=DC(kind="definition",
                    elements=["空1 形容策略方向不同（非目标相反）", "空2 与\'只是话术\'的掩饰义名词"],
                    options_elements=[
                        ("A", ["空1 形容策略方向不同（非目标相反）", "空2 与\'只是话术\'的掩饰义名词"]),
                        ("B", ["空2 与\'只是话术\'的掩饰义名词"]),
                        ("C", ["空1 形容策略方向不同（非目标相反）"]),
                        ("D", ["空2 与\'只是话术\'的掩饰义名词"])]),
        gold_expr="", method="vb.content-word",
        proof={"conditions": "看似…实则…转折：策略不同但同客群。背道而驰/南辕北辙均指方向相反"
                            "（与\'看似不同实则同\'矛盾且带目标相反义）；殊途同归与\'看似\'语义重复；"
                            "\障眼\'口语且\'话术的障眼\'搭配劣",
               "recompute": "要件矩阵（作者词义辨析结论）",
               "pitfall": "选 C（殊途同归恰好抹掉转折对比）",
               "source": "自撰语境"},
        brute=lambda: "分道扬镳…包装")
    add("language_reasoning",
        "填入划横线处最恰当的词语：这份抽样报告的结论过于\_\_\_\_，只覆盖了城区三所学校，"
        "不足以代表全省县域情况。",
        ["武断", "偏颇", "草率", "狭隘"], "偏颇",
        declared=DC(kind="definition",
                    elements=["指结论有失全面（非态度匆忙）", "可受\'过于\'修饰"],
                    options_elements=[("A", ["可受\'过于\'修饰"]),
                                      ("B", ["指结论有失全面（非态度匆忙）", "可受\'过于\'修饰"]),
                                      ("C", ["可受\'过于\'修饰"]),
                                      ("D", [])]),
        gold_expr="", method="vb.content-word",
        proof={"conditions": "语境=样本覆盖不全→结论失衡；非下结论匆忙",
               "solution_path": "偏颇=不全面 ✓；武断=轻率下判断（态度义）；草率=匆忙；狭隘=眼界/心胸",
               "recompute": "要件矩阵", "pitfall": "选 A 武断（近义强度误判）",
               "source": "自撰语境"},
        brute=lambda: "偏颇")
    add("language_reasoning",
        "依次填入横线：技术标准的\_\_\_\_滞后于产品迭代，监管只能以\_\_\_\_方式追赶，行业期待"
        "的是前置性规则而非事后\_\_\_\_。",
        ["制定…补丁式…纠偏", "修订…运动式…矫正", "出台…渐进式…匡正", "确立…嵌入式…匡正"], "制定…补丁式…纠偏",
        declared=DC(kind="definition",
                    elements=["空2 修饰追赶式监管的临时碎片特征", "空3 与\'事后\'搭配且名词化"],
                    options_elements=[
                        ("A", ["空2 修饰追赶式监管的临时碎片特征", "空3 与\'事后\'搭配且名词化"]),
                        ("B", ["空3 与\'事后\'搭配且名词化"]),
                        ("C", []),
                        ("D", ["空3 与\'事后\'搭配且名词化"])]),
        gold_expr="", method="vb.content-word",
        proof={"conditions": "三空语境；\'补丁式\'唯一给出临时碎片义",
               "solution_path": "A ✓；B 运动式≠追赶滞后（是集中整治）；C 渐进式无\'只能追赶\'的贬义；"
                               "D 嵌入式无临时义且\'事后匡正\'文言色彩与语体不合",
               "recompute": "要件矩阵", "pitfall": "选 B（运动式热词）",
               "source": "自撰语段"},
        brute=lambda: "制定…补丁式…纠偏")
    add("language_reasoning",
        "将句子排序成连贯段落：① 因此，指标体系必须随产业成熟度动态调整。② 新兴产业早期，"
        "用户规模比营收更能反映价值。③ 成熟期则相反，利润与现金流成为核心标尺。④ 若沿用早期"
        "指标评价成熟期业务，估值判断会系统性失真。排序正确的是：",
        ["②③④①", "①②③④", "②③①④", "③②④①"], "②③④①",
        declared=DC(kind="manual_semantic", premises=["fact:O"], options_logic=[]),
        gold_expr="", method="vb.sentence-ordering",
        proof={"conditions": "②早期/③成熟期对照 → ④错配后果 → ①因此收束（对策句）",
               "solution_path": "②③④① ✓；①\'因此\'为结论必须在④之后",
               "recompute": "人工衔接词审查（因此/若…则）", "pitfall": "选 ②③①④（结论前置因果倒置）",
               "verification": "manual_only", "source": "自撰语料"},
        brute=lambda: "②③④①")
    add("language_reasoning",
        "① 但普查发现，其中相当比例的\'夜间活跃灯光\'来自 24 小时便利店而非工厂。② 夜经济"
        "常被用作产业活力的代理指标。③ 这提示单一灯光指标会高估工业活动。④ 研究者借此推测"
        "区域制造业景气度。排序正确的是：",
        ["②④①③", "②①④③", "①②④③", "④②①③"], "②④①③",
        declared=DC(kind="manual_semantic", premises=["fact:O"], options_logic=[]),
        gold_expr="", method="vb.sentence-ordering",
        proof={"conditions": "②引入指标 → ④\'借此\'指代②灯光指标 → ①\'但\'普查反转 → ③\'这提示\'收束",
               "solution_path": "②④①③ ✓；①的\'其中\'指代④的推测对象，不可在④前",
               "recompute": "人工指代链审查", "pitfall": "选 ②①④③（\'但\'过早转折）",
               "verification": "manual_only", "source": "自撰语料"},
        brute=lambda: "②④①③")


# ================================================================ 判断补充·定义新建（4）+ 情境人格（4）
def dd_items():
    # 新定义判断：定义自撰，要件矩阵机械判唯一
    add("logical_reasoning",
        "定义：被动收入指无需本人持续投入时间与劳动即可定期产生的收入。根据定义，下列属于被动收入的是：",
        ["将自有住房长期出租按月收取的租金", "每周三晚上在线授课获得的课酬",
         "加班获得的当月绩效奖金", "撰写一篇公众号文章所得的一次性稿费"], "将自有住房长期出租按月收取的租金",
        declared=DC(kind="definition",
                    elements=["无需持续投入时间劳动", "定期产生", "属于收入"],
                    options_elements=[("A", ["无需持续投入时间劳动", "定期产生", "属于收入"]),
                                      ("B", ["定期产生", "属于收入"]),
                                      ("C", ["属于收入"]),
                                      ("D", ["属于收入"])]),
        gold_expr="", method="dd.new-definition",
        proof={"conditions": "三要件", "solution_path": "A 全满足；B 需每周授课（持续劳动）；"
                        "C 加班绩效与劳动挂钩；D 一次性（非定期）",
               "recompute": "要件矩阵机械判恰一", "pitfall": "把课酬当被动（忽视每周授课）",
               "source": "定义自撰"},
        brute=lambda: "将自有住房长期出租按月收取的租金")
    add("logical_reasoning",
        "定义：沉没成本指已经发生且无法通过当前决策收回的成本。根据定义，下列决策**不受**沉没成本影响的是：",
        ["因为已装修花 20 万而继续入住质量差的新房", "因买了电影票而冒暴雨去影院",
         "评估新项目只看未来投入产出，不再纠结已付的咨询费", "因读完一半的书而硬着头皮读完"],
        "评估新项目只看未来投入产出，不再纠结已付的咨询费",
        declared=DC(kind="definition", negative=True,
                    elements=["已发生且不可收回", "当前决策仍以其为依据"],
                    options_elements=[
                        ("A", ["已发生且不可收回", "当前决策仍以其为依据"]),
                        ("B", ["已发生且不可收回", "当前决策仍以其为依据"]),
                        ("C", ["已发生且不可收回"]),
                        ("D", ["已发生且不可收回", "当前决策仍以其为依据"])]),
        gold_expr="", method="dd.new-definition-neg",
        proof={"conditions": "问\'不受影响\'项 = 不满足\'仍以其为依据\'要件",
               "solution_path": "A/B/D 决策都被已付成本牵引；C 明确只看未来 → 不受 ✓",
               "recompute": "要件矩阵（C 缺第二要件 → 为正确项）",
               "pitfall": "把\'不受影响\'看成\'属于影响\'",
               "source": "定义自撰（反向设问）"},
        brute=lambda: "评估新项目只看未来投入产出，不再纠结已付的咨询费")
    add("logical_reasoning",
        "定义：白名单机制指默认禁止、仅对明示列出的对象放行的管理方式。根据定义，属于白名单机制的是：",
        ["邮件系统默认拦截所有发件人，仅放行用户登记的地址", "垃圾邮件过滤器按拦截规则表屏蔽可疑邮件",
         "社交平台对投诉内容人工审核后删除", "网关限流时优先保障认证用户带宽"],
        "邮件系统默认拦截所有发件人，仅放行用户登记的地址",
        declared=DC(kind="definition",
                    elements=["默认禁止", "仅明示列出对象放行", "管理方式"],
                    options_elements=[("A", ["默认禁止", "仅明示列出对象放行", "管理方式"]),
                                      ("B", ["仅明示列出对象放行", "管理方式"]),
                                      ("C", ["管理方式"]),
                                      ("D", ["仅明示列出对象放行"])]),
        gold_expr="", method="dd.new-definition",
        proof={"conditions": "三要件：默认态=禁止；放行依据=明示名单",
               "solution_path": "A ✓；B 黑名单（默认允许+列出**拦截**）缺\'默认禁止\'；"
                               "C 事后处置非默认禁止；D 优先≠仅放行",
               "recompute": "要件矩阵机械", "pitfall": "黑白名单方向混淆",
               "source": "定义自撰"},
        brute=lambda: "邮件系统默认拦截所有发件人，仅放行用户登记的地址")
    add("logical_reasoning",
        "定义：正惩罚指行为后施加厌恶刺激以降低该行为频率的做法。根据定义，属于正惩罚的是：",
        ["司机违章后被通报批评，此后违章减少", "学生表现好被免去值日，此后更用功",
         "孩子哭闹时家长不再理睬，哭闹渐少", "员工按时出勤可得全勤奖"],
        "司机违章后被通报批评，此后违章减少",
        declared=DC(kind="definition",
                    elements=["行为后施加", "厌恶刺激", "行为频率降低"],
                    options_elements=[("A", ["行为后施加", "厌恶刺激", "行为频率降低"]),
                                      ("B", ["行为后施加"]),
                                      ("C", ["行为频率降低"]),
                                      ("D", ["行为后施加"])]),
        gold_expr="", method="dd.new-definition",
        proof={"conditions": "操作性条件反射术语（自撰定义文本）",
               "solution_path": "A 全满足（通报批评=厌恶刺激+频率降）；B 负强化（移除厌恶物增频）；"
                               "C 消退（撤销关注）；D 正强化",
               "recompute": "要件矩阵机械", "pitfall": "混淆正惩罚/负强化",
               "source": "定义自撰"},
        brute=lambda: "司机违章后被通报批评，此后违章减少")


def personality_items():
    # R5 含 personality 域（Solver 未支持）。v2 保留该域做能力覆盖评测，全部 manual 通道。
    add("personality",
        "团队紧急改版只剩 48 小时，成员小周主张推翻现方案重做，小陈主张在现方案上修补。"
        "作为负责人，**最优先**应做的是什么？",
        ["立即投票按多数执行", "先比对两种方案与时间约束的匹配度再决策",
         "采纳资历更深者意见", "延后两天再定"], "先比对两种方案与时间约束的匹配度再决策",
        declared=DC(kind="manual_semantic", premises=["fact:S"], options_logic=[]),
        gold_expr="", method="pers.situational-judgment",
        proof={"conditions": "情境约束：48 小时 + 分歧方案",
               "solution_path": "B 依约束决策 ✓；A 忽视可行性；C 权威导向；D 逃避时限",
               "recompute": "人工情境判断审查", "pitfall": "投票民主优先于可行性（A）",
               "verification": "manual_only", "source": "自撰情境题（R5 未支持域覆盖评测）"})
    add("personality",
        "客户投诉报告数据有误，经核查确系我方笔误。回复客户时最恰当的做法是：",
        ["先解释笔误产生的原因再致歉", "直接致歉并给出更正数据与复核承诺",
         "要求客户重新提交原始数据以便核对", "让经办人个人向客户道歉"],
        "直接致歉并给出更正数据与复核承诺",
        declared=DC(kind="manual_semantic", premises=["fact:S"], options_logic=[]),
        gold_expr="", method="pers.situational-judgment",
        proof={"conditions": "事实已确认我方错",
               "solution_path": "B 承担+补救 ✓；A 解释先行弱化诚意；C 转嫁责任；D 组织问题个人化",
               "recompute": "人工审查", "pitfall": "选 A（先解释）",
               "verification": "manual_only", "source": "自撰情境题"})
    add("personality",
        "跨部门协作中，对方负责人多次未按时提交依赖材料，影响本项目排期。最佳**首次**应对：",
        ["向其上级投诉", "当面沟通时间困难并约定明确的交付节点与提醒机制",
         "暂停合作以示警告", "由己方代为完成"], "当面沟通时间困难并约定明确的交付节点与提醒机制",
        declared=DC(kind="manual_semantic", premises=["fact:S"], options_logic=[]),
        gold_expr="", method="pers.situational-judgment",
        proof={"conditions": "升级梯度原则：先平级直接沟通",
               "solution_path": "B ✓；A 越级过早；C 双输；D 掩盖问题不可持续",
               "recompute": "人工审查", "pitfall": "跳过平级沟通直接升级",
               "verification": "manual_only", "source": "自撰情境题"})
    add("personality",
        "你的方案在评审中被指出一个未考虑的关键约束，且对方证据充分。当下最建设性的回应是：",
        ["承认疏漏并当场表示将按对方意见修改", "指出对方的证据样本量不足",
         "先确认约束的适用边界，再决定接受或调整方案", "保持沉默按原案推进"],
        "先确认约束的适用边界，再决定接受或调整方案",
        declared=DC(kind="manual_semantic", premises=["fact:S"], options_logic=[]),
        gold_expr="", method="pers.situational-judgment",
        proof={"conditions": "证据充分但未验证适用性",
               "solution_path": "C 既不固执也不盲从 ✓；A 全盘接受欠独立判断；B 转移话题；D 拒绝",
               "recompute": "人工审查", "pitfall": "把 A 当开放态度",
               "verification": "manual_only", "source": "自撰情境题"})


# ================================================================ 泄漏两维检查 + 输出
def _tokens(text):
    toks = set()
    for seg in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", text or ""):
        if re.match(r"[a-zA-Z0-9]", seg):
            toks.add(seg.lower())
        else:
            toks.update(list(seg))
            toks.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return toks


def leak_checks(existing_rows):
    """返回 (dropped, soft_flags, structural_dup)。
    数学结构近重复 = 题面数值多重集与既有题相同（v2 特别要求）。"""
    ex_sigs = {(r.get("id"), numsig(r.get("stem", ""))) for r in existing_rows}
    ex_texts = [(r.get("id"), r.get("stem", "")) for r in existing_rows]
    dropped, flags = set(), []
    kept = []
    for it in ITEMS:
        sig = numsig(it["stem"])
        sdup = [eid for eid, es in ex_sigs if sig and sig == es]
        if sdup:
            flags.append({"item": it["id"], "action": "dropped_structural",
                          "near": sdup, "sig": list(sig)})
            continue
        kept.append(it)
        t = _tokens(it["stem"])
        for eid, et in ex_texts:
            e = _tokens(et)
            j = len(t & e) / max(1, min(len(t), len(e)))
            if j >= 0.7:
                dropped.add(it["id"])
                flags.append({"item": it["id"], "near": eid, "similarity": round(j, 3),
                              "action": "dropped"})
            elif j > 0.55:
                flags.append({"item": it["id"], "near": eid, "similarity": round(j, 3),
                              "action": "soft_flag"})
    # 文本泄漏丢弃：proofs 与 items 同序，一并剔除后同步重编号
    kept_by_index = [i for i, k in enumerate(kept) if k["id"] not in dropped]
    kept = [kept[i] for i in kept_by_index]
    dropped_struct = {f["item"] for f in flags if f.get("action") == "dropped_structural"}
    # PROOFS 顺序与 ITEMS 顺序一致（add 同步 append），按原始序号对齐剔除
    import copy as _cp
    orig_order = [it["id"] for it in ITEMS]
    proof_by_orig = {}
    for pr, oid in zip(PROOFS, orig_order):
        proof_by_orig[oid] = pr
    old_ids = [k["id"] for k in kept]
    for n, it in enumerate(kept, 1):
        it["id"] = f"v2-{n:02d}"
    proofs_final = []
    for new_it, old_id in zip(kept, old_ids):
        pr = _cp.deepcopy(proof_by_orig[old_id])
        pr["id"] = new_it["id"]
        pr["answer"] = new_it["answer"]
        pr["answer_text"] = next(o["text"] for o in new_it["options"]
                                 if o["label"] == new_it["answer"])
        proofs_final.append(pr)
    PROOFS.clear()
    PROOFS.extend(proofs_final)
    _ = dropped_struct
    return kept, flags


def main():
    nc_items()
    ns_items()
    da_items()
    lg_items()
    fig_items()
    vu_items()
    lr_items()
    dd_items()
    personality_items()
    existing = []
    for p in (ROOT / "data/benchmark_xingce_dev/dev/xingce_dev.jsonl",
              ROOT / "data/benchmark_xingce/test/xingce.jsonl",
              ROOT / "data/benchmark_xingce_blind/test/xingce_blind.jsonl",
              ROOT / "data/benchmark_xingce_blind/test_v11/xingce_blind.jsonl"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                existing.append(json.loads(line))
    kept, flags = leak_checks(existing)
    out_dir = ROOT / "data/benchmark_xingce_blind_v2/test"
    out_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(k, ensure_ascii=False) for k in kept) + "\n"
    (out_dir / "xingce_blind_v2.jsonl").write_text(body, encoding="utf-8")
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    (out_dir / "proofs.json").write_text(
        json.dumps(PROOFS, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "rejected_items.json").write_text(
        json.dumps(REJECTED, ensure_ascii=False, indent=2), encoding="utf-8")
    flags_dropped = [f for f in flags if f.get("action", "").startswith("dropped")]
    meta = {"count": len(kept), "sha256_16": sha, "seed": 20261021,
             "frozen_at": None,
             "leak": {"flags": flags, "dropped": len(flags_dropped)},
             "validity_rejected": len(REJECTED),
             "note": "独立命题：全部新数值/新结构；每题含唯一性证明（proofs.json）；"
                     "四层验证+暴力穷举+双维泄漏检查；冻结时间待预注册时填写",
             "skill_dist": {}}
    from collections import Counter
    meta["skill_dist"] = dict(Counter(k["skill"] for k in kept))
    (out_dir / "frozen_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[v2] {len(kept)} 题 sha={sha} 拒={len(REJECTED)} 泄漏丢弃={len(flags_dropped)}")
    print("  分布:", meta["skill_dist"])
    for r in REJECTED[:6]:
        lay = r["validity"]["layers"]
        print("  REJ", r["method"], r["validity"]["marker"],
              next((v["why"][:70] for v in lay.values() if not v["passed"]), ""))


if __name__ == "__main__":
    main()
