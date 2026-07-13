"""Role prompt templates. The conductor assembles each agent's full prompt string and passes it to
`claude -p` (the agent container is a credential-free runner), so the templates live here rather
than in the agent image.

Templates are editable from the console and stored in the DB (`agent_prompts`). The bundled
`prompts/*.md` files are the canonical defaults: they seed the DB on first boot and are the runtime
fallback if a row is ever missing. Templates use `str.format` fields; every placeholder the calling
code supplies must appear, and the template may only reference the fields listed in `FIELDS`."""

from __future__ import annotations

from importlib.resources import files

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.models import AgentPrompt

_PROMPTS = files("conductor") / "prompts"

DEFAULT_VARIANT = "default"

# The `{placeholder}` fields each role's template is allowed to reference. The scheduler supplies
# exactly these when rendering; a template using anything else would raise KeyError on a live job,
# so `validate` rejects it at save time.
FIELDS: dict[str, set[str]] = {
    "engineer": {"ticket_id", "title", "body", "repos"},
    "planner": {"title", "body", "repos"},
    "reviewer": {"ticket_id", "title", "body", "repos"},
    "qa": {"ticket_id", "title", "body", "repos"},
    "engineer_followup": {"ticket_id", "findings"},
    "engineer_resume": {"ticket_id", "conversation"},
}

# The order the console lists them in: the four agent roles, then the engineer's sub-prompts.
ROLES: tuple[str, ...] = (
    "engineer",
    "planner",
    "reviewer",
    "qa",
    "engineer_followup",
    "engineer_resume",
)


def default_template(role: str) -> str:
    """The bundled default for a role (seed source + runtime fallback)."""
    return (_PROMPTS / f"{role}.md").read_text(encoding="utf-8")


def _allowed(role: str) -> str:
    return ", ".join(f"{{{f}}}" for f in sorted(FIELDS[role])) or "(none)"


def validate(role: str, content: str) -> str | None:
    """Return an error message if `content` is not a usable template for `role`, else None. Renders
    it against dummy values so an unknown `{placeholder}` (KeyError) or a stray brace (ValueError)
    is caught before it can break a live dispatch."""
    try:
        content.format(**{field: "" for field in FIELDS[role]})
    except KeyError as exc:
        return f"Unknown placeholder {exc} — allowed: {_allowed(role)}."
    except (ValueError, IndexError):
        return "Invalid template: a literal { or } must be written as {{ or }}."
    return None


class PromptView(BaseModel):
    role: str
    variant: str
    content: str
    source: str


class PromptStore:
    """DB-backed access to the editable agent prompts, with the bundled files as fallback."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def seed_defaults(self, variant: str = DEFAULT_VARIANT) -> int:
        """Insert any missing role rows for `variant` from the bundled files. Seed-once: a row that
        already exists (e.g. edited in the console) is left untouched. Returns the number seeded."""
        seeded = 0
        async with self._sessionmaker() as session:
            existing = set(
                (
                    await session.execute(
                        select(AgentPrompt.role).where(AgentPrompt.variant == variant)
                    )
                )
                .scalars()
                .all()
            )
            for role in ROLES:
                if role in existing:
                    continue
                session.add(
                    AgentPrompt(
                        role=role,
                        variant=variant,
                        content=default_template(role),
                        source="seed",
                    )
                )
                seeded += 1
            if seeded:
                await session.commit()
        return seeded

    async def list(self, variant: str = DEFAULT_VARIANT) -> list[PromptView]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(AgentPrompt).where(AgentPrompt.variant == variant)
            )
            by_role = {row.role: row for row in result.scalars().all()}
        out: list[PromptView] = []
        for role in ROLES:
            row = by_role.get(role)
            out.append(
                PromptView(
                    role=role,
                    variant=variant,
                    content=row.content if row else default_template(role),
                    source=row.source if row else "default",
                )
            )
        return out

    async def get(self, role: str, variant: str = DEFAULT_VARIANT) -> str:
        async with self._sessionmaker() as session:
            row = await self._row(session, role, variant)
            return row.content if row else default_template(role)

    async def set(self, role: str, content: str, variant: str = DEFAULT_VARIANT) -> None:
        error = validate(role, content)
        if error:
            raise ValueError(error)
        async with self._sessionmaker() as session:
            row = await self._row(session, role, variant)
            if row:
                row.content = content
                row.source = "ui"
            else:
                session.add(AgentPrompt(role=role, variant=variant, content=content, source="ui"))
            await session.commit()

    async def render(self, role: str, *, variant: str = DEFAULT_VARIANT, **fields: str) -> str:
        template = await self.get(role, variant)
        return template.format(**fields)

    @staticmethod
    async def _row(session: AsyncSession, role: str, variant: str) -> AgentPrompt | None:
        result = await session.execute(
            select(AgentPrompt).where(AgentPrompt.role == role, AgentPrompt.variant == variant)
        )
        return result.scalar_one_or_none()
