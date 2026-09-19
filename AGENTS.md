# Project Development Instructions

## Purpose

This is an educational Python project for learning production-minded agent development. Every change should teach one clear concept, produce a runnable artifact, and include observable exit evidence.

## Default stack

- Use Python for implementation and examples.
- Use LangChain for model interfaces, messages, tools, structured output, retrieval components, and simple agents.
- Use LangGraph for explicit state, branching, loops, persistence, interrupts, durable execution, and multi-agent orchestration.
- Use plain Python when framework machinery does not improve clarity or behavior.
- Keep model and provider choices configurable through environment variables.

## Design rules

- Introduce one major concept per lesson.
- Separate deterministic domain logic from model-controlled decisions.
- Define typed state and tool contracts.
- Add behavioral tests before adding more autonomy.
- Make routing, retries, termination, and human approval explicit.
- Treat prompts, schemas, evaluation cases, and safety policies as versioned code.
- Do not introduce multiple agents unless an evaluation shows an advantage over one agent.
- Do not allow models to authorize irreversible or high-impact actions without an external policy check or human approval.

## Quality checks

Run these before considering a change complete:

```bash
uv run ruff check .
uv run pytest
```

For model-backed features, keep most tests deterministic and add clearly marked integration tests for real API calls.

