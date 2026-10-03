"""Interactive terminal loop for the single job search agent."""

import os
from collections.abc import Callable

from agents import TResponseInputItem
from dotenv import load_dotenv

from openai_agent_sdk.agent import AgentTurn, run_agent
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.filtering import CriteriaError

AgentFunction = Callable[[str, list[TResponseInputItem] | None], AgentTurn]


class ConfigurationError(Exception):
    """A safe, user-facing local setup error."""


def _run_live_turn(message: str, history: list[TResponseInputItem] | None) -> AgentTurn:
    """Check local configuration before calling the model-backed runner."""
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise ConfigurationError(
            "OPENAI_API_KEY is missing. Add it to .env and try again."
        )
    return run_agent(message, history)


def run_cli(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    agent_fn: AgentFunction = _run_live_turn,
) -> None:
    """Keep a local conversation open until the user exits."""
    history: list[TResponseInputItem] | None = None
    while True:
        try:
            message = input_fn("Job Agent > ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("Goodbye.")
            return

        if message.lower() in {"exit", "quit"}:
            output_fn("Goodbye.")
            return
        if not message:
            continue

        try:
            turn = agent_fn(message, history)
        except ConfigurationError as error:
            output_fn(f"Agent > {error}")
            continue
        except CriteriaError as error:
            output_fn(f"Agent > {error}")
            continue
        except Exception:
            output_fn(
                "Agent > Could not complete that turn. Check your API key, "
                "model, and local files, then try again."
            )
            continue

        history = turn.history
        output_fn(f"Agent > {turn.text}")


def main() -> None:
    """Run the CLI from the installed entry point or module."""
    run_cli()


if __name__ == "__main__":
    main()
