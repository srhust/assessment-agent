"""OpenAI 兼容模型客户端（文本 + 视觉）。

设计要点：
- 只依赖 ``httpx``，``base_url`` 可指向任意兼容 Chat Completions 的服务；
- 文本模型与视觉模型分开配置（``LLM_TEXT_MODEL`` / ``LLM_VISION_MODEL``）；
- 支持 ``response_format={"type":"json_object"}``，不支持时自动降级并容错解析；
- 失败一律抛 ``LLMError``，调用方决定降级策略，不吞异常也不伪造结果。
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

import httpx

from .config import LLMSettings


class LLMError(RuntimeError):
    """模型调用失败。"""


class LLMNotConfigured(LLMError):
    """未配置密钥 / 离线模式，禁止发起请求。"""


@dataclass
class ChatResponse:
    text: str
    model: str
    usage: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ChatModel(Protocol):
    """Pipeline 依赖的最小模型协议，便于注入 Fake 客户端做离线测试。"""

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: Optional[str] = None,
        json_mode: Optional[bool] = None,
        temperature: Optional[float] = None,
    ) -> ChatResponse: ...

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]: ...


def extract_json_object(text: str) -> Dict[str, Any]:
    """从模型输出中取出第一个 JSON 对象（容忍 ```json 围栏与前后噪声）。"""
    if not text or not text.strip():
        raise LLMError("模型返回为空，无法解析 JSON")
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        snippet = stripped[start : end + 1]
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError as exc:  # pragma: no cover - 极端异常输出
            raise LLMError(f"模型输出不是合法 JSON: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise LLMError("模型输出中未找到 JSON 对象")


def image_to_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"题图不存在: {path}")
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(
        suffix, "png"
    )
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{payload}"


class OpenAICompatibleClient:
    """最小可用的 OpenAI 兼容客户端（同步）。"""

    def __init__(self, settings: LLMSettings, *, transport: Optional[httpx.BaseTransport] = None):
        self.settings = settings
        self._json_mode_supported = settings.json_mode
        self._transport = transport

    # ------------------------------------------------------------------ 基础
    def _headers(self) -> Dict[str, str]:
        if not self.settings.configured:
            raise LLMNotConfigured("未配置 LLM_API_KEY，无法调用模型（可设置 ASSESSMENT_OFFLINE=1 使用纯确定性流程）")
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        if self.settings.organization:
            headers["OpenAI-Organization"] = self.settings.organization
        return headers

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = self.settings.chat_completions_url
        last_error: Optional[Exception] = None
        attempts = max(0, self.settings.max_retries) + 1
        for attempt in range(attempts):
            try:
                with httpx.Client(
                    timeout=self.settings.timeout_s,
                    transport=self._transport,
                    follow_redirects=True,
                ) as client:
                    response = client.post(url, headers=self._headers(), json=payload)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(min(4.0, 0.5 * (2**attempt)))
                continue
            if response.status_code >= 500 or response.status_code in (408, 409, 429):
                last_error = LLMError(f"模型服务返回 {response.status_code}: {response.text[:300]}")
                if attempt + 1 >= attempts:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2**attempt)
                time.sleep(min(8.0, delay))
                continue
            if response.status_code >= 400:
                raise LLMError(f"模型服务返回 {response.status_code}: {response.text[:300]}")
            try:
                return response.json()
            except json.JSONDecodeError as exc:  # pragma: no cover - 非 JSON 响应
                raise LLMError(f"模型响应不是 JSON: {exc}") from exc
        raise LLMError(f"模型调用重试后仍失败: {last_error}")

    # ------------------------------------------------------------------ 对话
    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: Optional[str] = None,
        json_mode: Optional[bool] = None,
        temperature: Optional[float] = None,
    ) -> ChatResponse:
        payload: Dict[str, Any] = {
            "model": model or self.settings.text_model,
            "messages": list(messages),
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_tokens,
        }
        want_json = self._json_mode_supported if json_mode is None else json_mode
        if want_json:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        try:
            raw = self._post(payload)
        except LLMError as exc:
            unsupported = "response_format" in str(exc) or "json_object" in str(exc)
            if want_json and unsupported:
                self._json_mode_supported = False
                payload.pop("response_format", None)
                raw = self._post(payload)
            else:
                raise
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            choice = raw["choices"][0]
            content = choice.get("message", {}).get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"模型响应结构异常: {str(raw)[:300]}") from exc
        if isinstance(content, list):  # 少数服务返回分段内容
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return ChatResponse(
            text=str(content),
            model=str(raw.get("model") or payload["model"]),
            usage=dict(raw.get("usage") or {}),
            latency_ms=latency_ms,
            raw=raw,
        )

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        image_path: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        content: Any = user
        if image_path:
            content = [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": image_to_data_url(image_path)}},
            ]
        response = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            model=model,
        )
        return extract_json_object(response.text)

    def close(self) -> None:  # 便于上下文管理器使用
        return None

    def __enter__(self) -> "OpenAICompatibleClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def build_client(settings_llm: LLMSettings, *, transport: Optional[httpx.BaseTransport] = None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(settings_llm, transport=transport)


def _smoke_test() -> List[str]:  # 供 doctor 使用
    checks: List[str] = []
    for name in ("httpx", "sympy"):
        try:
            __import__(name)
            checks.append(f"{name}: ok")
        except ImportError:  # pragma: no cover
            checks.append(f"{name}: missing")
    return checks
