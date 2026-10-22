# -*- coding: utf-8 -*-
"""R14-E：生产回退链——题面选项文字缺失时用**像素锚点**恢复选项标签。

真实截图 OCR 常返回 0 个选项（tight 变体）；选项字母内嵌在版式里：
锚点切分要求字母层恰 expect 个、等距（0.6-1.5×pitch）、每单元字母/图形
一一对应且行线对齐——这是**版式结构验证**（字母身份仍按行序 A… 指派，
label_assignment=row_major），不是逐字 OCR。不猜内容。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from copy import deepcopy  # noqa: E402

from assessment_agent.schemas import Question  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

GOLD = {"ft-mirror-04": "A", "ft-rot90-01": "A", "ft-trans-07": "A",
        "ft-overlay-12": "A", "ft-mirror-04--tight": "A"}
LETTERS = "ABCDEFGH"


def probe_anchor_question(q: Question, reg) -> tuple:
    """题面 <2 选项时：扫 2..6 找锚点成立版式；返回 (替换选项的题, notes)。"""
    from assessment_agent.tools.figure_page_localizer import localize_page
    from assessment_agent.tools.figure_tools import load_mask
    try:
        mask = load_mask(str(q.image_path))
    except Exception as exc:  # noqa: BLE001
        return q, [f"anchor_probe_mask_error:{type(exc).__name__}"]
    for cand_n in range(2, min(7, len(LETTERS) + 1)):
        trial = localize_page(mask, n_options=cand_n)
        tc = trial.get("option_regions") or []
        if (len(tc) == cand_n
                and all(c.get("provenance") == "localizer+label_anchor"
                        and c.get("label_bbox") for c in tc)):
            labels = LETTERS[:cand_n]
            q2 = q.model_copy(update={
                "options": [{"label": l, "text": ""} for l in labels]})
            return q2, [f"labels_from_pixel_anchors:n{cand_n}"]
    return q, ["anchor_probe:no_label_anchors"]


def main() -> int:
    reg = SkillRegistry()
    out = {}
    for pid, gold in GOLD.items():
        q = Question.model_validate(json.loads(
            (ROOT / "runs" / f"r13g-{pid}" / "question.json").read_text(encoding="utf-8")))
        st = make_settings(Path(tempfile.mkdtemp()))
        st.r4g_layout = "pixel_only"
        ctx = SkillContext(settings=st)
        fr = reg.require("figure_reasoning")
        chain = []
        n_opts = len(q.options or [])
        if n_opts < 2:
            q, probe_notes = probe_anchor_question(q, reg)
            chain += probe_notes
        sol = fr.solve(q, ctx)
        chain.append(f"solver:{sol.status}")
        act = fa = None
        fails = []
        if sol.status == "solved":
            vr = Verifier(st, registry=reg).verify(q, sol)
            act, fa = vr.action, vr.final_answer
            fails = [c.name for c in vr.checks if not c.passed]
        rs = release_status(act, fa, gold)
        m = sol.meta or {}
        prov = m.get("region_provenance") or {}
        opt_cells = prov.get("option_cells") or []
        out[pid] = {
            "solver_status": sol.status, "solver_answer": sol.answer,
            "verify_action": act, "final_answer": fa,
            **rs, "failed_checks": fails[:3],
            "options_used": [o.label for o in q.options or []],
            "chain": chain,
            "option_boxes": [[c["bbox_page"], c.get("label_bbox"), c["ink"]]
                             for c in opt_cells][:6],
            "option_sha256": [c["pixel_sha"] for c in opt_cells][:6],
            "provenance": [c.get("provenance") for c in opt_cells][:6],
            "solver_error": (sol.error or "")[:200],
        }
        r = out[pid]
        print(f"{pid} -> {r['solver_status']} {r['solver_answer']} | "
              f"{r['verify_action']} {r['final_answer']} | released "
              f"{r.get('released')} {r.get('release_kind')} | {chain} | "
              f"{r['solver_error'][:60]}")
    dst = ROOT / "evidence" / "R14E_REAL_PAGE_REPLAY_V3.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("written", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
