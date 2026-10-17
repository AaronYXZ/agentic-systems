"""Local keyword search. Returned document text is an exact source excerpt."""

import json
import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from .identity import Identity, UserResources


@dataclass(frozen=True)
class Document:
    document_id: str
    owner: Identity
    kind: Literal["job", "company"]
    reference: str
    source: str
    updated_on: date
    text: str

    def __post_init__(self):
        if self.kind not in {"job", "company"}:
            raise ValueError("Document kind must be job or company.")
        for value in (self.document_id, self.reference, self.source, self.text):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Document fields must contain text.")


def load_documents(resources: UserResources) -> list[Document]:
    """Load the owner's catalog; source labels are never fetched."""
    path = resources.file("documents.json")
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("Document catalog must be a list.")
    documents = []
    seen = set()
    for record in records:
        owner = Identity(**record["owner"])
        if owner != resources.identity:
            raise PermissionError("Document access denied.")
        document = Document(**{
            **record, "owner": owner,
            "updated_on": date.fromisoformat(record["updated_on"]),
        })
        if document.document_id in seen:
            raise ValueError("Document IDs must be unique within an owner catalog.")
        seen.add(document.document_id)
        documents.append(document)
    return documents


def search_documents(resources: UserResources, query: str) -> list[Document]:
    """Return up to five matches, with at most 400 source characters each."""
    keywords = set(re.findall(r"\w+", query.casefold()))
    if not keywords:
        return []
    hits = []
    # shortcut: use catalog order; add ranking when retrieval evals justify it.
    for document in load_documents(resources):
        for word in re.finditer(r"\w+", document.text):
            if word.group().casefold() in keywords:
                start = word.start()
                hits.append(replace(document, text=document.text[start:start + 400]))
                break
        if len(hits) == 5:
            break
    return hits
