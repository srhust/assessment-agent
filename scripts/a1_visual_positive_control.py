# -*- coding: utf-8 -*-
"""R11-A：视觉阳性对照（工单 A 阶段验收物）。

文字指令**完全相同**、图像内容明显不同的两张图，必须得到不同且正确的回答，
证明网关模型真的利用图片内容（JSON/坐标/回显模型名都不构成此证据）。
预算：≤4 次 HTTP（含每题 1 次重试）。不保存 Base64/密钥，只存 SHA/字节/尺寸/
内容块标志/模型回显。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BUDGET_HTTP = 4

PROMPT_SYSTEM = "你只输出 JSON，不输出其他文本。"
PROMPT_USER = ("图中黑色实心图形是什么形状、共几个？"
               '输出 {"shape": "circle|square", "count": <整数>}')


def make_controls(out_dir: Path) -> list[dict]:
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    specs = []
    # 控制组 1：3 个圆
    im = Image.new("L", (300, 160), 255)
    d = ImageDraw.Draw(im)
    for i in range(3):
        d.ellipse([30 + i * 90, 50, 100 + i * 90, 120], fill=0)
    p1 = out_dir / "control_1_three_circles.png"
    im.save(p1)
    specs.append({"id": "ctrl1", "path": str(p1), "shape": "circle", "count": 3})
    # 控制组 2：7 个方块（2+2+2+1 排布）
    im2 = Image.new("L", (300, 200), 255)
    d2 = ImageDraw.Draw(im2)
    pos = [(20, 20), (120, 20), (20, 90), (120, 90), (20, 160), (120, 160), (220, 90)]
    for x, y in pos:
        d2.rectangle([x, y - 30 if y >= 30 else y, x + 60, y + 30], fill=0)
    p2 = out_dir / "control_2_seven_squares.png"
    im2.save(p2)
    specs.append({"id": "ctrl2", "path": str(p2), "shape": "square", "count": 7})
    return specs


def main() -> int:
    from assessment_agent.config import Settings
    from assessment_agent.llm import OpenAICompatibleClient, image_audit_meta

    settings = Settings.from_env().llm
    if not (settings.api_key and settings.vision_model):
        print("LLM 未配置：视觉阳性对照无法执行（VISION_GATEWAY_NOT_CONFIGURED）")
        return 2
    client = OpenAICompatibleClient(settings)
    out_dir = ROOT / "evidence" / "r11_visual_controls"
    specs = make_controls(out_dir)
    results = []
    used = 0
    for spec in specs:
        row = {"id": spec["id"], "expect": {"shape": spec["shape"], "count": spec["count"]},
               "image_audit": image_audit_meta(spec["path"])}
        for attempt in (1, 2):
            if used >= BUDGET_HTTP:
                row["attempts"].append("budget_exhausted")
                break
            used += 1
            try:
                ans = client.complete_json(PROMPT_SYSTEM, PROMPT_USER,
                                           image_path=spec["path"])
                row["answer"] = ans
                break
            except Exception as exc:  # noqa: BLE001
                row.setdefault("errors", []).append(f"{type(exc).__name__}: {str(exc)[:80]}")
        ok = (row.get("answer") or {}).get("shape") == spec["shape"] and \
            (row.get("answer") or {}).get("count") == spec["count"]
        row["correct"] = bool(ok)
        results.append(row)
    snap = client.audit_snapshot()
    both = len(results) == 2 and all(r["correct"] for r in results)
    answers_distinct = (len({json.dumps(r.get("answer"), sort_keys=True)
                             for r in results if r.get("answer")}) == 2)
    verdict = ("VISION_GATEWAY_VERIFIED" if both and answers_distinct
               else "VISION_GATEWAY_NOT_VERIFIED")
    out = {"verdict": verdict, "http_budget": BUDGET_HTTP, "http_used": used,
           "same_text_prompt": PROMPT_USER, "results": results,
           "audit": {"requests_sent": snap.get("requests_sent"),
                     "requests_succeeded": snap.get("requests_succeeded"),
                     "echoed_models": snap.get("echoed_models"),
                     "error_types": snap.get("error_types"),
                     "image_evidence": snap.get("by_phase", {})}}
    (ROOT / "evidence/R11A_VISUAL_CONTROL.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(verdict, "| http_used", used, "| distinct", answers_distinct)
    return 0 if verdict == "VISION_GATEWAY_VERIFIED" else 3


if __name__ == "__main__":
    sys.exit(main())
