"""**自伤事故**与修复：

我在 t_small.py 里写了一次 `s.replace(chr(34)*2, '')`（把两个连续双引号
替换成空），于是把 r14_verify_all.py 的模块 docstring 开闭三引号各吃掉一个
字符，整个文件不再可解析（SyntaxError）。
同一脚本还差点把 `missing = [art for _, art, _, _ in STEPS]` 改成
`[art for ...]`（未定义名）——那次因为 assert 失败而没写盘。

修法：从 git 取回**损坏前**的版本（`bc6cae6`，含正确 docstring 与
SNAPSHOT_MODE），再**逐字重放**后来真正需要的三项改动：
  1. `worktree_clean()` 忽略 scratch 运行日志（t_*）与 bundle 自身产物；
  2. STEPS 改 5 元组：显式声明每步 `--out` 形态（bare / cwd_relative / fixed），
     并新增 A4 预检"这步将写到哪个绝对路径"必须等于现行件；
  3. 真实截图复放脚本改为默认写 V8（已在独立提交里完成）。
本次不再用"字符串盲替换"改这个文件，改为**整文件重写**，重写后立即
`ast.parse` + `--help` 级干跑验证。
"""
import ast
import io
import subprocess
import sys
from pathlib import Path

good = subprocess.run(["git", "show", "bc6cae6:scripts/r14_verify_all.py"],
                      capture_output=True, text=True, encoding="utf-8").stdout
assert good.startswith('"""R14'), "取回的版本仍损坏"
ast.parse(good)
io.open("scripts/r14_verify_all.py", "w", encoding="utf-8").write(good)
print("restored from bc6cae6, lines", len(good.splitlines()))
