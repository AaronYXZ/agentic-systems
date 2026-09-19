"""A deterministic LangGraph that exposes routing and state transitions.

This is intentionally not an autonomous agent. It teaches the control plane before
an LLM is added to it.
"""

import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

ACTION_VERBS = frozenset(
    {
        "analyze",
        "build",
        "calculate",
        "compare",
        "create",
        "debug",
        "design",
        "explain",
        "review",
        "summarize",
        "test",
    }
)
OBJECT_FILLERS = frozenset({"a", "an", "please", "the"})


class TriageState(TypedDict):
    request: str
    normalized_request: str
    route: Literal["answer", "clarify"]
    response: str
    audit_log: list[str]


def normalize(state: TriageState) -> dict[str, object]:
    """Normalize input and record the transition without mutating prior state."""
    normalized = " ".join(state["request"].strip().split())
    return {
        "normalized_request": normalized,
        "audit_log": [*state.get("audit_log", []), "normalize"],
    }


def choose_route(state: TriageState) -> dict[str, object]:
    """Require an explicit action and a meaningful object before proceeding."""
    tokens = re.findall(
        r"[a-z0-9]+(?:[-_][a-z0-9]+)*",
        state["normalized_request"].lower(),
    )
    if tokens[:1] == ["please"]:
        tokens = tokens[1:]

    has_action = bool(tokens) and tokens[0] in ACTION_VERBS
    has_object = any(token not in OBJECT_FILLERS for token in tokens[1:])
    route: Literal["answer", "clarify"] = (
        "answer" if has_action and has_object else "clarify"
    )
    return {
        "route": route,
        "audit_log": [*state["audit_log"], f"route:{route}"],
    }


def route_after_triage(state: TriageState) -> Literal["answer", "clarify"]:
    return state["route"]


def answer(state: TriageState) -> dict[str, object]:
    return {
        "response": f"Accepted for processing: {state['normalized_request']}",
        "audit_log": [*state["audit_log"], "answer"],
    }


def clarify(state: TriageState) -> dict[str, object]:
    return {
        "response": "Please add the desired outcome and one relevant constraint.",
        "audit_log": [*state["audit_log"], "clarify"],
    }


def build_graph():
    builder = StateGraph(TriageState)
    builder.add_node("normalize", normalize)
    builder.add_node("triage", choose_route)
    builder.add_node("answer", answer)
    builder.add_node("clarify", clarify)

    builder.add_edge(START, "normalize")
    builder.add_edge("normalize", "triage")
    builder.add_conditional_edges("triage", route_after_triage)
    builder.add_edge("answer", END)
    builder.add_edge("clarify", END)
    return builder.compile()


graph = build_graph()


def run(request: str) -> TriageState:
    return graph.invoke({"request": request, "audit_log": []})


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="A request for the graph to triage")
    args = parser.parse_args()
    print(json.dumps(run(args.request), indent=2))


if __name__ == "__main__":
    main()
