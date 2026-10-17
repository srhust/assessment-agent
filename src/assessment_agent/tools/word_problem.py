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
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_KINDS = ("pct_of", "discount", "speed_distance", "unit_price", "ratio_share", "pct_reverse")

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

    # 5. 比例分配（最多/最小的一方）
    match = re.search(r"按\s*(\d+)\s*[:：]\s*(\d+)(?:\s*[:：]\s*(\d+))?\s*(?:分配|分成|比)", text)
    if match:
        parts = [int(g) for g in match.groups() if g]
        total_match = re.search(_NUM + r"\s*(?:元|个|本|张|千克|公斤|斤)[^0-9]{0,30}?(?:最多|最大|最少|最小|最少的一方|最大的一方)?", text)
        if total_match and len(parts) >= 2:
            total = total_match.group(1)
            pick = "max" if any(w in text for w in ("最多", "最大")) else "min" if any(w in text for w in ("最少", "最小")) else None
            if pick:
                chosen = max(parts) if pick == "max" else min(parts)
                denom = "+".join(str(p) for p in parts)
                return _plan(
                    "ratio_share",
                    [("result", f"{total}*{chosen}/({denom})", f"总量 {total} × 份额 {chosen}/{denom}（{pick}方）")],
                    "result",
                )

    # 6. 逆向百分比：X 已知，X 比 Y 少/多 p%，求 Y
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
