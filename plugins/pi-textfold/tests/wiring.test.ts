import { beforeEach, describe, expect, test, vi } from "vitest";
import { mkdirSync, readFileSync, writeFileSync as fsWriteFileSync } from "node:fs";
import { ENTRY_TYPE } from "../extension/persist";

const writeFileSync = (p: string, s: string) => fsWriteFileSync(p, s);

/** Shape of the calibration overlay component the factory hands back. */
type TeachComponent = { render: (width: number) => string[]; handleInput: (data: string) => boolean };

const readMaybe = (p: string): string | undefined => {
	try {
		return readFileSync(p, "utf8");
	} catch {
		return undefined;
	}
};


beforeEach(() => {
	vi.resetModules();
	// Default the keys file into a per-process tmp dir. Tests that fire session_start
	// without setting the override would otherwise write the welcome marker to the
	// real home; tests that need their own file set it themselves afterwards.
	process.env.TEXTFOLD_PI_KEYS_FILE = `/tmp/textfold-wiring-${process.pid}/keys.json`;
});

type TestTheme = { fg: (_kind: string, s: string) => string; bold: (s: string) => string; dim: (s: string) => string };
type Transformer = (markdown: string, context: { messageType: string }) => string;
type MouseLike = { type: string; button: string; x: number; y?: number };
type WidgetFactory = (tui: unknown, theme: TestTheme) => {
	render: (width: number) => string[];
	handleMouse?: (event: MouseLike) => { handled?: boolean } | undefined;
};

// A fake pi with the surface the ported index.ts uses, recording everything it does.
function makePi(opts: { branch?: unknown[]; appendEntryThrows?: boolean; digitsFlag?: boolean; editorText?: string } = {}) {
	const handlers = new Map<string, (event: any, ctx: any) => unknown>();
	const shortcuts = new Map<string, { description: string; handler: (ctx: any) => void }>();
	const commands = new Map<string, { handler: (arg: string | undefined, ctx: any) => void }>();
	const entries: { type: string; payload: unknown }[] = [];
	const notifications: string[] = [];
	const widgets = new Map<string, { factory?: WidgetFactory; content?: string[]; opts?: unknown }>();
	const transformers: Transformer[] = [];
	const toolRenderers: ((name: string, next: () => unknown) => unknown)[] = [];
	const customCaptures: ((tui: unknown, theme: unknown, kb: unknown, done: () => void) => unknown)[] = [];
	let customDone: (() => void) | undefined;
	const calls: string[] = [];
	let editorText = opts.editorText ?? "";
	const editorWrites: string[] = [];
	const pastes: string[] = [];

	const branch = opts.branch as unknown[];
	const theme: TestTheme = { fg: (_kind: string, s: string) => s, bold: (s: string) => s, dim: (s: string) => s };
	const screen = {
		invalidate: () => {
			calls.push("invalidate");
		},
		requestRender: (hint: boolean) => {
			calls.push(`render:${hint ? "full" : "normal"}`);
		},
	};

	const pi = {
		on: (name: string, handler: (event: any, ctx: any) => unknown) => {
			handlers.set(name, handler);
			calls.push(`on:${name}`);
		},
		registerShortcut: (key: string, shortcut: { description: string; handler: (ctx: any) => void }) => {
			shortcuts.set(key, shortcut);
			calls.push(`shortcut:${key}`);
		},
		registerCommand: (name: string, command: { handler: (arg: string | undefined, ctx: any) => void }) => {
			commands.set(name, command);
			calls.push(`command:${name}`);
		},
		registerMarkdownTransformer: (t: Transformer) => {
			transformers.push(t);
			calls.push("markdownTransformer");
		},
		registerToolRenderer: (r: (name: string, next: () => unknown) => unknown) => {
			toolRenderers.push(r);
			calls.push("toolRenderer");
		},
		registerFlag: (name: string, flag: { type: string; default?: unknown; description?: string }) => {
			calls.push(`flag:${name}=${String(flag.default)}`);
		},
		getFlag: (name: string) => (name === "textfold-digits" ? opts.digitsFlag === true : undefined),
		appendEntry: (type: string, payload: unknown) => {
			if (opts.appendEntryThrows) throw new Error("disk full");
			entries.push({ type, payload });
			return true;
		},
	};
	const ctx = {
		mode: "tui",
		hasUI: true,
		screen,
		reloads: 0,
		// Command contexts get reload(); the calibration uses it to make the taught
		// keys dispatch in the SAME session (pi snapshots shortcuts at editor setup).
		reload: async () => {
			ctx.reloads++;
		},
		sessionManager: { getBranch: () => branch },
		ui: {
			notify: (message: string, _kind?: string) => notifications.push(message),
			custom: (factory: (tui: unknown, theme: unknown, kb: unknown, done: () => void) => unknown) => {
				customCaptures.push(factory);
				return new Promise<void>((resolve) => {
					customDone = resolve;
				});
			},
			getEditorText: () => editorText,
			setEditorText: (t: string) => {
				editorText = t;
				editorWrites.push(t);
			},
			pasteToEditor: (t: string) => {
				pastes.push(t);
			},
			setWidget: (key: string, build: unknown, placement?: unknown) => {
				const w: { factory?: WidgetFactory; content?: string[]; opts?: unknown } = { opts: placement };
				if (typeof build === "function") w.factory = build as WidgetFactory;
				else w.content = build as string[];
				widgets.set(key, w);
			},
		},
	};

	// Render one widget through its factory, the way the TUI would.
	const render = (key: string, width = 78): string[] => {
		const w = widgets.get(key);
		if (!w) throw new Error(`no widget ${key}`);
		if (w.factory) return w.factory(screen, theme).render(width);
		return w.content ?? [];
	};

	return {
		pi: pi as never,
		customCaptures,
		closeCustom: () => customDone?.(),
		handler: (name: string) => {
			const h = handlers.get(name);
			if (!h) throw new Error(`no ${name} handler registered`);
			return h;
		},
		press: (key: string) => shortcuts.get(key)?.handler(ctx as never),
		runCommand: async (name: string, arg?: string) => commands.get(name)?.handler(arg, ctx as never),
		widgets,
		render,
		notifications,
		calls,
		entries,
		ctx: ctx as never,
		screen,
		editorWrites,
		pastes,
		transformers,
		toolRenderers,
	};
	// The factory-based capture path needs resolve on done: kept where the tests use it.
}

