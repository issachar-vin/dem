"""Typed contracts for agent output. Every agent runs `claude -p --output-format json`, which
wraps the model's text in a result envelope; that text is itself the role's JSON contract. So
parsing is two layers: the Claude envelope (session_id + result), then the role payload inside it.

Malformed output is a `MalformedAgentOutput`; the dispatcher's policy is to re-prompt once for
valid JSON and, failing that, park the ticket for a human — the policy lives there, not here."""

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class MalformedAgentOutput(Exception):
    """The agent's output could not be parsed into its expected contract."""


class ClaudeEnvelope(BaseModel):
    """The `claude -p --output-format json` result envelope. Permissive — we only need the
    session id (for `--resume`) and the result text (the role's contract payload)."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    result: str = ""
    is_error: bool = False
    subtype: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: str
    comment: str
    file: str | None = None
    line: int | None = None


class Verdict(BaseModel):
    """Reviewer / QA output: pass plus any findings to feed back to the engineer."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    passed: bool = Field(alias="pass")
    findings: list[Finding] = Field(default_factory=list)


class PlannedTicket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    title: str
    body: str = ""
    acceptance_criteria: str = ""
    target_repo: str
    blocked_by: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    """Planner output: the tickets to create plus their blocks/blocked-by graph (carried on each
    ticket's `blocked_by`, referencing the plan's own ticket keys)."""

    model_config = ConfigDict(extra="ignore")

    tickets: list[PlannedTicket]


class EngineerResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    summary: str = ""
    done: bool = True


def parse_envelope(raw: str) -> ClaudeEnvelope:
    """Extract the result envelope from a `claude -p` run. With `--output-format stream-json` the
    output is JSONL — many events, the last `type == "result"` one carrying session_id + result;
    falls back to parsing a single JSON object (the non-streaming `json` format) for back-compat."""
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        raise MalformedAgentOutput("empty agent output")
    result_event: dict[str, Any] | None = None
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            result_event = obj  # last result event wins
    if result_event is not None:
        return _validate(ClaudeEnvelope, result_event)
    return _parse(ClaudeEnvelope, lines[-1])


def parse_verdict(result: str) -> Verdict:
    return _parse(Verdict, result)


def parse_plan(result: str) -> Plan:
    return _parse(Plan, result)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(raw: str) -> str:
    """Pull the JSON payload out of an agent's reply. Models routinely wrap the contract JSON in a
    ```json code fence and pad it with prose ("Perfect. Here's my assessment: ```json … ``` Summary:
    …"), so a bare `json.loads` fails on otherwise-valid output. Prefer a fenced block if present,
    else fall back to the outermost {...}; return the input unchanged if neither is found (the parse
    then fails as before)."""
    text = raw.strip()
    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        return text[start : end + 1]
    return text


def _parse[T: BaseModel](model: type[T], raw: str) -> T:
    try:
        data: Any = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        raise MalformedAgentOutput(f"not valid JSON: {exc}") from exc
    return _validate(model, data)


def _validate[T: BaseModel](model: type[T], data: Any) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise MalformedAgentOutput(f"does not match {model.__name__}: {exc}") from exc
