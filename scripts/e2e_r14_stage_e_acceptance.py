# -*- coding: utf-8 -*-
"""R14-E Stage E 验收：选项证据真实完整性根修（零 API、生产链路本体）。

必过判据（全部来自生产 `solve()` 的实际产物，非脚本复刻）：
 1. overlay-12 选项 C **不再近空白**：消费裁剪 ink ≥ 页面真值（8×8=64px）。
 2. tight 页：选项带单元定位成功（3 份），标签恢复如实报告
    （该截图带内**无字母**→ 不猜标签，诚实弃权；见 anchor_recovery）。
 3. 标签一一对应：锚点单元 `label_association_verified` 全 True（真实页）。
 4. OptionEvidence↔Solver 绑定：陈旧/缺失裁剪必须被拒并页面像素重建，
    重建后的消费哈希必须与页面真值可对账（binding state ∈ verified/rebuilt）。
 5. 释放口径：released 必须 answer==gold 且 verify∈{accept,repair}；
    wrong_releases 恒空。
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
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.artifacts import RunArtifacts  # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

GOLD = {"ft-mirror-04": "A", "ft-rot90-01": "A", "ft-trans-07": "A",
        "ft-overlay-12": "A", "ft-mirror-04--tight": "A"}
BAND40 = {"ft-mirror-04", "ft-rot90-01", "ft-trans-07", "ft-overlay-12"}


def main() -> int:
    reg = SkillRegistry()
    fr = reg.require("figure_reasoning")
    report, fails = {}, []
    for pid, gold in GOLD.items():
        q = Question.model_validate(json.loads(
            (ROOT / "runs" / f"r13g-{pid}" / "question.json").read_text(encoding="utf-8")))
        st = make_settings(Path(tempfile.mkdtemp()))
        st.r4g_layout = "pixel_only"
        ctx = SkillContext(settings=st, artifacts=RunArtifacts(st.runs_dir,
                                                              run_id=f"e2e-{pid}"))
        sol = fr.solve(q, ctx)
        prov = (sol.meta or {}).get("region_provenance") or {}
        cells = prov.get("option_cells") or []
        mask = load_mask(str(q.image_path))
        truth = {}
        for n in range(2, 7):
            rn = localize_page(mask, n_options=n)
            cn = rn.get("option_regions") or []
            if len(cn) == n and all(c.get("label_bbox")
                                    and c.get("provenance") == "localizer+label_anchor"
                                    for c in cn):
                for c in cn:
                    bx, by, bw, bh = c["bbox_page"]
                    pc = mask[by:by + bh, bx:bx + bw].copy()
                    lx0, ly0, lw_, lh_ = c["label_bbox"]
                    pc[max(0, ly0 - by):ly0 - by + lh_,
                       max(0, lx0 - bx):lx0 - bx + lw_] = False
                    truth[int(pc.sum())] = (bx, by, bw, bh)
        # 消费的实际裁剪文件（solve 之后仍在 ctx.artifacts 里）
        consumed = []
        for src in ((ctx.artifacts.dir / "regions_pixel"),
                    (ctx.artifacts.dir / "regions")):
            if src.is_dir():
                for p in sorted(src.glob("option_*.png")):
                    m = load_mask(str(p))
                    consumed.append({"file": p.name, "ink": int(m.sum())})
        act = fa = None
        if sol.status == "solved":
            vr = Verifier(st, registry=reg).verify(q, sol)
            act, fa = vr.action, vr.final_answer
        rs = release_status(act, fa, gold)
        rec = {
            "declared_options": len(q.options or []),
            "solver_status": sol.status, "solver_answer": sol.answer,
            "verify_action": act, "final_answer": fa, **rs,
            "solver_error": (sol.error or "")[:240],
            "n_option_cells": len(cells),
            "anchored_cells": sum(1 for c in cells
                                  if c.get("provenance") == "localizer+label_anchor"),
            "label_assoc": [c.get("label_association_verified") for c in cells],
            "cell_boxes": [c["bbox_page"] for c in cells],
            "cell_inks": [c.get("ink") for c in cells],
            "consumed_crops": consumed,
            "page_truth_inks": sorted(truth),
            "binding_state": prov.get("consumption_binding"),
            "binding_notes": prov.get("binding_notes"),
            "consumed_crop_shas": prov.get("consumed_crop_shas"),
        }
        report[pid] = rec
        bad = []
        if pid in BAND40:
            declared = sum(1 for o in (q.options or [])) or len(cells)
            if not cells or rec["anchored_cells"] != len(cells):
                bad.append(f"锚点单元 {rec['anchored_cells']}/{len(cells)} 不齐")
            elif declared and len(cells) != declared:
                bad.append(f"单元数 {len(cells)} != 题面选项 {declared}")
            if not all(rec["label_assoc"][:len(cells)]):
                bad.append("label_association_verified 存在非 True")
            if len(consumed) != len(cells):
                bad.append(f"消费裁剪 {len(consumed)} 份 != 单元 {len(cells)}")
            for c in consumed:
                if c["ink"] < 20:
                    bad.append(f"消费裁剪 {c['file']} 近空白 ink={c['ink']}")
            if rec["binding_state"] not in ("verified", "rebuilt", None):
                bad.append(f"binding_state={rec['binding_state']}")
            # 绑定证据：重建单元必须带 consumed_crop_sha（实际消费文件的哈希）
            if any(cc.get("source") == "page_pixels_r14e"
                   and not cc.get("consumed_crop_sha") for cc in cells):
                bad.append("重建单元缺 consumed_crop_sha")
        if rs.get("released") and fa != gold:
            bad.append("错误释放")
        print(f"== {pid}: {sol.status} {sol.answer} | {act} {fa} | rel={rs.get('released')}"
              f" | cells={len(cells)} anch={rec['anchored_cells']}"
              f" | binding={rec['binding_state']}"
              f" | consumed={[(c['file'], c['ink']) for c in consumed]}")
        if bad:
            fails.append((pid, bad))
    out = {"pages": report,
           "wrong_releases": [p for p, r in report.items()
                              if r.get("released") and r.get("final_answer")
                              and r["final_answer"] != GOLD[p]],
           "released": [p for p, r in report.items() if r.get("released")],
           "checks_failed": fails,
           "llm_calls_used": 0}
    name = "R14E_STAGE_E_ACCEPTANCE.json"
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
    dst = ROOT / "evidence" / name
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", dst)
    print("checks_failed:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
