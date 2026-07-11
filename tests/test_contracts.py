import json

import pytest

from conductor.agents import contracts


def test_parse_envelope_extracts_session_and_result() -> None:
    raw = json.dumps(
        {"session_id": "s1", "result": "hi", "is_error": False, "extra": 1}
    )
    env = contracts.parse_envelope(raw)
    assert env.session_id == "s1"
    assert env.result == "hi"


def test_parse_envelope_reads_result_event_from_stream_json() -> None:
    # stream-json output: many events, the final type=result one carries session_id + result.
    raw = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s2"}),
            json.dumps({"type": "assistant", "message": {"content": "working…"}}),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "s2",
                    "result": "done",
                }
            ),
        ]
    )
    env = contracts.parse_envelope(raw)
    assert env.session_id == "s2"
    assert env.result == "done"


def test_parse_envelope_empty_raises() -> None:
    with pytest.raises(contracts.MalformedAgentOutput):
        contracts.parse_envelope("   \n  ")


def test_parse_verdict_accepts_pass_alias() -> None:
    verdict = contracts.parse_verdict(
        json.dumps(
            {
                "pass": False,
                "findings": [
                    {"severity": "high", "comment": "bug", "file": "a.py", "line": 3}
                ],
            }
        )
    )
    assert verdict.passed is False
    assert verdict.findings[0].file == "a.py"
    assert verdict.findings[0].line == 3


def test_parse_verdict_defaults_empty_findings() -> None:
    verdict = contracts.parse_verdict(json.dumps({"pass": True}))
    assert verdict.passed is True
    assert verdict.findings == []


def test_parse_plan() -> None:
    plan = contracts.parse_plan(
        json.dumps(
            {
                "tickets": [
                    {"key": "T1", "title": "a", "target_repo": "backend"},
                    {
                        "key": "T2",
                        "title": "b",
                        "target_repo": "ui",
                        "blocked_by": ["T1"],
                    },
                ]
            }
        )
    )
    assert [t.key for t in plan.tickets] == ["T1", "T2"]
    assert plan.tickets[1].blocked_by == ["T1"]


def test_invalid_json_raises_malformed() -> None:
    with pytest.raises(contracts.MalformedAgentOutput):
        contracts.parse_verdict("not json")


def test_wrong_shape_raises_malformed() -> None:
    with pytest.raises(contracts.MalformedAgentOutput):
        contracts.parse_verdict(json.dumps({"findings": []}))  # missing required "pass"
