import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from app import clock
from app.chat.agent import Agent
from app.models import Conversation, Message

router = APIRouter()
PAGE = Path(__file__).resolve().parents[1] / "chat" / "index.html"


class MessageIn(BaseModel):
    content: str


@router.get("/", response_class=HTMLResponse)
def home():
    return PAGE.read_text()


@router.post("/conversations")
def create_conversation(request: Request):
    session = request.app.state.session_factory()
    try:
        conversation = Conversation(created_at=clock.utcnow())
        session.add(conversation)
        session.commit()
        return {"id": conversation.id, "created_at": conversation.created_at.isoformat() + "Z"}
    finally:
        session.close()


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: int, request: Request):
    session = request.app.state.session_factory()
    try:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        ).all()
        return {
            "id": conversation.id,
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "result": json.loads(message.result_json) if message.result_json else None,
                    "created_at": message.created_at.isoformat() + "Z",
                }
                for message in messages
            ],
        }
    finally:
        session.close()


@router.post("/conversations/{conversation_id}/messages")
async def post_message(conversation_id: int, body: MessageIn, request: Request):
    text = body.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="A question is required")
    session = request.app.state.session_factory()
    try:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        session.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=text,
                result_json=None,
                created_at=clock.utcnow(),
            )
        )
        session.flush()
        reply, result = await Agent(request.app.state.model, request.app.state.settings).respond(session, conversation.id)
        session.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=reply,
                result_json=json.dumps(result),
                created_at=clock.utcnow(),
            )
        )
        session.commit()
        return {
            "reply": reply,
            "result": result,
            "explanation_source": request.app.state.model.name,
        }
    finally:
        session.close()
