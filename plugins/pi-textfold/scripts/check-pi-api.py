#!/usr/bin/env python3
"""S0 preflight: the installed pi must expose every extension API TextFold for
Pi relies on. Exits 0 with a one-line OK, else exits 1 naming the missing API.

Looked up in this order: TEXTFOLD_PI_CLI's package dirdist tree, the npm global
install, then the newest npx cache entry.
"""
import glob, os, re, sys

WANT = {
    "registerMarkdownTransformer": "F-C1 in-place fold",
    "registerToolRenderer": "tool rows fold at the level (D4)",
    "registerShortcut": "key UX",
    "registerFlag": "digit-guard flag (C2)",
    "appendEntry": "level persistence (C1)",
    "registerEntryRenderer": "custom entry rendering",
    "ToolRenderContext": "tool-row wrapper keeps the built-in component (D4)",
    "getEditorText": "digit-guard reinsertion (C2)",
    "setEditorText": "digit-guard reinsertion (C2)",
    "custom": "/fold calibrate teaching overlay",
    "reload": "taught keys dispatch in the session they are taught",
}

# parseKey comes from the sibling package, not the extension API surface: the
# calibration overlay names each pressed key with it, so it gets its own check.
TUI_WANT = {"parseKey": "/fold calibrate key naming"}

def dist_candidates():
    cands = []
    cli = os.environ.get("TEXTFOLD_PI_CLI", "")
    if cli:
        cands.append(os.path.join(os.path.dirname(cli), "..", ".."))
    cands += [
        os.path.expanduser("~/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent/dist"),
    ] + sorted(glob.glob(os.path.expanduser(
        "~/.npm/_npx/*/node_modules/@earendil-works/pi-coding-agent/dist")), reverse=True)
    return [c for c in cands if os.path.exists(c)]

def tui_candidates(agent_dist):
    cands = [
        os.path.join(os.path.dirname(__file__), "..", "node_modules",
                     "@earendil-works", "pi-tui", "dist"),
    ]
    if agent_dist:
        cands.append(os.path.join(agent_dist, "..", "node_modules",
                                  "@earendil-works", "pi-tui", "dist"))
    return [c for c in cands if os.path.exists(c)]

def main():
    types = None
    agent_dist = None
    for dist in dist_candidates():
        f = os.path.join(dist, "core", "extensions", "types.d.ts")
        if os.path.exists(f):
            types = f
            agent_dist = dist
            break
    if not types:
        print("FAIL: no pi-coding-agent dist found; set TEXTFOLD_PI_CLI", file=sys.stderr)
        return 1
    text = open(types, encoding="utf-8").read()
    missing = [api for api in WANT if not re.search(rf"\b{re.escape(api)}\s*[<(]", text)]
    if missing:
        print("FAIL", types)
        for api in missing:
            print(f"  missing {api}  (needed for {WANT[api]})")
        return 1
    tui = None
    for dist in tui_candidates(agent_dist):
        f = os.path.join(dist, "keys.d.ts")
        if os.path.exists(f):
            tui = f
            break
    if not tui:
        print("FAIL: no pi-tui dist found (needed for parseKey: /fold calibrate key naming)",
              file=sys.stderr)
        return 1
    tuitext = open(tui, encoding="utf-8").read()
    # keys.d.ts declares it (`export declare function parseKey(...)`); the
    # package index only re-exports the name, which this regex would miss.
    tui_missing = [api for api in TUI_WANT if not re.search(rf"\b{re.escape(api)}\s*[<(]", tuitext)]
    if tui_missing:
        print("FAIL", tui)
        for api in tui_missing:
            print(f"  missing {api}  (needed for {TUI_WANT[api]})")
        return 1
    print(f"OK: all {len(WANT)} extension APIs present in {types}; "
          f"pi-tui exports parseKey ({tui})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
