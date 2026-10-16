"""One catalog of every model this computer can reach, and the settings that choose one.

  agent models  claude/claude-code/opus, codex/codex/<m>, pi/<provider>/<m>   (agents.py, scanned at startup)
  API models    <model id> from an env provider or a provider added in the settings (providers.py)

ThoughtDAG's rules: an API id that two providers share belongs to the env provider; the default is the
reader's choice, else Claude Code's sonnet when Claude Code is here, else the first model there is.
"""
import json
import os
import shutil

from . import agents, bridges, providers
from .store import HOME, OLD_HOMES

SETTINGS = HOME / "settings.json"
# How much the model may think, per task, in levels every caller maps onto its own (llm.py). Not a setting: a reader is
# here to read. Measured on one paper (2026-10-04): thinking adds time, not quality, to the levels and the
# translation, while the links need some.
TASKS = ("ladder", "links", "translate", "ask")
LEVELS = ("default", "off", "low", "medium", "high")
DEFAULT_THINKING = {"ladder": "off", "links": "low", "translate": "off", "ask": "low"}
LEGACY_ALIASES = {"fable", "opus", "sonnet", "haiku"}
# the Anthropic API spoken natively (official SDK), offered as one more preset in the settings
ANTHROPIC_PRESET = {"id": "anthropic", "name": "Anthropic", "base": "https://api.anthropic.com", "protocol": "anthropic",
                    "fixed": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"], "keyUrl": "https://console.anthropic.com/settings/keys"}


def load_settings():
    s = {"model": None, "ask_model": None, "ask_effort": None, "efforts": {}, "providers": [], "use_thoughtdag_env": False,
         "pdf_tier": "fast", "zotero": {}}
    old_home = next((h for h in OLD_HOMES if (h / "settings.json").exists()), None)
    if not SETTINGS.exists() and old_home:   # from before the name TextFold: copied, kept
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_home / "settings.json", SETTINGS)
        os.chmod(SETTINGS, 0o600)
    if SETTINGS.exists():
        old = json.loads(SETTINGS.read_text(encoding="utf-8"))
        s.update({k: v for k, v in old.items() if k in s})
        if not s["model"] and old.get("backend") == "claude-cli":  # the settings of v0.4
            s["model"] = f"claude/claude-code/{old.get('cli_model') or 'sonnet'}"
        if old.get("use_thoughtdag_env", True):  # still reading ThoughtDAG's .env: copy its providers once, then stop
            import_thoughtdag_env(s)
            save_settings(s)
    return s


def import_thoughtdag_env(s):
    """Until v0.8 the keys in ThoughtDAG's .env (a sibling folder) were read live. The reader now stands on its own:
    the providers found there are copied once into its own settings, keys and model lists included, and the link is cut."""
    have = {p["key"] for p in s["providers"]}
    for p in providers.env_providers(True):
        if p["source"] != "thoughtdag" or p["key"] in have:
            continue
        s["providers"].append({"key": p["key"], "preset": p["key"], "name": p["name"], "base": p["base"], "api_key": p["api_key"],
                               "models": [{"id": m["id"]} for m in p["models"]][:providers.MAX_MODELS]})
    s["use_thoughtdag_env"] = False


def save_settings(s):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
    os.chmod(SETTINGS, 0o600)


def provider_list(s):
    if os.environ.get("TEXTFOLD_NO_MODELS"):   # (agents.scan): no provider, no bridge
        return []
    env = providers.env_providers(s.get("use_thoughtdag_env", True), providers.ollama_models())
    ui = []
    for p in providers.ui_providers(s):
        raw = next(x for x in s["providers"] if x["key"] == p["key"])
        if raw.get("protocol") == "anthropic":
            p["protocol"] = "anthropic"
        ui.append(p)
    # the ChatGPT bridge, when it runs, joins without being added (unless it was added by hand)
    bridge = bridges.bridge_provider(providers.NOT_TEXT)
    if bridge and not any(p["base"].rstrip("/") == bridges.BRIDGE for p in ui):
        env.append(bridge)
    return env + ui