const custom = (level: number, id = "e1") =>
	({ type: "custom", customType: ENTRY_TYPE, data: { level }, id, parentId: "p", timestamp: "2026-10-19T13:46:18.406Z" } as unknown);

const load = async (): Promise<(pi: unknown) => void> => {
	const m = await import("../extension/index.ts");
	const d = (m as unknown as { default: (pi: unknown) => void }).default;
	if (typeof d !== "function") throw new Error("index.ts must default-export its factory");
	return d;
};

describe("a restart restores the level the branch ended on", () => {
	test("a branch ending at level 4: the row marks it, a notify says so, and nothing is written", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [custom(4)] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(mk.entries).toEqual([]); // restore writes nothing
		expect(mk.notifications.join(" ")).toContain("restored");
		expect(mk.render("textfold").join(" ")).toContain("4 Outline"); // the row marks the level read at
	});

	// Per-process dir: the welcome marker persists on disk, so fixed paths would leak
	// "already welcomed" from one run into the next.
	const wt = (n: string) => `/tmp/textfold-wiring-${process.pid}/${n}/keys.json`;

	test("a fresh session starts at Full: one welcome notice, no entry, no re-render", async () => {
		// Behavior change, deliberate: a fresh session used to stay silent. Silence is
		// what strands users whose terminal eats Alt+numbers, so the first session
		// now says once how to check the keys and where to teach them.
		process.env.TEXTFOLD_PI_KEYS_FILE = wt("fresh");
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(mk.entries).toEqual([]);
		expect(mk.notifications).toHaveLength(1);
		expect(mk.notifications.join(" ")).toContain("/fold calibrate");
		expect(mk.render("textfold").join(" ")).toContain("1 Full");
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("the welcome fires once: a second session_start stays silent", async () => {
		process.env.TEXTFOLD_PI_KEYS_FILE = wt("once");
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(mk.notifications).toHaveLength(1);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		mk.handler("session_start")({ reason: "resume" }, mk.ctx);
		expect(mk.notifications).toHaveLength(1); // the marker, not the reason, decides
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("no welcome when keys are taught or the branch already folds", async () => {
		// Taught keys: the user proved their keyboard works.
		process.env.TEXTFOLD_PI_KEYS_FILE = wt("taught");
		mkdirSync(`/tmp/textfold-wiring-${process.pid}/taught`, { recursive: true });
		writeFileSync(wt("taught"), JSON.stringify({ "1": "alt+&" }));
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(mk.notifications).toEqual([]);
		// A branch that already folds: the user manages without the pointer.
		process.env.TEXTFOLD_PI_KEYS_FILE = wt("folded");
		const mk2 = makePi({ branch: [custom(3)] });
		textfold(mk2.pi);
		mk2.handler("session_start")({ reason: "startup" }, mk2.ctx);
		expect(mk2.notifications.join(" ")).toContain("restored");
		expect(mk2.notifications.join(" ")).not.toContain("calibrate");
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("a resume logs with any reason and restores just the same", async () => {
		const textfold = await load();
		for (const reason of ["startup", "resume", "fork", "new"]) {
			const mk = makePi({ branch: [custom(5, `s-${reason}`)] });
			textfold(mk.pi);
			mk.handler("session_start")({ reason }, mk.ctx);
			mk.press("alt+1"); // a real change (5 → 1) proves the restore took
			mk.press("alt+1"); // a no-op proves dedupe after the restore too
			expect(mk.entries).toEqual([{ type: ENTRY_TYPE, payload: { level: 1 } }]);
		}
	});

	test("a corrupt recent entry does not shadow an earlier valid one", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [custom(2, "a"), { type: "custom", customType: ENTRY_TYPE, data: { level: 99 } }] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(mk.render("textfold").join(" ")).toContain("2 Brief");
	});
});

describe("a level change writes one entry, at most once per change", () => {
	test("repeated presses write nothing; each real change writes one entry", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.press("alt+3");
		mk.press("alt+3");
		mk.press("alt+4");
		expect(mk.entries).toEqual([
			{ type: ENTRY_TYPE, payload: { level: 3 } },
			{ type: ENTRY_TYPE, payload: { level: 4 } },
		]);
	});

	test("appendEntry failing (a full disk) does not strand the extension: the level still changes and the reader still sees it", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [], appendEntryThrows: true });
		textfold(mk.pi);
		expect(() => mk.press("alt+5")).not.toThrow();
		expect(mk.notifications.join(" ")).toContain("Gist");
		expect(mk.entries).toEqual([]);
	});
});

