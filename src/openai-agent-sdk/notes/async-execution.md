# Async execution in the job-search agent

This note combines the discussion that started with “explain the async design
in run_agent_async”. It covers `await`, the event loop, worker threads, and
concurrent users. Examples use simulated work and make no API calls.

### 1. The terms we need

| Term | Meaning in this project |
|---|---|
| Conversation | Messages across several user requests. |
| Turn | One user request and the agent's work on that request. |
| Coroutine | An async operation created by calling an `async def` function. Calling it alone does not execute its body. |
| Task | An operation scheduled on the event loop. |
| Event loop | A scheduler that runs ready tasks and waits for events. It normally runs in one thread. |
| Worker thread | A separate thread that executes blocking code, such as a database read. |
| Thread pool | A reusable group of worker threads, with a maximum worker count. |
| Concurrency | Several operations make progress over overlapping periods. |
| Parallelism | Work executes simultaneously, for example on separate CPU cores. |

A coroutine is not a thread. One user turn can use the event-loop thread,
a worker thread, and network waiting at different points.

### 2. What happens before, during, and after `await`?

In [agent.py](../openai_agent_sdk/agent.py), `run_agent` starts an event loop
with `asyncio.run`. `run_agent_async` executes inside that loop.

```text
run_agent
  asyncio.run
    run_agent_async
      validate request
      create fresh turn context
      await session.get_items
      await Runner.run, through wait_for
      restore history if execution fails
      deliver tool traces
      return AgentTurn, or raise an error
```

An async function executes ordinary code sequentially until it must wait.
For example:

``` python
history = await session.get_items()
print(len(history))
```

1. The function starts the history operation.
2. If the operation must wait, this coroutine suspends. The event loop can run other ready tasks.
3. When the operation finishes, its result becomes `history`.
4. Only then does `print` execute.

If the operation raises an exception, `await` raises that exception instead.
If the operation finishes immediately, `await` need not suspend execution.

The next line in this coroutine does not execute while it waits.
Other scheduled tasks can execute during that wait.

### 3. Who reads the SQLite database?

The installed SDK implements the database read through `asyncio.to_thread`.
Its shape is:

``` python
history = await asyncio.to_thread(read_database)
```

`read_database` is illustrative. The SDK supplies its own database function.

```text
Event-loop thread                    Worker thread
─────────────────                    ─────────────
Submit database function ──────────►  Read SQLite
Suspend this coroutine               Perform blocking database work
Run other ready tasks
Receive result          ◄──────────  Finish read
Continue this coroutine
```

`asyncio.to_thread` submits work to the event loop's default thread pool.
An available worker executes it. If all workers are busy, the work waits.
The operating system schedules the threads and handles underlying file access.

`await` does not create a thread. `asyncio.to_thread` is the explicit step
that sends this blocking function to a worker.

### 4. What other tasks can execute?

The program must schedule them. The event loop does not invent work.
Tasks can handle different users, conversations, or features in the same
application. Examples include sending streamed text, waiting for another API
response, or handling a run deadline.

Another application has its own event loop or execution mechanism. The
operating system schedules separate processes. Your event loop does not
schedule their tasks.

The current CLI processes one user turn at a time. It does not schedule
100 user requests merely because the runner is async.

### 5. A small example where concurrency saves time

These two operations simulate independent network waits:

``` python
import asyncio
from time import perf_counter

async def request(name):
    await asyncio.sleep(0.2)
    return name

async def main():
    start = perf_counter()
    results = await asyncio.gather(request("A"), request("B"))
    print(results, round(perf_counter() - start, 2))

asyncio.run(main())
```

`gather` schedules both operations. They complete in about 0.2 seconds total.
Two sequential awaits would take about 0.4 seconds.

```text
Sequential: A [wait 0.2 s] B [wait 0.2 s]   total ≈ 0.4 s
Concurrent: A [wait 0.2 s]
            B [wait 0.2 s]                  total ≈ 0.2 s
```

This reduces the time to finish both operations. It does not make either
individual operation faster. The times are illustrative, not project benchmarks.

Only independent work can overlap this way. A model request that requires
conversation history must wait for that history first.

### 6. Do 100 async tasks need 100 threads?

No. Native async network operations generally need no dedicated worker thread
per request. The event loop coordinates them with operating-system support.
Some supporting operations, such as DNS lookup, can still use worker threads.

Blocking operations submitted with `to_thread` share the thread pool:

```text
100 tasks submit database reads to a pool with 10 workers
  10 database functions execute in worker threads
  90 database functions wait in the queue
  a worker finishes, then takes another function
```

Workers belong to the pool. They do not belong permanently to users.
The example does not imply that SQLite permits 10 simultaneous writes.

In Python 3.13, the default `ThreadPoolExecutor` maximum is:

``` python
min(32, (os.process_cpu_count() or 1) + 4)
```

If Python sees eight available logical CPUs, this gives 12 workers.
Threads are created as needed and reused. This limits that pool, not all
threads on the computer. You can configure another maximum with `max_workers`.
More threads consume more memory and do not remove database or API limits.

### 7. When can user A block user B?

