# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Valeriy Kovalev

"""Minimal UI localization for Mimora (English -> Chinese).

The GUI is written in English; every user-visible literal is wrapped in
``tr(...)``. The English catalog is the identity, so with the UI language set
to English nothing changes at all. Selecting Chinese swaps the catalog at
startup (``set_language`` is called from the app run sequence before any
widget is built), and every ``tr()`` call thereafter resolves against it.

Catalog design
--------------
* The lookup key IS the English source literal (e.g. ``"Ready"``), so a
  missing translation degrades to the English string rather than to a key.
  There is no separate key namespace to keep in sync.
* Templates use ``str.format`` placeholders, so a Chinese translation can
  reorder or drop words::

      tr("{count} phrase{plural}", count=count,
         plural="" if count == 1 else "s")

  Extra kwargs that the template does not reference are ignored (the English
  plural suffix ``{plural}`` is absent from the Chinese template, which has no
  plural). A kwarg missing from the template leaves the placeholder intact.

Plural handling
---------------
Chinese has no plural form, so the ``{plural}`` suffix trick only appears in
English templates. Each pluralizing caller passes ``plural`` explicitly.

Thread safety
-------------
The language is read at startup and never changes during a session (the
settings window marks ``ui_language`` as restart-only), so ``tr()`` needs no
locking. This is a deliberate constraint: switching the language live would
require rebuilding every window, which this module does not attempt.

The active language is a UI concern only: it never affects the practice
language, translation, engine or any data. ``config.UI_LANGUAGE`` is the
validated, persisted value; this module just renders text for it.
"""

from typing import Dict

# Valid UI language codes. The first entry is the default.
SUPPORTED_LANGUAGES = ("en", "zh")


# English is the identity catalog: a key absent from it renders as itself.
# Entries here exist only where the English template needs an explicit form
# (mostly plural-aware ones) that differs from the plain source literal.
_EN: Dict[str, str] = {
    # Pluralized labels (see the "{plural}" convention above).
    "{count} phrase{plural}": "{count} phrase{plural}",
    "{count} phrases": "{count} phrases",
    # Phoneme interference hints (ZH_INTERFERENCE.hint_zh): the source string
    # is Chinese, so the English catalog must map each Chinese hint to an
    # English rendering. Without these the English UI shows Chinese hints.
    "咬舌音，中文无对应": "interdental, no Chinese equivalent",
    "咬舌浊音，中文无对应": "voiced interdental, no Chinese equivalent",
    "唇齿摩擦，中文常读成 w": "labiodental fricative, often read as w",
    "卷舌，中文 r 不卷": "retroflex, Chinese r is not retroflex",
    "中元音，中文无对应": "central vowel, no Chinese equivalent",
    "美 /æ/，中文常读成 e": "American /æ/, often read as e",
    "sh 音，中文 s/sh 易混": "sh sound, Chinese s/sh easily confused",
    "j 音，中文易读成 tʃ": "j sound, often read as tʃ",
    "后鼻音，中文易丢 n": "velar nasal, n often dropped",
    "亮 l / 暗 l 区分不清": "clear/dark l distinction unclear",
    "轻辅音，中文 h 常过重": "light consonant, h often too heavy",
}

