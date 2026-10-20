"""R3a 资料分析 Skill：表格/统计图 → 结构化数据 → 计算计划 → Fraction 求值 → 独立验证。

可靠性纪律（继承 R2c）：
- 计算计划中每个数字必须能追溯到源单元格/图上标注文本证据；
- 信息不足、OCR 歧义、单位冲突、引用不唯一 → 结构化失败（不猜答案）；
- 独立验证重读原始数据并用**另一条算术路径**复算；不能确认时 unverified（受控弃权），
  与 Solver 不共享最终决策。
"""

from __future__ import annotations

import json
import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import (DataCalculationPlan, Question, Solution, Verification,
                        VerificationCheck)
from ..tools.chart_tool import chart_values_from_texts, read_bar_chart
from ..tools.data_plan import (OPERATION_EXPRESSION_TEMPLATES, build_data_plan,
                                 evaluate_plan, match_plan_option, plan_from_llm_payload,
                                 recompute_independently)
from ..tools.table_tool import get_cell, normalize_table
from .base import BaseSkill, SkillContext


class DataAnalysisSkill(BaseSkill):
    id = "data_analysis"
    name_zh = "资料分析"
    description = "表格/柱状图数据读取与计算：合计、平均、同比/环比、比重、倍数、基期/现期、百分点、单位换算"
    stage = 3
    supported = True
    question_types = ("single_choice", "fill_blank")

    # ------------------------------------------------------------------ solve
    @staticmethod
    def _question_part(stem: str) -> str:
        """题面问句 = 最后一个 '？' 句（材料句只作证据来源，不参与运算检测）。
        多问题干（问句含多个 '？'）取最末问号所在句；无问号则原样返回。"""
        if not stem:
            return stem or ""
        q_pos = max((i for i, c in enumerate(stem) if c in "？?：:"), default=-1)
        if q_pos < 0:
            return stem
        start_candidates = [stem.rfind("。", 0, q_pos), stem.rfind("\n", 0, q_pos)]
        start = max(start_candidates) + 1
        seg = stem[start:q_pos + 1].strip()
        # 句内含引导语（"根据以下资料回答问题。X？"）时取最后一个问号句的主体
        inner = max((i for i, c in enumerate(seg[:-1]) if c == "。"), default=-1)
        return seg[inner + 1:].strip() if inner >= 0 else seg

    def _effective_inputs(self, question: Question):
        """solve/verify 共用：vision 表格/统计图优先；纯文字材料规约为事实表（R7-C2）。
        返回 (tables, table_failures, chart, text_material)。"""
        vision = (question.meta or {}).get("vision") or {}
        tables, table_failures = self._gather_tables(vision)
        chart = self._gather_chart(vision)
        text_material = False
        if not tables and chart is None:
            from ..tools.table_tool import normalize_table
            from ..tools.text_material import build_fact_table
            built = build_fact_table(question.stem)
            if built:
                synth = normalize_table(built[0], region_id="txt1")
                if synth.get("ok"):
                    tables = [synth["table"]]
                    text_material = True
        return tables, table_failures, chart, text_material

    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        if getattr(ctx.settings, "r3a_data_analysis", True) is False:
            return self.unsupported(question, "资料分析能力已由开关关闭（消融基线）")
        tables, table_failures, chart, text_material = self._effective_inputs(question)
        if not tables and chart is None:
            return Solution(
                status="failed", skill=self.id, answer=None, confidence=0.0,
                method="data_analysis:inputs",
                error="资料分析输入缺少表格/统计图数据区域（不做无证据作答）",
                meta={"failure": {"code": "no_data_regions",
                                  "vision_keys": sorted((question.meta or {}).get("vision") or {})}},
            )

        result = build_data_plan(self._question_part(question.stem), tables, chart)
        plan_source = "rules"
        llm_rejection: Optional[Dict[str, Any]] = None
        if not result.get("ok") and ctx.llm is not None:
            llm_result, llm_rejection = self._llm_plan(question, ctx, tables, chart)
            if llm_result is not None:
                result = llm_result
                plan_source = "llm_evidence_bound"
        if not result.get("ok"):
            failure = result.get("failure", {"code": "plan_unavailable"})
            fail_meta: Dict[str, Any] = {"failure": failure, "tables": len(tables), "chart": bool(chart)}
            if llm_rejection is not None:
                fail_meta["llm_rejection"] = llm_rejection
            return Solution(
                status="failed", skill=self.id, answer=None, confidence=0.0,
                method="data_analysis:plan",
                error=f"资料分析计划构建失败: {failure.get('code')} {json.dumps(failure.get('detail', {}), ensure_ascii=False)[:220]}",
                meta=fail_meta,
            )

        plan: DataCalculationPlan = result["plan"]
        evaluation = evaluate_plan(plan)
        if not evaluation.get("ok"):
            failure = evaluation.get("failure", {})
            return Solution(
                status="failed", skill=self.id, confidence=0.0, method="data_analysis:evaluate",
                error=f"资料分析求值失败: {failure.get('code')} {failure.get('detail', '')}",
                meta={"data_plan": plan.model_dump(mode="json"), "failure": failure},
            )

        value = evaluation["value"]
        label_operand = self._chosen_label(plan, evaluation)
        matched = match_plan_option(
            value if isinstance(value, Fraction) else Fraction(str(value)),
            plan.answer_kind, label_operand,
            [(o.label, o.text) for o in question.options],
            approx=bool(evaluation.get("approx")),
        )
        if not matched.get("ok"):
            return Solution(
                status="failed", skill=self.id, confidence=0.0, method="data_analysis:match",
                error=f"资料分析结果与选项无法唯一匹配: {matched.get('why')}",
                meta={"data_plan": plan.model_dump(mode="json"),
                      "value": str(value), "match": matched},
            )

        option_text = next((o.text for o in question.options if o.label == matched["label"]), "")
        steps = [
            f"操作: {plan.operation}（题面线索：{plan.question_focus}）",
            "引用: " + "; ".join(
                f"{o.name}={o.raw_text}[行:{o.row_label}|列:{o.col_label}]" for o in plan.operands),
            f"表达式: {plan.expression}",
            f"结果: {value}{'（近似）' if evaluation.get('approx') else ''} {plan.result_unit}",
            f"匹配选项: {matched['label']}",
        ]
        if plan_source != "rules":
            steps = steps + [f"计划来源: {plan_source}（LLM 提出结构，证据与求值全部过确定性闸门）"]
        return Solution(
            status="solved", skill=self.id, answer=matched["label"], answer_text=option_text,
            confidence=0.82, method=f"data_analysis:{plan.operation}", steps=steps,
            meta={
                "data_plan": plan.model_dump(mode="json"),
                "evaluation": {"value": str(value), "approx": bool(evaluation.get("approx")),
                               "steps": evaluation.get("steps", [])},
                "matched_label": matched["label"],
                "plan_source": plan_source,
                "evidence": [o.model_dump(mode="json") for o in plan.operands],
                "tables_normalized": len(tables), "table_failures": table_failures,
                "chart": bool(chart),
            },
        )

    @staticmethod
    def _chosen_label(plan: DataCalculationPlan, evaluation: Dict[str, Any]) -> Optional[str]:
        if plan.answer_kind != "label":
            return None
        for inter in plan.intermediate_values:
            if inter.get("name") == "PICK" and inter.get("label"):
                return str(inter["label"])
        expr = ""
        for inter in plan.intermediate_values:
            if inter.get("name") == "PICK":
                expr = inter.get("expression", "")
        if not expr:
            refs = re.findall(r"O\d+", plan.expression)
            expr = refs[0] if len(set(refs)) == 1 else ""
        operand = next((o for o in plan.operands if o.name == expr), None) \
            or (plan.operands[0] if plan.operands else None)
        if operand is None:
            return None
        distinct = {o.row_label for o in plan.operands}
        return operand.row_label if len(distinct) > 1 else operand.col_label

    # ------------------------------------------------------------------ inputs
    def _gather_tables(self, vision: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        tables: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        raw_tables: List[Dict[str, Any]] = []
        for item in vision.get("table_regions") or []:
            if isinstance(item, dict) and item.get("cells"):
                raw_tables.append(item)
        table = vision.get("table")
        if isinstance(table, dict) and table.get("cells"):
            raw_tables.append(table)
        for item in raw_tables:
            result = normalize_table(item["cells"], bbox=item.get("bbox"),
                                      region_id=str(item.get("region_id") or item.get("id") or "tb1"))
            if result.get("ok"):
                tables.append(result["table"])
            else:
                failures.append(result.get("failure", {}))
        return tables, failures

    def _gather_chart(self, vision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        gold = vision.get("chart")
        if isinstance(gold, dict) and gold.get("text"):
            parsed = chart_values_from_texts([{"id": "chart", "text": gold["text"]}])
            if parsed.get("ok"):
                parsed["crop_path"] = gold.get("crop_path")
                return parsed
            return None
        texts = vision.get("text_regions") or []
        chart_texts = [r for r in texts if isinstance(r, dict) and r.get("text")]
        if not chart_texts:
            return None
        parsed = chart_values_from_texts(chart_texts)
        if not parsed.get("ok"):
            return None
        crop = vision.get("chart_crop") or next((r.get("crop_path") for r in chart_texts
                                                  if r.get("id") == "chart" and r.get("crop_path")), None)
        parsed["crop_path"] = crop
        return parsed

    # ------------------------------------------------------------------ LLM 计划入口（R3b：真正执行证据绑定的模型计划）
    def _llm_plan(self, question: Question, ctx: SkillContext,
                  tables: Sequence[Dict[str, Any]],
                  chart: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]],
                                                             Optional[Dict[str, Any]]]:
        """返回 (可执行计划结果|None, 拒绝原因|None)。

        LLM 只能提交 {operation, operands, expression}：操作走白名单、表达式匹配该操作的
        确定性模板、每个操作数逐字绑定真实证据；通过后进入与规则计划**同一**的求值/复算/
        选项匹配路径——模型没有任何直接给出答案的通道，也无法用"自我确认"跳过独立验证。
        """
        dump = {"row_col_note": "row/col 必须是下表给出的网格坐标；raw 必须逐字等于单元格原文",
                "tables": [{"columns": [{"col": c["index"], "name": c["name"]} for c in t["columns"]],
                            "rows": [{"row": r["index"], "label": r["label"],
                                       "cells": {str(c["col"]): c["raw"] for c in r["cells"]}}
                                      for r in t["rows"]]} for t in tables],
                "chart": {k: str(v) for k, v in (chart or {}).get("series", {})}}
        try:
            payload = ctx.llm.complete_json(  # type: ignore[union-attr]
                system="你是资料分析计划器。只输出 JSON：{operation(白名单:sum/mean/difference/"
                       "rank_extreme/growth_yoy/growth_mom/growth_amount/share/multiple/avg_annual_growth/"
                       "base_value/current_value/pct_point_diff/unit_convert), operands:[{name,source:"
                       "table|inline|chart,row,col|label,raw}], expression, question_focus}。"
                       "不得给出最终答案；表达式必须使用操作对应的固定形式；operands 必须逐字引用真实单元格原文。",
                user=json.dumps({"question": question.stem, "data": dump}, ensure_ascii=False)[:4000],
            )
        except Exception:
            return None, {"code": "llm_call_failed"}
        built = plan_from_llm_payload(question.stem, payload, tables, chart)
        if built.get("ok"):
            return built, None
        return None, built.get("failure")

    # ------------------------------------------------------------------ verify
    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        meta = solution.meta or {}
        plan_raw = meta.get("data_plan")
        if not plan_raw:
            return None
        try:
            plan = DataCalculationPlan.model_validate(plan_raw)
        except Exception:
            return Verification(passed=False, action="unverified",
                                 checks=[VerificationCheck(name="data.plan_valid", passed=False,
                                                            detail="计划结构不可解析")],
                                 notes="资料分析计划不可解析，拒绝自动接受")
        # R7-C2：verify 与 solve 用同一输入规约（文字材料题重读合成事实表）
        tables, _, chart, _ = self._effective_inputs(question)
        checks: List[VerificationCheck] = []
        contradicted = False
        unverifiable = False

        # 1) 重读源单元格：值与原文必须与计划一致（计算数据未被篡改）
        for o in plan.operands:
            if o.col_label == "图上读数":
                # 图上读数操作数：统计图文本证据必须仍能解析出同一数值
                if chart is None or o.row_label not in (chart.get("series") or {}):
                    checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                     detail=f"{o.name}: 图上读数证据消失"))
                    unverifiable = True
                elif chart["series"][o.row_label] != Fraction(o.value):
                    checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                     detail=f"{o.name}: 图上读数 {o.value} ≠ 重读 {chart['series'][o.row_label]}"))
                    contradicted = True
                continue
            if o.table_ref >= 0 and o.row is not None and o.col is not None and o.table_ref < len(tables):
                cell = get_cell(tables[o.table_ref], o.row, o.col)
                if cell is None:
                    checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                     detail=f"{o.name}: 源单元格不可重读"))
                    unverifiable = True
                    continue
                if str(cell.get("raw", "")) != o.raw_text:
                    checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                     detail=f"{o.name}: 原文变化 {o.raw_text!r} -> {cell.get('raw')!r}"))
                    contradicted = True
                    continue
                if not cell.get("ok") or cell.get("value") is None:
                    checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                     detail=f"{o.name}: 源单元格现为缺失/歧义"))
                    contradicted = True
                    continue
                if str(cell.get("value")) != o.value:
                    checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                     detail=f"{o.name}: 数值不一致 {o.value} vs {cell.get('value')}"))
                    contradicted = True
                    continue
                if str(cell.get("unit_conflict") and "unit_conflict" or "") == "unit_conflict":
                    checks.append(VerificationCheck(name="data.units", passed=False,
                                                     detail=f"{o.name}: 列内单位冲突"))
                    contradicted = True
            elif o.table_ref == -1 and o.row_label and o.col_label != "图上读数":
                # 题干内联率等文本证据：必须在当前题干中仍能找到
                if o.raw_text not in question.stem:
                    checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                     detail=f"{o.name}: 题干内联证据消失 {o.raw_text!r}"))
                    contradicted = True
            else:
                # 修复缺口 2：每个操作数必须且只能命中一种有效证据来源；
                # 越界 table_ref、缺失行列、无法绑定的引用一律阻断接受。
                checks.append(VerificationCheck(name="data.source_recheck", passed=False,
                                                 detail=(f"{o.name}: 无法绑定有效证据来源"
                                                          f"（table_ref={o.table_ref}, row={o.row}, col={o.col}）"),
                                                 severity="blocking"))
                contradicted = True
        if not any(c.name == "data.source_recheck" and not c.passed for c in checks):
            checks.append(VerificationCheck(name="data.source_recheck", passed=True,
                                             detail=f"{len(plan.operands)} 个操作数重读一致"))

        # 2) 行列索引/时间/统计对象一致性：题面线索仍在，行/列标签仍能解析到表结构。
        #    注意：这是**表层线索检查**——question_focus 字符串存在不能证明计划回答了题目所问，
        #    真正的题意一致性由结构语义检查（操作↔表达式模板）+ 独立复算 + 单位核对共同支撑。
        focus = plan.question_focus or ""
        if focus and focus not in question.stem:
            checks.append(VerificationCheck(name="data.intent", passed=False,
                                             detail=f"题面线索消失: {focus!r}", severity="blocking"))
            unverifiable = True
        else:
            checks.append(VerificationCheck(name="data.intent", passed=True,
                                             detail=(f"线索 {focus!r} 仍在题干（表层检查，"
                                                      "不构成题意正确性证明）")))

        # 2b) 操作↔表达式结构一致性：篡改表达式（如把差值改成求和）必须被阻断
        template = OPERATION_EXPRESSION_TEMPLATES.get(plan.operation)
        expr_norm = re.sub(r"\s+", " ", plan.expression.strip())
        if template and not re.fullmatch(template, expr_norm):
            checks.append(VerificationCheck(name="data.plan_semantics", passed=False,
                                             detail=f"表达式与操作不一致: {plan.operation} vs {expr_norm[:60]}",
                                             severity="blocking"))
            contradicted = True
        else:
            checks.append(VerificationCheck(name="data.plan_semantics", passed=True,
                                             detail="操作↔表达式模板一致（结构语义检查，非完整题意证明）"))

        # 2c) 表达式重放：篡改后的表达式必须按**当前计划**重新求值并与存档数值比对，
        #     防止"表达式被改但存档值仍是旧结果"的计划蒙混通过独立复算。
        replay = evaluate_plan(plan)
        eval_value_now = (solution.meta.get("evaluation") or {}).get("value")
        if not replay.get("ok"):
            checks.append(VerificationCheck(name="data.expr_replay", passed=False,
                                             detail=f"表达式重放失败: {plan.expression[:60]}",
                                             severity="blocking"))
            contradicted = True
        elif eval_value_now is None:
            checks.append(VerificationCheck(name="data.expr_replay", passed=False,
                                             detail="缺少存档求值结果，无法重放核对", severity="blocking"))
            unverifiable = True
        else:
            try:
                same = Fraction(str(replay["value"])) == Fraction(str(eval_value_now))
            except (TypeError, ValueError, ZeroDivisionError):
                try:
                    same = abs(float(replay["value"]) - float(eval_value_now)) <= 1e-6
                except (TypeError, ValueError):
                    same = False
            if not same:
                checks.append(VerificationCheck(name="data.expr_replay", passed=False,
                                                 detail=(f"表达式重放 {replay['value']} ≠ 存档 "
                                                          f"{eval_value_now}（计划被篡改）"),
                                                 severity="blocking"))
                contradicted = True
            else:
                checks.append(VerificationCheck(name="data.expr_replay", passed=True,
                                                 detail="表达式重放与存档数值一致"))

        # 3) 单位与百分点/百分比区分
        kinds = [o.kind for o in plan.operands if o.value]
        units = {o.unit for o in plan.operands if o.unit and o.kind not in ("percent", "ppt", "multiple")}
        ok_units = True
        detail_units = "、".join(sorted(u for u in units if u)) or "—"
        if plan.operation == "pct_point_diff" and not any(k == "percent" for k in kinds):
            ok_units = False
            detail_units = "百分点差必须引用百分比读数"
        if plan.result_kind == "percent" and units and plan.operation in {"growth_yoy", "growth_mom", "share",
                                                                          "avg_annual_growth"}:
            ok_units = True
        if len({u for u in units if u}) > 1 and plan.operation in {"sum", "mean", "difference", "growth_amount",
                                                                   "growth_yoy", "growth_mom", "share", "multiple"}:
            ok_units = False
            detail_units = f"跨单位运算未换算: {detail_units}"
        checks.append(VerificationCheck(name="data.units", passed=ok_units,
                                         detail=f"操作数单位: {detail_units}；结果单位: {plan.result_unit}"))
        if not ok_units:
            contradicted = True

        # 4) 独立算术路径复算（不重放表达式字符串）
        eval_value = (solution.meta.get("evaluation") or {}).get("value")
        alt = recompute_independently(plan)
        stored: Optional[Fraction] = None
        if eval_value is not None:
            try:
                stored = Fraction(str(eval_value))
            except (ValueError, ZeroDivisionError):
                stored = None
        if plan.operation == "avg_annual_growth" and (solution.meta.get("evaluation") or {}).get("approx"):
            alt_float = None
            try:
                alt_float = float(str(eval_value))
            except ValueError:
                alt_float = None
            direct = None
            if len(plan.operands) == 2 and alt_float is not None:
                n = next((int(i["value"]) for i in plan.intermediate_values if i["name"] == "N"), 0)
                if not n:
                    m = re.search(r"\^\(1/(\d+)\)", re.sub(r"\s+", "", plan.expression))
                    n = int(m.group(1)) if m else 0
                v0, v1 = (Fraction(plan.operands[0].value), Fraction(plan.operands[1].value))
                if n and v0 > 0 and v1 > 0:
                    direct = (float(v1 / v0) ** (1.0 / n) - 1) * 100
            if direct is None or abs(direct - alt_float) > 1e-6:
                checks.append(VerificationCheck(name="data.recompute", passed=False,
                                                 detail="近似开方复算不一致"))
                contradicted = True
            else:
                checks.append(VerificationCheck(name="data.recompute", passed=True,
                                                 detail="近似几何均值独立复算一致（已标注近似）"))
        elif alt is None:
            checks.append(VerificationCheck(name="data.recompute", passed=False,
                                             detail="无法用独立路径复算", severity="blocking"))
            unverifiable = True
        elif stored is not None and alt != stored:
            checks.append(VerificationCheck(name="data.recompute", passed=False,
                                             detail=f"独立复算 {alt} ≠ 计划求值 {stored}"))
            contradicted = True
        else:
            checks.append(VerificationCheck(name="data.recompute", passed=True,
                                             detail=f"独立路径复算一致: {alt}"))

        # 5) 选项唯一性 + 6) 图上柱高一致性
        label_operand = self._chosen_label(plan, solution.meta.get("evaluation") or {})
        value_for_match = stored if stored is not None else (
            Fraction(str(eval_value)) if eval_value is not None else None)
        if value_for_match is None:
            checks.append(VerificationCheck(name="data.option_match", passed=False,
                                             detail="求值结果缺失", severity="blocking"))
            unverifiable = True
        else:
            matched = match_plan_option(value_for_match, plan.answer_kind, label_operand,
                                         [(o.label, o.text) for o in question.options],
                                         approx=bool((solution.meta.get("evaluation") or {}).get("approx")))
            if not matched.get("ok"):
                if matched.get("why") in {"ambiguous_options", "no_match_within_tolerance"}:
                    checks.append(VerificationCheck(name="data.option_match", passed=False,
                                                     detail=f"选项匹配不唯一/超容差: {matched.get('why')}",
                                                     severity="blocking"))
                    contradicted = matched.get("why") == "no_match_within_tolerance"
                    if matched.get("why") == "ambiguous_options":
                        contradicted = True
                    else:
                        unverifiable = True
                else:
                    checks.append(VerificationCheck(name="data.option_match", passed=False,
                                                     detail=f"无法匹配选项: {matched.get('why')}",
                                                     severity="blocking"))
                    contradicted = True
            elif plan.answer_kind != "label" and matched.get("label") != solution.answer:
                checks.append(VerificationCheck(name="data.option_match", passed=False,
                                                 detail=f"选项漂移 {solution.answer} -> {matched.get('label')}"))
                contradicted = True
            else:
                checks.append(VerificationCheck(name="data.option_match", passed=True,
                                                 detail=f"唯一命中 {matched.get('label')}"))

        if chart and plan.operands and plan.operands[0].table_ref == -1 and plan.operands[0].col_label == "图上读数":
            series = {o.row_label: Fraction(o.value) for o in plan.operands}
            order = [o.row_label for o in plan.operands]
            crop = chart.get("crop_path")
            consistency = read_bar_chart(crop, {**chart["series"], **series},
                                          chart.get("order") or order)
            if consistency.get("ok") is False:
                checks.append(VerificationCheck(name="data.chart_pixels", passed=False,
                                                 detail=f"柱高比例与读数矛盾: {consistency}",
                                                 severity="blocking"))
                contradicted = True
            elif consistency.get("ok") is None:
                checks.append(VerificationCheck(name="data.chart_pixels", passed=False,
                                                 detail=f"柱高不可独立复核（{consistency.get('why')}）：不作为确认依据",
                                                 severity="warning"))
            else:
                checks.append(VerificationCheck(name="data.chart_pixels", passed=True,
                                                 detail=f"柱高比例一致（偏差 {consistency.get('worst_deviation')}）"))

        hard = [c for c in checks if not c.passed and c.severity == "blocking"]
        if contradicted:
            return Verification(passed=False, action="reject", checks=checks, final_answer=None,
                                 notes="R3a 资料分析独立复核：来源/单位/复算/选项存在矛盾，拒绝输出")
        if hard or unverifiable:
            return Verification(passed=False, action="unverified", checks=checks, final_answer=None,
                                 notes="R3a 资料分析独立复核无法确认（重读/复算/唯一性不充分），受控弃权")
        return Verification(passed=True, action="accept", checks=checks,
                             final_answer=solution.answer,
                             notes="R3a 资料分析：源单元格重读+题意线索+单位+独立复算+选项唯一 全部通过")
