# -*- coding: utf-8 -*-
"""古风/多音字发音词典：**只改送 TTS 的文本**，不改字幕展示文本（2026-11-28 O7）.

## 为什么需要

`text` 同时承担两件事：字幕屏显（必须与原著/改编稿逐字一致）与 TTS 输入（读音要
对）。多音字（「可汗」「单于」「仆射」「参差」…）在纯文本 TTS 里只能按默认读音念，
而改 `text` 又会污染字幕。因此把「读音修正」收敛成一个**派生的** `tts_text`：

    display_text（字幕/台账） = unit.text      —— 永不改写
    tts_text（送 provider）  = lexicon.apply(text)

词典为空或未命中时 `tts_text == text`，请求 key 与行为都不变（零行为变化）。
词典一旦变化，命中的单句 `tts_text` 变了 ⇒ `request_key_for` 变 ⇒ 该句缓存失效重算，
未命中的句子 key 不变、继续复用。

## 配置

- `TTS_PRONUNCIATION_LEXICON`：词典文件路径（`.json` 或 `.yaml`，结构见下）；
  留空 = 只用内置条目；`off`/`0`/`false` = 完全关闭（`tts_text` 恒等于 `text`）。
- 文件结构：`{"entries": {"可汗": "克寒", ...}}` 或直接的 `{"可汗": "克寒"}`
  （YAML 同理）。文件不可读/结构非法 ⇒ **显式报错**，不静默退回内置（否则读音修正
  会悄悄失效）。

## 内容无关性

内置条目只收**通用**古风/书面语多音字（官职、礼制、地名通名、成语），不针对任何
具体作品、人物或情节。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

ENV_LEXICON = "TTS_PRONUNCIATION_LEXICON"
_DISABLED = ("off", "0", "false", "no", "none")

#: 内置通用多音字/古音修正（词 → 同音改写；仅用于 TTS 输入，不进字幕）
BUILTIN_ENTRIES: tuple[tuple[str, str], ...] = (
    ("可汗", "克寒"),
    ("单于", "蝉于"),
    ("仆射", "仆夜"),
    ("参差", "岑差"),
    ("龟裂", "军裂"),
    ("女红", "女工"),
    ("吐蕃", "土波"),
    ("大宛", "大渊"),
    ("身毒", "捐笃"),
    ("阿房宫", "俄旁宫"),
)


class LexiconError(RuntimeError):
    """词典文件不可读/结构非法（显式失败，不静默退回）。"""


@dataclass(frozen=True)
class PronunciationLexicon:
    """不可变词典：按最长匹配、从左到右、互不重叠地改写."""

    entries: Mapping[str, str] = field(default_factory=dict)
    source: str = "builtin"

    def __post_init__(self) -> None:
        pairs = [(str(k), str(v)) for k, v in (self.entries or {}).items()
                 if str(k) and str(v)]
        object.__setattr__(self, "entries", dict(sorted(pairs, key=lambda kv: (-len(kv[0]), kv[0]))))

    @property
    def enabled(self) -> bool:
        return bool(self.entries)

    def apply(self, text: str) -> str:
        """返回 `tts_text`；未命中/禁用时原样返回（不做任何空白规整）."""
        source = str(text or "")
        if not source or not self.entries:
            return source
        out: list[str] = []
        i = 0
        while i < len(source):
            matched = False
            for word, replacement in self.entries.items():
                if source.startswith(word, i):
                    out.append(replacement)
                    i += len(word)
                    matched = True
                    break
            if not matched:
                out.append(source[i])
                i += 1
        return "".join(out)

    def digest(self) -> str:
        """词典内容摘要（供台账/报告标注「这一版读音表」）."""
        import hashlib

        payload = json.dumps([[k, self.entries[k]] for k in self.entries],
                             ensure_ascii=False, sort_keys=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _read_entries(path: Path) -> dict[str, str]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise LexiconError("发音词典不可读: %s（%s）" % (path, exc)) from exc
    suffix = path.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            import yaml

            data = yaml.safe_load(raw)
        else:
            data = json.loads(raw)
    except Exception as exc:                             # noqa: BLE001 - 统一成 LexiconError
        raise LexiconError("发音词典解析失败: %s（%s）" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise LexiconError("发音词典顶层必须是对象: %s" % path)
    entries = data.get("entries", data)
    if not isinstance(entries, dict):
        raise LexiconError("发音词典 entries 必须是对象: %s" % path)
    out: dict[str, str] = {}
    for key, value in entries.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key:
            raise LexiconError("发音词典条目必须是 字符串→字符串: %r=%r" % (key, value))
        out[key] = value
    return out


def load_lexicon(path: str | os.PathLike | None = None,
                 env: Mapping[str, str] | None = None) -> PronunciationLexicon:
    """按配置装载词典（`path` 优先；`off` 关闭；空路径 = 仅内置）."""
    source = os.environ if env is None else env
    raw = str(source.get(ENV_LEXICON, "") or "").strip()
    if raw.lower() in _DISABLED:
        return PronunciationLexicon({}, source="disabled")
    explicit = str(path or raw or "").strip()
    entries: dict[str, str] = {}
    origin = "builtin"
    if explicit:
        file_entries = _read_entries(Path(explicit))
        entries.update(file_entries)
        origin = str(Path(explicit))
    for word, replacement in BUILTIN_ENTRIES:
        entries.setdefault(word, replacement)
    return PronunciationLexicon(entries, source=origin)


def apply_tts_text(text: str, lexicon: PronunciationLexicon | None = None) -> str:
    """便捷入口：给文本算 `tts_text`（默认装载一次进程级词典）."""
    lex = lexicon if lexicon is not None else cached_lexicon()
    return lex.apply(text)


_CACHE: dict[str, PronunciationLexicon] = {}


def cached_lexicon(env: Mapping[str, str] | None = None) -> PronunciationLexicon:
    """进程内缓存（按生效路径缓存；`off` 与路径变化都各自一份）."""
    source = os.environ if env is None else env
    raw = str(source.get(ENV_LEXICON, "") or "").strip()
    key = raw.lower() if raw.lower() in _DISABLED else raw
    lex = _CACHE.get(key)
    if lex is None:
        lex = load_lexicon(env=source)
        _CACHE[key] = lex
    return lex


def reset_lexicon_cache() -> None:
    _CACHE.clear()


def entries_iter(lexicon: PronunciationLexicon | None = None) -> Iterable[tuple[str, str]]:
    lex = lexicon if lexicon is not None else cached_lexicon()
    return tuple(lex.entries.items())


__all__ = ["BUILTIN_ENTRIES", "ENV_LEXICON", "LexiconError", "PronunciationLexicon",
           "apply_tts_text", "cached_lexicon", "entries_iter", "load_lexicon",
           "reset_lexicon_cache"]
