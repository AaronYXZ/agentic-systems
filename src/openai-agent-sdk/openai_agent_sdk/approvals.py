"""In-memory approval binding. No credentials or application text in errors."""

import json
from dataclasses import dataclass
from time import monotonic

from agents.exceptions import AgentsException

from .identity import Identity

APPROVAL_TIMEOUT_SECONDS = 300


class ApprovalError(AgentsException):
    """A review expired or no longer matches its proposed action."""


def canonical_arguments(arguments: str) -> str:
    return json.dumps(json.loads(arguments), sort_keys=True)


@dataclass(frozen=True)
class ApprovalRequest:
    identity: Identity
    run_id: str
    call_id: str
    tool_name: str
    arguments: str
    expires_at: float

    def check(self, identity, run_id, call_id, tool_name, arguments):
        if (identity, run_id, call_id, tool_name, canonical_arguments(arguments)) != (
            self.identity, self.run_id, self.call_id, self.tool_name, self.arguments
        ):
            raise ApprovalError("Approval does not match this owner, run, or action.")
        if monotonic() >= self.expires_at:
            raise ApprovalError("Approval expired. Request a fresh application review.")


@dataclass(frozen=True)
class ApprovalDecision:
    request: ApprovalRequest
    approved: bool
