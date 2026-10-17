"""Deterministic SDK guardrail demonstration, not a general secret detector."""

from agents import GuardrailFunctionOutput, TResponseInputItem, input_guardrail

RESTRICTED_MARKER = "[RESTRICTED-DEMO]"


def latest_user_text(input: str | list[TResponseInputItem]) -> str:
    """Read the current text request without stripping quoted evidence."""
    if isinstance(input, str):
        return input
    for item in reversed(input):
        if item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str):
                return content
            break
    raise ValueError("Input guardrail requires a text user message.")


@input_guardrail(run_in_parallel=False)
def restricted_input(ctx, agent, input: str | list[TResponseInputItem]):
    blocked = RESTRICTED_MARKER.casefold() in latest_user_text(input).casefold()
    return GuardrailFunctionOutput(
        output_info={"reason": "restricted_marker" if blocked else "allowed"},
        tripwire_triggered=blocked,
    )
