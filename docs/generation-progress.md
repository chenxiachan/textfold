# Slow sections, stopping and shared Codex capacity

A slow early chapter previously held up the visible progress count: the
thread-pool iterator yielded in input order even after later sections completed.
Generation and translation now count completion order. Translation still applies
results in document order so its section/title choices stay deterministic.
Both use upstream's `llm.as_answered`, including its periodic log while replies
are outstanding; the PR adds cancellation propagation around that implementation.

Codex requests from this TextFold process are serialized. Generation Stop reaches
the active Codex runner and cancels queued calls before another subprocess starts.
Only the created process tree is terminated: a Windows Job Object or a separate
POSIX process group. There is no process-name search or shared bridge shutdown.
On Windows the process starts suspended, joins its Job Object, then resumes;
otherwise a quick launcher could create descendants before job assignment and
leave them outside cancellation. A delayed-assignment regression covers this
window, and setup failure or Stop during assignment never runs the child or
leaves it suspended. The implementation uses documented
[process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags),
[thread snapshots](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-thread32first)
and [ResumeThread](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread),
opening only threads owned by the new process.
Prompts use a temporary input file so a child that does not read stdin cannot
block cancellation. Completed replies returned before cancellation are still
cached normally. Other agent/API runners retain their existing stop semantics.

Standalone agent children exclude inherited desktop IPC and session control
variables while retaining normal configuration and authentication variables.
Rate-limit errors are not retried as reasoning-effort fallback requests. These
changes reduce contention; they cannot remove provider-side rate limits or
guarantee that another application's requests will not be throttled.

Codex reasoning levels come from the selected model's `model/list` capabilities.
`off` selects its lowest advertised level directly: `none` or `minimal` only if
supported, otherwise (for example) `low`. Supported explicit levels are kept;
an unavailable explicit level chooses the nearest supported lower level, or the
minimum if no lower level exists. Missing capabilities leave `off` to the CLI
default rather than probing with an assumed `none` request. Unrecognized future
levels use the advertised default or first supported option.

This mapping happens only when constructing CLI arguments. The original model
specification, prompt, schema, logical effort and cache-key calculation are
unchanged. Existing completed chapter replies remain cache hits, including
replies previously obtained through the `off` → `low` fallback. Cache reads do
not invoke model discovery or a model subprocess. A mixed cached/unfinished
generation requests only the unfinished chapter; explicit force regeneration
retains its existing cache-bypass meaning.

The regression suite uses stand-in model replies and disposable Python child
processes, never a real Codex/bridge process. It checks slow-section completion,
translation progress, environment isolation, queued cancellation, timeout and
large-stdin behavior, reasoning fallback suppression, and preservation of an
unrelated sentinel process when an owned child/grandchild is canceled.

A replay of the cached public source for arXiv 2602.01219 produced 17 sections.
With the second stand-in request held back, progress reached 16/17 before it
was released, then 17/17 afterward. Production storage was opened read-only;
the replay issued no model requests. This verifies progress behavior, not the
latency of a real model request or the cause of every desktop disconnection.
