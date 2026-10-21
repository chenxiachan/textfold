# TextFold for Pi — D3 port: detailed implementation plan

**Status: implemented** (2026-10-19 to 2026-10-21). The branch is rebased onto
`origin/main` and green there; §13–§15 are the log of each round.
Revised after a critical audit of the first draft (corrections listed in § 10).

Ports C1 (level persistence + restore), C2 (`--textfold-digits` digit-guard) and the fold-preview
panel onto **origin/main's refactored** plugin (the maintainer's level-row design). The goal is
"best of all worlds": keep upstream's UI and file organization, add only the behaviors upstream
lacks. Every pi-API assumption was verified live on pi 1.3.0 before this plan was written
(§ 3 — including one hypothesis the first draft wrongly claimed as verified, see VH6).

- Upstream: `origin/main` = `https://github.com/chenxiachan/textfold.git`
- The plugin is ported into upstream's own layout (`plugins/pi-textfold/`), not a fork of it
- Verified on the installed pi 1.3.0, Node 22; pi 1.2.0 is the declared floor
- Durations in § 5 were measured on one machine on 2026-10-19; read them as orders of magnitude

---

## 1. Current state (the two sides being merged)

| | origin/main `plugins/pi-textfold` | branch `pi-textfold` (5 commits) |
|---|---|---|
| UI | `extension/level.ts`: mod-style **row above the prompt**; themed factory widget, `placement:"aboveEditor"`; `screen.invalidate()` + `requestRender(true)` redraws the whole screen on a level change | `extension/panel.ts`: self-rendered `TextFold · N` panel widget (default placement) |
| Persistence (C1) | **none** — a restart starts at Full | `extension/persist.ts`: `appendEntry("textfold-level", {level})` on change; `sessionManager.getBranch()` scan on `session_start`, last valid wins, corrupt entries skipped |
| Digit-guard (C2) | **none** | `extension/guard.ts` pure decision + registration deferred to `session_start` (persistent `digitKeysRegistered` guard) |
| Fold marker wording | `Points · 40 of 240 words · Alt+1 unfolds` | `(folded: 40 of 240 words · Alt+1 unfolds)` |
| `fold.ts` | identical (blob-equal) | identical |
| Tests | few | 46 unit tests (vitest), tui_smoke S1–S6, s6_probe, pi_install_smoke |

Branch commits ahead of old base: `c632389` (extension), `2ee6aab` (plan+preflight),
`e6dc8f1` (C1), `d53e26a` (restore panel), `a31d0bd` (C2). Working tree clean except untracked
`pull-request.md` (out of scope here).

## 2. Target design (D3)

On a fresh branch **`d3-port` created at a recorded `origin/main` sha** (§ 8) the file set is
exactly upstream's plus three modules brought over:

```
plugins/pi-textfold/
  extension/index.ts        rewritten: upstream's structure + 4 wiring additions (§ 2.1)
  extension/fold.ts         untouched (blob-identical already)
  extension/level.ts        untouched (the row, /fold parser, Level type)
  extension/persist.ts      added from branch as-is (C1)
  extension/guard.ts        added from branch as-is (C2)
  extension/panel.ts        adapted: same foldPanelLines/wrap logic; imports Level from ./level;
                            typed against the 1.3.0 ExtensionContext ui surface
  package.json / tsconfig.json / tests/  upstream base + our test files (§ 2.2)
  tests/*.test.ts           wiring/persist/guard/panel tests, retargeted to the new index
  tests/tui_smoke.py        upstream base + S6 digits stage + checkpointing (§ 6)
  tests/s6_probe.py         added (single-stage digits probe)
  tests/pi_install_smoke.py added (README-flow probe)
  scripts/check-pi-api.py   added (S0b preflight)
  docs/                     this plan, updated IMPLEMENTATION-PLAN
```

### 2.1 Wiring additions to (otherwise upstream-shaped) `index.ts`

1. **`session_start` does three things in order:** install the row (upstream's job, keep), scan
   `getBranch()` for the restored level and set it **before** `install()` renders the row
   closure reads `level` at render time, so order is safe, but restore-then-install keeps the
   first paint correct), and — once, guarded by a module-level flag — register digit shortcuts
   when `pi.getFlag("textfold-digits") === true` (C2). Restore never writes an entry.
2. **`apply()` gains `appendEntry` (try/catch, debug-tapped on failure)** between setting
   `level` and the redraw. Nothing else changes; dedupe on equal level stays.
