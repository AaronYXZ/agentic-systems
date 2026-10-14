# Job Search Agent: LangChain and LangGraph Migration Plan

### Purpose and status

Planning only, based on the implementation inspected on 2026-10-09.
No dependencies or runtime behavior change as part of this document.

Migrate the current single Job Search Agent from the OpenAI Agents SDK to
LangChain model, message, and tool interfaces with LangGraph orchestration.
Preserve observable behavior before adding persistence, MCP, or more autonomy.

Current baseline: V1 CLI and V2 Steps 1 through 7 have offline implementations.
Live acceptance remains pending. V2-b MCP and V3 persistence are plans, not
existing features to port. Unrelated root-project MCP examples are outside this scope.

### Target architecture

- Keep deterministic domain functions in plain Python.
- Use LangChain for chat models, messages, tool schemas, and model tool calls.
- Use an explicit LangGraph `StateGraph` for state, routing, and termination.
- Keep one agent. The migration does not require multiple agents.
- Keep the CLI independent from the framework through a small service interface.

LangChain's `create_agent` is a simpler alternative for a standard tool loop.
It runs on LangGraph. An explicit graph is the proposed target here because
the lesson requires visible turn counting, state updates, and policy boundaries.
Do not implement both orchestration approaches in the first migration.
[LangChain overview](https://www.langchain.com/oss-overview),
[create_agent reference](https://reference.langchain.com/python/langchain/agents/factory/create_agent).

Proposed flow:

```text
CLI -> turn initialization -> model -> tool dispatch -> model -> final answer
                              |                         |
                              +-> budget check          +-> budget check
```

The model selects among registered tools. Deterministic code validates tool
names and arguments, enforces save restrictions, and applies domain rules.
The tool dispatcher must preserve call IDs in tool-result messages.

### 1. General migration steps

#### Step 1. Freeze behavior and define parity cases

- Run the isolated project and root checks to establish a fresh baseline.
- Record tool names, schemas, output strings, provider errors, and CLI behavior.
- Reuse fixtures for mock search, JSearch normalization, filters, matching,
  deduplication, resume reading, and duplicate-safe saving.
- Add adapter-level behavioral cases before replacing orchestration. Cover
  denied saves, same-turn fit checks, repeated tool calls, and failed-run traces.
- Exit evidence: an offline parity matrix distinguishes current guarantees
  from pending live acceptance and future capabilities.

#### Step 2. Isolate framework-independent functions

- Reuse `contracts.py`, `catalog.py`, `jsearch.py`, `filtering.py`,
  `matching.py`, `deduplication.py`, and file tool functions.
- Keep `explicit_save_requested` and `parse_criteria` deterministic.
- Extract shared prompt loading and service result contracts only where needed.
  Avoid copying domain implementations into a second package.
- Keep the SDK implementation available while the new adapter is verified.
- Exit evidence: domain tests run without importing either agent framework.

#### Step 3. Define state, context, and messages

- Use typed graph state with messages, searched jobs, and a model-call counter.
  Specify reducers for accumulated messages and any concurrent updates.
- Put immutable per-invocation values in runtime context: current save
  permission, user-owned criteria, selected provider, and configured file paths.
- Keep secrets and transient trace sinks outside checkpointed state.
- Initialize permission and criteria from each current user message. Reset
  searched jobs and counters for every new user turn, even when history persists.
- Initially preserve manual history replay using LangChain messages. Convert
  SDK history explicitly if importing an old conversation is required.
- Exit evidence: follow-up history survives while an earlier save request or
  searched job cannot authorize a new turn.

#### Step 4. Implement LangChain tool wrappers

- Define `@tool` wrappers with the same model-facing inputs. Runtime context
  must remain absent from schemas sent to the model.
- Preserve three mock tools and add `assess_fit` only in JSearch mode.
- Make `search_jobs` return the current output and update searched-job state
  only after successful normalization, filtering, and deduplication.
- Use `Command(update=...)` with a correlated `ToolMessage` for graph state
  updates where appropriate. Do not mutate hidden shared dictionaries as a
  substitute for declared graph updates.
- Enforce save permission and the live-save restriction inside the tool path.
  Keep `assess_fit` dependent on a job searched in the current turn.
- Exit evidence: schemas, tool results, denials, and evidence match offline cases.
[LangChain tools and runtime context](https://docs.langchain.com/oss/python/langchain/tools).

#### Step 5. Implement the bounded graph loop

- Load the versioned prompt and JSearch instructions into each model request.
- Configure a LangChain chat model through environment variables. Start with
  the existing provider and model to avoid changing two variables at once.
- Bind only the selected tools to the model. Route a final response to `END`
  and tool calls to a deterministic dispatcher.
- Execute tool calls sequentially for the first migration. This prevents fit
  checks racing search state and avoids concurrent writes. Define the policy
  for multiple calls in one response, including invalid ordering.
- Count model invocations explicitly. Preserve the existing eight-turn default
  and SDK limit behavior through tests. Use `recursion_limit` as an additional
  graph safety bound, not as a numeric replacement for `max_turns`.
- Preserve reported `Error:` results and define handling for raised exceptions,
  unknown tools, and malformed arguments. Do not silently enable retries.
- Exit evidence: scripted responses exercise the real graph and stop at the
  intended model-call limit.
[LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).

#### Step 6. Preserve tracing and the CLI service contract

- Return an application result with final text, replay-ready history, and local
  tool traces. Retain the current CLI exit paths and per-turn recovery behavior.
- Replace SDK-specific tracing context with tool name and `tool_call_id` from
  the dispatcher or `ToolRuntime`. Time the complete execution, including awaits.
- Retain metadata-only records, `ToolReportedError`, exception class names,
  failed-run delivery, and trace-sink failure isolation.
- Keep local tracing opt-in. Introduce a separately named export setting for
  any LangSmith integration. Do not silently reinterpret SDK export settings.
- Default external export off. Check redaction before enabling it. General
  framework callbacks can include inputs and outputs and are not automatically
  equivalent to the current metadata-only policy.
- Exit evidence: call IDs correlate with tool messages, and sensitive fixtures
  never appear in local records or approved export payloads.
[SDK tracing baseline](https://openai.github.io/openai-agents-python/tracing/).

#### Step 7. Verify parity and select the migrated runtime

- Adapt `ScriptedModel` into a tool-call-capable fake chat model or injected
  model node. Keep most tests offline and exercise actual graph execution.
- Run the same domain fixtures through both framework adapters. Compare job
  identities, exclusions, evidence, file effects, error behavior, and history.
  Do not require identical natural-language output from live models.
- Verify greeting, missing resume, denied save, explicit mock save, unsupported
  live save, follow-up, exhaustion, and recovery after a failed turn.
- Run isolated tests and root Ruff and pytest checks. Perform a separately
  enabled live acceptance run before making the migrated runtime the default.
- Exit evidence: parity results and known differences are documented. Remove
  SDK dependencies only after SDK code and tests are no longer required.

#### Step 8. Add future capabilities separately

- MCP: select either V2-b server option. Retain the narrow `search_jobs` wrapper
  and validation adapter. Use one explicit client session for the CLI lifetime.
- Persistence: add a checkpointer and stable `thread_id` after parity. Choose
  checkpoint continuation or manual history replay. Do not duplicate both.
- Memory: use a namespaced Store for selected cross-conversation information.
  Keep job database records separate from execution checkpoints.
- HITL: use `interrupt()` and `Command(resume=...)`, or agent HITL middleware
  when using `create_agent`. Retain deterministic policy enforcement. Make
  pre-interrupt side effects safe for replay because interrupted nodes restart.
- FastAPI and persistent workers: expose the verified service, then add durable
  execution, cancellation, ownership checks, budgets, and idempotent writes.
- Sandbox: enforce file, network, and resource restrictions outside the model
  and graph. Neither framework alone supplies this application policy.
- Exit evidence: each added concept has independent behavioral tests and
  observable recovery or approval evidence.
[MCP integration](https://docs.langchain.com/oss/python/langchain/mcp),
[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence),
[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).

### 2. Current implementation to framework mapping

Exact means a similar primitive exists, not that behavior or serialization is
identical. Closest means additional application code is required. Reuse means
the current plain Python implementation should remain.

| Current SDK implementation | LangChain / LangGraph counterpart | Match and migration requirement |
|---|---|---|
| `Agent(name, instructions, model, tools)` in `build_agent` | `create_agent(model, tools, system_prompt)` or explicit `StateGraph` | Closest. Proposed graph makes orchestration explicit; keep name as metadata. |
| SDK `Model` and configured model string | `BaseChatModel`, `init_chat_model`, or provider integration such as `ChatOpenAI` | Closest. Check tool-calling support and provider options; preserve environment configuration. |
| `function_tool` | LangChain `@tool` / `StructuredTool` | Similar primitive. Compare generated schemas and validation failures. |
| `ToolContext[JobAgentContext]` | `ToolRuntime`, runtime context, and graph state | Closest. Split immutable invocation context from mutable execution state. |
| `ctx.tool_name`, `ctx.tool_call_id` | Registered tool name and `runtime.tool_call_id` or dispatcher call ID | Similar identity mechanism. Do not substitute a tracing run ID for a model call ID. |
| `save_allowed` and `criteria` | Typed runtime context supplied at invocation | Closest. Recompute from the current user message; never accept model-supplied permission. |
| `searched_jobs` | Custom typed state field, updated by `Command` or dispatcher node | Closest. Reset each user turn and define update behavior. |
| `Runner.run_sync` | Compiled graph `.invoke()` | Similar entry point. Implement loop and termination semantics explicitly. |
| Async `Runner.run` (not currently used) | Compiled graph `.ainvoke()` | Future counterpart for MCP and service execution; preserve lifecycle cleanup. |
| `max_turns=8`, `MaxTurnsExceeded` | Explicit model-call counter plus graph routing; `recursion_limit` / `GraphRecursionError` as backup | No exact match. Graph steps and SDK turns count different events. |
| `TResponseInputItem` and `to_input_list()` | `HumanMessage`, `AIMessage`, `ToolMessage` and message state | Closest. Convert call arguments, IDs, results, and message content explicitly. |
| `AgentTurn.text`, `.history`, `.tool_traces` | Application result dataclass built from graph output | No built-in exact result contract. Preserve the CLI-facing adapter. |
| `load_instructions` and versioned prompt | System message or `create_agent` system prompt | Reuse prompt source and provider-specific additions. |
| `search_jobs(query)` wrapper | LangChain tool plus provider adapter and state update | Reuse domain search, normalization, filters, and deduplication. |
| `read_resume()` | LangChain tool around `read_resume_file` | Reuse local path and error behavior. |
| `save_results(content)` | LangChain tool with deterministic policy check | Reuse explicit-save gate and duplicate-safe file logic; retain live-save denial. |
| `assess_fit(job_id)` | LangChain tool reading searched-job state | Reuse evidence matcher and internal resume read; enforce same-turn dependency. |
| `LiveJobPosting`, `SearchCriteria` | Existing typed domain contracts; optional Pydantic boundary validation | Reuse. Agent structured output is not a replacement for provider validation. |
| `ToolTrace`, `traced_call`, stderr sink | Application tool wrapper or dispatcher instrumentation | Reuse record format. Adapt invocation context and async timing. |
| `RunConfig` tracing switches | Invocation callbacks / metadata and optional LangSmith export | Closest. No direct equivalent guarantees the same sensitive-data exclusion. |
| Injected SDK `ScriptedModel` | Injected model node or custom fake `BaseChatModel` emitting `AIMessage.tool_calls` | Closest. Port scripted loop tests rather than mocking away orchestration. |
| `run_cli` and injected `agent_fn` | Same CLI with a graph-backed service adapter | Reuse input, output, history, and recovery tests. |
| SDK MCP support, planned V2-b adapter | `langchain-mcp-adapters`, explicit client session, and owned provider wrapper | Planned only. Keep discovered raw tools behind application validation. |
| V3 history persistence, not implemented | LangGraph checkpointer with `thread_id` | Closest future capability. Checkpoints do not replace the jobs/searches database. |
| Future cross-run preferences | LangGraph Store with user-scoped namespaces | Closest future capability. Define consent, retention, and deletion. |
| Current save gate / future approval pause | Existing policy check / `interrupt` or `HumanInTheLoopMiddleware` | Different roles. A pause is not authorization; validate approval before execution. |

Mapping references: [Tools](https://docs.langchain.com/oss/python/langchain/tools),
[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[Prebuilt middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in).

### 3. Framework comparison for this project

The target stack contains two layers: LangChain supplies common model and tool
interfaces. LangGraph supplies execution and state control. Compare that stack
with the SDK, rather than treating LangChain and LangGraph as interchangeable.

| Area | OpenAI Agents SDK | LangChain + LangGraph | Project implication |
|---|---|---|---|
| Simple single-agent tool loop | `Agent` and `Runner` manage the loop | `create_agent` manages a loop; custom graphs expose its parts | SDK is already sufficient for current CLI behavior. |
| Explicit orchestration | Runner options and application code | Nodes, edges, typed state, and conditional routing | Graph target makes state transitions easier to teach and inspect. |
| Model interfaces | OpenAI-first, with other model integrations | Common chat model interfaces and provider packages | More provider choices still require behavior and tool-schema tests. |
| Tool schemas and context | Function tools and local run/tool context | Tools, runtime context, graph state, and commands | Context migration requires deliberate state ownership. |
| Conversation continuation | Manual history, sessions, and API conversation options | Manual messages or checkpointed threads | Use one continuation strategy per conversation. |
| Persistence and recovery | Sessions provide history; durable execution needs additional integration | Checkpointers and graph execution support recovery patterns | Neither storage nor checkpointing makes repeated writes safe automatically. |
| Human approval | SDK supports approval workflows; current project uses a deterministic gate | Interrupt/resume and optional HITL middleware | Both require approval identity, scope, expiry, and policy checks. |
| MCP | Built-in SDK server integration | MCP adapter package and session management | Either stack must preserve the owned search boundary. |
| Observability | Built-in SDK tracing and custom local traces | Callbacks, graph streaming, and optional LangSmith tracing | Keep metadata-only local evidence independent of external export. |
| Operational complexity | Smaller current implementation and fewer integration layers | More explicit contracts, reducers, and execution choices | Migrate for needed control, not to increase framework usage. |
| FastAPI and background work | Can be wrapped by an application service | Can be wrapped by an application service | Neither automatically provides auth, queues, scheduling, or a sandbox. |

This comparison is an architectural assessment for the inspected project, not
a benchmark. The SDK remains a valid choice for the existing single-agent CLI.
The proposed migration serves the project's explicit-state learning goals and
future recovery and approval requirements. It does not guarantee better output,
lower latency, or lower cost.
[SDK overview](https://openai.github.io/openai-agents-python/),
[SDK running agents](https://openai.github.io/openai-agents-python/running_agents/),
[LangChain stack overview](https://www.langchain.com/oss-overview).

### Completion criteria

- Existing deterministic domain behavior remains unchanged.
- Three mock tools and four JSearch tools keep their model-facing contracts.
- Save permission and searched jobs remain scoped to the current user turn.
- Model invocations stop at the tested budget; CLI errors remain recoverable.
- Local tracing preserves correlation, failure delivery, and sensitive-data exclusion.
- Offline parity checks pass in both adapters before default selection changes.
- A separately enabled live run checks observed provider and model behavior.
- Exact dependencies, Python compatibility, and signatures are checked and locked
  during implementation. Documentation consulted today is not a version lock.
