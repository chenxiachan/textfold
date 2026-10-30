"""Any OpenAI-compatible chat endpoint: Zhipu, Qwen, DeepSeek, Kimi, OpenRouter, OpenAI, Google's
compatible layer, MiniMax, a local Ollama, or a custom one (the providers of ThoughtDAG).

Not every provider honours a JSON schema, so the schema rides in the prompt and JSON mode is asked for;
a provider that rejects JSON mode is asked again without it.

A service that answers only streamed (server-sent events) is read too: a reply that comes as a stream is joined into
one, and a service that refuses a request not streamed is asked again with "stream": true. Every other endpoint gets
the requests it got before: the stream is asked for only after it refused them. WorkBuddy Cloud's applications
(*.workbuddy.host), which take a stream alone, are asked for one at once, with their publishable key sent the way
their SDK sends it.

A reasoning level (spec["effort"]: off, low, medium, high; None for the model's own) is spelled the way each provider
documents it. These spellings change as APIs change, so none of them is relied on: a request a provider refuses is
sent again the next way, and last with no reasoning field at all, which every endpoint takes. The way that worked is
remembered for the rest of the run.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

_WORKED = {}   # (base, model, level) -> index of the request shape that was accepted
WAITS = (5, 15, 30, 45)   # seconds before asking again when rate-limited (free models: 20 requests a minute)


def _workbuddy(base):
    """An application of WorkBuddy Cloud: its chat completions are streamed only (its SDK: "non-streaming is
    unsupported"), and its key is the application's publishable key."""
    host = urllib.parse.urlparse(base).hostname or ""
    return host.endswith(".workbuddy.host")


def auth(base, api_key):
    """The headers that carry a key: a bearer token, as every OpenAI-compatible endpoint takes it; for WorkBuddy Cloud
    the publishable key in x-wb-webapp-access-key and no Authorization, which its SDK keeps for a signed-in user's
    session."""
    if _workbuddy(base):
        return {"x-wb-webapp-access-key": api_key or ""}
    return {"Authorization": f"Bearer {api_key or 'none'}"}


def _post(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        kind = r.headers.get("Content-Type") or ""
        raw = r.read()
    if "text/event-stream" in kind or raw.lstrip().startswith(b"data:"):   # streamed, asked for or not
        d = _events(raw)
        # Some OpenAI-compatible relays answer a whole completion under the stream's header: one JSON object, no data:
        # line at all, which _events() reads as nothing. Read that body as the completion it is, rather than fail the
        # call "no JSON in reply" with the reply right there in it. A stream that really was one keeps its own text.
        if _content(d) == "":
            try:
                return json.loads(raw)
            except ValueError:
                pass
        return d
    return json.loads(raw)


def _events(raw):
    """A streamed reply (data: {chunk} lines, data: [DONE] last) as one chat completion: the pieces of its first
    choice joined, its usage if a chunk carries one. A chunk that is only an error is given back as it came."""
    text, usage = [], None
    for line in raw.decode("utf-8", errors="replace").split("\n"):   # (not splitlines: a reply may hold U+2028)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error") and not chunk.get("choices"):
            return chunk
        usage = chunk.get("usage") or usage
        for c in chunk.get("choices") or []:
            if not isinstance(c, dict) or c.get("index", 0) != 0:
                continue
            part = c.get("delta") or c.get("message") or {}
            piece = part.get("content") if isinstance(part, dict) else None
            if isinstance(piece, list):
                piece = "".join((p.get("text") or "") if isinstance(p, dict) else str(p) for p in piece)
            if isinstance(piece, str):
                text.append(piece)
    return {"choices": [{"message": {"content": "".join(text)}}], **({"usage": usage} if usage else {})}


def _content(d):
    """The reply's text: message.content, a string on most endpoints, a list of parts ({type: text, text}) on some.
    None when the body is not a chat completion at all (an error sent with HTTP 200)."""
    choices = d.get("choices") if isinstance(d, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(first, dict):
        return None
    msg = first.get("message")
    c = msg.get("content") if isinstance(msg, dict) else msg
    if isinstance(c, list):
        c = "".join((p.get("text") or "") if isinstance(p, dict) else str(p) for p in c)
    return c if isinstance(c, str) else ""


def reasoning(base, level):
    """The fields that ask a provider for a level of reasoning, most fitting first: the provider's own spelling, then
    OpenAI's reasoning_effort (which many take), for off down to the least a model allows (some always think), and
    last {} (the model's default), which every endpoint takes."""
    host = urllib.parse.urlparse(base).netloc
    if level is None:
        return ([{"reasoning": {"enabled": True}}] if "openrouter.ai" in host else []) + [{}]
    on = level != "off"
    generic = [{"reasoning_effort": level}] if on else [{"reasoning_effort": x} for x in ("none", "minimal", "low")]
    toggle = {"thinking": {"type": "enabled" if on else "disabled"}}
    if "openrouter.ai" in host:
        own = [{"reasoning": {"effort": level}}] if on else [{"reasoning": {"enabled": False}}, {"reasoning": {"effort": "low"}}]
    elif any(h in host for h in ("bigmodel.cn", "z.ai")):   # GLM: a level by reasoning_effort, off by the toggle (where it exists)
        own, generic = ([], generic + [toggle]) if on else ([toggle], generic)
    elif any(h in host for h in ("deepseek.com", "moonshot.", "kimi.com")):
        own = [toggle]
    elif "dashscope" in host:   # Qwen
        own = [{"enable_thinking": on, **({"thinking_budget": {"low": 2048, "medium": 8192}[level]} if level in ("low", "medium") else {})}]
    elif "minimax" in host:     # always thinks; nothing to set
        own, generic = [], []
    else:
        own = []
    return own + generic + [{}]


def call(prompt, schema, spec, system, timeout):
    base = (spec.get("base") or "https://api.openai.com/v1").rstrip("/")
    level = spec.get("effort")
    body = {
        "model": spec["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{prompt}\n\nReply with one JSON object that matches this JSON Schema:\n{json.dumps(schema)}"},
        ],
    }
    headers = {"Content-Type": "application/json", **auth(base, spec.get("api_key"))}
    if "openrouter.ai" in base:
        headers["X-Title"] = "TextFold"
    ways = reasoning(base, level)
    key = (base, spec["model"], level)
    shapes = [(r, jm, False) for r in ways for jm in (True, False)]   # each reasoning shape with JSON mode, then without
    streamed = len(shapes)
    shapes += [({}, jm, True) for jm in (True, False)]   # last, for a service that answers only streamed: the plain request
    if _workbuddy(base):   # streamed only: every shape as a stream
        shapes, streamed = [(r, jm, True) for r in ways for jm in (True, False)], 0
    start = _WORKED.get(key, 0)
    last, busy = None, 0
    i = start
    while i < len(shapes):
        r, jm, st = shapes[i]
        try:
            d = _post(f"{base}/chat/completions", {**body, **r, **({"response_format": {"type": "json_object"}} if jm else {}),
                                                   **({"stream": True} if st else {})}, headers, timeout)
        except urllib.error.HTTPError as e:
            last = e
            said = e.read()[:300].decode(errors="replace")
            if e.code in (429, 502, 503, 529) and busy < len(WAITS):   # rate-limited or busy: wait, then the same again
                after = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(60, float(after)) if after and after.replace(".", "", 1).isdigit() else WAITS[busy])
                busy += 1
                continue
            if e.code in (400, 404, 415, 422) and i + 1 < len(shapes):   # this shape refused: the next one
                # (a service that takes only a stream says so: straight to the streamed requests)
                i = streamed if i < streamed and "stream" in said.lower() else i + 1
                continue
            raise RuntimeError(f"{spec.get('provider') or base}: HTTP {e.code} {said}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"{spec.get('provider') or base}: {e.reason}")
        text = _content(d)
        if text is None:   # not a completion: what the provider said instead, after the other request shapes
            last = RuntimeError(f"{spec.get('provider') or base}: not a chat completion: {str((d.get('error') or d.get('detail') or d) if isinstance(d, dict) else d)[:300]}")
            if i + 1 < len(shapes):
                i += 1
                continue
            raise last
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        m = re.search(r"\{.*\}", text, re.S)
        if not m and i + 1 < len(shapes) and r:   # an answer left in the reasoning, none in the reply: ask the plain way
            i += 1
            continue
        if not m:
            raise RuntimeError("no JSON in reply")
        _WORKED[key] = i
        cost = ((d.get("usage") or {}).get("cost")) if "openrouter.ai" in base else None
        return json.loads(m.group(0)), cost
    raise RuntimeError(f"{spec.get('provider') or base}: {last}")
