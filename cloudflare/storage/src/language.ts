// Legacy-ladder index detection follows textfold/langs.py; never modifies source or layers.
export const languageCodes = new Set(["en", "zh", "zh-Hant", "ja", "ko", "es", "fr", "de", "pt", "it", "ru", "hi", "sv", "da", "nb", "fi", "nl", "pl", "cs", "uk", "tr", "id", "vi", "th"]);
const rules = {
  scripts: {"latin": "[A-Za-z\\u00c0-\\u024f\\u1e00-\\u1eff]+", "cyrillic": "[\\u0400-\\u04ff]+", "devanagari": "[\\u0900-\\u097f]+", "hangul": "[\\uac00-\\ud7af\\u1100-\\u11ff]+"},
  han: "[\\u4e00-\\u9fff\\u3400-\\u4dbf]",
  kana: "[\\u3040-\\u30ff]",
  thai: "[\\u0e00-\\u0e7f]",
  simplified: "这们个来时说为国学会对发经过还体关与点实现机开长间问题种后业应进动产让认设计资数据级统处务将东区场结构变标准条网络语历层万号",
  traditional: "這們個來時說為國學會對發經過還體關與點實現機開長間問題種後業應進動產讓認設計資數據級統處務將東區場結構變標準條網絡語歷層萬號",
  viet: "đơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ",
  small: {"en": ["an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "not", "of", "on", "or", "that", "the", "this", "to", "we", "which", "with"], "es": ["al", "como", "con", "de", "del", "el", "en", "es", "la", "las", "lo", "los", "más", "no", "para", "pero", "por", "que", "se", "sus", "un", "una"], "fr": ["au", "dans", "de", "des", "du", "en", "est", "et", "la", "le", "les", "ne", "par", "pas", "plus", "pour", "que", "qui", "se", "sur", "un", "une"], "de": ["auch", "auf", "das", "dem", "den", "der", "des", "die", "ein", "eine", "für", "im", "ist", "mit", "nicht", "sich", "und", "von", "werden", "wird", "zu"], "pt": ["ao", "com", "como", "da", "das", "de", "do", "dos", "em", "mais", "na", "no", "não", "os", "para", "por", "que", "se", "são", "um", "uma", "é"], "it": ["al", "alla", "anche", "che", "con", "dei", "del", "della", "di", "gli", "il", "la", "le", "nel", "non", "per", "più", "sono", "una", "è"], "nl": ["aan", "als", "dat", "de", "die", "een", "en", "er", "het", "in", "is", "met", "niet", "ook", "op", "te", "van", "voor", "zijn"], "sv": ["att", "av", "de", "den", "det", "en", "ett", "för", "har", "inte", "jag", "med", "men", "och", "om", "på", "som", "till", "var", "är"], "da": ["af", "at", "de", "den", "det", "efter", "en", "er", "et", "for", "har", "ikke", "jeg", "med", "og", "om", "på", "som", "til", "var"], "nb": ["at", "av", "de", "den", "det", "en", "er", "et", "etter", "for", "har", "ikke", "jeg", "med", "og", "om", "på", "som", "til", "var", "å"], "fi": ["ei", "että", "hän", "ja", "joka", "jos", "kuin", "kun", "mutta", "myös", "niin", "ole", "oli", "on", "ovat", "se", "sekä", "tai", "tämä", "voi"], "pl": ["ale", "co", "dla", "do", "i", "jak", "jest", "na", "nie", "o", "od", "po", "przez", "się", "są", "to", "w", "z", "za", "że"], "cs": ["a", "ale", "by", "do", "i", "jako", "je", "jsou", "které", "na", "o", "pro", "s", "se", "to", "v", "ve", "z", "za", "že"], "tr": ["ama", "bir", "bu", "da", "daha", "de", "değil", "en", "gibi", "her", "ile", "için", "kadar", "olan", "olarak", "sonra", "ve", "ya", "çok"], "id": ["adalah", "akan", "atau", "dalam", "dan", "dari", "dengan", "di", "ini", "itu", "juga", "ke", "oleh", "pada", "tidak", "untuk", "yang"]}
};

export function sourceLanguage(html: string): string {
  const text = html.replace(/<(math|pre|script|style)\b.*?<\/\1>|<section[^>]*ltx_bibliography.*?<\/section>/gis, ' ')
    .replace(/<[^>]+>/g, ' ').slice(0, 40000);
  const matches = (rx: string) => text.match(new RegExp(rx, 'g')) ?? [];
  const han = matches(rules.han).length, kana = matches(rules.kana).length;
  const words: Record<string, string[]> = Object.fromEntries(Object.entries(rules.scripts).map(([k,rx]) => [k,matches(rx)]));
  const counts = Object.fromEntries(Object.entries(words).map(([k,v]) => [k,v.length]));
  counts.cjk = (han+kana)/1.7; counts.thai = matches(rules.thai).length/4;
  const script = Object.keys(counts).reduce((a,b) => counts[b]>counts[a] ? b : a);
  if (counts[script]<5) return 'en';
  const occurrences = (letters: string, choices: string) => [...letters].filter(c => choices.includes(c)).length;
  if (script==='cjk') {
    if (kana>0.05*(han+kana)) return 'ja';
    return occurrences(text,rules.traditional)>occurrences(text,rules.simplified) ? 'zh-Hant' : 'zh';
  }
  if (script==='hangul') return 'ko';
  if (script==='thai') return 'th';
  if (script==='devanagari') return 'hi';
  if (script==='cyrillic') {
    const letters=words.cyrillic.join('').toLowerCase();
    return occurrences(letters,'іїєґ')>0.01*letters.length ? 'uk' : 'ru';
  }
  const latin=words.latin.slice(0,6000).map(w=>w.toLowerCase()), letters=latin.join('');
  if (occurrences(letters,rules.viet)>0.03*Math.max(1,letters.length)) return 'vi';
  const hits=Object.entries(rules.small).map(([k,small])=>[k,latin.filter(w=>small.includes(w)).length] as const);
  const best=hits.reduce((a,b)=>b[1]>a[1] ? b : a);
  return best[1]>=5 ? best[0] : 'en';
}
