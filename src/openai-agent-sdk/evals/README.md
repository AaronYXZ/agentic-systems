# Job-agent evaluation examples

Assumption: evaluate the existing single agent against fixed fictional jobs,
without relying on changing job listings or accessing a personal resume.
The output is a named pass/fail result for every case and model mode.

### Framework choices

Use **pytest** for execution, assertions, parametrization, temporary files,
and integration-test selection. It is already a development dependency.
[Pytest markers](https://docs.pytest.org/en/stable/how-to/mark.html) let us
select paid model runs independently of the offline checks.

Use the existing **OpenAI Agents SDK ScriptedModel** test double for offline
runs. It exercises the real runner and tools with predetermined tool calls.
Use the configured real model for opt-in behavioral evaluations, with the
same fixed provider and deterministic graders. No additional eval framework,
LLM judge, or async pytest plugin is needed for these observable contracts.

### Cases and graders

| Case | Pass criteria |
|---|---|
| `remote-jobs-no-save` | Search retains the remote US job, fit is assessed, ID appears in the answer, and no file is written. The scripted model also attempts an unauthorized save to check the policy denial. |
| `duplicate-results` | Repeated provider ID appears once; duplicate is reported; fit evidence and job ID appear in the answer; no save call. |
| `missing-filter-evidence` | Unknown remote status or country excludes the job; no fit calls; answer says "No matching jobs"; no save call. |
| `partial-fit` | Python evidence matches; SQL stays unverified; exact source excerpts and job ID appear in the answer; no save call. |
| `tool-order` | Search output must return the retained job before `assess_fit` uses it; the excluded job and saving are forbidden. |
| `search-arguments` | Six cases check the model's raw skill query and the provider's US or city and remote filters. |

Fixtures are versioned JSON files. Each run uses a temporary fictional resume
and results path. The fixed provider makes no HTTP or MCP server calls.
SDK trace export is disabled by the tests.

### Run

From `src/openai-agent-sdk/`:

```bash
# Offline evaluations and grader checks. Verbose output identifies cases.
RUN_LIVE_AGENT_EVAL=0 uv run pytest tests/test_eval_example.py tests/test_eval_scenarios.py tests/test_eval_agent_behavior.py -m 'not integration' -v

# Eleven real-model evaluations. Requires OPENAI_API_KEY; may incur charges.
# OPENAI_MODEL selects the model, using the agent's normal default if unset.
RUN_LIVE_AGENT_EVAL=1 uv run pytest tests/test_eval_example.py tests/test_eval_scenarios.py tests/test_eval_agent_behavior.py -m integration -v -s

# Full subproject regression suite. Live evaluations skip by default.
RUN_LIVE_AGENT_EVAL=0 uv run pytest
```

Real-model runs send the versioned fictional request, resume, and jobs to the
model provider. They do not use a job-provider key or write local job results.
A failed assertion fails the pytest run. An API error also fails the run, but
is not evidence of a model-quality regression.

### What these examples establish

Scripted passes establish tool and policy behavior under predetermined calls.
They do not establish that a real model chooses the right calls or answers.
Live passes establish behavior on these eleven fixtures for that run only.
Exact excerpts and the requested no-match phrase deliberately make the answer
checks narrow and reproducible. They do not grade general answer quality,
detect every unsupported claim, or establish production reliability.