_ZH: Dict[str, str] = {
    # ------------------------------------------------------------------
    # Main window (ui.py / app.py)
    # ------------------------------------------------------------------
    "MIMORA · {language}": "MIMORA · {language}",
    "Mimora · {language} - Pronunciation Trainer v{version}":
        "Mimora · {language} - 发音训练器 v{version}",
    "MIMORA · {language} - Pronunciation Trainer v{version}":
        "MIMORA · {language} - 发音训练器 v{version}",
    "Pronunciation Trainer": "发音训练器",
    "- Pronunciation Trainer": "- 发音训练器",
    "Starting...": "正在启动…",
    "Tip: press SPACE or click the mic to record. Tap the 🔊 button to replay the example.":
        "提示：按空格键或点击麦克风进行录音。点击 🔊 按钮可重播示例。",
    # Icon glyphs for the control buttons. These are same in every language, so
    # the Chinese entry is the literal itself (identity mapping).
    "🔊": "🔊",
    "🐢": "🐢",
    "📼": "📼",
    "⏭": "⏭",
    "Play reference": "播放参考发音",
    "Play reference slowly": "慢速播放参考发音",
    "Play at speed": "以指定速度播放",
    "Slow multiplier": "慢速倍率",
    "Phrase source": "短语来源",
    "Reference ▶": "参考发音 ▶",
    "Slow ▶": "慢速 ▶",
    "Record (Click/Press SPACE)": "录音（点击/按空格键）",
    "Record (Space)": "录音（空格键）",
    "Example": "示例",
    "My recording": "我的录音",
    "Previous": "上一个",
    "Next": "下一个",
    "▶": "▶",
    "🎙": "🎙",
    "⏮": "⏮",
    "Play my recording": "播放我的录音",
    "My recording ▶": "我的录音 ▶",
    "Next phrase": "下一句",
    "Next phrase ▶": "下一句 ▶",
    "Ready": "就绪",
    "Generating phrase (LLM)...": "正在生成句子（LLM）…",
    "Listen to the reference...": "请听参考发音…",
    "Your turn": "轮到你了",
    "Recording...": "正在录音…",
    "Analyzing pronunciation...": "正在分析发音…",
    "Loading models...": "正在加载模型…",
    "Starting LLM server...": "正在启动 LLM 服务器…",
    "Warming up models...": "正在预热模型…",
    "LLM Server Error": "LLM 服务器错误",
    "Initialization Failed": "初始化失败",
    "Recording Error": "录音错误",
    "Loading TTS and pronunciation models...": "正在加载 TTS 和发音模型…",
    "Loading Wav2Vec2 (pronunciation, ~1.2 GB on first run)...":
        "正在加载 Wav2Vec2（发音识别，首次运行约 1.2 GB）…",
    "Loading translator (NLLB) from disk...": "正在从磁盘加载翻译模型（NLLB）…",
    "LLM is off - phrases are taken from the practice text.":
        "LLM 已关闭——句子取自练习文本。",
    "Starting llama-server with {name}...": "正在使用 {name} 启动 llama-server…",
    "LLM server is ready.": "LLM 服务器已就绪。",
    "Using the llama-server already running on {host}:{port}. It keeps the model it was started with.":
        "使用已在 {host}:{port} 运行的 llama-server。它将保留启动时加载的模型。",
    "Warning: the LLM server has a context of {served} tokens instead of {expected}. Long prompts may not fit. See logs/main.log.":
        "警告：LLM 服务器的上下文为 {served} 个 token，而不是 {expected}。过长的提示可能放不下。参见 logs/main.log。",
    "Warning: LM Studio is offline. Start it to generate phrases!":
        "警告：LM Studio 已离线。请启动它以生成句子！",
    "Initialization Error: {type}: {msg}": "初始化错误：{type}：{msg}",
    "Error: {msg}": "错误：{msg}",
    "Error: LLM server failed to start. Check model path and GPU memory.":
        "错误：LLM 服务器启动失败。请检查模型路径和显存。",
    "Error: \"llama_server_path\" in config/settings.json points at a file that is not there. Fix the path, or clear it to use the build Mimora installs itself.":
        "错误：config/settings.json 中的“llama_server_path”指向不存在的文件。请修正路径，或清空它以使用 Mimora 自行安装的版本。",
    "Error: no llama-server binary was found. Install one with 'python -m mimora.llama_server_fetch', or point \"llama_server_path\" in config/settings.json at a build you make yourself, or set \"llm_backend\" to \"lm-studio\" and generate phrases with LM Studio instead.":
        "错误：未找到 llama-server 可执行文件。请用 'python -m mimora.llama_server_fetch' 安装，或在 config/settings.json 的“llama_server_path”中指向自行构建的版本，或将“llm_backend”设为“lm-studio”，改用 LM Studio 生成句子。",
    "Could not read {name}: {msg}": "无法读取 {name}：{msg}",
    "{name} is empty - nothing to load.": "{name} 为空——没有可加载的内容。",
    "Loaded practice text: {name}": "已加载练习文本：{name}",
    "Ready. Generate a phrase, listen, then press SPACE to repeat it.":
        "就绪。生成一句话，听一听，然后按空格键重复。",
    "Playing your recording...": "正在播放你的录音…",
    "The recording for that attempt is no longer on disk.":
        "该次尝试的录音已不在磁盘上。",
    "Could not play that recording.": "无法播放该录音。",
    "Could not export stats.": "无法导出统计。",
    "Previewing {voice}...": "正在预览 {voice}…",
    "Previewing {voice} ({speed}×)...": "正在预览 {voice}（{speed}×）…",
    "Could not preview voice {voice}.": "无法预览声音 {voice}。",
    "Please enter some practice text first.": "请先输入一些练习文本。",
    "The model returned no phrase. Try again.": "模型未返回句子。请重试。",
    "Could not synthesize the reference audio.": "无法合成参考音频。",
    "Translation unavailable: the translator model could not be loaded (see logs/main.log).":
        "翻译不可用：无法加载翻译模型（参见 logs/main.log）。",
    "New phrase: {phrase}": "新句子：{phrase}",
    "Audio device did not stop in time - take discarded, please try again.":
        "音频设备未及时停止——本次尝试已丢弃，请重试。",
    "Reached maximum record limit - take cut off.":
        "达到最长录音时间——本次尝试被截断。",
    "Playing reference...": "正在播放参考发音…",
    "Testing with reference...": "正在用参考发音测试…",
    "Reference test failed.": "参考发音测试失败。",
    "Audio is too short. Speak a little longer and try again.":
        "音频太短。请多说一点再试。",
    "No active phrase to compare against.": "没有可用于对比的当前句子。",
    "Analysis error. Please try again.": "分析出错。请重试。",
    "Playing '{word}' slowly...": "正在慢速播放“{word}”…",
    "Playing example '{word}'...": "正在播放示例“{word}”…",
    "Playing reference ({speed}×)...": "正在播放参考发音（{speed}×）…",
    # Quality bands.
    "Weak": "较弱",
    "Poor": "较差",
    "Needs work": "待改进",
    "Good": "良好",
    "Excellent": "优秀",
    "scoring off": "评分已关闭",
    "Recorded (scoring off)": "已录音（评分已关闭）",
    # Copy menu.
    "Copy": "复制",

    # ------------------------------------------------------------------
    # Hero card (ui_hero.py) + progress ring (progress_widget.py)
    # ------------------------------------------------------------------
    "SCORE": "得分",
    "record to get a score": "录音以获取得分",
    "WORK ON": "重点练习",
    "Click a word or sound to hear it. Double-click a word to drill it.": "单击单词或音标试听，双击单词进入练习。",
    "Click a word or a sound to hear it.": "点击单词或音标可试听。",
    "as in '{example}'": "如“{example}”",
    # Word-feedback strip quality labels (clickable per-word cells).
    "good": "好",
    "acceptable": "尚可",
    "needs work": "待改进",
    # Pass-gating status line (P0#3): shown instead of the quality verdict when
    # the gate is on and the take did not pass - "Next phrase" stays locked.
    "Keep trying - the next phrase unlocks when this take passes.": "继续练习——通过后才会解锁下一句。",
    # Fluency tempo verdicts (P0#2). The label is rendered verbatim next to the
    # fluency score, so the user sees which way to adjust their pace.
    "too slow": "语速偏慢",
    "natural": "语速自然",
    "too fast": "语速偏快",
    "Fluency": "流利度",
    # Shadowing (P1#4): the axis label and the settings-window strings.
    "Shadowing": "跟读",
    "Shadowing mode (speak along with the reference)": "跟读模式（跟随参考句同步朗读）",
    "Shadowing weight (%)": "跟读权重（%）",
    "When on, the reference plays and you speak along with it; the take is scored on pronunciation plus how well you kept up (delay/overlap).": "开启后，参考句播放时你同步朗读；评分同时考察发音质量和跟随程度（延迟/重叠）。",
    "How much the shadowing alignment counts in the blended score. 0 = show but do not blend.": "跟读对齐在混合得分中的权重。0 = 仅显示不混合。",
    "{count} phrase{plural}": "{count} 个句子",
    "{count} phrases": "{count} 个句子",

    # ------------------------------------------------------------------
    # Practice-text editor (ui_practice.py)
    # ------------------------------------------------------------------
    "▾ Practice text:": "▾ 练习文本：",
    "▸ Practice text:": "▸ 练习文本：",
    "Show/hide the practice-text editor": "显示/隐藏练习文本编辑器",
    "Paste": "粘贴",
    "Clear text": "清空文本",
    "(empty)": "（空）",
    "\"{shown}\"": "“{shown}”",
    "Practice text": "练习文本",

    # ------------------------------------------------------------------
    # History list (ui_history.py)
    # ------------------------------------------------------------------
    "Your attempts will appear here.": "你的尝试将显示在这里。",
    "Heard: ": "听到：",
    "scoring is off - compare the takes by ear": "评分已关闭——请凭耳朵比较每次尝试",
    "Words: ": "单词：",
    "Work on: ": "重点练习：",
    "Practice history": "练习历史",
    "Delete take": "删除尝试",
    "Delete this practice record?\n\n(If audio is stored, its wav files are also removed.)":
        "删除这条练习记录？\n\n（如果保存了音频，其 wav 文件也会一并删除。）",
    "Delete session": "删除会话",
    "Delete this whole session and all its takes?":
        "删除整个会话及其所有尝试？",
    "Clear all history": "清空全部历史",
    "This deletes every session, take and drill record.\nAudio files are also removed.\n\nAre you sure?":
        "这将删除所有会话、尝试和练习记录。\n音频文件也会被删除。\n\n确定吗？",
    "Confirm": "确认",
    "This action cannot be undone. Continue?":
        "此操作无法撤销。是否继续？",
    "Export complete": "导出完成",
    "JSON saved to:\n{path}": "JSON 已保存到：\n{path}",
    "CSV saved to:\n{path}": "CSV 已保存到：\n{path}",
    "Report saved to:\n{path}": "报告已保存到：\n{path}",
    "{sessions} sessions · {takes} takes":
        "{sessions} 个会话 · {takes} 次尝试",
    "{total} sessions · {takes} takes":
        "{total} 个会话 · {takes} 次尝试",
    "· {n} takes": "· {n} 次尝试",
    "· avg {avg:.0f}": "· 平均 {avg:.0f}",
    "{n} attempts": "{n} 次尝试",
    "{n} sentences": "{n} 个句子",
    "avg {avg:.0f}": "平均 {avg:.0f}",
    "Drill sessions": "练习会话",
    "Dictation sessions": "听写会话",
    "  ✕ delete session": "  ✕ 删除会话",
    "work on: {words}": "重点练习：{words}",
    "[{type}:{value}]  {attempts} attempts  avg {avg:.0f}  {outcome}":
        "[{type}:{value}]  {attempts} 次尝试  平均 {avg:.0f}  {outcome}",
    "   result: {outcome}": "   结果：{outcome}",
    "{sentences} sentences  avg {avg:.0f}  {outcome}":
        "{sentences} 个句子  平均 {avg:.0f}  {outcome}",
    "   words to review: {words}": "   待复习单词：{words}",

    # ------------------------------------------------------------------
    # Prosody panel (ui_prosody.py)
    # ------------------------------------------------------------------
    "▾ Intonation & stress": "▾ 语调与重音",
    "▸ Intonation & stress": "▸ 语调与重音",
    "Show/hide the intonation charts": "显示/隐藏语调图表",
    "Pitch - intonation (semitones vs your median)":
        "音高——语调（相对你的中位数的半音）",
    "● reference": "● 参考",
    "● you": "● 你",
    "Energy - stress pattern": "能量——重音模式",
    "Time runs (stretched to equal width). Aim to match the reference shape.":
        "时间轴（拉伸为等宽）。尽量匹配参考形状。",
    "▶  Play my take": "▶  播放我的尝试",

    # ------------------------------------------------------------------
    # Stats dashboard (ui_stats.py)
    # ------------------------------------------------------------------
    "Practice stats": "练习统计",
    "Practice minutes (last 28 days)": "练习分钟数（最近 28 天）",
    "Practice duration": "练习时长明细",
    "Export JSON": "导出 JSON",
    "Export CSV": "导出 CSV",
    "Export PNG": "导出 PNG",
    "Export PDF": "导出 PDF",
    "Export progress report as {ext}": "导出进度报告为 {ext}",
    "Close": "关闭",
    "Practice time": "练习时长",
    "Sessions": "会话数",
    "Takes": "尝试数",
    "Unique phrases": "不同句子",
    "Avg score": "平均分",
    "Best score": "最高分",
    "Streak": "连续达标",
    "Daily avg score (last 30 days)": "日均分（最近 30 天）",
    "Score distribution": "分数分布",
    "No sessions yet.": "还没有会话。",
    "Recent sessions": "最近会话",
    "{started}  ·  {takes} takes  ·  avg {avg:.0f}  ·  {dur}":
        "{started}  ·  {takes} 次尝试  ·  平均 {avg:.0f}  ·  {dur}",
    "{takes} takes": "{takes} 次尝试",
    "avg {avg}": "平均 {avg}",
    "No data yet": "暂无数据",
    "No data": "无数据",
    "{value}s": "{value} 秒",
    "{value}m": "{value} 分钟",
    "{value}h {rem}m": "{value} 小时 {rem} 分",
    "{value}d {rem}h": "{value} 天 {rem} 小时",
    "{seconds}s": "{seconds} 秒",
    "{minutes}m": "{minutes} 分钟",
    "{hours}h {rem}m": "{hours} 小时 {rem} 分",
    "{days}d {rem_h}h": "{days} 天 {rem_h} 小时",

    # ------------------------------------------------------------------
    # Weakness panel (ui_weakness.py)
    # ------------------------------------------------------------------
    "Pronunciation weakness": "发音薄弱项",
    "Overview": "总览",
    "Words": "单词",
    "Phonemes": "音标",
    "Refresh": "刷新",
    "Range:": "范围：",
    "Last 7 days": "近 7 天",
    "Last 14 days": "近 2 周",
    "Last 30 days": "近 1 月",
    "Last 60 days": "近 2 月",
    "Last 30 takes": "近 30 次",
    "Last 100 takes": "近 100 次",
    "Last 300 takes": "近 300 次",
    "Suggested next steps (based on your last 30 takes)":
        "建议的下一步（基于你最近 30 次尝试）",
    "Suggested next steps (based on {range})":
        "建议的下一步（基于{range}）",
    "your last 30 takes": "你最近 30 次尝试",
    "No obvious weaknesses. Keep practicing!":
        "没有明显的薄弱项。继续练习！",
    "Start drill →": "开始练习 →",
    "No word-level data yet. Take some practice first.":
        "还没有单词级数据。先练习几次吧。",
    "Need practice": "需练习",
    "Watch": "关注",
    "Improving": "改善中",
    "{count}×  err {rate:.0%}  {types}": "{count}×  错误 {rate:.0%}  {types}",
    "err": "错误",
    "score": "得分",
    "Weakness": "薄弱点",
    "Drill": "练习",
    "Play": "播放",
    "Re-drill": "再练",
    "No phoneme-level data yet. The phoneme engine provides this automatically.":
        "还没有音标级数据。音标引擎会自动提供。",
    "Issued phonemes (native: {native})": "易错音标（母语：{native}）",
    "{count}×  score {avg:.0%}": "{count}×  得分 {avg:.0%}",
    "Example word: {example}": "例词：{example}",
    "Example word: {example}  {hint}": "例词：{example}  {hint}",
    # Suggestion cards (suggestions.py templates, rendered in ui_weakness.py).
    "Practice phoneme /{ipa}/": "练音素 /{ipa}/",
    "Average score {avg:.0%}, {count} occurrences. Example: {example}. {hint}":
        "平均得分 {avg:.0%}，出现 {count} 次。例词：{example}。{hint}",
    "Average score {avg:.0%}, {count} occurrences. Example: {example}.":
        "平均得分 {avg:.0%}，出现 {count} 次。例词：{example}。",
    "Average score {avg:.0%}, {count} occurrences. {hint}":
        "平均得分 {avg:.0%}，出现 {count} 次。{hint}",
    "Average score {avg:.0%}, {count} occurrences.":
        "平均得分 {avg:.0%}，出现 {count} 次。",
    "Practice word \"{word}\"": "练单词 \"{word}\"",
    "{count} occurrences, {rate:.0%} error rate.":
        "出现 {count} 次，错误率 {rate:.0%}。",
    "Main problem: {label} ({pct:.0f}%).": "主要问题：{label}（{pct:.0f}%）。",
    "Reading speed too fast": "朗读语速偏快",
    "Average {wpm:.0f} WPM, above the target band {low:.0f}-{high:.0f}. Practice slowly (~90 WPM) for 3 days, then speed up.":
        "平均 WPM {wpm:.0f}，超过目标区间 {low:.0f}-{high:.0f}。建议先按慢速（约 90 WPM）练 3 天，再逐步提速。",
    "Reading speed too slow": "朗读语速偏慢",
    "Average {wpm:.0f} WPM, below the target band {low:.0f}-{high:.0f}. Practice long connected phrases and reduce pauses.":
        "平均 WPM {wpm:.0f}，低于目标区间 {low:.0f}-{high:.0f}。建议做长句连贯训练，减少停顿。",

    # ------------------------------------------------------------------
    # Dictation mode (ui_dictation.py)
    # ------------------------------------------------------------------
    "Custom text": "自定义文本",
    "Built-in": "内置",
    "Dictation": "听写",
    "Finish & report": "完成并报告",
    "Cancel": "取消",
    "Source:": "来源：",
    "{current} / {total}  ·  {passed} passed":
        "{current} / {total}  ·  通过 {passed}",
    "Play reference": "播放参考发音",
    "Play reference slowly": "慢速播放参考发音",
    "Play at speed": "以指定速度播放",
    "Slow multiplier": "慢速倍率",
    "Phrase source": "短语来源",
    "Random order from practice text": "从练习文本随机选择",
    "Spaced repetition (Ebbinghaus)": "间隔复习（艾宾浩斯）",
    "Hint": "提示",
    "Show sentence": "显示例句",
    "Check answer": "检查答案",
    "💡": "💡",
    "👁": "👁",
    "✓": "✓",
    "🐢  Slow play": "🐢  慢速播放",
    "💡  Hint": "💡  提示",
    "👁  Show sentence": "👁  显示例句",
    "Listen, then type what you hear.": "先听，然后输入你听到的内容。",
    "Your answer (one word per slot):": "你的答案（每格一个单词）：",
    "✓  Check answer": "✓  检查答案",
    "⏮  Previous": "⏮  上一个",
    "⏭  Next": "⏭  下一个",
    "Hint: {hint}": "提示：{hint}",
    "Previous sentence - type what you hear.":
        "上一句——输入你听到的内容。",
    "Type your sentences above, then press Next.":
        "在上方输入你的句子，然后点击“下一个”。",
    "🔥 streak {streak}/{required}": "🔥 连续 {streak}/{required}",
    "target ≥ {threshold:.0f}": "目标 ≥ {threshold:.0f}",
    "✅ {words}": "✅ {words}",
    "❌ Missed: {words}": "❌ 遗漏：{words}",
    "✏️ Spelling: {words}": "✏️ 拼写：{words}",
    "➕ Extra: {words}": "➕ 多余：{words}",
    "Words to review:": "待复习单词：",
    "Extra words:": "多余单词：",
    "Dictation finished": "听写完成",
    "Attempts: {n}": "尝试次数：{n}",
    "Passed: {n}": "通过：{n}",
    "First score: {n:.0f}": "首次得分：{n:.0f}",
    "Last score:  {n:.0f}": "最后得分：{n:.0f}",
    "Average:     {n:.1f}": "平均分：    {n:.1f}",
    "Outcome: {result}": "结果：{result}",
    "✅ Mastered": "✅ 已掌握",
    "🔁 Try again next time": "🔁 下次再试",
    "Dictation report": "听写报告",
    "Sentences per session": "每回句子数",
    "Apply": "应用",
    "✅ ": "✅ ",
    "❌ Missed: ": "❌ 遗漏：",
    "✏️ Spelling: ": "✏️ 拼写：",
    "➕ Extra: ": "➕ 多余：",
    "Outcome: ✅ Mastered": "结果：✅ 已掌握",
    "Outcome: 🔁 Try again next time": "结果：🔁 下次再试",

    # ------------------------------------------------------------------
    # Drill mode (ui_drill.py)
    # ------------------------------------------------------------------
    "Drill · {value}": "练习 · {value}",
    "{kind} drill · {value}": "{kind}练习 · {value}",
    "Word drill · {value}": "单词练习 · {value}",
    "Phoneme drill · {value}": "音标练习 · {value}",
    "Custom drill · {value}": "自定义练习 · {value}",
    "❌ Words to fix: ": "❌ 需要纠正的单词：",
    "🔊 Practice: ": "🔊 练习：",
    "Outcome: ✅ Mastered": "结果：✅ 已掌握",
    "Outcome: 🔁 Try again next time": "结果：🔁 下次再试",
    "Word": "单词",
    "Phoneme": "音标",
    "Custom": "自定义",
    "▶  Record (Space)": "▶  录音（空格）",
    "🔊  Example": "🔊  示范读",
    "🎙  My recording": "🎙  我的录音",
    "Auto-next phrase": "自动下一句",
    "(no phrase available)": "（没有可用句子）",
    "Record your attempt when ready.": "准备好后录音。",
    "Previous phrase — press Record to retry.":
        "上一句——按“录音”重试。",
    "❌ Words to fix: {words}": "❌ 需要纠正的单词：{words}",
    "🔊 Practice: {phonemes}": "🔊 练习：{phonemes}",
    "Drill finished": "练习完成",
    "Target: {type} = {value}": "目标：{type} = {value}",
    "Drill report": "练习报告",
    # Drill fallback messages (drill/engine.py fallback_message, localized at
    # display time by ui_drill.py). These are shown when the drill substitutes
    # the synthetic pool because the target was not found in the source text.
    "No target value was given, so the drill uses sample phrases.":
        "没有给定目标值，练习使用示例句子。",
    "Your practice text is empty, so the drill uses sample phrases. Add the target word to your practice text, or load a text that contains it.":
        "你的练习文本为空，练习使用示例句子。请把目标单词加入练习文本，或载入包含它的文本。",
    "The word \"{value}\" is not in your practice text, so the drill uses sample phrases. Add it to the practice text, or pick a word that appears there.":
        "单词“{value}”不在你的练习文本中，练习使用示例句子。请把它加入练习文本，或选择一个文本中已有的词。",
    "No word in your practice text contains the sound /{value}/, so the drill uses sample phrases. Add a word with this sound to your practice text.":
        "你的练习文本中没有包含音素 /{value}/ 的单词，练习使用示例句子。请加入一个含此音素的词。",
    "The drill uses sample phrases because the target was not found in your practice text.":
        "因为目标未出现在练习文本中，练习使用示例句子。",

    # ------------------------------------------------------------------
    # Settings window (settings_window.py)
    # ------------------------------------------------------------------
    "Mimora - Settings": "Mimora - 设置",
    "General": "常规",
    "Your name": "你的名字",
    "Used in the greeting and the per-user calibration.":
        "用于问候语和按用户校准。",
    "Language": "练习语言",
    "The language you practice. Changing it needs a restart.":
        "你练习的语言。更改后需要重启。",
    "Difficulty": "难度",
    "Difficulty of generated phrases: 0 = complete beginner (simplest words, present tense only), 5 = advanced (rich vocabulary, complex sentences). Applies from the next phrase.":
        "生成句子的难度：0 = 完全初学者（最简单的词、仅现在时），5 = 高级（词汇丰富、句子复杂）。从下一句开始生效。",
    "Accent": "口音",
    "Regional variant of the language.": "该语言的地区变体。",
    "Voice": "声音",
    "Translation": "翻译",
    "Empty = translation off.": "空 = 关闭翻译。",
    "Phrase length": "句子长度",
    "full = whole sentence, fragment = 2-4 words. Disabled while the LLM backend is off (source-text sentences are never shortened).":
        "full = 完整句子，fragment = 2-4 个词。LLM 后端关闭时此项禁用（源文本句子从不缩短）。",
    "Phrase source": "短语来源",
    "llm = generate phrases with the LLM, practice = cycle the practice text's own sentences in order. Also selectable by long-pressing the Next button.":
        "llm = 由 LLM 生成短语，practice = 按顺序循环练习文本自己的句子。也可通过长按“下一句”按钮选择。",
    "When the phrase source is practice, pick a random sentence instead of cycling in order. Requires a restart to take effect.":
        "当短语来源为 practice 时，随机选择句子而非按顺序循环。需重启后生效。",
    "Re-present phrases you have practiced on the Ebbinghaus forgetting-curve schedule (1, 2, 4, 7, 15, 30, 60 days) instead of only generating new ones. Requires a restart to take effect.":
        "按艾宾浩斯遗忘曲线时间表（1、2、4、7、15、30、60 天）重新呈现你练习过的短语，而非只生成新句。需重启后生效。",
    "Reference speed": "参考语速",
    "Normal reference playback speed. The Slow ▶ button next to Reference plays 0.1 below this (e.g. 0.9 -> 0.8).":
        "正常参考播放语速。“参考发音”旁的“慢速 ▶”按钮以比此低 0.1 的速度播放（例如 0.9 → 0.8）。",
    "Random voice per phrase": "每句随机声音",
    "Play back your recording": "回放你的录音",
    "Practice text file": "练习文本文件",
    "Pronunciation": "发音",
    "Scoring engine": "评分引擎",
    "Phoneme anchor mode": "音素锚定模式",
    "Include fluency & stress in score": "将流利度与重音纳入评分",
    "Fluency weight (%)": "流利度权重（%）",
    "Target speaking rate (WPM)": "目标语速（词/分钟）",
    "Appearance": "外观",
    "Color theme": "颜色主题",
    "Show articulation face": "显示发音嘴型",
    "Auto-play phrase": "自动播放句子",
    "Auto-next phrase": "自动下一句",
    "Auto-next delay (seconds)": "自动下一句延迟（秒）",
    "Require passing score for next phrase": "通过评分后才能进入下一句",
    "Show intonation & stress": "显示语调与重音",
    "Collapse practice text": "折叠练习文本",
    "LLM & phrase generation": "LLM 与句子生成",
    "LLM backend": "LLM 后端",
    "LM Studio host": "LM Studio 主机",
    "GGUF model file": "GGUF 模型文件",
    "Context window (tokens)": "上下文窗口（token）",
    "Phrase-gen window sentences": "句子生成窗口句子数",
    "Phrase-gen window repeats": "句子生成窗口重复数",
    "History & Stats": "历史与统计",
    "Enable history": "启用历史",
    "Save per-take data": "保存每次尝试的数据",
    "Idle timeout (seconds)": "空闲超时（秒）",
    "Daily target (minutes)": "每日目标（分钟）",
    "Native language": "母语",
    "Drill pass threshold": "练习通过阈值",
    "Drill max attempts": "练习最大尝试次数",
    "Dictation pass threshold": "听写通过阈值",
    "Dictation max attempts": "听写最大尝试次数",
    "Technical": "技术",
    "Save recordings (debug)": "保存录音（调试）",
    "Warm up models": "预热模型",
    "Max record seconds": "最长录音秒数",
    "Silence timeout (seconds)": "静音超时（秒）",
    "Silence threshold": "静音阈值",
    "UI language": "界面语言",
    "The language of the interface. Changing it needs a restart.":
        "界面的语言。更改后需要重启。",
    "Default": "默认",
    "Restart now": "立即重启",
    "▶ Listen": "▶  试听",
    "Browse…": "浏览…",
    "The phoneme engine is experimental for this language...":
        "音标引擎在此语言上仍处于实验阶段…",
    "Voice preview needs a restart into the selected language/variant first.":
        "声音预览需要先重启到所选的语言/变体。",
    "{label}: not a number.": "{label}：不是数字。",
    "{label}: must be a whole number.": "{label}：必须是整数。",
    "{label}: must be at least {minimum:g}.": "{label}：必须至少为 {minimum:g}。",
    "{label}: must be at most {maximum:g}.": "{label}：必须至多为 {maximum:g}。",
    "Reset settings?": "重置设置？",
    "Settings": "设置",
    "Reset all settings to their defaults?\n\nRestart-only settings apply after a restart; Cancel still restores the previous values.":
        "将所有设置重置为默认值？\n\n仅重启的设置将在重启后生效；取消仍会恢复之前的值。",
    "Reset failed - see the error in the main window.":
        "重置失败——请查看主窗口中的错误。",
    "Settings were reset to their defaults.": "设置已重置为默认值。",
    "Applies after restart: {labels}": "重启后生效：{labels}",
    "Restart Mimora?": "重启 Mimora？",
    "These changes apply after a restart:\n{labels}\n\nRestart now?":
        "这些更改将在重启后生效：\n{labels}\n\n现在重启？",
    "Could not save {key} to settings.json.":
        "无法将 {key} 保存到 settings.json。",
    "Mimora": "Mimora",
    "Translation needs the offline translator model ({size} GB), which is not downloaded yet.\n\nMimora will restart to download it. The current session (phrase and score) is lost.\n\nRestart and download now?":
        "翻译需要离线翻译模型（{size} GB），但尚未下载。\n\nMimora 将重启以下载它。当前会话（句子和得分）将丢失。\n\n现在重启并下载？",
    "Export stats as JSON": "导出统计为 JSON",
    "Export takes as CSV": "导出尝试为 CSV",

    # ------------------------------------------------------------------
    # Settings window extra keys (settings_window.py) that the label/help
    # templates above do not cover - kept here so a single pass can audit them.
    # ------------------------------------------------------------------
    "Auto-play phrase before recording": "录音前自动播放句子",
    "Auto-generate next phrase": "自动生成下一句",
    "When on, the software reads each phrase aloud before you record. "
    "When off, you record directly — press Reference ▶ to hear it manually.":
        "开启时，软件在录音前朗读每个句子。关闭时直接录音——点击“参考发音 ▶”手动聆听。",
    "When on, a new phrase is generated automatically after each scored take. "
    "When off, you must click 'Next phrase ▶' to continue.":
        "开启时，每次得分后自动生成新句子。关闭时需点击“下一句 ▶”继续。",
    "What to persist per take: metadata only keeps phrase, "
    "score and error details; with audio also saves the "
    "raw + normalized wav per take (~1-3 MB/take), which is what the attempt list's ▶ button and the history browser replay. Default is with audio, so every attempt keeps its recording.":
        "每次尝试持久化的内容：仅元数据会保存句子、得分和错误详情；含音频还会保存每次尝试的原始 + 归一化 wav（约 1-3 MB/次），供尝试列表的 ▶ 按钮和历史浏览器重播。默认为含音频，因此每次尝试都保留录音。",
    "LM Studio address": "LM Studio 地址",
    "Context size (n_ctx)": "上下文大小（n_ctx）",
    "Text window, sentences": "文本窗口，句子数",
    "Advance window every N phrases": "每 N 句推进窗口",
    "Idle split, minutes": "空闲切分，分钟",
    "Daily target, minutes": "每日目标，分钟",
    "Drill takes per session": "每次练习的尝试数",
    "Dictation sentences per session": "每次听写的句子数",
    "Warm up models at startup": "启动时预热模型",
    "Max recording, seconds": "最长录音，秒",
    "Silence timeout, seconds": "静音超时，秒",
    "Silence threshold (RMS)": "静音阈值（RMS）",
    "The phoneme engine is experimental for this language: no tuned calibration yet, scoring falls back to English.":
        "音标引擎在此语言上仍处于实验阶段：尚无调校校准，评分回退到英语。",
    "Each new phrase speaks with a different voice of the current language. The Voice choice above is kept and used again when this is off. Needs at least two voices.":
        "每个新句子使用当前语言的不同声音。关闭时保留上方“声音”选择。至少需要两种声音。",
    "Hear your take played back before the score. Off skips straight to analysis.":
        "在打分前回放你的录音。关闭则直接进入分析。",
    "Loaded into the practice panel right away.": "立即加载到练习面板。",
    "phoneme = default; acoustic = audio comparison; hybrid = both (needs reference audio, English only); none = scoring off (fast start). Only engines available for the language are shown.":
        "phoneme = 默认；acoustic = 音频对比；hybrid = 两者（需要参考音频，仅英语）；none = 关闭评分（快速启动）。只显示该语言可用的引擎。",
    "global = one calibrated anchor (default, faster); ceiling = a flawless read maps to 100 per phrase.":
        "global = 单一校准锚点（默认，更快）；ceiling = 完美朗读每句映射为 100。",
    "Blend speaking rate (WPM) and intonation shape into the pronunciation score. Off keeps the engine score as-is.":
        "将语速（词/分钟）与语调形状混入发音评分。关闭则保持引擎评分不变。",
    "How much the fluency axis counts in the blended score. 0 = show but do not blend.":
        "流利度轴在混合评分中的占比。0 = 显示但不混合。",
    "Words per minute the tempo half scores against. 0 = no tempo penalty (shape only).":
        "语速部分所对照的词/分钟数。0 = 不扣语速分（仅形状）。",
    "Delay before auto-generating the next phrase. Longer delay gives time to read error feedback.":
        "自动生成下一句前的延迟。更长的延迟留出时间阅读错误反馈。",
    "When on, 'Next phrase' is disabled until the take passes the threshold (repeat until it passes). When off, you may move on at any time.":
        "开启后，直到本次录音通过阈值才会解锁“下一句”（需反复练习直至通过）。关闭则可随时继续。",
    "Pitch and energy charts under the recording controls.": "录音控制下方的音高和能量图表。",
    "Hide the editable text box under its caption.": "在标题下隐藏可编辑文本框。",
    "llama-server starts the official llama.cpp binary for you and uses the GGUF model below; the binary is named by 'llama_server_path' in settings.json (empty = look in bin/llama and on PATH). install.py installs it, so does the app on its first start, and 'python -m mimora.llama_server_fetch' does it at any time. lm-studio requires LM Studio running separately. off = no LLM: phrases are the practice text's own sentences, taken as-is.":
        "llama-server 会为你启动官方 llama.cpp 二进制并使用下方 GGUF 模型；二进制由 settings.json 中的 'llama_server_path' 指定（空 = 在 bin/llama 和 PATH 中查找）。install.py 安装它，应用首次启动也会安装，'python -m mimora.llama_server_fetch' 可随时安装。lm-studio 需要单独运行 LM Studio。off = 无 LLM：句子取自练习文本，原样使用。",
    "Server address as host or host:port (port defaults to 1234), e.g. 192.168.1.50:1234 when LM Studio runs on another machine in the local network.":
        "服务器地址，主机或主机:端口（端口默认为 1234），例如 LM Studio 在局域网另一台机器上运行时用 192.168.1.50:1234。",
    "LLM context window in tokens.": "LLM 上下文窗口（token）。",
    "Source sentences sent to the LLM per request.": "每次请求发送给 LLM 的源句子。",
    "When off, takes are not persisted. Practice still works; only the running session's tally is kept.":
        "关闭时不持久化尝试。练习仍可进行；仅保留当前会话的计数。",
    "Minutes without a take before a new session starts. 30 minutes is the default; a coffee break won't split, an overnight gap will.":
        "多久没有尝试后开始新会话。默认 30 分钟；喝杯咖啡不会切分，隔夜间隔会。",
    "Shown on the stats dashboard; the streak badge is based on this.":
        "显示在统计仪表盘上；连续达标徽章基于此。",
    "Used to pick the IPA mother-tongue interference table on the Weakness panel.":
        "用于在薄弱项面板选择 IPA 母语干扰表。",
    "A drill target is marked 'mastered' when the last 3 takes all score at or above this.":
        "当最近 3 次尝试得分均不低于此值时，练习目标标记为“已掌握”。",
    "How many takes one drill session runs before it reports done.":
        "一次练习会话在报告完成前运行多少次尝试。",
    "A dictation session is marked 'mastered' when the last 3 attempts all score at or above this.":
        "当最近 3 次尝试得分均不低于此值时，听写会话标记为“已掌握”。",
    "How many sentences one dictation session runs before it reports done.":
        "一次听写会话在报告完成前运行多少句子。",
    "Writes each take's WAVs and phrase to logs/records/.":
        "将每次尝试的 WAV 和句子写入 logs/records/。",
    "Faster first take, slower startup.": "首次尝试更快，启动更慢。",
    "Silence after speech before the take auto-stops.":
        "语音后的静默时长，之后自动停止。",
    "Chunk RMS above this counts as speech.": "高于此值的块 RMS 视为语音。",
    "Could not reset settings.json.": "无法重置 settings.json。",

    # ------------------------------------------------------------------
    # History browser extra keys (ui_history_browse.py)
    # ------------------------------------------------------------------
    "No practice sessions yet.": "还没有练习会话。",
    "{n} attempts": "{n} 次尝试",
    "{n} sentences": "{n} 个句子",
    "avg {avg:.0f}": "平均 {avg:.0f}",
    "work on: {words}{more}": "重点练习：{words}{more}",
    "Clear all": "清空全部",

    # ------------------------------------------------------------------
    # Dictation / drill extra keys
    # ------------------------------------------------------------------
    "First score: {score:.0f}": "首次得分：{score:.0f}",
    "Last score:  {score:.0f}": "最后得分：{score:.0f}",
    "Average:     {score:.1f}": "平均分：    {score:.1f}",
    "\n\nWords to review:\n": "\n\n待复习单词：\n",
    "\nExtra words:\n": "\n多余单词：\n",
    "/{ipa}/ (as in '{example}')": "/{ipa}/（如“{example}”）",
    "Target: {ttype} = {tvalue}": "目标：{ttype} = {tvalue}",

    # ------------------------------------------------------------------
    # Translation language choices (config.py) - display labels.
    # ------------------------------------------------------------------
    "English": "英语",
    "Russian": "俄语",
    "Ukrainian": "乌克兰语",
    "Spanish": "西班牙语",
    "French": "法语",
    "German": "德语",
    "Italian": "意大利语",
    "Chinese": "中文",
    "Japanese": "日语",
}

