"""One OpenAI Agents SDK agent over local job search tools."""

import asyncio
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files
from math import isfinite
from pathlib import Path
from time import monotonic
from uuid import uuid4

from agents import (
    Agent,
    Model,
    RunConfig,
    RunHooks,
    Runner,
    Session,
    SessionSettings,
    TResponseInputItem,
    function_tool,
)
from agents.exceptions import AgentsException
from agents.tool_context import ToolContext
from dotenv import load_dotenv

from .approvals import (
    APPROVAL_TIMEOUT_SECONDS,
    ApprovalDecision,
    ApprovalError,
    ApprovalRequest,
    canonical_arguments,
)
from .contracts import (
    JOBS_PATH,
    PROJECT_ROOT,
    RESULTS_PATH,
    RESUME_PATH,
    LiveJobPosting,
)
from .filtering import SearchCriteria, parse_criteria
from .guardrails import restricted_input, restricted_output
from .identity import Identity, OwnedSession, UserResources
from .jsearch import JSearchError
from .matching import assess_fit as assess_live_fit
from .openwebninja_mcp import OpenWebNinjaAdapter
from .retrieval import search_documents as search_document_records
from .tool_tracing import ToolTrace, traced_call, traced_call_async
from .tools import read_resume as read_resume_file
from .tools import save_results as save_results_file
from .tools import search_jobs as search_job_records
from .tools.search_jobs import format_live_results, provider_request

MAX_TURNS = 8
MAX_MODEL_CALLS = 8
MAX_TOOL_CALLS = 16
RUN_TIMEOUT_SECONDS = 60
MAX_CONTEXT_TURNS = 5
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
    request_text: str = ""
    save_job_ids: frozenset[str] | None = None
    identity: Identity | None = None
    run_id: str = field(default_factory=lambda: uuid4().hex)
    approved_actions: dict[str, ApprovalRequest] = field(default_factory=dict)
    criteria: SearchCriteria = SearchCriteria()
    searched_jobs: dict[str, LiveJobPosting] = field(default_factory=dict)
    tool_traces: list[ToolTrace] = field(default_factory=list)


@dataclass
class OwnedAgent(Agent[JobAgentContext]):
    owner: Identity | None = None


@dataclass(frozen=True)
class AgentTurn:
    """Final answer and replay-ready session history for the next CLI turn."""

    text: str
    history: list[TResponseInputItem]
    tool_traces: tuple[ToolTrace, ...] = ()


class SessionRecoveryError(RuntimeError):
    """Session history could not be restored; callers must stop this conversation."""


class RunBudgetExceeded(AgentsException):
    """A run tried to start a call beyond its budget."""


class BudgetHooks(RunHooks[JobAgentContext]):
    def __init__(self, max_model_calls: int, max_tool_calls: int):
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.model_calls = 0
        self.tool_calls = 0

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        if self.model_calls >= self.max_model_calls:
            raise RunBudgetExceeded("Model call budget exhausted.")
        self.model_calls += 1

    async def on_tool_start(self, context, agent, tool):
        if self.tool_calls >= self.max_tool_calls:
            raise RunBudgetExceeded("Tool call budget exhausted.")
        self.tool_calls += 1


