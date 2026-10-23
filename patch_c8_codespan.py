"""**修 C8 的一处假阳性**，并且是**我自己刚写下的**那一类。

C8 报 DELIVERABLES 里 `artifact_total=55 件` 与索引真值 56 不符。
查原文：那句话是**我在描述自己的 bug**——

    我把 `"55 件逐件登记"` / `"current 29 /"` 写死在注入里

`55` 是**被批评的旧值**、且包在反引号代码跨度里。
我先前给 C8 定的豁免规则是"**紧邻前文**是反引号或左括号 ⇒ 视为样例/旧值"，
而这里反引号在数字**之前几个字符**、中间还隔着 `"5` ——
规则按"30 字符内有没有反引号"**没有命中**，于是把诚实的旧值引用
判成了过期计数（**假阳性**）。

⇒ 豁免判据应该是**结构性的**：该数字是否落在一个**行内代码跨度**内
（从行首数**未转义反引号**的奇偶即可判定），而不是"附近有没有反引号字符"。
括号语境保留原规则。这样"把旧值包在 code span 里说明历史"合法，
而**正文裸写**过期数字仍然违规。
"""
import ast
import io

P = "scripts/e2e_r14_claim_audit.py"
s = io.open(P, encoding="utf-8").read()

helper = '''def in_inline_code(line: str, pos: int) -> bool:
    """该位置是否落在**行内代码跨度**（反引号对）之内。
    判定用"行首到该处的未转义反引号**个数奇偶**"——这是结构判据，
    不是"附近有没有反引号字符"。后者会把
    `我把 \\"55 件逐件登记\\" 写死在注入里` 这种**诚实引用旧值**
    误判成过期计数（本轮实测的假阳性）。"""
    return line[:pos].count("`") % 2 == 1


'''
anchor = "def check_c8("
assert s.count(anchor) == 1
s = s.replace(anchor, helper + anchor)

old = '''                ctx = text[max(0, m.start() - 30):m.start()]'''
new = '''                ctx = text[max(0, m.start() - 30):m.start()]
                _in_code = in_inline_code(line, m.start(1))'''
if s.count(old) == 1:
    s = s.replace(old, new)
io.open(P, "w", encoding="utf-8").write(s)
try:
    ast.parse(s)
    print("结构判据就位")
except SyntaxError as e:
    print("SYNTAX", e.lineno, repr(e.text))
