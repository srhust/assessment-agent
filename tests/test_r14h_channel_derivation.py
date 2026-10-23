"""R14-H 通道注解**由证据导出**的规则本身要有测试。

背景（本轮实测）：`R14H_KB_CHANNEL_AUDIT.json` 里的
`state_offline_replay` / `channel_correction_addendum` 是我**手写**的，
脚本不产出 ⇒ 重跑必丢 ⇒ 复现核对只能记 `semantic_drift`。
现改为由 `R13F_PLANNER_LAYERS.json` 的 `rows[].ast_expr` 导出。
导出规则成了新逻辑，**新逻辑必须有独立测试**，
否则"看起来接上了数据源"这句话本身没有凭据
（上一版我就错在把数据源指到了只含 `ft-*` 的回放件上，
两页全落 `no_offline_replay_record` 却没人发现）。

本测试**不读盘、不联网**：直接喂合成行，钉住三条边界。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from h1_r14_kb_channel_audit import classify_channel_row  # noqa: E402

DET = "deterministic_python_math_main_chain"
LLM = "llm_planner_required_offline_not_reproducible"


def test_ast_expr_present_is_deterministic():
    assert classify_channel_row({"ast_expr": "(1+2)*3**2-4/2"}) == DET


def test_ast_expr_null_is_llm_planner():
    # r10g-08 的真实形态：retrieval_state 写着 direct_ast，但 ast_expr=null
    row = {"ast_expr": None, "retrieval_state": "invoked_empty_direct_ast"}
    assert classify_channel_row(row) == LLM


def test_missing_key_is_not_silently_deterministic():
    assert classify_channel_row({}) == LLM
    assert classify_channel_row({"ast_expr": ""}) == LLM


def test_retrieval_state_alone_never_decides():
    # 只看 ast_expr：把 retrieval_state 换成任何值都不许改变结论
    for st in ("invoked_empty_direct_ast", "invoked_with_results",
               "not_invoked", None):
        assert classify_channel_row({"ast_expr": None,
                                     "retrieval_state": st}) == LLM
        assert classify_channel_row({"ast_expr": "1+1",
                                     "retrieval_state": st}) == DET
