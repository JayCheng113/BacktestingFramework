"""V2.7: Chat SSE endpoint — streaming AI assistant responses."""
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
    """Stream chat response via SSE."""
    try:
        provider = create_provider()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"LLM provider unavailable: {e}")

    llm_messages = [LLMMessage(role=m.role, content=m.content) for m in req.messages]

    from ez.agent.assistant import chat_stream

    def generate():
        try:
            for event in chat_stream(provider, llm_messages, editor_code=req.editor_code):
                line = f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                yield line
        except Exception as e:
            logger.error("Chat stream error: %s", e)
            error = f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
            yield error

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/status")
def chat_status():
    """Check if LLM provider is configured."""
    try:
        provider = create_provider()
        return {
            "available": True,
            "provider": provider._provider if hasattr(provider, "_provider") else "unknown",
            "model": provider._model if hasattr(provider, "_model") else "unknown",
        }
    except Exception as e:
        return {"available": False, "error": str(e)}
