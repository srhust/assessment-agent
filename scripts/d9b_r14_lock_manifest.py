"""把 manifest 里未锁定的项（模型名、Prompt 源哈希、页数上限）**如实暴露**，
而不是留成 null 让闸门评审去猜。零请求。"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assessment_agent import prompts as P                        # noqa: E402
from assessment_agent.config import Settings                      # noqa: E402

PLAN = ROOT / "evidence" / "R14G_PLAN.json"
plan = json.loads(PLAN.read_text(encoding="utf-8"))

pp = ROOT / "src" / "assessment_agent" / "prompts.py"
prompt_sha = hashlib.sha256(pp.read_bytes()).hexdigest()

# 当前环境里模型名的**实况**（不使用任何 key，只读配置）
st = Settings.from_env(env={"ASSESSMENT_OFFLINE": "1"})
env_model = st.llm.vision_model
key_present = bool(st.llm.api_key)

plan["fixed"].update({
    "prompt_source": "src/assessment_agent/prompts.py",
    "prompt_sha256": prompt_sha,
    "vision_model_in_config": env_model,
    "api_key_present_locally": key_present,
})
plan["model_lock"] = {
    "status": "UNRESOLVED_MUST_BE_CONFIRMED_BY_HUMAN" if not env_model
    else "FROM_LOCAL_CONFIG",
    "rule": "G 批发起前模型名必须与工单/人工确认一致并写回本 manifest；"
            "本地无配置时**禁止**由脚本自选模型。",
    "vision_model": env_model,
}
plan["budget"]["max_pages_planned"] = len(plan["planned_calls"])
plan["budget"]["retry_headroom_note"] = (
    "计划 5 页 + 余量 1 次请求 = 上限 6；一旦发生 **2 页**重试即触顶，"
    "第 6 次之后的请求会被原子闸拒绝——缺的那一页必须记为 "
    "BudgetExhausted 并计入端到端分母，不得降低页数或放松闸来抹掉。"
    "（G0 C1d 已实测该行为）")
PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"prompt_sha256": prompt_sha,
                  "vision_model": env_model,
                  "api_key_present_locally": key_present,
                  "model_lock": plan["model_lock"]["status"],
                  "planned": len(plan["planned_calls"])},
                 ensure_ascii=False, indent=1))
