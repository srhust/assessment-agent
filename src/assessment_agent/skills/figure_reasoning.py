"""图形推理 Skill（第一阶段）：截图 → 网格解析 → 规律推断 → 选项匹配。

能力边界（真实实现，可在 ``knowledge/figure_reasoning.md`` 查看）：
- 序列题：图形个数（等差数列）、单图形边数、墨迹面积、质心平移、镜像对称性；
- 九宫格：每行第三格 = 前两格的 异或 / 并集 / 交集；
- 布局：自动推断规则网格；排列不规则时可用显式 ``grid=(行, 列)``；
- 任何一条规律必须能解释**全部已知格**，且只与**一个**选项匹配，否则明确失败。

未实现（明确返回失败而不是猜测）：旋转/翻转匹配、曲线与立体图形、位置轮换类的
非叠加九宫格规律、多规律冲突时的仲裁。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..schemas import Question, Solution, ToolInvocation, Verification, VerificationCheck
from .base import BaseSkill, SkillContext

TOOL_ID = "figure_rules"
ADAPTER_ID = "figure_rules_adapter"

# R4e P1-2：变换通道进入 solution.meta 的**审计字段白名单**。除历史判定字段外，
# 必须带上结构化阻塞原因（blockers/overlay_eval）与坐标契约（coord_contract），
# 归因层才能把"上游文本偏差"与"真正阻断作答的几何失败"分开，不再由
# extraction_text_error 一类早退标签遮蔽下游几何错误。
TRANSFORM_META_KEYS = ("status", "answer", "reason", "verdict", "candidates",
                       "parameter_stable", "independently_verified",
                       "symmetry_unresolvable", "stem_fingerprint", "verification",
                       "blockers", "overlay_eval", "coord_contract")


def _xywh_to_xyxy(b):
    x, y, w, h = [int(v) for v in b]
    return [x, y, x + w, y + h]


class FigureReasoningSkill(BaseSkill):
    id = "figure_reasoning"
    name_zh = "图形推理"
    description = "图形序列/九宫格：个数、边数、面积、平移、对称性、叠加规律"
    stage = 1
    supported = True
    requires = ("opencv-python-headless", "numpy")
    question_types = ("single_choice",)

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _grid(question: Question) -> Optional[Tuple[int, int]]:
        raw = (question.meta or {}).get("grid")
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                rows, cols = int(raw[0]), int(raw[1])
            except (TypeError, ValueError):
                return None
            if rows > 0 and cols > 0:
                return (rows, cols)
        return None

    def unavailable_reason(self) -> Optional[str]:
        try:
            import cv2  # noqa: F401
        except ImportError:
            return "图形推理需要 OpenCV：pip install opencv-python-headless"
        return None

    # ------------------------------------------------------ 区域模式（R2a）
    @staticmethod
    def _basic_region_inputs(question: Question) -> Optional[Dict[str, Any]]:
        """从 VisionParser 的区域 meta 中提取题干矩阵裁剪与选项裁剪。"""
        vision = (question.meta or {}).get("vision") or {}
        stem_crop = vision.get("stem_matrix_crop")
        option_regions = vision.get("option_regions") or []
        options = [
            (str(region.get("label")), str(region.get("crop_path")))
            for region in option_regions
            if region.get("has_figure") and region.get("crop_path")
        ]
        if stem_crop and len(options) >= 2:
            return {
                "stem_crop": stem_crop,
                "options": options,
                "grid": tuple(vision["stem_matrix_grid"]) if vision.get("stem_matrix_grid") else None,
            }
        return None

    def _region_inputs(self, question: Question,
                       ctx: Optional[SkillContext] = None) -> Optional[Dict[str, Any]]:
        """R4g：按 ``settings.r4g_layout`` 档位在原始区域输入上叠加布局重建。

        重建 verified 时用等分画布替换题干输入（Solver 与 Verifier 不改动、
        阈值不放宽）；未 verified 时保持现有输入，resolution 证据仍随行记录。
        """
        inputs = self._basic_region_inputs(question)
        profile = str(getattr(ctx.settings, "r4g_layout", "off") if ctx else "off").lower()
        if profile not in ("merge", "merge_pixel", "pixel_only"):
            return inputs
        vision = (question.meta or {}).get("vision") or {}
        stem_regions = [d for d in (vision.get("diagram_regions") or [])
                        if str(d.get("role")) in ("stem_figure", "stem_matrix")]
        if not stem_regions or not question.image_path:
            if inputs:
                inputs["layout_resolution"] = {
                    "layout_source": "none", "mode": "unverified", "verified": False,
                    "ambiguity_reason": "no_stem_regions_or_image", "profile": profile}
            return inputs
        try:
            import cv2  # noqa: F401
            import numpy as np
            from ..tools.diagram_layout import assemble_stem_layout
            from ..tools.figure_tools import load_mask

            mask = load_mask(str(question.image_path))
            # R9-B：补全线索取自题干文字自身（部署链路由解析通道判定，非坐标/案例硬编码）
            stem_text = str(question.stem or "") + str(vision.get("stem") or "")
            cue_markers = ("补全", "填入", "填上", "？", "?", "符合规律的一项",
                           "下一", "未知", "问号")
            expects_blank = any(m in stem_text for m in cue_markers)
            res = assemble_stem_layout(
                mask, stem_regions, vision.get("option_regions") or [],
                profile=profile, trust_vlm_grid=(profile != "pixel_only"),
                expects_blank_target=expects_blank)
            canvas = res.pop("_canvas", None)
            res["profile"] = profile
            res["algorithm"] = "r4g-diagram-layout-v1"
            res["image"] = str(question.image_path)
            # R9-B 替换判据：重建**严格更完整**（列数多于原声明）或原无可用
            # 裁剪时才替换，避免扰动金标正确几何（A 的 1×3 原样保留），
            # 同时恢复 VLM 欠分割（C 的 1×1/1×2 → 1×3）。
            orig = (inputs or {}).get("grid")
            orig_cols = int(orig[1]) if orig else 0
            new_cols = int(res.get("cols") or 0)
            do_replace = (res.get("verified") and canvas is not None
                          and new_cols > orig_cols)
            if do_replace:
                if ctx is not None and ctx.artifacts is not None:
                    target = ctx.artifacts.dir / "regions" / "stem_reconstructed.png"
                else:
                    import tempfile
                    target = Path(tempfile.gettempdir()) / "assessment_r4g" / "stem_reconstructed.png"
                image = np.where(canvas, 0, 255).astype(np.uint8)
                target.parent.mkdir(parents=True, exist_ok=True)
                if cv2.imwrite(str(target), image):
                    option_regions = vision.get("option_regions") or []
                    options = [
                        (str(r.get("label")), str(r.get("crop_path")))
                        for r in option_regions
                        if r.get("has_figure") and r.get("crop_path")]
                    if inputs is None and len(options) >= 2:
                        inputs = {"options": options}
                    if inputs is not None:
                        inputs["stem_crop"] = str(target)
                        inputs["grid"] = (int(res["rows"] or 1), int(res["cols"]))
            if inputs is not None:
                inputs["layout_resolution"] = res
            return inputs
        except Exception as exc:  # noqa: BLE001 - 重建失败保持原输入，记录原因
            if inputs is not None:
                inputs["layout_resolution"] = {
                    "layout_source": "none", "mode": "unverified", "verified": False,
                    "ambiguity_reason": f"assembler_error:{type(exc).__name__}: {exc}",
                    "profile": profile}
            return inputs

    def _region_incomplete(self, question: Question,
                           extra: Optional[List[str]] = None) -> Solution:
        """v2 区域证据不完整：说明缺什么，返回结构化失败（不猜测、不回退整图）。"""
        vision = (question.meta or {}).get("vision") or {}
        problems: List[str] = []
        stem_crop = vision.get("stem_matrix_crop")
        if not stem_crop:
            problems.append("缺少题干矩阵裁剪")
        elif not Path(stem_crop).is_file():
            problems.append(f"题干矩阵裁剪文件不存在: {stem_crop}")
        figure_options = [
            region for region in (vision.get("option_regions") or []) if region.get("has_figure")
        ]
        with_crop = [region for region in figure_options if region.get("crop_path")]
        lost = [region for region in with_crop if not Path(str(region.get("crop_path"))).is_file()]
        if len(figure_options) < 2:
            problems.append(f"带图形的选项仅 {len(figure_options)} 个（至少需要 2 个）")
        if len(with_crop) < 2:
            problems.append("选项图形裁剪不足 2 份")
        if lost:
            problems.append(f"选项裁剪文件丢失: {[region.get('label') for region in lost]}")
        if extra:
            problems.append("像素定位器：" + "；".join(str(e) for e in extra))
        detail = "；".join(problems) or "区域信息不足以定位图形"
        return Solution(
            status="failed",
            skill=self.id,
            answer=None,
            confidence=0.0,
            method=ADAPTER_ID,
            error=f"图形区域证据不完整：{detail}（不回退整图求解，避免在网页截图上产生假证据）",
            steps=[f"[区域检查] {detail}"],
            meta={"stage": self.stage, "reason": "region_incomplete", "problems": problems},
        )

    def _localizer_fallback_inputs(self, question: Question,
                                   ctx: SkillContext) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        """R10-D 确定性回退：像素定位选项区（行序标签指派）+ 完整性校验 + 定向二次观察。

        题干裁剪仍要求解析通道产物（定位器不盲拼矩阵——避免整图假证据红线）。
        """
        notes: List[str] = []
        vision = (question.meta or {}).get("vision") or {}
        stem_crop = vision.get("stem_matrix_crop")
        stem_crop_ok = bool(stem_crop) and Path(str(stem_crop)).is_file() if stem_crop else False
        n_opts = len(question.options or [])
        if not stem_crop_ok and not question.image_path:
            return None, ["no_stem_crop_and_no_page"]
        if n_opts < 2:
            return None, ["选项少于 2 个"]
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None, ["opencv_unavailable"]
        try:
            from ..tools.figure_page_localizer import localize_page
            from ..tools.figure_tools import load_mask
            mask = load_mask(str(question.image_path))
        except Exception as exc:  # noqa: BLE001
            return None, [f"mask_error:{type(exc).__name__}"]
        vlm_opts = [r for r in (vision.get("option_regions") or []) if isinstance(r, dict)]
        extra: List[Dict[str, Any]] = []
        res = localize_page(mask, n_options=n_opts, vlm_options=vlm_opts)
        cells = res.get("option_regions") or []
        if len(cells) != n_opts:
            extra = self._targeted_second_observation(question, ctx, res, n_opts)
            if extra:
                res2 = localize_page(mask, n_options=n_opts,
                                     vlm_options=vlm_opts + extra)
                cells = res2.get("option_regions") or cells
                notes.append("second_observation_applied")
        if len(cells) != n_opts:
            return None, notes + [f"定位到 {len(cells)} 个选项区，题面 {n_opts} 个选项"]
        bad = [c["index"] for c in cells if not c["completeness"]["passed"]]
        if bad:
            return None, notes + [f"选项裁剪完整性未过: idx{bad}"]
        if ctx is not None and ctx.artifacts is not None:
            out_dir = ctx.artifacts.dir / "regions_pixel"
        else:
            import tempfile
            out_dir = Path(tempfile.gettempdir()) / "assessment_r10d"
        labels = [str(o.label) for o in question.options]
        # 墨迹框扩为格盒：以相邻单元间隙中点切分，端头按中位节距外扩
        boxes: List[List[int]] = []
        n = len(cells)
        starts = [c["bbox_page"][0] for c in cells]
        ends = [c["bbox_page"][0] + c["bbox_page"][2] for c in cells]
        pitch = max(4, (starts[-1] - starts[0]) // max(1, n - 1)) if n > 1 else \
            max(c["bbox_page"][2] for c in cells)
        for i, c in enumerate(cells):
            ink_x0, ink_y0, iw, ih = c["bbox_page"]
            ink_x1 = ink_x0 + iw
            pad = 12  # R11：向金标 padding-cell 几何对齐（ink+24px 盒）
            left_cap = (ends[i - 1] + starts[i]) // 2 if i > 0 else 0
            right_cap = (ends[i] + starts[i + 1]) // 2 if i < n - 1 else mask.shape[1]
            x0 = max(0, ink_x0 - pad) if i == 0 else max(left_cap, ink_x0 - pad)
            x1 = min(mask.shape[1], ink_x1 + pad) if i == n - 1 else min(right_cap, ink_x1 + pad)
            y0 = max(0, ink_y0 - int(ih * 0.5))
            y1 = min(mask.shape[0], ink_y0 + ih + int(ih * 0.5))
            # 保底：格盒必须完整包含墨迹（tight 间隙帽不得切进墨迹框）
            x0 = min(x0, ink_x0); x1 = max(x1, ink_x1)
            y1 = max(y1, ink_y0 + ih)
            boxes.append([x0, y0, x1, y1])
        options: List[Tuple[str, str]] = []
        for lab, c, (x0, y0, x1, y1) in zip(labels, cells, boxes):
            crop = mask[y0:y1, x0:x1]
            img = np.where(crop, 0, 255).astype(np.uint8)
            out_dir.mkdir(parents=True, exist_ok=True)
            p = out_dir / f"option_{lab}.png"
            if not cv2.imwrite(str(p), img):
                return None, notes + [f"裁剪写出失败: {lab}"]
            options.append((lab, str(p)))
        stem_evidence: Dict[str, Any] = {}
        if not stem_crop_ok:
            # R11-C：格数不再由 VLM 自报决定——像素证据重建题干矩阵。
            # 仅在布局假设唯一、目标空格可判时成立；否则如实弃权。
            sregions = res.get("stem_regions") or []
            if not sregions:
                return None, notes + ["stem_band_missing（像素证据无题干带）"]
            from ..tools.diagram_grid_evidence import merge_stem_regions
            sregions = merge_stem_regions(sregions)
            try:
                from ..tools.diagram_grid_evidence import (
                    build_grid_evidence, known_sequence)
            except ImportError:
                return None, notes + ["grid_evidence_unavailable"]
            import hashlib
            img_sha = hashlib.sha256(np.ascontiguousarray(mask).tobytes()).hexdigest()[:16]
            text = getattr(question, "text", None) or getattr(question, "stem", "") or ""
            sb = _xywh_to_xyxy(sregions[0]["bbox_page"])
            ev = build_grid_evidence(mask, img_sha, sb,
                                      0, 0, expects_blank=True,
                                      cells_hint=sregions[0].get("cells"),
                                      rows_hint=int(sregions[0].get("sub_band_count") or 1))
            seq = known_sequence(ev)
            if ev["verification_status"] == "ambiguous" or seq.get("mode") == "unsupported":
                return None, notes + [
                    f"stem_evidence_{seq.get('reason') or ev['verification_status']}"
                    f"（布局假设不唯一/空格不可判，弃权不猜）"]
            rows, cols = ev["grid_rows"], ev["grid_cols"]
            from ..tools.diagram_grid_evidence import render_canvas
            canvas = render_canvas(mask, ev)
            out_dir.mkdir(parents=True, exist_ok=True)
            sp = out_dir / "stem_evidence.png"
            if not cv2.imwrite(str(sp), np.where(canvas, 0, 255).astype(np.uint8)):
                return None, notes + ["stem_evidence_write_failed"]
            stem_crop = str(sp)
            stem_evidence = {
                "grid": [rows, cols], "target_cell": ev.get("target_cell"),
                "target_basis": ev.get("target_basis"),
                "status": ev["verification_status"], "confidence": ev["confidence"],
                "visible_cells": ev["visible_cells"], "blank_cells": ev["blank_cells"],
                "cell_pixel_shas": [c["cell_pixel_sha"] for c in ev["cells"]],
                "canvas_sha": hashlib.sha256(np.ascontiguousarray(canvas).tobytes()).hexdigest()[:16],
            }
        inputs = {
            "stem_crop": str(stem_crop),
            "options": options,
            "grid": (tuple(vision["stem_matrix_grid"])
                     if vision.get("stem_matrix_grid") else
                     ([rows, cols] if stem_evidence else None)),            "region_provenance": {
                "algorithm": "r10d-page-localizer-v1",
                "image": str(question.image_path),
                "n_options": n_opts,
                "option_cells": [{**{k: c[k] for k in
                                  ("bbox_page", "bbox_local", "pixel_sha",
                                   "completeness", "source", "confidence")},
                                  "cell_bbox": bx}
                                 for c, bx in zip(cells, boxes)],
                "stem_crop_source": ("vision_parser" if stem_crop_ok
                                     else "pixel_grid_evidence"),
                "stem_evidence": stem_evidence,
                "label_assignment": "row_major",
                "second_observation": bool(extra),
                "notes": res.get("notes") or [],
            },
        }
        return inputs, notes

    def _targeted_second_observation(self, question: Question, ctx: SkillContext,
                                     res: Dict[str, Any], n_opts: int) -> List[Dict[str, Any]]:
        """置信不足/区域缺失时的**一次**定向二次观察（只问缺的框，不做整页重解析）。

        默认关闭，`ASSESSMENT_R10_SECOND_OBS=on` 启用；失败返回 []（不重试、不追问）。
        """
        import os
        if os.environ.get("ASSESSMENT_R10_SECOND_OBS", "").lower() != "on":
            return []
        if ctx is None or ctx.llm is None:
            return []
        have = [c["bbox_page"] for c in (res.get("option_regions") or [])]
        system = ("你是行测图形题定位助手。只输出 JSON：{\"boxes\": [[x,y,w,h], ...]}，"
                  "顺序对应缺失选项（从左到右、从上到下），坐标为原图像素。不要输出其他内容。")
        user = (f"页面图 {res.get('image_size')}，共 {n_opts} 个选项，已定位 {len(have)} 个："
                f"{have}。请给出其余 {n_opts - len(have)} 个选项图形区域的 bbox。")
        try:
            payload = ctx.llm.complete_json(system, user, image_path=str(question.image_path))
        except Exception:  # noqa: BLE001 - 网络不稳：一次即止
            return []
        boxes = payload.get("boxes") if isinstance(payload, dict) else None
        out = []
        labels = [str(o.label) for o in question.options]
        start = len(have)
        for i, b in enumerate(boxes or []):
            if isinstance(b, (list, tuple)) and len(b) == 4:
                lab = labels[start + i] if start + i < len(labels) else str(i)
                out.append({"label": lab, "bbox": [int(v) for v in b],
                            "has_figure": True})
        return out

    def _recheck_region_provenance(self, question: Question,
                                   prov: Dict[str, Any]) -> Tuple[bool, str]:
        """验证器独立复推：重跑定位，比对 pixel_sha 集与完整性（不复用求解结论）。"""
        try:
            import cv2  # noqa: F401
            from ..tools.figure_page_localizer import localize_page
            from ..tools.figure_tools import load_mask
            mask = load_mask(str(prov.get("image") or question.image_path))
            n_opts = int(prov.get("n_options") or len(question.options or []))
            res = localize_page(mask, n_options=n_opts)
            fresh = [c["pixel_sha"] for c in (res.get("option_regions") or [])]
            cells_old = prov.get("option_cells") or []
            old = [c.get("pixel_sha") if isinstance(c, dict) else c for c in cells_old]
            if len(fresh) != len(old):
                return False, f"独立复推选项数 {len(fresh)} ≠ 求解时 {len(old)}"
            if fresh != old:
                return False, "独立复推像素哈希与求解时不一致（区域漂移？）"
            if any(not (c.get("completeness") or {}).get("passed")
                   for c in cells_old if isinstance(c, dict)):
                return False, "存在完整性未过的选项裁剪"
            sev = prov.get("stem_evidence") or {}
            if prov.get("stem_crop_source") == "pixel_grid_evidence" and sev:
                # R11：像素证据题干独立复推——重跑格契约并比对画布哈希与目标位
                from ..tools.diagram_grid_evidence import (
                    build_grid_evidence, render_canvas)
                import hashlib
                import numpy as np
                sregions = res.get("stem_regions") or []
                if not sregions:
                    return False, "复核无题干带，无法重建格证据"
                ev2 = build_grid_evidence(mask, "", _xywh_to_xyxy(sregions[0]["bbox_page"]),
                                          int(sev["grid"][0]), int(sev["grid"][1]),
                                          expects_blank=True,
                                          cells_hint=sregions[0].get("cells"),
                                          rows_hint=int(sregions[0].get("sub_band_count") or 1))
                canvas2 = render_canvas(mask, ev2)
                sha2 = hashlib.sha256(
                    np.ascontiguousarray(canvas2).tobytes()).hexdigest()[:16]
                if sha2 != sev.get("canvas_sha"):
                    return False, "题干证据画布复推哈希不一致（格内容漂移）"
                if list(ev2.get("target_cell") or []) != list(sev.get("target_cell") or []):
                    return False, f"目标空格复推 {ev2.get('target_cell')} ≠ 求解时 {sev.get('target_cell')}"
                return True, (f"{len(fresh)} 选项区哈希复推一致且完整；"
                               f"题干 {sev['grid'][0]}×{sev['grid'][1]} 证据复推一致")
            return True, f"{len(fresh)} 选项区哈希复推一致且完整"
        except Exception as exc:  # noqa: BLE001
            return False, f"复推异常：{type(exc).__name__}: {exc}"

    def _run_adapter(self, inputs: Dict[str, Any], *, threshold: Optional[float] = None, canvas_path: Optional[str] = None) -> Dict[str, Any]:
        from ..tools.figure_adapter import solve_figure_regions

        return solve_figure_regions(
            inputs["stem_crop"],
            inputs["options"],
            grid=inputs.get("grid"),
            threshold=threshold,
            canvas_path=canvas_path,
        )

    def _solve_regions(self, question: Question, ctx: SkillContext,
                       inputs: Dict[str, Any]) -> Solution:
        """R4g：布局重建证据随行——resolution 记入 meta.diagram_layout。"""
        res = inputs.pop("layout_resolution", None)
        prov = inputs.get("region_provenance")
        if prov is not None:
            inputs = dict(inputs)
            inputs.pop("region_provenance", None)
        sol = self._solve_regions_core(question, ctx, inputs)
        if res is not None:
            sol.meta["diagram_layout"] = res
        if prov is not None:
            sol.meta["region_provenance"] = prov
        return sol

    def _solve_regions_core(self, question: Question, ctx: SkillContext,
                            inputs: Dict[str, Any]) -> Solution:
        """有区域裁剪证据时走适配层；适配异常 → 结构化失败（不回退整图猜测）。"""
        if getattr(ctx.settings, "r4a_disable_six_rules", False):
            # 消融臂 r1：六类通道禁用，仅变换通道可作答
            payload = {"status": "failed", "reason": "ablation: 六类通道禁用",
                       "candidates": [], "verdict": {}}
        else:
            try:
                payload = self._run_adapter(inputs)
            except Exception as exc:  # noqa: BLE001 - 区域异常记录为失败，不猜答案
                return Solution(
                    status="failed",
                    skill=self.id,
                    answer=None,
                    confidence=0.0,
                    method=ADAPTER_ID,
                    error=f"区域适配失败：{type(exc).__name__}: {exc}",
                    tool_calls=[
                        ToolInvocation(
                            tool=ADAPTER_ID,
                            args={"stem_crop": inputs["stem_crop"], "labels": [label for label, _ in inputs["options"]]},
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    ],
                    meta={"stage": self.stage, "reason": "adapter_error"},
                )
        six_solved = payload.get("status") == "solved"
        # R4b P1-1：变换通道对**所有**区域化题常开，适用性由图形结构决定
        # （solve_figure_transform_regions 内部结构过滤：单行末空格/九宫格第9空格），
        # 不再读取数据集 layout 标签——Oracle/AutoRouter/ImageOnly 三模式同一决策路径。
        if getattr(ctx.settings, "r4a_transform", True):
            # 六类未解出 → 变换通道独立作答；六类已解 → 双通道交叉校验。
            # 冲突拦截仅限"变换规律族真的表态"的情形（有投票候选）：
            # 变换通道无候选 = 结构上不适用 = 无意见，保留六类结果（历史语义）。
            try:
                from ..tools.figure_adapter import solve_figure_transform_regions

                tp = solve_figure_transform_regions(
                    inputs["stem_crop"],
                    inputs["options"],
                    grid=inputs.get("grid"),
                )
            except Exception as exc:  # noqa: BLE001
                tp = {"status": "failed", "reason": f"变换通道异常：{type(exc).__name__}: {exc}",
                      "candidates": [], "verdict": {"verdict": "no_rule"}}
            tinv = ToolInvocation(
                tool="figure_transform",
                args={"stem_crop": inputs["stem_crop"],
                      "labels": [label for label, _ in inputs["options"]],
                      "mode": tp.get("mode")},
                ok=True,
                result={"status": tp.get("status"), "answer": tp.get("answer"),
                        "verdict": tp.get("verdict"),
                        "candidates": tp.get("candidates"),
                        "symmetry_unresolvable": tp.get("symmetry_unresolvable"),
                        "verification": tp.get("verification")},
            )
            if six_solved:
                if tp.get("status") == "solved" and tp.get("answer") == payload.get("answer"):
                    sol = self._finalize_regions(question, payload, inputs)
                    sol.meta["transform_cross_support"] = True
                    sol.meta["transform"] = {k: tp.get(k) for k in TRANSFORM_META_KEYS}
                    sol.tool_calls.append(tinv)
                    return sol
                t_verdict = str((tp.get("verdict") or {}).get("verdict") or "")
                has_cands = bool(tp.get("candidates"))
                if tp.get("status") == "solved" or (
                        has_cands and t_verdict in ("ambiguous", "multiple_rules")):
                    return Solution(
                        status="failed",
                        skill=self.id,
                        answer=None,
                        confidence=0.0,
                        method="figure_transform",
                        error=(f"规律族冲突（ambiguous）：六类规律 {payload.get('rule')} "
                               f"指向 {payload.get('answer')}，变换规律指向 "
                               f"{tp.get('answer') or '多个/无法唯一'}，不能共同支持同一选项"),
                        tool_calls=[tinv],
                        meta={"stage": self.stage, "channel": "cross_check",
                              "transform": {k: tp.get(k) for k in TRANSFORM_META_KEYS},
                              "six_rule_claim": {"rule": payload.get("rule"),
                                                 "answer": payload.get("answer")}},
                    )
                # 变换通道无规律可提 → 保留六类结果（历史语义）
                sol = self._finalize_regions(question, payload, inputs)
                sol.meta["transform"] = {k: tp.get(k) for k in TRANSFORM_META_KEYS}
                return sol
            if tp.get("status") in ("solved", "unverified"):
                if tp.get("status") == "unverified":
                    return Solution(
                        status="failed",       # 有唯一候选但独立验证不稳定 → 诚实弃权
                        skill=self.id,
                        answer=None,
                        confidence=0.0,
                        method="figure_transform",
                        error="变换候选未通过独立验证（unverified），不对外作答",
                        steps=tp.get("steps") or [],
                        tool_calls=[tinv],
                        meta={"stage": self.stage, "channel": "transform",
                              "transform": {k: tp.get(k) for k in TRANSFORM_META_KEYS},
                              "six_rule_failure": payload.get("reason")},
                    )
                return Solution(
                    status="solved",
                    skill=self.id,
                    answer=tp.get("answer"),
                    answer_text=(question.option_text(tp["answer"])
                                 if tp.get("answer") else "") or "",
                    confidence=0.88,
                    method="figure_transform",
                    steps=tp.get("steps") or [],
                    tool_calls=[tinv],
                    meta={"stage": self.stage, "channel": "transform",
                          "transform": {k: tp.get(k) for k in TRANSFORM_META_KEYS},
                          "six_rule_failure": payload.get("reason")},
                )
            # 变换通道也失败：合并两通道原因，保持 failed（诚实）
            merged = self._finalize_regions(question, payload, inputs)
            merged.error = (merged.error or "六类规律未解") + f"；变换通道：{tp.get('reason')}"
            merged.meta["transform"] = {k: tp.get(k) for k in TRANSFORM_META_KEYS}
            return merged
        return self._finalize_regions(question, payload, inputs)

    def _finalize_regions(self, question: Question, payload: Dict[str, Any], inputs: Dict[str, Any]) -> Solution:
        invocation = ToolInvocation(
            tool=ADAPTER_ID,
            args={
                "stem_crop": inputs["stem_crop"],
                "option_labels": [label for label, _ in inputs["options"]],
                "grid": list(inputs["grid"]) if inputs.get("grid") else None,
            },
            ok=True,
            result={key: payload.get(key) for key in ("status", "mode", "rule", "answer", "reason", "agreement")},
        )
        meta: Dict[str, Any] = {
            "stage": self.stage,
            "image": question.image_path,
            "mode": payload.get("mode"),
            "rule": payload.get("rule"),
            "agreement": payload.get("agreement") or [],
            "layout": payload.get("layout") or {},
            "predicted": payload.get("predicted"),
            "options_from_image": payload.get("options") or [],
            "candidates": payload.get("candidates") or [],
            "regions": {
                "stem_crop": inputs["stem_crop"],
                "option_labels": [label for label, _ in inputs["options"]],
                "option_crops": [path for _, path in inputs["options"]],
                "stem_grid": list(inputs["grid"]) if inputs.get("grid") else None,
            },
            "tool": ADAPTER_ID,
        }
        steps = list(payload.get("steps") or [])
        if payload.get("status") != "solved":
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method=ADAPTER_ID,
                error=payload.get("reason") or "区域图形规律未能确定唯一答案",
                steps=steps,
                tool_calls=[invocation],
                meta=meta,
            )
        answer = payload.get("answer")
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer,
            answer_text=(question.option_text(answer) if answer else "") or "",
            steps=steps,
            confidence=float(payload.get("confidence") or 0.85),
            method=ADAPTER_ID,
            tool_calls=[invocation],
            meta=meta,
        )

    # ------------------------------------------------------------------ 求解
    def solve(self, question: Question, ctx: SkillContext) -> Solution:
        if not question.image_path:
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method="figure_rules",
                error="图形推理需要题目截图：请用 --image 传入题图（纯文本无法还原图形）",
                meta={"stage": self.stage, "reason": "missing_image"},
            )

        # R2a：区域裁剪证据优先（不再假定整图最后一行是选项，也不需要整图 grid）
        vision_present = isinstance((question.meta or {}).get("vision"), dict)
        region_inputs = self._region_inputs(question, ctx) if vision_present else None
        if region_inputs is not None:
            return self._solve_regions(question, ctx, region_inputs)
        if vision_present:
            # R10-D：VLM 区域不足时，确定性像素定位器补齐选项区（题干裁剪仍需解析
            # 通道提供，不回退整图）；来源与完整性全程可审计
            fb, fb_notes = self._localizer_fallback_inputs(question, ctx)
            if fb is not None:
                return self._solve_regions(question, ctx, fb)
            return self._region_incomplete(question, extra=fb_notes)

        grid = self._grid(question)
        invocation = ctx.toolbox.run(TOOL_ID, path=question.image_path, grid=grid)
        if not invocation.ok:
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method="figure_rules",
                error=f"图形分析失败：{invocation.error}",
                tool_calls=[invocation],
                meta={"stage": self.stage, "grid": list(grid) if grid else None},
            )

        payload: Dict[str, Any] = dict(invocation.result or {})
        layout = payload.get("layout") or {}
        meta: Dict[str, Any] = {
            "stage": self.stage,
            "image": question.image_path,
            "grid": list(grid) if grid else None,
            "mode": payload.get("mode"),
            "rule": payload.get("rule"),
            "agreement": payload.get("agreement") or [],
            "layout": layout,
            "predicted": payload.get("predicted"),
            "options_from_image": payload.get("options") or [],
            "candidates": payload.get("candidates") or [],
            "tool": TOOL_ID,
        }

        if payload.get("status") != "solved":
            return Solution(
                status="failed",
                skill=self.id,
                answer=None,
                confidence=0.0,
                method="figure_rules",
                error=payload.get("reason") or "图形规律未能确定唯一答案",
                steps=list(payload.get("steps") or []),
                tool_calls=[invocation],
                meta=meta,
            )

        steps = list(payload.get("steps") or [])
        bands = layout.get("cells_per_band") or []
        steps.insert(
            0,
            f"网格解析：{layout.get('mode')} 模式，{len(bands)} 行，各行列数 {bands}，"
            f"二值化阈值 {layout.get('threshold')}",
        )
        answer = payload.get("answer")
        return Solution(
            status="solved",
            skill=self.id,
            answer=answer,
            answer_text=(question.option_text(answer) if answer else "") or "",
            steps=steps,
            confidence=float(payload.get("confidence") or 0.85),
            method="figure_rules",
            tool_calls=[invocation],
            meta=meta,
        )

    # ------------------------------------------------------------ 独立复核
    def _verify_transform(self, question: Question, solution: Solution,
                          meta: Dict[str, Any], ctx: SkillContext) -> Verification:
        """变换通道验证：R2c 候选代回复核架构保留——验证器从**原始裁剪**独立重建
        每格 mask、重跑变换推理与帧参数扫描；求解器的归一化/容差不作前提。
        消融臂3（indep_verifier=off）时只做候选字段自洽检查（回到原 verifier 强度）。"""
        t = meta.get("transform") or {}
        verdict = t.get("verdict") or {}
        checks: List[VerificationCheck] = []
        if solution.answer is None:
            checks.append(VerificationCheck(
                name="figure_transform.abstain", passed=True, severity="info",
                detail="变换通道未给出对外答案（unverified/failed 语义）"))
            return Verification(passed=False, action="unverified", checks=checks,
                                notes="候选未过独立验证，弃权")
        settings = getattr(ctx, "settings", None)
        indep = getattr(settings, "r4a_indep_verifier", True)
        cands = t.get("candidates") or []
        voting = [c for c in cands
                  if c.get("candidate_scores", {}).get(solution.answer, 1.0) <= 0.15
                  and (c.get("discriminative_margin") or 0) >= 0.05]
        checks.append(VerificationCheck(
            name="figure_transform.support", passed=bool(voting),
            detail=f"{len(voting)} 条**参与裁决**的候选在容差内支持 {solution.answer}"
                   f"（总候选 {len(cands)} 条；未排除竞争项的候选按弃权处理，不构成竞争）"))
        if not voting:
            return Verification(passed=False, action="unverified", checks=checks,
                                notes="无参与裁决的候选支撑该答案")
        if indep:
            # 独立重建：从原始裁剪文件重新切格重跑（与求解器同一入口但独立调用，
            # verify_transformation 已在 solve 内做过 9 组参数扫描，这里再独立做一次，
            # 不复用 solution.meta 中求解器留下的结论）
            try:
                from ..tools.figure_adapter import solve_figure_transform_regions
                inputs = self._region_inputs(question, ctx)
                if not inputs:
                    # R10-D：验证独立重建与求解走**同一像素回退入口**（重新定位，
                    # 不复用求解时的裁剪文件声明）
                    inputs, _ = self._localizer_fallback_inputs(question, ctx)
                if not inputs:
                    raise RuntimeError("区域裁剪不可用")
                fresh = solve_figure_transform_regions(
                    inputs["stem_crop"], inputs["options"], grid=inputs.get("grid"))
            except Exception as exc:  # noqa: BLE001
                checks.append(VerificationCheck(
                    name="figure_transform.independent", passed=False,
                    detail=f"独立重建失败：{exc}"))
                return Verification(passed=False, action="unverified", checks=checks,
                                    notes="验证器无法独立重建证据链")
            fresh_answer = fresh.get("answer")
            stable = (fresh.get("status") == "solved" and fresh_answer == solution.answer)
            checks.append(VerificationCheck(
                name="figure_transform.independent", passed=stable,
                detail=f"独立重算={fresh_answer or fresh.get('reason')}，求解器={solution.answer}"))
            if not stable:
                return Verification(passed=False,
                                    action="reject" if fresh_answer else "unverified",
                                    checks=checks, notes="独立重算不一致")
        passed = all(c.passed for c in checks if c.severity == "blocking")
        return Verification(passed=passed, action="accept" if passed else "unverified",
                            checks=checks, final_answer=solution.answer if passed else None,
                            notes="变换通道独立验证通过" if passed else "变换验证未过")

    def verify(self, question: Question, solution: Solution, ctx: SkillContext) -> Optional[Verification]:
        if solution.status != "solved":
            return None
        meta = solution.meta or {}
        prov = meta.get("region_provenance")
        if prov:
            # R10-D 内容完整性红线：像素定位来源必须能被独立复推（哈希一致+完整），
            # 否则不释放——定位器的产物不能靠自身声明过检
            ok, detail = self._recheck_region_provenance(question, prov)
            chk = VerificationCheck(name="figure.region_provenance", passed=ok,
                                    detail=detail)
            if not ok:
                return Verification(passed=False, action="unverified", checks=[chk],
                                    notes=f"区域来源复核失败：{detail}")
        if (meta.get("channel") or "") == "transform" or meta.get("transform_cross_support"):
            # R4a：变换通道解 + 双通道互证解 → 由变换独立 verifier 接管复核
            # （R2c substitution 复核不认识旋转/镜像/叠加等几何约束，互证题若再走
            # R2c 只会产生系统性错误拒绝；独立验证器的 9 参数重算覆盖更强）
            return self._verify_transform(question, solution, meta, ctx)
        regions = meta.get("regions")
        if regions:
            return self._verify_regions(question, solution, regions, settings=getattr(ctx, "settings", None))
        image = meta.get("image") or question.image_path
        grid = tuple(meta["grid"]) if meta.get("grid") else None
        layout = meta.get("layout") or {}
        threshold = layout.get("threshold")
        alternative = 127 if threshold == "otsu" else 160
        checks: List[VerificationCheck] = []

        if not image:
            return None
        invocation = ctx.toolbox.run(TOOL_ID, path=image, grid=grid, threshold=alternative)
        if invocation.ok:
            rerun = dict(invocation.result or {})
            same = rerun.get("status") == "solved" and rerun.get("answer") == solution.answer
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=same,
                    detail=(
                        f"阈值 {threshold} → {alternative} 独立复算："
                        f"{rerun.get('answer') or rerun.get('reason')}（Solver 记录 {solution.answer}）"
                    ),
                )
            )
            if same:
                checks.append(
                    VerificationCheck(
                        name="figure.rule_stable",
                        passed=rerun.get("rule") == meta.get("rule"),
                        severity="warning",
                        detail=f"两次阈值命中的规律：{meta.get('rule')} vs {rerun.get('rule')}",
                    )
                )
        else:
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=False,
                    detail=f"阈值 {alternative} 独立复算失败：{invocation.error}",
                )
            )

        checks.append(
            VerificationCheck(
                name="figure.unique_match",
                passed=len(meta.get("agreement") or []) >= 1,
                detail=f"能唯一匹配选项的规律：{meta.get('agreement')}（命中规律 {meta.get('rule')}）",
            )
        )
        if question.options and solution.answer:
            checks.append(
                VerificationCheck(
                    name="figure.answer_in_options",
                    passed=solution.answer in question.option_labels(),
                    detail=f"答案 {solution.answer} 是否在选项 {question.option_labels()} 内",
                )
            )

        passed = all(check.passed for check in checks if check.severity == "blocking")
        return Verification(
            passed=passed,
            action="accept" if passed else "reject",
            checks=checks,
            final_answer=solution.answer if passed else None,
            notes="换阈值独立复算 + 唯一匹配检查（不做答案修补，避免猜测）",
        )

    # ------------------------------------------------------ 区域模式独立复核
    def _verify_regions(
        self,
        question: Question,
        solution: Solution,
        regions: Dict[str, Any],
        *,
        settings: Any = None,
    ) -> Optional[Verification]:
        """区域裁剪证据重放：候选代入独立复核（R2c）+ 换阈值重跑（兼容保留）。

        R2c 核心：换阈值重跑只验证同一规则引擎的自我一致性，发现不了系统性规则
        误选（独立基准里的 verifier_false_accept 全部来自这种"自证"）。现在必须先
        通过 ``figure_verify.substitution_check``——逐选项单独感知复测 + 从题干独立
        重建约束 + 候选代入：

        - ``confirmed``：全部约束满足且唯一 → 才可能 accept；
        - ``contradicted``：候选违反可独立复测的约束 → reject（验证器确实发现了错误）；
        - ``unverified``：约束无法重建/唯一性不成立 → 受控拒答，**绝不默认通过**，
          也不冒充"验证纠错"（上层归因区分二者）。
        """
        meta = solution.meta or {}
        labels = regions.get("option_labels") or []
        crops = regions.get("option_crops") or []
        stem_crop = regions.get("stem_crop")
        if not stem_crop or len(labels) != len(crops) or len(labels) < 2:
            return None
        layout = meta.get("layout") or {}
        threshold = layout.get("threshold")
        alternative = 127 if threshold == "otsu" else 160
        inputs = {
            "stem_crop": stem_crop,
            "options": list(zip(labels, crops)),
            "grid": tuple(regions["stem_grid"]) if regions.get("stem_grid") else None,
        }
        checks: List[VerificationCheck] = []
        independent_enabled = bool(getattr(settings, "r2c_figure_verify", True))
        verdict: Optional[str] = None
        if independent_enabled:
            from ..tools.figure_verify import substitution_check

            try:
                report = substitution_check(
                    stem_crop,
                    list(zip(labels, crops)),
                    solution.answer or "",
                    meta.get("rule"),
                    grid=inputs["grid"],
                )
            except Exception as exc:  # noqa: BLE001 - 复核自身异常按未验证处理，不默认通过
                report = {"verdict": "unverified", "reasons": [f"独立复核执行异常：{type(exc).__name__}: {exc}"]}
            verdict = str(report.get("verdict") or "unverified")
            checks.append(
                VerificationCheck(
                    name="figure.independent_substitution",
                    passed=verdict == "confirmed",
                    detail=f"[{verdict}] " + "；".join(str(r) for r in report.get("reasons") or []),
                )
            )
        try:
            rerun = self._run_adapter(inputs, threshold=alternative)
            same = rerun.get("status") == "solved" and rerun.get("answer") == solution.answer
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=same,
                    severity="warning" if verdict == "confirmed" else "blocking",
                    detail=(
                        f"区域模式阈值 {threshold} → {alternative} 自我一致性重跑："
                        f"{rerun.get('answer') or rerun.get('reason')}（Solver 记录 {solution.answer}）"
                        + ("；独立复核已确认，此项仅作阈值鲁棒性记录" if verdict == "confirmed" else "")
                    ),
                )
            )
            if same:
                checks.append(
                    VerificationCheck(
                        name="figure.rule_stable",
                        passed=rerun.get("rule") == meta.get("rule"),
                        severity="warning",
                        detail=f"两次阈值命中的规律：{meta.get('rule')} vs {rerun.get('rule')}",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                VerificationCheck(
                    name="figure.rerun_consistent",
                    passed=False,
                    detail=f"区域模式阈值 {alternative} 独立复算失败：{type(exc).__name__}: {exc}",
                )
            )

        checks.append(
            VerificationCheck(
                name="figure.unique_match",
                passed=len(meta.get("agreement") or []) >= 1,
                detail=f"能唯一匹配选项的规律：{meta.get('agreement')}（命中规律 {meta.get('rule')}）",
            )
        )
        checks.append(
            VerificationCheck(
                name="figure.labels_from_regions",
                passed=bool(regions.get("option_labels")) and solution.answer in labels,
                detail=f"答案 {solution.answer} 是否来自区域标号 {labels}（裁剪证据 {len(crops)} 份）",
            )
        )

        blocking_ok = all(check.passed for check in checks if check.severity == "blocking")
        if blocking_ok:
            passed, action = True, "accept"
        else:
            passed = False
            action = "unverified" if verdict == "unverified" else "reject"
        return Verification(
            passed=passed,
            action=action,
            checks=checks,
            final_answer=solution.answer if passed else None,
            notes="R2c 候选代入独立复核 + 换阈值自我一致性重跑 + 唯一匹配检查（不确认不通过，不做答案修补）",
        )
