# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Mandarin Chinese (普通话) practice-language profile (variant: standard).

Pure data consumed by mimora/config.py (assembled into LANGUAGE_PROFILES).
The profile format is documented in mimora/languages/__init__.py.

Mandarin specifics:
- The espeak-ng language code is ``cmn`` (Mandarin Chinese). Note that ``zh``
  is NOT a valid espeak language - ``reference_word_phonemes('你好世界', 'zh')``
  raises RuntimeError("language zh is not supported") - so the profile MUST use
  ``espeak_language="cmn"``. The espeak tones are emitted as tone-number IPA
  tokens (e.g. ``n i5 x ɑu5 s. i.5 tɕ iɛ5`` for 你好 世界).
- Chinese MUST be space-separated (词间空格) for per-word grouping and word-count
  validation: ``reference_word_phonemes`` returns one phone group per
  ``text.split()`` token, and ``_fits_level`` counts ``_WORD_RE`` matches. The
  LLM is instructed to separate every word with a single space (verified: the
  running llama-server generates "我 学习 中文 每天。" correctly).
- TTS runs Kokoro with ``kokoro_lang_code="z"`` (Mandarin Chinese). Kokoro is
  the only installed backend that supports Mandarin; Supertonic-3 has no
  Mandarin voice (see mimora/tts.py). ``misaki[zh]`` must be installed for the
  Chinese pipeline (cn2an, jieba, ordered-set, pypinyin, pypinyin-dict).
- The acoustic engine is English-only ASR, so Mandarin omits it like Spanish:
  only the text-only phoneme engine and the no-op none engine are offered. The
  phoneme engine runs experimental until a Mandarin model calibration
  (cmn_model_calibration.json) is committed - see engine_experimental().
- The wav2vec2 phoneme recognizer is multilingual and its 392-token vocab
  already includes Mandarin tone tokens, so phoneme-engine ASR can recognize
  Mandarin-ish symbols.
- wordfreq has no ``cmn``/``zho``/``zh-CN`` frequency table - it maps to the
  nearest match ``zh`` (which requires jieba; installed as a misaki[zh] dep).
  ``_min_zipf`` skips words shorter than 3 characters, so most Chinese words
  (1-2 characters) are skipped and the Zipf floor is effectively inert for
  Mandarin (no false failures).
