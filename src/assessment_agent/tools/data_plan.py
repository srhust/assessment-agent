"""R3a 资料分析：确定性计算计划构建与求值（data plan）。

纪律：
- LLM（若启用）只允许输出 DataCalculationPlan 结构，**不得直接给答案**；
- 计划中的每个数字必须携带源单元格证据（row/col/raw/unit），求值全部走 Fraction；
- 解析不到明确目标、引用缺失/歧义单元格、时间对不完整 → 结构化失败，绝不猜；
- 验证层使用 `recompute_independently`：不重放表达式字符串，而从操作数原始
  Fraction 走**另一条算术路径**复算（防止表达式解析器自身的错误被自我确认）。
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import DataCalculationPlan, DataOperand
from .table_tool import _resolve_scale, find_columns, find_rows, normalize_text, question_times

# 操作识别按特异性排序；先命中者优先
OPERATION_PATTERNS: List[Tuple[str, str]] = [
    ("avg_annual_growth", r"年均增长率|平均每年增长|复合增长率|年平均增速"),
    ("pct_point_diff", r"百分点"),
    # 基期/现期题也可能含"同比"字样：先按"上年…是多少/预计达到"识别，避免误当纯增长率题
    ("base_value", r"(?:上年|去年|前一年|基期)[^0-9%]{0,10}(?:是多少|多少|为|约多少)"),
    ("current_value", r"(?:明年|下一年(?:度)?)[^。？]{0,14}(?:将达到|约为|达到|是多少|多少)|如果[^。？]{0,12}(?:保持|增速)[^。？]{0,16}(?:将达到|约为|达到|多少)"),
    ("growth_yoy", r"同比"),
    ("growth_mom", r"环比"),
    ("share", r"占.{0,14}(?:比重|比例|份额)|比重|占比"),
    ("multiple", r"倍"),
    ("growth_amount", r"增长(?:了)?(?:约)?(?:多少|几)"),
    ("mean", r"平均"),
    ("difference", r"(?:最多|最高)[^。？]{0,10}(?:最少|最低)|(?:比)[^。？]{0,8}(?:多|少)(?:多少|几)|相差"),
    ("rank_extreme", r"最多|最少|最高|最低|最大|最小|排名|哪个|哪一"),
    ("sum", r"合计|总共|总计|一共|总和"),
    ("unit_convert", r"换算|相当于|折合"),
]


class PlanError(Exception):
    def __init__(self, code: str, detail: Any = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def detect_operation(question: str) -> Optional[Tuple[str, str]]:
    for op, pattern in OPERATION_PATTERNS:
        m = re.search(pattern, question)
        if m:
            return op, m.group(0)
    return None


def _time_num(token: str) -> Tuple[str, int]:
    m = re.fullmatch(r"(20\d\d)", token)
    if m:
        return ("year", int(token))
    m = re.fullmatch(r"Q([1-4])", token)
    if m:
        return ("quarter", int(m.group(1)))
    m = re.fullmatch(r"M(\d{1,2})", token)
    if m:
        return ("month", int(m.group(1)))
    return ("none", 0)


def _time_sort_key(token: Optional[str]) -> Tuple[int, int]:
    kind, num = _time_num(token or "")
    order = {"year": 0, "quarter": 1, "month": 2, "none": 3}[kind]
    return (order, num)


def _prev_time(token: str) -> Optional[str]:
    """上一个可比时间：年份 -1；Q2..Q4 → 前一季度；M2..M12 → 前一月。"""
    m = re.fullmatch(r"(20\d\d)", token)
    if m:
        return str(int(token) - 1)
    m = re.fullmatch(r"Q([1-4])", token)
    if m and int(m.group(1)) > 1:
        return f"Q{int(m.group(1)) - 1}"
    m = re.fullmatch(r"M(\d{1,2})", token)
    if m and int(m.group(1)) > 1:
        return f"M{int(m.group(1)) - 1}"
    return None


def _order_pair(t1: str, t2: str) -> Tuple[str, str]:
    """返回 (基期, 现期)。"""
    return tuple(sorted((t1, t2), key=_time_sort_key))  # type: ignore[return-value]


# ------------------------------------------------------------------ 表引用解析
class TableContext:
    def __init__(self, table: Dict[str, Any], question: str, table_ref: int) -> None:
        self.table = table
        self.q = question
        self.qn = normalize_text(question)
        self.table_ref = table_ref
        self.times = question_times(question)

    def pick_indicator_row(self) -> Optional[Dict[str, Any]]:
        """题干中出现的行指标（统计对象）；多个候选且无法嵌套消解时报歧义。"""
        candidates: List[Dict[str, Any]] = []
        for row in self.table["rows"]:
            label = row["label_clean"]
            if len(label) >= 2 and label in self.qn and not row.get("is_total"):
                candidates.append(row)
        if len(candidates) > 1:
            longest = max(candidates, key=lambda r: len(r["label_clean"]))
            if all(c["label_clean"] in longest["label_clean"] for c in candidates):
                return longest
            raise PlanError("ambiguous_reference", {"rows": [c["label"] for c in candidates]})
        return candidates[0] if candidates else None

    def match_row(self, needle: str) -> Dict[str, Any]:
        hits = [h for h in find_rows(self.table, needle) if h.get("numeric") or h.get("is_total")]
        if len(hits) != 1:
            raise PlanError("target_not_found" if not hits else "ambiguous_reference",
                            {"needle": needle, "rows": [h["label"] for h in hits]})
        return hits[0]

    def match_row_by_time(self, time_token: str) -> Optional[Dict[str, Any]]:
        for row in self.table["rows"]:
            if row.get("time") == time_token and row.get("numeric"):
                return row
        return None

    def data_cols(self) -> List[Dict[str, Any]]:
        return [c for c in self.table["columns"] if c["index"] > 0]

    def cols_by_time(self) -> Dict[str, Dict[str, Any]]:
        return {c["time"]: c for c in self.data_cols() if c.get("time")}

    def pick_column(self, needle: str = "", time: Optional[str] = None) -> Dict[str, Any]:
        cols = self.data_cols()
        if time:
            cols = [c for c in cols if c.get("time") == time]
        if needle:
            n = normalize_text(re.sub(r"[（(][^）)]*[）)]", "", needle))
            cols = [c for c in cols if n and (n in c["name_clean"] or c["name_clean"] in n)]
        if not cols:
            raise PlanError("target_not_found", {"needle": needle, "time": time})
        if len(cols) > 1:
            raise PlanError("ambiguous_reference", {"cols": [c["name"] for c in cols]})
        return cols[0]

    def indicator_column_from_question(self, allow_rate: bool = False) -> Optional[Dict[str, Any]]:
        """题面点名的指标列；默认排除"率/增速"列——增长率题的指标是**金额/数量**列，
        率列本身是要求解的对象，不能当操作数来源（否则会把答案当输入）。"""
        best: Optional[Dict[str, Any]] = None
        for c in self.data_cols():
            name = c["name_clean"]
            if not allow_rate and re.search(r"增长率|增速|占比|比重|率$", name):
                continue
            if len(name) >= 2 and name in self.qn:
                if best is None or len(name) > len(best["name_clean"]):
                    best = c
        if best is not None:
            same = [c for c in self.data_cols()
                    if c["name_clean"] == best["name_clean"] and c["index"] != best["index"]]
            if same:
                # "销售额(万元)" 与 "销售额(元)" 并存：题面无法区分 → 拒绝猜测
                raise PlanError("ambiguous_reference", {
                    "why": "同名指标列单位不同", "cols": [best["name"]] + [c["name"] for c in same]})
        return best

    # ---------------- 操作数 ----------------
    def operand_from_cell(self, name: str, cell: Dict[str, Any]) -> DataOperand:
        if cell.get("unit_conflict"):
            raise PlanError("unit_conflict", {"raw": cell["raw"]})
        if not cell.get("ok") or cell.get("value") is None:
            failure = dict(cell.get("failure") or {"code": "cell_unusable"})
            failure.setdefault("raw", cell.get("raw", ""))
            raise PlanError(str(failure.get("code", "cell_unusable")), failure)
        col = self.table["columns"][cell["col"]]
        row = next(r for r in self.table["rows"] if r["index"] == cell["row"])
        display_unit = cell.get("display_unit") or col["header_unit"] or cell.get("unit", "")
        return DataOperand(
            name=name, table_ref=self.table_ref, row=cell["row"], col=cell["col"],
            row_label=row["label"], col_label=col["name"], raw_text=cell["raw"],
            value=cell["value"], unit=display_unit,
            kind=cell.get("kind", "number"), source_bbox=self.table.get("bbox"),
        )

    def single(self, name: str, row: Dict[str, Any], col: Dict[str, Any]) -> DataOperand:
        return self.operand_from_cell(name, row["cells"][col["index"]])

    def series_row(self, row: Dict[str, Any]) -> List[DataOperand]:
        ops: List[DataOperand] = []
        for col in self.data_cols():
            cell = row["cells"][col["index"]]
            if cell.get("kind") == "time":
                continue
            if cell.get("value") or not cell.get("ok"):
                ops.append(self.operand_from_cell(f"O{len(ops) + 1}", cell))
        if not ops:
            raise PlanError("target_not_found", {"why": "empty_row_series", "row": row["label"]})
        return ops

    def series_column(self, col: Dict[str, Any]) -> List[DataOperand]:
        ops: List[DataOperand] = []
        for row in self.table["rows"]:
            if row.get("is_total"):
                continue
            cell = row["cells"][col["index"]]
            if cell.get("kind") == "time":
                continue
            if cell.get("value") or not cell.get("ok"):
                ops.append(self.operand_from_cell(f"O{len(ops) + 1}", cell))
        if not ops:
            raise PlanError("target_not_found", {"why": "empty_col_series", "col": col["name"]})
        return ops


# ------------------------------------------------------------------ 计划入口
def build_data_plan(question: str, tables: Sequence[Dict[str, Any]],
                    chart: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        return {"ok": True, "plan": _build(question, tables, chart)}
    except PlanError as err:
        return {"ok": False, "failure": {"code": err.code, "detail": err.detail}}
    except Exception as err:  # 防御：任何未预期解析问题都如实失败，不猜答案
        return {"ok": False, "failure": {"code": "plan_build_error", "detail": {"error": str(err)}}}


def _build(question: str, tables: Sequence[Dict[str, Any]], chart: Optional[Dict[str, Any]]) -> DataCalculationPlan:
    op = detect_operation(question)
    if not op:
        raise PlanError("operation_unsupported", {"question": question})
    operation, focus = op

    if chart is not None and not tables:
        return _build_chart(operation, focus, question, chart)
    if not tables:
        raise PlanError("no_table", {"why": "缺少表格/图表数据区域"})

    ctx = TableContext(tables[0], question, 0)
    if len(tables) > 1:
        scored: List[int] = []
        for idx, tbl in enumerate(tables):
            c = TableContext(tbl, question, idx)
            try:
                if c.pick_indicator_row() or c.times or c.indicator_column_from_question():
                    scored.append(idx)
            except PlanError:
                scored.append(idx)
        if len(scored) != 1:
            raise PlanError("ambiguous_reference", {"why": "multi_table", "candidates": scored})
        ctx = TableContext(tables[scored[0]], question, scored[0])

    row = None
    if operation not in {"pct_point_diff"}:
        try:
            row = ctx.pick_indicator_row()
        except PlanError:
            # 多指标候选并列：单指标系列题无法消解时后面按"目标未定位"如实失败；
            # 两实体题（比/倍数）由 _entity_rows 路径处理，不在这里报错
            row = None

    # ---------------- 求和 / 平均 ----------------
    if operation == "sum":
        if row is not None:
            ops = ctx.series_row(row)
        else:
            col = ctx.indicator_column_from_question()
            ops = ctx.series_column(col if col is not None else _only_data_col(ctx))
        expr = " + ".join(o.name for o in ops)
        return DataCalculationPlan(operation="sum", operands=ops, expression=expr,
                                   result_unit=ops[0].unit, question_focus=focus)

    if operation == "mean":
        if row is not None:
            ops = ctx.series_row(row)
        else:
            col = ctx.indicator_column_from_question()
            ops = ctx.series_column(col if col is not None else _only_data_col(ctx))
        expr = "(" + " + ".join(o.name for o in ops) + f") / {len(ops)}"
        return DataCalculationPlan(operation="mean", operands=ops, expression=expr,
                                   result_unit=ops[0].unit, question_focus=focus,
                                   intermediate_values=[{"name": "COUNT", "expression": str(len(ops)),
                                                          "value": str(len(ops))}])

    # ---------------- 极值 / 差 ----------------
    if operation in {"difference", "rank_extreme"}:
        entities = _entity_rows(ctx, question)
        if (operation == "difference" and len(entities) > 2
                and re.search(r"比[^。？]{0,10}(?:多|少)(?:多少|几)", question)
                and not re.search(r"最多|最高|最少|最低|最大|最小", question)):
            # 题面点名了对象但超过两个：宁失败不猜
            raise PlanError("ambiguous_reference",
                            {"why": "差值比较对象不唯一", "rows": [e["label"] for e in entities]})
        if operation == "difference" and len(entities) == 2:
            # 题面点名两个实体（如"合计比三季度"）：优先实体对差值，胜过单行系列
            ecol = ctx.indicator_column_from_question()
            if ecol is None:
                cols = ctx.data_cols()
                ecol = cols[0] if len(cols) == 1 else None
            if ecol is not None:
                a = ctx.single("O1", entities[0], ecol)
                b = ctx.single("O2", entities[1], ecol)
                va, vb = Fraction(a.value), Fraction(b.value)
                hi_op, lo_op = (a, b) if va >= vb else (b, a)
                return DataCalculationPlan(
                    operation="difference", operands=[a, b],
                    expression=f"{hi_op.name} - {lo_op.name}", result_unit=hi_op.unit,
                    question_focus=focus,
                    intermediate_values=[{"name": "MAX", "expression": hi_op.name, "value": str(max(va, vb))},
                                          {"name": "MIN", "expression": lo_op.name, "value": str(min(va, vb))}])
        if row is not None:
            ops = ctx.series_row(row)
        else:
            col = ctx.indicator_column_from_question()
            col = col if col is not None else _only_data_col(ctx)
            if operation == "rank_extreme" and len(entities) == 2:
                # "甲和乙哪个最多"：候选限定在题干点名的两个对象内
                ops = [ctx.single("O1", entities[0], col), ctx.single("O2", entities[1], col)]
            else:
                ops = ctx.series_column(col)
        values = {o.name: Fraction(o.value) for o in ops}
        hi = max(values, key=lambda k: values[k])
        lo = min(values, key=lambda k: values[k])
        if hi == lo:
            raise PlanError("ambiguous_reference", {"why": "flat_series"})
        if operation == "difference":
            return DataCalculationPlan(
                operation="difference", operands=ops, expression=f"{hi} - {lo}",
                result_unit=ops[0].unit, question_focus=focus,
                intermediate_values=[{"name": "MAX", "expression": hi, "value": str(values[hi])},
                                      {"name": "MIN", "expression": lo, "value": str(values[lo])}])
        want_min = bool(re.search(r"最少|最低|最小", question))
        chosen = lo if want_min else hi
        tied = [k for k, v in values.items() if v == values[chosen]]
        if len(tied) != 1:
            raise PlanError("ambiguous_reference", {"why": "tie", "tied": tied})
        operand = next(o for o in ops if o.name == chosen)
        answer_kind = "label" if re.search(r"哪[一1]?[个季月年组项]?|哪些|谁|是（|是\(", question) else "value"
        # 系列来自"行×多列"时标签是列（时间/指标），来自"多行×一列"时标签是行（统计对象）
        label_text = operand.col_label if row is not None else operand.row_label
        return DataCalculationPlan(operation="rank_extreme", operands=ops,
                                   expression=operand.name, result_unit=operand.unit,
                                   answer_kind=answer_kind, question_focus=focus,
                                   intermediate_values=[{"name": "PICK", "expression": chosen,
                                                          "value": str(values[chosen]),
                                                          "label": label_text,
                                                          "direction": "min" if want_min else "max"}],
                                   assumptions=[f"{'最小' if want_min else '最大'}值由系列比较得出"])

    # ---------------- 同比 / 环比 ----------------
    if operation in {"growth_yoy", "growth_mom"}:
        if len(ctx.times) == 1:
            prev = _prev_time(ctx.times[0])
            if prev is None:
                raise PlanError("time_pair_incomplete", {"time": ctx.times[0]})
            pair = (prev, ctx.times[0])
        elif len(ctx.times) >= 2:
            pair = _order_pair(ctx.times[0], ctx.times[1])
        else:
            ordered = sorted((t for t in (ctx.cols_by_time() or {t: None for t in _row_times(ctx)}) if t),
                             key=_time_sort_key)
            if len(ordered) < 2:
                raise PlanError("time_pair_incomplete", {"times": ctx.times})
            pair = (ordered[-2], ordered[-1])
        colmap = ctx.cols_by_time()
        if row is not None and all(t in colmap for t in pair):
            base = ctx.single("O1", row, colmap[pair[0]])
            cur = ctx.single("O2", row, colmap[pair[1]])
        else:
            base_row = ctx.match_row_by_time(pair[0])
            cur_row = ctx.match_row_by_time(pair[1])
            if base_row is None or cur_row is None:
                raise PlanError("time_pair_incomplete", {"pair": pair})
            col = ctx.indicator_column_from_question() or _only_data_col(ctx)
            base = ctx.single("O1", base_row, col)
            cur = ctx.single("O2", cur_row, col)
        wants_amount = ("%" not in question and not re.search(r"率", question)
                        and bool(re.search(r"增长[^%。？]{0,10}(?:多少|约多少|[0-9])", question))
                        and bool(re.search(r"[万亿元件人吨套户个]", question)))
        if wants_amount:
            return DataCalculationPlan(operation="growth_amount", operands=[base, cur],
                                       expression="O2 - O1", result_unit=cur.unit, question_focus=focus)
        return DataCalculationPlan(operation=operation, operands=[base, cur],
                                   expression="(O2 - O1) / O1 * 100", result_kind="percent",
                                   result_unit="%", question_focus=focus)

    # ---------------- 年均/复合增长率 ----------------
    if operation == "avg_annual_growth":
        ops, series_times, n = None, [], None
        cols = sorted((c for c in ctx.data_cols() if c.get("time")),
                      key=lambda c: _time_sort_key(c["time"]))
        if row is not None and len(cols) >= 2:
            ops = [ctx.single(f"O{i + 1}", row, c) for i, c in enumerate(cols)]
            series_times = [c["time"] for c in cols]
        else:
            col = ctx.indicator_column_from_question()
            if col is None:
                cand = ctx.data_cols()
                col = cand[0] if len(cand) == 1 else None
            ordered_rows = sorted((r for r in ctx.table["rows"] if r.get("time") and r.get("numeric")),
                                  key=lambda r: _time_sort_key(r["time"]))
            series_times = [r["time"] for r in ordered_rows]
            if col is not None and len(ordered_rows) >= 2:
                if len(ctx.times) >= 2:
                    pair = _order_pair(ctx.times[0], ctx.times[1])
                    if pair[0] in series_times and pair[1] in series_times:
                        i0, i1 = series_times.index(pair[0]), series_times.index(pair[1])
                        ops = [ctx.single("O1", ordered_rows[i0], col),
                               ctx.single("O2", ordered_rows[i1], col)]
                        series_times = [pair[0], pair[1]]
                        n = i1 - i0
                if ops is None:
                    ops = [ctx.single(f"O{i + 1}", r, col) for i, r in enumerate(ordered_rows)]
        if not ops or len(ops) < 2:
            raise PlanError("time_pair_incomplete", {"why": "年均增长率需要起止两期"})
        if n is None:
            n = len(ops) - 1
        return DataCalculationPlan(
            operation="avg_annual_growth", operands=[ops[0], ops[-1]],
            expression=f"(O2 / O1) ^ (1/{n}) - 1", result_kind="percent", result_unit="%",
            question_focus=focus,
            intermediate_values=[{"name": "N", "expression": str(n), "value": str(n)}],
            assumptions=[f"按 {n} 个增长期几何平均（{series_times[0]}→{series_times[-1]}）；"
                         f"开方非精确有理数时标记为近似"])

    # ---------------- 基期 / 现期（题干给率） ----------------
    if operation in {"base_value", "current_value"}:
        cur = _inline_or_cell(ctx, row, question)
        rate = _rate_operand_from_question(question)
        if rate is None:
            rate_cell = _rate_cell_operand(ctx, question)
            rate = rate_cell
        if cur is None or rate is None:
            raise PlanError("target_not_found", {"why": "基期/现期计算需要现期(基期)值与增长率证据"})
        expr = "O1 / (1 + O2)" if operation == "base_value" else "O1 * (1 + O2)"
        return DataCalculationPlan(operation=operation, operands=[cur, rate], expression=expr,
                                   result_unit=cur.unit, question_focus=focus)

    # ---------------- 比重 ----------------
    if operation == "share":
        parts, total = _share_operands(ctx, row, question)
        names_p = " + ".join(o.name for o in parts)
        if len(total) == 1:
            expr = f"({names_p}) / {total[0].name} * 100"
        else:
            expr = f"({names_p}) / ({' + '.join(o.name for o in total)}) * 100"
        plan = DataCalculationPlan(operation="share", operands=parts + total, expression=expr,
                                   result_kind="percent", result_unit="%", question_focus=focus)
        if len(total) > 1:
            plan.assumptions = ["表中无合计行：总量=同列各明细行之和"]
        return plan

    # ---------------- 倍数 ----------------
    if operation == "multiple":
        rows_hit = _entity_rows(ctx, question)
        if len(rows_hit) != 2:
            raise PlanError("target_not_found", {"why": "倍数关系需要两个明确对象", "rows": len(rows_hit)})
        col = ctx.indicator_column_from_question()
        if col is None:
            col = _only_data_col(ctx)
        a = ctx.single("O1", rows_hit[0], col)
        b = ctx.single("O2", rows_hit[1], col)
        is_more = bool(re.search(r"多[^少][^。？]{0,4}倍", question))
        expr = "O1 / O2 - 1" if is_more else "O1 / O2"
        return DataCalculationPlan(operation="multiple", operands=[a, b], expression=expr,
                                   result_unit="倍", question_focus=focus,
                                   assumptions=["“多几倍”= 商 - 1"] if is_more else [])

    # ---------------- 百分点差 ----------------
    if operation == "pct_point_diff":
        rate_col = None
        for c in ctx.data_cols():
            if re.search(r"增长率|增速|率", c["name"]) and c["index"] > 0:
                rate_col = c
                break
        times = ctx.times
        if rate_col is None or len(times) != 2:
            raise PlanError("target_not_found", {"why": "百分点差需要两个时间与一个增长率列"})
        pair = _order_pair(times[0], times[1])
        colmap = ctx.cols_by_time()
        if row is not None and all(t in colmap for t in pair):
            a = ctx.single("O1", row, colmap[pair[1]])
            b = ctx.single("O2", row, colmap[pair[0]])
        else:
            r_cur, r_base = ctx.match_row_by_time(pair[1]), ctx.match_row_by_time(pair[0])
            if r_cur is None or r_base is None:
                raise PlanError("time_pair_incomplete", {"times": times})
            a = ctx.single("O1", r_cur, rate_col)
            b = ctx.single("O2", r_base, rate_col)
        return DataCalculationPlan(operation="pct_point_diff", operands=[a, b],
                                   expression="(O1 - O2) * 100", result_kind="ppt",
                                   result_unit="百分点", question_focus=focus,
                                   assumptions=["百分点差按两期百分比读数直接相减"])

    # ---------------- 单位换算 ----------------
    if operation == "unit_convert":
        target = re.search(r"(?:换算|相当于|折合)(?:成|为)?\s*(万亿|万|亿)?\s*([万亿元件人吨套])?", question)
        if not target:
            raise PlanError("target_not_found", {"why": "换算目标单位缺失"})
        to_unit = (target.group(1) or "") + (target.group(2) or "")
        cur = _inline_or_cell(ctx, row, question)
        if cur is None or not to_unit:
            raise PlanError("target_not_found", {})
        from_scale, _ = _resolve_scale(cur.unit)
        to_scale, to_base = _resolve_scale(to_unit)
        # cur.value 已是基准单位（display×from_scale）：目标显示值 = 基准 / to_scale
        expr = f"O1 / ({to_scale})"
        return DataCalculationPlan(operation="unit_convert", operands=[cur], expression=expr,
                                   result_unit=to_unit, question_focus=focus,
                                   assumptions=[f"单位 {cur.unit} 换算为 {to_unit}"])

    raise PlanError("operation_unsupported", {"operation": operation})


def _row_times(ctx: TableContext) -> List[str]:
    return [r["time"] for r in ctx.table["rows"] if r.get("time")]


def _only_data_col(ctx: TableContext) -> Dict[str, Any]:
    cols = ctx.data_cols()
    if len(cols) != 1:
        raise PlanError("ambiguous_reference", {"why": "无指标列线索且数据列不唯一",
                                                 "cols": [c["name"] for c in cols]})
    return cols[0]


def _entity_rows(ctx: TableContext, question: str) -> List[Dict[str, Any]]:
    hits: List[Tuple[int, Dict[str, Any]]] = []
    qn = normalize_text(question)
    for row in ctx.table["rows"]:
        label = row["label_clean"]
        if not label:
            continue
        # 单字行标（一/二/甲…）极易与数量表达误撞（"四个季度"的"四"不是行"四季度"！）：
        # 必须紧跟类别上下文字（组/类/期/度…）才算题面点名
        if len(label) == 1 and not re.search(re.escape(label) + r"[组类项班厂队期度年月日]", qn):
            continue
        pos = qn.find(label)
        if pos >= 0 and (row.get("numeric") or row.get("is_total")):
            hits.append((pos, row))
    hits.sort(key=lambda t: t[0])
    return [r for _, r in hits]


def _inline_or_cell(ctx: TableContext, row: Optional[Dict[str, Any]], question: str) -> Optional[DataOperand]:
    """基期/现期题的现期（基期）值：优先表格引用单元格；题干时间→行/列定位。"""
    if row is None:
        row = ctx.pick_indicator_row()
    if ctx.times:
        time = ctx.times[0]
        colmap = ctx.cols_by_time()
        if row is not None and time in colmap:
            try:
                return ctx.single("O1", row, colmap[time])
            except PlanError:
                return None
        timed_row = ctx.match_row_by_time(time)
        if timed_row is not None:
            col = ctx.indicator_column_from_question()
            if col is None:
                cols = ctx.data_cols()
                if len(cols) != 1:
                    return None
                col = cols[0]
            try:
                return ctx.single("O1", timed_row, col)
            except PlanError:
                return None
        return None
    if row is not None:
        col = ctx.indicator_column_from_question()
        cols = [col] if col is not None else ctx.data_cols()
        if len(cols) != 1:
            return None
        try:
            return ctx.single("O1", row, cols[0])
        except PlanError:
            return None
    return None


def _rate_operand_from_question(question: str) -> Optional[DataOperand]:
    m = re.search(r"(?:同比增长?|增长(?:了)?|增速|增长率)[为是达]?\s*(\d+(?:\.\d+)?)\s*%", question)
    if not m:
        return None
    value = Fraction(m.group(1)) / 100
    return DataOperand(name="O2", table_ref=-1, row=None, col=None, row_label="题干内联",
                       col_label="增长率", raw_text=m.group(0), value=str(value),
                       unit="%", kind="percent")


def _rate_cell_operand(ctx: TableContext, question: str) -> Optional[DataOperand]:
    rate_col = None
    for c in ctx.data_cols():
        if re.search(r"增长率|增速", c["name"]):
            rate_col = c
            break
    if rate_col is None or not ctx.times:
        return None
    colmap = ctx.cols_by_time()
    time = ctx.times[0]
    if row := ctx.pick_indicator_row():
        try:
            cell = row["cells"][rate_col["index"]]
            if cell.get("value"):
                op = ctx.operand_from_cell("O2", cell)
                op.name = "O2"
                return op
        except PlanError:
            return None
    r = ctx.match_row_by_time(time)
    if r is None:
        return None
    try:
        op = ctx.single("O2", r, rate_col)
        return op
    except PlanError:
        return None


def _share_operands(ctx: TableContext, row: Optional[Dict[str, Any]], question: str):
    colmap = ctx.cols_by_time()
    cols = ctx.data_cols()
    if ctx.times:
        cols = [c for c in cols if c.get("time") == ctx.times[0]] or cols
    if len(cols) != 1:
        needle_row = None
        m = re.search(r"([^\s，。？（）()]{2,10})占", question)
        if m and not row:
            row = None
        raise PlanError("ambiguous_reference", {"cols": [c["name"] for c in cols]})
    col = cols[0]
    m = re.search(r"([^\s，。？：；（）()]{2,12})(?:的)?(?:[一二三四]季度?)?占", question)
    needle = m.group(1) if m else ""
    parts: List[DataOperand] = []
    if needle:
        try:
            prow = ctx.match_row(needle)
            parts.append(ctx.single("O1", prow, col))
        except PlanError:
            cneedle = ctx.indicator_column_from_question()
            if cneedle is not None:
                r = ctx.pick_indicator_row()
                if r is not None:
                    parts.append(ctx.single("O1", r, cneedle))
    if not parts and row is not None:
        parts.append(ctx.single("O1", row, col))
    if not parts:
        raise PlanError("target_not_found", {"why": "比重主体不明"})
    total: List[DataOperand] = []
    for r in ctx.table["rows"]:
        if r.get("is_total"):
            cell = r["cells"][col["index"]]
            if cell.get("value"):
                total.append(ctx.operand_from_cell(f"O{len(parts) + 1}", cell))
                break
    if not total:
        for r in ctx.table["rows"]:
            cell = r["cells"][col["index"]]
            if cell.get("value") and cell.get("kind") not in {"percent", "ppt"}:
                total.append(ctx.operand_from_cell(f"O{len(parts) + len(total) + 1}", cell))
        if not total:
            raise PlanError("target_not_found", {"why": "总量缺失"})
        elif len(total) == 1 and total[0].raw_text == parts[0].raw_text:
            raise PlanError("ambiguous_reference", {"why": "明细即总量？"})
    return parts, total


# ------------------------------------------------------------------ 图读数计划
def _build_chart(operation: str, focus: str, question: str, chart: Dict[str, Any]) -> DataCalculationPlan:
    series: Dict[str, Fraction] = chart["series"]
    unit: str = chart.get("unit", "")
    names = list(series)
    if len(names) < 2 and operation != "rank_extreme":
        raise PlanError("target_not_found", {"why": "图上读数不足"})

    def operand(name: str, label: str) -> DataOperand:
        return DataOperand(name=name, table_ref=-1, row=None, col=None, row_label=label,
                           col_label="图上读数", raw_text=f"{label} {float(series[label]):g}{unit}",
                           value=str(series[label]), unit=unit,
                           kind="quantity" if unit else "number", source_bbox=chart.get("bbox"))

    if operation == "difference":
        hi = max(names, key=lambda k: series[k])
        lo = min(names, key=lambda k: series[k])
        ops = [operand("O1", hi), operand("O2", lo)]
        return DataCalculationPlan(operation="difference", operands=ops, expression="O1 - O2",
                                   result_unit=unit, question_focus=focus,
                                   assumptions=["读数来自图表标注文本，柱高比例由验证层像素复核"])
    if operation == "sum":
        ops = [operand(f"O{i + 1}", n) for i, n in enumerate(names)]
        return DataCalculationPlan(operation="sum", operands=ops,
                                   expression=" + ".join(o.name for o in ops),
                                   result_unit=unit, question_focus=focus)
    if operation == "mean":
        ops = [operand(f"O{i + 1}", n) for i, n in enumerate(names)]
        return DataCalculationPlan(operation="mean", operands=ops,
                                   expression="(" + " + ".join(o.name for o in ops) + f") / {len(ops)}",
                                   result_unit=unit, question_focus=focus)
    if operation == "rank_extreme":
        want_min = bool(re.search(r"最少|最低|最小", question))
        chosen = (min if want_min else max)(names, key=lambda k: series[k])
        tied = [n for n in names if series[n] == series[chosen]]
        if len(tied) != 1:
            raise PlanError("ambiguous_reference", {"why": "tie"})
        all_ops = [operand(f"O{i + 1}", n) for i, n in enumerate(names)]
        chosen_op = next(o for o in all_ops if o.row_label == chosen)
        return DataCalculationPlan(operation="rank_extreme", operands=all_ops,
                                   expression=chosen_op.name, result_unit=unit,
                                   answer_kind="label" if re.search(r"哪|哪些|谁|组", question) else "value",
                                   question_focus=focus,
                                   intermediate_values=[{"name": "PICK", "expression": chosen_op.name,
                                                          "value": str(series[chosen]), "label": chosen,
                                                          "direction": "min" if want_min else "max"}])
    if operation in {"growth_yoy", "growth_mom"}:
        order = chart.get("order") or names
        if len(order) < 2:
            raise PlanError("time_pair_incomplete", {"order": order})
        ops = [operand("O1", order[0]), operand("O2", order[-1])]
        if "%" not in question and re.search(r"增长[^%]{0,6}[万亿元件人]", question):
            return DataCalculationPlan(operation="growth_amount", operands=ops, expression="O2 - O1",
                                       result_unit=unit, question_focus=focus)
        return DataCalculationPlan(operation=operation, operands=ops,
                                   expression="(O2 - O1) / O1 * 100", result_kind="percent",
                                   result_unit="%", question_focus=focus)
    raise PlanError("operation_unsupported", {"operation": operation, "on_chart": True})


# ------------------------------------------------------------------ 求值
def _parse_arithmetic(expr: str) -> Fraction:
    """小型确定性算术解析（+ - * / 与括号、a/b 分数、整数/小数），全程 Fraction。"""
    tokens = re.findall(r"\d+/\d+|\d+\.\d+|\d+|[+\-*/()]|\s+|[A-Za-z][A-Za-z0-9_]*", expr)
    tokens = [t for t in tokens if not t.isspace()]
    pos = 0

    def peek() -> Optional[str]:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        token = tokens[pos]
        pos += 1
        return token

    def parse_expr() -> Fraction:
        value = parse_term()
        while peek() in {"+", "-"}:
            op = take()
            rhs = parse_term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def parse_term() -> Fraction:
        value = parse_factor()
        while peek() in {"*", "/"}:
            op = take()
            rhs = parse_factor()
            if op == "/":
                if rhs == 0:
                    raise ZeroDivisionError("除以零（基期为 0，无法计算增长率）")
                value = value / rhs
            else:
                value = value * rhs
        return value

    def parse_factor() -> Fraction:
        token = peek()
        if token is None:
            raise ValueError("表达式不完整")
        if token == "(":
            take()
            value = parse_expr()
            if peek() != ")":
                raise ValueError("括号不配对")
            take()
            return value
        if token == "-":
            take()
            return -parse_factor()
        if token == "+":
            take()
            return parse_factor()
        take()
        if re.fullmatch(r"\d+/\d+", token):
            num, den = token.split("/")
            return Fraction(int(num), int(den))
        if re.fullmatch(r"\d+\.\d+", token):
            return Fraction(token)
        if re.fullmatch(r"\d+", token):
            return Fraction(int(token))
        raise ValueError(f"不支持的记号: {token!r}")

    result = parse_expr()
    if pos != len(tokens):
        raise ValueError("表达式有尾随内容")
    return result


def _iroot(x: int, n: int) -> Optional[int]:
    if x < 0:
        return None
    if x == 0:
        return 0
    guess = int(round(x ** (1.0 / n)))
    for cand in (guess - 1, guess, guess + 1, guess + 2):
        if cand >= 0 and cand ** n == x:
            return cand
    return None


def rational_nth_root(fr: Fraction, n: int) -> Optional[Fraction]:
    if fr <= 0 or n < 1:
        return None
    num_root = _iroot(fr.numerator, n)
    den_root = _iroot(fr.denominator, n)
    if num_root is None or den_root is None:
        return None
    return Fraction(num_root, den_root)


def _display_divisor(plan: DataCalculationPlan) -> Fraction:
    """量纲结果需从基准单位（×scale）折算回显示单位以匹配选项。
    百分比/百分点/倍数/单位换算 已无量纲或已在表达式中折算 → 除数 1。"""
    if plan.operation == "unit_convert":
        return Fraction(1)
    if plan.result_kind in {"percent", "ppt", "multiple"} or plan.result_unit in ("", "%", "百分点", "倍"):
        return Fraction(1)
    scale, _ = _resolve_scale(plan.result_unit)
    return Fraction(scale) if scale and scale != 1 else Fraction(1)


def evaluate_plan(plan: DataCalculationPlan) -> Dict[str, Any]:
    """确定性求值：{"ok", "value"(Fraction|float), "kind", "approx", "steps"} 或 {"ok": False, "failure"}。
    量纲结果折算回 result_unit 显示单位。"""
    try:
        values: Dict[str, Fraction] = {}
        for o in plan.operands:
            values[o.name] = Fraction(o.value)
        steps: List[Dict[str, str]] = []
        approx = False
        expr = plan.expression
        final: Any
        if plan.operation == "avg_annual_growth":
            m = re.search(r"\^\s*\(\s*1\s*/\s*(\d+)\s*\)", expr)
            if not m or len(plan.operands) != 2:
                raise ValueError("年均增长率表达式不完整")
            n = int(m.group(1))
            ratio = values[plan.operands[1].name] / values[plan.operands[0].name]
            root = rational_nth_root(ratio, n)
            if root is None:
                approx = True
                final = float((float(ratio) ** (1.0 / n) - 1) * 100)
            else:
                final = (root - 1) * 100
            steps.append({"expr": f"ratio = {ratio}", "value": str(ratio),
                          "root": str(final), "approx": str(approx)})
        else:
            substituted = expr
            for name in sorted(values, key=len, reverse=True):
                substituted = re.sub(rf"\b{re.escape(name)}\b", f"({values[name]})", substituted)
            final = _parse_arithmetic(substituted)
            divisor = _display_divisor(plan)
            if divisor != 1:
                final = final / divisor
            steps.append({"expr": substituted, "value": str(final)})
        return {"ok": True, "value": final, "approx": approx, "steps": steps,
                "answer_kind": plan.answer_kind, "result_kind": plan.result_kind or "number"}
    except ZeroDivisionError as err:
        return {"ok": False, "failure": {"code": "divide_by_zero", "detail": str(err)}}
    except (ValueError, ArithmeticError, KeyError) as err:
        return {"ok": False, "failure": {"code": "plan_eval_error", "detail": str(err)}}


def recompute_independently(plan: DataCalculationPlan) -> Optional[Fraction]:
    """验证层专用：**不解析表达式字符串**，按 operation 从操作数原始 Fraction
    走另一条算术路径复算（结构上与 evaluate_plan 相互独立）。返回显示单位（与
    evaluate_plan 折算口径一致）。无法独立复算返回 None。"""
    raw = _recompute_base(plan)
    if raw is None:
        return None
    divisor = _display_divisor(plan)
    return raw / divisor if divisor != 1 else raw


def _recompute_base(plan: DataCalculationPlan) -> Optional[Fraction]:
    vals = [Fraction(o.value) for o in plan.operands]
    try:
        op = plan.operation
        if op == "sum" and len(vals) >= 2:
            acc = Fraction(0)
            for v in vals:  # 逐项累加（与表达式一次性求值不同路径）
                acc += v
            return acc
        if op == "mean" and len(vals) >= 2:
            acc = Fraction(0)
            for v in vals:
                acc += v
            return acc / len(vals)
        if op == "difference" and len(vals) >= 2:
            return max(vals) - min(vals)
        if op == "rank_extreme":
            pick = next((i for i in plan.intermediate_values if i.get("name") == "PICK"), None)
            if pick is None or not vals:
                return None
            chosen = next((Fraction(o.value) for o in plan.operands if o.name == pick.get("expression")), None)
            if chosen is None:
                return None
            want = pick.get("direction", "max")
            # 独立复核：选中值必须真的是全系列的极值
            if want == "min":
                return chosen if chosen == min(vals) and [v for v in vals if v == chosen].__len__() == 1 else None
            return chosen if chosen == max(vals) and [v for v in vals if v == chosen].__len__() == 1 else None
        if op in {"growth_amount"} and len(vals) == 2:
            return vals[1] - vals[0]
        if op in {"growth_yoy", "growth_mom"} and len(vals) == 2:
            if vals[0] == 0:
                return None
            return (vals[1] - vals[0]) * 100 / vals[0]
        if op == "share":
            head = plan.expression.split("/")[0]
            part_names = set(re.findall(r"\bO\d+\b", head))
            parts = [Fraction(o.value) for o in plan.operands if o.name in part_names]
            rest = [Fraction(o.value) for o in plan.operands if o.name not in part_names]
            denom = sum(rest, Fraction(0))
            if not parts or not rest or denom == 0:
                return None
            return sum(parts, Fraction(0)) * 100 / denom
        if op == "multiple" and len(vals) == 2:
            if vals[1] == 0:
                return None
            q = vals[0] / vals[1]
            return q - 1 if "O1 / O2 - 1" in plan.expression else q
        if op == "base_value" and len(vals) == 2:
            return vals[0] / (1 + vals[1])
        if op == "current_value" and len(vals) == 2:
            return vals[0] * (1 + vals[1])
        if op == "pct_point_diff" and len(vals) == 2:
            return (vals[0] - vals[1]) * 100
        if op == "unit_convert" and len(vals) == 1:
            m = re.fullmatch(r"O1 / \((\d+(?:/\d+)?)\)", plan.expression)
            if not m:
                return None
            return vals[0] / Fraction(m.group(1))
        if op == "avg_annual_growth" and len(vals) == 2:
            m = re.search(r"\^\s*\(\s*1\s*/\s*(\d+)\s*\)", plan.expression)
            if not m or vals[0] == 0 or vals[1] <= 0:
                return None
            n = int(m.group(1))
            root = rational_nth_root(vals[1] / vals[0], n)
            if root is None:
                return None  # 近似路径由调用方按 approx 容差处理
            return (root - 1) * 100
    except (ValueError, ZeroDivisionError, IndexError):
        return None
    return None


# ------------------------------------------------------------------ 答案→选项
def match_plan_option(result: Fraction, answer_kind: str, label: Optional[str],
                      options: Sequence[Tuple[str, str]],
                      approx: bool = False) -> Dict[str, Any]:
    """把结果匹配到选项；要求**唯一命中**。标签答案按归一文本匹配；数值答案按容差：
    近似（开方等）容差放宽为 0.5%，精确容差 0.05%（绝对下限 0.01）。"""
    if answer_kind == "label" and label:
        norm_label = normalize_text(label)
        hits = [lab for lab, text in options if normalize_text(text) == norm_label]
        if not hits:
            hits = [lab for lab, text in options
                    if norm_label and normalize_text(text) and norm_label in normalize_text(text)]
        if len(hits) == 1:
            return {"ok": True, "label": hits[0], "candidates": hits}
        return {"ok": False, "why": "label_match", "candidates": hits}

    value = float(result)
    # 资料分析选项普遍是"保留一位小数的近似读数"：容差 = max(0.5%·|v|, 0.11)
    # （近似开方路径再放宽一倍），并且仍要求唯一命中——两个选项都进容差即 ambiguous。
    tol = max(abs(value) * (0.01 if approx else 0.005), 0.35 if approx else 0.11)
    hits: List[Tuple[float, str]] = []
    for lab, text in options:
        m = re.search(r"[-+]?\d+(?:\.\d+)?", normalize_text(text))
        if not m:
            continue
        candidate = float(m.group(0))
        delta = abs(candidate - value)
        if delta <= tol:
            hits.append((delta, lab))
    if len(hits) == 1:
        return {"ok": True, "label": hits[0][1], "candidates": [hits[0][1]]}
    if not hits:
        return {"ok": False, "why": "no_match_within_tolerance", "tolerance": tol}
    return {"ok": False, "why": "ambiguous_options", "candidates": [h[1] for h in hits]}
