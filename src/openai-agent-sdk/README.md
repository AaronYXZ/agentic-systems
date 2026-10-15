# Job Search Agent, OpenAI Agents SDK

This is an installable Python project for the job search agent described
in [development_plan.md](development_plan.md). Its importable package is
`openai_agent_sdk`. Its single agent is in `openai_agent_sdk.agent`. The
interactive CLI is in `openai_agent_sdk.cli`.

The fictional catalog is in `data/jobs.json`. By default, the agent searches
mock jobs, reads a resume, and can save selected mock recommendations. V2
Steps 1–7 add opt-in JSearch search, filtering, fit checks, in-memory
deduplication, and tool tracing. Live-job saving is not supported yet.

### Requirements

- Python 3.10 or newer. The setup below uses Python 3.13 to match the parent
  course project.
- `uv` for creating the environment and installing dependencies.
- An OpenAI API key for real agent runs. Deterministic tests need no key.

### Setup

Run these commands from `src/openai-agent-sdk/`:

```bash
uv venv --python 3.13 .venv
uv sync
source .venv/bin/activate
cp .env.example .env
cp data/resume.md.example data/resume.md
```

Add your key to `.env` before running the agent. `OPENAI_MODEL` selects its
model. Use only a model available to your OpenAI account.

### Optional live job search

To search JSearch through RapidAPI, subscribe to the
[JSearch API](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch), then set
these values in the ignored `.env` file:

```text
JOB_SEARCH_PROVIDER=jsearch
JSEARCH_API_KEY=your-key
```

The agent still sees only `search_jobs(query: str)` for searching. The adapter sends one
JSearch search request with an eight-second timeout and returns at most ten
normalized records. Results include their source and UTC retrieval time. A
missing key, provider error, or malformed response is reported as an error;
the tool does not silently substitute fictional jobs. Search uses one request
and no automatic retry. The provider may charge for API calls. These results
are not an exhaustive market view, and retrieval does not prove a posting is
still open. The live API path has not been manually acceptance-tested.

Use `JOB_SEARCH_PROVIDER=mock` to return to the deterministic local catalog.
`save_results` remains limited to mock catalog IDs. In JSearch mode, even an
explicit save request returns a clear unsupported-operation error. This keeps
the existing write validation intact until live identity and duplicate
handling are persisted and validated in later steps.

### Live filters, fit checks, and duplicates

Hard filters are taken from the current user message, not model tool arguments.
For the common phrase “Find remote Python jobs in the US,” the local filter
enforces remote status and US country. For other exact constraints, add a
`filters:` clause to the request:

```text
Find Python jobs filters: location=Chicago; remote=true; role=Engineer; skills=SQL; prefer_skills=AWS
```

Supported keys are `location`, `remote`, `role`, `skills`, and `prefer_skills`.
The first four are hard constraints except `remote=false`, which simply does
not require remote work. `prefer_skills` changes order but does not exclude.
Missing location, country, remote status, role, or required-skill evidence does
not pass a hard filter. Tool results show each exclusion and its reason.
The adapter also passes US and remote constraints upstream and adds an explicit
role or city to the provider query, but the local post-filter is authoritative.
Only the first provider page is checked, so an empty filtered result is not
proof that no matching job exists.
Conflicting or malformed clauses produce a visible error. This parser is
deliberately narrow; use an explicit clause for constraints it cannot infer.

After live search, the agent can call `assess_fit(job_id)` for a returned job.
That tool reads the local resume and compares a limited set of explicit skill
requirements. It returns exact excerpts from both sources, marks unmatched
requirements as unverified, and does not infer that the candidate lacks a
skill. It is not a complete fit score. The job must be searched again on a
follow-up turn before it can be assessed there.

Within a live search, duplicate provider IDs and canonical URLs are removed
first. A conservative exact-content fallback handles some cross-source
reposts. The result reports duplicate IDs and reasons. This is in-memory only;
tracking previously seen jobs across runs is planned for V3.

### Tool tracing

Each executed function tool records its name, model-generated call ID,
duration in milliseconds, outcome, source, and error class. Python callers
can inspect `turn.tool_traces`. These records contain no arguments, outputs,
API keys, resume text, job descriptions, or exception messages. An `Error:`
result is recorded as `ToolReportedError`; the string contract does not retain
the underlying provider exception class. Raised exceptions record their class.

To print local records as JSON lines on stderr, set:

```text
JOB_AGENT_LOCAL_TRACING=true
```

Records are emitted after the run, including completed tool calls before a
run failure. They are kept in memory for the turn, and for as long as the
caller retains its `AgentTurn`. There is no trace file or automatic disk
retention. Redirected stderr is under the user's retention control. Trace
records are not added to conversation history or sent to the model.

