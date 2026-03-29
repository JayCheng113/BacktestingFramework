"""V2.7: Chat SSE endpoint — streaming AI assistant responses.

V2.7.1: Fully async — uses achat_stream() so LLM HTTP calls don't block
the event loop. chat_status uses public provider properties.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ez.llm.factory import create_provider
from ez.llm.provider import LLMMessage

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    editor_code: str = ""


@router.post("/send")
async def send_message(req: ChatRequest):
    """Stream chat response via SSE (async, non-blocking)."""
    try:
        provider = create_provider()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM provider unavailable: {e}")

    llm_messages = [LLMMessage(role=m.role, content=m.content) for m in req.messages]

    from ez.agent.assistant import achat_stream

    async def generate():
        try:
            async for event in achat_stream(provider, llm_messages, editor_code=req.editor_code):
                line = f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                yield line
        except Exception as e:
            logger.error("Chat stream error: %s", e)
            error = f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
            yield error

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/status")
def chat_status():
    """Check if LLM provider is configured and has credentials."""
    try:
        provider = create_provider()
        prov_name = provider.provider_name
        has_key = provider.has_api_key
        needs_key = prov_name not in ("local",)
        available = has_key or not needs_key
        return {
            "available": available,
            "provider": prov_name,
            "model": provider.model_name,
            **({"error": f"Missing API key for {prov_name}"} if not available else {}),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}
