# -*- coding: utf-8 -*-
"""R9-B：10 道图形损失题的四条件配对诊断（A/B/C/D）。

  A = 标准题干 + 金标区域（oracle 上限）
  B = VLM 题干 + 金标区域          （隔离题干 OCR 损失）
  C = 标准题干 + VLM 检测区域      （隔离结构/裁剪损失）
  D = VLM 题干 + VLM 区域          （真实端到端 = R8 image_only 复现）

A/B/C 为**离线诊断路径**，不作部署模式；全部复用 R8-E 缓存 extraction.json，零新 VLM 调用。
禁止硬编码页面尺寸/坐标/case_id：几何信息一律取自题集与缓存工件本身。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assessment_agent.config import Settings
from assessment_agent.pipeline import Pipeline
from assessment_agent.schemas import DiagramRegion, ExtractionResult, OptionRegion, QuestionOption, TextRegion

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIGROOT = DATA / "benchmark_figshots"  # 金标 crop 相对目录（同 runner _resolve 规则）
ITEMS = [json.loads(l) for l in
         (DATA / "benchmark_figshots/dev/figshots.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def gold_extraction(it) -> ExtractionResult:
    """金标区域 → v2 结构（crop 路径按 data/ 解析；bbox 来自题集自身，不新造常量）。"""
    reg = it["regions"]
    size = reg.get("image_size") or [None, None]
    ers = []
    for o in reg.get("option_regions") or []:
        crop = o.get("crop_path")
        ers.append(OptionRegion(
            label=o["label"], bbox=o["bbox"], text=o.get("text", ""),
            has_figure=bool(o.get("has_figure")),
            figure_bbox=o.get("figure_bbox"),
            crop_path=str(FIGROOT / crop) if crop and not Path(str(crop)).is_absolute() else crop))
    drs = []
    if reg.get("stem_matrix_bbox"):
        grid = reg.get("stem_matrix_grid")
        grid_d = ({"rows": int(grid[0]), "cols": int(grid[1])}
                  if isinstance(grid, (list, tuple)) and len(grid) == 2 else grid)
        d = DiagramRegion(region_id="gold-stem", role="stem_figure",
                          bbox=reg["stem_matrix_bbox"], grid=grid_d)
        sc = reg.get("stem_matrix_crop")
        if sc:
            p = Path(sc) if Path(str(sc)).is_absolute() else FIGROOT / str(sc)
            d.crop_path = str(p)
        drs.append(d)
    return ExtractionResult(
        stem=it["stem"], options=[QuestionOption(label=o["label"], text=o.get("text", ""))
                                  for o in it["options"]],
        question_type="single_choice", skill_hint="figure_reasoning", has_diagram=True,
        schema_version=2, image_width=size[0], image_height=size[1],
        text_regions=[TextRegion(region_id="t0", bbox=reg["stem_matrix_bbox"] or [0, 0, 1, 1],
                                 text=it["stem"])],
        diagram_regions=drs, option_regions=ers, parser="vision_parser", confidence=1.0)


def vlm_extraction(it):
    p = ROOT / "runs" / f"e2e-r8_e_img-{it['id']}" / "extraction.json"
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    data.setdefault("parser", "vision_parser")
    return ExtractionResult.model_validate(data)


def merge(stem_src: ExtractionResult, reg_src: ExtractionResult) -> ExtractionResult:
    out = reg_src.model_copy(deep=True)
    out.stem = stem_src.stem
    out.options = stem_src.options          # 选项字母/文本以题干侧为准
    return out


def run_condition(pipe: Pipeline, it, ex: ExtractionResult):
    """注入式提取：pipeline.run 无 extraction 参数——替换 _extract 返回预置结构。"""
    img = str(ROOT / "data" / it["image"])
    return pipe.run(text=None, image_path=img, skill="figure_reasoning",
                    save=False, hint="figure_reasoning")


def main():
    results = {c: [] for c in "ABCD"}
    pipe_holder = {}
    for it in ITEMS:
        g = gold_extraction(it)
        v = vlm_extraction(it)
        combos = {
            "A": g,
            "B": merge(v, g) if v else None,
            "C": merge(g, v) if v else None,
            "D": v,
        }
        for cond, ex in combos.items():
            pipe = pipe_holder.setdefault(cond, Pipeline(Settings.from_env()))
            if ex is None:
                results[cond].append({"id": it["id"], "expected": it["answer"],
                                      "expected_status": it.get("expected_status"),
                                      "answer": None, "status": "extraction_unavailable"})
                continue
            pipe._extract = (lambda e: (lambda self, image_path, hint, artifacts=None: e))(ex).__get__(pipe)
            img = str(ROOT / "data" / it["image"])
            try:
                r = pipe.run(text=None, image_path=img, skill="figure_reasoning",
                             save=False, hint="figure_reasoning")
                results[cond].append({
                    "id": it["id"], "expected": it["answer"],
                    "expected_status": it.get("expected_status"),
                    "answer": r.solution.answer,
                    "status": r.solution.status,
                    "reason": str(r.solution.error or "")[:160],
                })
            except Exception as exc:  # noqa: BLE001
                results[cond].append({"id": it["id"], "expected": it["answer"],
                                      "expected_status": it.get("expected_status"),
                                      "answer": None, "status": f"error: {exc}"[:120]})

    def score(cond):
        rows = results[cond]
        solv = [x for x in rows if x.get("expected_status") == "solved"]
        ok = sum(1 for x in solv if x.get("answer") == x.get("expected"))
        ab = [x for x in rows if x.get("expected_status") != "solved"]
        ab_ok = sum(1 for x in ab if x.get("answer") is None or x.get("status") != "solved")
        return {"answerable_correct": f"{ok}/{len(solv)}", "abstain_ok": f"{ab_ok}/{len(ab)}"}

    summary = {c: score(c) for c in "ABCD"}
    flips = {"A_vs_C": [], "A_vs_B": [], "B_vs_D": [], "C_vs_D": []}
    gi = {r["id"]: r for r in results["A"]}
    for cond, key in (("C", "A_vs_C"), ("B", "A_vs_B"), ("D", "C_vs_D")):
        for r in results[cond]:
            a = gi.get(r["id"], {})
            if r.get("expected_status") == "solved":
                af, rf = a.get("answer") == a.get("expected"), r.get("answer") == r.get("expected")
                if af and not rf:
                    flips[key].append((r["id"], "lost"))
                elif rf and not af:
                    flips[key].append((r["id"], "gained"))
    out = {"conditions": summary, "per_case": results, "flips": flips}
    (ROOT / "evidence/R9B_FOUR_CONDITION.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    for k, v2list in flips.items():
        if v2list:
            print(k, v2list)


if __name__ == "__main__":
    main()
