# Agentic Systems, From First Graph to Production

This repository is a step-by-step educational project for learning how to design, build, test, and reason about AI agents. It assumes you already know the basic vocabulary of LLMs, RAG, and agents, but have not yet shipped an agent to production.

Python is the implementation language. [LangChain](https://docs.langchain.com/oss/python/langchain/overview) is the default high-level agent API. [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) is the default orchestration runtime when explicit state, control flow, persistence, or human review matters.

## What you will be able to do

By the end, you should be able to:

- decide when an agent is justified and when a deterministic workflow is safer;
- implement tool use, ReAct, routing, reflection, and planning patterns;
- model short-term state and long-term memory without treating them as the same thing;
- expose tools through MCP and collaborate with remote agents through A2A;
- design multi-agent systems only when specialization adds enough value;
- evaluate hallucination, explainability, compliance, and security risks;
- ship a small agent with tests, traces, approval gates, and failure handling.

## The learning loop

Each module follows the same loop:

1. **Mental model.** Learn one distinction or pattern.
2. **Inspect.** Read the smallest working implementation.
3. **Run.** Observe concrete state and outputs.
4. **Change.** Complete a constrained exercise.
5. **Evaluate.** Add a test or failure case before moving on.
6. **Reflect.** Explain the design tradeoff in your own words.

Lessons live in `lessons/`. Compressed references live in `reference/`. Runnable implementations live in `src/agentic_systems/`. Tests are part of the curriculum, not cleanup work.

## Roadmap

| Stage | Build | Core ideas | Exit evidence |
|---|---|---|---|
| 0. Orientation | A deterministic triage graph | Agent vs workflow, nodes, edges, state | Predict the path before running it |
| 1. Tool use | A calculator agent | Tool contracts, ReAct loop, termination | Test tool selection and final answers |
| 2. State and memory | A resumable assistant | State, checkpoints, thread memory, stores | Resume an interrupted run safely |
| 3. Design patterns | A support workflow | Router, reflection, planning, fallbacks | Compare patterns against one task |
| 4. Grounded answers | A small RAG agent | Retrieval, citations, abstention, evaluation | Measure unsupported claims |
| 5. MCP | A local MCP tool server and client | Hosts, clients, servers, tools, resources, prompts | Connect without custom glue code |
| 6. Multi-agent | A supervisor with specialists | Delegation, handoffs, shared context, cost | Prove specialization beats one agent |
| 7. A2A | Two independently served agents | Agent cards, tasks, messages, artifacts | Complete a cross-agent task |
| 8. Responsible AI | A guarded workflow | Hallucination, explainability, compliance, security | Threat model and red-team tests pass |
| 9. Capstone | A production-minded research agent | Observability, evaluation, latency, cost, deployment | Demo plus operational runbook |

The ordering is intentional. MCP is easier after tool contracts are clear. A2A is easier after multi-agent responsibilities are clear. Multi-agent work comes late because multiple weak agents usually multiply ambiguity, latency, and failure modes.

## Framework rules for this project

- Start with plain Python for domain logic.
- Use LangChain for model interfaces, messages, tools, structured output, retrieval components, and fast agent construction.
- Use LangGraph when the workflow needs explicit state, branching, loops, persistence, interrupts, or multiple agents.
- Keep business logic outside graph nodes where possible so it remains easy to test.
- Never let an LLM silently authorize irreversible or high-impact actions.
- Treat prompts, tool schemas, state schemas, and evaluation datasets as versioned code.

## Quick start

This project uses [uv](https://docs.astral.sh/uv/) for reproducible Python environments.

```bash
uv sync
uv run first-graph "Please explain LangGraph state"
uv run pytest
```

The first graph makes no model call and requires no API key. It should return a route, a response, and an audit trail.

When you are ready for the LLM-backed tool-use example:

```bash
cp .env.example .env
# Add OPENAI_API_KEY to .env
uv run first-agent "What is 17 multiplied by 23?"
```

You can change `AGENT_MODEL` in `.env`. The example explicitly uses OpenAI's Responses API so reasoning models can call function tools. `OPENAI_TIMEOUT_SECONDS` and `OPENAI_MAX_RETRIES` control request behavior. Keeping these settings configurable prevents the learning material from depending on one model release or one network assumption.

## Start here

Open [Lesson 0001: State Before Autonomy](lessons/0001-state-before-autonomy.html). Do its prediction exercise, then run `first-graph` and compare the trace with your prediction.

After that, inspect these files in order:

1. `src/agentic_systems/first_graph.py`
2. `tests/test_first_graph.py`
3. `src/agentic_systems/first_agent.py`
4. `reference/agent-system-map.html`

## Repository map

```text
.
├── lessons/             Short, focused learning units
├── reference/           Durable cheat sheets and concept maps
├── learning-records/    Demonstrated knowledge and prior experience
├── assets/              Shared lesson styles and interactions
├── src/agentic_systems/ Runnable Python examples
├── tests/               Behavioral checks and evaluation seeds
├── MISSION.md           The outcome steering the curriculum
├── RESOURCES.md         Curated primary sources
└── NOTES.md             Teaching preferences and working assumptions
```

## Progress discipline

Do not finish the whole roadmap in one pass. Work one stage at a time and require observable evidence before adding complexity. The current stage is **0. Orientation**. The next repository change should follow your results from Lesson 0001, not merely add more concepts.
