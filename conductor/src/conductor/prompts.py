"""Role prompt templates. The conductor assembles each agent's full prompt string and passes it to
`claude -p` (the agent container is a credential-free runner), so the templates live here — bundled
in the conductor image — rather than in the agent image. Templates use `str.format` fields; every
placeholder must be supplied or rendering raises `KeyError`."""

from importlib.resources import files

_PROMPTS = files("conductor") / "prompts"


def render(role: str, **fields: str) -> str:
    template = (_PROMPTS / f"{role}.md").read_text(encoding="utf-8")
    return template.format(**fields)