OpenAI SDK trace export is a separate opt-in setting:

```text
JOB_AGENT_SDK_TRACING=true
```

The runner always sets `trace_include_sensitive_data=False` for SDK tracing.
This disables sensitive payload inclusion; SDK export is broader than the
local metadata records and is governed by the SDK and provider's retention.
Both switches default to `false`. Offline tests do not export SDK traces.

### Run the interactive CLI

From `src/openai-agent-sdk/`:

```bash
uv run job-agent
```

Alternatively, run `uv run python -m openai_agent_sdk.cli`. A session looks
like this:

```text
Job Agent > Find Bay Area machine learning roles involving ranking or LLMs
Agent > [ranked recommendations from the fictional catalog]
Job Agent > Save the first result
Agent > [save confirmation]
Job Agent > quit
Goodbye.
```

The CLI keeps context for follow-up requests during this process. Blank input
does nothing. Type `exit` or `quit`, send end-of-file, or press Ctrl-C to leave.
An error on one turn prints a recovery message and leaves the CLI open. Failed
turns do not replace the last successful conversation history.

### Persistent conversations

Without session options, the CLI keeps history only until exit. To create or
resume a conversation, run from this project folder:

```bash
uv run job-agent --session learning
uv run job-agent --session another-conversation
# Optional database location. Its parent directory must already exist.
uv run job-agent --session learning --session-db /tmp/job-conversations.sqlite3
```

Reusing the same ID and database restores conversation history. The default
database is `data/conversations.sqlite3`, relative to this project, and is
ignored by Git along with its SQLite side files. Custom databases must also
stay outside version control. Startup prints the selected ID and database path.
Both mock/JSearch and MCP CLI modes support these options. MCP still owns one
provider connection for the CLI lifetime. Database open failures stop startup;
storage or recovery failures stop the conversation without an ephemeral fallback.

Messages and tool results can contain resume and job text. Metadata-only
tracing does not remove that content from the database. Use fictional data for
learning. Keep one active CLI per conversation. This local ID is not user
authentication or tenant isolation.

Save permission, search criteria, searched-job evidence, and budgets start
fresh each turn. Continuing a conversation does not resume an interrupted
action. Handled failures restore prior messages, but do not undo tool effects
or provide recovery after a process crash.

To erase one conversation's messages, stop its CLI first. From this folder,
run the following with the intended ID and database path:

``` python
import asyncio
from contextlib import closing
from agents import SQLiteSession

with closing(SQLiteSession("learning", "data/conversations.sqlite3")) as session:
    asyncio.run(session.clear_session())
```

Reset removes that conversation's history without removing other conversations,
saved recommendations, or the resume. It is not secure erasure of database backups.

### Check the installation

```bash
python -c "from agents import Agent, Runner; import openai_agent_sdk; print('Package and SDK imports OK')"
```

The `.env` file, local resume, generated results, and virtual environment are
ignored by Git. Keep `data/resume.md.example` fictional so the repository can
share it safely.

### Try mock search

```bash
python -c "from openai_agent_sdk.tools import search_jobs; print(search_jobs('ranking'))"
uv run pytest
```

Search reads only the versioned mock catalog. It matches any meaningful query
word in a job's title, location, description, or skills, ignoring case and
common request words. Results keep catalog order and are labeled `mock`.
These are fictional records, not current job openings.

### Resume and saved results

`read_resume` returns the exact UTF-8 text from `data/resume.md`. A missing or
empty file produces an actionable error. The file is ignored by Git.

`save_results` accepts one recommendation per line, in this format:

```text
job-001: Strong match for ranking systems experience.
job-002: Relevant LLM application experience.
```

Each ID must exist in the mock catalog. The tool validates the entire request
before appending to `data/results.md`, adds a UTC timestamp, and skips IDs
already saved. It reports saved and skipped IDs. The results file is ignored by
Git. The agent allows this write only when the current user message explicitly
asks to save.

### Run the agent from Python

``` python
from openai_agent_sdk.agent import run_agent

first = run_agent("Find Bay Area machine learning roles related to ranking")
print(first.text)

second = run_agent("Save the first result", history=first.history)
print(second.text)
```

`run_agent` uses the OpenAI Agents SDK runner with limits of eight turns, eight
model calls, sixteen tool calls, and sixty seconds per run. Callers can lower
the last three limits with `max_model_calls`, `max_tool_calls`, and
`run_timeout_seconds`. Exhausting a call limit raises `RunBudgetExceeded`;
the deadline raises `TimeoutError`. Successful runs return final text and
replay-ready history for follow-up requests. The versioned instructions live in
`openai_agent_sdk/prompts/system.md`. SDK tracing is disabled for this local V1.
The tests use a scripted model and make no real API calls.

