"""Tokens, sentences and the word alignment the morph relies on.

A unit's text is a stream of tokens: words and punctuation carry their own
text, inline objects (math, citations, cross-references) are atoms that
stay whole. Every token remembers whether whitespace followed it in the
source, so a verbatim level renders exactly as the author wrote it.
"""
import re
import unicodedata

# a token: {"t": text} or {"a": atom id}; optional "s": 1 (space after), "f": font classes
# a CJK character (Han or kana) is a token of its own (the morph slides characters, as ThoughtDAG's Chinese plaques do)
CJK = "\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f"  # kana too: Japanese is not spaced


def _marks():
    """Combining marks (Unicode category M), which re's \\w does not match: the vowel signs of Hindi
    (पहला is प ह ल + ा) would otherwise split a word into pieces."""
    out, start = [], None
    for cp in range(0x300, 0x10000):
        is_mark = unicodedata.category(chr(cp)).startswith("M")
        if is_mark and start is None:
            start = cp
        elif not is_mark and start is not None:
            out.append(f"{chr(start)}-{chr(cp - 1)}")
            start = None
    return "".join(out)


MARKS = _marks()
# Thai has no space between its words, and no dictionary comes with Python: it is cut into syllables, the way CJK is
# cut into characters. A syllable here is a consonant with the vowel signs and tone marks written around it (the
# vowels เ แ โ ใ ไ stand before it); the reader breaks a line only between the words of the browser's own dictionary.
THAI = "\u0e00-\u0e7f"
_SYLLABLE = "[เแโใไ]*[ก-ฮ][\u0e30-\u0e3a\u0e45\u0e47-\u0e4e]*"
_LETTER = rf"[^\W_{CJK}{THAI}](?:[^\W_{CJK}{THAI}]|[{MARKS}])*"
WORD = re.compile(rf"[{CJK}]|{_SYLLABLE}|\d+(?:[.,]\d+)+%?|[๐-๙]+|{_LETTER}(?:['’\-]{_LETTER})*|[{THAI}]|\S", re.U)
ABBREV = {
    "e", "g", "i", "al", "etc", "vs", "cf", "fig", "figs", "eq", "eqs", "sec", "secs", "tab",
    "ref", "refs", "no", "approx", "resp", "ca", "viz", "dr", "mr", "ms", "prof", "st", "jr",
    "inc", "ltd", "co", "appx", "app", "thm", "def", "lem", "prop", "cor", "ch",
}
STOP = {
    "the", "a", "an", "of", "to", "in", "is", "are", "was", "were", "be", "been", "and", "or",
    "that", "this", "these", "those", "it", "its", "for", "on", "with", "as", "by", "at", "from",
    "we", "our", "their", "they", "which", "while", "than", "then", "into", "can", "not", "but",
    "such", "also", "has", "have", "had", "does", "do", "more", "most", "less", "only", "both",
    "的", "了", "是", "在", "和", "与", "或", "这", "那", "就", "也", "都", "而", "及", "其", "之", "对", "中", "为",
}


def is_cjk(s):
    return bool(re.search(f"[{CJK}]", s or ""))


def tokenize_text(text, font=None, out=None):
    """Append the tokens of a plain string to out; whitespace marks the previous token."""
    out = [] if out is None else out
    for m in re.finditer(r"\s+|" + WORD.pattern, text):
        piece = m.group(0)
        if piece.isspace():
            if out:
                out[-1]["s"] = 1
            continue
        tok = {"t": piece}
        if font:
            tok["f"] = font
        out.append(tok)
    return out


GREEK = "\u0370-\u03ff\u1f00-\u1fff"


def is_greek(s):
    return bool(re.search(f"[{GREEK}]", s or ""))


def split_sentences(toks):
    """Sentence ranges [start, end) over a token list. A sentence opens on a capital of any alphabet (Потом, Über,
    Ήρθε, not only A to Z), and a single capital before a stop is an initial. Greek asks with ; (or its own sign,
    U+037E), so a ; ends a sentence after a Greek word."""
    greek_q = {";", "\u037e"}
    cjk_stops = {"。", "！", "？"}
    dandas = {"।", "॥"}   # Hindi's full stops; Devanagari has no capitals to start the next sentence
    stops = {".", "?", "!"} | cjk_stops | dandas
    closers = {")", "]", "}", "）", "］", "｝", "】", "〕", "〉", "》", "」", "』", "”", '"', "’", "'", "»"}
    tails = stops | closers
    bounds, start, n = [], 0, len(toks)
    for i, tk in enumerate(toks):
        t = tk.get("t")
        prev = (toks[i - 1].get("t") or "") if i > 0 else ""
        if i < start or not (t in stops or (t in greek_q and is_greek(prev))):
            continue
        if t == "." and (prev.lower() in ABBREV or (len(prev) == 1 and prev.isupper())):
            continue
        end = i + 1
        # Keep a run of stops and any closing quotes/brackets with the sentence it ends.
        while end < n and not toks[end - 1].get("s") and toks[end].get("t") in tails:
            end += 1
        if end >= n:
            break
        nxt = toks[end]
        head = nxt.get("t") or ""
        # CJK sentences have neither spaces nor capital letters. They may also use ASCII stops.
        cjk = any(toks[j].get("t") in cjk_stops for j in range(i, end)) or is_cjk(prev) or is_cjk(head)
        if cjk and t == "." and i == start + 1 and prev.isdigit():
            cjk = False   # "1.研究背景": a number and its stop open a heading or a list item, they end no sentence
        danda = any(toks[j].get("t") in dandas for j in range(i, end))
        if cjk or danda or (toks[end - 1].get("s") and ("a" in nxt or head[:1].isupper() or re.match(r"[0-9(\[“\"‘«]", head))):
            bounds.append((start, end))
            start = end
    if start < n:
        bounds.append((start, n))
    return bounds


def norm(tok, atoms):
    """What two tokens must share to count as the same word in the morph; '' never matches."""
    if "a" in tok:
        return "atom:" + re.sub(r"\s+", "", atoms.get(tok["a"], {}).get("alt", tok["a"]))
    if "x" in tok:
        return "atom:" + re.sub(r"\s+", "", tok["x"])
    w = re.sub(rf"[^\w{MARKS}]", "", (tok.get("t") or "").lower())   # (a vowel sign or tone mark is part of its syllable)
    if not w or w in STOP:
        return ""
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def lcs_pairs(a, b):
    """Longest common subsequence of two lists of normalised words, as index pairs; '' never matches."""
    n, m = len(a), len(b)
    if not n or not m:
        return []
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] and a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    out, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] and a[i] == b[j]:
            out.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def plain(toks, atoms, math_dollars=True):
    """A token run as text for the model: math as $latex$, other atoms as their visible text."""
    s = ""
    for k, tk in enumerate(toks):
        if "a" in tk:
            at = atoms.get(tk["a"], {})
            piece = f"${at.get('alt', '')}$" if at.get("kind") == "math" and math_dollars else at.get("text", "")
        elif "x" in tk:
            piece = f"${tk['x']}$"
        else:
            piece = tk["t"]
        s += piece
        if tk.get("s") and k + 1 < len(toks):
            s += " "
    return s.strip()


def words(toks):
    return sum(1 for tk in toks if "t" in tk and re.search(r"\w", tk["t"]))
