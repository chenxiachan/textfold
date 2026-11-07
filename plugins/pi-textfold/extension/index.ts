/**
 * TextFold for Pi.
 *
 * A Pi coding-agent extension from TextFold (see `plugins/textfold` for the Claude
 * Code mod this mirrors): every Pi reply folds to the level you need, from every
 * word to the answer in one sentence, with one key. No second model and no
 * summary; the fold keeps prefixes of what the agent wrote.
 *
 * It works because the style section asked for below makes the agent open every
 * paragraph and list item with the sentence that states its point. Folding then
 * keeps a prefix of what Pi wrote; the fold changes the order of what the agent
 * says, not what it says or how much.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

import { parseKey, wrapTextWithAnsi, type KeyId, type TUI, type TuiMouseEvent, type TuiMouseEventResult } from "@earendil-works/pi-tui";
import { appendFileSync } from "node:fs";
import { fold, LEVELS, worthFolding } from "./fold";
import { foldToolLines } from "./tools";
import { hasBeenWelcomed, keysFile, loadKeys, markWelcomed, saveKeys, type KeysMap } from "./keys";
import { guardDigit } from "./guard";
import { foldPanelLines, PANEL_KEY } from "./panel";
import { KEY, levelAtColumn, levelRow, parseLevelArg, type Level } from "./level";
import { ENTRY_TYPE, levelEntryDraft, restoredLevel } from "./persist";

// Debug tap: writes every shortcut/apply/restore decision when TEXTFOLD_PI_DEBUG
// is set. Zero-cost otherwise.
const DEBUG = process.env.TEXTFOLD_PI_DEBUG
	? (s: string) => {
		const line = new Date().toISOString().slice(11, 19) + " " + s;
		try {
			appendFileSync(process.env.TEXTFOLD_PI_DEBUG_FILE ?? "/tmp/textfold-pi-debug.log", line + "\n");
		} catch {
			// stderr below still exposes it
		}
		try {
			(process as unknown as { stderr?: { write(s: string): void } }).stderr?.write?.(line + "\n");
		} catch {
			// best effort
		}
	}
	: undefined;

// Identical to the Claude Code mod's prompt section, so replies fold the same way
// on every harness. It asks for the point first at every scale.
const STYLE = `# Replies that fold
The person reading this session can fold your replies with one key: to each paragraph's first sentence or two, to the headings, or to the reply's first sentence. Write so that every fold still reads:
- Open the reply with one sentence that answers, or that says what you did.
- Open every paragraph and every list item with a sentence that states its point and reads on its own; reasons, details and caveats come after it.
- Give a reply longer than four paragraphs a few short headings.
This changes the order of what you write, not its content or its length.`;

// What folds: the reply and the reasoning around it, named one by one, so a kind of message Pi adds
// later is left as written until it is chosen here.
const FOLDS = new Set(["assistant", "assistant-thinking"]);

// Where Alt+digit goes when the terminal keeps Alt for itself.
const KEYS_FIX = process.platform === "darwin"
	? "on a Mac, let the terminal send Option as Meta (Terminal: Settings, Profiles, Keyboard; iTerm2: Profiles, Keys, Left Option key Esc+)"
	: "in GNOME Terminal: Preferences, Shortcuts, disable Switch to Tab";

export default function textfoldFold(pi: ExtensionAPI) {
	pi.registerFlag("textfold-digits", {
		type: "boolean",
		default: false,
		description: "Digits 1..5 fold on an empty prompt and are typed where the cursor is in a draft",
	});
	// The level every reply is read at: 1 Full (as written) to 5 Gist. Full at start, then
	// restored from the session's branch so a resumed session continues where the reader
	// left off (C1). Restore writes nothing: the level is already on disk.
	let level: Level = 1;
	// The latest settled assistant reply, for the preview panel: it shows the fold of
	// this, at the level in force, re-rendered on every level change (the widget
	// re-renders its content unconditionally, unlike the cached transcript).
	let lastAssistant = "";

	// The style section, appended as a structured prompt section so the session
	// records a delta instead of a replaced prompt.
	pi.on("before_agent_start", (event) => {
		event.systemPromptOptions.sections = {
			...event.systemPromptOptions.sections,
			textfold: STYLE,
		};
		return {};
	});

	// Each reply folded as Pi draws it, on the screen only: what the model sees is never changed.
	// Reasoning folds at the same level. In a coding turn it is where almost all the words are
	// (thousands of words of thinking around a few hundred of reply), so folding the reply alone
	// would leave the reader scrolling past exactly the part the fold exists to spare them.
	pi.registerMarkdownTransformer((markdown, context) => {
		if (!FOLDS.has(context.messageType) || level === 1) return markdown;
		const f = fold(markdown, level);
		const worth = worthFolding(markdown, f.text);
		// One line per settled block (streaming would write one a frame).
		if (!context.isStreaming) DEBUG?.(`fold ${context.messageType} ${worth ? "shown" : "whole"} ${f.shown}/${f.total} level=${level}`);
		if (!worth) return markdown;
		return `${f.text || "…"}\n\n*${levelName(level)} · ${f.shown} of ${f.total} words · Alt+1 unfolds*`;
	});

	// Tool rows fold at the same level. A row is a component, not markdown, so the
	// fold reaches it through pi's renderer hook: the built-in renderer still draws
	// the row, and the wrapper keeps only the lines this level wants. The level is
	// read when the row draws, not when it was built: a level change invalidates the
	// screen, which is what re-renders the row (a plain redraw does not).
	let lastToolLog = "";
	pi.registerToolRenderer((toolName, next) => {
		const base = next();
		const draw = base?.renderResult;
		if (!base || !draw) return base;
		return {
			...base,
			renderResult: (result, options, theme, ctx) => {
				const state = (ctx.state ?? {}) as { textfoldInner?: unknown };
				// pi hands back the component this renderer returned last time (the wrapper).
				// The built-in renderers clear and re-add children on what they get, so they
				// must get the component they drew — not the wrapper around it.
				const inner = draw(result, options, theme, { ...ctx, lastComponent: state.textfoldInner as never });
				state.textfoldInner = inner;
				// Asking for one row (pi's expand key) outranks the fold: it is the reader
				// naming the row they want in full.
				const expanded = options.expanded === true;
				return {
					render: (width: number): string[] => {
						const lines = inner.render(width);
						if (expanded) return lines;
						const folded = foldToolLines(lines, level);
						if (folded.length !== lines.length) {
							// One line per row shape, not per frame: the row re-renders often.
							const key = `${toolName}:${level}:${folded.length}:${lines.length}`;
							if (key !== lastToolLog) {
								lastToolLog = key;
								DEBUG?.(`tool ${toolName} level=${level} lines=${folded.length}/${lines.length}`);
							}
						}
						return folded;
					},
					invalidate: () => inner.invalidate?.(),
				};
			},
		};
	});
	DEBUG?.("tool rows folded by the level");

	// The row above the prompt names the five levels and marks the one read at. Pi hands its screen to the
	// row's factory, which the extension keeps: a new level has to be drawn on all of it.
	// The factory also keeps the latest command context, so a mouse press on a level
	// segment can fold without any key: the click path shares apply() with the keys.
	let screen: TUI | undefined;
	let mouseCtx: ExtensionContext | undefined;
	const install = (ctx: ExtensionContext) =>
		ctx.ui.setWidget(
			KEY,
			(tui, theme) => {
				screen = tui;
				mouseCtx = ctx;
				DEBUG?.("row installed");
				const paint = { label: (s: string) => theme.fg("muted", s), current: (s: string) => theme.bold(theme.fg("accent", s)), other: (s: string) => theme.fg("dim", s) };
				let lastWidth = 78;
				return {
					render: (width: number): string[] => {
						lastWidth = width;
						return [` ${levelRow(level, width - 1, paint, learnedKeys)}`];
					},
					invalidate: (): void => {},
					handleMouse: (event: TuiMouseEvent): TuiMouseEventResult | undefined => {
						// Primary-button presses only, on a level segment: the label, the
						// gaps, and the hint never misfire (levelAtColumn says undefined).
						// Coordinates are component-local; the line starts with one space.
						if ((event.type !== "press" && event.type !== "click") || event.button !== "left") return undefined;
						const plain = { label: (s: string) => s, current: (s: string) => s, other: (s: string) => s };
						const hit = levelAtColumn(levelRow(level, lastWidth - 1, plain, learnedKeys), event.x - 1);
						if (hit === undefined || !mouseCtx) return undefined;
						DEBUG?.(`row click level=${hit}`);
						void apply(hit, mouseCtx);
						return { handled: true };
					},
				};
			},
			{ placement: "aboveEditor" },
		);

	// Plain digits exist only behind --textfold-digits, because Pi dispatches extension
	// shortcuts before the editor sees any input. Registration must wait for session
	// start: pi commits CLI flag values after the factory runs, so reading the flag at
	// factory time always yields the default (a real bug this port inherited and fixed).
	let digitKeysRegistered = false;

	// Restore is unconditional on session start: a `--session` resume reports reason
	// "startup", so no reason-based gating can be trusted. The scan takes the branch's
	// last valid textfold entry; corrupt ones are skipped (persist.ts).
	pi.on("session_start", (event, ctx) => {
		if (!digitKeysRegistered) {
			digitKeysRegistered = true;
			if ((typeof (pi as { getFlag?: unknown }).getFlag === "function"
				&& (pi as { getFlag: (n: string) => unknown }).getFlag("textfold-digits") === true)) registerDigitKeys();
		}

		const branch = (ctx?.sessionManager as unknown as { getBranch?: () => unknown[] } | undefined)?.getBranch?.();
		const restored = restoredLevel(branch);
		DEBUG?.(`restore reason=${(event as { reason?: string }).reason} entries=${Array.isArray(branch) ? branch.length : -1} restored=${restored}`);
		if (restored && restored !== level) {
			level = restored as Level;
			ctx?.ui.notify(`TextFold level restored: ${levelName(restored)}`, "info");
		}
		install(ctx);
		ctx?.ui.setWidget(PANEL_KEY, foldPanelLines(lastAssistant, level, 78, learnedKeys));
		// First-run pointer: no taught keys and no level history means the reader
		// never folded anything — exactly the user whose terminal may be eating
		// Alt+numbers. One notice, once ever (marker file), naming the check and
		// the fix; taught users and returning folders never see it.
		if (ctx?.hasUI !== false && typeof ctx?.ui?.notify === "function"
			&& !learnedKeys && !restored && !hasBeenWelcomed()) {
			ctx.ui.notify(`TextFold keys: try Alt+3. If nothing folds, the terminal keeps Alt for itself: ${KEYS_FIX}, or teach your own keys with /fold calibrate`, "info");
			markWelcomed();
			DEBUG?.("welcomed");
		}
	});

	// A new level folds every reply in place at once, the earlier ones too. Pi keeps each reply as it drew it
	// until its text or the screen's width changes, so what it kept is dropped and the whole screen drawn again.
	// The level is also written to the session (C1): one entry per real change, so a restart
	// resumes where the reader left off. A failed write (a full disk) must not strand the
	// reader: the level still changes on the screen.
	const apply = (next: Level, ctx: ExtensionContext) => {
		if (next === level) {
			DEBUG?.(`apply no-op level=${next}`);
			return;
		}
		level = next;
		DEBUG?.(`apply level=${next} screen=${screen ? "yes" : "no"}`);
		try {
			pi.appendEntry(ENTRY_TYPE, levelEntryDraft(next));
		} catch (e) {
			DEBUG?.(`append failed: ${(e as Error)?.message ?? String(e)}`);
		}
		try {
			ctx.ui.notify(`Fold: ${levelName(next)}`, "info");
			const lines = foldPanelLines(lastAssistant, level, 78, learnedKeys);
			ctx.ui.setWidget(PANEL_KEY, lines);
			DEBUG?.(`panel set lines=${lines.length} head=${JSON.stringify((lines[0] ?? "").slice(0, 40))}`);
			if (!screen) install(ctx);
			screen?.invalidate();
			screen?.requestRender(true);
			DEBUG?.(`apply steps ok level=${next}`);
		} catch (e) {
			DEBUG?.(`apply steps threw: ${(e as Error)?.message ?? String(e)}`);
		}
	};

	// The latest settled assistant reply feeds the preview panel.
	pi.on("message_end", (event, ctx) => {
		const m = event.message as { role?: string; content?: Array<{ type?: string; text?: string }> };
		DEBUG?.(`message_end role=${m?.role} blocks=${Array.isArray(m?.content) ? m.content.length : -1}`);
		if (m?.role !== "assistant" || !Array.isArray(m.content)) return;
		const text = m.content.filter((b) => b?.type === "text" && b.text).map((b) => b.text).join("\n\n");
		if (text) {
			lastAssistant = text;
			ctx?.ui.setWidget(PANEL_KEY, foldPanelLines(lastAssistant, level, 78, learnedKeys));
		}
	});

	// The Alt keys: alt+1..5 by default, and the keys you teach once (`/fold
	// calibrate`, keys.ts) — which is what makes this general: on most ISO layouts a
	// terminal sends Alt as ESC + the unshifted glyph (`&` where US layouts send `1`,
	// é where ...), so alt+1..5 alone binds only on US/direct layouts and a
	// per-layout table would chase every keyboard forever. The learned keys register
	// at every load, so the level you taught Alt for is the level it folds to — on
	// any keyboard Pi can name a key for.
	const registerLevelKeys = (learned?: KeysMap) => {
		DEBUG?.(`registerLevelKeys learned=${learned ? JSON.stringify(learned) : "none"}`);
		for (const n of [1, 2, 3, 4, 5] as const) {
			pi.registerShortcut(`alt+${n}` as KeyId, {
				description: `Fold replies to ${levelName(n)}`,
				handler: async (ctx) => apply(n, ctx),
			});
			const taught = learned?.[String(n)];
			if (taught && taught !== `alt+${n}`) {
				DEBUG?.(`registerLevelKeys taught level=${n} key=${taught}`);

				pi.registerShortcut(taught as KeyId, {
					description: `Fold replies to ${levelName(n)} (the key you taught /fold calibrate)`,
					handler: async (ctx) => {
						DEBUG?.(`shortcut fired level=${n} taught`);
						await apply(n, ctx as ExtensionContext);
					},
				});
			}
		}
	};
	let learnedKeys = loadKeys();
	registerLevelKeys(learnedKeys);

	// --textfold-digits's shortcuts: a digit changes the level when the prompt is
	// empty and reinserts itself anywhere else, so the editor never swallows a pressed
	// digit the shortcut intercepted.
	const registerDigitKeys = () => {
		for (const n of [1, 2, 3, 4, 5] as const) {
			const key = `${n}` as KeyId;
			pi.registerShortcut(key, {
				description: `Fold replies to ${levelName(n)} when the prompt is empty; reinsert the digit otherwise`,
				handler: async (ctx) => {
					const editorText = (typeof ctx?.ui?.getEditorText === "function" ? ctx.ui.getEditorText() : "") ?? "";
					const decision = guardDigit({ editorText, digit: `${n}`, digitsEnabled: true });
					DEBUG?.(`digit ${n} decision=${decision.kind}${decision.kind === "level" ? ` level=${decision.level}` : ""} editorLen=${editorText.length}`);
					if (decision.kind === "level") {
						apply(decision.level, ctx as ExtensionContext);
					} else if (decision.kind === "reinsert") {
						// typed as a paste, so it lands where the cursor is, not at the end of the draft
						ctx.ui.pasteToEditor(`${n}`);
					}
				},
			});
		}
	};

	// `/fold calibrate`: teaches the five keys from your own presses. The overlay
	// receives every key event, names it with pi's parser, and binds the level being
	// asked for to what arrived — no table of layouts, any keyboard pi can name a key
	// for, including the Kitty-protocol terminals. A plain digit keeps that level's
	// default; escape leaves a previous learning untouched.
	const startCalibration = async (cmdCtx: ExtensionContext) => {
		if (cmdCtx.mode !== "tui") {
			cmdCtx.ui.notify("/fold calibrate needs the interactive UI", "warning");
			return;
		}
		const taught: Record<string, string> = {};
		const levels = [1, 2, 3, 4, 5] as const;
		let step = 0;
		let bare = false; // the last key came without Alt (a Mac's Option+1 types ¡)
		await cmdCtx.ui.custom((tui, _theme, _kb, done) => {
		const finish = (save: boolean): boolean => {
			if (save) {
			saveKeys(keysFile(), taught as KeysMap);
				learnedKeys = loadKeys();
				registerLevelKeys(learnedKeys);
				const summary = levels
					.map((n) => `${n}=${taught[String(n)] ?? `alt+${n}`}`)
					.join("  ");
				cmdCtx.ui.notify(`Saved your keys — every session starts with them: ${summary}`, "info");
				// Pi snapshots the shortcut map once at editor setup, so re-registered ids
				// dispatch only after that snapshot rebuilds. Reloading makes the taught
				// keys dispatch in THIS session; every next session loads them from the
				// file anyway (session_start → registerLevelKeys). Level continuity is on
				// disk (persist), so the reload does not move what you were reading.
				const rl = (cmdCtx as unknown as { reload?: () => Promise<void> }).reload;
				if (typeof rl === "function") void rl.call(cmdCtx).catch(() => DEBUG?.("reload after teaching failed"));
			}
				done(undefined);
				return true;
			};
			return {
				invalidate: (): void => {},
				render: (width: number): string[] => {
					const lines: string[] = [`TextFold — teach your keyboard (${width} cols)`];
					for (let i = 0; i < levels.length; i++) {
						const level = levels[i];
						const bound = taught[String(level)];
						const marker = i === step ? "  ← press Alt+it now" : "";
						lines.push(`  Level ${level} ${levelName(level)}: ${bound ?? "? " + marker}`);
					}
					lines.push("");
					if (bare) lines.push(...wrapTextWithAnsi(`That key came without Alt: ${KEYS_FIX}.`, Math.max(20, width - 1)), "");
					lines.push(
						"A plain digit on this step keeps that level's default (alt+<digit>).",
						"Escape leaves a previous teaching alone. Saved to " + keysFile(),
					);
					return lines;
				},
				handleInput: (data: string): boolean => {
					const parsed = String(parseKey(data) ?? data);
					if (parsed === "escape" || parsed === "ctrl+c") return finish(false);
					bare = !parsed.startsWith("alt+") && !/^[1-5]$/.test(parsed);
					if (bare) {
						tui?.requestRender?.(true);
						return true;
					}
					if (!parsed.startsWith("alt+")) {
						if (/^[1-5]$/.test(parsed)) {
							taught[String(parsed)] = `alt+${parsed}`;
							step = Number(parsed); // the digit itself names the level
							if (Object.keys(taught).length >= 5) return finish(true);
							tui?.requestRender?.(true);
						}
						return true; // teaching owns harmless keys; ctrl+c above stays ours too
					}
					taught[String(levels[step])] = parsed;
					step += 1;
					if (step >= levels.length) return finish(true);
					tui?.requestRender?.(true);
					return true;
				},
			};
		});
	};

	pi.registerCommand("fold", {
		description: "Fold replies: 1..5, a level name, off, or nothing for the next level",
		handler: async (arg: string | undefined, ctx) => {
			if (arg === "calibrate") {
				await startCalibration(ctx as ExtensionContext);
				return;
			}
			if (arg === "keys") {
				const learned = loadKeys();
				for (const n of [1, 2, 3, 4, 5]) {
					const taught = learned?.[String(n)];
					ctx.ui.notify(
						taught
							? `Level ${n} ${levelName(n)}: alt+${n} and ${taught} (taught)`
							: `Level ${n} ${levelName(n)}: alt+${n}`,
						"info",
					);
				}
				ctx.ui.notify(`Taught keys live in ${keysFile()} — /fold calibrate re-teaches`, "info");
				return;
			}
			const got = parseLevelArg(arg ?? "", level);
			if (got.error) {
				ctx.ui.notify(got.error, "warning");
				return;
			}
			if (got.level) apply(got.level, ctx);
		},
	});
}

const levelName = (n: number): string => LEVELS[n - 1] ?? "Full";