3. **`apply()` gains a notify** `Fold: ${levelName(next)}` (same position as the branch's) and
   **restore gains one** `TextFold level restored: ${levelName(restored)}, so level changes are
   human-evident even though the row already marks the level (no notify exists upstream).
4. **The panel** installs as a second widget under key `textfold-preview` (default placement),
   refreshed from `message_end` and from `apply()`, rendered with **upstream's marker wording**
   (`Points · 40 of 240 words · Alt+1 unfolds`) so row and fold speak the same dialect.

### 2.2 Documented behavior changes the port carries (test expectations change with them)

- Transformer/panel marker wording switches from `(folded: N of M words · Alt+1 unfolds)` to
  upstream's `· N of M words · Alt+1 unfolds` format. All tests asserting the old string are
  updated in the same commit that introduces the new one — no assertion is deleted silently.
- Upstream's footer word order and panel content are unified; the panel's pre-reply state
  (fold of an empty string) still renders header + `…`.

**Decision log** (why alternatives lost):
- *D1 upstream-without-panel* — viable (all row-side hypotheses verified) but drops the
  before-any-reply fold preview, which our S3/S5 evidence shows is the durable restore
  indicator. Kept as the fallback if the maintainer rejects the second widget.
- *Rebase the old branch* — rejected: `index.ts`/`level.ts`/`panel.ts` were restructured
  upstream; cherry-picks would tear apart cleanly separable behaviors. A fresh branch with
  re-typed modules produces the cleanest atomic history.
- *Upstream's no-notify minimalism* — rejected for level changes: silent level switching, with
  feedback only in a themed row, hides state from a new reader; a notify costs nothing and
  matches both our C1 tests and the mod's UX. Errors keep upstream's `notify(..., "warning")`.

## 3. Verified-evidence matrix (nothing rests on an unverified API)

| # | Hypothesis | Verified how (2026-10-19, pi 1.3.0) |
|---|---|---|
| VH1 | upstream plugin loads & row renders clean | live pty, `HOME=/tmp/fakehome --no-extensions -e` (clean startup, row text present) |
| VH2 | `invalidate()+requestRender(true)` re-folds earlier replies, no model call | live pty: one reply, level changes → `Points · 40 of 240` then `Outline · 14 of 240` in one session |
| VH3 | C1 needs only `appendEntry` + `getBranch` | live twice: battery S5 + `pi --session <dashed-prefix>` resume on the **installed package** |
| VH4 | digit-guard needs registration at `session_start` (flag commits after factory) | live twice (branch + installed); root cause debugged in the pi debug tap |
| VH5 | `aboveEditor` factory widget + default-placement string widget compose | live pty, throwaway `/tmp/verify-comp/comp.ts` |
| **VH6** | **`message_end` still fires on 1.3.0 and our panel's content actually refreshes from the reply** | **not yet verified — the batteries only ever asserted the panel's header presence, never content from a reply. First-class hypothesis, verified in P3's gate before P3 commits (§ 5).** |
| VH7 | session lookup matches the **session-file name prefix, dashes significant** | recorded finding: dashed prefix `01a152f8-928` resumed fine in the same battery run; a same-digits dashless prefix failed `No session found`. Full uuid is therefore still the safe form used everywhere (§ 7). |
| VH8 | **taught keys dispatch in the session they are taught** | **`parseKey("\\x1b&")` → `"alt+&"` on pi 1.3.0's matcher, but `setupExtensionShortcuts` snapshots `getShortcuts()` once at editor setup, so re-registered ids stay dead until a snapshot rebuild. Live PTY probe: after teaching, `ctx.reload()` re-runs load → `setupExtensionShortcuts` with the taught ids; `shortcut fired level=N taught` in the debug tap in the same session, and persistence proven by a fresh process registering from the file alone. Level continuity survives the reload via C1 entries on disk.** |

## 4. Atomic commit plan (each commit: compiles, tests green, tsc clean, no dead code)

Commit messages follow the repo's sentence style (`Pi: …`), with the contributor's
own git identity — name and address stay in commit metadata, not in documentation.

Every commit's gate uses `set -o pipefail` and captures exit codes; a failing gate means
fix-forward in the same commit or split the commit.

| # | Commit (one logical change) | Contents | Gate commands (must pass in-commit) | Budget |
|---|---|---|---|---|
| P0 | `Pi: the D3 port plan` | this file (docs only) | none (doc-only) | — |
| P0b | branch scaffolding, **no code**: `git fetch origin; BASE=$(git rev-parse origin/main)` recorded in the plan log; `git checkout -b d3-port "$BASE"; npm ci` inside the plugin; run `scripts/check-pi-api.py` (S0b) | no repo change to pi behavior; scaffolding recorded | S0b exit 0 on the **features the port needs** (`registerFlag`, `appendEntry`, `getEditorText/setEditorText`, `setWidget` placement option) — rerun **before P1**, not assumed from the old branch | 1–2 min |
| P1 | `Pi: the fold level survives a restart (C1 level-row port)` | `persist.ts` + `persist.test.ts` + `session_start` (install + restore) wiring + restore assertions added to `wiring.test.ts`; no guard, no panel yet | unit (`npx vitest run` + `npx tsc --noEmit`, ≤ 3 s); live C1 probe **with real HOME and `--no-extensions -e`** because restore needs a materialized session, and a session only materializes after a real user turn (§ 5, V-restore) | unit+scaffold ≤ 5 s; live ≈ 60 s (one short model turn) |
| P2 | `Pi: plain digits fold an empty prompt and are reinserted while typing (C2 level-row port)` | `guard.ts` + `guard.test.ts` + `registerFlag` + lazy registration + wiring tests | unit; live S6 probe (`python3 tests/s6_probe.py`, exit 0) | unit ≤ 3 s; S6 ≈ 40 s |
| P3 | `Pi: the preview panel shows the fold before any reply` | `panel.ts` adapted + `panel.test.ts` + `message_end`/`apply` wiring + **VH6 verification**: a probe with one real reply asserting the panel's *content* (not only its header) changes | unit; live panel probe ≈ 60 s (model turn + level change + assertion on panel content and both widgets coexisting) | unit ≤ 3 s; live ≈ 60 s |
| P4 | `Pi: the smoke battery runs every stage with checkpoint and resume` | `tui_smoke.py` rebased onto upstream wording + S6 stage + § 6 checkpoint/resume + `tests/ansiclean.py` (OS-level shared test infra, no product behavior) | full battery, `TEXTFOLD_SMOKE_STAGES` default all + **one injected mid-run kill → resume** proves the checkpoint path (acceptance § 9.3) | ≈ 70–95 s + one resume ≈ 50 s |
| P5 | `Pi: probes and the API preflight are part of the harness` | `tests/s6_probe.py`, `tests/pi_install_smoke.py`, `scripts/check-pi-api.py` as committed artifacts of the verification story | scripts syntax-check (`python3 -m py_compile`), S0b rerun | ≤ 5 s each |
| P6 | `Pi: the README contracts exactly the keys the harness proves` | plugin README table (Alt keys, `/fold`, `--textfold-digits`, pre-reply panel), IMPLEMENTATION-PLAN updated/strike-through; docs-name-only-verified-behaviors check | docs-only; grep that every documented string/key exists in code or tests | < 5 s |
| P7 | no commit: install stage — `pi remove` the current path source, then `pi install <abs path to d3-port worktree>/plugins/pi-textfold`; recorded in the plan log only (user-config churn happens once, last) | config only | `python3 tests/pi_install_smoke.py` exit 0 + `pi list` | ≈ 60 s |

Rules: no commit mixes product modules; a behavior lands **with its tests**; wording changes
land with the tests' wording updates (§ 2.2); fix-forward or split, never skip.

## 5. Verification matrix (what "done" requires beyond unit tests)

| Check | Interface exercised | Pass condition | Measured |
|---|---|---|---|
| V-restore | `pi --session <uuid>` (VH7: dash-consistent prefix; **full uuid everywhere**) | under real HOME: row marks restored level; notify present; no entry appended on restore | 3.3 s stage + model turn to materialize the session |
| V-folds | one live model reply (S2) | ≥ 4 fold renders with upstream `· N of M words` markers | 13–26 s |
| V-digits | `pi --textfold-digits` (installed or `-e`) | `ab 4` reinserts; ctrl+c then `3` → row marks Points; alt keys unaffected | 18.5 s stage |
| V-composition | both widgets one session | row **and** panel visible simultaneously; both update on one keypress | probe ≈ 45 s |
| **V-panel-content (VH6)** | one live reply then a level change | panel content switches from `…` to the folded text (asserts `message_end` refresh) | probe ≈ 60 s |
| V-install | README flow: `pi install` → plain `pi` | `[Extensions]` shows it; V-restore/V-digits pass | ≈ 60 s |

Every live run: `timeout 240`, pid recorded, `finally`-kill, artifacts written per stage.

## 6. Run-duration budget & checkpointing

Measured this machine (2026-10-19; cold `jiti` unless noted):

| Run | Duration | Notes |
|---|---|---|
| `npx vitest run` | 0.4–1.5 s | 46 tests |
| `npx tsc --noEmit` | 1–2 s | |
| `npm ci` (P0b, first on the plugin) | ~10–60 s (network) | runs once per base change |
| pi cold start in a probe | first read ≈ 5.4 s + 2 s settle; **`rm -rf /tmp/jiti` coded into probes before any extension-identity change or stale caches lie** | |
| one full S1–S6 battery | 60–95 s (S2 alone 13–26 s: model variance) | checkpointed per stage |
| single-stage rerun (`TEXTFOLD_SMOKE_STAGES=S6`) | ≈ 25–30 s | |
| composition/panel probes | 45–60 s each (one model turn) | |

**Total plan wall-time estimate:** ≈ 25–40 min of runs + human time, dominated by the full
battery ×2 (P4 gate + final acceptance) and the injected-kill resume proof.

**Policy: any run whose wall budget exceeds 60 s must be resumable from its last completed
stage, without re-running work whose inputs did not change.**

### Checkpoint design for `tui_smoke.py` (P4)

- Artifacts stay per `RUNID`: `/tmp/textfold-verify/<runid>/bin/<stage>.bin`, `state.json`.
- New `checkpoint.json` written with `os.replace` (crash-safe) at every existing `stage()`
  point: `{"completed": [...], "uuid": "...", "resumed": n}`.
- `TEXTFOLD_SMOKE_RUNID=<id>` (or `--resume <id>`): load artifacts + checkpoint, replay only
  pending stages. Dependencies: S1–S4 replay needs only the pi binary; S5 requires the
  `uuid` in the checkpoint (assert loudly, else instruct a fresh S1–S5 run); S6 is fully
  independent (fresh session). A stage's budget replaces the current unbounded `settle()`
  loop: S2 ≤ 120 s, others ≤ 30 s, plus `timeout 240` on the whole script.
- Exit code stays: 0 = all requested stages + report green; partial exit prints exactly one
  line: `RESUME: TEXTFOLD_SMOKE_RUNID=<id> STAGES=<remaining>`.

## 7. Debuggability requirements (highest priority; each exists to avoid a rerun)

- **Debug tap everywhere:** `TEXTFOLD_PI_DEBUG=1` keeps the existing appendFileSync+stderr tap;
  P4 parameterizes the path (`TEXTFOLD_PI_DEBUG_FILE`, default `/tmp/textfold-pi-debug.log`)
  and `stage()` snapshots the tap into the artifacts dir so stages cannot overwrite each other's
  logs. Every shortcut/apply/restore/digit decision logs, as today.
- **Artifact contract per stage:** raw pty `bin/<stage>.bin` + cleaned text dump + extracted
  evidence printed as `PASS/FAIL <name>: <value>` (never a silent boolean).
- **ANSI handling centralized:** `tests/ansiclean.py` with one strip regex (private-mode CSI,
  OSC-with-ST, charset/designate escapes) — three past false failures came from per-probe
  regexes.
- **Char-offset rule:** never slice a cleaned string with a byte offset (a real S6 bug);
  offsets into cleaned output come from `len(clean(buf))`.
- **Assert durable state, not banners:** a notify can be overdrawn by a redraw in the same
  frame (S5 lesson); assert the row/panel/footer render, and treat the notify as secondary.
- **Key-send hygiene:** `\x1b`-prefixed sends ≥ 2 s apart (esc-timeout merges are real; a real
  false negative came from chained sends); the editor clear on 1.3.0 is **ctrl+c**
  (ctrl+w only removed the last word, the digit then hit the reinsert branch).
- **Containment defaults, chosen per question (corrected from the first draft):**
  - *Startup/composition/wiring questions* → fake `$HOME` and/or `--no-extensions -e`
    (otherwise the installed package joins the session, pi resolves
    "Extension shortcut conflict" silently by file path, and typed `/`-commands leak into the
    model when sent while it is busy — VH1's first run).
  - *Persistence/restore/panel-content questions* → real HOME with `--no-extensions -e`:
    a session only materializes after a real user turn and the provider/model config lives in
    the real HOME; a fake `$HOME` session cannot be resumed (no model turn possible) — the
    first draft's fakehome C1 probe was unworkable as written.
- **Failure cookbook** (from bugs actually hit): `jiti` staleness → purge in probe code;
  flag-commit timing → register digits at `session_start`; session lookup by filename prefix,
  dashes significant → pass the full uuid; byte-vs-char offsets; banner overdraw → assert
  durable renders; key-merge timing; editor clear key; LEVELS names — Gist is 5, not 3;
  shortcut conflicts when two copies of the plugin load at once.

## 8. Risks & fallbacks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Upstream moves mid-port | low | pin P0b to a recorded sha: `git fetch origin && BASE=$(git rev-parse origin/main)`; base sha logged in the plan |
| Row and panel fight for space at narrow widths | medium | V-composition probe at widths 60/100/120 precedes P3's commit; if they collide, the panel becomes `--textfold-panel` opt-in (D1 fallback stands) |
| Model latency variance eats S2's budget | high | S2 ≤ 120 s + checkpoint; S2 never feeds a later stage's inputs |
| `message_end` semantics changed in 1.3.0 (VH6) | possible | P3's gate verifies panel **content** refresh on a real reply before committing; if `message_end` no longer carries `message` the same way, fall back to `assistant_message`/`message_update` events after an S0b-style read of the installed pi's types |
| `jiti` cache lies after a module is rewritten | certain on change | purge coded into probes |
| User-config churn from `pi remove`/`pi install` | expected | P7 runs once, last, after everything else is green |

## 9. Acceptance criteria (checked in order, no skips)

1. Branch `d3-port` exists at the recorded base sha; commits land one logical change each.
2. Every commit's gate commands pass in full (exit 0 captured with pipefail).
3. Full battery green on all six stages, **and** the checkpoint path proven by an injected
   mid-run kill followed by `TEXTFOLD_SMOKE_RUNID=<that runid>` completing the remaining
   stages (exercised once — implemented-but-never-run resume code does not count).
4. `pi_install_smoke.py` (installed package, no `-e`): exit 0.
5. `npx vitest run && npx tsc --noEmit` green on the final tree; test count ≥ 46.
6. The V-matrix of § 5 is fully green, including V-panel-content (VH6 closed by evidence).
7. Docs name exactly the behaviors the harness proves; no documented-but-unverified claim.

## 10. Critical corrections applied to the first draft

1. **`--session` matching is by session-file-name prefix with dashes significant** (VH7) — the
   first "needs the full uuid" claim was evidence-overreach; the dashless-prefix failure and the
   dashed-prefix success were both recorded. Full uuid is still mandated everywhere as the safe
   form, now for the right reason.
2. **The fakehome C1 gate was unworkable**: a session materializes only after a real user turn,
   and no provider/model config exists under a fake `$HOME` — restore cannot be probed there.
   Persistence/restore/panel-content probes now use the real HOME with `--no-extensions -e`.
3. **VH6 surfaced and was demoted from assumed-verified to a gated hypothesis**: no recorded
   evidence ever asserted panel *content* coming from `message_end` on pi 1.3.0; P3 now blocks
   on it.
4. **P4 was overstuffed** (smoke rework + probes + preflight in one commit) for "atomic";
   split into P4 (battery+checkpoint) and P5 (probes+preflight).
5. **Missing precondition added as P0b**: `npm ci` against upstream's pins and an S0b API
   re-check on the **features the port needs** before any ported code lands.
6. **Decision added**: level-change/restore notifies (§ 2.1.3) — upstream has none; silently
   relying on a themed row hides state, and our C1 tests assert notify behavior.
7. **Wording migration made explicit** (§ 2.2) so upstream's `· N of M words` marker replaces
   our `(folded: …)` string with the test updates riding the same commit.
8. **P6→P7 install step corrected** to a worktree path (`<abs>/plugins/pi-textfold`), not a
   branch name, and flagged config-only.
9. **Budget wording fixed** (P1/P3 rows) and the concrete fragile `origin/main@{now}` syntax
   replaced by an explicitly recorded `git rev-parse` base sha.
10. **Total wall-time estimate added** (§ 6): ≈ 25–40 min of machine time excluding human review.

**No implementation begins until this plan is approved.**

---

## 11. Execution log (2026-10-19)

Base sha: `2cd77e0` (origin/main at checkout); rebased clean twice, now 21/21
with no conflicts onto `cd0fe85` (origin/main at PR prep — none of its new
commits touch `plugins/pi-textfold/`). Branch: `d3-port`.

Commits, one logical change each, all gates green per commit:

| Commit | Change | Gate evidence |
|---|---|---|
| `a576d4a` | P0 the plan | docs only |
| `f6ac69d` | P1 C1 persistence port | 35 tests · tsc clean · live V-restore (full-uuid resume: row / panel / re-fold; entry count unchanged) |
| `3b4225d` | P2 C2 digit-guard port | 44 tests · tsc clean · S6 live (ab 4 reinsert, empty-prompt digit folds, Alt unaffected) |
| `43291c8` | P3 preview panel port | 54 tests · tsc clean · VH6 live (message_end on 1.3.0 delivers; panel body becomes the fold; header follows the level; row coexists) |
| `de17af5` | P4 battery + checkpoint/resume | full S1–S6 PASS, exit 0 |
| `3c2edec` | P5 probes + preflight | py_compile green · S0b all 7 APIs OK |
| `8dd3221` | P6 README contracts | every documented key/grep checked against the code |
| `07dbbfb` | P5-fix install-smoke cwd + foldable prompt (fix-forward) | base/digits/resume install smoke exit 0 on plain pi |
| `1b73c78` | taught keys: `/fold calibrate` + `/fold keys` + `keys.ts` (any keyboard, no layout table) | 63 tests · tsc clean · live teach probe exit 0 (real pi TTY: overlay → AZERTY-style `ESC+& 2 ESC+' 4 ESC+(` → saved file + `Saved` notify → learned keys fold same-session after reload → fresh process folds from the file alone); S1–S6 battery PASS |
| (this commit) | first-run welcome + taught hints: one `session_start` notice naming the terminal-eats-Alt check and `/fold calibrate` (marker file, once ever; skipped when keys are taught or the branch folds); row/panel hints name the taught glyphs | 72 tests · tsc clean · suite writes nothing outside per-process tmp (empty override means unset; default tmp override in wiring `beforeEach`) |
| (this commit) | click-to-fold: `levelAtColumn` maps a press to its level segment (label/gaps/hint never misfire); the row widget's `handleMouse` folds through the same `apply()` as the keys, left-button presses/clicks only | 76 tests · tsc clean · live click probe exit 0 (real pi pty: SGR press routes to the row, `row click level=1` + apply in the debug tap) |

Acceptance: § 9.1–7 all hold — 54 tests ≥ 46 floor, harness self-sufficient
(installed pi + Node 22 coded in), the checkpoint path **proven** by an injected
`timeout -s TERM 14` kill after S1 followed by `TEXTFOLD_SMOKE_RUNID=killtest`
replaying S2–S6 to a green exit, README contracts only its proven behaviors.

Corrections vs the plan itself, found in execution: the final wording of the
README landed as `07dbbfb` amends `9c94074`→`8dd3221` split (probe fix moved
after docs); S2's assertion now requires the reply + panel refresh (folds do not
exist at Full); S5 asserts durable evidence, the notify is reported not asserted.

