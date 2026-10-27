"""Wire models of the pet chat API (docs/接口契约.md §8)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from persona_os.domain.contract import StrictModel

# How the persona feels while saying a line; the pet maps it to an emote.
Emotion = Literal["calm", "happy", "excited", "surprised", "curious", "confused", "thoughtful",
                  "agreeing", "embarrassed", "worried", "sad", "angry", "inspired"]


class ChatSay(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    persona_name: str = Field(min_length=1, max_length=60)
    owner_name: str = Field(default="主人", min_length=1, max_length=60)


class ChatTurn(StrictModel):
    seq: int
    speaker: Literal["owner", "persona"]
    text: str
    at: datetime | None = None
    action_id: str | None = None
    emotion: Emotion | None = None


class ChatReply(StrictModel):
    """Returned as soon as the line is written; its explanation is ready a few seconds later."""

    session_id: str
    reply: ChatTurn


class ChatSession(StrictModel):
    session_id: str
    status: Literal["open", "closed"]
    started_at: datetime
    ended_at: datetime | None = None
    turns: list[ChatTurn]