def unquoted_request(message: str) -> str:
    """Exclude quoted evidence from current-request authorization."""
    text = re.sub(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", " ", message, flags=re.S)
    text = re.sub(r"(?m)^[ \t]*>[^\n]*", " ", text)
    return re.sub(
        r"""`[^`\n]*(?:`|$)|"[^"\n]*(?:"|$)|(?<!\w)'[^'\n]*(?:'(?!\w)|$)""",
        " ", text, flags=re.M,
    )


def explicit_save_requested(message: str) -> bool:
    """Allow affirmative save requests outside quoted evidence in this turn."""
    text = unquoted_request(message)
    return bool(_SAVE_REQUEST.search(text) and not _SAVE_NEGATION.search(text))


def load_instructions() -> str:
    """Load the versioned prompt from the installed package."""
    return files("openai_agent_sdk").joinpath("prompts", "system.md").read_text(
        encoding="utf-8"
    )


def build_agent(
    model: str | Model | None = None,
    *,
    resources: UserResources | None = None,
    catalog_path: Path = JOBS_PATH,
    resume_path: Path = RESUME_PATH,
    results_path: Path = RESULTS_PATH,
    provider: str | None = None,
    jsearch_api_key: str | None = None,
    mcp_adapter: OpenWebNinjaAdapter | None = None,
    enable_submissions: bool = False,
) -> Agent[JobAgentContext]:
    """Register job tools plus private document retrieval for scoped agents."""
    load_dotenv(PROJECT_ROOT / ".env")
    if model is None:
        model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-5-mini"
    selected_provider = (
        provider or os.getenv("JOB_SEARCH_PROVIDER", "mock")
    ).strip().lower()
    selected_key = jsearch_api_key if jsearch_api_key is not None else os.getenv(
        "JSEARCH_API_KEY", ""
    )

    if selected_provider == "mcp" and mcp_adapter is None:
        raise ValueError("MCP mode requires an open search session.")
    live_provider = selected_provider in {"jsearch", "mcp"}
    if resources is not None and (resume_path != RESUME_PATH or
                                  results_path != RESULTS_PATH):
        raise ValueError("Scoped agents use owner paths, not private path overrides.")

    submission_tools = []
    if enable_submissions:
        if resources is None or selected_provider != "mock":
            raise ValueError("Submissions require an identity and mock provider.")
        # Import here because the tool uses JobAgentContext from this module.
        from .tools.submit_application import build_submission_tool

        submission_tools = [build_submission_tool(resources, catalog_path=catalog_path)]

    def check_access(ctx, *arguments):
        owner = resources.identity if resources is not None else None
        if ctx.context.identity != owner:
            raise PermissionError("Agent access denied.")
        # SDK argument parsing can ignore extra fields; reject authority overrides.
        if set(json.loads(ctx.tool_arguments or "{}")) - set(arguments):
            raise PermissionError("Unexpected tool arguments.")

    def private_path(name, fallback):
        return resources.file(name) if resources is not None else fallback

    def external_query(ctx, query):
        query = query.strip()
        match = re.search(re.escape(query), ctx.context.request_text, re.IGNORECASE)
        if not query or len(query) > 200 or match is None:
            raise PermissionError(
                "External search query must be copied from the current request "
                "and contain 1–200 characters."
            )
        approved = match.group()
        outbound, _ = provider_request(approved, ctx.context.criteria)
        secrets = (selected_key, os.getenv("OPENAI_API_KEY", ""),
                   os.getenv("OPENWEBNINJA_API_KEY", ""))
        if any(secret and secret.casefold() in outbound.casefold()
               for secret in secrets):
            raise PermissionError("External search cannot contain credentials.")
        return approved

    @function_tool
    async def search_jobs(ctx: ToolContext[JobAgentContext], query: str) -> str:
        """Search jobs by role, skill, or location using the configured source."""
        def operation() -> str:
            check_access(ctx, "query")
            approved_query = external_query(ctx, query) if live_provider else query
            output = search_job_records(
                approved_query, catalog_path=catalog_path,
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

        if selected_provider == "mcp":
            async def mcp_operation() -> str:
                check_access(ctx, "query")
                approved_query = external_query(ctx, query)
                provider_query, options = provider_request(
                    approved_query, ctx.context.criteria
                )
                result = await mcp_adapter.search(provider_query, **options)
                output = format_live_results(result, ctx.context.criteria)
                if not output.startswith("Error:"):
                    ctx.context.searched_jobs.update(
                        {job["id"]: job for job in json.loads(output)["jobs"]}
                    )
                return output

            try:
                return await traced_call_async(
                    ctx, source="mcp", operation=mcp_operation
                )
            except JSearchError as error:
                return f"Error: {error}"
        return traced_call(
            ctx, operation=operation,
            source=(selected_provider if selected_provider in {"mock", "jsearch"}
                    else "unknown"),
        )

    @function_tool
    def search_documents(ctx: ToolContext[JobAgentContext], query: str) -> str:
        """Search your local job/company documents and return source excerpts."""
        def operation() -> str:
            check_access(ctx, "query")
            return json.dumps([
                {
                    "document_id": doc.document_id, "kind": doc.kind,
                    "reference": doc.reference, "source": doc.source,
                    "updated_on": doc.updated_on.isoformat(), "text": doc.text,
                }
                for doc in search_document_records(resources, query)
            ], ensure_ascii=False)

        return traced_call(ctx, source="local_documents", operation=operation)

    @function_tool
    def read_resume(ctx: ToolContext[JobAgentContext]) -> str:
        """Read the candidate's local resume for specific experience evidence."""
        def operation() -> str:
            check_access(ctx)
            return read_resume_file(resume_path=private_path("resume.md", resume_path))

        return traced_call(ctx, source="local_resume", operation=operation)

    @function_tool
    def save_results(ctx: ToolContext[JobAgentContext], content: str) -> str:
        """Save catalog job IDs and recommendation text only on user request."""
        def operation() -> str:
            check_access(ctx, "content")
            if not ctx.context.save_allowed:
                return (
                    "Error: Saving requires an explicit request in the current message."
                )
            if selected_provider != "mock":
                return "Error: Saving live jobs is not supported yet."
            return save_results_file(
                content, results_path=private_path("results.md", results_path),
                catalog_path=catalog_path,
                allowed_job_ids=ctx.context.save_job_ids,
            )

        return traced_call(ctx, source="local_results", operation=operation)

    @function_tool
    def assess_fit(ctx: ToolContext[JobAgentContext], job_id: str) -> str:
        """Check a searched job against local resume evidence, without guessing."""
        def operation() -> str:
            check_access(ctx, "job_id")
            path = private_path("resume.md", resume_path)
            job = ctx.context.searched_jobs.get(job_id)
            if job is None:
                return "Error: Search for this live job in the current turn first."
            resume = read_resume_file(resume_path=path)
            if resume.startswith("Error:"):
                return resume
            return json.dumps(assess_live_fit(job, resume), ensure_ascii=False)

        return traced_call(ctx, source="local_matching", operation=operation)

    return OwnedAgent(
        owner=resources.identity if resources is not None else None,
        name="Job Search Agent",
        input_guardrails=[restricted_input],
        output_guardrails=[restricted_output],
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
            if live_provider else ""
        ) + (
            "\nUse search_documents for your local job/company documents. "
            "It returns stored source excerpts, not live job search results.\n"
            if resources is not None else ""
        ) + (
            "\nFor a requested application, call submit_application with the exact "
            "fictional job ID and cover letter. The application pauses for human "
            "approval. A chat instruction is not an approval decision. This only "
            "records a local simulation; never claim an employer received it.\n"
            if enable_submissions else ""
        ),
        model=model,
        tools=[search_jobs, read_resume, save_results] + (
            [assess_fit] if live_provider else []
        ) + ([search_documents] if resources is not None else []) + submission_tools,
    )


def _recent_turns(history, new_input):
    """Keep the latest five user turns, including their tool calls and results."""
    items = history + new_input
    starts = [i for i, item in enumerate(items) if item.get("role") == "user"]
    return items[starts[-MAX_CONTEXT_TURNS:][0]:] if starts else new_input


def run_agent(
    user_message: str,
    history: list[TResponseInputItem] | None = None,
    *,
    agent: Agent[JobAgentContext] | None = None,
    session: Session | None = None,
    identity: Identity | None = None,
    max_turns: int = MAX_TURNS,
    max_model_calls: int = MAX_MODEL_CALLS,
    max_tool_calls: int = MAX_TOOL_CALLS,
    run_timeout_seconds: float = RUN_TIMEOUT_SECONDS,
    trace_sink: Callable[[ToolTrace], None] | None = None,
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    approval_timeout_seconds: float = APPROVAL_TIMEOUT_SECONDS,
) -> AgentTurn:
    """Synchronous entry point for callers that do not own an event loop."""
    return asyncio.run(run_agent_async(
        user_message, history, agent=agent, session=session, identity=identity,
        max_turns=max_turns,
        max_model_calls=max_model_calls, max_tool_calls=max_tool_calls,
        run_timeout_seconds=run_timeout_seconds, trace_sink=trace_sink,
        approval_handler=approval_handler,
        approval_timeout_seconds=approval_timeout_seconds,
    ))


async def run_agent_async(
    user_message: str,
    history: list[TResponseInputItem] | None = None,
    *,
    agent: Agent[JobAgentContext] | None = None,
    session: Session | None = None,
    identity: Identity | None = None,
    max_turns: int = MAX_TURNS,
    max_model_calls: int = MAX_MODEL_CALLS,
    max_tool_calls: int = MAX_TOOL_CALLS,
    run_timeout_seconds: float = RUN_TIMEOUT_SECONDS,
    trace_sink: Callable[[ToolTrace], None] | None = None,
    approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    approval_timeout_seconds: float = APPROVAL_TIMEOUT_SECONDS,
) -> AgentTurn:
    """Run a bounded turn on the caller’s event loop for async tools."""
    if not user_message.strip():
        raise ValueError("User message cannot be empty.")
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1.")
    if max_model_calls < 1 or max_tool_calls < 1 or run_timeout_seconds <= 0:
        raise ValueError("Run budgets must be positive.")

    if not isfinite(approval_timeout_seconds) or approval_timeout_seconds <= 0:
        raise ValueError("Approval timeout must be finite and positive.")

    if session is not None and history:
        raise ValueError("Use session or explicit history, not both.")

    request_text = unquoted_request(user_message)
    criteria = parse_criteria(request_text)
    turn_input = _recent_turns(
        history or [], [{"role": "user", "content": user_message}]
    )
    selected_agent = agent or build_agent(
        resources=UserResources(identity) if identity is not None else None
    )
    # Check trusted ownership before loading any conversation into model input.
    if getattr(selected_agent, "owner", None) != identity:
        raise PermissionError("Agent access denied.")
    if identity is not None and history:
        raise PermissionError("Scoped conversations require an owner-scoped session.")
    if isinstance(session, OwnedSession):
        session.check_owner(identity)
    elif identity is not None and session is not None:
        raise PermissionError("An owner-scoped session is required.")
    context = JobAgentContext(
        save_allowed=explicit_save_requested(user_message), identity=identity,
        request_text=request_text,
        save_job_ids=(frozenset(re.findall(r"\bjob-\d+\b", request_text))
                      if re.search(r"\bjob-\d+\b", user_message) else None),
        criteria=criteria
    )
    # SQLite limit=-1 reads full history for recovery without changing model limits.
    prior_history = await session.get_items(limit=-1) if session is not None else []
    sdk_tracing = os.getenv("JOB_AGENT_SDK_TRACING", "false").lower() == "true"
    hooks = BudgetHooks(max_model_calls, max_tool_calls)
    config = RunConfig(
        session_settings=SessionSettings(limit=-1),
        session_input_callback=_recent_turns,
        tracing_disabled=not sdk_tracing,
        trace_include_sensitive_data=False,
        workflow_name="Job Search Agent",
    )
    remaining = run_timeout_seconds

    async def execute(input):
        nonlocal remaining
        if remaining <= 0:
            raise TimeoutError("Run deadline exhausted.")
        started = monotonic()
        try:
            # shortcut: JSearch cannot stop an in-flight synchronous HTTP call.
            return await asyncio.wait_for(Runner.run(
                selected_agent, input, session=session, context=context,
                max_turns=max_turns, hooks=hooks, run_config=config,
            ), timeout=remaining)
        finally:
            remaining -= monotonic() - started

    try:
        result = await execute(user_message if session is not None else turn_input)
        while getattr(result, "interruptions", []):
            if approval_handler is None:
                raise ValueError("A human approval handler is required.")
            if identity is None:
                raise ApprovalError("Approval requires an owner-scoped identity.")
            state = result.to_state()
            reviews = [
                (pending, ApprovalRequest(
                    identity=identity, run_id=context.run_id,
                    call_id=pending.raw_item.call_id, tool_name=pending.raw_item.name,
                    arguments=canonical_arguments(pending.raw_item.arguments),
                    expires_at=monotonic() + approval_timeout_seconds,
                )) for pending in result.interruptions
            ]
            for pending, request in reviews:
                decision = approval_handler(request)
                if (not isinstance(decision, ApprovalDecision) or
                        decision.request != request or
                        type(decision.approved) is not bool):
                    raise ApprovalError(
                        "Decision must match the pending approval request. "
                        "Request a fresh review."
                    )
                request.check(context.identity, context.run_id,
                              pending.raw_item.call_id, pending.raw_item.name,
                              pending.raw_item.arguments)
                if decision.approved:
                    state.approve(pending, always_approve=False)
                    context.approved_actions[request.call_id] = request
                else:
                    state.reject(pending, always_reject=False)
            # Human review time is excluded; execution budgets remain shared.
            result = await execute(state)
    except BaseException:
        if session is not None:
            # shortcut: one writer per session; crash-safe recovery needs transactions.
            try:
                current = await session.get_items(limit=-1)
                if current[:len(prior_history)] != prior_history:
                    raise RuntimeError("Stored history changed during the turn.")
                for _ in range(len(current) - len(prior_history)):
                    await session.pop_item()
                if await session.get_items(limit=-1) != prior_history:
                    raise RuntimeError("Stored history could not be restored.")
            except Exception as error:
                raise SessionRecoveryError(
                    "Conversation recovery failed. Stop using this session "
                    "until its history is repaired."
                ) from error
        raise
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
