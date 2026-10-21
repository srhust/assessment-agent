# -*- coding: utf-8 -*-
"""R10-D 接线：像素定位回退 → 求解 → 独立哈希复推的闭环测试（零 API）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assessment_agent.schemas import Question, QuestionOption
from assessment_agent.skills import SkillContext, SkillRegistry
from assessment_agent.verifier import Verifier
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "data" / "benchmark_figshots"


def _perturb(pid):
    cor = json.loads((ROOT / "evidence/R10D_PERTURB_CORPUS.json").read_text(encoding="utf-8"))
    return [x for x in cor["items"] if x["id"] == pid][0]


def _src(item_id):
    for l in (FIG / "dev" / "figshots.jsonl").read_text(encoding="utf-8").splitlines():
        it = json.loads(l)
        if it["id"] == item_id:
            return it
    raise KeyError(item_id)


def _question(pid):
    it = _perturb(pid)
    s = _src(it["source_item"])
    q = Question(
        stem=s["stem"],
        options=[QuestionOption(label=o["label"], text="图") for o in s["options"]],
        image_path=str(ROOT / "data" / it["image"]), source="image",
        meta={"vision": {"bbox_format": "pixel_xywh",
                          "image_size": it["regions"]["image_size"],
                          "stem_matrix_crop": str(FIG / s["regions"]["stem_matrix_crop"]),
                          "stem_matrix_grid": s["regions"].get("stem_matrix_grid"),
                          "option_regions": []}},
    )
    return q, s


def test_pixel_fallback_end_to_end_new_layout(tmp_path: Path):
    """VLM 选项区缺失 → 像素定位 → 求解 → 独立哈希复推通过。"""
    q, s = _question("ft-mirror-04--tight")
    settings = make_settings(tmp_path)
    reg = SkillRegistry()
    sol = reg.require("figure_reasoning").solve(q, SkillContext(settings=settings))
    assert sol.status == "solved", sol.error
    assert sol.answer == s["answer"]
    prov = sol.meta["region_provenance"]
    assert prov["algorithm"] == "r10d-page-localizer-v1"
    assert prov["label_assignment"] == "row_major"
    assert all(c["completeness"]["passed"] for c in prov["option_cells"])
    v = Verifier(settings, registry=SkillRegistry()).verify(q, sol)
    assert v.action == "accept", [(c.name, c.passed) for c in v.checks if not c.passed]


def test_forged_region_hash_blocked(tmp_path: Path):
    """区域来源审计不可自证：pixel_sha 改写 → 独立复推失败 → unverified。"""
    q, s = _question("ft-mirror-04--tight")
    settings = make_settings(tmp_path)
    reg = SkillRegistry()
    sol = reg.require("figure_reasoning").solve(q, SkillContext(settings=settings))
    prov = json.loads(json.dumps(sol.meta["region_provenance"]))
    prov["option_cells"][0]["pixel_sha"] = "deadbeef00000000"
    sol.meta["region_provenance"] = prov
    v = Verifier(settings, registry=reg).verify(q, sol)
    assert v.passed is False and v.action == "unverified"
    assert any(c.name == "figure.region_provenance" for c in v.checks)


def test_no_stem_crop_still_abstains(tmp_path: Path):
    """红线：定位器不得替代题干矩阵——无 stem crop 时保持结构化弃权。"""
    q, s = _question("ft-mirror-04--tight")
    q.meta["vision"]["stem_matrix_crop"] = "does_not_exist.png"
    reg = SkillRegistry()
    sol = reg.require("figure_reasoning").solve(q, SkillContext(settings=make_settings(tmp_path)))
    assert sol.status == "failed"
    assert (sol.meta or {}).get("reason") == "region_incomplete"
    assert any("no_stem_crop" in str(p) for p in (sol.meta or {}).get("problems", []))
