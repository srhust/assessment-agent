"""配置：从环境变量 / .env 读取，不在代码中保存任何密钥。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, Optional

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_dotenv(text: str) -> Dict[str, str]:
    """解析极简 .env 文本（KEY=VALUE，支持 # 注释、export 前缀、引号包裹）。"""
    values: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_dotenv(path: Optional[Path]) -> Dict[str, str]:
    if path is None:
        return {}
    dotenv_path = Path(path)
    if not dotenv_path.is_file():
        return {}
    return parse_dotenv(dotenv_path.read_text(encoding="utf-8"))


def _as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Optional[str], default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_int(value: Optional[str], default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_path(value: Optional[str], default: Path) -> Path:
    text = _as_optional(value)
    return Path(text).expanduser() if text else default


class LLMSettings(BaseModel):
    """OpenAI 兼容接口配置：文本模型与视觉模型分开指定。"""

    base_url: str = "https://api.openai.com/v1"
    api_key: Optional[str] = None
    text_model: str = "gpt-4o-mini"
    vision_model: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_s: float = 60.0
    max_retries: int = 2
    json_mode: bool = True
    organization: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def vision_configured(self) -> bool:
        return bool(self.api_key and self.vision_model)

    @property
    def chat_completions_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


class Settings(BaseModel):
    """全局设置。``offline=True`` 时不会发起任何模型请求。"""

    llm: LLMSettings = Field(default_factory=LLMSettings)
    offline: bool = True
    runs_dir: Path = PROJECT_ROOT / "runs"
    results_dir: Path = PROJECT_ROOT / "results"
    data_dir: Path = PROJECT_ROOT / "data"
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"
    router_rule_threshold: float = 1.0
    verifier_use_llm: bool = False
    verifier_min_confidence: float = 0.5
    vision_parser: bool = True  # True：截图走区域化 VisionParser（schema v2）；False：旧版纯文本抽取
    # R2c 消融开关（env ASSESSMENT_R2C_DISABLE 逗号分隔：word_problem / figure_verify /
    # router_structure / data_analysis）。默认全部启用；消融脚本按臂关闭，量化每类改进的独立贡献。
    r2c_word_problem: bool = True  # 自然语言数量关系理解层（numeric_calculation 前置）
    r2c_figure_verify: bool = True  # 图形候选代入独立复核（与 Solver 不共享最终规则决策）
    r2c_router_structure: bool = True  # Router 结构证据优先（图形裁剪/表格区域压过关键词冲突）
    r3a_data_analysis: bool = True  # R3a 资料分析 Skill 开关（关闭时回退 declared 接口=弃权）
    r3b_verbal: bool = True         # R3b 言语理解 Skill 开关（同上，供消融）

    @property
    def llm_enabled(self) -> bool:
        return (not self.offline) and self.llm.configured

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        dotenv_path: Optional[Path] = None,
    ) -> "Settings":
        """构造设置。

        - ``env`` 为 None 时读取 ``os.environ``，并叠加 ``.env``（真实环境变量优先）。
        - ``env`` 被显式传入时只使用该映射（测试用，避免受本机环境影响）。
        """
        if env is None:
            dotenv = load_dotenv(PROJECT_ROOT / ".env" if dotenv_path is None else dotenv_path)
            merged: Mapping[str, str] = {**dotenv, **os.environ}
        else:
            merged = env

        api_key = _as_optional(merged.get("LLM_API_KEY") or merged.get("OPENAI_API_KEY"))
        llm = LLMSettings(
            base_url=_as_optional(merged.get("LLM_BASE_URL"))
            or _as_optional(merged.get("OPENAI_BASE_URL"))
            or "https://api.openai.com/v1",
            api_key=api_key,
            text_model=_as_optional(merged.get("LLM_TEXT_MODEL"))
            or _as_optional(merged.get("OPENAI_MODEL"))
            or "gpt-4o-mini",
            vision_model=_as_optional(merged.get("LLM_VISION_MODEL")),
            temperature=_as_float(merged.get("LLM_TEMPERATURE"), 0.0),
            max_tokens=_as_int(merged.get("LLM_MAX_TOKENS"), 2048),
            timeout_s=_as_float(merged.get("LLM_TIMEOUT_S"), 60.0),
            max_retries=_as_int(merged.get("LLM_MAX_RETRIES"), 2),
            json_mode=_as_bool(merged.get("LLM_JSON_MODE"), True),
            organization=_as_optional(merged.get("LLM_ORGANIZATION")),
        )
        offline = _as_bool(merged.get("ASSESSMENT_OFFLINE"), default=not bool(api_key))
        disabled = {
            item.strip()
            for item in (merged.get("ASSESSMENT_R2C_DISABLE") or "").split(",")
            if item.strip()
        }
        return cls(
            llm=llm,
            offline=offline,
            runs_dir=_as_path(merged.get("ASSESSMENT_RUNS_DIR"), PROJECT_ROOT / "runs"),
            results_dir=_as_path(merged.get("ASSESSMENT_RESULTS_DIR"), PROJECT_ROOT / "results"),
            data_dir=_as_path(merged.get("ASSESSMENT_DATA_DIR"), PROJECT_ROOT / "data"),
            knowledge_dir=_as_path(merged.get("ASSESSMENT_KNOWLEDGE_DIR"), PROJECT_ROOT / "knowledge"),
            router_rule_threshold=_as_float(merged.get("ROUTER_RULE_THRESHOLD"), 1.0),
            verifier_use_llm=_as_bool(merged.get("VERIFIER_USE_LLM"), False),
            verifier_min_confidence=_as_float(merged.get("VERIFIER_MIN_CONFIDENCE"), 0.5),
            vision_parser=_as_bool(merged.get("VISION_PARSER"), True),
            r2c_word_problem="word_problem" not in disabled,
            r2c_figure_verify="figure_verify" not in disabled,
            r2c_router_structure="router_structure" not in disabled,
            r3a_data_analysis="data_analysis" not in disabled,
            r3b_verbal="verbal_understanding" not in disabled,
        )

    def describe(self) -> Dict[str, object]:
        """不含密钥的可打印摘要（用于 doctor / 评测报告）。"""
        return {
            "offline": self.offline,
            "llm_enabled": self.llm_enabled,
            "base_url": self.llm.base_url,
            "text_model": self.llm.text_model,
            "vision_model": self.llm.vision_model,
            "api_key_present": self.llm.configured,
            "runs_dir": str(self.runs_dir),
            "results_dir": str(self.results_dir),
            "data_dir": str(self.data_dir),
            "vision_parser": self.vision_parser,
            "r2c_flags": {
                "word_problem": self.r2c_word_problem,
                "figure_verify": self.r2c_figure_verify,
                "router_structure": self.r2c_router_structure,
                "data_analysis": self.r3a_data_analysis,
                "verbal_understanding": self.r3b_verbal,
            },
        }
