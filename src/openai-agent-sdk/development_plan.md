# OpenAI Agents SDK Job Search Agent: Development Plan

### Status

Steps 1 through 8 are implemented. The CLI, deterministic checks, and V1
runbook are in place. A live API-backed manual acceptance run remains for a
user with a valid key and model access.

Step 1 was completed on 2026-10-02. `uv` created a local Python 3.13 virtual
environment and installed `openai-agents` 0.23.1 and `python-dotenv` 1.2.4.
The SDK import, dependency check, and Git ignore checks passed. Repository
checks passed with `uv run ruff check .` and `uv run pytest` (15 tests).

Step 2 now has the fictional job catalog, path definitions, typed job record,
and callable tool signatures. No tests were added or run during Step 2, as
requested. The three tool implementations followed in Steps 3 through 5.

The project now has its own `pyproject.toml` and an importable
`openai_agent_sdk` package. The outer project folder keeps its hyphenated name.

Step 3 is complete. `search_jobs` reads the mock catalog, matches query words
deterministically, and returns complete catalog records labeled as mock data.
Seven local search tests pass without an API key. Repository Ruff and pytest
checks also pass.

Steps 4 and 5 now implement local resume reading and duplicate-safe saving.
`save_results` accepts one `job-ID: recommendation` line per job and validates
all IDs before writing. Tests use temporary files only.

Step 6 adds one SDK agent with a versioned prompt, exactly three tools, local
history replay, and an eight-turn limit. A per-turn save gate requires clear
user intent before the save tool can write. Scripted-model tests exercise the
real SDK loop without an API key. A live model run remains part of Step 8.

Step 7 adds a testable interactive CLI with session history, clean exit paths,
blank-input handling, a missing-key message, and per-turn error recovery.

Step 8 adds setup, CLI, test, reset, troubleshooting, and manual acceptance
instructions to the README. Automated tests do not substitute for a live run.

This folder is a self-contained learning project directed by
`agent_codex_plan.md`. V1 uses the OpenAI Agents SDK and stays intentionally
small and local.

### V1 outcome

Create one CLI job search agent that can:

1. accept a natural-language job search request;
2. decide when to use its tools;
3. search a deterministic mock job catalog;
4. read a local resume;
5. compare jobs with the candidate profile;
6. return a short, ranked, evidence-based shortlist;
7. save requested results without obvious duplicates;
8. continue accepting commands until the user exits.

### V1 boundaries

V1 includes:

- one `Job Search Agent`;
- the OpenAI Agents SDK;
- one interactive CLI process;
- three tools: `search_jobs`, `read_resume`, and `save_results`;
- mock job data;
- local Markdown files for the resume and saved results;
- configurable model selection through environment variables;
- deterministic tests for tool behavior;
- graceful handling of API and tool failures.

V1 does not include:

- real job APIs;
- browser automation;
- FastAPI or a web interface;
- LangChain or LangGraph;
- MCP;
- a database or vector store;
- scheduling, deployment, or notifications;
- multiple agents.

### Planned folder structure

The implementation will remain contained inside this folder.

```text
src/openai-agent-sdk/
├── openai_agent_sdk/
│   ├── __init__.py
│   ├── contracts.py
│   ├── catalog.py
│   ├── agent.py
│   ├── cli.py
│   ├── prompts/
│   │   └── system.md
│   └── tools/
│       ├── __init__.py
│       ├── search_jobs.py
│       ├── read_resume.py
│       └── save_results.py
├── data/
│   ├── jobs.json
│   ├── resume.md
│   └── results.md
├── tests/
│   ├── test_search_jobs.py
│   ├── test_read_resume.py
│   ├── test_save_results.py
│   ├── test_agent.py
│   └── test_cli.py
├── .env.example
├── .gitignore
├── .python-version
├── README.md
├── pyproject.toml
├── requirements.txt
├── uv.lock
└── development_plan.md
```

The exact test split may be simplified during implementation if fewer files
make the behavior easier to understand.

### Plan 1. Bootstrap the isolated project

Goal: establish a runnable project boundary without implementing agent behavior.

Planned work:

