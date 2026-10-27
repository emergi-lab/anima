"""HTTP surface of docs/接口契约.md. Routes not built yet answer 501 with their stage."""

# No postponed annotations here: FastAPI must resolve the local `Ns` dependency alias.
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from persona_os.application.aftercare import Aftercare, queue_reflect
from persona_os.application.construction import PersonaStore
from persona_os.application.engine import Engine
from persona_os.application.evolution import Evolution
from persona_os.application.feedback import FeedbackStore
from persona_os.chat import contract as ch
from persona_os.chat.service import Chat
from persona_os.config import Settings
from persona_os.distill import contract as d
from persona_os.distill.service import Distiller
from persona_os.domain import contract as c
from persona_os.domain.errors import ServiceError, model_not_configured
from persona_os.domain.ports import PersonaModel
from persona_os.infra.db import create_tables, make_engine, sessions
from persona_os.model_settings import (
    ModelSettings,
    ModelSettingsUpdate,
    ModelSettingsView,
    ModelSwitch,
    ModelTestResult,
)
from persona_os.reflection import (
    Reflection,
    ReflectionDecision,
    ReflectionPreview,
    Reflections,
    ReflectionStart,
)
from persona_os.roster import Personas, Roster

API_VERSION = "v1"
ERRORS = {
    401: {"model": c.ErrorResponse}, 404: {"model": c.ErrorResponse},
    409: {"model": c.ErrorResponse}, 422: {"model": c.ErrorResponse},
}
GENERATION_ERRORS = ERRORS | {412: {"model": c.ErrorResponse}, 502: {"model": c.ErrorResponse},
                              503: {"model": c.ErrorResponse}}


