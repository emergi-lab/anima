"""Owner feedback (contract §4): taxonomies, one judgment per committed action, completion.

Feedback can be changed until the owner completes the episode; completion locks it and is what
lets reflection start (S4).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from persona_os.domain import contract as c
from persona_os.domain.errors import ServiceError
from persona_os.infra.db import (
    ActionRow,
    EpisodePersonaRow,
    FeedbackCompletionRow,
    FeedbackRow,
    FeedbackTaxonomyRow,
    now,
)


def _item(row: FeedbackRow) -> c.FeedbackCreate:
    return c.FeedbackCreate(feedback_id=row.feedback_id, persona_id=row.persona_id,
                            action_id=row.action_id, taxonomy=row.taxonomy,
                            judgment=row.judgment, reason_code=row.reason_code, note=row.note,
                            submitted_at=row.submitted_at)


class FeedbackStore:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self.sessions = sessions

    async def put_taxonomy(self, ns: str, name: str,
                           body: c.FeedbackTaxonomy) -> c.FeedbackTaxonomy:
        codes = [r.code for r in body.reasons]
        if len(codes) != len(set(codes)) or len(body.judgments) != len(set(body.judgments)):
            raise ServiceError(422, "invalid_taxonomy", "judgments and reason codes must be unique")
        async with self.sessions() as s, s.begin():
            row = await s.get(FeedbackTaxonomyRow, (ns, name))
            if row is None:
                s.add(FeedbackTaxonomyRow(namespace=ns, name=name,
                                          body=body.model_dump(mode="json")))
            else:
                row.body, row.updated_at = body.model_dump(mode="json"), now()
        return body

    async def add(self, ns: str, body: c.FeedbackCreate) -> c.FeedbackCreate:
        async with self.sessions() as s, s.begin():
            taxonomy = await s.get(FeedbackTaxonomyRow, (ns, body.taxonomy))
            if taxonomy is None:
                raise ServiceError(404, "taxonomy_not_found", body.taxonomy)
            spec = c.FeedbackTaxonomy.model_validate(taxonomy.body)
            if body.judgment not in spec.judgments:
                raise ServiceError(422, "invalid_feedback", f"unknown judgment {body.judgment}")
            reason = next((r for r in spec.reasons if r.code == body.reason_code), None)
            if body.reason_code is not None and reason is None:
                raise ServiceError(422, "invalid_feedback", f"unknown reason {body.reason_code}")
            action = await s.get(ActionRow, (ns, body.action_id))
            if action is None or action.status != "committed" or \
                    action.persona_id != body.persona_id:
                raise ServiceError(404, "action_not_found",
                                   f"{body.action_id} is not a committed action of "
                                   f"{body.persona_id}")
            if await s.get(FeedbackCompletionRow, (ns, action.episode_id, body.persona_id)):
                raise ServiceError(409, "feedback_closed",
                                   "feedback on this episode was completed and is locked")
            row = await s.get(FeedbackRow, (ns, body.feedback_id))
            other = await s.scalar(select(FeedbackRow).where(
                FeedbackRow.namespace == ns, FeedbackRow.action_id == body.action_id))
            if other is not None and other.feedback_id != body.feedback_id:
                raise ServiceError(409, "feedback_conflict",
                                   f"{body.action_id} already has feedback {other.feedback_id}")
            if row is not None and (row.action_id, row.persona_id) != \
                    (body.action_id, body.persona_id):
                raise ServiceError(409, "feedback_conflict",
                                   f"{body.feedback_id} belongs to another action")
            row = row or FeedbackRow(namespace=ns, feedback_id=body.feedback_id,
                                     persona_id=body.persona_id, episode_id=action.episode_id,
                                     action_id=body.action_id)
            # Before completion the owner may change their mind: the latest judgment counts.
            row.taxonomy, row.judgment, row.reason_code = body.taxonomy, body.judgment, \
                body.reason_code
            row.target = reason.target if reason else None
            row.note, row.submitted_at, row.updated_at = body.note, body.submitted_at, now()
            s.add(row)
        return _item(row)

    async def episode(self, ns: str, episode_id: str, persona_id: str) -> c.EpisodeFeedback:
        async with self.sessions() as s:
            await self._link(s, ns, episode_id, persona_id)
            done = await s.get(FeedbackCompletionRow, (ns, episode_id, persona_id))
            rows = (await s.scalars(select(FeedbackRow).where(
                FeedbackRow.namespace == ns, FeedbackRow.episode_id == episode_id,
                FeedbackRow.persona_id == persona_id))).all()
        return c.EpisodeFeedback(episode_id=episode_id, persona_id=persona_id,
                                 completed_at=done.completed_at if done else None,
                                 items=[_item(r) for r in rows])

    async def complete(self, ns: str, episode_id: str, persona_id: str) -> tuple[bool, str]:
        """Returns (newly completed, status). Completing twice is harmless."""
        async with self.sessions() as s, s.begin():
            await self._link(s, ns, episode_id, persona_id)
            if await s.get(FeedbackCompletionRow, (ns, episode_id, persona_id)):
                return False, "completed"
            s.add(FeedbackCompletionRow(namespace=ns, episode_id=episode_id,
                                        persona_id=persona_id))
        return True, "completed"

    @staticmethod
    async def _link(s, ns: str, episode_id: str, persona_id: str) -> None:
        if await s.get(EpisodePersonaRow, (ns, episode_id, persona_id)) is None:
            raise ServiceError(404, "not_in_episode",
                               f"{persona_id} did not take part in {episode_id}")
