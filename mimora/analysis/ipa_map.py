# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""English word -> IPA conversion, backed by the pronunciation engine's G2P.

The persistence layer stores ``phoneme_scores[]`` from the engine's
``weak_phonemes`` output (IPA symbols only, no per-word association).
When the UI wants to show "which word caused this phoneme error", we
need the reverse mapping. This module routes through the engine's own
espeak-ng pipeline (``pronunciation.phoneme.speech.reference_word_phonemes``),
so the symbols are exactly the folded inventory the scorer uses - never
a second, drifting transcription.

``word_to_ipa()`` is the single seam the project anticipated: it used to
ship a small hand-curated table plus a character heuristic, and the
engine already phonemizes with the bundled espeak-ng. The hand table and
heuristic are retained only as a soft-degrade fallback (espeak not
available, exotic word, or an error inside the engine), so the UI still
gets a best-effort string rather than nothing. The fallback is deliberately
not accurate for every word ("through"/"though" come out wrong) - it is a
rough guide for the rare case the real G2P cannot run.
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known words. Format: lowercase -> IPA (with spaces between phonemes).
# Only used as the soft-degrade fallback when the engine G2P cannot run.
# Kept deliberately small (~200 entries) so it stays maintainable by hand.
# ---------------------------------------------------------------------------
_KNOWN_WORDS = {
    "the": "ðə", "a": "ə", "an": "ən", "is": "ɪz", "of": "ʌv", "to": "tuː",
    "i": "aɪ", "you": "juː", "he": "hiː", "she": "ʃiː", "it": "ɪt",
    "we": "wiː", "they": "ðeɪ", "me": "miː", "him": "hɪm", "her": "hɜːr",
    "us": "ʌs", "them": "ðɛm", "my": "maɪ", "your": "jɔːr", "his": "hɪz",
    "our": "aʊr", "their": "ðɛr", "be": "biː", "was": "wəz", "were": "wɜːr",
    "are": "ɑːr", "been": "biːn", "being": "biːɪŋ", "have": "hæv",
    "has": "hæz", "had": "hæd", "do": "duː", "does": "dʌz", "did": "dɪd",
    "will": "wɪl", "would": "wʊd", "can": "kæn", "could": "kʊd",
    "should": "ʃʊd", "may": "meɪ", "might": "maɪt", "must": "mʌst",
    "shall": "ʃæl", "this": "ðɪs", "that": "ðæt", "these": "ðiːz",
    "those": "ðoʊz", "there": "ðɛr", "here": "hɪr", "where": "wɛr",
    "what": "wɒt", "which": "wɪtʃ", "who": "huː", "how": "haʊ",
    "very": "vɛri", "just": "dʒʌst", "also": "ɔːlsoʊ", "even": "iːvən",
    "still": "stɪl", "only": "oʊnliː", "really": "rɪliː", "much": "mʌtʃ",
    "many": "mɛni", "some": "sʌm", "any": "ɛni", "all": "ɔːl",
    "every": "ɛvri", "each": "iːtʃ", "both": "boʊθ", "few": "fjuː",
    "little": "lɪtl", "big": "bɪɡ", "small": "smɔːl", "large": "lɑːrdʒ",
    "long": "lɔːŋ", "short": "ʃɔːrt", "high": "haɪ", "low": "loʊ",
    "fast": "fæst", "slow": "sloʊ", "quick": "kwɪk", "slowly": "sloʊli",
    "quickly": "kwɪkli", "hard": "hɑːrd", "easy": "iːzi", "hard": "hɑːrd",
    "simple": "sɪmpl", "complex": "kɒmplɛks", "new": "nuː", "old": "oʊld",
    "first": "fɜːrst", "last": "læst", "next": "nɛkst", "another": "əˈnʌðɚ",
    "then": "ðɛn", "now": "naʊ", "when": "wɛn", "why": "waɪ",
    "thought": "θɔːt", "thick": "θɪk", "thin": "θɪn", "three": "θriː",
    "thunder": "θʌndɚ", "thank": "θæŋk", "thing": "θɪŋ", "thorough":
    "θʌrə", "this": "ðɪs", "that": "ðæt", "those": "ðoʊz",
    "the": "ðə", "them": "ðɛm", "the": "ðiːz",
    "squirrel": "skwɪrəl", "world": "wɜːrld", "work": "wɜːrk",
    "word": "wɜːrd", "girl": "ɡɜːrl", "burn": "bɜːrn", "turn": "tɜːrn",
    "shoe": "ʃuː", "ship": "ʃɪp", "sheep": "ʃiːp", "shop": "ʃɒp",
    "should": "ʃʊd", "show": "ʃoʊ", "shine": "ʃaɪn", "shark": "ʃɑːrk",
    "church": "tʃɜːrtʃ", "chair": "tʃɛr", "child": "tʃaɪld",
    "change": "tʃeɪndʒ", "chocolate": "tʃɒklət",
    "juice": "dʒuːs", "jam": "dʒæm", "jump": "dʒʌmp", "just": "dʒʌst",
    "job": "dʒɒb", "gentle": "dʒɛntl", "page": "peɪdʒ", "people":
    "piːpl", "please": "pliːz",
    "sing": "sɪŋ", "song": "sɔːŋ", "long": "lɔːŋ", "strong": "strɔːŋ",
    "think": "θɪŋk", "thing": "θɪŋ", "sing": "sɪŋ", "ring": "rɪŋ",
    "thing": "θɪŋ", "sing": "sɪŋ", "sink": "sɪŋk",
    "run": "rʌn", "red": "rɛd", "road": "roʊd", "round": "raʊnd",
    "right": "raɪt", "read": "rɛd", "rain": "reɪn", "river": "rɪvɚ",
    "love": "lʌv", "left": "lɛft", "light": "laɪt", "long": "lɔːŋ",
    "little": "lɪtl", "look": "lʊk", "listen": "lɪsən",
    "house": "haʊs", "home": "hoʊm", "hand": "hænd", "heart": "hɑːrt",
    "help": "hɛlp", "have": "hæv", "here": "hɪr",
    "world": "wɜːrld", "work": "wɜːrk", "water": "wɔːtɚ",
    "woman": "wʊmən", "window": "wɪndoʊ", "winter": "wɪntɚ",
    "cat": "kæt", "dog": "dɒɡ", "fox": "fɒks", "bird": "bɜːrd",
    "book": "bʊk", "bag": "bæɡ", "bed": "bɛd", "bus": "bʌs",
    "car": "kɑːr", "city": "sɪti", "class": "klæs", "cup": "kʌp",
    "day": "deɪ", "door": "dɔːr", "dance": "dæns", "dream": "driːm",
    "eat": "iːt", "end": "ɛnd", "eye": "aɪ", "easy": "iːzi",
    "fire": "faɪr", "father": "fɑːðɚ", "friend": "frɛnd", "fish": "fɪʃ",
    "good": "ɡʊd", "girl": "ɡɜːrl", "game": "ɡeɪm", "go": "ɡoʊ",
    "hand": "hænd", "house": "haʊs", "hand": "hænd", "hand": "hænd",
    "ink": "ɪŋk", "in": "ɪn", "into": "ɪntuː", "island": "aɪlənd",
    "kind": "kaɪnd", "kindness": "kaɪndnəs",
}


