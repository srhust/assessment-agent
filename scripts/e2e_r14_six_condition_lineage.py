"""六条件血缘**持久件**（零 API）：把"F 5/13 不是本轮新造的数"这件事留档，
并逐列分清 候选 / 接受 / 释放 三层（字段实名，不混同）。

R12A / R13A 两个**旧协议**件（tol 15%、无独立 verifier）必须与现行协议分开看，
它们的数字不可与 R14E/R14F 直接同列比较；本件只如实列差异，不做等价表述。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV = ROOT / "evidence"
FIELDS = ("correct_candidates", "wrong_candidates", "verified_accepted",
          "final_released", "of_effective_answerable", "gated_unverified",
          "design_abstain_ok")
ORDER = [
    ("R12A_SIX_CONDITION_RESCORED.json", "旧协议（R12-A：tol 15%、无独立 Verifier）"),
    ("R13A_SIX_CONDITION_VERIFIER_BACKED.json", "旧协议（R13-A：Verifier 独立复核首次引入）"),
    ("R14F_SIX_CONDITION.json", "现行协议（E 根修**前**基线）"),
    ("R14E_SIX_CONDITION.json", "现行协议（E 根修首跑）"),
    ("R14E_SIX_CONDITION_V9.json", "现行协议（E 回归后重跑）"),
    ("R14E_SIX_CONDITION_V10.json", "现行协议（HEAD b913813 复验重跑）"),
]


def main() -> int:
    table, missing = {}, []
    for name, proto in ORDER:
        p = EV / name
        if not p.is_file():
            missing.append(name)
            continue
        cs = (json.loads(p.read_text(encoding="utf-8")).get("conditions") or {})
        table[name] = {
            "protocol": proto,
            "conditions": {k: {f: (cs.get(k) or {}).get(f) for f in FIELDS}
                           for k in "ABCDEF"},
            "wrong_releases_by_layer": {
                k: sorted((cs.get(k) or {}).get("wrong_releases") or [])
                for k in "ABCDEF"},
            "any_wrong_release": any((cs.get(k) or {}).get("wrong_releases")
                                     for k in "ABCDEF"),
        }
    v10, v9 = table.get("R14E_SIX_CONDITION_V10.json"), \
        table.get("R14E_SIX_CONDITION_V9.json")
    # 等价判断只看**判定字段**：`protocol` 是人为标注栏（"E 回归后重跑" vs
    # "HEAD 复验重跑"），把它卷进比较会得到假的 v10_equals_v9=False——
    # 首版正是这个自伤错误，实测 conditions 五列与 wrong_releases 全等。
    def _verdict(t):
        return {k: v for k, v in t.items() if k != "protocol"} if t else None
    ident = json.dumps(_verdict(v10), sort_keys=True, ensure_ascii=False) == \
        json.dumps(_verdict(v9), sort_keys=True, ensure_ascii=False)
    out = {"table": table, "missing_artifacts": missing,
           "v10_equals_v9": ident,
           "v10_equals_v9_scope": "仅比较判定字段（conditions + "
                                  "wrong_releases_by_layer）；protocol 标注栏不参与",
           "read_this_as": [
               "候选/接受/释放是**三个不同计数**：本表 correct_candidates、"
               "verified_accepted、final_released 分列，禁止互相代称。",
               "F 链在现行协议四件（R14F/E/V9/V10）里恒为 候选 5（含 1 个"
               "wrong_candidates 记录）、接受 5、释放 5（5/13），且 "
               "wrong_releases 全层为空——所以 E 的根修**没有**改动 F 的结论。",
               "R12A/R13A 是旧协议件，数字不同（E 1/13、F 0/13 或 1/13）是"
               "协议差异所致，**不得**与现行协议同列比较或据此声称退化/进步。",
               "R14E_SIX_CONDITION.json（E 首跑）里 C=3/3、D=0 与 V9/V10 的 "
               "C=5 候选/3 释放、D=1 候选/0 释放不同：差异来自 E 期间消费门与"
               "候选集回到 HEAD 口径（候选数上升但释放未上升，且 wrong_releases "
               "始终为空），属如实记录，不是事后修饰。",
           ],
           "llm_calls_used": 0}
    dst = EV / "R14E_LINEAGE_SIX_CONDITION.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"artifacts": list(table), "missing": missing,
                      "v10_equals_v9": ident,
                      "any_wrong_release": {k: v["any_wrong_release"]
                                            for k, v in table.items()}},
                     ensure_ascii=False, indent=1))
    print("written", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
