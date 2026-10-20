"""R7 Stage D/E：方法卡 → 练习/盲集题目生成器（gold-first，确定性模板）。

纪律（工单 §6/§7）：
- 金标由**方法卡公式**在脚本内用 Fraction 精确算出（先有 gold 后有题），
  不经任何模型、不读任何评测集答案；
- 模板是卡片的语言变体（换情境换数字），不是对 dev/R5 题面的改写；
- 干扰项按 common_pitfalls 构造（典型错误路径），正确答案必须唯一；
- 生成后与 dev+R5 做 token 近重复泄漏检查，超阈值的题**只报告并人工复核**；
- 输出 `data/benchmark_xingce_blind/test/xingce_blind.jsonl`（dev 同 schema），
  sidecar 记录冻结 sha/种子/时间。

用法：python -X utf8 scripts/gen_blind_set.py   （seed 固定，可复现）
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

rng = random.Random(20261020)
ITEMS = []
_counters = {}


def variant(key, choices):
    """确定性轮换变体：同一模板的第 k 次调用取第 k 个配置，避免重复题面。"""
    i = _counters.get(key, 0)
    _counters[key] = i + 1
    return choices[i % len(choices)]


def pct_text(value: Fraction) -> str:
    v = Fraction(value) * 100
    if v.denominator == 1:
        return f"{v.numerator}%"
    return f"{float(v):.1f}%"


def money(value) -> str:
    v = Fraction(value)
    return str(v.numerator) if v.denominator == 1 else f"{float(v):g}"


def add(skill, method_id, stem, options, answer, note=""):
    opts = list(dict.fromkeys(str(o) for o in options))
    answer = str(answer)
    assert answer in opts, (method_id, answer, opts)
    if len(opts) < 4:
        for extra in ("0", "1", "2", "3"):
            if len(opts) >= 4:
                break
            if extra not in opts:
                opts.append(extra)
    opts = opts[:4]
    assert answer in opts, (method_id, answer, opts)
    order = list(opts)
    rng.shuffle(order)
    labels = "ABCD"
    ITEMS.append({
        "id": f"rb-{len(ITEMS) + 1:02d}",
        "skill": skill,
        "method_id": method_id,
        "stem": stem,
        "options": [{"label": labels[i], "text": t} for i, t in enumerate(order)],
        "answer": labels[order.index(answer)],
        "gold_value": answer,
        "note": note,
    })


# ---------------------------------------------------------------- nc 模板
def gen_discount():
    _tag, base = variant("discount_base", [("羽绒服", 800), ("图书券", 260), ("登山包", 450)])
    d1, d2 = variant("discount_rate", [(Fraction(8, 10), Fraction(9, 10)),
                         (Fraction(7, 10), Fraction(8, 10)),
                         (Fraction(9, 10), Fraction(9, 20) + Fraction(1, 4))])
    final = Fraction(base) * d1 * d2
    wrong_add = Fraction(base) * (1 - ((1 - d1) + (1 - d2)))  # pitfall: 折率直接相减
    stem = (f"某书店会员日全场书品按同一基准两次下调：第一次下调 m%，"
            f"第二次在第一次下调后的价格上下调 n%。已知 m={money((1 - d1) * 100)}、"
            f"n={money((1 - d2) * 100)}，一套定价 {base} 元的丛书两次下调后的最终售价是多少元？")
    opts = [money(final), money(wrong_add), money(Fraction(base) * d1), money(Fraction(base) * d2)]
    add("numeric_calculation", "nc.successive-discount", stem, opts, money(final),
        "连续折扣=乘法复合；干扰=折率相加/漏一次")


def gen_concentration():
    m, c, w = variant("conc", [(200, Fraction(1, 10), 50), (300, Fraction(2, 25), 100),
                          (400, Fraction(1, 5), 100)])
    salt = m * c
    new = salt / (m + w)
    stem = (f"实验小组配制了 {m} 克含盐量为{pct_text(c)}的盐溶液，随后向其中注入 {w} 克蒸馏水"
            f"（盐分未损失也未添加）。注入后盐溶液的含盐百分比是多少？")
    opts = [pct_text(new), pct_text(c), pct_text(salt / (m - w)), pct_text(salt / w)]
    add("numeric_calculation", "nc.concentration-conservation", stem, opts, pct_text(new),
        "分母=新总量；干扰=旧分母/蒸发/只除加水量")


def gen_restore_price():
    x = variant("restore", [Fraction(1, 4), Fraction(1, 5), Fraction(1, 3)])
    rate = x / (1 - x)
    p = variant("restore_price", [200, 400, 80])
    stem = (f"一件标价 {p} 元的商品降价{pct_text(x)}出售。若要恢复原价，"
            f"需要在现价基础上提价百分之几？")
    opts = [pct_text(rate), pct_text(x), pct_text(x / 2), pct_text(Fraction(1, 3))]
    add("numeric_calculation", "nc.profit-margin-base", stem, opts, pct_text(rate),
        "基准=现价；干扰=直接报降幅")


def gen_travel():
    v1, v2, t = variant("travel", [(60, 40, Fraction(5, 2)), (70, 50, Fraction(3, 2)), (80, 60, Fraction(2, 1))])
    dist = Fraction(v1 + v2) * t
    stem = (f"快车与慢车同时分别从甲、乙两地相向开出，快车时速 {v1} 千米、慢车时速 {v2} 千米，"
            f"经过 {float(t):g} 小时两车相遇。甲乙两地相距多少千米？")
    opts = [str(dist), str(Fraction(v1) * t), str(abs(Fraction(v1 - v2) * t)), str(Fraction(v2) * t)]
    add("numeric_calculation", "nc.travel-meeting-pursuit", stem, opts, str(dist),
        "路程=速度和×相遇时间")


def gen_work():
    a, b = variant("work", [(10, 15), (12, 6), (20, 30)])
    together = 1 / (Fraction(1, a) + Fraction(1, b))
    stem = (f"一项工程，甲队单独做 {a} 天完成，乙队单独做 {b} 天完成。"
            f"两队共同施工需要多少天完成？")
    opts = [f"{float(together):g}", f"{(a + b) / 2:g}", str(min(a, b)), f"{float(Fraction(a) * b / (a + b) / 2):g}"]
    add("numeric_calculation", "nc.work-rate-lcm", stem, opts, f"{float(together):g}",
        "效率相加再取倒数；干扰=平均天数")


# ---------------------------------------------------------------- da 模板
def gen_base_period():
    base, g, year = variant("base_period", [(1100, Fraction(1, 5), 2024),
                                             (672, Fraction(1, 4), 2023),
                                             (1000, Fraction(1, 10), 2024)])
    cur = Fraction(base) * (1 + g)
    assert cur.denominator == 1, cur
    other = base * 3 // 5
    stem = (f"某开发区统计局公报：{year - 1} 年该区数字经济营业收入 {base} 亿元，"
            f"同比增长{pct_text(g)}；同年全区软件服务收入 {other} 亿元，"
            f"同比增长{pct_text(Fraction(1, 8))}。\n问：{year} 年该区数字经济营业收入约为多少亿元？")
    opts = [f"{float(cur):g}", f"{float(Fraction(base) / (1 + g)):g}",
            f"{float(Fraction(base) + Fraction(cur - base) / 2):g}",
            f"{float(Fraction(base) * (1 + g + Fraction(1, 10))):g}"]
    add("data_analysis", "da.base-period", stem, opts, f"{float(cur):g}",
        "材料给基期+增速问现期：现期=基期×(1+r)")


def gen_growth_amount():
    base, r, year = variant("growth_amount", [(640, Fraction(1, 4), 2024),
                                              (450, Fraction(2, 5), 2023),
                                              (500, Fraction(1, 5), 2024)])
    cur = Fraction(base) * (1 + r)
    assert cur.denominator == 1, cur
    inc = cur - base
    stem = (f"统计公报：{year} 年某市新能源汽车产量 {float(cur):g} 万辆，"
            f"比上年增长{pct_text(r)}；配套充电设施 {base // 4} 万台，"
            f"同比增长{pct_text(Fraction(1, 2))}。\n问：{year} 年新能源汽车产量比上年约增加多少万辆？")
    opts = [f"{float(inc):g}", f"{float(cur * r):g}", f"{float(base):g}",
            f"{float(inc / 2):g}"]
    add("data_analysis", "da.growth-amount", stem, opts, f"{float(inc):g}",
        "增长量=现期×r/(1+r)；干扰=直接×r")


# ---------------------------------------------------------------- lg 模板
def gen_logic_direction():
    pairs = [("通过科目一", "领取学习证"), ("完成年检", "上路行驶"), ("提交材料", "获得受理")]
    a, b = variant("lg_pair", pairs)
    stem = (f"规定：只有{a}，才能{b}。已知小李{b}。由此可以推出，以下哪项必然为真？")
    opts = [f"小李{a}", f"小李没有{a}", f"{a}的人都会{b}", f"如果{a}那么就能{b}"]
    add("logical_reasoning", "lg.implication-direction", stem, opts, f"小李{a}",
        "只有P才Q：Q→P；肯定后件推前件")


# ---------------------------------------------------------------- ns 模板
def gen_series():
    primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67]
    start = variant("primes_start", [1, 4, 6, 9])
    seq = primes[start:start + 6]
    nxt = primes[start + 6]
    stem = "，".join(str(x) for x in seq) + "，（ ）"
    opts = [str(nxt), str(nxt + 2), str(seq[-1] + 2), str(nxt - 4)]
    add("number_series", "ns.primes", stem, opts, str(nxt), "连续素数")
    n0, d0, qq = variant("frac", [(3, 4, 2), (3, 2, 2), (5, 8, 2), (1, 4, 2)])
    nums = [n0 + i * 2 for i in range(4)]
    dens = [d0 * qq ** i for i in range(4)]
    assert all(Fraction(a, b).numerator == a for a, b in zip(nums, dens)), "分数需天然最简"
    stem2 = "，".join(f"{a}/{b}" for a, b in zip(nums, dens)) + "，（ ）"
    nxt_num, nxt_den = nums[-1] + 2, dens[-1] * qq
    opts2 = [f"{nxt_num}/{nxt_den}", f"{nxt_num}/{nxt_den * 2}",
             f"{nxt_num - 1}/{nxt_den}", f"{nxt_num + 2}/{nxt_den}"]
    add("number_series", "ns.numer-denom", stem2, opts2, f"{nxt_num}/{nxt_den}",
        "分子等差+分母等比")


# ---------------------------------------------------------------- 泄漏检查
def _tokens(text):
    toks = set()
    for seg in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", text or ""):
        if re.match(r"[a-zA-Z0-9]", seg):
            toks.add(seg.lower())
        else:
            toks.update(list(seg))
            toks.update(seg[i:i + 2] for i in range(len(seg) - 1))
    return toks


def leak_check(existing_texts):
    """两级判定：sim≥0.7 视为近重复**丢弃该题**；0.55–0.7 记为软标记留档人工复核。"""
    flagged, drop = [], set()
    for item in ITEMS:
        t = _tokens(item["stem"])
        for eid, et in existing_texts:
            e = _tokens(et)
            j = len(t & e) / max(1, min(len(t), len(e)))
            if j >= 0.7:
                drop.add(item["id"])
                flagged.append({"item": item["id"], "near": eid, "similarity": round(j, 3),
                                "action": "dropped"})
            elif j > 0.55:
                flagged.append({"item": item["id"], "near": eid, "similarity": round(j, 3),
                                "action": "soft_flag"})
    if drop:
        kept = [i for i in ITEMS if i["id"] not in drop]
        for n, it in enumerate(kept, 1):
            it["id"] = f"rb-{n:02d}"
        ITEMS[:] = kept
    return flagged


def main():
    gens = [gen_discount, gen_discount, gen_concentration, gen_concentration,
            gen_restore_price, gen_restore_price, gen_travel, gen_work,
            gen_base_period, gen_base_period, gen_growth_amount, gen_growth_amount,
            gen_logic_direction, gen_logic_direction, gen_series, gen_series]
    for fn in gens:
        fn()
    existing = []
    for p in (ROOT / "data/benchmark_xingce_dev/dev/xingce_dev.jsonl",
              ROOT / "data/benchmark_xingce/test/xingce.jsonl"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                existing.append((row.get("id"), row.get("stem", "")))
    flagged = leak_check(existing)
    out_dir = ROOT / "data/benchmark_xingce_blind/test"
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in ITEMS:
        row = {"id": item["id"], "skill": item["skill"],
               "stem": item["stem"], "options": item["options"],
               "answer": item["answer"], "answerability": "has_unique_answer",
               "expected_status": "solved", "tags": [item["method_id"], "blind"],
               "material": None, "regions": {}}
        lines.append(json.dumps(row, ensure_ascii=False))
    body = "\n".join(lines) + "\n"
    (out_dir / "xingce_blind.jsonl").write_text(body, encoding="utf-8")
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    meta = {"count": len(ITEMS), "sha256_16": sha, "seed": 20261020,
            "frozen_at": "2026-10-20",
            "leak_flags": flagged,
            "note": "gold-first 生成（方法卡公式+Fraction 计算金标），先于任何评测冻结；"
                    "泄漏检查两级：sim≥0.7 自动丢弃（action=dropped），0.55–0.7 软标记留档复核；全部列出不隐藏"}
    (out_dir / "frozen_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[blind] {len(ITEMS)} 题 sha={sha} leak_flags={len(flagged)}")
    for f in flagged[:8]:
        print("  FLAG", f)


if __name__ == "__main__":
    main()
