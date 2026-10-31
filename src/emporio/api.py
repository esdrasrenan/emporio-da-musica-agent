"""HTTP API - the integration point for a messaging channel such as a WhatsApp
Business webhook.

    uvicorn emporio.api:app --reload

``customer_phone`` is trusted: it must come from the channel integration (the
sender's number), never from the end user. Do not expose this API publicly
without authentication in front of it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from emporio import __version__
from emporio.agent import Agent


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(None, description="Omit to start a new conversation.")
    customer_phone: str | None = Field(None, description="Sender id from the channel (trusted).")


class ToolCallOut(BaseModel):
    name: str
    input: dict
    is_error: bool


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[ToolCallOut]
    latency_ms: int


def create_app(agent: Agent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if agent is None:
            from emporio.app import build_agent

            app.state.agent = build_agent()
        else:
            app.state.agent = agent
        yield

    app = FastAPI(title="Empório da Música - virtual assistant", version=__version__, lifespan=lifespan)

    @app.get("/healthz")
    def healthz(request: Request) -> dict:
        model = request.app.state.agent.model
        return {"status": "ok", "model": f"{model.provider}:{model.model}"}

    @app.post("/v1/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, request: Request) -> ChatResponse:
        agent: Agent = request.app.state.agent
        if body.session_id and agent.storage.get_session(body.session_id) is None:
            raise HTTPException(404, "Unknown session_id")
        session = body.session_id or agent.start_session(customer_phone=body.customer_phone)
        reply = agent.reply(session, body.message)
        return ChatResponse(
            session_id=session,
            reply=reply.text,
            tool_calls=[
                ToolCallOut(name=c["name"], input=c["input"], is_error=c["is_error"])
                for c in reply.tool_calls
            ],
            latency_ms=reply.latency_ms,
        )

    return app


app = create_app()
