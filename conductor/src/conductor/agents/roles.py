from enum import StrEnum


class AgentRole(StrEnum):
    """The four agent roles. One image serves all of them; the role selects the model, the OTel
    resource attributes, and (in Phase 5) the prompt and allowed tools."""

    PLANNER = "planner"
    ENGINEER = "engineer"
    REVIEWER = "reviewer"
    QA = "qa"


# Role → the ConfigStore setting name holding that role's Claude model.
MODEL_SETTING: dict[AgentRole, str] = {
    AgentRole.PLANNER: "claude_model_planner",
    AgentRole.ENGINEER: "claude_model_engineer",
    AgentRole.REVIEWER: "claude_model_reviewer",
    AgentRole.QA: "claude_model_qa",
}