---

## 12. Difference vs the remote main (`origin/main` = `cd0fe85` at PR prep; cut at `2cd77e0`, rebased 21/21 clean)

`d3-port` is 21 commits ahead of `origin/main`; the remote has nothing the branch lacks.
**No upstream file was touched beyond the two meant to change:** `extension/fold.ts`,
`extension/level.ts`, `package.json`, `package-lock.json`,
`tsconfig.json`, `tests/fold.test.ts`, `tests/level.test.ts` are blob-identical to main —
merge conflicts are structurally impossible outside the two adapted files.

18 files, +1948 / −132 (the plan itself is part of those lines):

| File | Δ | What it adds over main |
|---|---|
| `extension/index.ts` | +108/−, 4 hunks | C1: restore scan at `session_start` (before install), `appendEntry` + notify in `apply`; C2: `registerFlag("textfold-digits")` + digit shortcuts registered lazily at `session_start`; panel: `message_end` → preview widget refresh |
| `extension/persist.ts` | new (+24) | the C1 scan/store rules: branch's last valid `textfold-level` entry, corrupt entries skipped, restore writes nothing |
| `extension/guard.ts` | new (+18) | the C2 pure decision: digit folds an empty prompt, reinserts in a draft |
| `extension/panel.ts` | new (+56) | the preview panel lines + word-wrap (parseLevelArg stayed in upstream `level.ts`) |
| `README.md` | +8/−2, 2 hunks | documents persistence, the panel, and `--textfold-digits` — each as the battery proves it |
| `docs/D3-PORT-PLAN.md` | new | this plan, its verified-evidence matrix, execution log, and this manifest |
| `tests/wiring.test.ts` | new (+313) | restore / dedupe / flag-timing / digit / panel-content / section contracts against a fake pi |
| `tests/persist.test.ts`, `guard.test.ts`, `panel.test.ts` | new | the pure modules' unit contracts (54 tests total, floor 46) |
| `tests/tui_smoke.py` | rewritten (+373/−) | 7-stage live battery with per-stage checkpoint + `TEXTFOLD_SMOKE_RUNID` resume, bounded waits, durable-evidence assertions |
| `tests/ansiclean.py` | new | the one shared ANSI-stripping rule |
| `tests/s6_probe.py`, `tests/pi_install_smoke.py` | new | single-purpose probes: digits end-to-end; the README install flow on plain `pi` |
| `scripts/check-pi-api.py` | new | S0b preflight of every API the port needs, against the installed pi |

