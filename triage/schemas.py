"""Pydantic schemas shared by the agent, the tools, the guardrails and the eval.

Schemas the LLM fills in (Diagnosis, ReviewVerdict, ActionChoice) use plain `str` for
fields the guardrails check, so a bad value reaches the guardrail and gets logged,
instead of disappearing inside a parsing error.
"""

from typing import Literal

from pydantic import BaseModel, Field

ACTION_TYPES = ("scale_service", "restart_service", "rollback_service", "escalate_to_human", "no_action")
RISKY_ACTIONS = ("scale_service", "restart_service", "rollback_service")


class Alert(BaseModel):
    """What the on-call engineer gets paged with. Built only from the entry service."""

    case_id: str = Field(pattern=r"^case_\d{3}$")
    service: str = Field(min_length=1, max_length=64)
    signal: Literal["latency_p95", "error_rate"]
    started_at: int = Field(description="unix seconds when the symptom started")
    text: str = Field(min_length=1, max_length=300)


class ToolResult(BaseModel):
    """One tool call and the text the model saw. `data` is what evidence quotes are checked against."""

    tool_call_id: str
    tool: str
    args: dict
    data: str
    truncated: bool = False


class GuardrailEvent(BaseModel):
    layer: Literal["input", "process", "output", "action"]
    check: str
    case_id: str
    detail: str


# ---- Investigator output -------------------------------------------------


class Candidate(BaseModel):
    service: str
    fault_type: str = Field(description="one of: cpu, mem, disk, delay, loss, socket, unknown")
    confidence: float = Field(ge=0.0, le=1.0)


class Evidence(BaseModel):
    tool_call_id: str = Field(description="id of the tool output this quote comes from, e.g. t3")
    quote: str = Field(min_length=3, description="exact text copied from that tool output")
    why_it_matters: str


class Diagnosis(BaseModel):
    candidates: list[Candidate] = Field(min_length=1, max_length=3, description="ranked, most likely first")
    evidence: list[Evidence] = Field(min_length=1, max_length=6)
    summary: str


# ---- Reviewer output -----------------------------------------------------


class ReviewEvidence(BaseModel):
    tool_call_id: str
    quote: str = Field(min_length=3)


class ReviewVerdict(BaseModel):
    verdict: Literal["agree", "revise"]
    suspected_service: str | None = None
    reason: str
    evidence: list[ReviewEvidence] = Field(default_factory=list, max_length=4)


# ---- Action --------------------------------------------------------------


class ActionChoice(BaseModel):
    """What the LLM picks. Guardrails turn it into an ActionProposal or block it."""

    action: str = Field(description="one of: " + ", ".join(ACTION_TYPES))
    target_service: str
    runbook_id: str
    reason: str


class ActionProposal(ActionChoice):
    risk: Literal["low", "high"]
    needs_approval: bool


class Approval(BaseModel):
    approved: bool
    note: str = ""
    auto: bool = False


# ---- Final report --------------------------------------------------------


class IncidentReport(BaseModel):
    case_id: str
    run_id: str
    status: Literal["diagnosed", "escalated", "rejected"]
    alert: str
    diagnosis: Diagnosis | None = None
    reviews: list[ReviewVerdict] = Field(default_factory=list)
    runbook_id: str | None = None
    action: ActionProposal | None = None
    approval: Approval | None = None
    escalation_note: str | None = None
    tool_calls_used: int = 0
    guardrail_events: list[GuardrailEvent] = Field(default_factory=list)
    llm: dict = Field(default_factory=dict, description="provider, model, tokens, latency totals")
    timings: dict = Field(default_factory=dict)
    prompt_version: str = ""
