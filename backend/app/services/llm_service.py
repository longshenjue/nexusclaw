"""
Multi-model LLM abstraction. Currently supports Anthropic Claude and OpenAI-compatible APIs.
"""
from typing import AsyncIterator, Any
import anthropic
import httpx
from app.config import settings
from app.utils.security import decrypt_secret


class LLMMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


class StreamEvent:
    def __init__(self, type: str, **kwargs):
        self.type = type
        self.data = kwargs


async def stream_anthropic(
    messages: list[dict],
    model_id: str,
    api_key: str,
    base_url: str | None,
    system: str | list | None,
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = anthropic.AsyncAnthropic(**client_kwargs)

    kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    # Prompt caching requires the beta header on SDK < 0.50 (still beta in 0.39.x)
    if isinstance(system, list) and any(
        isinstance(b, dict) and "cache_control" in b for b in system
    ):
        kwargs["extra_headers"] = {"anthropic-beta": "prompt-caching-2024-07-31"}

    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            if hasattr(event, "type"):
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield StreamEvent("text_delta", delta=event.delta.text)
                elif event.type == "content_block_start":
                    if hasattr(event.content_block, "type") and event.content_block.type == "tool_use":
                        yield StreamEvent(
                            "tool_start",
                            tool_use_id=event.content_block.id,
                            tool_name=event.content_block.name,
                        )
                elif event.type == "message_delta":
                    if hasattr(event.delta, "stop_reason"):
                        yield StreamEvent("stop", stop_reason=event.delta.stop_reason)
                        if hasattr(event, "usage"):
                            yield StreamEvent("usage", input_tokens=event.usage.input_tokens if hasattr(event.usage, "input_tokens") else 0, output_tokens=event.usage.output_tokens if hasattr(event.usage, "output_tokens") else 0)

        # Get the final message for tool use inputs
        final_message = await stream.get_final_message()
        for block in final_message.content:
            if block.type == "tool_use":
                yield StreamEvent(
                    "tool_input",
                    tool_use_id=block.id,
                    tool_name=block.name,
                    tool_input=block.input,
                )
        yield StreamEvent(
            "message_done",
            input_tokens=final_message.usage.input_tokens,
            output_tokens=final_message.usage.output_tokens,
            stop_reason=final_message.stop_reason,
            content=final_message.content,
        )


async def stream_openai_compatible(
    messages: list[dict],
    model_id: str,
    api_key: str,
    base_url: str,
    system: str | None,
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """OpenAI-compatible streaming (works with OpenAI, DeepSeek, local Ollama, etc.)"""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    all_messages = []
    if system:
        all_messages.append({"role": "system", "content": system})
    all_messages.extend(messages)

    kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": max_tokens,
        "messages": all_messages,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]

    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta:
            if delta.content:
                yield StreamEvent("text_delta", delta=delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    if tc.function:
                        yield StreamEvent(
                            "tool_input",
                            tool_use_id=tc.id or "",
                            tool_name=tc.function.name or "",
                            tool_input=tc.function.arguments or "{}",
                        )
        if chunk.choices and chunk.choices[0].finish_reason:
            yield StreamEvent("stop", stop_reason=chunk.choices[0].finish_reason)
    yield StreamEvent("message_done", input_tokens=0, output_tokens=0, stop_reason="end_turn", content=[])


def get_model_stream(
    provider: str,
    model_id: str,
    api_key_encrypted: str | None,
    base_url: str | None,
    messages: list[dict],
    system: str | list | None = None,
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    api_key = decrypt_secret(api_key_encrypted) if api_key_encrypted else settings.anthropic_api_key

    if provider == "anthropic":
        # Use model-level base_url first, then fall back to global ANTHROPIC_BASE_URL
        effective_base_url = base_url or (settings.anthropic_base_url or None)
        return stream_anthropic(messages, model_id, api_key, effective_base_url, system, max_tokens, tools)
    else:
        # OpenAI compatible
        return stream_openai_compatible(messages, model_id, api_key, base_url or "https://api.openai.com/v1", system, max_tokens, tools)
