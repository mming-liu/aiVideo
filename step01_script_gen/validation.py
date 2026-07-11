"""step01_script_gen.validation - 脚本验证模块

功能：
- _validate_cript(): 验证脚本结构与数据完整性
- _check_prompt_subtitle_alignment(): 校验 prompt 与 subtitle 对齐
- _check_subtitle_char_coverage(): 校验 subtitle 字符覆盖
- parse_response(): 解析 LLM 返回的原始文本为 JSON
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import json
import re
from shared.utils.json_repair import safe_parse_json_object


def _validate_script(script: dict) -> None:
    """验证脚本结构与数据完整性."""
    if "shots" not in script:
        raise ValueError("缺少 shots 字段")
    shots = script.get("shots", [])

    # 兼容 LLM 估算错误：自动修正 shot_count 为实际数量
    reported_count = script.get("shot_count")
    actual_count = len(shots)
    if reported_count and reported_count != actual_count:
        print(f"[WARN] shot_count ({reported_count}) 与实际 shots 数量 ({actual_count}) 不符，已自动修正")
        script["shot_count"] = actual_count

    # 验证每个 shot 必有字段，自动补充缺失
    required = {"index", "narration_segment", "subtitle", "prompt"}
    for i, shot in enumerate(shots, 1):
        missing = required - set(shot.keys())
        if missing:
            # 自动补充缺失字段
            if "index" in missing:
                shot["index"] = i
                print(f"[WARN] shot {i} 缺少 index 字段，已自动补充")
            if "narration_segment" in missing:
                shot["narration_segment"] = shot.get("subtitle", "")
            if "subtitle" in missing:
                shot["subtitle"] = shot.get("narration_segment", "")
            if "prompt" in missing:
                shot["prompt"] = ""

    # 验证 narration == 所有 subtitle 拼接
    combined = "".join(s.get("subtitle", "") for s in shots)
    expected_narration = script.get("narration", "")
    if combined != expected_narration:
        print(f"[WARN] narration 与 subtitle 拼接不一致")

    # 验证每个 shot 的 subtitle == narration_segment
    for i, shot in enumerate(shots, 1):
        sub = shot.get("subtitle", "")
        narr = shot.get("narration_segment", "")
        if sub != narr:
            raise ValueError(f"shot {i}: subtitle 与 narration_segment 不一致")


def _check_prompt_subtitle_alignment(shot_idx: int, subtitle: str, prompt: str) -> None:
    """校验 prompt 中的视觉元素是否能在 subtitle 中找到对应文本.

    简化版：提取 subtitle 中的关键词，检查是否在 prompt 中有对应
    """
    # 提取中文名词和动词（简化版）
    words = re.findall(r'[一-龥]+', subtitle)
    # 检查关键名词是否在 prompt 中
    key_words = [w for w in words if len(w) >= 2]
    for word in key_words[:5]:  # 只检查前5个关键词
        if word not in prompt:
            print(f"[WARN] shot {shot_idx}: prompt 缺少 subtitle 关键词 '{word}'")


def _check_subtitle_char_coverage(shot_idx: int, subtitle: str, search_text: str) -> None:
    """校验 subtitle 中的字符是否都在 search_text 中出现."""
    missing = set(subtitle) - set(search_text)
    if missing:
        print(f"[WARN] shot {shot_idx}: subtitle 包含原文未出现的字符")


def _strip_punct(text: str) -> str:
    """去除标点符号."""
    return re.sub(r"[，。！？、；：''\"\"（）【】《》]", "", text)


def parse_response(raw_text: str) -> dict:
    """解析 LLM 返回的原始文本为 JSON."""
    try:
        script = json.loads(raw_text)
    except json.JSONDecodeError:
        # 尝试修复
        script = safe_parse_json_object(raw_text)
    _validate_script(script)
    return script