### Test and inspect results

For four versioned evaluation examples, framework choices, deterministic
graders, and opt-in real-model commands, see [evals/README.md](evals/README.md).

Run the deterministic project checks from this folder:

```bash
uv run pytest
```

From the repository root, run its required checks:

```bash
uv run ruff check .
uv run pytest
```

Inspect `data/results.md` after an explicit save request. Each saved job ID
has a UTC timestamp. Repeating a save request for the same ID reports it as a
duplicate without adding another entry. To reset local results, first inspect
or back up the file privately, then remove it with `rm -i data/results.md`.
This file is ignored by Git and should not be committed.

### Manual V1 acceptance run

With a valid API key and a local resume, start the CLI and ask for Bay Area
machine learning roles involving ranking or LLMs. Check that the shortlist
mentions only mock catalog IDs, uses resume evidence, and has not created
`data/results.md`. Ask it to save the top result, then repeat the same request.
The results file should contain that ID once. To check recovery, temporarily
make the local resume unavailable, ask for a candidate-fit recommendation,
then restore the resume and make another request in the same CLI session. Exit
with `quit`. Keep any temporary copy of a real resume outside Git.

This live run uses the OpenAI API and may incur usage charges. The automated
tests use no API key and do not prove live model behavior.

### Troubleshooting

- Missing resume: copy `data/resume.md.example` to `data/resume.md` and add
  your own details. The file is local and ignored by Git.
- Missing API key: set `OPENAI_API_KEY` in `.env` or the environment. The CLI
  reports a missing key without closing the session.
- Model or API failure: verify the key, account access, `OPENAI_MODEL`, and
  connectivity. The CLI keeps accepting requests after a failed turn.
- Tool failure: check that `data/jobs.json` is intact and local files are
  readable or writable. The agent should report a tool error rather than
  inventing missing information.

### V2-b Option B: minimal MCP adapter (steps 1–3)

The standalone adapter discovers `jsearch`, checks that `search_v2` is supported,
and calls only that read-only operation. It reuses `normalize_job` to return the
existing job contract. Each search requests one page, returns at most ten jobs,
and has an eight-second client deadline with no retries. Provider errors become
safe `JSearchError` messages; unexpected programming errors keep their traceback.
The caller owns the initialized MCP session. Discovery expects the pinned
server’s single-page tool list; incompatible or paginated lists are rejected.

The official server is pinned to `@openwebninja/mcp-server@0.1.1` and launched
with `npx` (Node.js >=18). Set `OPENWEBNINJA_API_KEY` in the ignored `.env` for
later live use. Confirm your account's JSearch access and quota manually.
RapidAPI credentials are separate. Account access has not been verified.

From this directory, run the offline checks and discovery-only smoke check:

```bash
uv run pytest tests/test_openwebninja_mcp.py
uv run python -m openai_agent_sdk.mcp_discovery
```

Discovery starts the pinned server with an empty key, validates its schema,
and closes the connection without searching or subscribing. `npx` may download
the package on first launch; its transitive dependencies are not locked.

The reviewed package maps `search_v2` to `GET /jsearch/search-v2` and returns
JSON text containing `data.jobs`. Its discovery schema has a generic `args`
object, so the adapter constructs the search arguments itself. It also accepts
structured results and rejects malformed jobs or conflicting result forms.

The server has no upstream HTTP timeout or cancellation support. The client
deadline does not prove a paid request stopped. The CLI can select MCP; async search tracing is implemented in step 6. Live response parity remains unverified.

