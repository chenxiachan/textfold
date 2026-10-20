# D4 — Tool rows at the level you read at

Status: **planned, verified in probes, not implemented** (2026-10-20). This document is the
design record for the one part of a turn the fold does not reach today: the rows Pi draws for
tool calls. It is written to be executed commit by commit, and to survive an interrupted run.

## 1. The gap, measured

The fold covers replies and reasoning. Tool rows are components, not Markdown, so
`registerMarkdownTransformer` never sees them. In a coding turn that is where the scroll goes.

Measured on the real components (`ToolExecutionComponent` + the built-in renderers, offline,
width 100), a representative turn from a recorded session (21 tool calls, bash/write/edit):

| shape | rendered lines | what it is |
|---|---:|---|
| as Pi draws it today | **623** | call + Pi's own preview (bash: last 5 output lines, edit: diff) |
| result cut to one non-empty line | 525 | Pi's box padding dominates the saving |
| result hidden, Pi's shell kept | **151** | call line per row, nothing else |
| result hidden, `renderShell: "self"` | 42 | 2 lines per call, but changes Full's look |

One bash row is 13 lines (call, 5 preview lines, "… (N earlier lines)", timing); one edit row
36 lines; a collapsed `read` row is already 1 line. A whole session's tool rows came to 19,227
rendered lines across 983 calls — 800 of them bash at 17.7 lines each.

So the saving lives in **dropping the result body**, not in trimming it: 623 → 151 lines
(−76%) with no change to how Pi draws a call line.

## 2. What an extension can actually do (verified)

| hypothesis | verdict | evidence |
|---|---|---|
| `registerToolRenderer((name, next) => renderers)` can choose renderers for **built-ins** | **yes** | `resolveToolRenderers` falls back to `withBuiltInRenderers(name, def)`; live replay logged 13 resolvers: `bash/read/edit`, each `call=true result=true`, `edit` with `shell=self` |
| the resolver's `next()` returns renderers for every built-in | **yes** | same replay; `core/tools/renderers/index.js` maps read, bash, edit, write, grep, find, ls |
| a wrapper can read the level **at draw time** | **yes, but only after `invalidate()`** | pressing Alt+3/4/5 with `requestRender(true)` produced **0** renderer calls; `tui.invalidate()` produced 26 |
| an empty result component hides the result body | **yes** | offline: 13-line bash row → 4 lines (default shell), 2 lines with `self` |
| replacing the call line needs no reimplementation of Pi's formatting | **yes** | only `renderResult` is replaced; `renderCall` is passed through, so the call text stays Pi's |
| built-in renderers reuse `context.lastComponent` | **yes** — and this is the trap | `bash.js` calls `component.clear()`/`addChild` on `lastComponent`; handing it our wrapper breaks it, so the wrapper must pass the *inner* component through `state` |
| `renderShell` is fixed per row at construction | **yes** | `ToolExecutionComponent.getRenderShell()` reads the definition object; a level change cannot re-frame old rows — so a `self`-shell design would have to be right at Full too |

Consequences for the design:

1. Use `registerToolRenderer`, replace **only** `renderResult`, keep the call line.
2. Compute the kept lines inside `render(width)`, so an existing invalidate redraws the row at
   the new level. The plugin already invalidates the screen on every level change (D3), so no
   new redraw path is needed.
3. Never pass our wrapper back to the built-in as `lastComponent`: keep the inner component in
   `context.state` and hand the built-in a context whose `lastComponent` is that inner one.

## 3. The design

A new pure module `extension/tools.ts`:

```
toolResultKeep(level) -> 5 for Full and Brief, 2 for Points, 0 for Outline and Gist
foldToolLines(lines, level) -> string[]   // non-empty lines only, plus "… (N more lines)"
```

- **Full (1) and Brief (2):** Pi's own row, untouched. Brief is what Pi already does — a preview
  with the expand key — so re-implementing it would only make the row differ from Pi's.
- **Points (3):** the first two non-empty result lines, then `… (N more lines)`. The command and
  its first output lines stay; the tail is one marker line.
- **Outline (4) and Gist (5):** no result body. The call line stays, so the turn still reads as
  the list of things that happened — at 4 lines a call instead of 13–36.

Nothing is paraphrased and nothing new is drawn: the fold removes, it never invents. A row that
hides anything ends in a marker that names how much is hidden and that Alt+1 restores it.

