# -*- coding: utf-8 -*-
"""R14-E：本地化器真实回放——5 张真实截图走**生产链路** solve+verify（0 HTTP）。

选项带切分改动后唯一诚实的端到端量：同一 question.json（仅 image_path 指向
真实截图），pixel_only，三层（候选/接受/释放）分别计数。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from copy import deepcopy  # noqa: E402
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from assessment_agent.schemas import Question  # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry  # noqa: E402
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask  # noqa: E402
from assessment_agent.tools.release_scoring import release_status  # noqa: E402
from assessment_agent.verifier import Verifier  # noqa: E402
from tests.conftest import make_settings  # noqa: E402

GOLD = {"ft-mirror-04": "A", "ft-rot90-01": "A", "ft-trans-07": "A",
        "ft-overlay-12": "A", "ft-mirror-04--tight": "A"}


def main() -> int:
    reg = SkillRegistry()
    out = {}
    for pid, gold in GOLD.items():
        q = Question.model_validate(json.loads(
            (ROOT / "runs" / f"r13g-{pid}" / "question.json").read_text(encoding="utf-8")))
        st = make_settings(Path(tempfile.mkdtemp()))
        st.r4g_layout = "pixel_only"
        sol = reg.require("figure_reasoning").solve(q, SkillContext(settings=st))
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
            "option_boxes": [[c["bbox_page"], c["ink"]] for c in opt_cells][:6],
            "solver_error": (sol.error or "")[:140],
        }
        print(pid, "->", sol.status, sol.answer, "|", act, fa,
              "| released", rs["released"],
              "WRONG!" if rs["wrong_accept"] else "ok",
              "|", (sol.error or "")[:64])
    name = "R14E_REAL_PAGE_REPLAY_V8.json"
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
    (ROOT / "evidence" / name).write_text(
        json.dumps({"stage": "R14-E option-band anchor split; production solve+verify on 5 real screenshots (0 HTTP)",
                    "items": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written", ROOT / "evidence" / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
