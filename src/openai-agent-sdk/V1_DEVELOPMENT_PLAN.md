# OpenAI Agents SDK Job Search Agent: V1 Development Plan

### Status

Planning only. No application code has been written.

Step 1 was completed on 2026-10-02. `uv` created a local Python 3.13 virtual
environment and installed `openai-agents` 0.23.1 and `python-dotenv` 1.2.4.
The SDK import, dependency check, and Git ignore checks passed. Repository
checks passed with `uv run ruff check .` and `uv run pytest` (15 tests).

Step 2 now has the fictional job catalog, path definitions, typed job record,
and callable tool signatures. No tests were added or run during Step 2, as
requested. `read_resume` and `save_results` remain placeholders for Steps 4
and 5.

The project now has its own `pyproject.toml` and an importable
`openai_agent_sdk` package. The outer project folder keeps its hyphenated name.

Step 3 is complete. `search_jobs` reads the mock catalog, matches query words
deterministically, and returns complete catalog records labeled as mock data.
Seven local search tests pass without an API key. Repository Ruff and pytest
checks also pass.

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
│   ├── agent.py
│   ├── cli.py
│   └── tools/
│       ├── __init__.py
│       ├── search_jobs.py
│       ├── read_resume.py
│       └── save_results.py
├── data/
│   ├── jobs.json
│   ├── resume.md
│   └── results.md
├── prompts/
│   └── system.md
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
└── V1_DEVELOPMENT_PLAN.md
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
uv run ruff check src/openai-agent-sdk
uv run pytest src/openai-agent-sdk/tests
```

If the isolated dependency setup is not integrated with the repository's root
`uv` environment, equivalent commands from the local virtual environment will
be documented before implementation is considered complete.

### V1 acceptance checklist

- [ ] The interactive CLI starts successfully.
- [ ] Natural-language requests reach one OpenAI Agents SDK agent.
- [ ] The agent can call `read_resume`.
- [ ] The agent can call `search_jobs`.
- [ ] The agent explains job fit using grounded evidence.
- [ ] The shortlist contains only jobs returned by the search tool.
- [ ] The agent saves only after explicit user intent.
- [ ] Duplicate job IDs are not appended twice.
- [ ] API and tool failures do not terminate the CLI.
- [ ] Agent logic remains independent from terminal input and output.
- [ ] Core tool tests do not require an API key.
- [ ] Secrets, personal resume data, and generated results are not committed.
- [ ] The local README contains complete setup and run instructions.
- [ ] V2 features have not been introduced.
- [ ] Repository quality checks pass.

### Deferred until V1 passes

The following remain unplanned implementation work until every V1 acceptance
item passes:

- real job search providers;
- structured ranking and filtering improvements;
- SQLite persistence;
- scheduled or long-running execution;
- web interfaces and deployment;
- MCP or multi-agent orchestration.
