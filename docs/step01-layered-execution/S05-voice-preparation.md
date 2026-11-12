# S05：声音准备与实测

> 离线可完成项：四类声音来源的准备与实测、窗口冲突、身份一致性、反馈路由。
> 未调用任何 TTS provider；真实合成仍需授权，TTS 分支保留旧音频。

## 1. 真实入口与数据

| 项 | 位置 |
|---|---|
| 准备/实测 | `shared/voice_preparation.py::build_voice_preparation` / `measure_units` |
| Step03 plan-only | `step03_tts_audio/main.py`：读取 `_compute_voice_windows`，写 `<candidate>/plan/voice_preparation.json` |
| Step03 候选实测 | `step03_tts_audio/main.py::_run_candidate`：对真实 `AudioArtifact.path` 做 ffprobe，写 attempt 内 `voice_preparation.json` |
| 反馈 | `feedback_for_conflicts`：正文/说话人/双来源 → `step01`；时长/音轨 → `voice_execution` |
| 旧音频 | 未改 TTS 生成/复用逻辑；只新增报告，不替换旧音轨 |

报告逐单元：`unit_id`、`kind`、`speaker`、`text`、`shot_index`、
`required_playback_sec`（所需/声明时长）、`actual_sec`、
`available_window_sec`（单元窗口与镜头窗口取小）、
`shot_audio_window_sec`、`measurement`（ffprobe/未测量原因）、`audio_sha256`、
身份（文本 sha256/说话人/revision/source）。

## 2. 四类来源

| 来源 | 判定 | 未核实处理 |
|---|---|---|
| 独立 TTS 对白 | `tts_dialogue`，实测优先 | 未实测 → `actual_sec` 用声明值，`verified=false`，不冒充实测 |
| 图外旁白 | `narration`，独立音轨，与视频原声分开记录 | 缺独立音轨 → `audio_track_missing`（要求音轨时）；超窗 → `audio_window_overflow` |
| 原生视频对白 | `native_dialogue`，必须 `verified=true` + 有效时长 | 未核实 → `native_dialogue_unverified`，不凭音轨存在判通过 |
| 无声音要求 | 无对白/旁白且无单元 | 写 `no_audio_required_confirmed_at_prepare`，不把空结果当通过 |

## 3. 自动反例

`tests/step03_tts_audio/test_voice_preparation.py`：

- 两种对白来源同镜 → `dual_dialogue_source`（route step01）；
- 旁白叠加/独立声音合计超窗 → `audio_window_overflow`，带 `unit_id`/`shot_index`/
  `overflow_sec`；
- 缺音轨（要求产物时）→ `audio_track_missing`；
- 原生对白 `verified=false` → `native_dialogue_unverified`；
- 正文/说话人与 Step01 不一致 → `voice_text_mismatch` / `voice_speaker_unknown`；
- 实测：本地 ffmpeg 造 1.8s/1.2s wav 通过；3.4s 版本自动超窗。

## 4. 命令与读数

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/step03_tts_audio/test_voice_preparation.py -q `
  --basetemp=.pytest_tmp/s05_module -p no:cacheprovider
```

读数：**5 passed**（本模块新增文件）。

## 5. 未验证项

- 未调用真实 TTS，未验证真实音色/语速/发音质量；实测只针对本地合成 wav。
- 原生对白“完整性”只能核对 `verified` 与时长/身份；真实音轨内容需模型侧证据。
- Step03 既有 TTS 生成/缓存逻辑未改；本模块报告是新增观察面。