- require Python 3.10 or newer;
- add `openai-agents` and `python-dotenv` dependencies;
- add an environment template with `OPENAI_API_KEY` and a configurable model;
- ignore `.env`, `.venv`, caches, the local resume, and generated results;
- add a fictional resume template so no real personal data is committed;
- document setup commands in the local README.

Exit evidence:

- dependencies install successfully in a clean environment;
- importing the Agents SDK succeeds;
- `git status` does not expose secrets or personal resume data.

### Plan 2. Define deterministic job data and tool contracts

Goal: make the tool boundary clear before involving a model.

Planned contracts:

``` python
search_jobs(query: str) -> str
read_resume() -> str
save_results(content: str) -> str
```

Planned work:

- create a small mock catalog containing stable job IDs, title, company,
  location, description, skills, and URL;
- define what counts as an invalid or empty query;
- define clear success and failure text for every tool;
- keep file paths resolved relative to this project folder;
- make all tool logic callable directly without running an agent.

Exit evidence:

- every tool has a precise name, description, inputs, output, and failure mode;
- every mock job has a unique stable ID;
- tests can invoke tool logic without an API key.

### Plan 3. Implement mock job search

Goal: return grounded job records without pretending to access current openings.

Planned work:

- load only the local mock catalog;
- match query terms against title, skills, location, and description;
- use deterministic case-insensitive matching;
- return complete records for matching jobs;
- return a clear no-results response when nothing matches;
- label results as mock data in tool output and user documentation.

Planned tests:

- role matching;
- skill matching;
- location matching;
- case-insensitive queries;
- empty query rejection;
- no-match behavior;
- output contains only records from the catalog.

Exit evidence:

- repeated searches return the same records in the same order;
- the search tool never invents a job or URL.

### Plan 4. Implement resume reading

Goal: give the agent grounded candidate evidence through a narrow read-only tool.

Planned work:

- read `data/resume.md` as UTF-8 text;
- reject a missing or empty resume with actionable error text;
- avoid returning stack traces or unrelated filesystem details;
- keep resume contents out of source code and test fixtures that may contain
  real personal information.

Planned tests:

- successful read;
- missing file;
- empty file;
- Unicode content;
- injected temporary path so tests never read a real resume.

Exit evidence:

- the tool returns the exact resume content when valid;
- failures are understandable and do not terminate the caller.

### Plan 5. Implement duplicate-safe result saving

Goal: make the only write operation predictable and observable.

Planned work:

- append recommendations to `data/results.md`;
- include a UTC timestamp;
- require saved recommendations to include stable job IDs;
- detect existing job IDs before writing;
- report which entries were saved and which were skipped;
- avoid creating partial output if validation fails;
- require explicit save intent in the agent instructions.

Planned tests:

- first save creates or appends content;
- repeated save does not duplicate a job ID;
- mixed new and duplicate jobs save only the new records;
- blank content is rejected;
- write failures return a clear error;
- tests write only to temporary files.

Exit evidence:

- saving the same recommendation twice produces one stored entry;
- the tool clearly reports the duplicate on the second attempt.

### Plan 6. Create the single Job Search Agent

Goal: let the model choose tools while deterministic code controls facts and
side effects.

Planned work:

- load agent instructions from `prompts/system.md`;
- register exactly the three V1 tools;
- configure the model through an environment variable;
- expose a `run_agent` function independent from terminal input and output;
- use the Agents SDK runner for the model and tool loop;
- preserve the current CLI session context for follow-up requests;
- add a bounded turn or tool-call limit so failures terminate predictably.

The system instructions will require the agent to:

- use tools instead of inventing resume or job facts;
- read the resume before making candidate-fit claims;
- search before recommending jobs;
- return a short ranked shortlist;
- identify each recommendation by stable job ID;
- explain each match using concrete resume and job evidence;
- state uncertainty when the resume lacks evidence;
- call `save_results` only when the user explicitly asks to save;
- report tool failures rather than hiding them;
- avoid duplicate save attempts when tool output already reports a duplicate.

Planned tests:

- construction registers exactly three tools;
- a search request can cause resume and search tool calls;
- returned recommendations reference only tool-provided jobs;
- a search-only request does not call the save tool;
- an explicit save request can call the save tool;
- a tool failure becomes a useful final response;
- the agent stops within its configured limit.