`extension/index.ts` gains one `registerToolRenderer` call and a `debug` line per level change
(not per frame): `tool <name> level=<n> kept=<k>/<total>`.

## 4. Debuggability

- **The tap already exists**: `TEXTFOLD_PI_DEBUG=1` writes every shortcut/apply/restore/fold
  decision to `TEXTFOLD_PI_DEBUG_FILE`. Add one field for tool rows: tool name, level, kept and
  total lines, and whether the result or the row was hidden.
- **A deterministic replay** (built for this round, kept as a probe): `make_fixture.py` extracts
  a real turn (bash/read/edit + results) from a recorded session into a 21-entry session file;
  `run_fixture.py` replays it under tmux with the extension and captures the screen at each
  level. No model call, no quota, ~8 s per run — the way to see a row change without waiting for
  a provider.
- **The probe extension** (`run_fixture.py` + `probe.ts`) is throwaway; the plan's tests are
  the durable form.
- Unit tests own the rule (`foldToolLines`) and the wiring (resolver returns a result renderer
  that reads the level, passes `state` through, and leaves `renderCall` alone).

## 5. Run durations and checkpointing

Measured on this machine:

| step | duration | when |
|---|---:|---|
| `npm test` (87 tests) | **17 s** | every commit |
| `tsc --noEmit` | 3 s | every commit |
| preflight `scripts/check-pi-api.py` | 3 s | before pushing |
| fixture replay under tmux | 8–12 s | after wiring changes |
| live pty probe with a model (D3 style) | 90–200 s | once, before pushing |
| full verification pass (tests + typecheck + preflight + replay) | **~45 s** | before the last commit |

None of these is long enough to need a background service, but the *implementation* run can be
interrupted. Checkpoints, in order:

1. Plan committed (this file). A run that stops here loses nothing.
2. Rule + tests committed and green: no wiring yet, so the extension behaves exactly as today.
3. Wiring committed and green: the fold reaches tool rows; the rule is already proven.
4. Docs committed: README and the D3 log name the new coverage.

If a step fails, the previous commit is the working state: `git checkout -- .` returns to it,
and the plan's sections say what remains. Each commit leaves `npm test` green, so a resumed run
starts from a green tree rather than a half-applied edit. Do not batch steps 2–4: a failing
wiring must not hide a passing rule.

## 6. Atomic commits

| # | commit | files | gate |
|---|---|---|---|
| 1 | `Pi: the plan for folding tool rows, with the probes behind it` | this doc | none (docs only) |
| 2 | `Pi: the tool-row fold rule` | `extension/tools.ts`, `tests/tools.test.ts` | new tests + full suite + tsc |
| 3 | `Pi: tool rows fold at the level (the wiring)` | `extension/index.ts`, `tests/wiring.test.ts` | full suite + tsc + fixture replay at 1/3/5 |
| 4 | `Pi: the README says tool rows fold too` | `README.md`, D3 log §14 | full suite, no code changes |

## 7. Out of scope, with the evidence that parked it

- **A turn-digest entry** (`appendEntry` + `registerEntryRenderer`): the mechanism works and is
  model-invisible (custom entries are not messages), but a renderer that returns nothing at
  Full is **never added to the transcript**, so the digest cannot appear later when the level
  changes. It would need an always-present row (a blank line at Full for every turn) or a
  re-append per level change. Deferred until that trade is decided.
- **A digest widget**: unverified. The mechanism (`setWidget` above the editor) is the same one
  the level row and panel use, but a 19-line block would take fixed space from the transcript
  and needs a reset at every turn start.
- **A compact one-line-per-call row (`renderShell: "self"`)**: 42 lines instead of 151 for the
  measured turn, but the shell is fixed at construction, so Full would lose Pi's box for every
  row. Not worth a different look at Full for 6 lines a call.
- **Folding tool rows from the Markdown transformer**: impossible — tool rows are components,
  which is why this plan uses the renderer hook at all.

## 8. What "done" means

- Alt+3 on a replayed turn shows the first two lines of each result and a marker with the rest;
  Alt+4 and Alt+5 leave the call lines and no result bodies; Alt+1 restores Pi's own rows.
- The existing fold of replies and reasoning is unchanged, and the marker line's claim
  ("Alt+1 unfolds") stays true for what the fold hid.
- `npm test`, `tsc --noEmit` and the preflight are green; the fixture replay shows the row
  change at 3/4/5 and the restore at 1.