Behavior a main user gains: the fold level survives a restart (and a resumed session points
the row at it before any reply); a small panel previews the reply's fold with every stage;
and, behind `--textfold-digits`, plain 1–5 fold empty prompts and reinsert themselves in a
draft. Alt keys, `/fold`, the fold engine, the prompt section, and the row are untouched.

---

## 13. After the PR (2026-10-20)

`origin/main` moved to `5a5de9d` (40 commits). Three of them — #29, #30, #31 —
updated the fold engine in **both** copies, `plugins/textfold/hooks/fold.ts` and
`plugins/pi-textfold/extension/fold.ts`, and the pi tests with it: tables kept
whole at Brief and Points (GFM tables, with or without leading pipes, even
straight after a paragraph), a mermaid drawing kept whole, and code up to six
lines kept at Points. The port needed no engine sync after all — main did it.
The branch was **merged** with `origin/main` instead of rebased, so the open PR
keeps its history and the diff vs main is only the new work.

What this round adds:

| Change | Why | Evidence |
|---|---|---|
| Reasoning folds at the level, with the reply (`extension/index.ts`) | Measured on a real session: one turn held 384 words of reply, **10,540 words of thinking** and 19 tool calls; across the session 545 of 575 text pieces were under the 80-word gate. Folding only replies folded the closing paragraph and left the work untouched | `assistant-thinking` now goes through the same transformer + gate, `user` never does: 4 wiring tests; live pty probe at `--thinking max` logged `fold assistant-thinking shown 195/332` beside `fold assistant shown 132/274`, exit 0 |
| Outline no longer collapses into Gist (`fold.ts`, both copies + tests) | A reply written without headings kept only its first sentence at level 4 — exactly Gist's text (reproduced live: Points 30/200, Outline 17/200, Gist 17/200) | Level 4 falls back to Points when the reply has no headings: same tests in both copies, live probe Points 132 → Outline 132 → Gist 30 |
| A full redraw re-folds **every** reply, not just the last (corrected claim) | The panel's original note said the transcript never invalidates. The probe shows the opposite: one Alt+3 after two replies logged `fold assistant shown 61/241` **and** `61/239` | README and `panel.ts` reworded: the panel is a stay-in-view preview, not a workaround |

