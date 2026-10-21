# -*- coding: utf-8 -*-
"""R10-C：算术题面的结构化表达式 AST（运算节点 + 题干跨度 + 计算依赖）。

工单：C(n,k)/P(n,k)/阶乘/幂/根式/一元负号必须建成 AST，弃"抽取最长数字运算
片段"式的字符串启发——本模块的候选片段以**可解析为合法 AST**为唯一准入条件。

设计约束：
* 归一化只做**等宽替换**（×→* 等 1:1），使节点 span 与原题干偏移一一对应；
  无法等宽表达的形式（如 √→sqrt）改为语法内一元前缀 `√`，保 span。
* 求值用 Fraction 精确值；复核通道是**从序列化节点表重放**（postfix 独立
  重算，与递归求值两条实现路径），不依赖 fraction_eval 的语法子集。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

# 等宽归一化表（1:1 字符映射，span 直映射题干）
_EQUIV = str.maketrans({"×": "*", "÷": "/", "－": "-", "＋": "+", "（": "(",
                        "）": ")", "＝": "=", "％": "%", "，": ",", "．": ".",
                        "–": "-", "：": ":", "−": "-", "﹢": "+"})

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Node:
    """运算/数值节点。kind: num|bin|un|fn|fact；span: (start,end) 在原题干中。"""
    kind: str
    op: str = ""
    value: Optional[Fraction] = None
    span: Tuple[int, int] = (-1, -1)
    children: List["Node"] = field(default_factory=list)

    # ---------- 求值（通道一：递归） ----------
    def eval(self) -> Fraction:
        if self.kind == "num":
            assert self.value is not None
            return self.value
        if self.kind == "un":
            v = self.children[0].eval()
            return -v if self.op == "-" else (v ** Fraction(1, 2) if self.op == "√" else v)
        if self.kind == "fact":
            n = self.children[0].eval()
            if n.denominator != 1 or n < 0:
                raise ValueError(f"阶乘需非负整数: {n}")
            out = Fraction(1)
            for i in range(2, int(n) + 1):
                out *= i
            return out
        if self.kind == "fn":
            args = [c.eval() for c in self.children]
            if self.op in ("C", "P"):
                n, k = int_chk(args[0]), int_chk(args[1])
                if k < 0 or k > n:
                    return Fraction(0) if self.op == "C" and k > n else _p(n, k)
                return _c(n, k) if self.op == "C" else _p(n, k)
            if self.op == "sqrt":
                v = args[0]
                root = isqrt_frac(v)
                if root is None:
                    raise ValueError(f"非完全平方根式 {v}：确定性求值不支持无理根")
                return root
            if self.op == "abs":
                return abs(args[0])
            if self.op == "ceil":
                return Fraction(math_ceil(args[0]))
            if self.op == "floor":
                return Fraction(math_floor(args[0]))
            raise ValueError(f"未知函数 {self.op}")
        if self.kind == "bin":
            a, b = self.children[0].eval(), self.children[1].eval()
            if self.op == "+":
                return a + b
            if self.op == "-":
                return a - b
            if self.op == "*":
                return a * b
            if self.op == "/":
                if b == 0:
                    raise ValueError("除数为 0")
                return a / b
            if self.op == "^":
                if b.denominator != 1 or abs(b.numerator) > 12:
                    raise ValueError("幂仅支持 |指数|<=12 的整数")
                return _pow(a, int(b))
        raise ValueError(f"非法节点 {self.kind}")

    # ---------- 序列化与复核（通道二：postfix 重放） ----------
    def postfix(self) -> List[Any]:
        out: List[Any] = []
        for c in self.children:
            out += c.postfix()
        if self.kind == "num":
            out.append(("num", str(self.value)))
        elif self.kind == "bin":
            out.append(("bin", self.op))
        elif self.kind == "un":
            out.append(("un", self.op))
        elif self.kind == "fact":
            out.append(("fact", "!"))
        else:
            out.append(("fn", self.op))
        return out

    def to_infix(self) -> str:
        if self.kind == "num":
            v = self.value
            return str(v.numerator) if v.denominator == 1 else f"({v.numerator}/{v.denominator})"
        if self.kind == "un":
            return f"({self.op}{self.children[0].to_infix()})"
        if self.kind == "fact":
            return f"({self.children[0].to_infix()})!"
        if self.kind == "fn":
            return f"{self.op}(" + ",".join(c.to_infix() for c in self.children) + ")"
        return f"({self.children[0].to_infix()}{self.op}{self.children[1].to_infix()})"

    def ops(self) -> List[str]:
        me = [] if self.kind == "num" else [self.op or self.kind]
        for c in self.children:
            me += c.ops()
        return me

    def number_spans(self) -> List[Tuple[int, int, str]]:
        if self.kind == "num":
            return [(self.span[0], self.span[1], str(self.value))]
        return [s for c in self.children for s in c.number_spans()]

    def summary(self) -> Dict[str, Any]:
        return {"infix": self.to_infix(), "ops": self.ops(),
                "spans": self.number_spans(), "postfix": self.postfix()}


def replay_postfix(seq: List[Any]) -> Fraction:
    """独立重放器：按 postfix 表用栈求值（与 Node.eval 两条路径互证）。"""
    stack: List[Fraction] = []
    for kind, tok in seq:
        if kind == "num":
            stack.append(Fraction(tok))
        elif kind == "bin":
            b, a = stack.pop(), stack.pop()
            if tok == "^":
                stack.append(_pow(a, int_chk(b)))
            else:
                stack.append({"+": lambda: a + b, "-": lambda: a - b,
                              "*": lambda: a * b, "/": lambda: _div(a, b)}[tok]())
        elif kind == "un":
            v = stack.pop()
            if tok == "-":
                stack.append(-v)
            elif tok == "√":
                r = isqrt_frac(v)
                if r is None:
                    raise ValueError("非完全平方根式")
                stack.append(r)
            else:
                stack.append(v)
        elif kind == "fact":
            stack.append(_fact(stack.pop()))
        elif kind == "fn":
            args = [stack.pop() for _ in range(2 if tok in ("C", "P", "pow") else 1)][::-1]
            if tok == "C":
                stack.append(_c(int_chk(args[0]), int_chk(args[1])))
            elif tok == "P":
                stack.append(_p(int_chk(args[0]), int_chk(args[1])))
            elif tok == "sqrt":
                stack.append(_need(isqrt_frac(args[0]), "非完全平方根式"))
            elif tok == "abs":
                stack.append(abs(args[0]))
            elif tok == "ceil":
                stack.append(Fraction(math_ceil(args[0])))
            elif tok == "floor":
                stack.append(Fraction(math_floor(args[0])))
            elif tok == "pow":
                stack.append(_pow(args[0], int_chk(args[1])))
            else:
                raise ValueError(f"未知函数 {tok}")
    if len(stack) != 1:
        raise ValueError("重放栈不平衡")
    return stack[0]


# ------------------------------------------------------------------ 小工具

import math


def int_chk(v: Fraction) -> int:
    if v.denominator != 1:
        raise ValueError(f"需要整数参数: {v}")
    return int(v)


def _need(x, msg):
    if x is None:
        raise ValueError(msg)
    return x


def _div(a: Fraction, b: Fraction) -> Fraction:
    if b == 0:
        raise ValueError("除数为 0")
    return a / b


def _fact(n: Fraction) -> Fraction:
    i = int_chk(n)
    if i < 0:
        raise ValueError("负数阶乘")
    out = Fraction(1)
    for k in range(2, i + 1):
        out *= k
    return out


def _c(n: int, k: int) -> Fraction:
    return _c_impl(n, k)


def _c_impl(n: int, k: int) -> Fraction:
    if k < 0 or k > n:
        return Fraction(0)
    num = Fraction(1)
    den = Fraction(1)
    for i in range(1, k + 1):
        num *= (n - k + i)
        den *= i
    return num / den


def _p(n: int, k: int) -> Fraction:
    if k < 0 or k > n:
        return Fraction(0)
    out = Fraction(1)
    for i in range(n, n - k, -1):
        out *= i
    return out


def _pow(a: Fraction, e: int) -> Fraction:
    if e < 0:
        if a == 0:
            raise ValueError("0 的负幂")
        return 1 / _pow(a, -e)
    return a ** e


def isqrt_frac(v: Fraction) -> Optional[Fraction]:
    if v < 0:
        return None
    rn = _isqrt_int(v.numerator)
    rd = _isqrt_int(v.denominator)
    if rn * rn == v.numerator and rd * rd == v.denominator:
        return Fraction(rn, rd)
    return None


def _isqrt_int(x: int) -> int:
    return math.isqrt(x)


def math_ceil(v: Fraction) -> int:
    return -((-v.numerator) // v.denominator)


def math_floor(v: Fraction) -> int:
    return v.numerator // v.denominator


# ------------------------------------------------------------------ 解析器

_FN_CALL = ("C", "P", "sqrt", "ceil", "floor", "pow", "abs")


class _Lexer:
    def __init__(self, text: str):
        self.text = text
        self.i = 0

    def peek(self) -> Optional[str]:
        self._skip_ws()
        return self.text[self.i] if self.i < len(self.text) else None

    def _skip_ws(self) -> None:
        while self.i < len(self.text) and self.text[self.i] in " \t":
            self.i += 1

    def eat(self, ch: str) -> bool:
        self._skip_ws()
        if self.i < len(self.text) and self.text[self.i] == ch:
            self.i += 1
            return True
        return False


def parse_expr(text: str, offset: int = 0, stem: Optional[str] = None) -> Node:
    """解析等宽归一化后的算术文本；span 记 (offset+start, offset+end)。

    文法：add := mult (('+'|'-') mult)*；mult := unary (('*'|'/'|'%') unary)*；
    unary := ('+'|'-')* ('√')* power；power := post ('^' unary)?（右结合，
    一元负号优先级低于幂：-2^2=-4）；post := atom ('!')*；
    atom := NUM | FN '(' args ')' | '(' add ')'。
    """
    lx = _Lexer(text)
    base = offset

    def fail(msg: str) -> "ValueError":
        return ValueError(f"{msg} @{'偏移' if stem is not None else ''}{lx.i}")

    def additive() -> Node:
        n = multiplicative()
        while True:
            lx._skip_ws()
            ch = lx.peek()
            if ch is None:
                return n
            if ch in "+-":
                lx.i += 1
                r = multiplicative()
                n = Node("bin", ch, span=(n.span[0], r.span[1]), children=[n, r])
            else:
                return n

    def multiplicative() -> Node:
        n = unary()
        while True:
            lx._skip_ws()
            ch = lx.peek()
            if ch is None:
                return n
            if ch in "*/%":
                lx.i += 1
                if ch == "%":  # 百分号后缀：x% = x/100（100 为语法合成常数，span=-1 不参与题干锚定）
                    n = Node("bin", "/", span=(n.span[0], n.span[1] + 1),
                             children=[n, Node("num", value=Fraction(100),
                                               span=(-1, -1))])
                    continue
                r = unary()
                n = Node("bin", ch, span=(n.span[0], r.span[1]), children=[n, r])
            else:
                return n

    def unary() -> Node:
        lx._skip_ws()
        start = lx.i
        ch = lx.peek()
        if ch is not None and ch in "+-":
            lx.i += 1
            v = unary()
            return Node("un", ch, span=(base + start, v.span[1]), children=[v])
        if ch == "√":
            lx.i += 1
            v = unary()
            r = isqrt_frac(v.eval())
            if r is None:
                raise fail("非完全平方根式，确定性 AST 不支持无理根")
            return Node("num", value=r, span=(base + start, v.span[1]))
        return power()

    def power() -> Node:
        atom = postfix()
        lx._skip_ws()
        if lx.peek() == "^":
            lx.i += 1
            exp = unary()          # 右结合且指数侧可带一元负号
            return Node("bin", "^", span=(atom.span[0], exp.span[1]),
                        children=[atom, exp])
        return atom

    def postfix() -> Node:
        n = atom()
        while True:
            lx._skip_ws()
            if lx.peek() == "!":
                lx.i += 1
                n = Node("fact", "!", span=(n.span[0], base + lx.i - 1 + 1), children=[n])
            else:
                return n

    def atom() -> Node:
        lx._skip_ws()
        start = lx.i
        m = _NUM_RE.match(text, lx.i)
        if m:
            lx.i = m.end()
            return Node("num", value=Fraction(m.group(0)),
                        span=(base + start, base + m.end()))
        ch = lx.peek()
        if ch == "(":
            lx.i += 1
            n = additive()
            if not lx.eat(")"):
                raise fail("括号未闭合")
            return Node(n.kind, n.op, value=n.value,
                        span=(base + start, base + lx.i - 1), children=n.children)
        # 函数名（含中文全角 C（4,2）已被等宽归一化）
        ident = re.match(r"[A-Za-z]+", text[lx.i:])
        if ident and ident.group(0) in _FN_CALL:
            name = ident.group(0)
            save = lx.i
            lx.i += len(name)
            if not lx.eat("("):
                lx.i = save
                raise fail(f"{name} 后必须紧跟括号")
            args = [additive()]
            while lx.eat(","):
                args.append(additive())
            if not lx.eat(")"):
                raise fail(f"{name}(...) 括号未闭合")
            want = 2 if name in ("C", "P", "pow") else 1
            if len(args) != want:
                raise fail(f"{name} 需要 {want} 个参数，得到 {len(args)}")
            return Node("fn", name, span=(base + start, base + lx.i - 1), children=args)
        if ch == "√":
            lx.i += 1
            v = atom()
            r = isqrt_frac(v.eval())
            if r is None:
                raise fail("非完全平方根式")
            return Node("num", value=r, span=(base + start, v.span[1]))
        if ch is not None and not ch.isdigit() and not ch.isspace():
            if re.match(r"[\u4e00-\u9fff]", ch):
                raise fail("题面含中文，非纯算术片段")
            raise fail(f"非法字符 {ch!r}")
        raise fail("表达式意外结束")

    root = additive()
    lx._skip_ws()
    if lx.i < len(text):
        raise fail("尾部有多余内容")
    return root


# ------------------------------------------------------------------ 题面入口

# 弃"最长数字运算片段"：候选 = 显式计算标记后的算式，或整段等宽文本必须
# 整体解析成 AST；解析不过则返回 None（宁缺毋滥）。
_CALC_PREFIX = re.compile(r"^\s*(?:请)?(?:计算|算一算|求)[:：]?\s*")
_Q_SUFFIX = re.compile(r"[=＝?？。.\s]*$")


def parse_stem_expression(stem: str) -> Optional[Node]:
    """从题干构造**带原图跨度**的 AST；失败返回 None（不猜测）。"""
    if not stem:
        return None
    norm_all = stem.translate(_EQUIV)
    s = _CALC_PREFIX.sub("", norm_all)
    lead = len(norm_all) - len(s)          # 前缀剥离偏移（等宽操作，不破坏 span 映射）
    s = _Q_SUFFIX.sub("", s)
    if not s or re.search(r"[\u4e00-\u9fff]", s):
        return None
    try:
        node = parse_expr(s, offset=lead, stem=norm_all)
    except (ValueError, ZeroDivisionError):
        return None
    node.eval()  # 结构合法但语义非法（无理根等）在此拦截
    return node


def computation_evidence(root: Node) -> Dict[str, Any]:
    """R10-C 工单：结果等于题干已知数值时的**结构化计算证据**。

    成立条件：根为运算节点，且 ①含 幂/阶乘/C/P/√/sqrt 等强算子之一，
    或 ②二元节点左右子树数值互不相等（真实合并了两条独立信息）。
    仅数字复制或 a±a/0·a 类退化不算证据。
    """
    strong = {"^", "!", "C", "P", "√", "sqrt"}
    has_strong = any(o in strong for o in root.ops())
    distinct = False
    if root.kind == "bin" and len(root.children) == 2:
        try:
            l, r = root.children[0].eval(), root.children[1].eval()
            distinct = l != r
            if root.op in ("*", "/") and (l == 0 or r == 0 or r == 1 or l == 1):
                distinct = distinct and not (root.op == "*" and (l == 1 or r == 1))
        except (ValueError, ZeroDivisionError):
            distinct = False
    return {"has_strong_operator": has_strong, "combines_distinct_values": distinct,
            "ok": bool(has_strong or distinct)}


def spans_anchor_stem(stem: str, spans: List[List[Any]]) -> bool:
    """复核通道：每个数值节点 span 必须在（等宽归一化）题干上锚定同值数字。

    span 来自 Solver 的 AST；Verifier 独立按偏移重取子串并比对，防止
    "表达式对但数字来源假"的构造。
    """
    norm = stem.translate(_EQUIV)
    for s, e, v in spans:
        if int(s) < 0:
            continue  # 语法合成常数（%/100）
        seg = norm[int(s):int(e)]
        m = _NUM_RE.match(seg)
        if not m or m.group(0) != seg:
            return False
        try:
            if Fraction(seg) != Fraction(str(v)):
                return False
        except (ValueError, ZeroDivisionError):
            return False
    return True
