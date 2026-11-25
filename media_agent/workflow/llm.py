"""LLM 统一入口。

三个层次：
- ``get_model``      构造 ChatOpenAI（温度/超时统一）；
- ``safe_llm_call``  纯文本调用，失败返回兜底串，**不抛异常**；
- ``call_json``      结构化输出（JSON mode + Pydantic 校验 + 1 次自修复重试），失败抛 ``LLMError``。
"""
from __future__ import annotations

import logging
import re
from typing import Type, TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MISSING_KEY_HINT = (
    "未配置 DEEPSEEK_API_KEY：请复制 .env.example 为 .env，填入"
    "`DEEPSEEK_API_KEY=sk-...` 后重启应用。"
)


class LLMError(RuntimeError):
    """LLM 调用失败或结构化输出无法解析。"""


def get_model(temperature: float = 0.5, max_tokens: int | None = None) -> ChatOpenAI:
    """构造统一配置的 ChatOpenAI 客户端。"""
    return ChatOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=settings.llm_timeout,
        max_retries=1,
    )


def strip_code_fence(text: str) -> str:
    """去掉模型偶尔包上的 ```json ``` 围栏。"""
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def safe_llm_call(model: ChatOpenAI, prompt: str, fallback: str = "") -> str:
    """纯文本调用，任何异常都吞掉并返回兜底串。"""
    try:
        response = model.invoke([HumanMessage(content=prompt)])
        content = response.content
        return content if isinstance(content, str) else str(content)
    except Exception as exc:  # noqa: BLE001 - 兜底层就是要吞异常
        log.warning("LLM 调用失败：%s", exc)
        return fallback or f"[LLM 调用失败：{exc}]"


def _summarize_validation_error(exc: ValidationError, limit: int = 4) -> str:
    details, _ = exc.errors(), None
    lines = []
    for item in details[:limit]:
        loc = ".".join(str(p) for p in item.get("loc", ()))
        lines.append(f"- {loc or '<root>'}: {item.get('msg')}")
    return "\n".join(lines) or str(exc)


def call_json(
    model: ChatOpenAI,
    system: str,
    user: str,
    schema: Type[T],
    retries: int = 1,
) -> T:
    """让模型输出 JSON 并解析成 Pydantic 对象；校验失败把错误回灌重试一次。"""
    if not settings.api_key_ok:
        raise LLMError(MISSING_KEY_HINT)

    messages: list = [SystemMessage(content=system), HumanMessage(content=user)]
    repair_hint = (
        "你上一次的输出没有通过 JSON Schema 校验，问题如下：\n{err}\n"
        "请只输出一个严格合法的 JSON 对象（不要 markdown 代码块、不要解释），"
        "字段名、类型、取值必须与要求完全一致。"
    )
    last_error = ""

    for attempt in range(retries + 1):
        raw = ""
        try:
            response = model.bind(response_format={"type": "json_object"}).invoke(messages)
            raw = response.content if isinstance(response.content, str) else str(response.content)
            return schema.model_validate_json(strip_code_fence(raw))
        except ValidationError as exc:
            last_error = _summarize_validation_error(exc)
        except ValueError as exc:  # 包含 json.JSONDecodeError
            last_error = f"不是合法 JSON：{exc}"
        except Exception as exc:  # noqa: BLE001 - 网络/鉴权/限流
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= retries:
                raise LLMError(f"模型调用失败：{last_error}") from exc

        log.warning("第 %d 次结构化输出失败：%s", attempt + 1, last_error)
        if attempt < retries:
            messages = messages + [
                AIMessage(content=raw or "(空)"),
                HumanMessage(content=repair_hint.format(err=last_error)),
            ]

    raise LLMError(f"结构化输出解析失败：{last_error}")


def call_json_safe(model: ChatOpenAI, system: str, user: str, schema: Type[T]) -> T | None:
    """call_json 的兜底版本，失败返回 None。"""
    try:
        return call_json(model, system, user, schema)
    except LLMError as exc:
        log.warning("call_json_safe 放弃：%s", exc)
        return None


def llm_call(prompt: str, temperature: float = 0.5, max_tokens: int | None = None) -> str:
    """文本进、文本出的一次性调用（工作流里的简写）。

    等价于 ``safe_llm_call(get_model(temperature, max_tokens), prompt)``，
    失败不抛异常、返回带错误原因的字符串，保证节点能把流程跑完。
    """
    return safe_llm_call(get_model(temperature=temperature, max_tokens=max_tokens), prompt)
