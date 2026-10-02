# Job Search Agent, OpenAI Agents SDK

This is an installable Python project for the V1 CLI job search agent described
in [V1_DEVELOPMENT_PLAN.md](V1_DEVELOPMENT_PLAN.md). Its importable package is
`openai_agent_sdk`. The agent and tool implementations are planned for later
steps.

The fictional catalog is in `data/jobs.json`. Step 3 implements deterministic
mock search in `openai_agent_sdk/tools/search_jobs.py`. Resume reading and
saving still raise `NotImplementedError` until Steps 4 and 5.

### Requirements

- Python 3.10 or newer. The setup below uses Python 3.13 to match the parent
  course project.
- `uv` for creating the environment and installing dependencies.
- An OpenAI API key for future model-backed runs. No key is needed to verify
  this bootstrap step.

### Setup

Run these commands from `src/openai-agent-sdk/`:

```bash
uv venv --python 3.13 .venv
uv sync
source .venv/bin/activate
cp .env.example .env
cp data/resume.md.example data/resume.md
```

Add your key to `.env` before a later step makes an API call.
`OPENAI_MODEL` sets the model for that future agent. The CLI has not been built
yet.

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
