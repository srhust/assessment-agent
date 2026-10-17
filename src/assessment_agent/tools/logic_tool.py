"""逻辑推理工具：用 Z3 对“排序比较类”逻辑题建模求解。

建模约定：为每个实体分配 1..n 的名次分数（分数越大越“高/大/快”），
实体间分数互不相同。题干中的比较语句被解析成约束，选项被解析成候选命题，
用“约束 ∧ ¬命题 是否可满足”判定命题是否必然成立。

只处理能被完整解析的题目：解析不完整或语句含未支持的数值差时明确失败，
不猜测答案。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .base import Tool, ToolError, ToolUnavailable

COMPARATIVE_ATTRS = {
    "高": ">", "大": ">", "长": ">", "快": ">", "多": ">", "早": ">", "重": ">", "远": ">",
    "矮": "<", "小": "<", "短": "<", "慢": "<", "少": "<", "晚": "<", "轻": "<", "近": "<",
    "低": "<", "浅": "<", "弱": "<", "深": ">", "强": ">",
}
_ATTR_CLASS = "".join(COMPARATIVE_ATTRS)
_CLAUSE_SPLIT = re.compile(r"[，,。；;\n]")
_MAX_MODELS = 6


def normalize_clause(clause: str) -> str:
    clause = clause.strip(" 　\t")
    clause = re.sub(r"^(?:已知|假设|其中|并且|而且|又|另外|根据题意)[:：]?", "", clause)
    return clause.strip(" 　\t")


def extract_entities(text: str, explicit: Optional[Sequence[str]] = None) -> List[str]:
    if explicit:
        return [str(item) for item in explicit]
    stem_order = "甲乙丙丁戊己庚辛壬癸"
    found = [char for char in stem_order if char in text]
    if len(found) >= 2:
        return found
    letters = sorted(set(re.findall(r"(?<![A-Za-z])([A-Z])(?![.、．)）:：A-Za-z])", text)))
    if len(letters) >= 2:
        return letters
    return []


@dataclass(frozen=True)
class Statement:
    op: str  # '>', '<', '=', '>=', '<=', 'max', 'min', 'not_max'
    left: str
    right: Optional[str] = None
    source: str = ""


def _entity_alt(entities: Sequence[str]) -> str:
    return "|".join(re.escape(entity) for entity in sorted(entities, key=len, reverse=True))


def parse_clause(clause: str, entities: Sequence[str]) -> Optional[Statement]:
    """把单个子句解析为比较约束；无法识别返回 None。"""
    text = normalize_clause(clause)
    if not text:
        return None
    alt = _entity_alt(entities)
    if not alt:
        return None

    pattern_not_equal = re.compile(
        rf"(?P<a>{alt})\s*(?:不比|没有|不如|不及)\s*(?P<b>{alt})\s*(?P<attr>[{_ATTR_CLASS}])"
    )
    pattern_equal = re.compile(
        rf"(?P<a>{alt})\s*(?:和|与|跟)\s*(?P<b>{alt})\s*(?:一样|同样|相同|相等)\s*(?P<attr>[{_ATTR_CLASS}]?)"
    )
    pattern_compare = re.compile(
        rf"(?P<a>{alt})\s*比\s*(?P<b>{alt})\s*(?:更|还要|还|都)?\s*(?P<attr>[{_ATTR_CLASS}])"
    )
    pattern_not_max = re.compile(
        rf"(?P<a>{alt})\s*(?:并|也|都|绝)?\s*(?:不是|并非|一定不是)\s*(?:最|排名第一|第一)\s*(?P<attr>[{_ATTR_CLASS}])"
    )
    pattern_max = re.compile(
        rf"(?P<a>{alt})\s*(?:是|为)?\s*(?:最|排名第一|第一)\s*(?P<attr>[{_ATTR_CLASS}])"
    )

    for pattern in (pattern_not_equal, pattern_compare, pattern_equal):
        match = pattern.search(text)
        if not match:
            continue
        if re.search(r"\d", text):
            raise ToolError("暂不支持带数值差的比较语句（如“甲比乙高 3 分”）")
        groups = match.groupdict()
        left, right = groups["a"], groups["b"]
        attr = groups.get("attr") or ""
        if pattern is pattern_equal:
            return Statement("=", left, right, text)
        comparison = COMPARATIVE_ATTRS.get(attr)
        if comparison is None:
            continue
        if pattern is pattern_not_equal:
            return Statement("<=" if comparison == ">" else ">=", left, right, text)
        return Statement(comparison, left, right, text)

    match = pattern_not_max.search(text)
    if match:
        return Statement("not_max", match.group("a"), None, text)
    match = pattern_max.search(text)
    if match:
        attr = match.group("attr")
        return Statement("max" if COMPARATIVE_ATTRS.get(attr) == ">" else "min", match.group("a"), None, text)
    return None


def _looks_like_claim(clause: str, entities: Sequence[str]) -> bool:
    alt = _entity_alt(entities)
    if not alt:
        return False
    patterns = (
        rf"(?:{alt})\s*(?:不比|没有|不如|不及)?\s*比\s*(?:{alt})",
        rf"(?:{alt})\s*(?:和|与|跟)\s*(?:{alt})",
        rf"最[{_ATTR_CLASS}]",
        rf"(?:{alt})\s*(?:是|为)?\s*(?:排名第一|第一)",
        rf"(?:{alt})\s*(?:并|也|都|绝)?\s*(?:不是|并非)\s*(?:最|排名第一|第一)",
    )
    return any(re.search(pattern, clause) for pattern in patterns)


def parse_statements(text: str, entities: Sequence[str]) -> Tuple[List[Statement], List[str]]:
    statements: List[Statement] = []
    unparsed: List[str] = []
    for clause in _CLAUSE_SPLIT.split(text):
        clause = normalize_clause(clause)
        if not clause:
            continue
        # 只关注含实体名且看起来在陈述比较/排名关系的子句（如“三人参加比赛”这类说明跳过）
        if not any(entity in clause for entity in entities):
            continue
        if re.search(r"(各不相同|互不相同|都不一样|均不同)", clause):
            continue
        if not _looks_like_claim(clause, entities):
            continue
        statement = parse_clause(clause, entities)
        if statement is None:
            unparsed.append(clause)
        else:
            statements.append(statement)
    return statements, unparsed


def detect_intent(stem: str) -> str:
    if re.search(r"谁(?:最高|最低|最矮)|最高的是|最低的是|排名第一的是|谁排第一", stem):
        return "max" if not re.search(r"最低|最矮", stem) else "min"
    if re.search(r"从高到低|从低到高|从大到小|从小到大|排列顺序|排序|顺序应是", stem):
        return "order"
    if re.search(r"可能为真|可能正确|有可能是|可能成立", stem):
        return "possible"
    return "entailment"


class _RankingModel:
    """Z3 名次模型。"""

    def __init__(self, entities: Sequence[str]):
        try:
            import z3
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise ToolUnavailable("未安装 z3-solver，无法进行逻辑约束求解（pip install z3-solver）") from exc
        self.z3 = z3
        self.entities = list(entities)
        self.score = {entity: z3.Int(f"score_{index}") for index, entity in enumerate(self.entities)}
        size = len(self.entities)
        self.base = [z3.Distinct(*self.score.values())]
        self.base.extend(z3.And(value >= 1, value <= size) for value in self.score.values())

    def predicate(self, statement: Statement) -> Any:
        z3 = self.z3
        left = self.score[statement.left]
        others = [value for entity, value in self.score.items() if entity != statement.left]
        if statement.op == "max":
            return z3.And(*[left > other for other in others]) if others else z3.BoolVal(True)
        if statement.op == "min":
            return z3.And(*[left < other for other in others]) if others else z3.BoolVal(True)
        if statement.op == "not_max":
            return z3.Or(*[left < other for other in others]) if others else z3.BoolVal(False)
        assert statement.right is not None
        right = self.score[statement.right]
        return {
            ">": left > right,
            "<": left < right,
            "=": left == right,
            ">=": left >= right,
            "<=": left <= right,
        }[statement.op]

    def _solver(self, extra: Iterable[Any] = ()) -> Any:
        solver = self.z3.Solver()
        solver.set("timeout", 5000)
        for constraint in self.base:
            solver.add(constraint)
        for constraint in extra:
            solver.add(constraint)
        return solver

    def consistent(self, statements: Iterable[Statement]) -> bool:
        predicates = [self.predicate(statement) for statement in statements]
        return self._solver(predicates).check() == self.z3.sat

    def load(self, statements: Iterable[Statement]) -> "_RankingModel":
        """把题干约束加入模型（必须先确认一致）。"""
        for statement in statements:
            self.base.append(self.predicate(statement))
        return self

    def entailed(self, statement: Statement) -> bool:
        solver = self._solver()
        solver.add(self.z3.Not(self.predicate(statement)))
        return solver.check() == self.z3.unsat

    def satisfiable(self, statement: Statement) -> bool:
        solver = self._solver([self.predicate(statement)])
        return solver.check() == self.z3.sat

    def enumerate_models(self, limit: int = 2) -> List[Dict[str, int]]:
        models: List[Dict[str, int]] = []
        solver = self._solver()
        while len(models) < limit and solver.check() == self.z3.sat:
            model = solver.model()

            def value_of(entity: str) -> int:
                interpretation = model.eval(self.score[entity], model_completion=True)
                return int(interpretation.as_long())

            assignment = {entity: value_of(entity) for entity in self.entities}
            models.append(assignment)
            solver.add(self.z3.Or(*[self.score[entity] != assignment[entity] for entity in self.entities]))
        return models


def _option_pairs(options: Sequence[Sequence[str]]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for item in options:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pairs.append((str(item[0]), str(item[1])))
    return pairs


class Z3LogicTool(Tool):
    id = "z3_logic"
    description = "排序比较类逻辑题的 Z3 约束建模与求解（必然为真 / 可能为真 / 唯一排序）"
    requires = ("z3-solver",)

    def available(self) -> bool:
        try:
            import z3  # noqa: F401
        except ImportError:
            return False
        return True

    def unavailable_reason(self) -> Optional[str]:
        return None if self.available() else "缺少 z3-solver 依赖，逻辑推理 Skill 不可用（pip install z3-solver）"

    def run(
        self,
        stem: str,
        options: Sequence[Sequence[str]] = (),
        entities: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        if not self.available():
            raise ToolUnavailable(self.unavailable_reason() or "z3 不可用")

        names = extract_entities(stem, entities)
        if len(names) < 2:
            raise ToolError("未能识别出至少两个比较对象（可用 meta.logic_entities 显式指定）")

        statements, unparsed = parse_statements(stem, names)
        if not statements:
            raise ToolError("未能从题干解析出任何比较约束")
        if unparsed:
            raise ToolError(f"存在无法解析的比较语句: {unparsed[:3]}")

        model = _RankingModel(names)
        if not model.consistent(statements):
            raise ToolError("题干约束相互矛盾，无法建模（请检查题目或抽取文本）")
        model.load(statements)

        intent = detect_intent(stem)
        pairs = _option_pairs(options)
        result: Dict[str, Any] = {
            "mode": "ordering",
            "entities": names,
            "intent": intent,
            "statements": [
                {"op": statement.op, "left": statement.left, "right": statement.right, "source": statement.source}
                for statement in statements
            ],
            "option_states": [],
            "answer_label": None,
            "answer_entity": None,
            "entailed_options": [],
            "possible_options": [],
            "notes": "",
        }

        # 选项本身是实体名（“谁最高”类）
        option_entities = [label for label, text in pairs if text.strip() in names]
        if intent in ("max", "min") and option_entities:
            target = Statement("max" if intent == "max" else "min", "", None)
            entailed = [name for name in names if model.entailed(Statement(target.op, name))]
            possible = [name for name in names if model.satisfiable(Statement(target.op, name))]
            answer_entity = entailed[0] if len(entailed) == 1 else (possible[0] if len(possible) == 1 else None)
            if answer_entity is None:
                raise ToolError(f"无法唯一确定{('最高' if intent == 'max' else '最低')}者（候选: {possible}）")
            result["answer_entity"] = answer_entity
            for label, text in pairs:
                if text.strip() == answer_entity:
                    result["answer_label"] = label
            result["notes"] = "直接问最高/最低，按实体唯一性判定"
            return result

        # 排序输出
        if intent == "order":
            models = model.enumerate_models(limit=2)
            if len(models) != 1:
                raise ToolError("排序不唯一，无法给出确定顺序")
            order = sorted(models[0].items(), key=lambda item: -item[1])
            result["order"] = [entity for entity, _ in order]
            result["notes"] = "约束唯一确定排序"
            return result

        if not pairs:
            raise ToolError("逻辑题需要选项才能判定；未提供选项")

        entailed_labels: List[str] = []
        possible_labels: List[str] = []
        states: List[Dict[str, Any]] = []
        parse_failures: List[str] = []
        for label, text in pairs:
            parsed = parse_clause(text, names)
            if parsed is None:
                parse_failures.append(f"{label}: {text}")
                states.append({"label": label, "parsed": False, "entailed": None, "possible": None})
                continue
            is_entailed = model.entailed(parsed)
            is_possible = model.satisfiable(parsed)
            states.append(
                {
                    "label": label,
                    "parsed": True,
                    "statement": {"op": parsed.op, "left": parsed.left, "right": parsed.right},
                    "entailed": is_entailed,
                    "possible": is_possible,
                }
            )
            if is_entailed:
                entailed_labels.append(label)
            if is_possible:
                possible_labels.append(label)

        result["option_states"] = states
        result["entailed_options"] = entailed_labels
        result["possible_options"] = possible_labels
        if parse_failures:
            raise ToolError(f"有选项无法解析为比较命题: {parse_failures}")

        if intent == "possible":
            if not possible_labels:
                raise ToolError("没有任何选项与题干约束相容")
            if len(possible_labels) > 1:
                raise ToolError(f"多个选项都可能为真（{possible_labels}），题目条件不足")
            result["answer_label"] = possible_labels[0]
            result["notes"] = f"唯一可能为真的选项: {possible_labels[0]}"
            return result

        if not entailed_labels:
            raise ToolError("没有选项能被题干必然推出")
        if len(entailed_labels) > 1:
            raise ToolError(f"多个选项都必然为真（{entailed_labels}），题目条件可能有误")
        result["answer_label"] = entailed_labels[0]
        result["notes"] = "唯一必然成立的选项"
        return result


RankingModel = _RankingModel


def verify_answer(
    entities: Sequence[str],
    statements: Sequence[Statement],
    intent: str,
    options: Sequence[Sequence[str]] = (),
) -> Dict[str, Any]:
    """用已解析的约束重新建模，独立复算答案（供 Verifier 使用）。"""
    names = list(entities)
    model = _RankingModel(names)
    consistent = model.consistent(statements)
    result: Dict[str, Any] = {
        "consistent": consistent,
        "answer_label": None,
        "answer_entity": None,
        "entailed": [],
        "possible": [],
    }
    if not consistent:
        return result
    model.load(statements)
    pairs = _option_pairs(options)

    if intent in ("max", "min"):
        entailed = [name for name in names if model.entailed(Statement(intent, name))]
        possible = [name for name in names if model.satisfiable(Statement(intent, name))]
        target = entailed[0] if len(entailed) == 1 else (possible[0] if len(possible) == 1 else None)
        result.update(
            {
                "entailed": entailed,
                "possible": possible,
                "answer_entity": target,
                "answer_label": next((label for label, text in pairs if text.strip() == target), None),
            }
        )
        return result

    if intent == "order":
        models = model.enumerate_models(limit=2)
        if len(models) == 1:
            order = sorted(models[0].items(), key=lambda item: -item[1])
            result["answer_label"] = " > ".join(entity for entity, _ in order)
        return result

    entailed_labels: List[str] = []
    possible_labels: List[str] = []
    for label, text in pairs:
        parsed = parse_clause(text, names)
        if parsed is None:
            continue
        if model.entailed(parsed):
            entailed_labels.append(label)
        if model.satisfiable(parsed):
            possible_labels.append(label)
    result["entailed"] = entailed_labels
    result["possible"] = possible_labels
    result["answer_label"] = entailed_labels[0] if len(entailed_labels) == 1 else None
    return result
