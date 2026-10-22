# -*- coding: utf-8 -*-
"""R14-E：生产链路重放（v4）——绑定 + 锚点恢复 + 标签一一对应 + 释放口径。

生产链路 = `solve()` 本体（含 `_rebind_option_evidence` 消费哈希对账、
`_localizer_fallback_inputs` 像素重建、题面缺选项时的 `labels_from_pixel_anchors`
恢复），不复制几何、不放宽阈值、不硬编码 case-id。金标全部 A（真实截图
人工判读）；`wrong_releases` 必须恒空。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.schemas import Question  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

GOLD = {"ft-mirror-04": "A", "ft-rot90-01": "A", "ft-trans-07": "A",
        "ft-overlay-12": "A", "ft-mirror-04--tight": "A"}
LETTERS = "ABCDEFGH"


def main() -> int:
    reg = SkillRegistry()
    out = {}
    for pid, gold in GOLD.items():
        q0 = Question.model_validate(json.loads(
            (ROOT / "runs" / f"r13g-{pid}" / "question.json").read_text(encoding="utf-8")))
        st = make_settings(Path(tempfile.mkdtemp()))
        st.r4g_layout = "pixel_only"
        ctx = SkillContext(settings=st)
        fr = reg.require("figure_reasoning")
        declared = len(q0.options or [])
        # 生产 solve()：题面 0 选项时走 labels_from_pixel_anchors 恢复
        sol = fr.solve(q0, ctx)
        q = sol.question if getattr(sol, "question", None) else q0
        chain = [f"declared_options:{declared}"]
        prov = (sol.meta or {}).get("region_provenance") or {}
        chain += [f"prov_notes:{n}" for n in (prov.get("notes") or [])]
        act = fa = None
        fails = []
        if sol.status == "solved":
            vr = Verifier(st, registry=reg).verify(q0, sol)
            act, fa = vr.action, vr.final_answer
            fails = [c.name for c in vr.checks if not c.passed]
        rs = release_status(act, fa, gold)
        opt_cells = prov.get("option_cells") or []
        out[pid] = {
            "solver_status": sol.status, "solver_answer": sol.answer,
            "verify_action": act, "final_answer": fa,
            **rs, "failed_checks": fails[:3],
            "options_used": [o.label for o in q.options or []],
            "chain": chain,
            "option_boxes": [[c["bbox_page"], c.get("label_bbox"),
                              c.get("ink")] for c in opt_cells][:6],
            "option_sha256": [c.get("pixel_sha") for c in opt_cells][:6],
            "provenance": [c.get("provenance") for c in opt_cells][:6],
            "label_association_verified": [c.get("label_association_verified")
                                           for c in opt_cells][:6],
            "consumption_binding": prov.get("consumption_binding"),
            "consumed_crop_shas": prov.get("consumed_crop_shas"),
            "solver_error": (sol.error or "")[:220],
        }
        r = out[pid]
        print(f"{pid} -> {r['solver_status']} {r['solver_answer']} | "
              f"{act} {fa} | released {r.get('released')} "
              f"{r.get('release_kind')} | err {r['solver_error'][:50]}")
        for c in opt_cells[:6]:
            print("   cell", c["bbox_page"], "lab", bool(c.get("label_bbox")),
                  "lav", c.get("label_association_verified"),
                  "prov", c.get("provenance"), "sha", c.get("pixel_sha"))
    name = "R14E_REAL_PAGE_REPLAY_V5.json"
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
    dst = ROOT / "evidence" / name
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