# ---------------------------------------------------------------------------
# Heuristic fallback for unknown words (soft-degrade path only).
# ---------------------------------------------------------------------------
# Each entry maps a substring (checked in order) to the IPA it usually
# represents. Longer matches take priority, so "ch" beats "c" and "sh"
# beats "s". This is not a real G2P - it is a rough guide for the case
# where espeak cannot run.
_HEURISTIC_PATTERNS = [
    ("tion", "ʃən"), ("sion", "ʒən"), ("ture", "tʃɚ"), ("sure", "ʒɚ"),
    ("ough", "oʊ"), ("augh", "ɔː"),
    ("igh", "aɪ"), ("igh", "aɪ"), ("ea", "iː"), ("ee", "iː"),
    ("oo", "uː"), ("ou", "aʊ"), ("oi", "ɔɪ"), ("oy", "ɔɪ"),
    ("ai", "eɪ"), ("ay", "eɪ"), ("ey", "iː"), ("aw", "ɔː"),
    ("ew", "juː"), ("ew", "uː"), ("ie", "aɪ"), ("ea", "iː"),
    ("th", "θ"), ("sh", "ʃ"), ("ch", "tʃ"), ("ph", "f"),
    ("ck", "k"), ("ng", "ŋ"), ("qu", "kw"), ("wr", "r"),
    ("gh", ""), ("kn", "n"), ("gn", "n"), ("mb", "m"),
    ("c", "k"), ("a", "æ"), ("b", "b"), ("d", "d"), ("e", "ɛ"),
    ("f", "f"), ("g", "ɡ"), ("h", "h"), ("i", "ɪ"), ("j", "dʒ"),
    ("k", "k"), ("l", "l"), ("m", "m"), ("n", "n"), ("o", "ɒ"),
    ("p", "p"), ("q", "k"), ("r", "r"), ("s", "s"), ("t", "t"),
    ("u", "ʌ"), ("v", "v"), ("w", "w"), ("x", "ks"), ("y", "i"),
    ("z", "z"),
]


# The engine's G2P is the primary path; imported lazily so this module loads
# without espeak/phonemizer (only the fallback needs it, and only on demand).
_g2p = None


