"""Pet chat (Anima A2): the owner types, the persona answers in a bubble.

A chat session is one engine episode with two participants: the persona (`self`) and the owner
(`owner`, not a persona). Each owner line is an observation; each answer is one action (the
engine's one-call `respond`), shown as soon as the line is written and committed once the rest of
the output is stored. A session ends after
`idle_minutes` without a line, after MAX_OBSERVATIONS lines, or when the pet asks; ending it
closes the episode, which queues the after-episode memory job — the next session recalls it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.chat import contract as ch
from persona_os.domain import contract as c
from persona_os.domain.errors import ServiceError
from persona_os.infra.db import (
    ActionRow,
    EpisodePersonaRow,
    EpisodeRow,
    ObservationRow,
    PersonaRow,
    now,
)

log = logging.getLogger(__name__)

PREFIX = "chat-"  # episode ids of chat sessions
SELF, OWNER = "self", "owner"
MAX_OBSERVATIONS = 40  # a longer chat continues in a new session (the prompt grows per line)
SWEEP_SECONDS = 60
LENGTH = {"unit": "chars", "soft": [6, 60], "hard": [1, 200]}
EMOTIONS = list(ch.Emotion.__args__)
# A free string, so a near miss ("concerned") never costs a redo; mapped back by _emotion.
EXTRA_SCHEMA = {
    "type": "object",
    "properties": {"emotion": {
        "type": "string", "description": "说这句话时的心情，取以下之一：" + "、".join(EMOTIONS)}},
    "required": ["emotion"],
}
SYNONYMS = {
    "neutral": "calm", "relaxed": "calm", "content": "calm", "joyful": "happy",
    "cheerful": "happy", "playful": "happy", "amused": "happy", "glad": "happy",
    "eager": "excited", "shocked": "surprised", "astonished": "surprised",
    "interested": "curious", "puzzled": "confused", "pensive": "thoughtful",
    "reflective": "thoughtful", "approving": "agreeing", "shy": "embarrassed",
    "bashful": "embarrassed", "concerned": "worried", "anxious": "worried",
    "nervous": "worried", "caring": "worried", "upset": "sad", "lonely": "sad",
    "annoyed": "angry", "frustrated": "angry", "proud": "excited", "determined": "excited",
}


def _emotion(raw) -> str | None:
    """The model's word for the mood, as one of the pet's emotions (unknown → calm)."""
    if raw is None:
        return None
    word = str(raw).strip().lower()
    return word if word in EMOTIONS else SYNONYMS.get(word, "calm")


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)  # SQLite drops the zone


def _situation(owner: str) -> c.Situation:
    return c.Situation(
        title=f"和{owner}在电脑桌面上聊天",
        description=(f"{owner}在电脑前打字跟你聊天。你住在{owner}的电脑桌面上，看得到{owner}打的"
                     f"字；你说的话会出现在你头顶的对话气泡里。像当面聊天一样说话：口语、简短，"
                     f"一次一两句。"),
        language="zh")


class Chat:
    def __init__(self, sessions: async_sessionmaker, runner, aftercare,
                 idle_minutes: float) -> None:
        self.sessions = sessions
        self.runner = runner  # Engine
        self.aftercare = aftercare
        self.idle = timedelta(minutes=idle_minutes)
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sweeper: asyncio.Task | None = None
        self._pending: set[asyncio.Task] = set()  # replies shown, still being stored

    def lock(self, ns: str, persona_id: str) -> asyncio.Lock:
        """Held while a line is answered and stored; others wait on it to touch the persona."""
        return self._locks[(ns, persona_id)]

    # ---- talking --------------------------------------------------------------------------------
    async def say(self, ns: str, persona_id: str, body: ch.ChatSay) -> ch.ChatReply:
        """One owner line → one persona line. Lines to the same persona are answered in order.

        The reply returns as soon as the model has written the line; the state, review and
        basis finish in the background and the action is committed then. The persona's lock is
        held until that commit, so the next line, `end` and the sweeper wait for it."""
        lock = self._locks[(ns, persona_id)]
        await lock.acquire()
        try:
            episode_id, last = await self._session_for(ns, persona_id, body)
            seq, at = last + 1, now()
            await self.runner.add_observations(ns, episode_id, c.ObservationBatch(items=[
                c.Observation(seq=seq, kind="utterance", actor=OWNER, content=body.text,
                              addressed_to=[SELF], at=at)]))
        except BaseException:
            lock.release()
            raise
        action_id = f"{episode_id}-{seq}"
        shown: asyncio.Future = asyncio.get_running_loop().create_future()

        async def early(final: c.ActionOutput) -> None:
            if not shown.done():
                shown.set_result((final, now()))

        async def finish() -> None:
            try:
                prepared = await self.runner.prepare(ns, episode_id, c.ActionPrepare(
                    action_id=action_id, participant_id=SELF, upto_seq=seq,
                    task=c.Task(instruction=f"回应{body.owner_name}刚才说的话。"),
                    output_contract=c.OutputContract(length=c.LengthContract(**LENGTH),
                                                     extra_schema=EXTRA_SCHEMA),
                    calls=1), early=early)
                await early(prepared.output)  # no-op when the line was shown early
                _, reply_at = shown.result()
                await self.runner.commit(ns, episode_id, action_id,
                                         c.ActionCommit(seq=seq + 1, at=reply_at))
            except Exception as exc:
                if not shown.done():
                    shown.set_exception(exc)
                else:  # the line is on screen but could not be stored
                    log.exception("chat reply %s shown but not committed", action_id)
            finally:
                lock.release()

        task = asyncio.create_task(finish())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        final, reply_at = await shown
        return ch.ChatReply(session_id=episode_id, reply=ch.ChatTurn(
            seq=seq + 1, speaker="persona", text=final.text, at=reply_at,
            action_id=action_id, emotion=_emotion(final.extra.get("emotion"))))

    async def settled(self) -> None:
        """Wait until replies already shown are stored (tests, shutdown)."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def _session_for(self, ns: str, persona_id: str,
                           body: ch.ChatSay) -> tuple[str, int]:
        """The open session to continue (and its last seq), or a new one."""
        open_id = await self._open_session(ns, persona_id)
        if open_id is not None:
            last, last_at = await self._last(ns, open_id)
            if now() - last_at < self.idle and last + 2 < MAX_OBSERVATIONS:
                return open_id, last
            await self._close(ns, open_id)
        async with self.sessions() as s:
            if await s.get(PersonaRow, (ns, persona_id)) is None:
                raise ServiceError(404, "persona_not_found", persona_id)
        episode_id = f"{PREFIX}{uuid.uuid4().hex[:16]}"
        await self.runner.create_episode(ns, c.EpisodeCreate(
            episode_id=episode_id, situation=_situation(body.owner_name),
            participants=[
                c.Participant(participant_id=SELF, display_name=body.persona_name,
                              persona_id=persona_id),
                c.Participant(participant_id=OWNER, display_name=body.owner_name)]))
        return episode_id, -1

    async def _open_session(self, ns: str, persona_id: str) -> str | None:
        async with self.sessions() as s:
            return await s.scalar(select(EpisodePersonaRow.episode_id).where(
                EpisodePersonaRow.namespace == ns, EpisodePersonaRow.persona_id == persona_id,
                EpisodePersonaRow.open.is_(True),
                EpisodePersonaRow.episode_id.startswith(PREFIX)))

    async def _last(self, ns: str, episode_id: str) -> tuple[int, datetime]:
        async with self.sessions() as s:
            row = await s.scalar(select(ObservationRow).where(
                ObservationRow.namespace == ns, ObservationRow.episode_id == episode_id)
                .order_by(ObservationRow.seq.desc()).limit(1))
            if row is None:
                episode = await s.get(EpisodeRow, (ns, episode_id))
                return -1, _aware(episode.created_at)
        at = row.payload.get("at")
        return row.seq, datetime.fromisoformat(at) if at else now()

    async def _close(self, ns: str, episode_id: str) -> None:
        await self.runner.close_episode(ns, episode_id, c.EpisodeClose())
        self.aftercare.schedule(ns, episode_id)  # memories of this chat

    async def end(self, ns: str, persona_id: str) -> ch.ChatSession | None:
        """End the open session (the pet is quitting, or the owner said goodbye)."""
        async with self._locks[(ns, persona_id)]:
            episode_id = await self._open_session(ns, persona_id)
            if episode_id is None:
                return None
            await self._close(ns, episode_id)
        return (await self._sessions(ns, [episode_id]))[0]

    # ---- idle sessions --------------------------------------------------------------------------
    def start_sweeper(self) -> None:
        self._sweeper = asyncio.create_task(self._sweep_forever())

    async def stop_sweeper(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
        await self.settled()

    async def _sweep_forever(self) -> None:
        while True:
            try:
                await self.sweep()
            except Exception:  # never let the sweeper die
                log.exception("chat sweep failed")
            await asyncio.sleep(SWEEP_SECONDS)

    async def sweep(self) -> int:
        """Close sessions idle for longer than `idle_minutes`, so their memories form even if
        the owner never comes back."""
        async with self.sessions() as s:
            rows = (await s.execute(select(EpisodePersonaRow.namespace,
                                           EpisodePersonaRow.persona_id,
                                           EpisodePersonaRow.episode_id).where(
                EpisodePersonaRow.open.is_(True),
                EpisodePersonaRow.episode_id.startswith(PREFIX)))).all()
        closed = 0
        for ns, persona_id, episode_id in rows:
            async with self._locks[(ns, persona_id)]:
                if await self._open_session(ns, persona_id) != episode_id:
                    continue
                _, last_at = await self._last(ns, episode_id)
                if now() - last_at >= self.idle:
                    await self._close(ns, episode_id)
                    closed += 1
        return closed

    # ---- reading --------------------------------------------------------------------------------
    async def history(self, ns: str, persona_id: str, limit: int) -> list[ch.ChatSession]:
        """Latest sessions first."""
        async with self.sessions() as s:
            if await s.get(PersonaRow, (ns, persona_id)) is None:
                raise ServiceError(404, "persona_not_found", persona_id)
            ids = (await s.scalars(select(EpisodeRow.episode_id).join(
                EpisodePersonaRow, (EpisodePersonaRow.namespace == EpisodeRow.namespace)
                & (EpisodePersonaRow.episode_id == EpisodeRow.episode_id)).where(
                EpisodeRow.namespace == ns, EpisodePersonaRow.persona_id == persona_id,
                EpisodeRow.episode_id.startswith(PREFIX))
                .order_by(EpisodeRow.created_at.desc()).limit(limit))).all()
        return await self._sessions(ns, list(ids))

    async def _sessions(self, ns: str, ids: list[str]) -> list[ch.ChatSession]:
        if not ids:
            return []
        async with self.sessions() as s:
            episodes = {e.episode_id: e for e in (await s.scalars(select(EpisodeRow).where(
                EpisodeRow.namespace == ns, EpisodeRow.episode_id.in_(ids)))).all()}
            observations = (await s.scalars(select(ObservationRow).where(
                ObservationRow.namespace == ns, ObservationRow.episode_id.in_(ids))
                .order_by(ObservationRow.seq))).all()
            extras = dict((await s.execute(select(ActionRow.action_id, ActionRow.output).where(
                ActionRow.namespace == ns, ActionRow.episode_id.in_(ids),
                ActionRow.status == "committed"))).all())
        turns: dict[str, list[ch.ChatTurn]] = defaultdict(list)
        for o in observations:
            p = o.payload
            emotion = (extras.get(o.action_id) or {}).get("extra", {}).get("emotion")
            turns[o.episode_id].append(ch.ChatTurn(
                seq=o.seq, speaker="persona" if p.get("actor") == SELF else "owner",
                text=p["content"], at=p.get("at"), action_id=o.action_id,
                emotion=_emotion(emotion)))
        return [ch.ChatSession(session_id=i, status=episodes[i].status,
                               started_at=_aware(episodes[i].created_at),
                               ended_at=_aware(episodes[i].closed_at)
                               if episodes[i].closed_at else None,
                               turns=turns[i]) for i in ids]
