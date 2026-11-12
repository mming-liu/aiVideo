# S06：最终时间轴验收与可播放样片

> 离线可完成项：合成前完整验收、合成后复核、转场扣时、音频越界、字幕边界、缺轨、
> 正片/钩子独立口径、本机 ffmpeg 可播放样片。未发起任何模型/媒体 provider 调用。

## 1. 真实入口与数据

| 项 | 位置 |
|---|---|
| 验收模型 | `step04_merge/timeline.py::build_timeline_acceptance` |
| 合成后复核 | `step04_merge/timeline.py::verify_rendered_output` |
| Step04 主入口 | `step04_merge/main.py`：`validate_timeline` 后立刻验收；阻塞不进入编码；渲染后复核；候选 attempt 写 `timeline_acceptance.json` |
| dry-run | 报告新增 `timeline_acceptance` |
| 样片工具 | `tools/build_timeline_sample.py`（本机 ffmpeg 造片段/旁白/字幕 + 渲染） |

`MergeTimeline` 新增 `acceptance` 字段并进入 `to_dict()`。验收逐项记录：片段声明/实测
时长、音频流、音轨实测与窗口、字幕 cue、overlay、转场重叠、hook/body 独立时长、
总长与差额、受影响 clip。

## 2. 自动反例（`tests/step04_merge/test_timeline_acceptance_s06.py`）

| 反例 | 读数 |
|---|---|
| 转场扣时 | hook 2.0 + body 5.0 − overlap 0.833334 = total 6.166667 |
| 音频越界 | 4.0s TTS 对 3.0s 镜头 → `audio_window_overflow`，clip/shot/overflow 可定位 |
| 时长不足 | `expected_body_sec=7.0` → `timeline_duration_short`、`delta_sec=-2.0`、受影响 clip |
| 字幕越界 | cue end=99s → `subtitle_out_of_window`，cue_id=`c1` |
| 缺轨 | `required_audio=True` 且无音轨/无片段音频 → `audio_track_missing` |
| 成片时长不符 | 1s 短片 vs 6.166s 时间轴 → `rendered_duration_mismatch` |

## 3. 可播放样片

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe `
  tools/build_timeline_sample.py --out .pytest_tmp/s06
```

读数：`acceptance_status=pass`、`rendered_status=pass`、时间轴 `6.166667s`、成片
`6.208333s`（帧/AAC 量化差在 0.15s 容差内）。稳定副本：
`docs/step01-layered-execution/artifacts/s06/sample.mp4`（43,779 bytes）与
`timeline_acceptance.json`。样片含视频轨与音频轨，可用本机播放器/ffprobe 复核。

## 4. 命令与读数

```powershell
C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest `
  tests/step04_merge/test_timeline_acceptance_s06.py -q `
  --basetemp=.pytest_tmp/s06_module -p no:cacheprovider
```

读数：**6 passed**（本模块新增文件）。模块级回归（旁证）：
`tests/step04_merge` = 20 passed / 2 skipped（skip 为既有“正式稿输入不唯一/缺失”）。

## 5. 未验证项

- 真实成片的时间轴验收未运行；样片是本地 ffmpeg 合成，不代表真实模型产出节奏。
- 字幕烧录字形/覆盖层内容未在本样片中验证；只核 cue 时窗。
- 正式产物采纳未执行（合并后责任现状见 S07）。
