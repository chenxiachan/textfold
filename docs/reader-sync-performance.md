# Reader synchronization and zoom performance

## Confirmed regression

The cloud reader handled every `tf-storage-synced` event by calling
`notesChanged`, including unchanged or empty note lists. That invalidated the
prepared zoom keyframes and scheduled layout and recapture work. A library sync
unrelated to the open paper could therefore interrupt otherwise cached reading.

A real Chromium probe waits for all five keyframes, dispatches an unchanged
sync event, and checks frame identity and CDP performance counters:

| Reader | Frames retained | Layouts | Main-thread task time |
|---|---|---:|---:|
| Upstream | Yes | 0 | 1.2 ms |
| Cloud, before fix | No | 22 | 253.1 ms |
| Cloud, fixed | Yes | 0 | 1.7 ms |

These are task-time deltas over the same observation window, not network latency
or a promise of identical timings on other hardware.

The fix retains current note acknowledgements and aliases but compares the
displayed notes per passage before redrawing. Revision metadata and object-key
order do not invalidate frames. Changed notes redraw only affected passages.
Refresh waits for zoom, other reader transitions and editing to finish; it checks
again after HTTP, including whether a local edit completed during the request.
Deletion requests participate in the same pending-write guard as note saves.
Notifications coalesce. Failed reads retain the last hint with retries backing
off from one second to a maximum of thirty seconds; long-lived editors and hidden
pages do not poll at animation cadence.

## Static animation comparison

The first 1,853 lines of the reader, including the zoom/keyframe implementation,
are byte-for-byte unchanged from upstream. The earlier static HTML measurements
never dispatched cloud notifications and cannot demonstrate this particular bug.

Independent controls use the same 166-unit dense fixture derived from
`2512.23916`, identical paper JSON, blocked external requests, ready fonts,
prepared frames and fixed click-to-Full input. Chromium uses the RTX 3080 Ti via
ANGLE/D3D11 at 1512 by 982 CSS pixels and a device scale factor of two. A/A and
B/B controls precede A/B and B/A comparisons. Chrome frame traces, rather than
only JavaScript animation timestamps, supply dropped-frame counts.

The initial A/A pair ranged from 36.84% to 44.80% dropped frames. In that balanced
eight-sample run, upstream's median was 37.58% and the fixed reader's 32.72%; both
performed 31 layouts. These observations do not establish a speedup. They show
why an earlier one-percentage-point median difference was insufficient evidence
of a static-animation regression.

A separate A/B/B/A pass compared the pre-fix cloud reader against upstream: baseline: 40.18%, 33.01%; after: 33.01%, 37.61%. Both readers again performed 31 layouts per gesture. No stable extra static-animation workload was established; the confirmed regression is the cloud-sync refresh path above.

This is Windows hardware-GPU evidence, not macOS validation. Absolute frame-drop
rates include existing rendering costs and are not a zero-jank guarantee.

## Regression coverage and data safety

Run `node --test tests/reader-sync-zoom.test.cjs` for the actual reader refresh
and deletion paths with controlled HTTP and timer boundaries. Cases cover
unchanged/revision-only responses, moved and deleted notes, conversation branches,
ordering, active gestures and drafts, edits completed during HTTP, burst hints,
failure recovery and retry backoff. The original failing cases were exercised
before their fixes.

Real-browser checks additionally hold the zoom between levels, deliver a sync
notification, and verify no refresh occurs until release. Changed remote notes
must subsequently appear without changing the resulting zoom level or causing a
page error.

The change writes no document files or database records, changes no storage
schema, and leaves the existing zoom implementation intact. Browser fixtures and
traces live outside tracked document data; tests use disposable libraries.

## Integration with upstream 0.10.32

Fresh balanced A/A, B/B, A/B and B/A GPU traces against main a9e80ce: click-to-Full dropped-frame median 41.69% (range 34.91–42.50%) upstream and 36.53% (range 34.65–42.37%) here. Both performed 31 layouts per gesture. These Windows RTX 3080 Ti/D3D11, 2× pixel measurements show no stable added workload; they do not establish exact parity, a speedup or macOS results.

The fixtures use identical document JSON and the latest upstream/branch CSS,
interface strings and reader scripts, with external requests blocked. The
new upstream Find and Mermaid code is present in both versions.

## Integration with upstream 0.10.33

Repeated the same eight-sample balanced run against main 6c0f123, including
its native-language and AI web-tool changes. Upstream dropped-frame percentages
were 37.04, 37.61, 35.24 and 36.89 (median 36.97%); the branch had 34.29, 38.68,
34.55 and 37.14 (median 35.85%). Layout counts were 30–31 upstream and 31 here.
This remains evidence of no stable added zoom workload on this Windows GPU,
not a speedup or a guarantee for other hardware. Paper data, viewport, cached
fonts, GPU options and blocked external requests were identical.
