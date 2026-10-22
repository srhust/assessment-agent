import sys, json
sys.path.insert(0, "src")
import numpy as np
from pathlib import Path
from assessment_agent.tools.figure_page_localizer import localize_page
from assessment_agent.tools.figure_tools import load_mask

ROOT = Path(".")
ITEMS = {}
for l in (ROOT / "data/benchmark_figshots/dev/figshots.jsonl").read_text(
        encoding="utf-8").splitlines():
    if l.strip():
        d = json.loads(l)
        ITEMS[d["id"]] = d

out = {"pages": [], "note": "Stage E 锚点/消费状态全量扫描（零 API，页级不变量）"}
cases = []
for p in sorted((ROOT / "runs").glob("r13g-ft-*")):
    f = p / "image" / "original.png"
    if f.is_file():
        cid = p.name.replace("r13g-ft-", "")
        base = ITEMS.get("ft-" + cid)
        cases.append(("real13g", cid, f, len(base["options"]) if base else 4))
for f in sorted((ROOT / "data/benchmark_figshots/images/dev").glob("*.png")):
    base = ITEMS.get(f.stem)
    cases.append(("dev15", f.stem, f, len(base["options"]) if base else 4))
for f in sorted((ROOT / "data/benchmark_figshots_perturb/images").glob("*.png")):
    base = ITEMS.get(f.stem.split("--")[0])
    cases.append(("perturb", f.stem, f, len(base["options"]) if base else 4))
for f in sorted((ROOT / "data/screens/images").glob("*.png"))[:12]:
    cases.append(("screens", f.stem, f, 4))

for kind, cid, f, n in cases:
    m = np.asarray(load_mask(str(f)), dtype=bool)
    r = localize_page(m, n_options=n)
    cs = r.get("option_regions") or []
    anchored = bool(cs) and all(c.get("label_bbox")
                                and c.get("provenance") == "localizer+label_anchor"
                                for c in cs)
    out["pages"].append({
        "kind": kind, "id": cid, "n_declared": n,
        "opts": len(cs), "label_anchored": anchored,
        "consumable_by_solver": anchored,     # R14-E 消费门
        "opt_bands": sorted({tuple(c["band"]) for c in cs}),
        "stem": [[s["bbox_page"], s.get("grid")] for s in r.get("stem_regions") or []],
        "notes": r.get("notes") or [],
    })
a = [p for p in out["pages"] if p["label_anchored"]]
out["summary"] = {
    "total": len(out["pages"]),
    "anchored": len(a),
    "by_kind": {k: {"total": sum(1 for p in out["pages"] if p["kind"] == k),
                    "anchored": sum(1 for p in out["pages"]
                                    if p["kind"] == k and p["label_anchored"])}
                for k in ("real13g", "dev15", "perturb", "screens")},
    "perturb_anchored_ids": [p["id"] for p in out["pages"]
                             if p["kind"] == "perturb" and p["label_anchored"]],
}
Path("evidence/R14E_ANCHOR_SWEEP.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out["summary"], ensure_ascii=False, indent=1))
