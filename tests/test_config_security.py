"""配置与密钥安全测试。"""

from __future__ import annotations

from pathlib import Path

from assessment_agent.config import PROJECT_ROOT, Settings, parse_dotenv


def test_parse_dotenv_handles_comments_quotes_and_export():
    text = """
    # 注释
    LLM_API_KEY="sk-test"
    export LLM_BASE_URL='http://localhost:8000/v1'
    EMPTY=
    不合法行
    """
    values = parse_dotenv(text)
    assert values["LLM_API_KEY"] == "sk-test"
    assert values["LLM_BASE_URL"] == "http://localhost:8000/v1"
    assert values["EMPTY"] == ""
    assert "不合法行" not in values


def test_settings_default_offline_without_key():
    settings = Settings.from_env(env={})
    assert settings.llm.api_key is None
    assert settings.offline is True
    assert settings.llm_enabled is False


def test_settings_llm_enabled_with_key():
    settings = Settings.from_env(env={"LLM_API_KEY": "sk-test", "LLM_TEXT_MODEL": "m1", "LLM_VISION_MODEL": "v1"})
    assert settings.offline is False
    assert settings.llm_enabled is True
    assert settings.llm.vision_configured is True
    assert settings.llm.chat_completions_url.endswith("/chat/completions")


def test_offline_flag_wins_even_with_key():
    settings = Settings.from_env(env={"LLM_API_KEY": "sk-test", "ASSESSMENT_OFFLINE": "true"})
    assert settings.offline is True
    assert settings.llm_enabled is False


def test_describe_never_exposes_api_key():
    settings = Settings.from_env(env={"LLM_API_KEY": "sk-very-secret-value"})
    described = settings.describe()
    assert "sk-very-secret-value" not in str(described)
    assert described["api_key_present"] is True


def test_env_example_uses_placeholder_only():
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "LLM_API_KEY=" in example
    for line in example.splitlines():
        if line.startswith("LLM_API_KEY="):
            assert "replace-with-your-key" in line
            assert not line.strip().endswith("sk-"), "示例文件不能包含真实密钥"


def test_gitignore_excludes_env_files():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = {line.strip() for line in gitignore.splitlines()}
    assert ".env" in lines
    assert "!.env.example" in lines


def test_no_secret_literals_in_repository_sources():
    """仓库源码/文档/数据中不允许出现形如真实密钥的字符串。"""
    import re

    pattern = re.compile(r"sk-[A-Za-z0-9]{20,}")
    roots = ["src", "tests", "data", "knowledge", "scripts", "README.md", ".env.example"]
    offenders = []
    for relative in roots:
        target = PROJECT_ROOT / relative
        paths = [target] if target.is_file() else list(target.rglob("*")) if target.is_dir() else []
        for path in paths:
            if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".pyc"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if pattern.search(text):
                offenders.append(str(path))
    assert not offenders, f"发现疑似密钥字面量: {offenders}"


def test_settings_paths_are_configurable(tmp_path: Path):
    settings = Settings.from_env(
        env={
            "ASSESSMENT_RUNS_DIR": str(tmp_path / "r"),
            "ASSESSMENT_RESULTS_DIR": str(tmp_path / "res"),
            "ASSESSMENT_DATA_DIR": str(tmp_path / "d"),
        }
    )
    assert settings.runs_dir == tmp_path / "r"
    assert settings.results_dir == tmp_path / "res"
    assert settings.data_dir == tmp_path / "d"