def create_app(settings: Settings | None = None, model: PersonaModel | None = None) -> FastAPI:
    settings = settings or Settings()
    tokens = settings.token_namespaces()
    engine = make_engine(settings.database_url)
    store = PersonaStore(sessions(engine), settings.max_personas)
    switch = ModelSwitch(model)
    runner = Engine(sessions(engine), switch, settings.model)
    model_settings = ModelSettings(settings, switch, runner)
    if model is None:  # the saved settings page, else the environment
        switch.inner = model_settings.build()
        runner.model_name = model_settings.effective().model
    model = switch
    aftercare = Aftercare(sessions(engine), model, runner)
    feedback = FeedbackStore(sessions(engine))
    evolution = Evolution(sessions(engine), model, runner)
    aftercare.evolution = evolution
    distiller = Distiller(sessions(engine), model, runner, store)
    chat = Chat(sessions(engine), runner, aftercare, settings.chat_idle_minutes)
    personas = Personas(sessions(engine), store, chat, aftercare)
    reflections = Reflections(sessions(engine), model, runner, store, chat)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.orm_auto_create:
            await create_tables(engine)
        await aftercare.resume()
        await distiller.resume()
        await reflections.fail_unfinished()
        chat.start_sweeper()
        yield
        await chat.stop_sweeper()
        if hasattr(model, "close"):
            await model.close()
        await engine.dispose()

    app = FastAPI(title="anima-server", version="0.1.0", lifespan=lifespan,
                  description="EMERGI-PersonaOS persona service. Contract: docs/接口契约.md")
    app.state.aftercare = aftercare
    app.state.distiller = distiller
    app.state.chat = chat
    app.state.model = switch
    app.state.reflections = reflections

    def error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
        body = c.ErrorResponse(error=c.ErrorBody(code=code, message=message, retryable=retryable))
        return JSONResponse(body.model_dump(), status_code=status)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return error(exc.status, exc.code, exc.message, exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        return error(422, "invalid_request", f"{where}: {first.get('msg', 'invalid')}")

    def namespace(authorization: Annotated[str | None, Header()] = None) -> str:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or token not in tokens:
            raise ServiceError(401, "unauthorized", "missing or unknown service token")
        return tokens[token]

    Ns = Annotated[str, Depends(namespace)]

    def need_model(ns: Ns) -> None:
        """Refuse model work at once while no API key is set (instead of failing in a job)."""
        if not model_settings.ready():
            raise model_not_configured()

    Model = [Depends(need_model)]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"/{API_VERSION}/runtime", responses=ERRORS)
    async def runtime(ns: Ns) -> dict[str, object]:
        active = model_settings.effective()
        return {"namespace": ns, "model_backend": active.model_backend, "model": active.model,
                "calls_per_action": 2, "calls_per_persona_after_episode": 1, "stage": "A4"}

    # ---- construction ------------------------------------------------------------------------
    @app.post(f"/{API_VERSION}/personas:import", responses=ERRORS)
    async def import_personas(body: c.PersonaImportRequest, ns: Ns) -> c.PersonaImportResponse:
        return await store.import_personas(ns, body)

    # ---- self-reflection (Anima) ----------------------------------------------------------------
    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/reflection:preview", responses=ERRORS)
    async def reflection_preview(persona_id: str, ns: Ns,
                                 persona_name: Annotated[str, Query(max_length=60)] = "它"
                                 ) -> ReflectionPreview:
        return await reflections.preview(ns, persona_id, persona_name)

    @app.post(f"/{API_VERSION}/personas/{{persona_id}}/reflections", responses=GENERATION_ERRORS,
              dependencies=Model)
    async def start_reflection(persona_id: str, body: ReflectionStart, ns: Ns) -> Reflection:
        return await reflections.start(ns, persona_id, body)

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/reflections", responses=ERRORS)
    async def list_reflections(persona_id: str, ns: Ns,
                               limit: Annotated[int, Query(ge=1, le=100)] = 20
                               ) -> list[Reflection]:
        return await reflections.list(ns, persona_id, limit)

    @app.get(f"/{API_VERSION}/reflections/{{reflection_id}}", responses=ERRORS)
    async def get_reflection(reflection_id: str, ns: Ns) -> Reflection:
        return await reflections.get(ns, reflection_id)

    @app.post(f"/{API_VERSION}/reflections/{{reflection_id}}:approve", responses=ERRORS)
    async def approve_reflection(reflection_id: str, body: ReflectionDecision,
                                 ns: Ns) -> Reflection:
        return await reflections.approve(ns, reflection_id, body)

    @app.post(f"/{API_VERSION}/reflections/{{reflection_id}}:decline", responses=ERRORS)
    async def decline_reflection(reflection_id: str, ns: Ns) -> Reflection:
        return await reflections.decline(ns, reflection_id)

    @app.post(f"/{API_VERSION}/reflections/{{reflection_id}}/versions/{{key}}:try",
              responses=GENERATION_ERRORS, dependencies=Model)
    async def try_reflection(reflection_id: str, key: str, ns: Ns) -> dict[str, str]:
        return await reflections.try_version(ns, reflection_id, key)

    # ---- the owner's model service (settings page) ---------------------------------------------
    @app.get(f"/{API_VERSION}/settings/model", responses=ERRORS)
    async def get_model_settings(ns: Ns) -> ModelSettingsView:
        return model_settings.view()

    @app.put(f"/{API_VERSION}/settings/model", responses=ERRORS)
    async def put_model_settings(body: ModelSettingsUpdate, ns: Ns) -> ModelSettingsView:
        return await model_settings.update(body)

    @app.post(f"/{API_VERSION}/settings/model:test", responses=ERRORS)
    async def test_model_settings(body: ModelSettingsUpdate, ns: Ns) -> ModelTestResult:
        return await model_settings.test(body)

    @app.get(f"/{API_VERSION}/personas", responses=ERRORS)
    async def list_personas(ns: Ns) -> Roster:
        return await personas.list(ns)

    @app.delete(f"/{API_VERSION}/personas/{{persona_id}}", responses=ERRORS)
    async def delete_persona(persona_id: str, ns: Ns) -> dict[str, str]:
        await personas.delete(ns, persona_id)
        return {"status": "deleted"}

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}", responses=ERRORS)
    async def get_persona(persona_id: str, ns: Ns) -> c.PersonaSummary:
        return await store.summary(ns, persona_id)

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/versions", responses=ERRORS)
    async def list_versions(persona_id: str, ns: Ns) -> list[c.BeliefVersion]:
        return await store.versions(ns, persona_id)

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/versions/{{version_id}}", responses=ERRORS)
    async def get_version(persona_id: str, version_id: str, ns: Ns) -> c.BeliefVersion:
        return await store.version(ns, persona_id, version_id)

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/history", responses=ERRORS)
    async def history(persona_id: str, ns: Ns) -> list[c.VersionHistory]:
        return await store.history(ns, persona_id)

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/materials", responses=ERRORS)
    async def materials(persona_id: str, ns: Ns,
                        refs: Annotated[str, Query(max_length=20000)] = "") -> dict[str, str]:
        return await store.materials(ns, persona_id, [r for r in refs.split(",") if r])

    @app.post(f"/{API_VERSION}/personas/{{persona_id}}/versions/{{version_id}}:restore",
              responses=ERRORS)
    async def restore(persona_id: str, version_id: str, ns: Ns) -> c.BeliefVersion:
        await chat.end(ns, persona_id)  # beliefs only change between episodes
        return await store.restore(ns, persona_id, version_id)

    # ---- situational adaptation ----------------------------------------------------------------
    @app.post(f"/{API_VERSION}/episodes", responses=ERRORS)
    async def create_episode(body: c.EpisodeCreate, ns: Ns) -> c.Episode:
        return await runner.create_episode(ns, body)

    @app.post(f"/{API_VERSION}/episodes/{{episode_id}}/observations", responses=ERRORS)
    async def add_observations(episode_id: str, body: c.ObservationBatch, ns: Ns) -> dict[str, int]:
        return await runner.add_observations(ns, episode_id, body)

    @app.post(f"/{API_VERSION}/episodes/{{episode_id}}/actions:prepare",
              responses=GENERATION_ERRORS, dependencies=Model)
    async def prepare_action(episode_id: str, body: c.ActionPrepare, ns: Ns) -> c.PreparedAction:
        return await runner.prepare(ns, episode_id, body)

    @app.post(f"/{API_VERSION}/episodes/{{episode_id}}/actions/{{action_id}}:commit",
              responses=ERRORS)
    async def commit_action(episode_id: str, action_id: str, body: c.ActionCommit,
                            ns: Ns) -> c.PreparedAction:
        return await runner.commit(ns, episode_id, action_id, body)

    @app.post(f"/{API_VERSION}/episodes/{{episode_id}}/actions/{{action_id}}:discard",
              responses=ERRORS)
    async def discard_action(episode_id: str, action_id: str, ns: Ns) -> c.PreparedAction:
        return await runner.discard(ns, episode_id, action_id)

    @app.post(f"/{API_VERSION}/episodes/{{episode_id}}:close", responses=ERRORS)
    async def close_episode(episode_id: str, body: c.EpisodeClose, ns: Ns) -> c.Episode:
        episode = await runner.close_episode(ns, episode_id, body)
        aftercare.schedule(ns, episode_id)  # memories + monitoring, one call per persona
        return episode

    @app.get(f"/{API_VERSION}/episodes/{{episode_id}}/jobs", responses=ERRORS)
    async def episode_jobs(episode_id: str, ns: Ns) -> list[c.EpisodeJob]:
        return await aftercare.jobs(ns, episode_id)

    @app.post(f"/{API_VERSION}/episodes/{{episode_id}}/jobs:run", responses=ERRORS)
    async def rerun_jobs(episode_id: str, ns: Ns) -> list[c.EpisodeJob]:
        return await aftercare.rerun(ns, episode_id)

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/memories", responses=ERRORS)
    async def memories(persona_id: str, ns: Ns,
                       limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[c.Memory]:
        return await aftercare.memories(ns, persona_id, limit)

    @app.get(f"/{API_VERSION}/actions/{{action_id}}/state", responses=ERRORS)
    async def action_state(action_id: str, ns: Ns) -> c.ActionState:
        return await runner.state_of(ns, action_id)

    @app.get(f"/{API_VERSION}/actions/{{action_id}}/explanation", responses=ERRORS)
    async def explain(action_id: str, ns: Ns) -> c.Explanation:
        return await runner.explain(ns, action_id)

    # ---- owner feedback ------------------------------------------------------------------------
    @app.put(f"/{API_VERSION}/feedback-taxonomies/{{name}}", responses=ERRORS)
    async def put_taxonomy(name: str, body: c.FeedbackTaxonomy, ns: Ns) -> c.FeedbackTaxonomy:
        return await feedback.put_taxonomy(ns, name, body)

    @app.post(f"/{API_VERSION}/feedback", responses=ERRORS)
    async def add_feedback(body: c.FeedbackCreate, ns: Ns) -> c.FeedbackCreate:
        return await feedback.add(ns, body)

    @app.get(f"/{API_VERSION}/episodes/{{episode_id}}/feedback", responses=ERRORS)
    async def episode_feedback(
        episode_id: str, ns: Ns, persona_id: Annotated[str, Query(min_length=1)]
    ) -> c.EpisodeFeedback:
        return await feedback.episode(ns, episode_id, persona_id)

    @app.post(f"/{API_VERSION}/episodes/{{episode_id}}/feedback:complete", responses=ERRORS)
    async def feedback_complete(episode_id: str, body: c.FeedbackComplete,
                                ns: Ns) -> dict[str, str]:
        newly, state = await feedback.complete(ns, episode_id, body.persona_id)
        if newly:  # reflection starts once the owner has finished (user decision 2026-10-22)
            async with aftercare.sessions() as s, s.begin():
                await queue_reflect(s, ns, episode_id, body.persona_id)
            aftercare.schedule(ns, episode_id)
        return {"status": state}

    # ---- controlled evolution ------------------------------------------------------------------
    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/candidates", responses=ERRORS)
    async def list_candidates(persona_id: str, ns: Ns,
                              status: c.CandidateStatus | None = None) -> list[c.Candidate]:
        return await evolution.candidates(ns, persona_id, status)

    @app.post(f"/{API_VERSION}/candidates/{{candidate_id}}:decide", responses=ERRORS)
    async def decide(candidate_id: str, body: c.CandidateDecision, ns: Ns) -> c.Candidate:
        return await evolution.decide(ns, candidate_id, body)

    # ---- distillation (Anima A1) ---------------------------------------------------------------
    base = f"/{API_VERSION}/distillations"

    @app.post(base, responses=ERRORS)
    async def create_distillation(body: d.DistillationCreate, ns: Ns) -> d.Distillation:
        return await distiller.create(ns, body)

    @app.get(base, responses=ERRORS)
    async def list_distillations(ns: Ns, kind: d.Kind = "initial") -> list[d.Distillation]:
        return await distiller.list_all(ns, kind)

    @app.get(base + "/{distillation_id}", responses=ERRORS)
    async def get_distillation(distillation_id: str, ns: Ns) -> d.Distillation:
        return await distiller.get(ns, distillation_id)

    @app.post(base + "/{distillation_id}/sources", responses=ERRORS)
    async def add_source(distillation_id: str, body: d.SourceUpload, ns: Ns) -> d.SourceInfo:
        return await distiller.add_source(ns, distillation_id, body)

    @app.get(base + "/{distillation_id}/characters", responses=ERRORS)
    async def characters(distillation_id: str, ns: Ns) -> list[d.Character]:
        return await distiller.characters(ns, distillation_id)

    @app.post(base + "/{distillation_id}:preview", responses=ERRORS)
    async def preview(distillation_id: str, body: d.Target, ns: Ns) -> d.Preview:
        return await distiller.preview(ns, distillation_id, body)

    @app.post(base + "/{distillation_id}:start", responses=ERRORS,
              dependencies=Model)
    async def start(distillation_id: str, body: d.DistillationStart,
                    ns: Ns) -> d.Distillation:
        return await distiller.start(ns, distillation_id, body)

    @app.get(base + "/{distillation_id}/entries", responses=ERRORS)
    async def distill_entries(distillation_id: str, ns: Ns) -> list[d.ReviewedEntry]:
        return await distiller.entries(ns, distillation_id)

    @app.post(base + "/{distillation_id}/entries", responses=ERRORS)
    async def add_distill_entry(distillation_id: str, body: d.EntryAdd,
                                ns: Ns) -> d.ReviewedEntry:
        return await distiller.add_entry(ns, distillation_id, body)

    @app.patch(base + "/{distillation_id}/entries/{entry_id}", responses=ERRORS)
    async def update_distill_entry(distillation_id: str, entry_id: str, body: d.EntryUpdate,
                                   ns: Ns) -> d.ReviewedEntry:
        return await distiller.update_entry(ns, distillation_id, entry_id, body)

    @app.get(base + "/{distillation_id}/evidence/{evidence_id}", responses=ERRORS)
    async def distill_evidence(distillation_id: str, evidence_id: str, ns: Ns) -> d.Evidence:
        return await distiller.evidence(ns, distillation_id, evidence_id)

    @app.post(base + "/{distillation_id}:commit", responses=ERRORS)
    async def commit_distillation(distillation_id: str, ns: Ns) -> d.CommitResult:
        found = await distiller.get(ns, distillation_id)
        if found.kind == "evolution" and found.persona_id:
            await chat.end(ns, found.persona_id)  # beliefs only change between episodes
        return await distiller.commit(ns, distillation_id)

    @app.post(base + "/{distillation_id}:discard", responses=ERRORS)
    async def discard_distillation(distillation_id: str, ns: Ns) -> dict[str, str]:
        await distiller.discard(ns, distillation_id)
        return {"status": "discarded"}

    @app.post(base + "/{distillation_id}/entries/{entry_id}:try", responses=GENERATION_ERRORS,
              dependencies=Model)
    async def try_entry(distillation_id: str, entry_id: str, ns: Ns) -> d.TryResult:
        return await distiller.try_entry(ns, distillation_id, entry_id)

    # ---- evolution as distillation (Anima A4) --------------------------------------------------
    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/evolution:preview", responses=ERRORS)
    async def evolution_preview(persona_id: str, ns: Ns,
                                persona_name: Annotated[str, Query(max_length=60)] = "它"
                                ) -> d.EvolutionPreview:
        return await distiller.evolution_preview(ns, persona_id, persona_name)

    @app.post(f"/{API_VERSION}/personas/{{persona_id}}/evolutions", responses=ERRORS,
              dependencies=Model)
    async def create_evolution(persona_id: str, body: d.EvolutionStart,
                               ns: Ns) -> d.Distillation:
        return await distiller.create_evolution(ns, persona_id, body)

    # ---- pet chat (Anima A2) -------------------------------------------------------------------
    @app.post(f"/{API_VERSION}/personas/{{persona_id}}/chat:say", responses=GENERATION_ERRORS,
              dependencies=Model)
    async def chat_say(persona_id: str, body: ch.ChatSay, ns: Ns) -> ch.ChatReply:
        return await chat.say(ns, persona_id, body)

    @app.post(f"/{API_VERSION}/personas/{{persona_id}}/chat:end", responses=ERRORS)
    async def chat_end(persona_id: str, ns: Ns) -> ch.ChatSession | None:
        return await chat.end(ns, persona_id)

    @app.get(f"/{API_VERSION}/personas/{{persona_id}}/chats", responses=ERRORS)
    async def chat_history(persona_id: str, ns: Ns,
                           limit: Annotated[int, Query(ge=1, le=100)] = 20
                           ) -> list[ch.ChatSession]:
        return await chat.history(ns, persona_id, limit)

    # ---- outbox ----------------------------------------------------------------------------------
    @app.get(f"/{API_VERSION}/events", responses=ERRORS)
    async def events(ns: Ns, after: Annotated[int, Query(ge=0)] = 0,
                     limit: Annotated[int, Query(ge=1, le=500)] = 100) -> c.EventPage:
        return await aftercare.events(ns, after, limit)

    return app
