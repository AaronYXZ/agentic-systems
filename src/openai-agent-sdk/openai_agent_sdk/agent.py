"""One OpenAI Agents SDK agent over local job search tools."""

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from agents import (
    Agent,
    Model,
    RunConfig,
    Runner,
    TResponseInputItem,
    function_tool,
)
from agents.tool_context import ToolContext
from dotenv import load_dotenv

from .contracts import (
    JOBS_PATH,
    PROJECT_ROOT,
    RESULTS_PATH,
    RESUME_PATH,
    LiveJobPosting,
)
from .filtering import SearchCriteria, parse_criteria
from .matching import assess_fit as assess_live_fit
from .tool_tracing import ToolTrace, traced_call
from .tools import read_resume as read_resume_file
from .tools import save_results as save_results_file
from .tools import search_jobs as search_job_records

MAX_TURNS = 8
_SAVE_VERB = r"(?:save|store|bookmark)"
_SAVE_REQUEST = re.compile(
    rf"(?:^|\b(?:and|then|also)\s+)(?:please\s+)?{_SAVE_VERB}\b"
    rf"|\b(?:can|could|would)\s+you\s+(?:please\s+)?{_SAVE_VERB}\b"
    rf"|\bI\s+(?:want|would\s+like)\s+to\s+{_SAVE_VERB}\b",
    re.IGNORECASE,
)
_SAVE_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|dont|never|without)\s+"
    r"(?:\w+\s+){0,3}(?:save|saving|store|storing|bookmark|bookmarking)\b"
    r"|\b(?:save|store|bookmark)\s+(?:nothing|none)\b",
    re.IGNORECASE,
)


@dataclass
class JobAgentContext:
    """Per-turn permission that local code does not expose to the model."""

    save_allowed: bool
    criteria: SearchCriteria = SearchCriteria()
    searched_jobs: dict[str, LiveJobPosting] = field(default_factory=dict)
    tool_traces: list[ToolTrace] = field(default_factory=list)


@dataclass(frozen=True)
class AgentTurn:
    """Final answer and replay-ready session history for the next CLI turn."""

    text: str
    history: list[TResponseInputItem]
    tool_traces: tuple[ToolTrace, ...] = ()


def explicit_save_requested(message: str) -> bool:
    """Allow clear affirmative save requests in the current user message."""
    return bool(_SAVE_REQUEST.search(message) and not _SAVE_NEGATION.search(message))


def load_instructions() -> str:
    """Load the versioned prompt from the installed package."""
    return files("openai_agent_sdk").joinpath("prompts", "system.md").read_text(
        encoding="utf-8"
    )