"""

PROFILE = {
    "display_name": "Mandarin",
    "flores_code": "zho_Hans",
    # Only Standard (Beijing) Mandarin is defined; a Cantonese variant (yue)
    # or a Traditional-script variant would be a second entry under "variants".
    "default_variant": "standard",
    # The acoustic engine is English-only ASR, so Mandarin omits it: only the
    # text-only phoneme engine and the no-op none engine are offered. The
    # phoneme engine runs experimental until a Mandarin model calibration
    # (cmn_model_calibration.json) is committed - see engine_experimental().
    "engines": ("phoneme", "none"),
    "practice_text_file": "texts/practice_text_zh.txt",
    # Phrase-generation prompts: the instructions stay in English (they steer
    # the model), only the TARGET language named inside each string is
    # Mandarin - mirroring the english/spanish entries. Crucially, the model
    # is instructed to separate every Chinese word with a single space
    # (词与词之间用空格隔开): the scorer groups words by whitespace tokens and
    # the level validator counts words by _WORD_RE, so unspaced Chinese
    # collapses to a single giant word and fails every validation.
    "phrase_gen": {
        "system": (
            "You generate short Mandarin Chinese sentences for pronunciation practice. "
            "Reply with exactly ONE natural spoken sentence of {min_words} to {max_words} words, "
            "easy to read aloud. "
            "Write the sentence in Simplified Chinese characters. Separate every Chinese word "
            "with a single space (词与词之间用空格隔开), for example: '我 喜欢 学习 中文'. "
            "Use only plain words and a single final period - no quotation marks, no numbering, "
            "no extra commentary. Output ONLY the sentence itself, with nothing before or after it: "
            "do not add a lead-in such as 'Here's a sentence' or 'Sure', and never put a colon before "
            "the sentence. Base the sentence on the topic and vocabulary of the text the user provides."
        ),
        "fragment_system": (
            "You generate very short Mandarin Chinese fragments for pronunciation practice. "
            "Reply with exactly ONE natural fragment of 2 to 4 words that is NOT a complete "
            "sentence - such as a sentence opening, a prepositional phrase, or the start of a "
            "question. Examples: 我 喜欢; 在 桌子 上; 你 从 哪里 来. "
            "Write the fragment in Simplified Chinese characters. Separate every Chinese word "
            "with a single space (词与词之间用空格隔开). "
            "Use only plain words - no final period, no quotation marks, no numbering, "
            "no extra commentary. Output ONLY the fragment itself, with nothing before or after it: "
            "do not add a lead-in such as 'Here's a fragment' or 'Sure', and never put a colon before "
            "the fragment. Base the fragment on the topic and vocabulary of the text the user provides."
        ),
        "full_ask": (
            "Give me ONE short Mandarin Chinese sentence to practice pronunciation, "
            "based on this text."
        ),
        "fragment_ask": (
            "Give me ONE short Mandarin Chinese fragment of 2 to 4 words (NOT a complete "
            "sentence) to practice pronunciation, based on this text."
        ),
        # Function and other non-content words never picked as the random
        # focus word - same schema and rationale as the english/spanish
        # profiles. The focus-word picker's regex [^\W\d_]{4,} matches only
        # runs of 4+ characters, so most Chinese words (1-2 characters) are
        # never picked anyway; this list exists for schema completeness and
        # for the longer words (词/语言/学习...) the picker CAN match.
        "stopwords": (
            "的 了 在 是 我 你 他 她 它 我们 你们 他们 这 那 这 些 那 些 "
            "和 与 或 但 而 因 为 所 以 如 果 虽 然 还 有 就 都 也 很 太 "
            "不 没 没 有 一 个 两 三 四 五 六 七 八 九 十 上 下 左 右 里 "
            "外 前 后 中 间 边 面 从 到 往 向 对 于 关 于 把 被 让 给 跟 "
            "比 得 地 着 过 呢 吗 吧 啊 呀 哦 嗯 什 么 谁 哪 怎 么 为 什 么 "
            "几 多 少 每 各 某 所 些 这 些 那 些 一 些 有 些 别 另 外 "
            "也 是 不 是 没 有 还 在 正 在 已 经 将 要 会 能 可 以 应 该 "
            "需 要 想 要 喜 欢 觉 得 知 道 认 为 说 看 听 走 来 去 回 到 "
            "大 小 好 坏 新 旧 多 少 高 低 长 短 快 慢 早 晚 今 天 明 天 "
            "昨 天 现 在 时 候 时 间 地 方 事 情 人 们 东 西 问 题 世 界"
        ),
        # Proficiency levels 0..5 - same schema and rationale as the english
        # profile (see the comment there). Zipf floors mirror the English
        # starting values, but they are effectively inert for Mandarin:
        # _min_zipf skips words shorter than 3 characters, and Chinese words
        # are mostly 1-2 characters, so the floor rarely rejects a word.
        # Tune from logs/phrase_level_samples.jsonl once Mandarin samples land.
        "levels": (
            {
                "vocab_hint": ("Use only the simplest everyday Mandarin words "
                               "that a complete beginner knows."),
                "grammar_hint": ("Use the present tense only, with a simple "
                                 "subject-verb structure."),
                "words": (3, 5),
                "min_zipf": 4.8,
            },
            {
                "vocab_hint": ("Use only very common everyday words a "
                               "beginner knows."),
                "grammar_hint": ("Use the simple present tense or a simple "
                                 "command."),
                "words": (3, 6),
                "min_zipf": 4.5,
            },
            {
                "vocab_hint": ("Use common everyday vocabulary an elementary "
                               "learner knows."),
                "grammar_hint": ("Simple present, simple past or the "
                                 "'要 + verb' future are all fine."),
                "words": (4, 7),
                "min_zipf": 4.0,
            },
            {
                "vocab_hint": "Use ordinary everyday vocabulary.",
                "grammar_hint": ("Any common aspect is fine; keep the "
                                 "structure simple."),
                "words": (4, 8),
                "min_zipf": 3.7,
            },
            {
                "vocab_hint": ("You may use some less common words and "
                               "idioms."),
                "grammar_hint": ("Varied structures are welcome, including "
                                 "complex sentences."),
                "words": (5, 9),
                "min_zipf": 3.3,
            },
            {
                "vocab_hint": ("Use rich natural vocabulary, including "
                               "idioms and less common words."),
                "grammar_hint": ("Any natural structure is fine, including "
                                 "complex sentences."),
                "words": (5, 10),
                "min_zipf": None,
            },
        ),
    },
    # Spoken by the voice-preview button (settings_window.py); short and
    # phonetically varied, in the language being practiced.
    "preview_phrase": "你好！这是我 的 声音。我们 一起 练习 吧。",
    # Throwaway text to prime the NLLB translator's source tokenizer
    # (mimora/translator.py warm_up); any short phrase in the source language.
    "translator_warmup": "你好。",
    # Throwaway word spoken by the TTS warm-up pass (mimora/tts.py warm_up):
    # a short in-vocabulary word of the practiced language.
    "tts_warmup": "你好。",
    # Startup greeting spoken once the app is ready (app.py
    # _greet_and_start), in the practiced language. The named form carries
    # a {name} placeholder; the anonymous form is used when no user name is set.
    "greeting_named": "{name}，你好！请 听 并 跟读。",
    "greeting_anonymous": "你好！请 听 并 跟读。",
    # Shown in the source panel when the practice-text file cannot be read
    # (app.py _load_practice_text), in the practiced language. The button
    # name stays English - the UI language is English by design.
    "practice_text_fallback": (
        "你好，欢迎 使用 Mimora。编辑 这个 文本 并 点击 "
        "New phrase 开始 练习。"
    ),
    "variants": {
        # Mandarin runs Kokoro with lang_code "z" (Mandarin Chinese). Kokoro
        # is the only installed backend that supports Mandarin; Supertonic-3
        # has no Chinese voice (31 languages, no zh/cmn). The voices are the
        # 8 Mandarin voices Kokoro ships (4 female, 4 male); the default is a
        # clear female voice.
        "standard": {
            "kokoro_lang_code": "z",
            "espeak_language": "cmn",
            "default_voice": "zf_xiaoxiao",
            "voices": [
                "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
                "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
            ],
        },
    },
}
