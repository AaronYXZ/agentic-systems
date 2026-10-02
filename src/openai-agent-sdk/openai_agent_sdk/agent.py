"""One OpenAI Agents SDK agent over three deterministic local tools."""

import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from agents import (
    Agent,
    Model,
    RunConfig,
    RunContextWrapper,
    Runner,
    TResponseInputItem,
    function_tool,
)
from dotenv import load_dotenv

from .contracts import JOBS_PATH, PROJECT_ROOT, RESULTS_PATH, RESUME_PATH
from .tools import read_resume as read_resume_file
from .tools import save_results as save_results_file
from .tools import search_jobs as search_mock_jobs

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


@dataclass(frozen=True)
class JobAgentContext:
    """Per-turn permission that local code does not expose to the model."""

    save_allowed: bool


@dataclass(frozen=True)
class AgentTurn:
    """Final answer and replay-ready session history for the next CLI turn."""

    text: str
    history: list[TResponseInputItem]


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
) -> Agent[JobAgentContext]:
    """Register exactly three SDK tools over the local Python operations."""
    if model is None:
        load_dotenv(PROJECT_ROOT / ".env")
        model = os.getenv("OPENAI_MODEL", "").strip() or "gpt-5-mini"

    @function_tool
    def search_jobs(query: str) -> str:
        """Search fictional job records by role, skill, or location."""
        return search_mock_jobs(query, catalog_path=catalog_path)

    @function_tool
    def read_resume() -> str:
        """Read the candidate's local resume for specific experience evidence."""
        return read_resume_file(resume_path=resume_path)

    @function_tool
    def save_results(ctx: RunContextWrapper[JobAgentContext], content: str) -> str:
        """Save catalog job IDs and recommendation text only on user request."""
        if not ctx.context.save_allowed:
            return "Error: Saving requires an explicit request in the current message."
        return save_results_file(
            content, results_path=results_path, catalog_path=catalog_path
        )

    return Agent(
        name="Job Search Agent",
        instructions=load_instructions(),
        model=model,
        tools=[search_jobs, read_resume, save_results],
    )


def run_agent(
    user_message: str,
    history: list[TResponseInputItem] | None = None,
    *,
    agent: Agent[JobAgentContext] | None = None,
    max_turns: int = MAX_TURNS,
) -> AgentTurn:
    """Run one bounded agent turn and return its answer plus local history."""
    if not user_message.strip():
        raise ValueError("User message cannot be empty.")
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1.")

    turn_input: list[TResponseInputItem] = [
        *(history or []),
        {"role": "user", "content": user_message},
    ]
    result = Runner.run_sync(
        agent or build_agent(),
        turn_input,
        context=JobAgentContext(save_allowed=explicit_save_requested(user_message)),
        max_turns=max_turns,
        run_config=RunConfig(tracing_disabled=True),
    )
    return AgentTurn(text=str(result.final_output), history=result.to_input_list())
