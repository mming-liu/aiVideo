# O7 发音词典与字幕屏显质量门（2026-11-28）

> 承接 O6（`de2d509`）。`RULE_SOURCE_FINGERPRINT` 不变（`4fba3d45a6851f41`）；
> 零 provider 调用、正式产物零写入。

## 1. 发音词典：只改送 TTS 的文本

`tts/pronunciation_lexicon.py`（新）：

- `text`（展示/字幕）**永不改写**；派生 `tts_text = lexicon.apply(text)`；
- 词典来源：内置 10 条**通用**古风多音字（可汗/单于/仆射/参差/龟裂/女红/吐蕃/
  大宛/身毒/阿房宫），外加 `TTS_PRONUNCIATION_LEXICON` 指向的 `.json` / `.yaml`
  （文件条目优先，未覆盖的内置条目仍生效）；`off/0/false` 完全关闭；
- 全部内置改写**等长**（2→2、3→3），字数与时长预算不变；
- 最长匹配、左→右、不重入；文件不可读/结构非法 ⇒ `LexiconError`（不静默退回内置）；
- `digest()` 供台账标注「这一版读音表」。

身份与失效：`SpeechUnit.tts_text` 只在**与 text 不同**时进入 `request_key_for`，
因此词典一改，命中句 key 变化 → 该句缓存失效重算；未命中句 key 不变 → 继续复用。
`synthesize_unit` 的分段与无损校验改用 `tts_text`（真正要念的文本）。

## 2. 字幕屏显质量门

`step04_merge/subtitle_gate.py`（新）：

| 代码 | 判据 |
| --- | --- |
| `cue_too_long` | 单屏（按最长一行）字符数 > `MERGE_SUBTITLE_MAX_CHARS`（默认 16） |
| `cue_too_short` | 停留 < `MERGE_SUBTITLE_MIN_DURATION_SEC`（默认 0.4s） |
| `cue_time_invalid` | start 为负 / end < start |
| `cue_out_of_range` | end 超出成片总时长 |
| `cue_crosses_shot` | 越过所属镜头的 `[start, end]` 窗口 |
| `cue_overlap` | 与前一条 cue 重叠 |
| `cue_empty` | 空白文本 |

`wrap_plan()` 按标点均衡拆分（保持文本顺序、按字符比例分配时窗、拆不动则等长硬切）；
`enforce()` 三档策略 `off` / `warn`（默认）/ `block`：

- **默认 `warn` 只报告、不改写 plan**（既有 SRT 逐字节不变）；
- `block` 才自动拆行，仍有问题则抛 `SubtitleErgonomicsError`；
- 已接入 `step04_merge/srt_gen.py::generate_srt`（写盘前过门）。

## 3. 测试

- `tests/step03_tts_audio/test_pronunciation_lexicon.py`（13 用例）：内置/自定义
  JSON/YAML/关闭、最长匹配、坏词典 fail-closed、`request_key` 只随 `tts_text` 变、
  词典更新只让命中句失效；
- `tests/step04_merge/test_subtitle_ergonomics_gate.py`（14 用例）：六类问题的正反例、
  拆行保序保时长、`warn` 不改写、`off` 无操作、`block` 先拆后判、阈值可配与非法回退。

回归：`tests/step04_merge` + `tests/enhancements` 共 600 项 → 594 passed / 6 failed /
2 skipped，6 项失败全是 O0 基线里的既有失败（merge_backend / tts_providers /
video_reroll）；`tests/step03_tts_audio` + `tests/tts` + `tests/enhancements` 601 passed，
同 6 项既有失败。

## 4. 残余

1. **`block` 未设为默认**：字幕行宽属于产品可读性口径（当前产出按 30 字/行生成），
   默认改成 16 字会改变所有既有成片字幕形态——按「产品语义由用户决定」，本包只提供
   门与自动拆行能力，默认 `warn`。
2. 字幕门目前只在 SRT 生成处生效，**尚未**写入 `step04_quality.json` 的 media 段落
   （那需要把 plan 传进质量门；等默认策略确定后再接，避免现在就把口径钉死）。
3. 发音修正只做**同音改写**，不做拼音/SSML：当前 provider 只接受纯文本。
