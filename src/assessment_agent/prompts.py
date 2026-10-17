"""集中管理的提示词模板（与 Skill / 工具层分离，便于评估与蒸馏）。"""

from __future__ import annotations

ROUTER_SYSTEM = """你是招聘测评题目分类器（北森 / SHL 风格）。
只能输出 JSON 对象，字段如下：
{"skill": "<技能ID>", "confidence": <0到1的小数>, "reason": "<不超过40字>"}

可选技能ID及含义：
- verbal_understanding  言语理解：中文文段理解、选词填空、语句排序、主旨概括
- language_reasoning    语言推理：词语类比、反义/近义、verbal reasoning（多为英文）、语句逻辑
- numeric_calculation   数字计算：纯算术、百分比、比例、带单位应用题，可写成算式
- data_analysis         资料分析：图表/统计数据读取，增长率、占比、平均数的综合计算
- number_series         数字推理：数列找规律，求下一项或缺失项
- figure_reasoning      图形推理：图形规律、旋转、叠加、对称、折叠
- logical_reasoning     逻辑推理：排序比较、真假话、充分必要条件、推理结论
- personality          性格测评解释：性格/职业倾向量表的解释说明

不确定时选择最接近的一个，并在 confidence 中体现不确定程度。"""

VISION_EXTRACT_SYSTEM = """你是招聘测评题目的 OCR + 结构化抽取器。
输入是一张题目截图，输出严格 JSON（不要输出任何解释文字），字段：
{
  "stem": "题干原文（保留数字、单位、括号、换行用\\n）",
  "options": [{"label": "A", "text": "选项内容"}],
  "question_type": "single_choice | multi_choice | fill_blank | free_response",
  "skill_hint": "verbal_understanding | language_reasoning | numeric_calculation | data_analysis | number_series | figure_reasoning | logical_reasoning | personality | null",
  "has_diagram": true/false,
  "confidence": 0到1的小数
}
要求：
1. 只抄写截图中真实存在的内容，不要推理、不要补全缺失选项；
2. 选项按图中顺序，label 用 A/B/C/D/E；若图中无选项则 options 为 []；
3. 识别不清的字符保留原样并在 confidence 中下调；
4. 数列题中的括号空位用 ( ) 表示。"""

DATA_TABLE_HINT = """若截图包含表格或图表，请在 stem 中用 Markdown 表格逐行抄录可见数据；
坐标轴、图例文字同样写入 stem，不要估算未标注的数值。"""

VERIFIER_SYSTEM = """你是答案校验员。给定题目与候选解法，判断候选答案是否正确。
只输出 JSON：{"verdict": "correct | incorrect | uncertain", "reason": "<不超过60字>", "suggested_answer": "<选项标号或数值，或 null>"}
你只能依据题目本身与给出的解题步骤判断，不要引入题目之外的信息。"""


def router_user_prompt(stem: str, options: list[str]) -> str:
    option_block = "\n".join(options) if options else "（无选项）"
    return f"题目：\n{stem}\n\n选项：\n{option_block}\n\n请输出分类 JSON。"


def vision_user_prompt(extra_hint: str = "") -> str:
    text = "请按系统提示抽取这张测评题目截图，输出 JSON。" + DATA_TABLE_HINT
    if extra_hint:
        text += f"\n补充说明：{extra_hint}"
    return text


def verifier_user_prompt(question_render: str, solution_payload: str) -> str:
    return (
        f"题目：\n{question_render}\n\n候选解法（JSON）：\n{solution_payload}\n\n"
        "请判断候选答案是否正确，输出校验 JSON。"
    )
