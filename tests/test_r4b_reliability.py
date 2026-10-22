"""R4b 可靠性与完整页面截图闭环的锁定测试。

覆盖验收条目：
  P0-1 完整截图 bbox 有界 + 像素一致性（crop 可从整页按 bbox 再导出）+ 中性题干
  P0-2 扫描计数洞：1/9 确认必须 unverified + 无全局 FRAME 突变 + 顺序调换稳健
  P0-3 parameter_stable 与 independently_verified 分离透出
  P1-1 layout 标签不再参与求解决策路径
  P1-3 image_only 严格输入合同（无 VLM 时诚实 NOT EXECUTED / model_api_error）
"""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import assessment_agent.tools.figure_transform as ft
from assessment_agent.tools.figure_transform import analyze_transform, _to_mask
from assessment_agent.tools.figure_transform_tools import (
    SCAN_GRID, STABILITY_POLICY, verify_transformation)
from assessment_agent.tools.figure_adapter import solve_figure_transform_regions

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIGSHOTS = DATA / "benchmark_figshots"


def _rows(split: str):
    f = FIGSHOTS / split / "figshots.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ P0-1


def test_figshots_manifest_and_bbox_bounded():
    man = json.loads((FIGSHOTS / "manifest.json").read_text(encoding="utf-8"))
    assert "dev" in man["splits"] and "test" in man["splits"]
    for split, info in man["splits"].items():
        f = FIGSHOTS / info["file"]
        assert hashlib.sha256(f.read_bytes()).hexdigest() == info["sha256"], split
        assert info["count"] == len(_rows(split))
    # 与 figtrans 冻结集互不覆盖
    assert (DATA / "benchmark_figtrans/test/figtrans.jsonl").exists()


def test_figshots_full_page_contains_options_bbox_in_image_and_pixel_consistent():
    for row in _rows("dev") + _rows("test"):
        page = np.array(Image.open(DATA / row["image"]))
        W, H = row["regions"]["image_size"]
        assert page.shape == (H, W), row["id"]
        sx, sy, sw, sh = row["regions"]["stem_matrix_bbox"]
        assert 0 <= sx and 0 <= sy and sx + sw <= W and sy + sh <= H, row["id"]
        base = FIGSHOTS
        stem = np.array(Image.open(base / row["regions"]["stem_matrix_crop"]))
        assert np.array_equal(page[sy:sy + sh, sx:sx + sw], stem), \
            f"{row['id']} stem crop 不可从整页按 bbox 再导出"
        assert len(row["regions"]["option_regions"]) >= 2, row["id"]
        for info in row["regions"]["option_regions"]:
            x, y, w, h = info["bbox"]
            assert 0 <= x and 0 <= y and x + w <= W and y + h <= H, (row["id"], info["label"])
            crop = np.array(Image.open(base / info["crop_path"]))
            assert np.array_equal(page[y:y + h, x:x + w], crop), \
                f"{row['id']}/{info['label']} option crop 像素与整页不一致"


def test_figshots_page_contains_letters_and_instruction_pixels():
    """选项字母与说明文字必须以像素存在（OCR 可寻址），且题干文字不含规律名提示。"""
    banned = ("旋转", "镜像", "叠加", "翻转", "平移", "缩放", "顺时针", "逆时针")
    for row in _rows("dev") + _rows("test"):
        assert not any(k in row["stem"] for k in banned), row["id"]
        page = np.array(Image.open(DATA / row["image"]))
        # 说明文字区（stem bbox 之上）存在深色像素
        _sx, sy = row["regions"]["stem_matrix_bbox"][0], row["regions"]["stem_matrix_bbox"][1]
        header = page[0:sy]
        assert (header < 100).sum() > 200, f"{row['id']} 页面缺说明文字"
        for info in row["regions"]["option_regions"]:
            x, y, w, h = info["bbox"]
            label_zone = page[y:y + h, max(0, x - 24):max(0, x - 2)]
            assert (label_zone < 128).sum() > 20, f"{row['id']} 缺字母标号像素"


def test_frozen_figtrans_untouched():
    """原 figtrans 冻结集与首评记录字节不变（本轮只做增量，不覆盖）。"""
    man = json.loads((DATA / "benchmark_figtrans/manifest.json").read_text(encoding="utf-8"))
    for split, info in man["splits"].items():
        f = DATA / "benchmark_figtrans" / info["file"]
        assert hashlib.sha256(f.read_bytes()).hexdigest() == info["sha256"], split
    first = json.loads((DATA / "benchmark_figtrans/test_firstrun_metrics.json").read_text(encoding="utf-8"))
    assert first["summary"]["correct"] == 7 and first["summary"]["total"] == 10
    assert first["data_sha256_at_firstrun"] == man["splits"]["test"]["sha256"]


# ------------------------------------------------------------------ P0-2


def _lshape(k: int) -> np.ndarray:
    """非对称 L：旋转 90° 后落在不同画布角，避免居中归一化抹平旋转。"""
    m = np.zeros((48, 48), np.uint8)
    m[k * 9 + 10:k * 9 + 16, 10:22] = 1
    m[10:22, k * 9 + 10:k * 9 + 16] = 1
    return m