Gates on the merged tree: **87 tests**, `tsc --noEmit` clean, live probes exit 0
(reasoning folds; two replies re-fold; Outline≠Gist). `extension/fold.ts` is
still blob-equal to `plugins/textfold/hooks/fold.ts`, the Outline fix included.
`plugins/textfold/tests/fold.test.ts` gained the matching Outline case; it runs
in that plugin's own harness, not here.

---

## 14. Tool rows (2026-10-20)

The fold covered replies and reasoning, not tool rows: they are components, not
Markdown, so `registerMarkdownTransformer` never saw them. Measured on a
recorded turn (21 calls, bash/write/edit), the rows came to **623 rendered
lines**, and a whole session's 983 calls to 19,227 — one bash row is 13 lines
(call, five preview lines, a hidden-lines marker, the timer), one edit row 36.

`registerToolRenderer` closes the gap without redrawing anything: the built-in
renderer still draws the row, and a wrapper keeps only the lines the level
wants.

| Change | Why | Evidence |
|---|---|---|
| `extension/tools.ts` (new) | The rule is the fold's: remove, never invent | `toolResultKeep` = 5/5/2/0/0 for Full…Gist; `foldToolLines` drops padding and names what it hid | 8 unit tests |
| Wrapped result renderer (`extension/index.ts`) | Pi hands the resolver the built-in renderers, including for tools it does not own | A live replay logged 13 resolvers: bash/read/edit, each `call=true result=true`, `edit` shell=self |
| The level is read when the row draws | A plain `requestRender` does **not** re-render a row: Alt+3/4/5 produced 0 renderer calls. `invalidate()` produced 26 | The level-change path already invalidates the screen, so no new redraw path was needed |
| An expanded row is never folded | Pi's expand key is the reader naming one row in full | wiring test |
| The built-in renderer gets its own component back | `bash.js` clears and re-fills `lastComponent`; handing it the wrapper would nest a wrapper per redraw | the wrapper keeps the inner component in `context.state`; wiring test asserts `seen[1]` is the inner, not the wrapper |

