"""**根修：`_assigned_in` 的作用域必须是词法的，不能是"整棵树"。**

`scopes = [tree] + 各函数/类` ⇒ 模块作用域用 `ast.walk(tree)`
一路下钻进**所有函数体**，于是把 `_bundle_steps()` 里的**局部** `p`
当成 `main()` 里那个 `r` 的来源：

    main():        ... _tmp.write_text(...)        ← 写出
      _tmp = ROOT / "evidence" / f"_tmp_self_{r['artifact']}"
    _bundle_steps(): p = EV / "R14_VERIFICATION_BUNDLE.json"   ← **读**
    某个函数:        r = json.loads(p.read_text(...))
    ⇒ `r → p → BUNDLE` 被串成"index 写出了 bundle"

⇒ 同名局部变量跨函数串联，是**假阳性的来源**，
   而判据文档自己承诺"变量回溯只在同一函数体内进行"——
   承诺与实现不符，这轮把它实现出来。
"""
import io

P = "scripts/r14_attribute_producer.py"
s = io.open(P, encoding="utf-8").read()

old = '''    def _assigned_in(scope, var, _seen=None):'''
assert s.count(old) == 1, s.count(old)

# 1) 先建"内层作用域归属表"
owner_code = '''    # **词法作用域归属表**：每个节点属于哪个**最内层**作用域。
    # 没有它，`_assigned_in(模块, x)` 会下钻进所有函数体，
    # 把别的函数的**同名局部变量**串成来源（本轮实测假阳性根因）。
    _inner_scopes = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))]
    owner_of = {id(tree): tree}
    for _sc in _inner_scopes:
        for _n in ast.walk(_sc):
            owner_of[id(_n)] = _sc

    def _assigned_in(scope, var, _seen=None):'''
s = s.replace(old, owner_code)

# 2) 回溯只认同一个内层作用域里的赋值
old2 = '''        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == var
                    for t in node.targets):
                vals = set(_json_lits(node.value))'''
assert s.count(old2) == 1, s.count(old2)
new2 = '''        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == var
                    for t in node.targets):
                # **词法边界**：`scope` 是模块时，只认**不在任何函数体内**
                # 的赋值；`scope` 是函数时，只认**归属该函数**的赋值
                # （嵌套函数里的局部变量不算外层来源）。
                if owner_of.get(id(node)) is not scope:
                    continue
                vals = set(_json_lits(node.value))'''
s = s.replace(old2, new2)

# 3) `_resolve` 里 Name 的回溯同理（`f.value` 本身在 scope 内）
old3 = '''    def _resolve(target, scope):
        """把写出目标解析成件名集合（只走语法树，不走文本邻近）。"""
        got = set(_json_lits(target))'''
assert s.count(old3) == 1, s.count(old3)
new3 = '''    def _resolve(target, scope):
        """把写出目标解析成件名集合（只走语法树，不走文本邻近）。

        **写出语句本身必须归属 `scope`**：
        上一版模块作用域会把每个函数的写句**再解析一遍**，
        且用全局的变量表 —— 与词法作用域不符，
        也正是跨函数串联的入口。
        """
        if owner_of.get(id(target.parent if hasattr(target, "parent")
                           else target)) is not None and False:
            return set()
        got = set(_json_lits(target))'''
s = s.replace(old3, new3)
io.open(P, "w", encoding="utf-8").write(s)
import py_compile
py_compile.compile(P, doraise=True)
print("作用域归属表已接入（第 3 步是占位，下面单独修）")