def _seq_case(k: int = 3):
    """构造确定性序列：R90 递推，选项含正确预测 + 干扰项（画布恒 48×48）。"""
    cells = [_lshape(0)]
    cur = cells[0]
    for _ in range(k):
        cur = np.rot90(cur, 1)
        padded = np.zeros((48, 48), np.uint8)
        padded[:cur.shape[0], :cur.shape[1]] = cur
        cells.append(padded)
    options = {"A": cells[-1], "B": np.fliplr(cells[-1]),
               "C": np.rot90(cells[-1], 1), "D": _lshape(3)}
    return cells[:-1], options


def test_partial_confirm_is_unverified_not_accept(monkeypatch):
    """P0-2 核心反例：若扫描只有 1 组确认、其余组弃权，绝不允许 accept。

    （R4a 原洞：answers={A,None,…} 去 None 后 =={A} 被当成全一致放行。）
    构造 frame=80 组不可判场景——2/3 确认必须 unverified，无法判定不是无关结果。
    """
    import assessment_agent.tools.figure_transform_tools as ftt
    stems, options = _seq_case()
    res = analyze_transform(stems, options, mode="sequence")
    answer = res["verdict"].get("answer")
    assert answer is not None, "前置：该构造题本应可唯一求解"
    real = analyze_transform

    def fake(stem_masks, option_masks, mode="sequence", tol=0.12, frame=None, **kw):
        if frame == 80:
            return {"verdict": {"verdict": "no_rule", "answer": None, "detail": "构造"},
                    "candidates": [], "symmetry_unresolvable": []}
        return real(stem_masks, option_masks, mode=mode, tol=tol, frame=frame, **kw)

    monkeypatch.setattr(ftt, "analyze_transform", fake)
    ver = verify_transformation({**res, "mode": "sequence"}, stems, options, answer)
    assert ver.action == "unverified", f"部分确认被 {ver.action} = P0-2 洞回归"
    checks = {c["name"]: c for c in ver.model_dump()["checks"]}
    detail = checks["figure_transform.scan_stats"]["detail"]
    assert "未判 1" in detail and "政策 all_confirm" in detail
    # 弃权答案同样永不 accept
    ver_abstain = verify_transformation({**res, "mode": "sequence"}, stems, options, None)
    assert ver_abstain.action != "accept", "弃权答案被 accept = P0-2 洞回归"


def test_scan_all_confirm_policy_enforced():
    stems, options = _seq_case()
    res = analyze_transform(stems, options, mode="sequence")
    answer = res["verdict"].get("answer")
    ver = verify_transformation({**res, "mode": "sequence"}, stems, options, answer)
    checks = {c["name"]: c for c in ver.model_dump()["checks"]}
    if ver.action == "accept":
        # accept ⇒ parameter_stable 检查通过 ⇒ 全组确认（all_confirm 政策）
        assert checks["figure_transform.parameter_stable"]["passed"] is True
        assert STABILITY_POLICY == "all_confirm"
        assert f"确认 {len(SCAN_GRID)}/{len(SCAN_GRID)}" in checks["figure_transform.scan_stats"]["detail"]


def test_verify_never_mutates_module_frame():
    stems, options = _seq_case()
    res = analyze_transform(stems, options, mode="sequence")
    before = ft.FRAME
    verify_transformation({**res, "mode": "sequence"}, stems, options, res["verdict"].get("answer"))
    assert ft.FRAME == before, "verify 期间/之后全局 FRAME 被突变"


def test_verify_concurrent_and_order_swap_stable():
    """并发 + 运行顺序调换：验证器无共享状态，结论必须逐位一致。"""
    stems, options = _seq_case()
    res = analyze_transform(stems, options, mode="sequence")
    answer = res["verdict"].get("answer")
    payload = {**res, "mode": "sequence"}

    def run():
        v = verify_transformation(payload, stems, options, answer)
        d = v.model_dump()
        return (d["action"], tuple(c["name"] + ":" + str(c["passed"]) for c in d["checks"]))

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda _: run(), range(12)))
    assert len(set(results)) == 1, "并发运行结果不一致（共享状态污染）"

    base = run()
    # 顺序调换：先跑另一题再回来，结果不变
    s2, o2 = _seq_case(2)
    r2 = analyze_transform(s2, o2, mode="sequence")
    verify_transformation({**r2, "mode": "sequence"}, s2, o2, r2["verdict"].get("answer"))
    assert run() == base


def test_interruption_leaves_no_state():
    stems, options = _seq_case()
    res = analyze_transform(stems, options, mode="sequence")
    before = ft.FRAME
    t = threading.Thread(target=lambda: verify_transformation(
        {**res, "mode": "sequence"}, stems, options, res["verdict"].get("answer")))
    t.start()
    t.join(timeout=0.001)  # 不等完成：即便中途交错，全局帧也必须恒定
    assert ft.FRAME == before


# ------------------------------------------------------------------ P0-3


