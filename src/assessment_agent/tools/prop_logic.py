"""R7 C3：命题逻辑工具——中文复句 → 公式 → Z3 判定（工单 §5 C3）。

设计（与 ranking 通道互补，不强把一切塞进排序）：
- 公式小语言：原子（大写字母/带下划线）、! ¬（非）、& ∧（且）、| ∨（或）、
  => →（蕴含）、<=> ↔（充要），括号分组；优先级 => 最低（右结合）。
- 规则解析：单句中文模式（如果…就/只要…就/只有…才/除非…否则/且/或/并非…）
  直接生成公式，原子=题面逐字子句（可溯源）。
- LLM 通道（skill 侧）：模型只负责抽取**子句原文 + 公式结构**，求值全在本地 Z3。
- 判定：entail(premises, concl) = UNSAT(Σ∧¬φ) → 必然为真；
  satisfiable(Σ∧φ) → 可能为真；问"不能推出/一定为假"用对应极性。

无金标、无题面外事实：所有 atom.text 必须逐字命中题干（validate_payload 强制）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 公式小语言

@dataclass
class F:
    """公式节点：kind ∈ {atom, not, and, or, imp, iff}；atom 时 name=符号名。"""
    kind: str
    name: str = ""
    children: Tuple["F", ...] = ()

    def __repr__(self) -> str:
        if self.kind == "atom":
            return self.name
        if self.kind == "not":
            return f"!{self.children[0]!r}"
        op = {"and": "&", "or": "|", "imp": "=>", "iff": "<=>"}[self.kind]
        a, b = self.children
        return f"({a!r} {op} {b!r})" if self.kind in ("imp", "iff") else f"({a!r} {op} {b!r})"


class FormulaError(ValueError):
    pass


_TOK_RE = re.compile(r"<=>|=>|[A-Z][A-Za-z0-9_]*|[!&|()]|¬|∧|∨|→")


def parse_formula(text: str) -> F:
    toks = _TOK_RE.findall(text or "")
    joined = "".join(toks)
    if joined.replace(" ", "") != re.sub(r"\s+", "", text or ""):
        # 存在无法识别的字符（中文原子名等）——拒绝而不是猜测
        raise FormulaError(f"公式含不支持 token: {text[:40]}")
    pos = 0

    def peek() -> Optional[str]:
        return toks[pos] if pos < len(toks) else None

    def eat(want: str) -> None:
        nonlocal pos
        if peek() != want:
            raise FormulaError(f"期望 {want} 得到 {peek()}")
        pos += 1

    def parse_impl() -> F:
        nonlocal pos
        left = parse_or()
        if peek() in ("=>", "→"):
            pos += 1
            return F("imp", children=(left, parse_impl()))  # 右结合
        if peek() in ("<=>",):
            pos += 1
            return F("iff", children=(left, parse_impl()))
        return left

    def parse_or() -> F:
        nonlocal pos
        v = parse_and()
        while peek() in ("|", "∨"):
            pos += 1
            v = F("or", children=(v, parse_and()))
        return v

    def parse_and() -> F:
        nonlocal pos
        v = parse_not()
        while peek() in ("&", "∧"):
            pos += 1
            v = F("and", children=(v, parse_not()))
        return v

    def parse_not() -> F:
        nonlocal pos
        if peek() in ("!", "¬"):
            pos += 1
            return F("not", children=(parse_not(),))
        return parse_atom()

    def parse_atom() -> F:
        nonlocal pos
        t = peek()
        if t == "(":
            pos += 1
            v = parse_impl()
            eat(")")
            return v
        if t and re.fullmatch(r"[A-Z][A-Za-z0-9_]*", t):
            pos += 1
            return F("atom", name=t)
        raise FormulaError(f"意外 token: {t}（文本 {text[:40]}）")

    if not toks:
        raise FormulaError("空公式")
    out = parse_impl()
    if pos != len(toks):
        raise FormulaError(f"公式尾部有多余 token: {toks[pos:][:4]}")
    return out


def atoms_of(f: F, acc: Optional[set] = None) -> set:
    acc = acc if acc is not None else set()
    if f.kind == "atom":
        acc.add(f.name)
    for c in f.children:
        atoms_of(c, acc)
    return acc


# ---------------------------------------------------------------- Z3 判定

def _to_z3(f: F, env: Dict[str, Any]):
    import z3
    if f.kind == "atom":
        return env[f.name]
    if f.kind == "not":
        return z3.Not(_to_z3(f.children[0], env))
    a, b = _to_z3(f.children[0], env), _to_z3(f.children[1], env)
    if f.kind == "and":
        return z3.And(a, b)
    if f.kind == "or":
        return z3.Or(a, b)
    if f.kind == "imp":
        return z3.Implies(a, b)
    return a == b  # iff


def _z3():
    import z3
    return z3


def make_model(formulas: Sequence[F]) -> Tuple[Any, Dict[str, Any]]:
    z3 = _z3()
    names = set()
    for f in formulas:
        names |= atoms_of(f)
    env = {n: z3.Bool(n) for n in sorted(names)}
    solver = z3.Solver()
    for f in formulas:
        solver.add(_to_z3(f, env))
    return solver, env


def entail(premises: Sequence[F], concl: F) -> Optional[bool]:
    """True=必然推出（Σ∧¬φ 不可满足）；False=推不出；None=前提自身矛盾（不可判定）。"""
    z3 = _z3()
    solver, env = make_model(list(premises) + [F("not", children=(concl,))])
    if solver.check() == z3.sat:
        return False
    base, _ = make_model(list(premises))
    return True if base.check() == z3.sat else None  # 前提冲突→退化，如实标不可判定


def possible(premises: Sequence[F], concl: F) -> Optional[bool]:
    z3 = _z3()
    solver, env = make_model(list(premises) + [concl])
    if solver.check() != z3.sat:
        return False
    base, _ = make_model(list(premises))
    return True if base.check() == z3.sat else None


# ---------------------------------------------------------------- 中文单句规则

_RULES = [
    # (pattern, builder(impl→(P,Q 或 Y,X)))
    (re.compile(r"^(?:所有|凡是|一切)(.+?)(?:都|均|就)(.+?)$", re.S), "if"),
    (re.compile(r"^(?:如果|假如|若|要是)(.+?)(?:那么|则|就|便)(.+?)$", re.S), "if"),
    (re.compile(r"^(?:只要|一旦)(.+?)(?:就|便|那么)(.+?)$", re.S), "if"),
    (re.compile(r"^(?:只有|除非)(.+?)才(?:能|会|可能)?(.+?)$", re.S), "only"),
    (re.compile(r"^(.+?)才(?:能|会)(.+?)$", re.S), "only"),
    (re.compile(r"^除非(.+?)否则(.+?)$", re.S), "unless"),
    # 泛化"X都Y"主谓条件句（"提前预约的人都能进馆"）：低优先级、放最后
    (re.compile(r"^(?:所有|凡是)?(.+?)(?:的)?人?(?:都能|都会|都|均)(.+?)$", re.S), "if"),
]


def _canon(t: str) -> str:
    """子句规范化：去空白/标点/时态助词/引导虚词/"的人"类后缀，供原子归并比较。"""
    t = re.sub(r"[\s，。；：、（）()?!？！]", "", t or "")
    t = t.replace("是否", "")
    t = re.sub(r"了|过|着", "", t)
    t = re.sub(r"^(?:那个|该|这|是|都|均|就|便|那么|才|能|会)", "", t)
    t = re.sub(r"(?:的|之)(?:员工|学生|人员|选手|会员|人)$", "", t)
    return t


def clause_atom(text: str, atoms: Dict[str, str]) -> F:
    """子句原文 → 原子符号。规范化后完全一致或互相包含（事实句含条件子句）复用同符。"""
    t = _canon(text)
    for name, orig in atoms.items():
        o = _canon(orig)
        if o == t or (len(t) >= 3 and len(o) >= 3 and (t in o or o in t)):
            return F("atom", name=name)
    name = f"A{len(atoms) + 1}"
    while name in atoms:
        name = f"A{len(atoms) + 1}x"
        if name[-1] == "x" and name.rstrip("x") in atoms:
            name = name.rstrip("x") + str(len(atoms) + 2)
    atoms[name] = text
    return F("atom", name=name)


_META_OPTION = re.compile(r"^(?:无法确定|不确定|不能确定|不一定|可能(?: true)?)")
_QUANTIFIED = re.compile(r"^(?:有些|某些|那些|有的|任何|没有一个|至多|至少)")


def parse_chinese_sentence(sent: str, atoms: Dict[str, str]) -> Optional[F]:
    """单句 → 公式（仅覆盖 gkbook 卡片方向规则的简单形）；失败返回 None。"""
    s = re.sub(r"[。；;]+$", "", (sent or "").strip())
    if not s:
        return None
    if _META_OPTION.match(s):
        return None  # "无法确定…"类元选项：规则层不判定
    # 特称量化句（"有些A是B"）超出命题逻辑表达力：如实拒绝，由调用方降级。
    if _QUANTIFIED.match(s):
        return None
    for pat, kind in _RULES:
        m = pat.match(s)
        if not m:
            continue
        p, q = clause_atom(m.group(1), atoms), clause_atom(m.group(2), atoms)
        if kind == "if":
            return F("imp", children=(p, q))
        if kind == "only":
            return F("imp", children=(q, p))       # 只有P才Q：Q→P
        if kind == "unless":
            return F("imp", children=(F("not", children=(p,)), q))  # 除非P否则Q：¬P→Q
    neg = re.match(r"^并非(.+)$", s)
    if neg:
        inner = parse_chinese_sentence(neg.group(1), atoms)
        return F("not", children=(inner,)) if inner else clause_atom(s, atoms)
    andm = re.split(r"[，,]?并且|[，,]?而且|(?<=[^，])且", s)
    if len(andm) == 2 and all(x.strip() for x in andm):
        a = parse_chinese_sentence(andm[0], atoms)
        b = parse_chinese_sentence(andm[1], atoms)
        if a and b:
            return F("and", children=(a, b))
    orm = re.split(r"或者", s)
    if len(orm) == 2 and all(x.strip() for x in orm):
        a = parse_chinese_sentence(orm[0], atoms)
        b = parse_chinese_sentence(orm[1], atoms)
        if a and b:
            return F("or", children=(a, b))
    # 事实句归并：先试"（没|不|未）+已知原子"的否定事实，再试肯定包含
    m = re.search(r"(?:没有|没|未|不)([^，。；\s]+)$", s)
    if m:
        tail = _canon(m.group(1))
        for name, orig in atoms.items():
            o = _canon(orig)
            if tail and o and min(len(tail), len(o)) >= 3 and (tail in o or o in tail):
                return F("not", children=(F("atom", name=name),))
    c = _canon(s)
    for name, orig in atoms.items():
        o = _canon(orig)
        if c and o and min(len(c), len(o)) >= 3 and (o in c or c in o):
            return F("atom", name=name)
    return clause_atom(s, atoms)


def parse_statements(stem: str) -> Tuple[List[F], Dict[str, str]]:
    """题干 → 前提公式列表 + 原子文本表（整段规则解析，句子按。；切分）。"""
    atoms: Dict[str, str] = {}
    out: List[F] = []
    for sent in re.split(r"[。；;！!?\n]", stem or ""):
        sent = sent.strip()
        if not sent or re.match(r"^以下|^根据|^由此", sent):
            continue
        # 去掉"题干："等前缀
        sent = re.sub(r"^(?:题干|已知|据)[：:].*", "", sent) or sent
        f = parse_chinese_sentence(sent, atoms)
        if f is not None:
            out.append(f)
    return out, atoms


# ---------------------------------------------------------------- LLM payload 校验

FORMULA_RE = re.compile(r"^[A-Z !&|()<=>A-Za-z0-9_¬∧∨→]+$")


def validate_payload(payload: dict, stem: str) -> Tuple[Optional[Dict], Optional[str]]:
    """把 LLM 抽取结果绑定回题干原文；不合格返回 (None, 原因)。

    期望结构：{"premises":[{"text","formula"}],
              "options":[{"label","formula"}],
              "question":"must|possible|impossible"}
    text 必须逐字命中题干（证据绑定）；formula 必须可解析且原子 ≤10。
    """
    if not isinstance(payload, dict):
        return None, "payload_shape"
    qkind = str(payload.get("question") or "must")
    if qkind not in {"must", "possible", "impossible"}:
        return None, f"question_kind:{qkind[:20]}"
    norm = re.sub(r"\s+", "", stem or "")
    prem: List[Tuple[str, F]] = []
    for item in payload.get("premises") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        if re.sub(r"\s+", "", text) not in norm:
            return None, f"premise_text_not_verbatim:{text[:30]}"
        try:
            f = parse_formula(str(item.get("formula") or ""))
        except FormulaError as exc:
            return None, f"premise_formula: {exc}"
        prem.append((text, f))
    if not prem:
        return None, "no_premises"
    opts: List[Tuple[str, F]] = []
    labels_seen = set()
    for item in payload.get("options") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        if not label or label in labels_seen:
            continue
        labels_seen.add(label)
        try:
            f = parse_formula(str(item.get("formula") or ""))
        except FormulaError as exc:
            return None, f"option_formula[{label}]: {exc}"
        opts.append((label, f))
    if not opts:
        return None, "no_options"
    return {"question": qkind, "premises": prem, "options": opts}, None
