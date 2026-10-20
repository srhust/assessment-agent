"""R7 C2：文字材料 → 统一事实表表示（复用 data_plan 证据绑定，不重写计算器）。

中文资料分析的文字材料句式高度规约：
  "2023 年某区文化体育支出 23 亿元，比上年增长 15%。同年教育支出 41 亿元，增长 8%。"
本模块把它规约成与 ``table_tool.normalize_table`` 同构的表（label 行、数值列、
增长率列），使 TableContext / data_plan 的基期、增长率、比重、倍数运算与
证据绑定机制**原样复用**——评审工单 §C2 要求。

确定性纯规则提取（零 LLM）：
- 数值：`\d+(\.\d+)? + 单位`（亿元/万元/万人/吨/套…）；
- 变化率：增长/下降/上升/减少/提高/降低 X%（同比/环比/比上年等关键词保留）；
- 年度：`20XX 年`，"同年/上年/去年/今年"按最近显式年份推移；
- 指标名：数值前最近的名词短语（截断助词"约/达/为/共"）。
无法可靠规约（无数值或无指标）返回 None——调用方维持原诚实失败。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_UNIT = r"(?:亿元|万元|亿元?|万人|万人次|万吨|万吨|万套|万平方米|公里|吨|套|亿元|亿|万|元)"
_VALUE_RE = re.compile(rf"([\d]+(?:\.\d+)?)\s*{_UNIT}")
_RATE_RE = re.compile(
    r"(?:同比|环比|比上年|较去年|比上一年|较上年)?\s*"
    r"(增长|上升|提高|增长|下降|减少|降低)\s*(?:了|约|近)?\s*([\d]+(?:\.\d+)?)\s*(%|个百分点)")
_YEAR_RE = re.compile(r"(20\d{2})\s*年")
_REL_YEAR = {"今年": 0, "去年": -1, "上年": -1, "前年": -2}
_IND_TAIL = re.compile(r"([一-龥A-Za-z0-9]{2,16}?)\s*(?:约|达|为|共|达到|接近|突破)?\s*$")


class Fact:
    def __init__(self, indicator: str, year: str, value: str, unit: str,
                 rate: Optional[str], rate_delta: Optional[float], rate_kind: str):
        self.indicator, self.year, self.value, self.unit = indicator, year, value, unit
        self.rate, self.rate_delta, self.rate_kind = rate, rate_delta, rate_kind


def extract_facts(stem: str) -> List[Fact]:
    facts: List[Fact] = []
    if not stem:
        return facts
    cur_year: Optional[str] = None
    segments = re.split(r"[。；;！!?\n]", stem)
    for seg in segments:
        if not seg.strip():
            continue
        ym = _YEAR_RE.search(seg)
        if ym:
            cur_year = ym.group(1)
        else:
            for token, shift in _REL_YEAR.items():
                if token in seg and cur_year:
                    cur_year = str(int(cur_year) + shift)
                    break
        vm = _VALUE_RE.search(seg)
        if not vm:
            continue
        # 指标名：数值前的尾段名词短语
        before = seg[:vm.start()]
        before = _YEAR_RE.sub("", before)
        for token in _REL_YEAR:
            before = before.replace(token + "的", "").replace(token, "")
        before = re.sub(r"^[，,、：:\s]*(?:同年|其中|另外|此外|据统计|数据显示)[，,、\s]*", "", before)
        im = _IND_TAIL.search(before.strip("，,、：: "))
        indicator = (im.group(1) if im else before[-12:]).strip("，,、。的")
        if len(indicator) < 2:
            continue
        rm = _RATE_RE.search(seg)
        rate = rate_delta = rate_kind = None
        if rm:
            rate = rm.group(0).strip()
            val = float(rm.group(2))
            rate_delta = val if rm.group(1) in ("增长", "上升", "提高") else -val
            rate_kind = "ppt" if rm.group(3) == "个百分点" else "percent"
        unit_m = re.search(rf"{vm.group(1)}\s*({_UNIT})", vm.group(0))
        facts.append(Fact(indicator, cur_year or "", vm.group(1),
                          unit_m.group(1) if unit_m else "",
                          rate, rate_delta, rate_kind or ""))
    return facts


def build_fact_table(stem: str) -> Optional[Tuple[List[List[str]], str]]:
    """返回 (cells 矩阵, 表头单位)，交给 normalize_table 规约；无可提取事实返回 None。"""
    facts = extract_facts(stem)
    if not facts:
        return None
    units = {f.unit for f in facts if f.unit}
    unit = "亿元" if "亿元" in units else (next(iter(units)) if units else "")
    years = sorted({f.year for f in facts if f.year}) or ["现期"]
    has_rate = any(f.rate for f in facts)
    # 两行表头（与 table_tool 表头/数据判定规约一致）：
    # 行1 = 时间 token（纯年份，不带单位），行2 = 指标名（首格留空视作跨列续头）
    header1 = [""] + [f"{y}年" for y in years] + ([""] if has_rate else [])
    header2 = ["指标"] + [f"数值（{unit}）" if unit else "数值" for _ in years] \
        + (["同比增速"] if has_rate else [])
    rows: List[List[str]] = [header1, header2]
    by_ind: Dict[str, Dict[str, Fact]] = {}
    for f in facts:
        by_ind.setdefault(f.indicator, {})[f.year or "现期"] = f
    # 行名剥离材料主语前缀（"某市新能源汽车产量"→"新能源汽车产量"）：
    # 问句通常只引用指标本身，前缀会阻断 pick_indicator_row 的包含匹配。
    def _row_name(name: str) -> str:
        stripped = re.sub(r"^某(?:市|省|区|县|集团|企业|公司|单位)?(?=[\u4e00-\u9fff])", "", name)
        return stripped if len(stripped) >= 2 else name
    for indicator, ymap in by_ind.items():
        row = [_row_name(indicator)]
        for y in years:
            row.append(ymap[y].value if y in ymap else "")
        if has_rate:
            f0 = next(iter(ymap.values()))
            if f0.rate and f0.rate_delta is not None:
                sign = "" if f0.rate_delta >= 0 else "-"
                row.append(f"{sign}{abs(f0.rate_delta):g}%")
            else:
                row.append("")
        rows.append(row)
    if len(rows) < 3:  # 两行表头 + 至少一行数据
        return None
    return rows, unit
