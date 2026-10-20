"""数字计算工具：文本 -> 表达式 -> SymPy 精确求值。

安全边界：只接受由数字与 ``+ - * / ( ) . ^`` 组成的字符串，符号白名单校验后
用 SymPy 求值，不调用 ``eval``，不接受任何变量名或函数名。
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .base import Tool, ToolError

_FULLWIDTH = str.maketrans(
    {
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "＋": "+", "－": "-", "—": "-", "–": "-", "−": "-",
        "＊": "*", "×": "*", "✕": "*", "·": "*",
        "／": "/", "÷": "/", "＝": "=", "％": "%",
        "（": "(", "）": ")", "，": ",", "、": ",",
    }
)

_ALLOWED_CHARS = set("0123456789+-*/().^ ")
_OPERATOR_CHARS = set("+-*/^")


def normalize_text(text: str) -> str:
    """全角转半角，统一乘除符号，去掉千分位逗号与空白噪声。"""
    normalized = text.translate(_FULLWIDTH)
    normalized = re.sub(r"(?<=\d),(?=\d{3}\b)", "", normalized)  # 1,200 -> 1200
    return normalized


def _replace_percent(text: str) -> str:
    """把 ``X%`` 改写为 ``X*(1/100)``，避免 SymPy 把 % 当作取模。"""
    pattern = re.compile(r"(?P<num>\d+(?:\.\d+)?|\))\s*%")
    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(lambda m: f"{m.group('num')}*(1/100)", text)
    return text


def _insert_implicit_multiplication(text: str) -> str:
    text = re.sub(r"(?<=[\d)])\((?=[\d(])", "*(", text)
    text = re.sub(r"(?<=\))\(?(\d)", r"*\1", text)
    return text


def prepare_expression(text: str) -> str:
    """文本 -> 可交给 SymPy 的纯算术表达式（不含求解语义）。"""
    expr = normalize_text(text)
    expr = expr.replace("的", "*").replace("乘以", "*").replace("除以", "/")
    expr = expr.replace("加上", "+").replace("减去", "-")
    expr = _replace_percent(expr)
    expr = expr.replace("^", "**")
    expr = _insert_implicit_multiplication(expr)
    expr = re.sub(r"[=?？]", "", expr)
    return expr.strip()


def extract_expression(text: str) -> Optional[str]:
    """从中文题干里抽取出最可能的算术表达式片段。"""
    prepared = prepare_expression(text)
    candidates = re.findall(r"[\d+\-*/.() ]+", prepared)
    best: Optional[str] = None
    best_score = (0, 0.0)
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        if not any(char.isdigit() for char in candidate):
            continue
        if not any(char in _OPERATOR_CHARS for char in candidate):
            continue
        # 去掉首尾多余运算符
        cleaned = candidate.strip(" +-*/")
        if not cleaned:
            continue
        try:
            _validate(cleaned)
        except ToolError:
            continue
        score = (cleaned.count("(") + cleaned.count(")") + len(cleaned), len(cleaned))
        if score >= best_score:
            best, best_score = cleaned, score
    return best


def _validate(expression: str) -> None:
    if not expression:
        raise ToolError("表达式为空")
    illegal = [char for char in expression if char not in _ALLOWED_CHARS and char not in set("*^.()")]
    if illegal:
        raise ToolError(f"表达式包含非法字符: {''.join(sorted(set(illegal)))}")
    if not any(char.isdigit() for char in expression):
        raise ToolError("表达式中没有数字")


def evaluate_expression(expression: str) -> Dict[str, Any]:
    """用 SymPy 精确求值，返回数值与精确有理数表示。"""
    prepared = prepare_expression(expression)
    _validate(prepared)
    try:
        import sympy
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise ToolError(f"缺少 sympy 依赖: {exc}") from exc
    try:
        value = sympy.sympify(prepared, evaluate=True, rational=True)
    except Exception as exc:  # noqa: BLE001 - SymPy 抛出的各类解析异常
        raise ToolError(f"无法解析表达式 {prepared!r}: {exc}") from exc
    if not getattr(value, "is_number", False):
        raise ToolError(f"表达式不是纯数值: {prepared!r}")
    exact = sympy.simplify(value)
    numeric = float(sympy.N(exact, 20))
    exact_text = str(int(exact)) if getattr(exact, "is_integer", False) else str(exact)
    return {
        "expression": prepared,
        "exact": exact_text,
        "value": numeric,
        "is_integer": bool(getattr(exact, "is_integer", False)),
    }


def _option_number(text: str) -> Optional[float]:
    """选项文本 -> 数值（支持 12 / 12.5 / 1/3 / 33.3% / 1,200）。"""
    prepared = normalize_text(text)
    match = re.search(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?", prepared)
    if not match:
        return None
    raw = match.group(0).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if "%" in prepared:
        value = value / 100.0
    fraction_match = re.search(r"(\d+)\s*/\s*(\d+)", prepared)
    if fraction_match and match.start() <= fraction_match.start():
        try:
            return int(fraction_match.group(1)) / int(fraction_match.group(2))
        except ZeroDivisionError:
            return None
    return value


def match_option(value: float, options: Sequence[Tuple[str, str]],
                 rel_tol: float = 1e-6) -> Optional[str]:
    """把计算结果匹配到选项标号。R7：题干含"约"时调用方可放宽 rel_tol。"""
    best_label: Optional[str] = None
    best_delta = float("inf")
    ambiguous = False
    for label, text in options:
        candidate = _option_number(text)
        if candidate is None:
            continue
        delta = abs(candidate - value)
        tolerance = max(1e-9, abs(value) * rel_tol)
        if delta <= tolerance:
            if best_label is not None and delta < best_delta * 0.5:
                ambiguous = False  # 明显更接近者胜出
            elif best_label is not None:
                ambiguous = True
            if delta < best_delta:
                best_label, best_delta = label, delta
    return None if ambiguous else best_label


class PythonMathTool(Tool):
    """数字计算工具（SymPy 精确算术）。"""

    id = "python_math"
    description = "把题干中的算术表达式用 SymPy 精确求值，或直接计算给定表达式"
    requires = ("sympy",)

    def available(self) -> bool:
        try:
            import sympy  # noqa: F401
        except ImportError:
            return False
        return True

    def unavailable_reason(self) -> Optional[str]:
        return None if self.available() else "缺少 sympy 依赖，请 pip install sympy"

    def run(
        self,
        expression: Optional[str] = None,
        text: Optional[str] = None,
        options: Optional[Sequence[Sequence[str]]] = None,
    ) -> Dict[str, Any]:
        if expression is None:
            if not text:
                raise ToolError("需要 expression 或 text 参数")
            expression = extract_expression(text) or ""
            if not expression:
                raise ToolError("未能从题干中解析出算术表达式")
        result = evaluate_expression(expression)
        option_pairs: List[Tuple[str, str]] = []
        for item in options or ():
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                option_pairs.append((str(item[0]), str(item[1])))
        if option_pairs:
            result["matched_label"] = match_option(result["value"], option_pairs)
        return result


def as_fraction(value: float) -> Fraction:
    """独立于 SymPy 的精确化路径，供 Verifier 交叉验证。"""
    return Fraction(str(value)).limit_denominator(10**9)


def option_value(text: str) -> Optional[float]:
    """选项文本 -> 数值（供 Verifier 比对）。"""
    return _option_number(text)


def fraction_eval(expression: str) -> Fraction:
    """用 ``ast`` + ``Fraction`` 独立求值（不依赖 SymPy），用于 Verifier 交叉验证。"""
    import ast

    prepared = prepare_expression(expression).replace("**", "^")
    _validate(prepared.replace("^", "**"))
    try:
        tree = ast.parse(prepared.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"表达式语法错误: {exc}") from exc

    def _eval(node: ast.AST) -> Fraction:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Fraction(str(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = _eval(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ToolError("除数为 0")
                return left / right
            if isinstance(node.op, ast.Pow):
                if right.denominator != 1 or abs(right.numerator) > 12:
                    raise ToolError("指数只支持 -12..12 的整数")
                return left ** int(right.numerator)
        raise ToolError(f"表达式包含不支持的语法: {type(node).__name__}")

    return _eval(tree)
