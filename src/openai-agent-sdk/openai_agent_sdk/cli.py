"""Interactive terminal loop for the single job search agent."""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from contextlib import closing, nullcontext
from dataclasses import asdict
from functools import partial
from pathlib import Path

from agents import SQLiteSession, TResponseInputItem
from dotenv import load_dotenv

from openai_agent_sdk.agent import (
    AgentTurn,
    SessionRecoveryError,
    build_agent,
    run_agent,
    run_agent_async,
)
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.filtering import CriteriaError
from openai_agent_sdk.openwebninja_mcp import OpenWebNinjaAdapter, open_search_session
from openai_agent_sdk.tool_tracing import ToolTrace

AgentFunction = Callable[[str, list[TResponseInputItem] | None], AgentTurn]


class ConfigurationError(Exception):
    """A safe, user-facing local setup error."""


def _run_live_turn(
    message: str, history: list[TResponseInputItem] | None,
    *, session: SQLiteSession | None = None,
) -> AgentTurn:
    """Check local configuration before calling the model-backed runner."""
    load_dotenv(PROJECT_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise ConfigurationError(
            "OPENAI_API_KEY is missing. Add it to .env and try again."
        )
    local_tracing = os.getenv("JOB_AGENT_LOCAL_TRACING", "false").lower() == "true"
    return run_agent(
        message, history, trace_sink=_print_trace if local_tracing else None,
        **({"session": session} if session is not None else {}),
    )


def _print_trace(record: ToolTrace) -> None:
    """Print allowlisted metadata to stderr without tool content."""
    print(json.dumps(asdict(record)), file=sys.stderr)


def run_cli(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    agent_fn: AgentFunction = _run_live_turn,
    persistent: bool = False,
) -> None:
    """Run the shared loop with the existing synchronous agent callback."""
    async def turn(message, history):
        return await asyncio.to_thread(agent_fn, message, history)

    asyncio.run(_run_cli_loop(input_fn, output_fn, turn, persistent=persistent))


async def _run_cli_loop(
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    agent_fn: Callable[
        [str, list[TResponseInputItem] | None], Awaitable[AgentTurn]
    ],
    *, persistent: bool = False,
) -> None:
    history: list[TResponseInputItem] | None = None
    while True:
        try:
            # Read on the main thread so Ctrl-C cannot leave a blocked input worker.
            message = input_fn("Job Agent > ").strip()
            if message.lower() in {"exit", "quit"}:
                break
            if not message:
                continue
            try:
                turn = await agent_fn(message, history)
            except (sqlite3.Error, SessionRecoveryError):
                output_fn(
                    "Agent > Conversation storage or recovery failed. "
                    "Stop and check the database before restarting."
                )
                break
            except (ConfigurationError, CriteriaError) as error:
                output_fn(f"Agent > {error}")
                continue
            except Exception:
                output_fn(
                    "Agent > Could not complete that turn. Check your API key, "
                    "model, and local files, then try again."
                )
                continue
            if not persistent:
                history = turn.history
            output_fn(f"Agent > {turn.text}")
        except (EOFError, KeyboardInterrupt):
            break
    output_fn("Goodbye.")


async def run_mcp_cli(
    *,
    agent_fn: Callable[
        [str, list[TResponseInputItem] | None, OpenWebNinjaAdapter],
        Awaitable[AgentTurn],
    ],
    api_key: str,
    persistent: bool = False,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Keep one connection for all turns. Provider routing is supplied by the caller."""
    if not api_key.strip():
        raise ConfigurationError("OPENWEBNINJA_API_KEY is missing.")
    async with open_search_session(api_key) as adapter:
        async def turn(message, history):
            return await agent_fn(message, history, adapter)

        await _run_cli_loop(input_fn, output_fn, turn, persistent=persistent)


def main(argv: list[str] | None = None) -> None:
    """Run an ephemeral CLI, or explicitly select a persistent conversation."""
    parser = argparse.ArgumentParser(description="Local job search agent")
    parser.add_argument("--session", help="Conversation ID to create or resume")
    parser.add_argument("--session-db", type=Path, help="SQLite database path")
    args = parser.parse_args(argv)
    if args.session is not None and not args.session.strip():
        parser.error("--session must not be blank")
    if args.session_db is not None and args.session is None:
        parser.error("--session-db requires --session")
    load_dotenv(PROJECT_ROOT / ".env")
    db_path = args.session_db or PROJECT_ROOT / "data" / "conversations.sqlite3"
    try:
        manager = (closing(SQLiteSession(args.session, db_path))
                   if args.session is not None else nullcontext(None))
        with manager as session:
            if session is not None:
                print(f"Conversation: {args.session} | Database: {db_path.resolve()}")
            if os.getenv("JOB_SEARCH_PROVIDER", "mock").strip().lower() != "mcp":
                if session is None:
                    run_cli()
                else:
                    run_cli(agent_fn=partial(_run_live_turn, session=session),
                            persistent=True)
                return
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise ConfigurationError("OPENAI_API_KEY is missing. Add it to .env.")

            async def turn(message, history, adapter):
                local_tracing = os.getenv("JOB_AGENT_LOCAL_TRACING", "false").lower()
                return await run_agent_async(
                    message, history, session=session,
                    agent=build_agent(provider="mcp", mcp_adapter=adapter),
                    trace_sink=_print_trace if local_tracing == "true" else None,
                )

            asyncio.run(run_mcp_cli(
                api_key=os.getenv("OPENWEBNINJA_API_KEY", ""), agent_fn=turn,
                **({"persistent": True} if session is not None else {}),
            ))
    except (sqlite3.Error, OSError):
        print("Conversation database could not be opened or closed. "
              "Check its path and permissions.", file=sys.stderr)
    except ConfigurationError as error:
        print(error, file=sys.stderr)
    except KeyboardInterrupt:
        print("Goodbye.")


if __name__ == "__main__":
    main()