def test_parameter_stable_and_independently_verified_are_separate_fields():
    d = FIGSHOTS / "regions/dev/ft-rot90-01"
    opts = [(p.stem.split("_")[1], str(p)) for p in sorted(d.glob("option_*.png"))]
    r = solve_figure_transform_regions(str(d / "stem.png"), opts, grid=(1, 4))
    assert "parameter_stable" in r and "independently_verified" in r
    assert isinstance(r["parameter_stable"], bool) and isinstance(r["independently_verified"], bool)
    # 二者独立：存在"参数稳定但代入验证未过"的真实样本（保守拒绝），绝不合并成单一布尔
    for qid, grid in (("ft-overlay-12", (3, 3)),):
        d2 = FIGSHOTS / "regions/dev" / qid
        o2 = [(p.stem.split("_")[1], str(p)) for p in sorted(d2.glob("option_*.png"))]
        r2 = solve_figure_transform_regions(str(d2 / "stem.png"), o2, grid=grid)
        assert r2["parameter_stable"] is True
        assert r2["independently_verified"] is False, qid
        assert r2["status"] == "unverified", f"{qid}: 代入未过却作答 = 布尔合并回归"
    # R14-D 能力前进（如实记录，非放松验证）：平移族金标 ft-trans-07 在
    # ①绝对面积跨协议尺度 ②验证器复用坐标契约 ③退化恒等序列弃权 三项修复后，
    # 由 translate(+0.12,0) 唯一判 A（=金标），两布尔同时为真且状态 solved。
    d3 = FIGSHOTS / "regions/dev" / "ft-trans-07"
    o3 = [(p.stem.split("_")[1], str(p)) for p in sorted(d3.glob("option_*.png"))]
    r3 = solve_figure_transform_regions(str(d3 / "stem.png"), o3, grid=(1, 4))
    assert r3["parameter_stable"] is True and r3["independently_verified"] is True
    assert r3["status"] == "solved" and r3["answer"] == "A"
    # 两布尔依然分离：overlay-12 为 (True, False)，trans-07 为 (True, True)——
    # 它们不可能由单一布尔同时表达这两行事实。


# ------------------------------------------------------------------ P1-1


def test_layout_label_does_not_change_solver_decision():
    """只改数据集 layout 字段，求解答案/候选/验证结论逐位不变。"""
    from assessment_agent.config import Settings
    from assessment_agent.pipeline import Pipeline
    from assessment_agent.evaluation.runner import load_cases

    cases = {c.id: c for c in load_cases(DATA, "figshots_dev")}
    case = cases["ft-rot90-01"]

    def run(layout_value):
        pipe = Pipeline(settings=Settings.from_env(env={"ASSESSMENT_OFFLINE": "1"}))
        meta = {}
        if case.grid is not None:
            meta["grid"] = case.grid
        if case.regions:
            meta["vision"] = case.regions
        meta["layout"] = layout_value  # 唯一变量：数据集身份标签
        run_ = pipe.run(text=case.stem, image_path=case.image, options=case.options,
                        skill="figure_reasoning", save=False, meta=meta)
        sm = run_.solution.meta or {}
        t = sm.get("transform") or {}
        return (run_.solution.status, run_.solution.answer, run_.verification.action,
                json.dumps(t.get("candidates"), sort_keys=True, default=str),
                str(t.get("verdict")), t.get("parameter_stable"), t.get("independently_verified"))

    a = run("figshots")
    b = run("figtrans")
    c = run("oracle_split_zz")
    d = run(None)
    assert a == b == c == d, "layout 标签参与了解算路径（P1-1 回归）"


# ------------------------------------------------------------------ P1-3


def test_image_only_input_contract_is_image_only():
    """image_only 的 runner 只允许传原始整页图片：不得有文本/选项/区域/grid。"""
    import inspect
    import assessment_agent.evaluation.runner as runner_mod
    src = inspect.getsource(runner_mod)
    i = src.find('if mode == "image_only"')
    assert i >= 0, "runner 无 image_only 分支"
    # 窗口 3200：覆盖缺图短路 + R4d 审计前快照到 pipeline.run 调用点
    block = src[i:i + 3200]
    j = block.find(".run(")
    assert j >= 0, "image_only 分支未调用 pipeline.run"
    call = block[j:block.find(")", j)]
    for forbidden in ("stem=", "options=", "skill=", "grid=", "meta=", "regions"):
        assert forbidden not in call, f"image_only 求解输入泄漏：{forbidden}"


def test_phase6_figshots_smoke_honest():
    f = FIGSHOTS / "phase6_vlm_smoke.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    # R4c 起未配置语义统一为五状态之一（NOT_CONFIGURED），与 NOT_EXECUTED 同族；
    # 无论哪个标签，绝不允许出现 EXECUTED/模拟结果
    assert d["status"] in ("NOT_EXECUTED", "NOT_CONFIGURED"), \
        "离线环境不得出现 EXECUTED/模拟结果"
    assert "LLM" in d["reason"] or "未配置" in d["reason"]
    assert d["split"] == "figshots_dev"
    assert "figtrans_dev" not in json.dumps(d, ensure_ascii=False)  # R4c 修旧 split 残留
    assert d["strict_input_contract"]
    assert len(d["artifacts_per_case"]) >= 8
    assert d["eval_mode"] == "image_only"
