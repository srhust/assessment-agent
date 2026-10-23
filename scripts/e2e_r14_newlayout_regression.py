"""R14-E 新排版独立 Dev 回归（零 API）。

目的：Stage E 的选项带根修**不能**只用 F 的分数来证明，必须独立证明
证据/排版层的四件事，并把"题面声明 / 实际消费 / 页面真值"三个口径
分列（互不冒充）：
  1. 选项证据非空白：被消费的每份 option_*.png 墨量 >= 20
     （overlay-12 optC「近空白」缺陷的直接反证，near_blank_consumed 必须为空）；
  2. 标签通道自洽：label_association_verified=True 只允许出现在
     provenance == localizer+label_anchor 的单元上；无字母页
     （has_letter_labels is False）不得被释放，也不得自报锚点
     （letterless_released / letterless_anchor_claims 必须为空）；
  3. 释放层零错放：released => (final_answer == gold) 且单元全为锚点
     且 binding_state in {verified, rebuilt}；
  4. 消费单元必须与**页面独立重推**同源（同 n 解、逐格 bbox 与 ink 相等），
     不采信 Solver 自报字段（cells_match_page_reeval）。

覆盖面（50 页）：
  real    = 5 张 R13-G 真实截图件（含 1 张 --tight 裁边排版），题面用其
            runs/r13g-*/question.json 原样（OCR 声明选项 + 真实裁剪），
            不注入任何金标几何；
  dev     = 15 张 dev 基准页（金标只用于**答案打分**与题面选项框）；
  perturb = 30 张排版扰动页（6 张 --tight 裁边 + --vertical/--distractor/
            --small/--large 等变体，题面沿用母版声明=生产口径）。

诚实口径（写进产物，不做掩饰）：
  * 页面末带的**列段数**不是选项数的可靠代理：同一行里"字母段+图形段"会
    各算一列段，字母被裁掉的紧排版页又会少一段——实测
    ft-mirror-04--tight（dev 版）末带 3 段、真实截图同名页 3 段、
    ft-overlay-13--tight 7 段、ft-grid-10--tight 5 段，都不等于声明数。
    因此本脚本对 tight 族只记录三元组 declared / consumed /
    bottom_band_col_runs，不据此立断言。
  * 真实截图件的选项数由 OCR 声明（tight 页 3），dev 母版同名件由基准声明
    （4）——不同题面、不同答案口径，两者 id 相同会互相覆盖，故真实件行键加
    [real-screenshot] 后缀消歧，绝不合并计数。
  * page_band20_audit 逐页记录 mask 尺寸/墨量/内容带宽高与上下留白，
    band20_flagged 列出内容带高或宽 <20 的页，供人工复核而非自动否决。

运行：python -X utf8 scripts/e2e_r14_newlayout_regression.py [--out 文件名]
产物：evidence/R14E_NEWLAYOUT_REGRESSION_V2.json
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from assessment_agent.schemas import Question                          # noqa: E402
from assessment_agent.skills import SkillContext, SkillRegistry        # noqa: E402
from assessment_agent.tools.figure_page_localizer import localize_page  # noqa: E402
from assessment_agent.tools.figure_tools import load_mask             # noqa: E402
from assessment_agent.artifacts import RunArtifacts                   # noqa: E402
from assessment_agent.tools.release_scoring import release_status     # noqa: E402
from assessment_agent.verifier import Verifier                        # noqa: E402
from tests.conftest import make_settings                              # noqa: E402

DEV = ROOT / "data" / "benchmark_figshots" / "dev" / "figshots.jsonl"
PERTURB = ROOT / "data" / "benchmark_figshots_perturb"
REAL = ROOT / "runs"

# 真实截图件（R13-G 采集，含 tight 裁边排版）的金标：与 runs/r13g-* 登记口径
# 一致（tight 页是母版裁边变体，答案不变）。**仅用于事后打分**，不参与任何
# 切分/释放判据——与 d1/f1 用 dev 金标给扰动页打分的做法同构。
REAL_GOLD = {"ft-mirror-04": "A", "ft-rot90-01": "A", "ft-trans-07": "A",
             "ft-overlay-12": "A", "ft-mirror-04--tight": "A"}


def build_pages():
    items = [json.loads(l) for l in DEV.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    imap = {i["id"]: i for i in items}
    gold = {i["id"]: i["answer"] for i in items}
    pages = []
    for d in sorted(REAL.glob("r13g-ft-*")):
        qj, img = d / "question.json", d / "image" / "original.png"
        if not qj.is_file() or not img.is_file():
            continue
        pid = d.name.replace("r13g-", "")
        if pid not in REAL_GOLD:
            continue
        # 真实件题面里的 image_path / crop_path 是采集机上的**绝对路径**，
        # 必须重写到当前工作区，否则 load_mask 读不到图、Solver 直接失败
        # （首版回归漏掉这 5 页正是这个原因，实测 real=0）。
        qq = json.loads(qj.read_text(encoding="utf-8"))
        qq["image_path"] = str(img)
        pages.append({"pid": pid, "kind": "real", "img": str(img),
                      "gold": REAL_GOLD[pid], "question_json": qq})
    for i in items:
        pages.append({"pid": i["id"], "kind": "dev", "gold": i["answer"],
                      "img": str(ROOT / "data" / "benchmark_figshots"
                                 / "images" / "dev" / f"{i['id']}.png"),
                      "stem": i.get("stem") or "", "options": i["options"],
                      "item": i})
    for src in sorted((PERTURB / "images").glob("*.png")):
        pid, base = src.stem, src.stem.split("--")[0]
        if base not in gold:
            continue
        pages.append({"pid": pid, "kind": "perturb", "img": str(src),
                      "gold": gold[base], "item": imap[base],
                      "options": imap[base]["options"],
                      "stem": imap[base].get("stem") or ""})
    return pages


def make_question(pg):
    if pg["kind"] == "real":
        qq = json.loads(json.dumps(pg["question_json"]))
        vis = (qq.get("meta") or {}).get("vision") or {}

        def _retarget(p):
            if not isinstance(p, str) or Path(p).is_file():
                return p
            tail = p.replace("\\", "/").split("/runs/")[-1]
            cand = ROOT / "runs" / tail
            return str(cand) if cand.is_file() else None

        if "stem_matrix_crop" in vis:
            vis["stem_matrix_crop"] = _retarget(vis["stem_matrix_crop"])
        for o in (vis.get("option_regions") or []):
            if "crop_path" in o:
                o["crop_path"] = _retarget(o["crop_path"])
        for dr in (vis.get("diagram_regions") or []):
            if "crop_path" in dr:
                dr["crop_path"] = _retarget(dr["crop_path"])
        q = Question.model_validate(qq)
        return q.model_copy(update={"image_path": pg["img"]})
    item = pg["item"]
    vision = {"bbox_format": "pixel_xywh", "image_size": None,
              "option_regions": [{"label": o["label"], "bbox": o["bbox"]}
                                 for o in item["regions"].get("option_regions", [])]}
    return Question.model_validate({
        "id": pg["pid"], "stem": pg["stem"] or pg["pid"],
        "image_path": pg["img"], "source": "image",
        "options": [{"label": o["label"], "text": "图"} for o in pg["options"]],
        "meta": {"vision": vision}})


def page_truth(img):
    """独立**页面真值**测量（不读 Solver 元数据、不读裁剪文件）：直接对页面
    末带做列分解，量出页面自身排布能给出的列段数/墨量/行高。只作对照入账，
    不作为断言（理由见模块 docstring 的诚实口径）。"""
    import numpy as np
    from assessment_agent.tools.figure_page_localizer import row_bands
    m = np.asarray(load_mask(img), dtype=bool)
    bands = list(row_bands(m))
    if not bands:
        return {"n_bands": 0, "bottom_band": None, "units": 0,
                "unit_ink": [], "unit_row_h": [], "pitch": [],
                "pitch_uniform": False, "ink_uniform": False,
                "row_h_uniform": False}
    y0, y1 = bands[-1]
    xs = np.nonzero(m[y0:y1, :].any(axis=0))[0]
    runs = []
    if len(xs):
        start = prev = int(xs[0])
        for x in xs[1:]:
            if x - prev > 2:
                runs.append((start, prev + 1))
                start = int(x)
            prev = int(x)
        runs.append((start, prev + 1))
    inks = [int(m[y0:y1, a:b].sum()) for a, b in runs]
    tops = []
    for a, b in runs:
        ys = np.nonzero(m[y0:y1, a:b].any(axis=1))[0]
        tops.append(int(ys.max() - ys.min() + 1) if len(ys) else 0)
    starts = [a for a, _ in runs]
    pitch = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    med_p = float(np.median(pitch)) if pitch else 0.0
    med_i = float(np.median(inks)) if inks else 0.0
    return {"n_bands": len(bands), "bottom_band": [int(y0), int(y1)],
            "units": len(runs), "unit_x": [list(r) for r in runs],
            "unit_ink": inks, "unit_row_h": tops, "pitch": pitch,
            "pitch_uniform": bool(pitch) and (max(pitch) - min(pitch)) <= max(
                4.0, 0.15 * med_p),
            "ink_uniform": bool(inks) and med_i >= 20 and all(
                abs(i - med_i) <= 0.25 * med_i for i in inks),
            "row_h_uniform": bool(tops) and (max(tops) - min(tops)) <= 4}


def main() -> int:
    pages = build_pages()
    reg = SkillRegistry()
    fr = reg.require("figure_reasoning")
    rows, bad = {}, []
    counts = {"solved": 0, "accepted": 0, "released": 0}
    band20 = []
    import numpy as np
    for pg in pages:
        m = np.asarray(load_mask(pg["img"]), dtype=bool)
        rws = np.nonzero(m.any(axis=1))[0]
        cls = np.nonzero(m.any(axis=0))[0]
        band20.append({"pid": pg["pid"], "kind": pg["kind"],
                       "mask_hw": [int(m.shape[0]), int(m.shape[1])],
                       "ink": int(m.sum()),
                       "band_h": int(rws.max() - rws.min() + 1) if len(rws) else 0,
                       "band_w": int(cls.max() - cls.min() + 1) if len(cls) else 0,
                       "top_margin": int(rws[0]) if len(rws) else 0,
                       "bottom_margin": int(m.shape[0] - 1 - int(rws[-1]))
                       if len(rws) else 0})
        q = make_question(pg)
        st = make_settings(Path(tempfile.mkdtemp()))
        st.r4g_layout = "pixel_only"
        ctx = SkillContext(settings=st, artifacts=RunArtifacts(
            st.runs_dir, run_id=f"nl-{pg['pid']}"))
        sol = fr.solve(q, ctx)
        prov = (sol.meta or {}).get("region_provenance") or {}
        cells = prov.get("option_cells") or []
        consumed = []
        for d in (ctx.artifacts.dir / "regions_pixel",
                  ctx.artifacts.dir / "regions"):
            if d.is_dir():
                for p in sorted(d.glob("option_*.png")):
                    consumed.append({"file": p.name,
                                     "ink": int(load_mask(str(p)).sum())})
        act = fa = None
        if sol.status == "solved":
            counts["solved"] += 1
            vr = Verifier(st, registry=reg).verify(q, sol)
            act, fa = vr.action, vr.final_answer
        rs = release_status(act, fa, pg["gold"])
        released = bool(rs.get("released"))
        if act in ("accept", "repair"):
            counts["accepted"] += 1
        if released:
            counts["released"] += 1
        truth = page_truth(pg["img"])
        # 页面级独立复核（不采信 Solver 自报字段）：消费单元集合必须与本轮
        # **独立重推**同 n 次 localize 的解逐格同源（bbox 与 ink 全等）。
        alt = localize_page(m, n_options=len(cells) or 2)
        ac = alt.get("option_regions") or []
        alt_anchored = bool(ac) and all(
            c.get("label_bbox") and c.get("provenance") == "localizer+label_anchor"
            for c in ac)
        same_source = (not cells or (
            len(ac) == len(cells)
            and [tuple(c["bbox_page"]) for c in ac]
            == [tuple(x["bbox_page"]) for x in cells]
            and [int(c.get("ink") or 0) for c in ac]
            == [int(x.get("ink") or 0) for x in cells]))
        rid = pg["pid"]
        if rid in rows:                       # 真实截图件与 dev 母版同 id 消歧
            rid = f"{rid}[real-screenshot]"
        anchored_all = bool(cells) and all(
            c.get("provenance") == "localizer+label_anchor" and c.get("label_bbox")
            for c in cells)
        letterless = bool(cells) and all(
            c.get("has_letter_labels") is False for c in cells)
        rec = {"kind": pg["kind"], "gold": pg["gold"],
               "solver_status": sol.status, "solver_answer": sol.answer,
               "verify_action": act, "final_answer": fa, "released": released,
               "release_status": rs.get("status"),
               "declared_options": len(q.options or []),
               "n_cells": len(cells),
               "anchored": sum(1 for c in cells if c.get("label_bbox")),
               "label_assoc": [c.get("label_association_verified") for c in cells],
               "has_letter_labels": [c.get("has_letter_labels") for c in cells],
               "binding_state": prov.get("consumption_binding"),
               "cut_provenance": prov.get("option_cut_provenance"),
               "cell_inks": [c.get("ink") for c in cells],
               "consumed": consumed,
               "page_reeval_n": len(ac), "page_reeval_anchored": alt_anchored,
               "cells_match_page_reeval": same_source,
               "page_truth": truth,
               "solver_error": (sol.error or "")[:170]}
        rows[rid] = rec
        # ---- 排版层断言（阈值一律不放宽）
        for c in consumed:
            if c["ink"] < 20:
                bad.append(f"{rid}: 消费裁剪近空白 {c['file']} ink={c['ink']}")
        for c, la in zip(cells, rec["label_assoc"]):
            if la is True and c.get("provenance") != "localizer+label_anchor":
                bad.append(f"{rid}: label_assoc=True 但单元非锚点 idx{c.get('index')}")
        if not same_source:
            bad.append(f"{rid}: 消费单元与页面独立重推不同源 "
                       f"{len(ac)}/{len(cells)}")
        if rec["anchored"] and not alt_anchored:
            bad.append(f"{rid}: 自报锚点但页面重推非锚点")
        if released:
            if fa != pg["gold"]:
                bad.append(f"{rid}: 错误释放 {fa} != 金标 {pg['gold']}")
            if not anchored_all:
                bad.append(f"{rid}: 单元未全锚点却被释放")
            if rec["binding_state"] not in ("verified", "rebuilt"):
                bad.append(f"{rid}: 已释放但 binding={rec['binding_state']}")
            if letterless:
                bad.append(f"{rid}: 无字母页被释放（标签通道不可验证）")
    tight = {k: {"declared_options": v["declared_options"],
                 # **两个量必须分开命名**（V2 把 provenance 单元数标成
                 # "consumed" 造成误读，见 V3 勘误）：
                 #   provenance_option_cells = Solver 证据链里的选项单元数
                 #   saved_option_crops      = 实际落盘的 option_*.png 份数
                 # 二者可以不同：题干布局歧义先弃权时证据链为空，但裁剪已写盘。
                 "provenance_option_cells": v["n_cells"],
                 "saved_option_crops": len(v["consumed"]),
                 "consumed_inks": [c["ink"] for c in v["consumed"]],
                 "n_cells": v["n_cells"],
                 "anchored": v["anchored"],
                 "has_letter_labels": v["has_letter_labels"],
                 "page_truth_units": v["page_truth"]["units"],
                 "page_truth_uniform": (v["page_truth"]["pitch_uniform"]
                                         and v["page_truth"]["ink_uniform"]
                                         and v["page_truth"]["row_h_uniform"]),
                 "page_truth_ink": v["page_truth"]["unit_ink"],
                 "released": v["released"], "binding": v["binding_state"],
                 "status": v["solver_status"]}
             for k, v in rows.items() if "--tight" in k}
    out = {"n_pages": len(pages),
           "by_kind": {k: sum(1 for v in rows.values() if v["kind"] == k)
                       for k in ("real", "dev", "perturb")},
           "layers": {"candidates_solved": counts["solved"],
                      "accepted": counts["accepted"],
                      "released": counts["released"]},
           "released_ids": sorted(k for k, v in rows.items() if v["released"]),
           "wrong_releases": sorted(k for k, v in rows.items()
                                    if v["released"] and v["final_answer"] != v["gold"]),
           "near_blank_consumed": sorted({k for k, v in rows.items()
                                          for c in v["consumed"] if c["ink"] < 20}),
           "letterless_released": sorted({k for k, v in rows.items()
                                          if v["released"] and v["has_letter_labels"]
                                          and all(x is False for x in v["has_letter_labels"])}),
           "letterless_anchor_claims": sorted({k for k, v in rows.items()
                                               if v["anchored"] and any(
                                                   x is False for x in
                                                   (v["has_letter_labels"] or []))}),
           "tight_layout_pages": tight,
           # 三元组分列：声明 / **证据链单元** / 页面末带列段（列段数只是对照，
           # 见 docstring）。V2 在这里把"证据链单元数"标成"消费单元"，与**落盘
           # 裁剪份数**混同：grid-10--tight / overlay-13--tight 各有 4 份选项
           # 裁剪已落盘，但题干布局歧义先弃权使证据链为空。V3 起两个量分列。
           "tight_declared_vs_evidence_cells": sorted(
               {f"{k}: 题面声明 {v['declared_options']} vs 证据链单元 "
                f"{v['provenance_option_cells']}（落盘裁剪 {v['saved_option_crops']} 份，"
                f"页面末带列段 {v['page_truth_units']}，仅对照）"
                for k, v in tight.items()
                if v["declared_options"] != v["provenance_option_cells"]}),
           "tight_page_truth_by_page": {
               k: {"declared": v["declared_options"],
                   "evidence_cells": v["provenance_option_cells"],
                   "saved_crops": v["saved_option_crops"],
                   "bottom_band_col_runs": v["page_truth_units"],
                   "uniform_layout": v["page_truth_uniform"],
                   "run_inks": v["page_truth_ink"]}
               for k, v in tight.items()},
           "page_band20_audit": band20,
           "erratum_v3": "V2 的 tight 汇总把 Solver 证据链单元数标为\"消费单元\"，"
                         "与落盘裁剪份数混同，导致\"4 页选项 0 份\"的表述**过强**：" "实测 scale-09--tight / trans-07--tight 确为 0 份裁剪；grid-10--tight / "
                         "overlay-13--tight 各已落盘 4 份选项裁剪（ink 168/296/168/168、"
                         "107/227/349/160），只是随后因题干布局假设不唯一而弃权、"
                         "证据链未记录单元，且其中 296/349 两格明显**并含多图**"
                         "（切分仍不合格）。三层计数、wrong_releases、checks_failed "
                         "均不受本次改名影响（同一批数字，仅口径命名更正）。",
           "band20_flagged": sorted(r["pid"] for r in band20
                                    if r["band_h"] < 20 or r["band_w"] < 20),
           "checks_failed": bad, "llm_calls_used": 0,
           "note": "三层分列：candidates_solved=Solver 产出候选、accepted=Verifier "
                   "动作、released=release_status 释放，互不代称；题面走生产入口"
                   "（真实件用其 question.json 原样、绝对路径重写到本工作区），"
                   "金标只用于事后打分。真实截图件与 dev 母版同 id 时行键加 "
                   "[real-screenshot] 后缀，绝不合并计数。",
           "rows": rows}
    name = "R14E_NEWLAYOUT_REGRESSION_V2.json"
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            name = sys.argv[i + 1]
    dst = ROOT / "evidence" / name
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n_pages", "by_kind", "layers",
                                          "released_ids", "wrong_releases",
                                          "near_blank_consumed",
                                          "letterless_released",
                                          "tight_declared_vs_evidence_cells",
                                          "tight_page_truth_by_page",
                                          "letterless_anchor_claims",
                                          "band20_flagged", "checks_failed",
                                          "llm_calls_used")},
                     ensure_ascii=False))
    print("written", dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
