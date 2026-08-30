import json
from typing import Any, AsyncGenerator, AsyncIterable

from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from loguru import logger
from pydantic import BaseModel


class PromptInput(BaseModel):
    prompt: str
    model_name: str


class Message(BaseModel):
    role: str
    content: str


class ChatStreamResponse(StreamingResponse):
    """
    It processes the LangGraph stream in different stream modes ("updates", "messages") and formats the data
    into JSON objects before sending them to the client.
    """

    def __init__(self, astream: AsyncIterable[dict[str, Any | Any]], **kwargs):
        kwargs.setdefault("media_type", "application/x-ndjson")
        super().__init__(content=self.process_stream(astream), **kwargs)

    async def process_stream(self, astream: AsyncIterable[dict[str, Any | Any]]) -> AsyncGenerator[str, Any]:
        try:
            async for stream_mode, chunk in astream:
                if stream_mode == "messages":
                    yield self._handle_messages_stream(chunk)  # type: ignore

                elif stream_mode == "updates":
                    async for formatted_chunk in self._handle_updates_stream(chunk):  # type: ignore
                        yield formatted_chunk
        except Exception as exc:
            logger.exception("Chat stream failed: {}", exc)
            yield json.dumps({"type": "error", "content": "生成回答失败，请检查模型配置后重试。"}) + "\n"

    def _handle_messages_stream(self, chunk: tuple[AIMessageChunk | AIMessage, Any]) -> str:
        message = chunk[0]
        if isinstance(message, (AIMessageChunk, AIMessage)) and message.content:
            response = {"type": "llm_chunk", "content": str(message.content)}
            return json.dumps(response) + "\n"
        return ""

    async def _handle_updates_stream(self, chunk: dict[str, Any]) -> AsyncGenerator[str, Any]:
        for node_output in chunk.values():
            if "messages" not in node_output:
                continue

            message = node_output["messages"][-1]

            if isinstance(message, AIMessage):
                if message.tool_calls:
                    for tool_call in message.tool_calls:
                        response = {"type": "tool_call", "name": tool_call["name"], "args": tool_call["args"]}
                        yield json.dumps(response) + "\n"

            elif isinstance(message, ToolMessage):
                response = {"type": "tool_result", "name": message.name, "content": message.content}
                yield json.dumps(response) + "\n"

            else:
                response = {"type": message.type, "content": message.content}
                logger.debug(f"Unknown message type: {message.type} - {json.dumps(response)}")
