# Job Search Agent, OpenAI Agents SDK

This is an installable Python project for the V1 CLI job search agent described
in [V1_DEVELOPMENT_PLAN.md](V1_DEVELOPMENT_PLAN.md). Its importable package is
`openai_agent_sdk`. Its single agent is in `openai_agent_sdk.agent`. The
interactive CLI is planned for Step 7.

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
model. The CLI has not been built yet.

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
