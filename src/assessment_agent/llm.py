"""OpenAI 兼容模型客户端（文本 + 视觉）。

设计要点：
- 只依赖 ``httpx``，``base_url`` 可指向任意兼容 Chat Completions 的服务；
- 文本模型与视觉模型分开配置（``LLM_TEXT_MODEL`` / ``LLM_VISION_MODEL``）；
- 支持 ``response_format={"type":"json_object"}``，不支持时自动降级并容错解析；
- 失败一律抛 ``LLMError``，调用方决定降级策略，不吞异常也不伪造结果；
- **错误不落响应正文原文**（R4d 收尾，评审建议）：异常字符串只含状态码、
  服务端 error type/code 与脱敏摘要（密钥形态一律打码），避免 4xx/5xx 正文
  经 failure_reason、stderr_tail、报告等诊断链路泄露敏感内容。
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, runtime_checkable

import httpx

from .config import LLMSettings
from .prompts import (
    ROUTER_SYSTEM,
    VERIFIER_SYSTEM,
    VISION_EXTRACT_SYSTEM,
    VISION_PARSE_SYSTEM,
)


# 密钥形态脱敏（sk-*/Bearer/api_key 值等）：诊断信息只允许出现打码后的摘要
_SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za0-9_\-]{6,}|bearer\s+[A-Za-z0-9._\-]+|"
    r"(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[^\s,;\"'}]+)"
)


def redact_secrets(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(0)[:6] + "***REDACTED***", text)


def _safe_error_summary(response: "httpx.Response") -> str:
    """HTTP 错误的脱敏摘要：状态码 + error 协议字段 + ≤120 字符打码正文片段。
    原始正文不落任何持久化路径（异常字符串是 runner/报告唯一的错误出口）。"""
    bits = [f"HTTP {response.status_code}"]
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            for key in ("type", "code"):
                if err.get(key):
                    bits.append(f"{key}={str(err[key])[:60]}")
            message = str(err.get("message") or "")[:200]
        else:
            message = str(data.get("message") or data.get("detail") or data)[:200]
        if message:
            bits.append("msg=" + redact_secrets(message).replace("\n", " ")[:120])
        if data.get("request_id") or data.get("id"):
            bits.append(f"server_id={str(data.get('request_id') or data.get('id'))[:40]}")
    else:
        raw = (response.text or "")[:120]
        if raw:
            bits.append("body=" + redact_secrets(raw).replace("\n", " ")[:120])
    return " | ".join(bits)


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
    """最小可用的 OpenAI 兼容客户端（同步）。

    R4c 审计凭证：每次发起 HTTP 请求（含重试的每次尝试）都记录状态码、错误类型、
    服务端回显模型与用量；进程内不写网络日志文件，由评测/冒烟脚本读取
    `audit_snapshot()` 落盘。**凭证语义**：`requests_sent` 只统计真实发出的
    HTTP 尝试，`requests_succeeded` 只统计拿到 2xx JSON 的响应——EXECUTED 判定
    依据这些计数而非进程退出码。
    """

    def __init__(self, settings: LLMSettings, *, transport: Optional[httpx.BaseTransport] = None):
        self.settings = settings
        self._json_mode_supported = settings.json_mode
        self._transport = transport
        # R2b 用量聚合
        self._call_count = 0
        self._usage_totals: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.last_model: Optional[str] = None
        # R4c 调用审计
        self._requests_sent = 0
        self._requests_succeeded = 0
        self._http_status_counts: Dict[str, int] = {}
        self._error_types: Dict[str, int] = {}
        self._echoed_models: set = set()
        self._request_ids: List[str] = []
        self._transport_is_fake = transport is not None
        self._phase_counts: Dict[str, Dict[str, int]] = {}

    def _phase(self, name: str) -> Dict[str, int]:
        return self._phase_counts.setdefault(
            name, {"http_succeeded": 0, "http_failed": 0, "malformed_envelope": 0,
                   "json_output_valid": 0, "json_output_invalid": 0, "latency_ms_sum": 0.0})

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
            self._requests_sent += 1
            try:
                with httpx.Client(
                    timeout=self.settings.timeout_s,
                    transport=self._transport,
                    follow_redirects=True,
                ) as client:
                    response = client.post(url, headers=self._headers(), json=payload)
            except httpx.RequestError as exc:
                self._error_types[type(exc).__name__] = self._error_types.get(type(exc).__name__, 0) + 1
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(min(4.0, 0.5 * (2**attempt)))
                continue
            if response.status_code >= 500 or response.status_code in (408, 409, 429):
                self._record_status(response, ok=False)
                last_error = LLMError(
                    f"模型服务返回 {response.status_code}: {_safe_error_summary(response)}")
                if attempt + 1 >= attempts:
                    break
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (2**attempt)
                time.sleep(min(8.0, delay))
                continue
            if response.status_code >= 400:
                self._record_status(response, ok=False)
                raise LLMError(
                    f"模型服务返回 {response.status_code}: {_safe_error_summary(response)}")
            self._record_status(response, ok=True)
            try:
                data = response.json()
            except json.JSONDecodeError as exc:  # pragma: no cover - 非 JSON 响应
                self._error_types["NonJsonResponse"] = self._error_types.get("NonJsonResponse", 0) + 1
                # 正文不落原文：只留状态码 + 打码后的短摘要
                raise LLMError(
                    f"模型响应不是 JSON: HTTP {response.status_code} · "
                    f"{redact_secrets((response.text or '')[:120]).replace(chr(10), ' ')}"
                ) from exc
            self._requests_succeeded += 1
            if isinstance(data, dict):
                if data.get("model"):
                    self._echoed_models.add(str(data["model"]))
                rid = (response.headers.get("x-request-id")
                       or response.headers.get("x-dashscope-request-id")
                       or data.get("id"))
                if rid:
                    self._request_ids.append(str(rid))
            return data
        raise LLMError(f"模型调用重试后仍失败: {last_error}")

    # ------------------------------------------------------------------ 对话
    def _classify_phase(self, messages: Sequence[Mapping[str, Any]]) -> str:
        """R4d：按 system prompt 归类请求阶段（vision_parse/router/verifier/
        solver_or_skill）。规则来自 prompts.py 常量的稳定前缀，不依赖调用点传参
        （调用方签名保持不变，避免侵入各 Skill）。"""
        try:
            first = messages[0] if messages else {}
            sys_text = first.get("content") if isinstance(first, dict) else None
            if isinstance(sys_text, list):  # 多段 content
                sys_text = "".join(p.get("text", "") for p in sys_text if isinstance(p, dict))
            sys_text = str(sys_text or "")
        except Exception:  # noqa: BLE001 - 归类失败不影响调用
            return "unknown"
        head = sys_text[:24]
        # 完整 system 前缀匹配优先（防 24 字符巧合）；再退回稳定长前缀。
        for cand, name in ((VISION_PARSE_SYSTEM, "vision_parse"),
                           (VISION_EXTRACT_SYSTEM, "vision_parse"),
                           (ROUTER_SYSTEM, "router"),
                           (VERIFIER_SYSTEM, "verifier")):
            if sys_text.startswith(cand):
                return name
        if head.startswith(VISION_PARSE_SYSTEM[:24]) or head.startswith(VISION_EXTRACT_SYSTEM[:24]):
            return "vision_parse"
        if head.startswith(ROUTER_SYSTEM[:24]):
            return "router"
        if head.startswith(VERIFIER_SYSTEM[:24]):
            return "verifier"
        if "你是" in head or head.startswith("你"):
            return "solver_or_skill"
        return "unknown"

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: Optional[str] = None,
        json_mode: Optional[bool] = None,
        temperature: Optional[float] = None,
    ) -> ChatResponse:
        phase = self._classify_phase(messages)
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
            text = str(exc)
            unsupported = ("response_format" in text or "json_object" in text
                           or "400" in text)
            if want_json and unsupported:
                # 仅对可诊断的 json_object 拒绝原文重试一次；正文本身不落盘
                # （_post 抛出的已是脱敏摘要），但摘要没有 error.code 证据时
                # 不再猜测性重试，避免把真正的鉴权/配额错误当成格式问题。
                if ("response_format" in text or "json_object" in text):
                    self._json_mode_supported = False
                    payload.pop("response_format", None)
                    raw = self._post(payload)
                else:
                    raise
            else:
                self._phase(phase)["http_failed"] += 1
                raise
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            choice = raw["choices"][0]
            content = choice.get("message", {}).get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            self._phase(phase)["malformed_envelope"] += 1
            raise LLMError(f"模型响应结构异常: {str(raw)[:300]}") from exc
        if isinstance(content, list):  # 少数服务返回分段内容
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        response = ChatResponse(
            text=str(content),
            model=str(raw.get("model") or payload["model"]),
            usage=dict(raw.get("usage") or {}),
            latency_ms=latency_ms,
            raw=raw,
        )
        # R2b：聚合真实模型用量（评测时记录 token/调用数；网关未返回 usage 则计数为 0）
        self._call_count += 1
        self.last_model = response.model
        ph = self._phase(phase)
        ph["http_succeeded"] += 1
        ph["latency_ms_sum"] = round(ph["latency_ms_sum"] + latency_ms, 1)
        usage = response.usage or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                self._usage_totals[key] = self._usage_totals.get(key, 0) + int(value)
        return response

    def _record_status(self, response: "httpx.Response", *, ok: bool) -> None:
        key = str(response.status_code) if ok else f"{response.status_code} (error)"
        self._http_status_counts[key] = self._http_status_counts.get(key, 0) + 1
        if not ok:
            kind = ("auth_401_403" if response.status_code in (401, 403)
                    else "not_found_404" if response.status_code == 404
                    else "rate_limit_429" if response.status_code == 429
                    else "server_5xx" if response.status_code >= 500
                    else f"http_{response.status_code}")
            self._error_types[kind] = self._error_types.get(kind, 0) + 1

    def audit_snapshot(self) -> Dict[str, Any]:
        """真实调用审计（EXECUTED 判定的唯一依据；不含密钥）。"""
        return {
            "requests_sent": self._requests_sent,
            "requests_succeeded": self._requests_succeeded,
            "http_status_counts": dict(self._http_status_counts),
            "error_types": dict(self._error_types),
            "echoed_models": sorted(self._echoed_models),
            "request_ids_sample": self._request_ids[:5],
            "base_url_host": (self.settings.base_url.split("//")[-1].split("/")[0]
                              if self.settings.base_url else None),
            "vision_model_configured": self.settings.vision_model,
            "mock_or_injected_transport": bool(self._transport_is_fake),
            "offline_mode": not self.settings.configured,
            # R4d：按请求阶段的审计（vision_parse ≠ 全部 HTTP 成功；
            # requests_succeeded 是总数，不得直接当视觉成功数使用）
            "by_phase": {name: dict(counts) for name, counts in
                         sorted(self._phase_counts.items())},
        }

    def usage_snapshot(self) -> Dict[str, Any]:
        """当前累计用量（评测逐题取差分即可得到单题 token/调用数）。"""
        return {
            "calls": self._call_count,
            "model": self.last_model,
            "prompt_tokens": self._usage_totals.get("prompt_tokens", 0),
            "completion_tokens": self._usage_totals.get("completion_tokens", 0),
            "total_tokens": self._usage_totals.get("total_tokens", 0),
        }

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
        phase = self._classify_phase([{"role": "system", "content": system}])
        try:
            parsed = extract_json_object(response.text)
        except LLMError as exc:
            ph = self._phase(phase)
            ph["json_output_invalid"] += 1
            # R7（承接 R6.1 修正清单）：解析失败时持久化原始输出，
            # 否则协议失败只能看到异常摘要、无法归因（fig-19/20 教训）。
            choices = (response.raw or {}).get("choices") or [{}]
            ph.setdefault("json_invalid_evidence", []).append(
                {"error": str(exc)[:200], "raw": (response.text or "")[:2000],
                 "finish_reason": (choices[0] or {}).get("finish_reason"),
                 "usage": dict(response.usage or {})})
            raise
        self._phase(phase)["json_output_valid"] += 1
        return parsed

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