describe("the fold command", () => {
	test("`/fold 4` moves the level like a keypress does, once per change", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.runCommand("fold", "4");
		mk.runCommand("fold", "4");
		expect(mk.entries).toEqual([{ type: ENTRY_TYPE, payload: { level: 4 } }]);
	});

	test("`/fold off` goes back to Full and writes that", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.runCommand("fold", "3");
		mk.runCommand("fold", "off");
		expect(mk.entries.map((e) => e.payload)).toEqual([{ level: 3 }, { level: 1 }]);
	});
});

describe("--textfold-digits registers plain digits from session_start, not the factory", () => {
	test("the flag is registered at the factory; no digit shortcuts before session_start", async () => {
		const textfold = await load();
		const mk = makePi({ digitsFlag: true });
		textfold(mk.pi);
		expect(mk.calls).toContain("flag:textfold-digits=false");
		expect(mk.calls).not.toContain("shortcut:3"); // factory time: the flag is not yet committed
		expect(mk.calls).toContain("shortcut:alt+3");
	});

	test("session_start with the flag on registers 1..5 once", async () => {
		const textfold = await load();
		const mk = makePi({ digitsFlag: true });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		for (const n of [1, 2, 3, 4, 5]) expect(mk.calls).toContain(`shortcut:${n}`);
		mk.handler("session_start")({ reason: "resume" }, mk.ctx); // idempotent across sessions
		expect(mk.calls.filter((c) => c === "shortcut:3")).toHaveLength(1);
	});

	test("session_start with the flag off registers no digits; alt keys untouched", async () => {
		const textfold = await load();
		const mk = makePi({ digitsFlag: false });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(mk.calls).not.toContain("shortcut:3");
		expect(mk.calls).toContain("shortcut:alt+3");
	});

	test("a digit on an empty editor folds to its level; in a draft it is typed where the cursor is", async () => {
		const textfold = await load();
		const mk = makePi({ digitsFlag: true, editorText: "" });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		mk.press("3");
		expect(mk.entries).toEqual([{ type: ENTRY_TYPE, payload: { level: 3 } }]);
		expect(mk.notifications.join(" ")).toContain("Points");

		const draft = makePi({ digitsFlag: true, editorText: "ab " });
		textfold(draft.pi);
		draft.handler("session_start")({ reason: "startup" }, draft.ctx);
		draft.press("3");
		expect(draft.pastes).toEqual(["3"]); // a paste lands at the cursor, not at the end of the draft
		expect(draft.editorWrites).toEqual([]);
		expect(draft.entries).toEqual([]); // no fold while typing
	});
});

