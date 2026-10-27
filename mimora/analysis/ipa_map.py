# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Best-effort English word -> IPA conversion.

The persistence layer stores ``phoneme_scores[]`` from the engine's
``weak_phonemes`` output (IPA symbols only, no per-word association).
When the UI wants to show "which word caused this phoneme error", we
need the reverse mapping. Rather than depending on a full phonemizer,
this module ships a small hand-curated table of common English words
and their standard G2P-ESpeak-style IPA transcriptions.

The table is deliberately small (~200 entries) so it stays
maintainable by hand; unknown words fall back to a heuristic that
reads the word's orthography character-by-character and produces a
rough IPA. That heuristic is not accurate for every word, but it is
good enough to show the user a reasonable IPA sequence and to detect
common vowel/consonant patterns (th-, sh-, ch-, etc.).

If the project ever decides to add ``misaki`` or ``espeak-ng`` as a
dependency, ``word_to_ipa()`` is the single seam to swap.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Known words. Format: lowercase -> IPA (with spaces between phonemes).
# Only common words used in Mimora's practice phrases are listed; anything
# not here falls through to the heuristic.
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
# Heuristic fallback for unknown words.
# ---------------------------------------------------------------------------
# Each entry maps a substring (checked in order) to the IPA it usually
# represents. Longer matches take priority, so "ch" beats "c" and "sh"
# beats "s". This is not a real G2P - it is a rough guide for the case
# where a word is not in _KNOWN_WORDS.
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


def word_to_ipa(word: str) -> str:
    """Return the best-guess IPA for one word, or empty string for ``""``.

    Uses the known-words table first; falls back to a character
    heuristic. The result is a single IPA string without spaces (the
    extractor's shape). Not perfect - "through" and "though" would both
    come out as ``θroʊ`` and ``θɒ`` respectively, and neither is
    correct - but it is a reasonable enough approximation for the
    "which word caused this phoneme" UI question.
    """
    clean = (word or "").strip().lower()
    if not clean:
        return ""
    if clean in _KNOWN_WORDS:
        return _KNOWN_WORDS[clean]
    # Heuristic: greedily match the longest pattern at each position.
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


def ipa_from_words(phrase: str) -> str:
    """Return the best-guess IPA for a whole phrase (word-by-word)."""
    words = (phrase or "").strip().split()
    if not words:
        return ""
    return " ".join(word_to_ipa(w) for w in words)


def extract_ipas_from_transcript(transcript: str) -> set:
    """Return the set of distinct IPA symbols appearing in a transcript.

    Used by the UI to highlight which phonemes the user actually
    produced. The heuristic may over-generate phonemes for unknown
    words, but for the words that ARE in _KNOWN_WORDS it is accurate.
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