References: [official server](https://github.com/OpenWeb-Ninja/openwebninja-mcp),
[operation manifest](https://github.com/OpenWeb-Ninja/openwebninja-mcp/blob/main/src/generated/manifest.ts).

### V2-b step 4: connection ownership

`open_search_session(api_key)` opens one server process and initialized MCP
session. Its async context closes both on normal exit, startup failure, tool
failure, or cancellation. It uses the MCP SDK's stdio cleanup, which closes
stdin and terminates the process tree if the server does not exit.

`run_mcp_cli(api_key=..., agent_fn=...)` keeps that connection open across turns.
Both CLI entry points share one conversation loop. The supplied async turn
function receives `(message, history, adapter)`. Terminal input runs on the main
thread so interruption does not leave a blocked input worker. MCP background
tasks pause while the user enters text; this CLI processes one turn at a time. Failed
turns keep the previous history. Exit commands, EOF, and input interruption
close the connection. `run_agent_async` supports the existing eight-turn limit
on the same event loop. `run_agent` remains available for synchronous callers.

The normal `job-agent` entry point selects this lifecycle when
`JOB_SEARCH_PROVIDER=mcp`. Step 6 traces awaited MCP search calls.
No raw provider tools are exposed to the model.

Discovery uses a 15-second initialization deadline. Tool discovery and search
use eight-second client deadlines. Search requests one page, returns at most
ten jobs, and never retries. The server still has no upstream HTTP timeout
or cancellation guarantee. Initial package download and process cleanup can
take longer than a request deadline.

Run the offline lifecycle checks and discovery smoke check:

```bash
uv run pytest tests/test_mcp_lifecycle.py
uv run python -m openai_agent_sdk.mcp_discovery
```

### V2-b step 5: MCP through the owned search tool

Set these values in the ignored `.env`, then run `uv run job-agent`:

```text
JOB_SEARCH_PROVIDER=mcp
OPENWEBNINJA_API_KEY=<your OpenWeb Ninja key>
OPENAI_API_KEY=<your OpenAI key>
```

This is a live model and provider workflow. It keeps one MCP session open
for the CLI conversation. The model sees `search_jobs`, `read_resume`,
`assess_fit`, and `save_results`; it cannot choose a transport or call the
server's subscription tool. MCP failures return errors without falling back
to direct JSearch or mock data.

HTTP and MCP share provider-query preparation, local filters, deduplication,
and result formatting. Only retained jobs enter the current turn's searched
job context. Fit checks require those jobs and local resume evidence. Saving
live jobs remains blocked even when the user requests it.

MCP searches use the existing local metadata trace format. Other local tools
retain their existing tracing. Account access, quotas, and live result parity remain
unverified; implementation checks use a scripted model and fake MCP adapter.

### V2-b step 6: awaited MCP search tracing

Set `JOB_AGENT_LOCAL_TRACING=true` to print metadata records to stderr. MCP
search uses `source=mcp` and the SDK tool call ID. Duration includes the awaited
request and local result processing. A provider exception records its class
before the search wrapper returns a safe error. Cancellation also records an
error and continues to propagate so session cleanup can run.

Records contain only tool name, call ID, duration, outcome, source, and error
class. They exclude credentials, arguments, job descriptions, resume text,
result content, and exception messages. SDK export remains independently
controlled by `JOB_AGENT_SDK_TRACING`, with sensitive payloads disabled.

### V2-b step 7: acceptance and known differences

Offline checks cover discovery compatibility, content decoding, normalization,
local filters, duplicate removal, fit evidence, save restrictions, safe errors,
timeouts without retries, tracing, and session cleanup. A shared response fixture
produces identical normalized jobs through HTTP and MCP. This does not prove
that the two live providers return the same jobs.

From this project directory, run:

```bash
uv run pytest
```

Live acceptance is deliberately opt-in. These commands make provider requests
that may consume quota, but make no model calls or subscription changes:

```bash
# One MCP search, with OPENWEBNINJA_API_KEY configured.
uv run python -m openai_agent_sdk.mcp_acceptance --live

# One MCP search plus one RapidAPI search, with both provider keys configured.
uv run python -m openai_agent_sdk.mcp_acceptance --live --compare-direct
```

The command saves counts, field names, timing, outcomes, and error classes to
ignored `data/mcp-acceptance.json`. It excludes keys, queries, job text, and raw
errors. Successful MCP reports confirm that the connection context exited.
Error reports do not claim verified shutdown. Failed checks exit with status 1.
A comparison is a separate explicit request, not a fallback after MCP failure.

| Difference | Direct JSearch | OpenWeb Ninja MCP |
|---|---|---|
| Credentials | RapidAPI `JSEARCH_API_KEY` | `OPENWEBNINJA_API_KEY` |
| Request path | HTTP `/search-v2` | MCP `jsearch.search_v2`, then HTTP `/jsearch/search-v2` |
| Response | JSON provider envelope | JSON envelope inside MCP content |
| Timeout | Eight-second HTTP timeout | Eight-second client wait; upstream cancellation unverified |
| Extra lifecycle | None | Node.js server process and MCP session |
| Application behavior | Shared filtering, deduplication, fit and save rules | Same local rules |

Live acceptance remains pending. During manual debugging, an MCP request timed
out under the eight-second client deadline. Provider access, quota availability,
live result differences, and cancellation have not been verified. An empty
result does not prove that two providers use equivalent datasets. Do not declare
live equivalence from offline fixture parity or schema discovery.
