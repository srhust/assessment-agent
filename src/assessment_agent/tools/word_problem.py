"""自然语言数量关系理解层（R2c）：中文应用题题干 → 结构化计算计划 → 确定性求值。

设计纪律：
- 本层只做**语义抽取**（数量/单位/关系），产出带中间变量的计算步骤链；
  计算一律交给 ``math_tool.fraction_eval``（Fraction 精确算术），**绝不由文本层直接给答案**；
- 明确支持范围（见 SUPPORTED_KINDS），范围外/信息不足/单位不一致（无换算表）
  一律返回 None——上层按能力缺口诚实失败，不允许猜答案；
- LLM 生成的计算计划若未来接入，也必须经过同一 ``evaluate_plan`` 确定性复核，
  本模块就是那道确定性闸门。

支持题型（公务员行测数字推理常见子类）：
1. 求一个数的百分之几（pct_of）：``480 的 35%``
2. 折扣（discount）：``原价 260 元打八折后实际支付多少``（含一~九五折/任意数字折扣）
3. 速度—时间—距离（speed_distance）：``1.5 小时行驶 90 千米，照这样 4 小时行驶多少千米``
4. 单价×数量（unit_price）：``每千克 8.5 元，买 6 千克需要多少元``；反向求单价
5. 比例分配（ratio_share）：``按 3:5 分配 1200 元，最多的一方得多少``
6. 逆向百分比（pct_reverse）：``甲数 480，比乙数少 20%，乙数是多少`` → 480/(1-0.2)
7. R9-C 浓度（concentration）：``300 克浓度 12% 加 60 克水`` 盐守恒
8. R9-C 行程（travel_meeting/travel_pursuit）：相向÷速度和、同向÷速度差
9. R9-C 多阶段百分变化（compound_pct）：连续降价/提价打折 → 乘法复合禁止相加
10. R9-C 工程合作（work_together）：1÷(1/a+1/b)（仅两独立完成者同时开工）
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_KINDS = ("pct_of", "discount", "speed_distance", "unit_price", "ratio_share", "pct_reverse",
                   "concentration", "travel_meeting", "travel_pursuit", "travel_pursuit_lead",
                   "compound_pct", "net_compound", "pct_restore", "work_together",
                   "unit_count", "map_scale", "plan_actual_two_step")

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "半": 5}

_NUM = r"(\d+(?:\.\d+)?)"
_TIME_UNITS = {"小时": Fraction(1), "分钟": Fraction(1, 60), "分": Fraction(1, 60)}
_DIST_UNITS = {"千米": 1.0, "公里": 1.0}


def _discount_factor(raw: str) -> Optional[Fraction]:
    """八折→8/10；5折→1/2；8.5折→0.85。"""
    if raw in _CN_DIGITS:
        return Fraction(_CN_DIGITS[raw], 10)
    try:
        value = float(raw)
    except ValueError:
        return None
    if 0 < value < 10:
        return Fraction(str(value)) / 10
    return None


def _plan(kind: str, steps: List[Tuple[str, str, str]], final_var: str) -> Dict[str, Any]:
    return {"kind": kind, "steps": steps, "final_var": final_var}


def extract_word_problem(stem: str) -> Optional[Dict[str, Any]]:
    """题干 → 计算计划（无法可靠抽取返回 None，绝不猜测）。"""
    text = (stem or "").replace("，", ",").replace("。", " ")
    if not text.strip():
        return None

    # 0a. R9-C 浓度加溶质/加水/蒸发（concentration）：盐=质量×浓度 守恒
    m = re.search(_NUM + r"\s*(?:克|千克|公斤)[^0-9%]{0,8}?浓度\s*" + _NUM + r"\s*[%％]", text)
    if m:
        mass, c = m.group(1), m.group(2)
        add = re.search(r"(?:加入|加|倒入)\s*" + _NUM + r"\s*(?:克|千克|公斤)的?\s*(水|盐)", text)
        evap = re.search(r"蒸发\s*(?:掉)?\s*" + _NUM + r"\s*(?:克|千克|公斤)", text)
        if add and "浓度" in text:
            x, kind = add.group(1), add.group(2)
            if kind == "盐":
                steps = [
                    ("salt", f"{mass}*{c}/100", f"原溶质 = {mass}×{c}%"),
                    ("result", f"(salt+{x})/({mass}+{x})", "新浓度=(盐+加盐)/(总+加盐)"),
                ]
            else:
                steps = [
                    ("salt", f"{mass}*{c}/100", f"原溶质 = {mass}×{c}%"),
                    ("result", f"salt/({mass}+{x})", "新浓度=盐/(总+加水)"),
                ]
            return _plan("concentration", steps, "result")
        if evap and "浓度" in text:
            w = evap.group(1)
            steps = [
                ("salt", f"{mass}*{c}/100", f"原溶质 = {mass}×{c}%"),
                ("result", f"salt/({mass}-{w})", "新浓度=盐/(总-蒸发水)"),
            ]
            return _plan("concentration", steps, "result")

    # 0b. R9-C 行程相遇/追及（travel）：相向÷速度和、同向÷速度差；
    # 追及支持"先出发 t 单位"的领先距离（d=慢速×领先时间）
    dist_m = re.search(r"(?:相距|距离|相隔)\s*" + _NUM + r"\s*(?:千米|公里|米)", text)
    speeds = [(a, u) for a, u in
              re.findall(_NUM + r"\s*(?:千米|公里|米)/(小时|分钟|分)", text)]
    if len(speeds) >= 2:
        u0 = speeds[0][1]
        v1, v2 = Fraction(speeds[0][0]), Fraction(speeds[1][0])
        if dist_m:
            d = Fraction(dist_m.group(1))
            if any(w in text for w in ("相向", "相对而行", "相对出发")):
                steps = [("result", f"{d}/({v1.numerator}/{v1.denominator}+{v2.numerator}/{v2.denominator})",
                          f"相遇时间 = 距离 {d} ÷ 速度和（单位与 {u0} 一致）")]
                return _plan("travel_meeting", steps, "result")
            if any(w in text for w in ("追及", "追上", "同向出发", "追赶")) and v1 != v2:
                hi, lo = max(v1, v2), min(v1, v2)
                steps = [("result", f"{d}/({hi.numerator}/{hi.denominator}-{lo.numerator}/{lo.denominator})",
                          f"追及时间 = 距离 {d} ÷ 速度差")]
                return _plan("travel_pursuit", steps, "result")
        else:
            # 领先式追及：慢者先出发 t，快者追及时间 = 慢速×t ÷ 速度差
            lead = re.search(r"先出发\s*" + _NUM + r"\s*(分钟|小时|天)", text)
            if lead and any(w in text for w in ("追及", "追上", "追赶")):
                slow = min(v1, v2)
                t = Fraction(lead.group(1))
                hi = max(v1, v2)
                if hi != slow:
                    steps = [("lead_d", f"{slow.numerator}/{slow.denominator}*{t.numerator}/{t.denominator}",
                              f"领先距离 = 慢速 × 先出发 {t}"),
                             ("result", f"lead_d/({hi.numerator}/{hi.denominator}-{slow.numerator}/{slow.denominator})",
                              "追及时间 = 领先距离 ÷ 速度差")]
                    return _plan("travel_pursuit_lead", steps, "result")

    # 0c. R9-C 多阶段百分变化（compound_pct）：≥2 个乘性因子逐步复合，禁止相加
    changes = re.findall(r"(降价|涨价|提价|加价|提高|增长|降低|减少)\s*" + _NUM + r"\s*[%％]", text)
    zhe_factors = [_discount_factor(raw) for raw in
                   re.findall(r"打\s*([一二两三四五六七八九半]|\d{1,2}(?:\.\d)?)\s*折", text)]
    base_m = re.search(r"(?:原价|标价|定价|成本价?|单价)\s*(?:为|是)?\s*" + _NUM + r"\s*(?:元|千克|克)?", text)
    factors: List[str] = []
    descs: List[str] = []
    for word, p in changes:
        sign = "-" if word in ("降价", "降低", "减少") else "+"
        factors.append(f"(1{sign}{p}/100)")
        descs.append(f"{word}{p}%→乘(1{sign}{p}/100)")
    for f in zhe_factors:
        if f is not None:
            factors.append(f"({f.numerator}/{f.denominator})")
            descs.append(f"打{float(f) * 10:g}折")
    if base_m and len(factors) >= 2:
        b = base_m.group(1)
        steps = [("result", "*".join([b] + factors),
                  f"基准 {b} 逐步复合：" + "；".join(descs) + "（乘法复合，禁止百分数相加）")]
        return _plan("compound_pct", steps, "result")

    # 0c-2. 无基准的纯率链（net_compound）：连续增长率本身 → (1+p1)(1+p2)−1
    if (not base_m and len(changes) >= 2
            and all(w in ("增长", "提高", "降低", "减少") for w, _ in changes)
            and re.search(r"(?:增长|降低|提高|减少)\s*(?:了)?\s*(?:多少|约多少|几)|增长约", text)):
        prod = "*".join(f"(1{'-' if w in ('降低', '减少') else '+'}{p}/100)" for w, p in changes)
        steps = [("result", f"abs({prod}-1)",
                  "净变化率 = (1+p1)(1+p2)…−1 的绝对值：" + "；".join(descs))]
        # fraction_eval 无 abs：按方向改写显式差值
        if all(w in ("增长", "提高") for w, _ in changes):
            steps = [("result", f"({prod})-1",
                      "净增长率 = (1+p1)(1+p2)−1（乘法复合，禁止相加）")]
        elif all(w in ("降低", "减少") for w, _ in changes):
            steps = [("result", f"1-({prod})",
                      "净降低率 = 1−(1−p1)(1−p2)（乘法复合，禁止相加）")]
        else:
            return None          # 混合升降问净变动方向不明 → 如实弃权
        return _plan("net_compound", steps, "result")

    # 0c-3. 降价后恢复原价的反向变动率（pct_restore）：r/(1−r)
    m = re.search(r"(?:降价|降低|减少)[^0-9]{0,4}" + _NUM + r"\s*[%％][^0-9]{0,24}"
                  r"(?:恢复原价|回到原价|恢复原价)[^0-9]{0,24}(?:提价|涨价|增长|增加)?"
                  r"[^0-9]{0,8}(?:多少|几)", text)
    if m:
        p = m.group(1)
        steps = [("result", f"({p}/100)/(1-{p}/100)",
                  f"降幅 {p}% → 恢复原价需反向变动 ({p}/100)÷(1−{p}/100)")]
        return _plan("pct_restore", steps, "result")

    # 0d. R9-C 工程合作（work_together）：1/(1/a+1/b)，仅限两独立完成者同时开工
    w = re.search(r"甲[^0-9]{0,8}?(\d+)\s*天[^0-9]{0,14}?乙[^0-9]{0,8}?(\d+)\s*天", text)
    if w and "合作" in text and not re.search(r"\d+\s*天.{0,12}\d+\s*天", text[w.end():]):
        a, b = w.group(1), w.group(2)
        steps = [("result", f"1/(1/{a}+1/{b})", f"合作工时 = 1 ÷ (1/{a} + 1/{b})")]
        return _plan("work_together", steps, "result")

    # 1. 求 a 的 p%
    match = re.search(_NUM + r"\s*的\s*" + _NUM + r"\s*[%％]", text)
    if match:
        a, p = match.group(1), match.group(2)
        return _plan("pct_of", [("result", f"{a}×{p}/100", f"{a} 的 {p}%")], "result")

    # 2. 折扣：价格 打 X 折
    match = re.search(_NUM + r"\s*(?:元)?[^0-9%]{0,14}?打\s*([一二两三四五六七八九半]|\d{1,2}(?:\.\d)?)\s*折", text)
    if match:
        a, raw = match.group(1), match.group(2)
        factor = _discount_factor(raw)
        if factor is not None:
            return _plan(
                "discount",
                [("result", f"{a}*{factor.numerator}/{factor.denominator}", f"原价 {a} × 折扣 {raw}折"), ],
                "result",
            )

    # 3. 速度—时间—距离（照这样计算）
    first = re.search(_NUM + r"\s*(小时|分钟|分)[^0-9]{0,14}(?:行驶|走|行|前进|跑了?)?\s*" + _NUM + r"\s*(千米|公里)", text)
    second = re.search(_NUM + r"\s*(小时|分钟|分)[^0-9]{0,14}(?:行驶|走|行|前进|跑了?)?\s*(?:多少|几)\s*(千米|公里)", text)
    if first and second and "照这样" in text:
        t1, d1 = first.group(1), first.group(3)
        u_t1, u_t2 = first.group(2), second.group(2)
        t2 = second.group(1)
        if u_t1 in _TIME_UNITS and u_t2 in _TIME_UNITS:
            # 单位统一到小时（Fraction 精确换算）
            s1 = _TIME_UNITS[u_t1].numerator, _TIME_UNITS[u_t1].denominator
            t1_norm = f"({t1}*{s1[0]}/{s1[1]})"
            s2 = _TIME_UNITS[u_t2].numerator, _TIME_UNITS[u_t2].denominator
            t2_norm = f"({second.group(1)}*{s2[0]}/{s2[1]})"
            steps = [
                ("v", f"{d1}/{t1_norm}", f"速度 = 距离 {d1} / 时间 {t1}{u_t1}（小时）"),
                ("result", f"v*{t2_norm}", f"距离 = 速度 × 时间 {second.group(1)}{u_t2}"),
            ]
            return _plan("speed_distance", steps, "result")

    # 4. 单价 × 数量（"每千克苹果 8.5 元，买 6 千克"——单位词与数字间允许商品名）
    match = re.search(r"每(?:个|件|支|本|张|千克|公斤|斤|米|台)[^0-9%]{0,10}?" + _NUM + r"\s*元[^0-9]{0,22}?(\d+)\s*(?:个|件|支|本|张|千克|公斤|斤|米|台)", text)
    if match:
        price, qty = match.group(1), match.group(2)
        return _plan("unit_price", [("result", f"{price}*{qty}", f"单价 {price} × 数量 {qty}")], "result")
    match = re.search(_NUM + r"\s*元[^0-9]{0,14}?(?:买|购|可买)\s*(\d+)\s*(?:个|件|支|本|张|千克|公斤|斤|米|台)[^0-9]{0,16}?(?:每|单价)", text)
    if match:
        total, qty = match.group(1), match.group(2)
        return _plan("unit_price", [("result", f"{total}/{qty}", f"总价 {total} ÷ 数量 {qty}")], "result")

    # 4b. R9-C 单位数量 × 组数（unit_count）：每箱 n 个 × m 箱
    match = re.search(r"每(箱|盒|袋|份|组|批|排|列|捆)[^0-9]{0,12}?" + _NUM +
                      r"\s*(个|件|本|支|张|名|人|只)[^0-9]{0,20}?" + _NUM +
                      r"\s*(?:箱|盒|袋|份|组|批|排|列|捆)[^0-9]{0,10}?(?:共|总|一共|多少)", text)
    if match:
        per, groups = match.group(2), match.group(4)
        return _plan("unit_count", [("result", f"{per}*{groups}",
                                    f"每组 {per} × {groups} 组")], "result")

    # 5. 比例分配（最多/最小的一方）
    match = re.search(r"按\s*(\d+)\s*[:：∶]\s*(\d+)(?:\s*[:：∶]\s*(\d+))?\s*(?:分配|分成|比|分给)", text)
    if match:
        parts = [int(g) for g in match.groups() if g]
        total_match = re.search(_NUM + r"\s*(?:元|个|本|张|千克|公斤|斤)[^0-9]{0,30}?(?:最多|最大|最少|最小|最少的一方|最大的一方)?", text)
        if total_match and len(parts) >= 2:
            total = total_match.group(1)
            pick = ("max" if any(w in text for w in ("最多", "最大"))
                    else "min" if any(w in text for w in ("最少", "最小"))
                    else "mid" if any(w in text for w in ("中间", "居中", "居中的")) else None)
            if pick:
                chosen = (max(parts) if pick == "max" else min(parts) if pick == "min"
                          else sorted(parts)[len(parts) // 2])
                denom = "+".join(str(p) for p in parts)
                return _plan(
                    "ratio_share",
                    [("result", f"{total}*{chosen}/({denom})", f"总量 {total} × 份额 {chosen}/{denom}（{pick}方）")],
                    "result",
                )

    # 6b. R9-C 计划/实际两步（plan_actual_two_step）：
    # ``原计划 N 天生产 T 个，实际每天比原计划多 D 个，实际每天?`` → T/N (+/- D)
    m_plan = re.search(r"计划\s*" + _NUM + r"\s*天[^0-9]{0,14}?生产\s*" + _NUM + r"\s*(个|件|台|只|套)", text)
    m_delta = re.search(r"每天比[^0-9]{0,10}(?:原)?计划\s*(多|少)[^0-9]{0,6}" + _NUM + r"\s*(个|件|台|只|套)", text)
    if m_plan and m_delta and re.search(r"实际每天[^0-9]{0,6}多少", text):
        days, total = m_plan.group(1), m_plan.group(2)
        sign = "+" if m_delta.group(1) == "多" else "-"
        d = m_delta.group(2)
        steps = [
            ("plan_daily", f"{total}/{days}", f"原计划日产量 = {total}÷{days}"),
            ("result", f"(plan_daily){sign}{d}", f"实际日产量 = 计划日产量 {sign} {d}"),
        ]
        return _plan("plan_actual_two_step", steps, "result")

    # 6c. R9-C 地图比例尺（map_scale）：1∶N 地图图上 d 厘米 → 实际千米（cm→km 精确换算）
    m = re.search(r"1\s*[:：∶]\s*(\d+)\s*(?:的)?地图", text)
    if m:
        cm = re.search(r"图上距离\s*" + _NUM + r"\s*厘米", text)
        if cm and "千米" in text:
            den = m.group(1)
            steps = [("result", f"{cm.group(1)}*{den}/100000",
                      f"图上 {cm.group(1)} 厘米 × 比例尺 {den} ÷100000（厘米→千米）")]
            return _plan("map_scale", steps, "result")

    # 7. 逆向百分比：X 已知，X 比 Y 少/多 p%，求 Y
    match = re.search(
        _NUM + r"[^0-9\d%]{0,40}?比[^0-9\d%]{1,24}?(少|多|低|高|便宜|贵)\s*" + _NUM + r"\s*[%％][^0-9]{0,40}?(?:多少|几)[^0-9]{0,8}[?？]",
        text,
    )
    if match:
        known, sign, p = match.group(1), match.group(2), match.group(3)
        if sign in ("少", "低", "便宜"):
            expr = f"{known}/(1-{p}/100)"
            desc = f"已知量 {known} 比目标少 {p}% → 目标 = {known}/(1-{p}%)"
        else:
            expr = f"{known}/(1+{p}/100)"
            desc = f"已知量 {known} 比目标多 {p}% → 目标 = {known}/(1+{p}%)"
        return _plan("pct_reverse", [("result", expr, desc)], "result")

    return None


def evaluate_plan(plan: Dict[str, Any]) -> Tuple[Optional[Dict[str, Fraction]], Optional[str]]:
    """按步骤链确定性求值（Fraction 精确算术；变量按声明顺序注入）。

    返回 ({"var": Fraction, ...}, None) 或 (None, "失败原因")——任何一步表达式
    无法求值都如实报错，不回退猜测。
    """
    from .math_tool import fraction_eval

    env: Dict[str, Fraction] = {}
    for var, expression, _desc in plan["steps"]:
        substituted = expression
        for name, value in env.items():
            substituted = re.sub(rf"\b{re.escape(name)}\b", f"({value.numerator}/{value.denominator})", substituted)
        try:
            env[var] = fraction_eval(substituted)
        except ZeroDivisionError:
            return None, f"步骤 {var}={expression} 除零（条件矛盾）"
        except Exception as exc:  # noqa: BLE001 - 抽取错也要可见
            return None, f"步骤 {var}={expression} 求值失败: {type(exc).__name__}: {exc}"
    final = plan["final_var"]
    if final not in env:
        return None, f"计划缺少最终变量 {final}"
    return env, None