| Operation | Effect on the event-loop thread |
|---|---|
| `await async_client.get(url)` | Normally yields while waiting for network events. |
| Blocking HTTP call inside `async def` | Blocks other tasks on that loop during the call. |
| `await asyncio.to_thread(write_file)` | A worker performs the write. The loop remains available. |
| Ordinary synchronous file write | Occupies the loop during the write. |
| Long Python calculation | Occupies the loop unless execution moves elsewhere. |

In this agent, SQLite session reads use workers. The JSearch HTTP path and
some local file operations are synchronous. They can still block the loop.
Marking their enclosing function `async` does not change this behavior.

For many users, likely limits include provider quotas, connection pools,
worker availability, database write locks, CPU capacity, and memory.
Measure them before changing concurrency settings.

Correctness also matters. Two users must not share private resume or results
paths. Two turns for the same conversation must execute sequentially with
the current recovery design. SQLite allows only one writer at a time per
database. Duplicate prevention needs safe writes, not only async scheduling.

### 8. What happens when the agent finishes or fails?

`run_agent_async` waits for the complete SDK agent loop:

```text
Model request → tool execution → model request → final answer
                 Repeat as needed within budgets
```

While this executes, the calling function remains at its main `await`.
It does not advance between model calls.

| Outcome | Next action in `run_agent_async` |
|---|---|
| Success | Receive the SDK result, deliver traces, return `AgentTurn`. |
| Model/tool/budget error | Restore prior session history, deliver traces, raise the error. |
| Deadline expires | Request cancellation; normally receive `TimeoutError` after cancellation handling, then restore history and deliver traces. |
| Caller cancels the task | Handle cancellation, attempt history restoration, deliver traces, and propagate cancellation. |

The runner uses `asyncio.wait_for` for its deadline. Cancellation is
cooperative. It cannot forcibly stop blocking code or guarantee that an
external provider stopped processing a request.

The pre-run history snapshot and post-failure recovery are outside that
runner timeout. The function can therefore return after the configured deadline.
A second cancellation can also interrupt recovery.

Recovery awaits each database removal sequentially. Removing a message does
not undo a file write or a paid request. Recovery assumes one writer and full,
untrimmed history. Limited-history sessions currently break that assumption.

The `finally` block sends tool traces synchronously. A slow trace sink can
block the event loop. A trace-sink exception is ignored so it does not replace
the agent's result or original error.

### 9. The learning foundation

The questions in this discussion point to five concepts to learn together:

1. Processes and threads: where code executes.
2. Coroutines and tasks: how async work becomes scheduled work.
3. Waiting and blocking: whether the event-loop thread remains available.
4. Dependencies: which operations can overlap safely.
5. Capacity: why concurrency still needs limits and safe shared storage.

Use this check when reading an `await`: What operation is waiting? Who
performs it? What independent work is scheduled? What happens if it fails?

### 10. Your exact questions, with one-line answers

Original wording, spelling, and capitalization are preserved below. The
answers summarize the discussion above.

1. **explain the async design in run_agent_async. specially, what happens during and after await**

   Answer: During `await`, this coroutine can suspend so other ready tasks can run; afterward, it continues with a result or receives an exception.

2. **what is an event loop?**

   Answer: An event loop schedules ready async tasks and waits for events, normally within one thread.

3. **what other scheduled tasks can be done while the get session items operation wait?**

   Answer: Other scheduled tasks can handle another conversation, stream output, wait for an API response, or process a deadline.

4. **give concrete examples, are the scheduled tasks must coming from the same agentic systems?**

   Answer: They must share the same event loop, but can serve different users or features, such as an agent request and a file upload.

5. **give a case where this helps reduce time.**

   Answer: Two independent 0.2-second waits can overlap and finish in about 0.2 seconds instead of 0.4 seconds.

6. **who gets assigned to complete the session.get_items() task?**

   Answer: The SDK submits the SQLite read to a worker thread through `asyncio.to_thread`, then the waiting coroutine receives its result.

7. **What knowledge gap you can spot from me asking the questions above?**

   Answer: The questions suggest a gap in how coroutines, tasks, event loops, threads, and operating-system resources relate.

8. **suppose i have 10 users use this agent at the same time, their tasks such as read DB or call API can happen cuncurrently thanks to async, user A's api call won't block user B's file writing?**

   Answer: Yes, if both requests are scheduled concurrently and blocking work uses workers; synchronous API or file calls on the event loop can still block other tasks.

9. **what would be the bottleneck?**

   Answer: The bottleneck can be blocking code, provider quotas, connection limits, worker availability, database locks, CPU capacity, or memory.

10. **are Worker-thread pool. are there limits on how many threads i can have for this agent.**

    Answer: Yes, each pool has a configurable worker maximum, while system resources limit how many threads the application can support.

11. **Suppose i have 100 tasks concurrently, do i get 100 threads to work on each of them**

    Answer: No, async tasks share the event loop, and blocking functions share a limited worker pool where excess work waits in a queue.

### References

- [Python: coroutines and tasks](https://docs.python.org/3.13/library/asyncio-task.html)
- [Python: thread-pool limits](https://docs.python.org/3.13/library/concurrent.futures.html)
- [Python: event loop and executors](https://docs.python.org/3.13/library/asyncio-eventloop.html)

No `CONTEXT.md` was present when this note was written. Terms follow the
current agent code. This note adds no concurrency configuration or runtime behavior.
