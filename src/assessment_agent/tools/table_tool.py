"""R3a 资料分析：表格结构化（normalize_table）。

把 vision 抽取的原始 cells（List[List[str]]）标准化为可查询结构：
- 多层表头（最多 3 行）合并为列名，时间列（年份/季度/月份）显式打标；
- 单元格数值 Fraction 精确解析，单位（万/亿、元/件/人…）与数据类型
  （money/quantity/percent/ppt）分离；列内单位冲突、OCR 数字歧义、
  数字缺失都记录为**结构化失败证据**，由调用方在引用时如实失败——
  本工具绝不猜测缺失/歧义数字。

序列化注意：cell["fraction"] 是内存对象（Fraction），写进 meta 前需转成 value 字符串。
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

_FULLWIDTH = str.maketrans("０１２３４５６７８９．％－（）", "0123456789.%-()")

_QUANTITY_UNITS = ("人", "件", "个", "户", "套", "吨", "千克", "公斤", "公里", "千米", "米", "亩", "台", "辆", "次", "天", "小时")
_MONEY_UNITS = ("元", "万元", "亿元", "万亿元")
_SCALE = {"万": Fraction(10**4), "亿": Fraction(10**8), "万亿": Fraction(10**12)}

_TIME_RE = re.compile(r"(20\d{2})\s*年|第?\s*([一二三四1-4])\s*(?:季度|季)|(\d{1,2})\s*月")
_TIME_FULL = re.compile(r"^(?:19|20)\d{2}\s*年?$|^[第]?\s*[一二三四1-4]\s*季(?:度)?$|^\d{1,2}\s*月$")

_NUM_CORE = r"\d+(?:,\d{3})*(?:\.\d+)?"


def normalize_text(text: str) -> str:
    """检索/比较用归一：全角转半角、去空白与常见标点。"""
    out = str(text).translate(_FULLWIDTH)
    return re.sub(r"[\s，。、；：·\-—_/()（）\[\]【】]", "", out)


def cell_time_token(text: str) -> Optional[str]:
    m = _TIME_RE.search(str(text))
    if not m:
        return None
    if m.group(1):
        return m.group(1)
    if m.group(2):
        digit = {"一": "1", "二": "2", "三": "3", "四": "4"}.get(m.group(2), m.group(2))
        return f"Q{digit}"
    return f"M{int(m.group(3))}"


def _is_time_token(text: str) -> bool:
    return bool(_TIME_FULL.match(str(text).strip()))


def parse_cell_value(raw: str, column_unit: str = "") -> Dict[str, Any]:
    """解析一个单元格。

    kind/dimension：percent（%）、ppt（百分点）、multiple（倍）、money、quantity、
    number（无量纲）、label（非数值文本）；missing/ambiguous 返回 ok=False + failure。
    """
    text = str(raw).translate(_FULLWIDTH).strip()
    if text == "" or text in {"-", "—", "－", "/", "N/A", "n/a"}:
        return {"ok": False, "kind": "missing", "failure": {"code": "cell_missing", "raw": str(raw)}}
    # OCR 典型歧义：字母混进数字串 / 多个小数点 / 千分位组距错误
    if re.search(r"\d[lIoOzs]|[lIoOzs]\d", text):
        return {"ok": False, "kind": "ambiguous", "failure": {"code": "digit_ambiguous", "raw": str(raw)}}
    if text.count(".") > 1:
        return {"ok": False, "kind": "ambiguous", "failure": {"code": "digit_ambiguous", "raw": str(raw)}}
    if re.search(r"\d,\d{1,2}(?!\d)", text) or re.search(r"\d,\d{4,}", text):
        return {"ok": False, "kind": "ambiguous", "failure": {"code": "digit_ambiguous", "raw": str(raw)}}
    numbers = re.findall(_NUM_CORE, text)
    if len(numbers) > 1:
        return {"ok": False, "kind": "ambiguous", "failure": {"code": "multi_number", "raw": str(raw)}}
    if not numbers:
        return {"ok": True, "value": None, "kind": "label", "unit": "", "scale": Fraction(1), "dimension": "none"}

    num_str = numbers[0].replace(",", "")
    sign = -1 if re.match(r"^[-－]", text) else 1
    value = sign * Fraction(num_str)
    tail = text[text.find(numbers[0]) + len(numbers[0]):].strip()

    if "%" in text:
        return {"ok": True, "value": value / 100, "display": str(value), "kind": "percent",
                "unit": "%", "scale": Fraction(1), "dimension": "ratio"}
    if "百分点" in text:
        return {"ok": True, "value": value, "display": str(value), "kind": "ppt",
                "unit": "百分点", "scale": Fraction(1), "dimension": "ppt"}
    if "倍" in text:
        return {"ok": True, "value": value, "display": str(value), "kind": "multiple",
                "unit": "倍", "scale": Fraction(1), "dimension": "ratio"}

    unit = tail or column_unit or ""
    scale, base_unit = _resolve_scale(unit)
    if base_unit in _MONEY_UNITS or unit in _MONEY_UNITS:
        dimension, kind = "money", "money"
    elif base_unit in _QUANTITY_UNITS:
        dimension, kind = "quantity", "quantity"
    else:
        dimension, kind = "none", "number"
    return {"ok": True, "value": value * scale, "display": str(value), "kind": kind,
            "unit": base_unit or unit, "display_unit": unit, "scale": scale,
            "dimension": dimension}


def _resolve_scale(unit: str) -> Tuple[Fraction, str]:
    """把"亿元/万人/万吨…"拆成 (倍率, 基础单位)。"""
    u = str(unit).strip()
    if not u:
        return Fraction(1), ""
    for prefix in ("万亿", "亿", "万"):
        if u.startswith(prefix) and len(u) > len(prefix):
            return _SCALE[prefix], u[len(prefix):]
    return Fraction(1), u


def extract_header_unit(header: str) -> str:
    """从列头括号里取单位：如 "销售额(万元)" -> "万元"。"""
    m = re.search(r"[（(]([^）)]{1,8})[）)]", str(header))
    return m.group(1) if m else ""


def _is_data_numeric(text: str) -> bool:
    """表头识别用：真正的数据数值（时间戳记如 2023/一季度 不算数值）。
    OCR 歧义数字（l80/多小数点）也是**数据**而非表头——不能因为解析失败就把它
    并进表头行，否则坏单元格会被静默丢弃。"""
    t = str(text).strip()
    if not t or _is_time_token(t):
        return False
    p = parse_cell_value(t)
    if p.get("ok"):
        return p.get("value") is not None
    return (p.get("failure") or {}).get("code") in {"digit_ambiguous", "multi_number"}


def _row_counts_as_data(row: Sequence[str]) -> bool:
    """行级表头/数据判定：数值或 OCR 歧义数字之外，"行名在、值列全空/占位"的行也是数据行。

    否则带缺失值的行会被静默并进表头（缺失证据消失）。跨列表头续行（首格为空）不受影响。
    """
    if any(_is_data_numeric(c) for c in row):
        return True
    first = str(row[0]).strip() if row else ""
    if not first:
        return False
    rest = [str(c).strip() for c in list(row)[1:]]
    if not rest:
        return False
    return any((not r) or r in {"-", "—", "－", "/", "N/A", "n/a"} for r in rest)


def normalize_table(cells: Sequence[Sequence[str]], *, bbox: Optional[List[int]] = None,
                    region_id: str = "tb1") -> Dict[str, Any]:
    """结构化表格。返回 {"ok": True, "table": {...}} 或 {"ok": False, "failure": {...}}。

    局部单元格失败不阻断整表（保留在 cell["failure"]），引用坏单元格时由上层如实失败。
    """
    grid = [[str(c) for c in row] for row in cells]
    if not grid or not grid[0]:
        return {"ok": False, "failure": {"code": "empty_table"}}
    width = len(grid[0])
    if any(len(row) != width for row in grid):
        return {"ok": False, "failure": {"code": "ragged_rows", "detail": {"widths": [len(r) for r in grid]}}}

    header_rows = 0
    for row in grid:
        if header_rows >= 3:
            break
        if any(_is_data_numeric(c) for c in row) or _row_counts_as_data(row):
            break
        header_rows += 1
    if header_rows == 0:
        return {"ok": False, "failure": {"code": "no_header_row", "detail": {"first_row": grid[0]}}}
    if header_rows >= len(grid):
        return {"ok": False, "failure": {"code": "no_data_rows"}}

    columns: List[Dict[str, Any]] = []
    for c in range(width):
        names = [grid[r][c].strip() for r in range(header_rows) if grid[r][c].strip()]
        merged: List[str] = []
        for n in names:
            if not merged or merged[-1] != n:
                merged.append(n)
        name = "·".join(merged)
        unit = ""
        for n in merged:
            u = extract_header_unit(n)
            if u:
                unit = u
                break
        columns.append({
            "index": c,
            "name": name,
            "name_clean": normalize_text(re.sub(r"[（(][^）)]*[）)]", "", name)),
            "time": cell_time_token(" ".join(merged)),
            "header_unit": unit,
        })

    rows: List[Dict[str, Any]] = []
    for r in range(header_rows, len(grid)):
        label = grid[r][0].strip()
        cells_out: List[Dict[str, Any]] = []
        has_number = False
        for c in range(width):
            raw = grid[r][c]
            time_only = _is_time_token(raw)
            parsed = parse_cell_value(raw, columns[c]["header_unit"])
            entry: Dict[str, Any] = {
                "raw": raw, "row": r, "col": c, "ok": bool(parsed.get("ok")),
                "kind": "time" if time_only else parsed.get("kind"),
                "unit": parsed.get("unit", ""),
                "display_unit": parsed.get("display_unit", parsed.get("unit", "")),
                "dimension": parsed.get("dimension", "none"),
                "value": str(parsed["value"]) if parsed.get("value") is not None else None,
            }
            if not parsed.get("ok"):
                entry["failure"] = parsed.get("failure")
            elif parsed.get("value") is not None and not time_only:
                has_number = True
                entry["fraction"] = parsed["value"]
            cells_out.append(entry)
        is_total = bool(re.search(r"合计|总计|总和", label))
        rows.append({
            "index": r, "label": label, "label_clean": normalize_text(label),
            "time": cell_time_token(label), "is_total": is_total, "cells": cells_out,
            "numeric": has_number,
        })

    if not any(row["numeric"] for row in rows):
        return {"ok": False, "failure": {"code": "no_numeric_data"}}

    # 列内单位冲突：同一数据列出现互不一致的 inline 单位，或 inline 量级与表头单位矛盾
    def _magnitude(unit: str) -> Optional[float]:
        for token, val in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4), ("千", 1e3)):
            if token in (unit or ""):
                return val
        return None

    unit_conflicts: List[str] = []
    for c in range(1, width):
        seen = set()
        for row in rows:
            cell = row["cells"][c]
            if not cell.get("value") or cell.get("kind") in {"percent", "ppt", "multiple", "time"}:
                continue
            raw = str(cell["raw"]).translate(_FULLWIDTH).strip()
            if _is_time_token(raw):
                continue
            num = re.findall(_NUM_CORE, raw)[0]
            tail = raw[raw.find(num) + len(num):].strip()
            if tail:
                seen.add(normalize_text(tail))
        header_u = normalize_text(columns[c].get("header_unit") or "")
        head_mag = _magnitude(header_u)
        conflicting = head_mag is not None and any(_magnitude(t) not in (None, head_mag) for t in seen)
        if len(seen) > 1 or conflicting:
            unit_conflicts.append(columns[c]["name"])
            for row in rows:
                if row["cells"][c].get("value"):
                    row["cells"][c]["unit_conflict"] = True

    return {
        "ok": True,
        "table": {
            "region_id": region_id, "bbox": list(bbox) if bbox else None,
            "header_rows": header_rows, "columns": columns, "rows": rows,
            "unit_conflict_columns": unit_conflicts,
        },
    }


def find_rows(table: Dict[str, Any], needle: str) -> List[Dict[str, Any]]:
    n = normalize_text(needle)
    if not n:
        return []
    hits = [row for row in table["rows"]
            if row["label_clean"] and (n in row["label_clean"] or (len(n) >= 2 and row["label_clean"] in n))]
    return hits


def find_columns(table: Dict[str, Any], needle: str = "", time: Optional[str] = None) -> List[Dict[str, Any]]:
    cols = table["columns"]
    if time:
        cols = [c for c in cols if c.get("time") == time]
    if needle:
        n = normalize_text(needle)
        cols = [c for c in cols if n and (n in c["name_clean"] or c["name_clean"] in n)]
    return cols


def get_cell(table: Dict[str, Any], row_index: int, col_index: int) -> Optional[Dict[str, Any]]:
    for row in table["rows"]:
        if row["index"] == row_index:
            for cell in row["cells"]:
                if cell["col"] == col_index:
                    return cell
    return None


def question_times(text: str) -> List[str]:
    """题干里出现的全部时间标记（按出现顺序）。"""
    found: List[str] = []
    for m in _TIME_RE.finditer(str(text)):
        if m.group(1):
            token = m.group(1)
        elif m.group(2):
            token = "Q" + {"一": "1", "二": "2", "三": "3", "四": "4"}.get(m.group(2), m.group(2))
        else:
            token = f"M{int(m.group(3))}"
        if token not in found:
            found.append(token)
    return found