describe("clicking the row folds: the pointer without the keyboard", () => {
	// The row's first line starts with one space; the plain row underneath sits at
	// x-1. "fold  1 Full  2 Brief  3 Points …": "3 Points" spans row-local 23..30.
	const click = (mk: { widgets: Map<string, { factory?: WidgetFactory }> }, x: number, button = "left") => {
		const f = mk.widgets.get("textfold")?.factory;
		if (!f) throw new Error("no row widget");
		const screen = { requestRender: () => {} };
		const theme: TestTheme = { fg: (_k: string, s: string) => s, bold: (s: string) => s, dim: (s: string) => s };
		return f(screen, theme).handleMouse?.({ type: "press", button, x, y: 0 });
	};

	test("a left press on a level segment folds to it, like its shortcut", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		const out = click(mk, 1 + 24); // inside "3 Points" (23..30)
		expect(out?.handled).toBe(true);
		expect(mk.entries).toEqual([{ type: ENTRY_TYPE, payload: { level: 3 } }]);
		expect(mk.notifications.join(" ")).toContain("Points");
	});

	test("presses on the label, the gaps, the hint, or another button fold nothing", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(click(mk, 1 + 0)).toBeUndefined(); // "fold" label
		expect(click(mk, 1 + 60)).toBeUndefined(); // the hint
		expect(click(mk, 1 + 24, "right")).toBeUndefined(); // not the primary button
		expect(mk.entries).toEqual([]);
	});
});

describe("the preview panel shows the fold before any reply and with it", () => {
	const reply = "## Head one\n\n" + "This is a longish paragraph whose point comes first when folded. ".repeat(4);

	test("the panel is installed at session start: header for the level read at, and an ellipsis for no reply yet", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		const lines = mk.render("textfold-preview");
		expect(lines[0]).toContain("TextFold · Full");
		expect(lines.join(" ")).toContain("…");
	});

	test("an assistant reply refreshes the panel content: the fold, not just the header", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		mk.handler("message_end")({ message: { role: "assistant", content: [{ type: "text", text: reply }] } }, mk.ctx);
		mk.press("alt+3");
		const lines = mk.render("textfold-preview");
		expect(lines[0]).toContain("TextFold · Points");
		expect(lines.join(" ")).toContain("Head one"); // real fold content, not the ellipsis
		expect(lines).not.toContain("…");
	});

	test("the row and the panel are distinct widgets, both present after a session start", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.handler("session_start")({ reason: "startup" }, mk.ctx);
		expect(mk.widgets.has("textfold")).toBe(true);
		expect(mk.widgets.has("textfold-preview")).toBe(true);
		expect(mk.render("textfold").join(" ")).toContain("1 Full");
	});
});

