"""Approval-gated local simulation. No real applications are sent."""

import json
from pathlib import Path

from agents import FunctionTool, function_tool
from agents.tool_context import ToolContext

from ..agent import JobAgentContext
from ..catalog import load_catalog
from ..contracts import JOBS_PATH
from ..identity import UserResources
from ..tool_tracing import traced_call


def record_submission(
    resources: UserResources, job_id: str, cover_letter: str,
    *, catalog_path: Path = JOBS_PATH,
) -> str:
    """Append a simulation record. Trusted Python callers handle approval."""
    if not cover_letter.strip() or len(cover_letter) > 8000:
        raise ValueError("Cover letter must contain 1–8000 characters.")
    catalog = load_catalog(catalog_path)
    if catalog is None:
        raise ValueError("Mock job catalog is unavailable.")
    if job_id not in {job["id"] for job in catalog}:
        raise ValueError("Submission requires a fictional catalog job ID.")
    record = {"job_id": job_id, "cover_letter": cover_letter, "status": "simulated"}
    path = resources.file("submissions.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record) + "\n")
    return "Simulated application recorded locally."


def build_submission_tool(
    resources: UserResources, *, catalog_path: Path = JOBS_PATH,
) -> FunctionTool:
    """Build the local simulation tool used by the opt-in approval CLI."""
    @function_tool(needs_approval=True)
    def submit_application(
        ctx: ToolContext[JobAgentContext], job_id: str, cover_letter: str,
    ) -> str:
        """Record a fictional application locally after approval."""
        def operation() -> str:
            if ctx.context.identity != resources.identity:
                raise PermissionError("Submission access denied.")
            if set(json.loads(ctx.tool_arguments or "{}")) - {"job_id", "cover_letter"}:
                raise PermissionError("Unexpected tool arguments.")
            approval = ctx.context.approved_actions.pop(ctx.tool_call_id, None)
            if approval is None:
                raise PermissionError("Submission requires an application approval.")
            approval.check(ctx.context.identity, ctx.context.run_id, ctx.tool_call_id,
                           ctx.tool_name, ctx.tool_arguments)
            return record_submission(resources, job_id, cover_letter,
                                     catalog_path=catalog_path)

        return traced_call(ctx, source="local_submission", operation=operation)

    return submit_application
