"""The languages a paper can be read in. A text's levels are written first in its own language (detect), and every
translation is built from them: an English text's in English, a Chinese one's in Chinese, which is not translated again.

The interface speaks fewer of them (web/i18n.js): a text read in one it does not speak has the interface in English.
Budgets are per script: languages written without spaces count characters, the rest count words.
Thai has no spaces between words but does between sentences and clauses: it counts characters like Chinese, and joins
its sentences with a space like English (tokens.py cuts it into syllables for the reader).
Arabic and Hebrew are left out for now: the reader lays text out left to right.
"""
import re


# code: (name used in prompts, native name, script)
LANGS = {
    "en": ("English", "English", "latin"),
    "zh": ("Simplified Chinese", "简体中文", "cjk"),
    "zh-Hant": ("Traditional Chinese", "繁體中文", "cjk"),
    "ja": ("Japanese", "日本語", "cjk"),
    "ko": ("Korean", "한국어", "hangul"),
    "es": ("Spanish", "Español", "latin"),
    "fr": ("French", "Français", "latin"),
    "de": ("German", "Deutsch", "latin"),
    "pt": ("Portuguese", "Português", "latin"),
    "it": ("Italian", "Italiano", "latin"),
    "ru": ("Russian", "Русский", "cyrillic"),
    "hi": ("Hindi", "हिन्दी", "devanagari"),
    "sv": ("Swedish", "Svenska", "latin"),
    "da": ("Danish", "Dansk", "latin"),
    "nb": ("Norwegian Bokmål", "Norsk bokmål", "latin"),
    "fi": ("Finnish", "Suomi", "latin"),
    "nl": ("Dutch", "Nederlands", "latin"),
    "pl": ("Polish", "Polski", "latin"),
    "cs": ("Czech", "Čeština", "latin"),
    "uk": ("Ukrainian", "Українська", "cyrillic"),
    "tr": ("Turkish", "Türkçe", "latin"),
    "id": ("Indonesian", "Bahasa Indonesia", "latin"),
    "vi": ("Vietnamese", "Tiếng Việt", "latin"),
    "th": ("Thai", "ภาษาไทย", "thai"),
    "el": ("Greek", "Ελληνικά", "greek"),
}

_CHARS = {"takeaway": 28, "reason": 50, "topic": 6, "section": 32, "unit": "characters"}
_WORDS = {"takeaway": 16, "reason": 26, "topic": 4, "section": 18, "unit": "words"}
_THAI = {"takeaway": 80, "reason": 130, "topic": 18, "section": 90, "unit": "characters"}   # counted in characters, about as many as the English has
_HANGUL = {"takeaway": 12, "reason": 22, "topic": 3, "section": 14, "unit": "words"}
_SYLLABLES = {"takeaway": 24, "reason": 40, "topic": 6, "section": 27, "unit": "words"}   # Vietnamese spaces its syllables


def name(code):
    return LANGS[code][0]


def natives():
    """Every language a text can be read in, by its own name, in the order of LANGS (for the pages' menus)."""
    return {code: v[1] for code, v in LANGS.items()}


def budget(code):
    script = LANGS[code][2]
    return (_CHARS if script == "cjk" else _HANGUL if script == "hangul" else _THAI if script == "thai"
            else _SYLLABLES if code == "vi" else _WORDS)


def cjk(code):
    return LANGS[code][2] == "cjk"


def stop(code):
    """The full stop that joins the takeaway and the reason in Brief."""
    return "。" if cjk(code) else "।" if LANGS[code][2] == "devanagari" else "."


def punctuation_rule(code):
    n = name(code)
    if code in ("zh", "zh-Hant"):
        return f"{n} full-width punctuation; no dashes (——)."
    if code == "ja":
        return "Japanese punctuation (、。); no dashes (——)."
    if code == "el":
        return "Standard Greek punctuation: the question mark is ; and the raised dot · is a semicolon; quotes are « »; no em or en dashes as punctuation, use commas or colons."
    if code == "th":
        return "Thai writing: no spaces between the words of a clause, one space between clauses; no em or en dashes as punctuation."
    return f"Standard {n} punctuation; no em or en dashes as punctuation, use commas or colons."