Verified on a replayed session (no model call, `small.jsonl` + tmux), with the
extension loaded:

```
Full/Brief  $ printf 'one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\n'
            ... (4 earlier lines, ctrl+o to expand)   five six seven eight nine   Took 0.0s
Points      $ printf '…'   ... (4 earlier lines, …)   five   … (5 more lines · Alt+1)
Outline     $ printf '…'                                  (no result body)
Gist        $ printf '…'                                  (no result body)
```

Gates: **101 tests**, `tsc --noEmit` clean, preflight **11 APIs** (it now checks
`registerToolRenderer` and `ToolRenderContext`), replay at 1/3/4/5 as above.
Rows keep a four-line floor at Outline/Gist, and none of it is the fold's: Pi
adds a `Spacer(1)` above every tool row, the row's `Box` has `paddingY = 1`
(top and bottom), and the call line is the fourth. Only `renderShell: "self"`
avoids the box (two lines fewer), but the shell is fixed when the row is built,
so every row would lose Pi's box at Full as well — not worth it. The fold only
removes the result lines it can (`toolResultKeep` 0), and the box stays.

---

## 15. The live run, and the rebase (2026-10-21)

The replay above proves the rule against real Pi components but feeds them a
recorded turn. A live session closes that last gap: the extension installed the
way a user installs it (`~/.pi/agent/settings.json` → `packages`), a real model
turn that called `bash seq 1 40`, `ls -R .` and `read notes.txt`, then the keys.