Most tests will use fakes or SDK test hooks. A real API call will be optional and
clearly marked as an integration test.

Exit evidence:

- a deterministic behavioral test demonstrates model request, tool execution,
  observation, and final response;
- the core runner can be called without importing the CLI.

### Plan 7. Build the interactive CLI

Goal: provide a small, resilient interface around the agent runner.

Planned behavior:

```text
Job Agent > Find Bay Area MLE roles related to ranking and LLMs
Agent > [ranked shortlist]
Job Agent > Save the first result
Agent > [save confirmation]
Job Agent > quit
```

Planned work:

- loop until `exit`, `quit`, end-of-file, or keyboard interruption;
- ignore blank input without calling the model;
- preserve session context for follow-up requests;
- catch configuration, API, and tool errors per turn;
- print a concise recovery message and continue accepting input;
- keep all input and output code outside `agent.py`;
- make the loop testable with injected input, output, and agent functions.

Planned tests:

- normal request and response;
- blank input;
- `exit` and `quit`;
- end-of-file and keyboard interruption;
- one failed turn followed by a successful turn;
- session context passed to a follow-up command.

Exit evidence:

- one simulated error does not terminate the CLI;
- the process exits cleanly for every supported exit path.

### Plan 8. Document and verify the complete V1 flow

Goal: make setup and observable proof part of the deliverable.

Planned README sections:

- V1 purpose and limitations;
- environment setup;
- dependency installation;
- resume template setup;
- API key and model configuration;
- running the CLI;
- running tests;
- inspecting saved results;
- resetting local results;
- troubleshooting missing resume, missing API key, and model failures.

Manual acceptance scenario:

1. Start the CLI.
2. Ask for Bay Area machine learning roles involving ranking or LLMs.
3. Confirm that the agent reads the resume and searches mock data.
4. Confirm that the shortlist uses only returned job IDs.
5. Confirm that no result is saved during the search-only turn.
6. Ask the agent to save the top result.
7. Repeat the same save request.
8. Confirm that the results file contains one entry for that job.
9. Trigger one recoverable error and confirm the CLI accepts another command.
10. Exit cleanly.

Final verification commands:

```bash
cd src/openai-agent-sdk
uv run pytest
cd ../..
uv run ruff check .
uv run pytest
```

The isolated project and repository root have separate `uv` environments.
Run each command from the directory shown. The local CLI entry point was also
smoke-tested with end-of-file input. Live API behavior still needs a manual
run with a configured key.

### V1 acceptance checklist

- [x] The interactive CLI starts successfully.
- [x] Natural-language requests reach one OpenAI Agents SDK agent in scripted tests.
- [x] The agent can call `read_resume` in scripted tests.
- [x] The agent can call `search_jobs` in scripted tests.
- [ ] A live model explains job fit using grounded evidence.
- [ ] A live shortlist contains only jobs returned by the search tool.
- [x] The agent saves only after explicit user intent in scripted tests.
- [x] Duplicate job IDs are not appended twice in deterministic tests.
- [x] Simulated API and tool failures do not terminate the CLI.
- [x] Agent logic remains independent from terminal input and output.
- [x] Core tool tests do not require an API key.
- [x] Secrets, personal resume data, and generated results are not committed.
- [x] The local README contains complete setup and run instructions.
- [x] V1 mock behavior remains available alongside opt-in V2 search.
- [x] Repository quality checks pass.

### Milestone gate

V2 Steps 1 through 3 are implemented with JSearch as an opt-in source. V2
Steps 4 through 8 and all V3 steps remain plans. Complete the remaining
live V1 acceptance checks before replacing the mock search path. Keep the V1
deterministic tests as regression tests. A possible V4 long-running agent is
not planned here. FastAPI, deployment, notifications, and background
scheduling are not V2 or V3 work.

### V2. Real Tools

Outcome: replace mock search with real job/search APIs while keeping one
agent, an interactive CLI, and explicit user control of writes. V2 adds
structured job objects, filtering, resume/JD matching, better deduplication,
and tool tracing. A live provider must not be described as a complete view of
the job market.

