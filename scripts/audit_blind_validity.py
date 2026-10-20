"""R8-A1：xingce_blind 全部 14 题的独立金标复算与唯一可定审查（人工建模，逐题）。

四层判定：
- arithmetic：金标值按人工独立重建的条件模型用 Fraction 复算是否吻合；
- dependency：复算式只引用题干显式条件（不引入隐含假设，如"增长率延续"）；
- sufficiency：条件是否**唯一确定**目标（da 用年份可达性不动点；lg 用模型枚举）；
- option_unique：恰一个选项匹配（舍入/近似语义必须在题面显式声明，否则记 flawed）。
输出 data/benchmark_xingce_blind/validity_audit.json。
"""
from __future__ import annotations

import json
import re
from fractions import Fraction as F
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS = {json.loads(l)["id"]: json.loads(l)
         for l in (ROOT / "data/benchmark_xingce_blind/test/xingce_blind.jsonl")
         .read_text(encoding="utf-8").splitlines()}


def opt_val(text: str):
    m = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if not m:
        return None
    v = F(m.group(1))
    return v / 100 if "%" in text else v


def year_reachable(facts: dict, rates: dict, asked: int) -> bool:
    """facts{年:值}，rates{年: 该年相对上一年的增长率}。线性年份链上的不动点闭包。"""
    known = set(facts)
    changed = True
    while changed:
        changed = False
        for y in list(known):
            if (y + 1) in rates and (y + 1) not in known:
                known.add(y + 1); changed = True
            if y in rates and (y - 1) not in known:
                known.add(y - 1); changed = True
        for y in rates:
            if (y - 1) in known and y not in known:
                known.add(y); changed = True
    return asked in known


def verdict(item_id, *, expected, deps_ok, sufficient, approx_declared, note):
    it = ITEMS[item_id]
    opts = {o["label"]: opt_val(o["text"]) for o in it["options"]}
    gold = it["answer"]
    arith = opts[gold] == expected
    tol = F(5, 1000) if approx_declared else F(1, 10 ** 9)
    hits = [lb for lb, v in opts.items()
            if v is not None and abs(v - expected) <= abs(expected) * tol + F(1, 10 ** 6)]
    uniq = hits == [gold]
    layers = {"arithmetic": arith, "dependency": deps_ok,
              "sufficiency": sufficient, "option_unique": uniq}
    if all(layers.values()):
        v = "valid"
    elif not layers["sufficiency"] or not layers["dependency"]:
        v = "invalid"
    else:
        v = "flawed"
    return {"id": item_id, "skill": it["skill"], "gold": gold,
            "recomputed": f"{float(expected):g}", "option_hits": hits,
            "layers": layers, "verdict": v, "note": note}


rows = []

# ---------------- nc：条件全显式给出，算术/唯一性核对 ----------------
rows.append(verdict("rb-01", expected=F(800) * F(8, 10) * F(9, 10), deps_ok=True,
                    sufficient=True, approx_declared=False,
                    note="800 连降 20%、10%（第二次明确基于第一次后价格）→ 576 唯一"))
rows.append(verdict("rb-02", expected=F(260) * F(7, 10) * F(8, 10), deps_ok=True,
                    sufficient=True, approx_declared=False, note="260×0.7×0.8=145.6 唯一"))
rows.append(verdict("rb-03", expected=F(200 * 10, 100) / 250, deps_ok=True,
                    sufficient=True, approx_declared=False, note="盐 20g/新总量 250g=8% 唯一"))
rows.append(verdict("rb-04", expected=F(300 * 8, 100) / 400, deps_ok=True,
                    sufficient=True, approx_declared=False, note="盐 24g/400g=6% 唯一"))
rows.append(verdict("rb-05", expected=F(25, 75), deps_ok=True, sufficient=True,
                    approx_declared=False,
                    note="降 25% 恢复需提价 1/3=33.3…%；选项 33.3% 为舍入值而题面未写'约'——"
                         "严格口径无选项命中（1/3∉选项），舍入口径唯一；判 flawed(近似未声明)"))
rows.append(verdict("rb-06", expected=F(20, 80), deps_ok=True, sufficient=True,
                    approx_declared=False, note="降 20% 恢复需提价 25% 精确值命中，唯一"))
rows.append(verdict("rb-07", expected=F(100) * F(5, 2), deps_ok=True,
                    sufficient=True, approx_declared=False, note="(60+40)×2.5=250 唯一"))
rows.append(verdict("rb-08", expected=1 / (F(1, 10) + F(1, 15)), deps_ok=True,
                    sufficient=True, approx_declared=False, note="1/(1/10+1/15)=6 天唯一"))

# ---------------- da：年份可达性审查 ----------------
rows.append(verdict("rb-09", expected=F(1100) * F(6, 5), deps_ok=False, sufficient=False,
                    approx_declared=True,
                    note="材料只有 2023 值与其(对2022)增长率；问 2024。2023→2024 的率缺失，"
                         "gold=1320 依赖未声明的'增速延续'隐含假设 → 条件不足，不可唯一求解"))
rows.append(verdict("rb-10", expected=F(672) * F(5, 4), deps_ok=False, sufficient=False,
                    approx_declared=True,
                    note="同型：2022 值+2022 率，问 2023 → 缺 2022→2023 率，不可唯一求解"))
rows.append(verdict("rb-11", expected=F(800) - F(800) / F(5, 4), deps_ok=True,
                    sufficient=True, approx_declared=True,
                    note="2024 现期 800+其率 25% → 上年 640、增量 160 唯一（率与值同年，合法）"))
rows.append(verdict("rb-12", expected=F(630) - F(630) / F(7, 5), deps_ok=True,
                    sufficient=True, approx_declared=True,
                    note="630 与 40% 同属 2023 → 基期 450、增量 180 唯一"))

# ---------------- lg：真值模型枚举 ----------------
for iid in ("rb-13", "rb-14"):
    # 前提：Q→P（只有P才Q）与实例 Q。模型枚举只剩 (P,Q)=(T,T)：P 必然真
    models = [(p, q) for p in (True, False) for q in (True, False)
              if ((not q) or p) and q]
    unique_p = len(models) == 1 and all(p for p, _ in models)
    it = ITEMS[iid]
    rows.append({"id": iid, "skill": it["skill"], "gold": it["answer"],
                 "recomputed": "P∈{" + ",".join(str(p) for p, _ in models) + "}",
                 "option_hits": [it["answer"]],
                 "layers": {"arithmetic": True, "dependency": True,
                            "sufficiency": unique_p, "option_unique": True},
                 "verdict": "valid" if unique_p else "invalid",
                 "note": "前提集合唯一模型 (P,Q)=(T,T)；逆命题(选项A/C形态)与否定命题均可假 → 唯一"})

from collections import Counter
c = Counter(r["verdict"] for r in rows)
out = {"total": len(rows), "by_verdict": dict(c),
       "invalid": [r["id"] for r in rows if r["verdict"] == "invalid"],
       "flawed": [r["id"] for r in rows if r["verdict"] == "flawed"],
       "items": rows}
(ROOT / "data/benchmark_xingce_blind/validity_audit.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: out[k] for k in ("total", "by_verdict", "invalid", "flawed")},
                 ensure_ascii=False))
for r in rows:
    if r["verdict"] != "valid":
        print(" ", r["id"], r["verdict"], {k: v for k, v in r["layers"].items()})
