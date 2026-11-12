# S07 影子比较（同一冻结输入）

> 本对照记录同一冻结输入下的接收与终局；合并后责任现状见 S07；未采纳正式产物。

| 阻断 | 现行责任/严重度 | 新责任 | 下游接收 | 终局 |
|---|---|---|---|---|
| D1 正片总时长低于下限 | step01 / blocking | S05/S06 | — | checklist_only_no_synthetic_input |
| D2 正片总时长超过上限 | step01 / blocking | S05/S06 | — | checklist_only_no_synthetic_input |
| D3 红果时长标准告警 | step01 / advisory | S06 | — | checklist_only_no_synthetic_input |
| S-01 结果缺因果/过程（causal_gap） | step01 / blocking | S03 | temporal_ellipsis:['causal_gap', 'time_ellipsis_lost']@blocked | downstream_detected_and_routed |
| S-02 时间省略丢失（time_ellipsis_lost） | step01 / blocking | S03 | temporal_ellipsis:['causal_gap', 'time_ellipsis_lost']@blocked | downstream_detected_and_routed |
| S-03 画外声音错误出镜（offscreen_speaker_visible） | step01 / not_detected | S03 | offscreen_voice:['listener_reaction_missing', 'offscreen_speaker_visible']@blocked | downstream_detected_and_routed |
| S-04 听者反应缺失（listener_reaction_missing） | step01 / not_detected | S03 | offscreen_voice:['listener_reaction_missing', 'offscreen_speaker_visible']@blocked | downstream_detected_and_routed |
| S-05 独立声音超窗口（audio_window_overflow） | step01 / not_detected | S05/S06 | voice_timing:['audio_window_overflow', 'native_dialogue_unverified']@blocked | downstream_detected_and_routed |
| S-06 原生对白未核实（native_dialogue_unverified） | step01 / not_detected | S05 | voice_timing:['audio_window_overflow', 'native_dialogue_unverified']@blocked | downstream_detected_and_routed |

## 基线与变异读数

| 样本族 | 基线 | 变异 codes | 变异下游状态 |
|---|---|---|---|
| temporal_ellipsis | accept / [] | ['causal_gap', 'time_ellipsis_lost'] | plan=blocked voice=ready codes=[] |
| offscreen_voice | accept / [] | ['listener_reaction_missing', 'offscreen_speaker_visible'] | plan=blocked voice=ready codes=[] |
| voice_timing | accept / [] | ['audio_window_overflow', 'native_dialogue_unverified'] | plan=blocked voice=blocked codes=['audio_window_overflow', 'native_dialogue_unverified'] |

基线全绿：`True`；三族变异检测：`True`。
