import io

P = "scripts/r14_verify_all.py"
s = io.open(P, encoding="utf-8").read()
old = '''        if "R14_VERIFICATION_BUNDLE" in path or SCRATCH.match(base):
            continue'''
assert s.count(old) == 1
new = '''        if "R14_VERIFICATION_BUNDLE" in path or SCRATCH.match(base):
            continue
        # **`SCRATCH` 是正则，且只匹配**根目录**的 `t_/tmp_`**：
        # 我把 bundle 输出写成 `t_va.txt`（根目录）它能抓到，
        # 但 `evidence/t_att.txt` 这类**带目录前缀**的临时件
        # 因为 `base` 只剩文件名、模式又无 `^` 锚定外的分支 ⇒
        # 实际是**匹配了才被我删掉才没暴露**（本轮实测：
        # 探针输出留在根目录时入口守卫照样拒跑，说明判据有效）。
        # 这里不加新豁免，只记录：**任何"临时件"命名都必须在
        # `SCRATCH` 覆盖范围内**，否则它会变成下一轮的死锁源。'''
io.open(P, "w", encoding="utf-8").write(s.replace(old, new))
import py_compile

py_compile.compile(P, doraise=True)
print("注释落地 ok")
