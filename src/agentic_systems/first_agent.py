"""A minimal LangChain tool-using agent.

This example makes model API calls. Configure OPENAI_API_KEY and AGENT_MODEL in
.env before running it.
"""

import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI


@tool
def multiply(left: int, right: int) -> int:
    """Multiply two integers exactly."""
    return left * right


def _model_name() -> str:
    """Read the configured model and remove LangChain's optional provider prefix."""
    configured = (
        os.getenv("AGENT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gpt-5-mini"
    )
    return configured.removeprefix("openai:")


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _non_negative_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def build_model() -> ChatOpenAI:
    """Build an OpenAI model configured for reasoning with function tools."""
    load_dotenv()
    return ChatOpenAI(
        model=_model_name(),
        use_responses_api=True,
        timeout=_positive_float("OPENAI_TIMEOUT_SECONDS", 90),
        max_retries=_non_negative_int("OPENAI_MAX_RETRIES", 2),
    )


def build_agent():
    return create_agent(
        model=build_model(),
        tools=[multiply],
        system_prompt=(
            "You are a careful assistant. Use the multiply tool for integer "
            "multiplication. Briefly state when you used a tool."
        ),
    )


def run(request: str) -> str:
    result = build_agent().invoke(
        {"messages": [{"role": "user", "content": request}]}
    )
    return result["messages"][-1].text


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="A request for the agent")
    args = parser.parse_args()
    print(run(args.request))


if __name__ == "__main__":
    main()