#### V2 Step 1. Select a real source and define its boundary

Status: implemented. JSearch is the first adapter because the current agent
accepts broad keyword searches such as remote Python jobs. TheirStack is a
candidate for more complex company, seniority, and date combinations.
Greenhouse and Lever are candidates for a later company-shortlist workflow.
Those sources are not integrated. Provider pricing, access terms, and quotas
must be checked against the current provider account before a live run.

JSearch uses the RapidAPI-hosted endpoint, an ignored API key, one request,
an eight-second timeout, and no automatic retry. Missing credentials,
authorization failure, rate limit, network failure, and malformed responses
produce explicit errors. A fake response is used in tests. No real API call
was made during implementation.

Redacted example of the provider fields accepted by the adapter:

```json
{
  "data": {"jobs": [{
    "job_id": "example-id",
    "job_title": "Python Engineer",
    "employer_name": "Example Co",
    "job_location": "Remote, US",
    "job_description": "Build Python services.",
    "job_apply_link": "https://example.com/apply",
    "job_posted_at_datetime_utc": "2026-10-01T12:00:00Z"
  }], "cursor": "example-next-page-token"}
}
```

- Compare candidate job/search APIs for access, terms, rate limits, fields,
  freshness, and testability. Choose one provider for the first adapter.
- Keep credentials in ignored environment configuration. Define timeouts,
  retry limits, and a clear response when the provider is unavailable.
- Exit evidence: a documented provider choice, a redacted sample response,
  and a deterministic fake for tests. No live key is needed for unit tests.

#### V2 Step 2. Define structured job objects

Status: implemented. `LiveJobPosting` is the normalized shape. The adapter
rejects missing required fields, invalid field types, and non-HTTP URLs.
Location, posting date, and employment type are explicitly nullable. Tests
cover valid, missing-optional, and malformed records.

- Add a typed job object with provider, provider job ID, canonical URL, title,
  company, location, description, posting date when available, and retrieval
  time. Mark optional or missing fields explicitly.
- Normalize provider responses at the boundary. Reject malformed records
  rather than passing raw provider text to the agent as trusted facts.
- Exit evidence: fixture tests map valid, missing-field, and malformed API
  responses into a stable internal schema.

#### V2 Step 3. Replace mock search behind a provider adapter

Status: implemented for search only. `search_jobs(query: str)` remains the
model-facing contract. `JOB_SEARCH_PROVIDER=jsearch` selects the live adapter;
mock remains the default and offline fixture. The adapter limits the response
to ten jobs, adds source and retrieval time, and does not fall back to mock on
provider failure. Fake-adapter tests pass. A live integration run remains
unverified. Saving live IDs is deferred until identity and deduplication rules
are completed.

- Make `search_jobs` call the selected adapter while keeping its tool contract
  clear to the model. Preserve the mock catalog as an offline test fixture.
- Bound result count and latency. Report source and retrieval time with each
  result, and surface provider errors without inventing jobs.
- Exit evidence: the same search contract passes with a fake adapter; one
  separately marked integration check can exercise the real API.

#### V2 Step 4. Add deterministic filtering

- Filter structured jobs by explicit user criteria such as location, remote
  preference, role, and required skills. Separate hard exclusions from
  preferences that can affect ranking.
- Show which criteria removed each job. Do not let the model silently change
  hard filters or claim a missing field passed a filter.
- Exit evidence: table-driven tests cover matching, missing fields, and
  conflicting criteria before model-backed ranking is added.

#### V2 Step 5. Match resumes to job descriptions

- Extract evidence from the local resume and each job description. Keep source
  excerpts or field references so fit claims can be checked.
- Use deterministic requirements checks where possible; use the model only
  for interpretation and explanation. State gaps as uncertainty, not as
  invented candidate experience.
- Exit evidence: evaluation cases include strong fit, weak fit, missing
  resume evidence, and misleading job text. Recommendations cite both sides.

#### V2 Step 6. Improve job deduplication

- Prefer provider ID and canonical URL for identity. Add a conservative
  fallback for equivalent titles, companies, and locations across sources.
