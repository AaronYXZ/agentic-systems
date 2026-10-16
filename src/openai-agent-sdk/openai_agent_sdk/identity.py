"""Trusted local identities and owner-scoped files. This is not authentication."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agents import SQLiteSession

from .contracts import PROJECT_ROOT


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str

    def __post_init__(self):
        for field in ("tenant_id", "user_id"):
            value = getattr(self, field)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
                raise ValueError("Identity IDs must use letters, digits, '_' or '-'.")
            # Case-insensitive IDs avoid file aliases on macOS and Windows.
            object.__setattr__(self, field, value.lower())


class OwnedSession(SQLiteSession):
    """A session capability created by trusted application code."""

    def __init__(self, identity: Identity, conversation: str, db_path: Path):
        self.owner = identity
        # Structured encoding prevents collisions between owner and conversation IDs.
        key = json.dumps([identity.tenant_id, identity.user_id, conversation])
        super().__init__(key, db_path)

    def check_owner(self, identity: Identity | None) -> None:
        if identity != self.owner:
            raise PermissionError("Conversation access denied.")


@dataclass(frozen=True)
class UserResources:
    identity: Identity
    root: Path = PROJECT_ROOT / "data" / "users"

    def file(self, name: str) -> Path:
        """Resolve private paths without following links into another owner's data."""
        directory = (
            self.root.resolve() / self.identity.tenant_id / self.identity.user_id
        )
        if directory.resolve() != directory:
            raise PermissionError("Owner directory must not be a symbolic link.")
        path = (directory / name).resolve()
        if not path.is_relative_to(directory):
            raise PermissionError("Resource access denied.")
        return path

    def open_session(self, conversation: str) -> OwnedSession:
        if not conversation.strip():
            raise ValueError("Conversation ID cannot be blank.")
        path = self.file("conversations.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        return OwnedSession(self.identity, conversation, path)

    async def review(self, session: OwnedSession) -> list:
        session.check_owner(self.identity)
        return await session.get_items(limit=-1)

    async def reset(self, session: OwnedSession) -> None:
        session.check_owner(self.identity)
        await session.clear_session()
