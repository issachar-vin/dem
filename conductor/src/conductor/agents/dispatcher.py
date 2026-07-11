"""The agent dispatcher: turns a role + ticket + prompt into a running agent container and returns
its parsed output. Owns the docker-run construction (per-ticket volumes, role env, resource limits,
hard timeout → kill) and the per-role concurrency limit. Volume *lifecycle* (create/clone/destroy)
lives in volumes.py; this module assumes the ticket's volumes already exist."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from conductor.agents import contracts
from conductor.agents.contracts import MalformedAgentOutput
from conductor.agents.dockerctl import (
    ContainerFailed,
    ContainerTimeout,
    DockerFactory,
    run_container,
)
from conductor.agents.roles import AgentRole
from conductor.catalog import DEFAULT_AGENT_IMAGE
from conductor.store import ConfigStore

logger = logging.getLogger("conductor")

_REPARSE_PROMPT = (
    "Your previous reply could not be parsed as the required JSON contract. Reply again with only "
    "the JSON object the instructions asked for — no prose, no code fences, nothing else."
)


@dataclass(frozen=True)
class AgentRun:
    role: AgentRole
    ticket_id: str
    prompt: str
    model: str
    resume_session_id: str | None = None
    loop_round: int = 0


class RunRecorder(Protocol):
    """Persists one agent run's captured output so the console can replay it after the container is
    gone. Injected so the dispatcher stays free of DB/store coupling (and tests can drop it)."""

    async def __call__(
        self, *, ticket_id: str, role: str, loop_round: int, output: str, ok: bool
    ) -> None: ...


class Dispatcher:
    def __init__(
        self,
        *,
        store: ConfigStore,
        docker_factory: DockerFactory,
        max_concurrent: int = 1,
        recorder: RunRecorder | None = None,
    ) -> None:
        self._store = store
        self._docker_factory = docker_factory
        self._recorder = recorder
        # Per-role semaphore: at most `max_concurrent` engineers / reviewers / QA at a time (v1 is
        # 1 — serial builds). One agent per role, not one across all roles, so review can run while
        # a different ticket's engineer holds its own slot.
        self._semaphores = {role: asyncio.Semaphore(max_concurrent) for role in AgentRole}

    async def run_parsed[T](
        self, run: AgentRun, parse: Callable[[str], T]
    ) -> tuple[contracts.ClaudeEnvelope, T]:
        """Run an agent and parse its output into a contract. Re-prompt policy (per contracts.py):
        on malformed output, resume the same session once asking for valid JSON and parse again; a
        second failure raises `MalformedAgentOutput` for the caller to park the ticket."""
        envelope = await self.run(run)
        try:
            return envelope, parse(envelope.result)
        except MalformedAgentOutput as exc:
            logger.warning("Malformed %s output; re-prompting once: %s", run.role.value, exc)
        retry = replace(run, prompt=_REPARSE_PROMPT, resume_session_id=envelope.session_id)
        envelope = await self.run(retry)
        return envelope, parse(envelope.result)

    async def run(self, run: AgentRun) -> contracts.ClaudeEnvelope:
        async with self._semaphores[run.role]:
            cfg = await self._store.resolved()
            client = self._docker_factory()
            try:
                stdout = await run_container(
                    client,
                    image=cfg.get("agent_image") or DEFAULT_AGENT_IMAGE,
                    command=_build_command(run),
                    name=f"psa-{run.role.value}-{run.ticket_id}",
                    environment=_build_env(run, cfg),
                    volumes={
                        f"psa-repo-{run.ticket_id}": "/work",
                        f"psa-claude-{run.ticket_id}": "/home/agent/.claude",
                    },
                    mem_limit=cfg.get("agent_memory_limit"),
                    nano_cpus=_nano_cpus(cfg.get("agent_cpu_limit")),
                    timeout=_timeout_seconds(cfg),
                )
            except (ContainerFailed, ContainerTimeout) as exc:
                await self._record(run, getattr(exc, "logs", str(exc)), ok=False)
                raise
            await self._record(run, stdout, ok=True)
            return contracts.parse_envelope(stdout.strip())

    async def _record(self, run: AgentRun, output: str, *, ok: bool) -> None:
        if self._recorder is None:
            return
        try:
            await self._recorder(
                ticket_id=run.ticket_id,
                role=run.role.value,
                loop_round=run.loop_round,
                output=output,
                ok=ok,
            )
        except Exception:  # capturing a log must never break a dispatch
            logger.warning("Failed to record agent run for %s", run.ticket_id, exc_info=True)


def _build_command(run: AgentRun) -> list[str]:
    command = ["claude", "-p"]
    if run.resume_session_id:
        command += ["--resume", run.resume_session_id]
    # stream-json (requires --verbose) emits incremental events — tool calls, messages — instead of
    # one buffered blob at the end, so the run is visible live in `docker logs` and captured for the
    # console. parse_envelope reads the final result event out of the JSONL.
    command += [
        run.prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        run.model,
    ]
    return command


def _build_env(run: AgentRun, cfg: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    # Exactly one Claude credential (config validation guarantees not-both); prefer subscription.
    if cfg.get("claude_code_oauth_token"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = cfg["claude_code_oauth_token"]
    elif cfg.get("anthropic_api_key"):
        env["ANTHROPIC_API_KEY"] = cfg["anthropic_api_key"]

    endpoint = cfg.get("otel_exporter_otlp_endpoint")
    if cfg.get("otel_enabled", "true").lower() == "true" and endpoint:
        env["CLAUDE_CODE_ENABLE_TELEMETRY"] = "1"
        env["OTEL_METRICS_EXPORTER"] = "otlp"
        env["OTEL_LOGS_EXPORTER"] = "otlp"
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
        env["OTEL_SERVICE_NAME"] = cfg.get("otel_service_name") or "dem"
        env["OTEL_RESOURCE_ATTRIBUTES"] = (
            f"agent.role={run.role.value},ticket.id={run.ticket_id},loop.round={run.loop_round}"
        )
    return env


def _nano_cpus(cpu_limit: str | None) -> int | None:
    if not cpu_limit:
        return None
    try:
        return int(float(cpu_limit) * 1_000_000_000)
    except ValueError:
        return None


def _timeout_seconds(cfg: dict[str, str]) -> float | None:
    try:
        return max(1.0, float(cfg.get("agent_timeout_minutes", "30")) * 60)
    except ValueError:
        return None