def _engine_g2p():
    """Return the engine's ``reference_word_phonemes``, importing it once.

    The pronunciation package is deliberately a sibling, never a dependency
    of the fallback: importing it pulls in the espeak/panphon stack, which
    this module should not require just to answer a UI query. Imported lazily
    and cached so repeated calls after a failure retry on the next call
    (a transient espeak problem should not permanently disable G2P).
    """
    global _g2p
    if _g2p is None:
        from pronunciation.phoneme.speech import reference_word_phonemes
        _g2p = reference_word_phonemes
    return _g2p


def _espeak_language() -> str:
    """The active espeak language code, or ``"en-us"`` on any failure.

    The engine reads the same value from ``mimora.config.ESPEAK_LANGUAGE``;
    routing through ``mimora.config`` keeps the two in sync without duplicating
    the profile lookup. Falls back to the engine's own default if the host
    config is unavailable (library use outside the app).
    """
    try:
        from mimora import config as _host_config
        return getattr(_host_config, "ESPEAK_LANGUAGE", None) or "en-us"
    except Exception:
        return "en-us"


def word_to_ipa(word: str, espeak_lang: str | None = None) -> str:
    """Return the best-guess IPA for one word, or empty string for ``""``.

    Primary path is the pronunciation engine's espeak-ng G2P, so the returned
    symbols are the same folded inventory the scorer uses. Falls back to a
    hand-curated table plus character heuristic only when the engine G2P
    cannot run (espeak unavailable, exotic input, or an error inside it).

    The result is a single IPA string without spaces (the extractor's shape).
    ``espeak_lang`` overrides the active language code (defaults to the
    configured one); the optional argument is added for callers that need a
    different variety without touching the host config.
    """
    clean = (word or "").strip().lower()
    if not clean:
        return ""
    lang = espeak_lang or _espeak_language()
    # The engine's G2P takes a whole text string (it splits into tokens
    # internally and phonemizes them in one batched espeak call).
    try:
        groups = _engine_g2p()(clean, lang)
    except Exception as exc:  # noqa: BLE001 - deliberate soft degrade
        _logger.warning("ipa_map: espeak G2P failed for %r (%s); using heuristic",
                        clean, exc)
        return _heuristic_word_to_ipa(clean)
    if not groups or not groups[0]:
        return _heuristic_word_to_ipa(clean)
    # The engine already strips diacritics and folds the inventory; join the
    # phones directly (no second normalization - that would double-fold).
    return "".join(groups[0])


def _heuristic_word_to_ipa(clean: str) -> str:
    """Heuristic fallback: greedy longest-pattern match over the orthography."""
    if clean in _KNOWN_WORDS:
        return _KNOWN_WORDS[clean]
    out = []
    i = 0
    while i < len(clean):
        matched = False
        for pattern, ipa in _HEURISTIC_PATTERNS:
            if clean.startswith(pattern, i):
                if ipa:
                    out.append(ipa)
                i += len(pattern)
                matched = True
                break
        if not matched:
            # Unknown character - leave it out rather than corrupting
            # the IPA (the extractor will just skip it).
            i += 1
    return "".join(out)


def ipa_from_words(phrase: str, espeak_lang: str | None = None) -> str:
    """Return the best-guess IPA for a whole phrase (word-by-word).

    Batch-optimized: the engine's G2P accepts a list of tokens in a single
    espeak call, so the whole phrase is phonemized at once rather than once
    per word. Falls back to the per-word heuristic on any engine error.
    """
    words = (phrase or "").strip().split()
    if not words:
        return ""
    lang = espeak_lang or _espeak_language()
    # Batch: the engine takes the whole phrase string and phonemizes every
    # token in one espeak call, so this is a single call, not one per word.
    try:
        groups = _engine_g2p()(phrase, lang)
    except Exception as exc:  # noqa: BLE001 - deliberate soft degrade
        _logger.warning("ipa_map: espeak G2P failed for %r (%s); using heuristic",
                        phrase, exc)
        return " ".join(_heuristic_word_to_ipa(w) for w in words)
    return " ".join("".join(g) for g in groups)


def extract_ipas_from_transcript(transcript: str) -> set:
    """Return the set of distinct IPA symbols appearing in a transcript.

    Used by the UI to highlight which phonemes the user actually produced.
    The engine G2P is accurate for the words it recognizes; the heuristic
    fallback may over-generate phonemes for unknown words.
    """
    result = set()
    for word in (transcript or "").split():
        ipa = word_to_ipa(word)
        if not ipa:
            continue
        for ch in ipa:
            if ch.isalpha() or ch in "ɪʊəæɒɔɜɛʃʒθðŋː":
                result.add(ch)
    return result
