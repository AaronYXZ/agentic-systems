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
from time import monotonic

from agents import SQLiteSession, TResponseInputItem
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
)
from dotenv import load_dotenv

from openai_agent_sdk.agent import (
    AgentTurn,
    SessionRecoveryError,
    build_agent,
    run_agent,
    run_agent_async,
)
from openai_agent_sdk.approvals import ApprovalDecision, ApprovalError, ApprovalRequest
from openai_agent_sdk.contracts import PROJECT_ROOT
from openai_agent_sdk.filtering import CriteriaError
from openai_agent_sdk.identity import Identity, UserResources
from openai_agent_sdk.openwebninja_mcp import OpenWebNinjaAdapter, open_search_session
from openai_agent_sdk.tool_tracing import ToolTrace

AgentFunction = Callable[[str, list[TResponseInputItem] | None], AgentTurn]


class ConfigurationError(Exception):
    """A safe, user-facing local setup error."""


def _run_live_turn(
    message: str, history: list[TResponseInputItem] | None,
    *, session: SQLiteSession | None = None,
    resources: UserResources | None = None,
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
        **({"identity": resources.identity, "agent": build_agent(resources=resources)}
           if resources is not None else {}),
    )


def _print_trace(record: ToolTrace) -> None:
    """Print allowlisted metadata to stderr without tool content."""
    print(json.dumps(asdict(record)), file=sys.stderr)


def request_approval(
    pending: ApprovalRequest, *, input_fn=input, output_fn=print,
) -> ApprovalDecision:
    """Review one exact tool call. Only an explicit approve decision permits it."""
    if pending.tool_name != "submit_application":
        return ApprovalDecision(pending, False)
    output_fn("Approval required: simulated application (no real submission).")
    output_fn(f"Owner: {pending.identity.tenant_id}/{pending.identity.user_id}")
    output_fn(f"Expires in {max(0, int(pending.expires_at - monotonic()))} seconds.")
    output_fn(json.dumps(json.loads(pending.arguments), indent=2))
    while True:
        try:
            decision = input_fn("Type approve or reject: ").strip().casefold()
        except (EOFError, KeyboardInterrupt):
            return ApprovalDecision(pending, False)
        if decision in {"approve", "reject"}:
            return ApprovalDecision(pending, decision == "approve")
        output_fn("Enter approve or reject. Nothing has been submitted.")


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
            except (InputGuardrailTripwireTriggered,
                    OutputGuardrailTripwireTriggered) as error:
                stage = ("input" if isinstance(error, InputGuardrailTripwireTriggered)
                         else "output")
                if os.getenv("JOB_AGENT_LOCAL_TRACING", "false").lower() == "true":
                    print(json.dumps({"event": "guardrail_rejected", "stage": stage,
                                      "reason": "policy_tripwire"}), file=sys.stderr)
                output_fn(
                    "Agent > Input rejected by policy. Revise your request."
                    if stage == "input" else
                    "Agent > Response withheld by policy. "
                    "Earlier tool actions may already have completed."
                )
                continue
            except ApprovalError as error:
                output_fn(f"Agent > {error}")
                continue
            except PermissionError:
                output_fn(
                    "Agent > Access denied. Stop and check the selected identity."
                )
                break
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
    parser.add_argument("--tenant", help="Synthetic local tenant ID")
    parser.add_argument("--user", help="Synthetic local user ID (not authentication)")
    parser.add_argument("--approvals", action="store_true",
                        help="Enable human-approved local application simulations")
    args = parser.parse_args(argv)
    if args.session is not None and not args.session.strip():
        parser.error("--session must not be blank")
    if args.session_db is not None and args.session is None:
        parser.error("--session-db requires --session")
    if (args.tenant is None) != (args.user is None):
        parser.error("--tenant and --user must be supplied together")
    resources = None
    if args.user is not None:
        if args.session is None or args.session_db is not None:
            parser.error("Identity mode requires --session and disallows --session-db")
        try:
            resources = UserResources(Identity(args.tenant, args.user))
        except ValueError as error:
            parser.error(str(error))
    if args.approvals and resources is None:
        parser.error("--approvals requires --tenant, --user, and --session")
    load_dotenv(PROJECT_ROOT / ".env")
    if (args.approvals and
            os.getenv("JOB_SEARCH_PROVIDER", "mock").strip().lower() != "mock"):
        parser.error("--approvals requires JOB_SEARCH_PROVIDER=mock")
    db_path = args.session_db or PROJECT_ROOT / "data" / "conversations.sqlite3"
    try:
        if resources is not None:
            manager = closing(resources.open_session(args.session))
            db_path = resources.file("conversations.sqlite3")
            print(f"Local identity: {resources.identity.tenant_id}/"
                  f"{resources.identity.user_id} (not authenticated)")
        else:
            manager = (closing(SQLiteSession(args.session, db_path))
                       if args.session is not None else nullcontext(None))
        with manager as session:
            if session is not None:
                print(f"Conversation: {args.session} | Database: {db_path.resolve()}")
            if args.approvals:
                if not os.getenv("OPENAI_API_KEY", "").strip():
                    raise ConfigurationError("Set OPENAI_API_KEY in .env.")
                agent = build_agent(resources=resources, provider="mock",
                                    enable_submissions=True)

                async def approved_turn(message, history):
                    return await run_agent_async(
                        message, session=session, identity=resources.identity,
                        agent=agent, approval_handler=partial(
                            request_approval, input_fn=input, output_fn=print),
                        trace_sink=(_print_trace if os.getenv(
                            "JOB_AGENT_LOCAL_TRACING", "false").lower() == "true"
                            else None),
                    )

                asyncio.run(_run_cli_loop(input, print, approved_turn, persistent=True))
                return
            if os.getenv("JOB_SEARCH_PROVIDER", "mock").strip().lower() != "mcp":
                if session is None:
                    run_cli()
                else:
                    options = {"resources": resources} if resources is not None else {}
                    run_cli(
                        agent_fn=partial(_run_live_turn, session=session, **options),
                        persistent=True,
                    )
                return
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise ConfigurationError("OPENAI_API_KEY is missing. Add it to .env.")

            async def turn(message, history, adapter):
                local_tracing = os.getenv("JOB_AGENT_LOCAL_TRACING", "false").lower()
                return await run_agent_async(
                    message, history, session=session,
                    identity=resources.identity if resources is not None else None,
                    agent=build_agent(provider="mcp", mcp_adapter=adapter,
                                      resources=resources),
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
