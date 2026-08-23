"""Conversation history: list, read, rename, delete.

Every handler passes the RepContext down to the service layer, which pairs the
conversation id with the rep's chair_id in the WHERE clause. A conversation
belonging to another rep is indistinguishable from one that does not exist.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ..deps import CurrentRep
from ..services import conversations as service

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


@router.get("")
def list_conversations(rep: CurrentRep) -> list[dict]:
    return service.list_for_rep(rep)


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, rep: CurrentRep) -> dict:
    messages = service.messages_for(rep, conversation_id)
    if messages is None:
        raise _NOT_FOUND
    return {"conversation_id": conversation_id, "messages": messages}


@router.patch("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def rename_conversation(conversation_id: str, payload: RenameRequest, rep: CurrentRep) -> None:
    if not service.rename(rep, conversation_id, payload.title):
        raise _NOT_FOUND


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: str, rep: CurrentRep) -> None:
    if not service.delete(rep, conversation_id):
        raise _NOT_FOUND