def build_agent(
    model: str | Model | None = None,
    *,
    catalog_path: Path = JOBS_PATH,
    resume_path: Path = RESUME_PATH,
    results_path: Path = RESULTS_PATH,
    provider: str | None = None,
    jsearch_api_key: str | None = None,
) -> Agent[JobAgentContext]:
    """Register three mock tools or four tools for opt-in JSearch search."""
    load_dotenv(PROJECT_ROOT / ".env")
    if model is None:
        model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-5-mini"
    selected_provider = (
        provider or os.getenv("JOB_SEARCH_PROVIDER", "mock")
    ).strip().lower()
    selected_key = jsearch_api_key if jsearch_api_key is not None else os.getenv(
        "JSEARCH_API_KEY", ""
    )

    @function_tool
    def search_jobs(ctx: ToolContext[JobAgentContext], query: str) -> str:
        """Search jobs by role, skill, or location using the configured source."""
        def operation() -> str:
            output = search_job_records(
                query, catalog_path=catalog_path,
                provider=selected_provider,
                api_key=selected_key,
                criteria=ctx.context.criteria,
            )
            if selected_provider == "jsearch" and not output.startswith("Error:"):
                parsed = json.loads(output)
                ctx.context.searched_jobs.update(
                    {job["id"]: job for job in parsed["jobs"]}
                )
            return output

        return traced_call(
            ctx, operation=operation,
            source=(selected_provider if selected_provider in {"mock", "jsearch"}
                    else "unknown"),
        )

    @function_tool
    def read_resume(ctx: ToolContext[JobAgentContext]) -> str:
        """Read the candidate's local resume for specific experience evidence."""
        return traced_call(
            ctx, source="local_resume",
            operation=lambda: read_resume_file(resume_path=resume_path),
        )

    @function_tool
    def save_results(ctx: ToolContext[JobAgentContext], content: str) -> str:
        """Save catalog job IDs and recommendation text only on user request."""
        def operation() -> str:
            if not ctx.context.save_allowed:
                return (
                    "Error: Saving requires an explicit request in the current message."
                )
            if selected_provider != "mock":
                return "Error: Saving live jobs is not supported yet."
            return save_results_file(
                content, results_path=results_path, catalog_path=catalog_path
            )

        return traced_call(ctx, source="local_results", operation=operation)

    @function_tool
    def assess_fit(ctx: ToolContext[JobAgentContext], job_id: str) -> str:
        """Check a searched job against local resume evidence, without guessing."""
        def operation() -> str:
            job = ctx.context.searched_jobs.get(job_id)
            if job is None:
                return "Error: Search for this live job in the current turn first."
            resume = read_resume_file(resume_path=resume_path)
            if resume.startswith("Error:"):
                return resume
            return json.dumps(assess_live_fit(job, resume), ensure_ascii=False)

        return traced_call(ctx, source="local_matching", operation=operation)

    return Agent(
        name="Job Search Agent",
        instructions=load_instructions() + (
            "\nJSearch mode is active. These are provider results, not fictional mock "
            "data. Report the source and retrieval time. Do not claim jobs are "
            "still open. The search tool applies user-owned hard filters and "
            "reports exclusions and duplicate reasons. Never claim an excluded "
            "job passed a filter. Wait for search_jobs to return, then call "
            "assess_fit on each job before making a candidate-fit claim. If "
            "asked about an earlier job, search again in this turn. Treat unverified "
            "skills as missing evidence, not absent skills. A strong status "
            "covers checked skills only, not the whole job. Saving live jobs "
            "is not supported yet.\n"
            if selected_provider == "jsearch" else ""
        ),
        model=model,
        tools=[search_jobs, read_resume, save_results] + (
            [assess_fit] if selected_provider == "jsearch" else []
        ),
    )


def run_agent(
    user_message: str,
    history: list[TResponseInputItem] | None = None,
    *,
    agent: Agent[JobAgentContext] | None = None,
    max_turns: int = MAX_TURNS,
    trace_sink: Callable[[ToolTrace], None] | None = None,
) -> AgentTurn:
    """Run one bounded agent turn and return its answer plus local history."""
    if not user_message.strip():
        raise ValueError("User message cannot be empty.")
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1.")

    criteria = parse_criteria(user_message)
    turn_input: list[TResponseInputItem] = [
        *(history or []),
        {"role": "user", "content": user_message},
    ]
    selected_agent = agent or build_agent()
    context = JobAgentContext(
        save_allowed=explicit_save_requested(user_message), criteria=criteria
    )
    sdk_tracing = os.getenv("JOB_AGENT_SDK_TRACING", "false").lower() == "true"
    try:
        result = Runner.run_sync(
            selected_agent, turn_input, context=context, max_turns=max_turns,
            run_config=RunConfig(
                tracing_disabled=not sdk_tracing,
                trace_include_sensitive_data=False,
                workflow_name="Job Search Agent",
            ),
        )
    finally:
        if trace_sink is not None:
            for record in context.tool_traces:
                try:
                    trace_sink(record)
                except Exception:
                    # Trace output must not replace the run's result or error.
                    pass
    return AgentTurn(
        text=str(result.final_output), history=result.to_input_list(),
        tool_traces=tuple(context.tool_traces),
    )