# The English catalog is the identity. Entries that need an explicit English
# template (plural forms) come from _EN; every other key renders as itself.
_CATALOGS: Dict[str, Dict[str, str]] = {
    "en": _EN,
    "zh": _ZH,
}

_language = "en"


def set_language(code: str) -> None:
    """Select the active UI language.

    *code* is one of :data:`SUPPORTED_LANGUAGES`; anything else falls back to
    English (never raises - a stale or hand-edited setting must not break the
    GUI). The change applies from the next ``tr()`` call.
    """
    global _language
    _language = code if code in _CATALOGS else "en"


def get_language() -> str:
    """The active UI language code (see :data:`SUPPORTED_LANGUAGES`)."""
    return _language


def available_languages() -> tuple:
    """The selectable UI language codes."""
    return SUPPORTED_LANGUAGES


def tr(text: str, **kwargs) -> str:
    """Translate *text* into the active UI language.

    *text* is the English source literal and also the catalog key. When no
    translation exists, the English string is returned unchanged - a missing
    entry degrades gracefully and never crashes the GUI.

    ``{...}`` placeholders follow ``str.format``: ``kwargs`` fill them in the
    translated template. A kwarg not referenced by the template is ignored, so
    English-only suffixes like ``{plural}`` disappear from Chinese.
    """
    catalog = _CATALOGS.get(_language, _EN)
    template = catalog.get(text, text)
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError, TypeError):
        # A malformed template, a missing kwarg, or a numeric placeholder given
        # None (e.g. {avg:.0f} with avg=None): fall back to the untranslated
        # form so a translation bug never blanks the UI.
        try:
            return text.format(**kwargs) if "{" in text else text
        except (KeyError, IndexError, ValueError, TypeError):
            # The English source itself has stray braces (e.g. user text with
            # {placeholder} in it): never raise out of a translator.
            return text
