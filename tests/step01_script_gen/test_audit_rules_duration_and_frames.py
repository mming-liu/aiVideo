"""审计判据：时长数字取生效配置 + 首帧结果泄漏的定义（2026-10-28 M05）.

匿名样本只验证**规则表达与消费链**：规则文本随生效时长带变化，首帧/尾帧文字
进入送审提示，且规则明确「上一镜终态可以合法成为下一镜起态」。这里不声称离线
测试能证明模型以后必然判断正确。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = {"min": 60, "max": 90, "target_min": 70, "target_max": 80}
EXTENDED = {"min": 60, "max": 120, "target_min": 105, "target_max": 115}
KEYS = ("DURATION_MIN_SEC", "DURATION_MAX_SEC", "DURATION_TARGET_MIN_SEC", "DURATION_TARGET_MAX_SEC")


def _run(band):
    env = dict(os.environ)
    env.update(dict(zip(KEYS, map(str, band.values()))))
    env.update({"OUTPUT_BASE": str(ROOT / ".p1a_exp" / "step01_delivery_20261028" / "probe_runtime" / "audit_rules"),
                "PYTHONIOENCODING": "utf-8",
                "STEP01_LOG_PATH": str(ROOT / ".p1a_exp" / "step01_delivery_20261028" / "probe_runtime" / "audit_rules.log"),
                "VOICE_ADAPTATION_ENABLED": "1"})
    code = textwrap.dedent(r'''
        import json
        from step01_script_gen import audit, main as m

        def shot(index, ff, lf, motion, prompt):
            return {"index": index, "duration_sec": 5.97, "ff_desc": ff, "lf_desc": lf,
                    "motion_desc": motion, "prompt": prompt, "main_subject": "旅人",
                    "narration_segment": "", "frame_layouts": {}, "keyframes": []}

        # 匿名样本：合法连续切镜（前镜放下杯子，后镜从已放下开始）
        legal_prev = shot(1, "旅人站在桌边，手握杯子", "旅人把杯子放在桌上", "前3秒：旅人把杯子放到桌上", "桌边，旅人放下杯子")
        legal_next = shot(2, "旅人把杯子放在桌上", "旅人伸手去开门", "前3秒：旅人伸手去开门", "桌边，旅人伸手开门")
        # 匿名样本：非法提前结果（本镜要开门，首帧已经站在门外）
        illegal_next = shot(3, "旅人已经站在门外，门在身后关上", "旅人沿走廊走远", "前3秒：旅人沿走廊走远", "门外走廊，旅人走远")
        script = {"shots": [legal_prev, legal_next, illegal_next], "hook": {"duration_sec": 4.0},
                  "total_duration": 21.91}
        prompt = audit.build_audit_prompt("匿名原文", {}, script)
        no_hook = audit.build_audit_prompt("匿名原文", {}, {"shots": [legal_prev], "total_duration": 5.97})
        body = 119.4
        shots = [{"index": i + 1, "duration_sec": body / 20, "narration_segment": "",
                  "prompt": "p", "motion_desc": "m"} for i in range(20)]
        duration_prompt = audit.build_audit_prompt(
            "匿名原文", {}, {"shots": shots, "hook": {"duration_sec": 4.0},
                             "total_duration": body + 4.0})
        gate = m._build_step01_quality_gate({"shots": shots, "hook": {"duration_sec": 4.0}}, "")
        print("RESULT:" + json.dumps({
            "version": audit.audit_prompt_version(),
            "prompt": prompt,
            "duration_prompt": duration_prompt,
            "no_hook_prompt": no_hook,
            "duration_blocking": "duration_budget" in (gate.get("blocking_categories") or []),
            "quality_status": gate.get("status"),
        }, ensure_ascii=False))
    ''')
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(next(line[7:] for line in reversed(result.stdout.splitlines())
                           if line.startswith("RESULT:")))


@pytest.mark.parametrize("band", [DEFAULT, EXTENDED])
def test_duration_numbers_come_from_effective_config(band):
    result = _run(band)
    text = result["prompt"]
    assert f"{band['min']}–{band['max']} 秒" in text
    assert f"正片超过 {band['max']} 秒即判 blocking" in text
    assert "不得因钩子另算而豁免" in text
    # 正片 119.4：默认上限 90 必须拒收，扩展上限 120 才放行。
    assert result["duration_blocking"] is (band["max"] < 119.4)
    # 送审上下文同时给出三种时长口径。
    duration_text = result["duration_prompt"]
    assert '"body_duration": 119.4' in duration_text
    assert '"hook_duration": 4.0' in duration_text
    assert '"total_duration": 123.4' in duration_text



def test_duration_band_change_changes_prompt_identity():
    default = _run(DEFAULT)
    extended = _run(EXTENDED)
    assert default["prompt"] != extended["prompt"]
    assert default["version"] != extended["version"], "规则变了，审计提示版本必须失效"


def test_no_hook_rule_states_total_equals_body():
    result = _run(EXTENDED)
    text = result["no_hook_prompt"]
    assert "无钩子时" in text and "就等于" in text
    assert "5. 结尾是否真的来自当前故事。" in text
    assert '"hook_duration": 0.0' in text


def test_frame_leak_rule_uses_action_not_text_equality():
    result = _run(EXTENDED)
    text = result["prompt"]
    assert "上一镜终态可以合法成为下一镜起态" in text
    assert "而不是比较两段描述是否相同" in text
    assert "首帧与上一镜尾帧文字相同本身不构成结果泄漏" in text
    assert "两段文字不同也不能证明修复正确" in text
    # 合法连续样本：后镜首帧逐字等于前镜尾帧，仍然只是正常切镜。
    assert "旅人把杯子放在桌上" in text
    # 非法样本：本镜尚未发生的动作已在首帧完成。
    assert "旅人已经站在门外，门在身后关上" in text