# ── the language a text is written in ─────────────────────────────────
# Among the languages above, from the text alone: the script with the most words (a CJK character counts as a part of
# one, about 1.7 to a word; Thai, written without spaces too, about 4 characters to a word), then within it Japanese by its kana, Traditional Chinese by the characters only it writes
# so, Ukrainian by its own letters, Vietnamese by its tone marks, and the other Latin scripts by the small words each
# uses most; Greek by its own alphabet. A text it cannot tell is English, as every text was before.
_SCRIPTS = {"latin": r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]+", "cyrillic": r"[\u0400-\u04ff]+", "devanagari": r"[\u0900-\u097f]+",
            "hangul": r"[\uac00-\ud7af\u1100-\u11ff]+", "greek": r"[\u0370-\u03ff\u1f00-\u1fff]+"}
_HAN, _KANA, _THAI_SCRIPT = r"[\u4e00-\u9fff\u3400-\u4dbf]", r"[\u3040-\u30ff]", r"[\u0e00-\u0e7f]"   # (_THAI: Thai's budgets)
_SIMPLIFIED = "这们个来时说为国学会对发经过还体关与点实现机开长间问题种后业应进动产让认设计资数据级统处务将东区场结构变标准条网络语历层万号"
_TRADITIONAL = "這們個來時說為國學會對發經過還體關與點實現機開長間問題種後業應進動產讓認設計資數據級統處務將東區場結構變標準條網絡語歷層萬號"
_VIET = set("ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđơư")
_SMALL = {
    "en": "the of and to in is that for with as on are this by be it from at or an which we not",
    "es": "de la que el en los del las por un una para con es se no al lo como más pero sus",
    "fr": "de la le les des et en du un une est que pour dans qui au sur par pas plus ne se",
    "de": "der die und den das ist nicht von zu mit des sich dem ein eine auf für im auch werden wird",
    "pt": "de que não uma para com os das dos do da em um por mais como ao se na no é são",
    "it": "di che il la per non una con del della le gli sono è nel alla più dei anche al",
    "nl": "de het een van en in is dat op te zijn voor met niet die aan er ook als",
    "sv": "och att det som en på är av för med till den har de inte om ett var jag men",
    "da": "og at det som en på er af for med til den har de ikke om et var jeg efter",
    "nb": "og at det som en på er av for med til den har de ikke om et var jeg etter å",
    "fi": "ja on ei se että oli ovat kuin mutta tai joka myös sekä tämä niin jos kun ole voi hän",
    "pl": "i w się na nie z do jest to że o jak ale co za od po przez dla są",
    "cs": "a v se na je že to s z do o jako ale pro které jsou by i za ve",
    "tr": "ve bir bu da de için ile olarak daha çok gibi olan en ama ya her kadar sonra değil",
    "id": "yang dan di dari ini itu dengan untuk dalam tidak pada adalah ke akan juga atau oleh",
}
_SMALL = {code: set(words.split()) for code, words in _SMALL.items()}


def detect(text):
    """The language (a code of LANGS) the text is written in, English when it cannot tell."""
    text = text[:40000]
    han, kana = len(re.findall(_HAN, text)), len(re.findall(_KANA, text))
    words = {script: re.findall(rx, text) for script, rx in _SCRIPTS.items()}
    counts = {script: len(w) for script, w in words.items()}
    counts["cjk"] = (han + kana) / 1.7
    counts["thai"] = len(re.findall(_THAI_SCRIPT, text)) / 4
    script = max(counts, key=counts.get)
    if counts[script] < 5:
        return "en"
    if script == "cjk":
        if kana > 0.05 * (han + kana):
            return "ja"
        simplified, traditional = sum(map(text.count, _SIMPLIFIED)), sum(map(text.count, _TRADITIONAL))
        return "zh-Hant" if traditional > simplified else "zh"
    if script == "hangul":
        return "ko"
    if script == "thai":
        return "th"
    if script == "devanagari":
        return "hi"
    if script == "greek":
        return "el"
    if script == "cyrillic":
        letters = "".join(words["cyrillic"]).lower()
        return "uk" if sum(map(letters.count, "іїєґ")) > 0.01 * len(letters) else "ru"
    latin = [w.lower() for w in words["latin"][:6000]]
    letters = "".join(latin)
    if sum(c in _VIET for c in letters) > 0.03 * max(1, len(letters)):
        return "vi"
    hits = {code: sum(w in small for w in latin) for code, small in _SMALL.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] >= 5 else "en"