- Keep distinct openings separate when identity is uncertain, and record why
  two records were considered duplicates.
- Exit evidence: tests cover repeated pages, URL variants, reposts, and
  same-title but distinct jobs without collapsing valid openings.

#### V2 Step 7. Add safe tool tracing

- Record each tool name, call ID, duration, outcome, source, and error class.
  Make SDK tracing an explicit, configurable choice instead of assuming it is
  enabled in the V1 CLI.
- Do not log API keys, full resume text, or unrestricted job descriptions.
  Define a retention and redaction rule before persisting traces.
- Exit evidence: one search and one tool failure produce inspectable traces
  with matching call IDs and no secrets or personal resume content.

#### V2 Step 8. Verify the real-tool flow

- Run unit tests offline, a marked live integration test with a user-provided
  key, and the repository quality checks. Review API costs and failure modes.
- Manually confirm that results identify their source and time, filters are
  respected, fit claims are grounded, and saving still needs explicit intent.
- Exit evidence: the README has setup and acceptance instructions; V1 mock
  tests and V2 tests pass. Defer persistence and scheduling to later stages.

### V3. Persistent State

Outcome: add local SQLite storage for `jobs`, `searches`, `recommendations`,
and `agent_runs`. Persist previously seen jobs and agent history so the CLI
can resume without treating a new process as a new user. No scheduler,
notifications, web service, or deployment belongs in V3.

#### V3 Step 1. Define the database schema and migrations

- Specify primary keys, foreign keys, unique constraints, UTC timestamps,
  and schema versions for the four tables. Keep provider IDs and canonical
  URLs where available; define how missing identifiers are handled.
- Store replay-ready agent history in `agent_runs` or an explicitly related
  record, with a documented serialization format and version.
- Exit evidence: a new database migrates from empty, and a second migration
  run makes no duplicate tables or data.

#### V3 Step 2. Persist jobs and searches

- Insert normalized job objects and record each search query, filters,
  provider, retrieval time, outcome, and job references.
- Upsert repeat sightings without losing the first-seen time; update the
  last-seen time and current provider fields deliberately.
- Exit evidence: repeated searches preserve one stable job identity and a
  separate record of each search attempt.

#### V3 Step 3. Track previously seen jobs

- Classify search results as new, previously seen, or uncertain duplicates
  using V2 identity rules and stored sightings.
- Keep the agent's shortlist grounded in the current result set while
  allowing it to explain that a job was seen before.
- Exit evidence: a second search in a new process identifies prior jobs
  without hiding distinct openings.

#### V3 Step 4. Persist recommendations and save decisions

- Link each recommendation to a job and the run that produced it. Record fit
  evidence, saved status, and timestamps without duplicating a saved job.
- Make validation and writes transactional. Keep the explicit save gate
  outside the model and report skipped duplicates.
- Exit evidence: repeated saves and interrupted writes leave one consistent
  recommendation record per intended save.

#### V3 Step 5. Persist agent runs and conversation history

- Record conversation ID, run ID, start and end time, status, model identifier,
  redacted error details, and replay-ready history. Choose one continuation
  method: local replay or an SDK session. Do not mix both in one conversation.
- Define retention, deletion, and access rules for stored resume-derived
  content. Keep the database out of Git.
- Exit evidence: a follow-up request after a process restart uses the prior
  conversation once, without duplicated history items.

#### V3 Step 6. Add a narrow storage layer and recovery path

- Put SQL and transactions behind typed repository functions. Keep tools and
  CLI independent from table details.
- Handle locked or corrupt databases with actionable errors. Avoid silently
  discarding history or overwriting existing records.
- Exit evidence: temporary-database tests cover migration, rollback,
  restart, duplicate records, and storage failure.

#### V3 Step 7. Verify persistence end to end

- Run the CLI, search and save, exit, restart, and request a follow-up.
  Inspect the four tables and compare them with the visible conversation.
- Run offline tests and repository quality checks. Document database location,
  backup, privacy, and reset procedures before declaring V3 complete.
- Exit evidence: new and seen jobs, recommendations, searches, and run
  history survive restart. No V4 background process has been introduced.