describe("reasoning folds at the level too", () => {
	// A coding turn is mostly thinking: thousands of words of it around a few hundred words
	// of reply. The transformer sees it too, as messageType "assistant-thinking".
	const thinking = Array.from({ length: 8 }, (_, i) =>
		`Step ${i} checks one thing before the next. It then says what it found and moves on.`,
	).join("\n\n");

	test("a folded reply and folded reasoning both carry the level marker", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.press("alt+3");
		const t = mk.transformers[0]!;
		const reply = t(thinking, { messageType: "assistant" });
		const reason = t(thinking, { messageType: "assistant-thinking" });
		expect(reply).toContain("Points · ");
		expect(reason).toContain("Points · ");
		expect(reason).toContain("Alt+1 unfolds");
		expect(reason.length).toBeLessThan(thinking.length); // it really is folded, not passed through
	});

	test("the reader's own message is never transformed", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.press("alt+5");
		expect(mk.transformers[0]!(thinking, { messageType: "user" })).toBe(thinking);
	});

	test("a kind of message the extension does not name is left as written", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.press("alt+5");
		expect(mk.transformers[0]!(thinking, { messageType: "something-new" })).toBe(thinking);
	});

	test("at Full nothing folds, reasoning included", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		expect(mk.transformers[0]!(thinking, { messageType: "assistant-thinking" })).toBe(thinking);
		expect(mk.transformers[0]!(thinking, { messageType: "assistant" })).toBe(thinking);
	});

	test("reasoning too short to gain from a fold is left as written", async () => {
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.press("alt+3");
		const short = "Look at the parser first. Then the tests.";
		expect(mk.transformers[0]!(short, { messageType: "assistant-thinking" })).toBe(short);
	});
});

