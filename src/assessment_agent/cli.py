"""命令行入口：``python -m assessment_agent <command>``。

命令：
- ``solve``    求解单题（文本或截图）
- ``eval``     运行 train/dev/test 数据集评测
- ``skills``   列出八个题型 Skill 及其阶段状态
- ``doctor``   打印环境与能力自检
- ``inspect``  查看某次运行的产物摘要
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Settings
from .evaluation.runner import EvaluationRunner
from .pipeline import Pipeline, PipelineError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assessment-agent",
        description="多模态招聘测评解题 Agent（北森 / SHL 类练习与模拟测试）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    solve = subparsers.add_parser("solve", help="求解单道题目")
    solve.add_argument("--text", help="题干文本（含选项时可自动拆分）")
    solve.add_argument("--image", help="题目截图路径")
    solve.add_argument("--options", help='选项，如 "A=12,B=13,C=16" 或 "12|13|16"')
    solve.add_argument("--skill", help="显式指定题型（默认自动路由）")
    solve.add_argument("--run-id", help="自定义运行 ID")
    solve.add_argument("--hint", help="截图抽取的补充提示")
    solve.add_argument("--no-save", action="store_true", help="不写 runs/ 产物")
    solve.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    evaluate = subparsers.add_parser("eval", help="运行数据集评测")
    evaluate.add_argument(
        "--split",
        default="test",
        choices=["train", "dev", "test", "screens", "bench_dev", "bench_holdout",
                  "verbal_dev", "verbal_blind", "verbal_epistemic_dev",
                  "language_dev", "language_blind", "verbal_blind2",
                  "figtrans_dev", "figtrans_test",
                  "figshots_dev", "figshots_test"],
        help="screens=合成截图回归基线；bench_dev/bench_holdout=独立泛化基准（可调参/冻结）；"
              "verbal_epistemic_dev=R3c 命题归属对照组（最小对比对，可调参）；"
              "language_dev/language_blind=R3c 语言推理（可调参/冻结）；"
              "verbal_blind2=R3c 全新言语盲集（冻结）；"
              "figtrans_dev/figtrans_test=R4a 图形变换基准（可调参/冻结首评）；"
              "figshots_dev/figshots_test=R4b 完整页面截图集（image_only 端到端，可调参/冻结）",
    )
    evaluate.add_argument("--eval-batch", help="评测批次标识（image_only runs 产物按批次隔离，默认使用时间戳）")
    evaluate.add_argument("--skills", help="只评测指定题型，逗号分隔")
    evaluate.add_argument("--case-ids", help="R4c 冒烟：只评测指定题号，逗号分隔（运行前显式声明）")
    evaluate.add_argument("--limit", type=int, help="R4c 冒烟：只评测前 N 题（顺序=数据集行序，不按结果筛选）")
    evaluate.add_argument("--run-name", help="结果目录名")
    evaluate.add_argument("--no-overwrite", action="store_true",
                          help="R4d：输出目录已存在且非空时报错退出（保护历史调用凭证）")
    evaluate.add_argument("--no-save", action="store_true", help="不写 results/ 产物")
    evaluate.add_argument("--json", action="store_true", help="以 JSON 输出指标")
    evaluate.add_argument(
        "--route-mode",
        default="oracle",
        choices=["oracle", "auto"],
        help="路由模式（兼容保留）：oracle→oracle_solver，auto→auto_router",
    )
    evaluate.add_argument(
        "--eval-mode",
        default=None,
        choices=["oracle_solver", "auto_router", "image_only"],
        help="评测输入模式：oracle_solver 标注题型+标准题干；auto_router 标准题干+自动路由；"
        "image_only 仅原始截图（Vision+Router+Solver+Verifier 全链路，不注入任何标注）",
    )

    subparsers.add_parser("skills", help="列出题型 Skill")

    doctor = subparsers.add_parser("doctor", help="环境与能力自检")
    doctor.add_argument("--json", action="store_true")

    inspect = subparsers.add_parser("inspect", help="查看运行产物")
    inspect.add_argument("run", help="运行 ID 或 runs/ 下的目录路径")

    manifest = subparsers.add_parser("mcp-manifest", help="输出 MCP / DSH 工具清单")
    manifest.add_argument("--out", help="写入文件路径（默认打印到标准输出）")

    call = subparsers.add_parser("call", help="按 MCP tools/call 形状调用工具")
    call.add_argument("--tool", required=True, help="工具名，如 assessment_solve")
    call.add_argument("--args", default="{}", help='JSON 参数，如 \'{"text": "2+2=?"}\'')
    call.add_argument(
        "--args-file",
        help="从文件读取 JSON 参数（Windows PowerShell 会吞掉内层双引号，建议改用本参数）",
    )
    return parser


def _print_solution(result: Any) -> None:
    question = result.question
    solution = result.solution
    verification = result.verification
    print("=" * 68)
    print(f"题型：{result.route.skill}（{result.route.source}，置信度 {result.route.confidence:.2f}）")
    print(f"题干：{question.stem}")
    for option in question.options:
        print(f"  {option.label}. {option.text}")
    print("-" * 68)
    print(f"状态：{solution.status}")
    if solution.steps:
        print("步骤：")
        for step in solution.steps:
            print(f"  - {step}")
    for call in solution.tool_calls:
        status = "ok" if call.ok else f"failed: {call.error}"
        print(f"工具 {call.tool}: {status} ({call.duration_ms} ms)")
    final_answer = (
        verification.final_answer
        if verification.action in {"accept", "repair"}
        else None
    )
    print(f"答案：{final_answer or '无'}   校验：{'通过' if verification.passed else '未通过'}（{verification.action}）")
    if verification.repaired_answer:
        print(f"校验修正答案：{verification.repaired_answer}")
    if solution.error:
        print(f"说明：{solution.error}")
    if result.artifacts:
        print(f"产物目录：{result.run_dir}")
        print(f"报告：{result.artifacts.get('report')}")
    print("=" * 68)


def _cmd_solve(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = Pipeline(settings)
    try:
        result = pipeline.run(
            text=args.text,
            image_path=args.image,
            options=args.options,
            skill=args.skill,
            run_id=args.run_id,
            save=not args.no_save,
            hint=args.hint,
        )
    except PipelineError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
    else:
        _print_solution(result)
    if result.solution.status == "unsupported":
        return 2
    return 0 if result.verification.passed else 1


def _cmd_eval(args: argparse.Namespace, settings: Settings) -> int:
    pipeline = Pipeline(settings)
    runner = EvaluationRunner(pipeline, data_dir=settings.data_dir, results_dir=settings.results_dir)
    skills = [item.strip() for item in args.skills.split(",") if item.strip()] if args.skills else None
    try:
        summary = runner.run(
            split=args.split,
            save=not args.no_save,
            run_name=args.run_name,
            skills=skills,
            route_mode=args.route_mode,
            eval_mode=getattr(args, "eval_mode", None),
            eval_batch=getattr(args, "eval_batch", None),
            case_ids=([q.strip() for q in args.case_ids.split(",") if q.strip()]
                      if getattr(args, "case_ids", None) else None),
            limit=getattr(args, "limit", None),
            no_overwrite=bool(getattr(args, "no_overwrite", False)),
        )
    except (FileNotFoundError, ValueError, FileExistsError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "split": summary.split,
                    "eval_mode": summary.eval_mode,
                    "total": summary.total,
                    "accuracy": summary.accuracy,
                    "duration_s": summary.duration_s,
                    "output_dir": summary.output_dir,
                    "metrics": summary.metrics,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        metrics = summary.metrics
        print(
            f"split={summary.split}  eval_mode={summary.eval_mode}  样本 {summary.total}"
            f"  准确率 {summary.accuracy:.2%}  用时 {summary.duration_s:.2f}s"
        )
        e2e = metrics.get("e2e") or {}
        if e2e:
            print(
                f"E2E(image_only)：题干解析 {e2e.get('extraction_accuracy')!s}｜"
                f"区域IoU {e2e.get('region_iou_mean')!s}｜选项映射 {e2e.get('option_mapping_accuracy')!s}｜"
                f"可答题准确率 {e2e.get('answer_accuracy')!s}（分母 {e2e.get('answer_scored')}）｜"
                f"拒答准确率 {e2e.get('abstention_accuracy')!s}（分母 {e2e.get('abstention_scored')}）"
            )
        print(f"{'题型':<24}{'正确/总数':<14}{'准确率':<10}{'平均ms':<10}{'p95ms':<10}失败原因")
        for skill, stats in metrics["per_skill"].items():
            reasons = ", ".join(f"{key}×{value}" for key, value in stats["failure_reasons"].items()) or "-"
            print(
                f"{skill:<24}{str(stats['correct']) + '/' + str(stats['total']):<14}"
                f"{stats['accuracy']:<10.2%}{stats['mean_latency_ms']:<10.1f}{stats['p95_latency_ms']:<10.1f}{reasons}"
            )
        if summary.output_dir:
            print(f"结果目录：{summary.output_dir}")
    return 0


def _cmd_skills(settings: Settings) -> int:
    pipeline = Pipeline(settings)
    for skill in pipeline.registry.describe():
        stage = "MVP（已实现）" if skill["supported"] else "第二阶段（仅接口）"
        print(f"{skill['id']:<24}{skill['name_zh']:<10}{stage:<20}{skill['description']}")
    return 0


def _cmd_doctor(settings: Settings, as_json: bool) -> int:
    pipeline = Pipeline(settings)
    capabilities = pipeline.capabilities()
    if as_json:
        print(json.dumps(capabilities, ensure_ascii=False, indent=2))
        return 0
    info = capabilities["settings"]
    print("== 环境 ==")
    print(f"offline={info['offline']}  llm_enabled={info['llm_enabled']}  api_key_present={info['api_key_present']}")
    print(f"base_url={info['base_url']}  text_model={info['text_model']}  vision_model={info['vision_model']}")
    print("\n== 工具 ==")
    for tool in capabilities["tools"]:
        state = "可用" if tool["available"] else f"不可用（{tool['unavailable_reason']}）"
        print(f"{tool['id']:<16}stage={tool['stage']}  {state}")
    print("\n== Skill ==")
    for skill in capabilities["skills"]:
        state = "MVP" if skill["supported"] else "接口"
        print(f"{skill['id']:<24}{skill['name_zh']:<10}{state}")
    print(f"\n== 知识库 ==\n{', '.join(capabilities['knowledge']) or '（空）'}")
    return 0


def _cmd_inspect(args: argparse.Namespace, settings: Settings) -> int:
    candidate = Path(args.run)
    run_dir = candidate if candidate.is_dir() else settings.runs_dir / args.run
    if not run_dir.is_dir():
        print(f"[错误] 未找到运行目录: {run_dir}", file=sys.stderr)
        return 2
    for name in ("run.json", "solution.json", "verification.json"):
        path = run_dir / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        print(f"== {name} ==")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
    report = run_dir / "report.md"
    if report.is_file():
        print(f"\n报告：{report}")
    return 0


def _cmd_mcp_manifest(args: argparse.Namespace) -> int:
    from .integration import tool_manifest

    payload = json.dumps(tool_manifest(), ensure_ascii=False, indent=2)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        print(f"已写入 {path}")
    else:
        print(payload)
    return 0


def _cmd_call(args: argparse.Namespace, settings: Settings) -> int:
    from .integration import AssessmentAgentService

    raw = args.args or "{}"
    if args.args_file:
        path = Path(args.args_file)
        if not path.is_file():
            print(f"[错误] 参数文件不存在: {path}", file=sys.stderr)
            return 2
        # utf-8-sig：兼容 Windows PowerShell 写出的 BOM 文件
        raw = path.read_text(encoding="utf-8-sig")
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[错误] 参数不是合法 JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(arguments, dict):
        print("[错误] --args 必须是 JSON 对象", file=sys.stderr)
        return 2
    service = AssessmentAgentService(settings)
    result = service.call_tool(args.tool, arguments)
    for item in result["content"]:
        print(item["text"])
    return 1 if result.get("isError") else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "solve":
        return _cmd_solve(args, settings)
    if args.command == "eval":
        return _cmd_eval(args, settings)
    if args.command == "skills":
        return _cmd_skills(settings)
    if args.command == "doctor":
        return _cmd_doctor(settings, as_json=args.json)
    if args.command == "inspect":
        return _cmd_inspect(args, settings)
    if args.command == "mcp-manifest":
        return _cmd_mcp_manifest(args)
    if args.command == "call":
        return _cmd_call(args, settings)
    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