def catalog(s=None):
    s = s or load_settings()
    out, seen = [], set()
    for m in agents.models():
        out.append({"id": m["id"], "name": m["name"], "kind": "agent", "group": m["runtime"],
                    "efforts": m.get("efforts") or [], "defaultEffort": m.get("defaultEffort")})
        seen.add(m["id"])
    provs = provider_list(s)
    for p in provs:
        for m in p["models"]:
            if m["id"] in seen:
                continue
            seen.add(m["id"])
            out.append({"id": m["id"], "name": m["name"], "kind": "api", "group": p["key"], "efforts": [], "defaultEffort": None})
    return out, provs


def default_model(s=None, cat=None):
    s = s or load_settings()
    cat = cat if cat is not None else catalog(s)[0]
    ids = [m["id"] for m in cat]
    if s.get("model") in ids:
        return s["model"]
    for pref in ("claude/claude-code/sonnet", "gpt-5.5", "openrouter/free"):   # Claude Code, the ChatGPT plan, OpenRouter's free models
        if pref in ids:
            return pref
    return ids[0] if ids else None


def resolve(model_id=None, s=None):
    """A model id to what it takes to call it."""
    s = s or load_settings()
    if not model_id:
        model_id = default_model(s)
        if not model_id:
            raise RuntimeError("No model is available: install Claude Code, Codex or Pi, or add an API provider in the settings")
    if model_id in LEGACY_ALIASES:  # the command line's --model sonnet
        model_id = f"claude/claude-code/{model_id}"
    effort = None   # set per task (for_task); the per-model efforts of v0.9 and before are no longer read
    rt = agents.runtime_of(model_id)
    if rt:
        rest = model_id[len(agents.PREFIX[rt]):]
        model = rest.split("/", 1)[1] if rt in ("claude-code", "codex") else rest  # pi keeps provider/model
        return {"id": model_id, "kind": "agent", "runtime": rt, "model": model, "effort": effort}
    for p in provider_list(s):
        if any(m["id"] == model_id for m in p["models"]):
            return {"id": model_id, "kind": "api", "protocol": p["protocol"], "base": p["base"], "api_key": p["api_key"],
                    "provider": p["name"], "model": model_id, "effort": None}
    raise RuntimeError(f"Model {model_id} is not available (its agent or provider is gone)")


def for_task(spec, task, s=None):
    """The spec a task is run with: the reasoning level for the task. A spec that names its own effort keeps it (the
    command line's, the model bench's)."""
    if spec.get("effort") is not None:
        return spec
    level = DEFAULT_THINKING[task]
    return {**spec, "effort": None if level == "default" else level}


def ask_choice(s=None, cat=None):
    """What questions are answered with, as the settings say: (a model of their own or None for the levels' model, a
    reasoning level or None for the questions' default). One no longer there counts as none."""
    s = s or load_settings()
    cat = cat if cat is not None else catalog(s)[0]
    own = next((m for m in cat if m["id"] == s.get("ask_model")), None)
    used = own or next((m for m in cat if m["id"] == default_model(s, cat)), None)
    effort = s.get("ask_effort") if used and s.get("ask_effort") in (used.get("efforts") or []) else None
    return (own["id"] if own else None), effort


def ask_spec(s=None):
    """The model a question is answered with, and how hard it reasons: the settings' own for questions (a stronger
    model, more reasoning), else the one the levels are written with, at the questions' default (DEFAULT_THINKING)."""
    s = s or load_settings()
    model_id, effort = ask_choice(s)
    spec = resolve(model_id, s)
    return {**spec, "effort": effort} if effort else for_task(spec, "ask", s)


def label(model_id, s=None):
    cat, _ = catalog(s)
    return next((m["name"] for m in cat if m["id"] == model_id), model_id or "")
