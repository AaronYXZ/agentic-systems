# Job Search Agent, OpenAI Agents SDK

This is an installable Python project for the V1 CLI job search agent described
in [V1_DEVELOPMENT_PLAN.md](V1_DEVELOPMENT_PLAN.md). Its importable package is
`openai_agent_sdk`. Its single agent is in `openai_agent_sdk.agent`. The
interactive CLI is in `openai_agent_sdk.cli`.

The fictional catalog is in `data/jobs.json`. Its three local tools now search
mock jobs, read a resume, and save selected recommendations. They can be called
without an API key or an agent.

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

`run_agent` uses the OpenAI Agents SDK runner with a bounded number of model
turns. It returns the final text and replay-ready history for follow-up
requests. The versioned instructions live in
`openai_agent_sdk/prompts/system.md`. SDK tracing is disabled for this local V1.
The tests use a scripted model and make no real API calls.

### Test and inspect results

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
