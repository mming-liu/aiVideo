# -*- coding: utf-8 -*-
"""把 D1 最终说话人证据并入 l1_l2_evidence.json（零调用）."""
from __future__ import annotations

import json
from pathlib import Path

P = Path(__file__).with_name("l1_l2_evidence.json")
data = json.loads(P.read_text(encoding="utf-8"))
data["L2"]["D1_resolved_speaker"] = {
    "role": "张十三",
    "source": "prefix",
    "source_text": "张十三道：公子爷吃醉了酒，险些跌落水中",
    "carrier_shots": [12, 13],
    "resolved_by": ("step03_tts_audio.shot_tts.resolve_shot_speaker("
                    "dialogue_speech_text(...))"),
    "entry_speaker_field": "",
    "entry_speaker_field_note": (
        "plan_voice_entries 只在已批准 voice_adaptations 的条目上搬运 speaker；"
        "本稿无改编块，故 D1 条目没有 speaker（读作空串）。最终说话人不是从空值"
        "推出来的，而是从镜头 12 的 narration_segment 前缀“张十三道：”解析出来的。"),
    "shot_13_note": (
        "镜头 13 的 narration_segment 为空；step03 的对话消费在 "
        "resolve_shot_speaker 之前就因 dialogue_speech_text 返回 (None, None) 跳过它，"
        "不构成第二个说话人缺口。"),
    "source_vs_candidate": "identical（role 与 source 均相同）",
    "probe": "l2_d1_speaker_probe.json",
}
P.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(str(P))
