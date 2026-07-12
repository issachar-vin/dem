import pytest

from conductor import prompts


def test_render_engineer_interpolates_ticket() -> None:
    rendered = prompts.render(
        "engineer",
        ticket_id="T-1",
        title="Add login",
        body="It must accept OAuth.",
        repos="- `/work/backend` — octo/backend (branch `main`)",
    )
    assert "T-1" in rendered
    assert "Add login" in rendered
    assert "It must accept OAuth." in rendered
    assert "{ticket_id}" not in rendered  # every placeholder was filled


def test_render_missing_field_raises() -> None:
    with pytest.raises(KeyError):
        prompts.render("engineer", ticket_id="T-1")  # title/body omitted
