"""Model capability for self-reflection: one call for the diary and findings, one call for the
evolution versions drawn from them."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from persona_os.domain.contract import StrictModel


class ReflectSelfInput(StrictModel):
    name: str
    language: str
    beliefs: list[dict[str, Any]]  # {entry_id, layer, construct_id, text, value, scope}
    lines: list[dict[str, Any]]  # {id: "L7", who, owner: bool, text}, oldest first
    deviations: list[dict[str, Any]]  # {id: "D1", entry_id, direction, note, sessions}
    memories: list[str]


class Finding(StrictModel):
    what: str = Field(min_length=1, max_length=300)  # what happened
    meaning: str = Field(min_length=1, max_length=300)  # what it says about me
    layer: Literal["L1", "L2", "L3"]
    entry_id: str | None = None
    refs: list[str] = Field(default_factory=list, max_length=20)  # line / deviation ids


class ReflectSelfOutput(StrictModel):
    diary: str = Field(min_length=1, max_length=1200)
    findings: list[Finding] = Field(default_factory=list, max_length=8)


class ReflectVersionsInput(StrictModel):
    name: str
    language: str
    beliefs: list[dict[str, Any]]
    findings: list[dict[str, Any]]  # {id: "F1", what, meaning, layer, entry_id}
    limits: dict[str, Any]
    variants: list[dict[str, Any]]  # {key, title, rule}


class VersionChange(StrictModel):
    action: Literal["revise", "add"]
    entry_id: str | None = None
    construct_id: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=1500)
    value: dict[str, Any] | None = None
    scope: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(min_length=1, max_length=400)
    findings: list[str] = Field(default_factory=list, max_length=8)  # "F1", ...


class ProposedVersion(StrictModel):
    key: str = Field(min_length=1, max_length=4)
    summary: str = Field(min_length=1, max_length=300)  # "这样变了以后，我会……"
    changes: list[VersionChange] = Field(default_factory=list, max_length=10)


class ReflectVersionsOutput(StrictModel):
    versions: list[ProposedVersion] = Field(default_factory=list, max_length=4)