```
Full    $ seq 1 40
        ... (35 earlier lines, ctrl+o to expand)   36 37 38 39 40   Took 0.0s
Points  $ seq 1 40   ... (35 earlier lines, …)   36   … (5 more lines · Alt+1)
Outline $ seq 1 40                                       (no result body)
Gist    $ seq 1 40                                       (no result body)
```

The tap from that session, on the installed extension:

```
tool rows folded by the level
tool bash level=3 lines=3/5
tool bash level=3 lines=3/9
tool bash level=4 lines=0/9
tool bash level=5 lines=0/9
```

Pi's own expand key (`ctrl+o`) still showed the whole row at Gist, and Alt+1
brought every result back. A second `ctrl+o` folds the row again — proven in a
separate session at Outline with `seq 1 9`: the row went from the call line only
to `1 … 9` + `Took 0.0s` and back, Pi's status line saying `Tool output:
expanded` then `collapsed`. (The first attempt at this looked like a failure
because it ran at Full, where there is nothing to expand, and because the
startup-help header shares the key: test the toggle at a level that folds.)

One harness fact worth keeping: `albert-deepseek-v4-flash` answered the tool
prompt with empty text and no tool call through the local router, so a
live tool-row run needs a tool-calling model.

**Rebase.** `origin/main` had moved 38 commits (to `cdad27c`) while this branch
sat on `5a5de9d`, so the five commits were replayed onto the new head. It applied
without a conflict; the suite on top of `cdad27c` is **103 tests** (upstream added
two), `tsc --noEmit` clean, preflight 11 APIs. Nothing outside
`plugins/pi-textfold/` changes in this round: the engine updates and the
six-line Outline fallback were merged into main before the rebase.
