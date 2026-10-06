"""Workflow state machine. The backend state is the single source of truth."""
from __future__ import annotations


class WorkflowState:
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"
    VERIFYING = "VERIFYING"
    WRITING = "WRITING"
    CRITIQUING = "CRITIQUING"
    REVISING = "REVISING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    ACTIVE = {QUEUED, PLANNING, RESEARCHING, VERIFYING, WRITING, CRITIQUING, REVISING}
    TERMINAL = {COMPLETED, FAILED}


# Each stage: (stage key, agent name, running state, completion state)
STAGES = [
    ("planning", "planner", WorkflowState.PLANNING, "PLANNING_COMPLETED"),
    ("research", "researcher", WorkflowState.RESEARCHING, "RESEARCH_COMPLETED"),
    ("verification", "verifier", WorkflowState.VERIFYING, "VERIFICATION_COMPLETED"),
    ("writing", "writer", WorkflowState.WRITING, "REPORT_COMPLETED"),
    ("critique", "critic", WorkflowState.CRITIQUING, "CRITIQUE_COMPLETED"),
]
AGENTS = [s[1] for s in STAGES]
STAGE_KEYS = [s[0] for s in STAGES]
AGENT_LABELS = {
    "planner": "Research Planner",
    "researcher": "Researcher",
    "verifier": "Verifier",
    "writer": "Report Writer",
    "critic": "Report Critic",
    "orchestrator": "Orchestrator",
}
STAGE_FOR_AGENT = {s[1]: s[0] for s in STAGES}
MAX_REVISIONS = 2