describe("keys you teach once, for any keyboard", () => {
	const writeKeys = (map: Record<string, string>) => {
		process.env.TEXTFOLD_PI_KEYS_FILE = "/tmp/textfold-wiring-test-keys.json";
		writeFileSync("/tmp/textfold-wiring-test-keys.json", JSON.stringify(map));
	};

	test("a fresh extension registers alt+1..5 and nothing else", async () => {
		process.env.TEXTFOLD_PI_KEYS_FILE = "/tmp/textfold-wiring-test-unset.json";
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		for (const n of [1, 2, 3, 4, 5]) expect(mk.calls).toContain(`shortcut:alt+${n}`);
		expect(mk.calls).not.toContain("shortcut:alt+&");
	});

	test("learned keys register alongside, from session start, for any keyboard", async () => {
		writeKeys({ "1": "alt+&", "5": "alt+(" });
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		for (const id of ["shortcut:alt+&", "shortcut:alt+(", "shortcut:alt+1", "shortcut:alt+5"])
			expect(mk.calls).toContain(id);
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("a learned key folds like its digit: alt+& is Full, alt+( is Gist", async () => {
		writeKeys({ "1": "alt+&", "5": "alt+(" });
		const textfold = await load();
		const mk = makePi({ branch: [] });
		textfold(mk.pi);
		mk.press("alt+&"); // Gist's neighbor... Full from the start: a no-op
		expect(mk.entries).toEqual([]);
		mk.press("alt+5");
		mk.press("alt+&");
		mk.press("alt+(");
		expect(mk.entries).toEqual([
			{ type: ENTRY_TYPE, payload: { level: 5 } },
			{ type: ENTRY_TYPE, payload: { level: 1 } },
			{ type: ENTRY_TYPE, payload: { level: 5 } },
		]);
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("/fold calibrate teaches the keys from the user's own presses and saves them", async () => {
		process.env.TEXTFOLD_PI_KEYS_FILE = "/tmp/textfold-wiring-test-cal.json";
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const running = mk.runCommand("fold", "calibrate");
		const factory = mk.customCaptures[0];
		expect(factory).toBeDefined();
		// pi-tui hands the factory its own done(); the fake's promise resolves through it.
		const comp = factory({}, {}, {}, () => mk.closeCustom()) as unknown as TeachComponent;
		// The five presses in order: teach, then defaults where the user cannot produce
		// one (a plain digit = keep the default).
		comp.handleInput("\x1b&");
		comp.handleInput("2"); // skip: default stays
		comp.handleInput("\x1b'");
		comp.handleInput("\x1b4");
		comp.handleInput("\x1b(");
		await running;
		expect((mk.ctx as unknown as { reloads: number }).reloads).toBe(1); // teaching must be live in this same session too
		expect(mk.notifications.join(" ")).toContain("Saved");
		const map = JSON.parse(readFileSync("/tmp/textfold-wiring-test-cal.json", "utf8"));
		expect(map).toEqual({ "1": "alt+&", "2": "alt+2", "3": "alt+'", "4": "alt+4", "5": "alt+(" });
		expect(mk.calls).toContain("shortcut:alt+&"); // re-registered right after teaching
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("/fold calibrate on escape teaches nothing and saves nothing", async () => {
		process.env.TEXTFOLD_PI_KEYS_FILE = "/tmp/textfold-wiring-test-cancel.json";
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const running = mk.runCommand("fold", "calibrate");
		const comp = mk.customCaptures[0]({}, {}, {}, () => mk.closeCustom()) as unknown as TeachComponent;
		comp.handleInput("\x1b&");
		comp.handleInput("\x1b"); // escape
		await running;
		expect(readMaybe("/tmp/textfold-wiring-test-cancel.json")).toBeUndefined();
		expect((mk.ctx as unknown as { reloads?: number }).reloads ?? 0).toBe(0); // quitting teaches nothing live
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("/fold calibrate says so when a key comes without Alt, and still waits for that level", async () => {
		process.env.TEXTFOLD_PI_KEYS_FILE = "/tmp/textfold-wiring-test-bare.json";
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const running = mk.runCommand("fold", "calibrate");
		const comp = mk.customCaptures[0]({}, {}, {}, () => mk.closeCustom()) as unknown as TeachComponent;
		comp.handleInput("¡"); // Option+1 on a Mac whose terminal keeps Option for itself
		const shown = comp.render(78).join("\n");
		expect(shown).toContain("came without Alt");
		expect(shown).toContain("Level 1 Full: ?");
		comp.handleInput("\x1b1");
		expect(comp.render(78).join("\n")).not.toContain("came without Alt");
		comp.handleInput("\x1b");
		await running;
		expect(readMaybe("/tmp/textfold-wiring-test-bare.json")).toBeUndefined();
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});

	test("/fold keys says what is bound", async () => {
		process.env.TEXTFOLD_PI_KEYS_FILE = "/tmp/textfold-wiring-test-keys.json"; // written above
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		await mk.runCommand("fold", "keys");
		expect(mk.notifications.join(" ")).toContain("alt+&");
		process.env.TEXTFOLD_PI_KEYS_FILE = "";
	});
});

describe("what the extension registers", () => {
	test("alt keys, the fold command, the transformer, and the two handlers", async () => {
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		for (const n of [1, 2, 3, 4, 5]) expect(mk.calls).toContain(`shortcut:alt+${n}`);
		expect(mk.calls).toContain("command:fold");
		expect(mk.calls).toContain("markdownTransformer");
		expect(mk.calls).toContain("on:session_start");
		expect(mk.calls).toContain("on:before_agent_start");
	});

	test("the tool-row resolver runs, alongside the markdown transformer", async () => {
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		expect(mk.toolRenderers).toHaveLength(1);
	});

	test("a before_agent_start adds the fold style as a section, leaving the other options", async () => {
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const event = { systemPromptOptions: { sections: { keepme: "kept" }, model: "x" } };
		const out = mk.handler("before_agent_start")(event, mk.ctx);
		expect((event.systemPromptOptions.sections as Record<string, string>).textfold).toContain("can fold your replies");
		expect((event.systemPromptOptions.sections as Record<string, string>).keepme).toBe("kept");
		expect(event.systemPromptOptions.model).toBe("x");
		expect(out).toEqual({});
	});
});

describe("tool rows fold at the level", () => {
	type Comp = { render: (width: number) => string[]; invalidate?: () => void };
	const theme: TestTheme = { fg: (_kind: string, s: string) => s, bold: (s: string) => s, dim: (s: string) => s };

	/** A stand-in for Pi's built-in renderers: one call line, four result lines. */
	function builtIn() {
		const made: Comp[] = [];
		const seen: unknown[] = [];
		const base = {
			renderShell: "default",
			renderCall: (args: { command?: string }) => ({ render: () => [` $ ${args.command ?? ""}`] }),
			renderResult: (_result: unknown, options: { expanded?: boolean }, _theme: unknown, ctx: { lastComponent?: unknown }) => {
				seen.push(ctx.lastComponent);
				const component: Comp = options.expanded
					? { render: () => [" a", " b", " c", " d", " e"], invalidate() {} }
					: { render: () => ["", " a", " b", " c", " d", ""], invalidate() {} };
				made.push(component);
				return component;
			},
		};
		return { base, made, seen };
	}

	const setLevel = async (mk: ReturnType<typeof makePi>, n: number) => {
		await mk.press(`alt+${n}`);
	};

	test("Full and Brief draw Pi's own row; Points keeps two lines; Outline and Gist keep none", async () => {
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const { base } = builtIn();
		const wrapped = mk.toolRenderers[0]!("bash", () => base) as typeof base & {
			renderResult: (r: unknown, o: { expanded: boolean }, t: unknown, c: any) => Comp;
		};

		const draw = (expanded = false) => {
			const ctx = { state: {}, lastComponent: undefined, isPartial: false };
			return wrapped.renderResult({}, { expanded }, theme, ctx).render(80);
		};

		expect(draw()).toEqual(["", " a", " b", " c", " d", ""]); // Full: byte-identical to Pi
		await setLevel(mk, 2);
		expect(draw()).toEqual(["", " a", " b", " c", " d", ""]); // Brief: Pi's preview
		await setLevel(mk, 3);
		expect(draw()).toEqual([" a", " b", "… (2 more lines · Alt+1)"]);
		await setLevel(mk, 4);
		expect(draw()).toEqual([]);
		await setLevel(mk, 5);
		expect(draw()).toEqual([]);
		await setLevel(mk, 1);
		expect(draw()).toEqual(["", " a", " b", " c", " d", ""]);
	});

	test("the call line stays Pi's, and only the result renderer is wrapped", async () => {
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const { base } = builtIn();
		const wrapped = mk.toolRenderers[0]!("bash", () => base) as typeof base;
		expect(wrapped.renderCall).toBe(base.renderCall);
		expect(wrapped.renderShell).toBe("default");
	});

	test("the built-in renderer gets its own component back, never our wrapper", async () => {
		// Pi passes the component we returned last time as lastComponent. The built-in
		// renderers clear() and addChild() on it, so handing them the wrapper would
		// nest a wrapper per redraw.
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const { base, made, seen } = builtIn();
		const wrapped = mk.toolRenderers[0]!("bash", () => base) as typeof base & {
			renderResult: (r: unknown, o: { expanded: boolean }, t: unknown, c: any) => Comp;
		};
		const state: Record<string, unknown> = {};
		const first = wrapped.renderResult({}, { expanded: false }, theme, { state, lastComponent: undefined, isPartial: false });
		expect(seen[0]).toBeUndefined();
		wrapped.renderResult({}, { expanded: false }, theme, { state, lastComponent: first, isPartial: false });
		expect(seen[1]).toBe(made[0]);
		expect(seen[1]).not.toBe(first);
	});

	test("an expanded row shows everything: asking for one row outranks the fold", async () => {
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const { base } = builtIn();
		const wrapped = mk.toolRenderers[0]!("bash", () => base) as typeof base & {
			renderResult: (r: unknown, o: { expanded: boolean }, t: unknown, c: any) => Comp;
		};
		await setLevel(mk, 5);
		const out = wrapped.renderResult({}, { expanded: true }, theme, { state: {}, lastComponent: undefined, isPartial: false }).render(80);
		expect(out).toEqual([" a", " b", " c", " d", " e"]);
	});

	test("tools without a result renderer, and tools Pi does not know, are left alone", async () => {
		const textfold = await load();
		const mk = makePi();
		textfold(mk.pi);
		const resolver = mk.toolRenderers[0]!;
		expect(resolver("mystery", () => undefined)).toBeUndefined();
		const callOnly = { renderShell: "self" as const, renderCall: () => ({ render: () => ["x"] }) };
		expect(resolver("mystery", () => callOnly)).toBe(callOnly);
	});
});
