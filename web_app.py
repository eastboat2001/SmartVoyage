from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import Config
from create_logger import logger
from utils.fastapi_middleware import install_common_middleware
from utils.orchestrator import SmartVoyageOrchestrator


ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
SESSION_COOKIE = "smartvoyage_session"


@dataclass
class WebSessionState:
    messages: list[dict[str, str]] = field(default_factory=list)
    conversation_history: str = ""
    pending_order_context: dict[str, Any] = field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, WebSessionState] = {}

    def get(self, session_id: str) -> WebSessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = WebSessionState()
        return self._sessions[session_id]

    def reset(self, session_id: str) -> WebSessionState:
        self._sessions[session_id] = WebSessionState()
        return self._sessions[session_id]


conf = Config()
orchestrator = SmartVoyageOrchestrator(conf)
session_store = SessionStore()

app = FastAPI(title="SmartVoyage Web")
install_common_middleware(app)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def ensure_session_id(request: Request) -> str:
    session_id = request.cookies.get(SESSION_COOKIE, "").strip()
    return session_id or uuid.uuid4().hex


def serialize_pending_context(pending_context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(pending_context, dict):
        return {}
    return pending_context


def build_agent_cards() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for agent_name, metadata in orchestrator.agent_metadata.items():
        cards.append(
            {
                "name": agent_name,
                "description": metadata.get("description", ""),
                "url": metadata.get("url", ""),
                "skills": metadata.get("skills", []),
            }
        )
    return cards


def process_chat_turn(session: WebSessionState, prompt: str) -> dict[str, Any]:
    session.messages.append({"role": "user", "content": prompt})
    session.conversation_history += f"\nUser: {prompt}"

    result = orchestrator.process_user_input(
        prompt,
        session.conversation_history,
        session.pending_order_context,
    )
    response = result["response"]
    session.pending_order_context = result.get("pending_order_context", {}) or {}
    session.conversation_history += f"\nAssistant: {response}"
    session.messages.append({"role": "assistant", "content": response})

    if result.get("routed_agents"):
        logger.info(f"Web 路由到代理：{result['routed_agents']}")

    pending = serialize_pending_context(session.pending_order_context)
    review_payload = pending.get("review_payload", {}) if pending.get("action") == "hitl_review" else {}

    return {
        "response": response,
        "messages": session.messages,
        "routed_agents": result.get("routed_agents", []),
        "intents": result.get("intents", []),
        "pending_order_context": pending,
        "hitl_pending": pending.get("action") == "hitl_review",
        "review_payload": review_payload,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    session_id = ensure_session_id(request)
    session_store.get(session_id)
    response = TEMPLATES.TemplateResponse(
        "index.html",
        {
            "request": request,
            "username": conf.default_username,
            "agent_cards": build_agent_cards(),
            "session_id": session_id,
        },
    )
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "service": "SmartVoyageWeb"}


@app.get("/api/bootstrap")
async def bootstrap(request: Request):
    session_id = ensure_session_id(request)
    session = session_store.get(session_id)
    return {
        "username": conf.default_username,
        "messages": session.messages,
        "agent_cards": build_agent_cards(),
        "pending_order_context": serialize_pending_context(session.pending_order_context),
        "hitl_pending": session.pending_order_context.get("action") == "hitl_review",
        "review_payload": session.pending_order_context.get("review_payload", {}),
    }


@app.post("/api/chat")
async def chat(request: Request, payload: ChatRequest):
    session_id = ensure_session_id(request)
    session = session_store.get(session_id)
    result = await asyncio.to_thread(process_chat_turn, session, payload.message.strip())
    response = JSONResponse(result)
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return response


@app.post("/api/reset")
async def reset(request: Request):
    session_id = ensure_session_id(request)
    session_store.reset(session_id)
    response = JSONResponse({"ok": True, "messages": []})
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return response


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8501, reload=False)
