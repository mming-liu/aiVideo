# S08：语义稿与执行计划差异

> 同一冻结夹具的语义稿字段 vs 执行候选字段；受保护语义字段必须逐字保留。

| 样本族 | 版本 | 执行细化字段 | 受保护语义字段 | 语义检查 |
|---|---|---|---|---|
| temporal_ellipsis | baseline | shots / prompt_refined=True | fact_uids=3, required_facts=3, dialogue_lines=1, narration_lines=1, voice_units=0 | [] |
| temporal_ellipsis | mutation | shots / prompt_refined=True | fact_uids=2, required_facts=2, dialogue_lines=1, narration_lines=0, voice_units=0 | ['causal_gap', 'time_ellipsis_lost'] |
| offscreen_voice | baseline | shots, visual_plan / prompt_refined=True | fact_uids=3, required_facts=3, dialogue_lines=2, narration_lines=1, voice_units=2 | [] |
| offscreen_voice | mutation | shots, visual_plan / prompt_refined=True | fact_uids=3, required_facts=3, dialogue_lines=2, narration_lines=1, voice_units=2 | ['listener_reaction_missing', 'offscreen_speaker_visible'] |
| voice_timing | baseline | audio_windows, voice_units / prompt_refined=False | fact_uids=3, required_facts=3, dialogue_lines=2, narration_lines=1, voice_units=3 | [] |
| voice_timing | mutation | audio_windows, voice_units / prompt_refined=False | fact_uids=3, required_facts=3, dialogue_lines=2, narration_lines=1, voice_units=3 | ['audio_window_overflow', 'native_dialogue_unverified'] |
