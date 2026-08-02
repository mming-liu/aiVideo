"""step04_merge.srt_gen - SRT subtitle generation module"""
import sys
from pathlib import Path
import os

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from typing import List


def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def get_shot_target_duration(shot: dict) -> float:
    """Get shot target duration - audio duration. Video will be stretched to this.

    Priority: actual_audio_duration (TTS measured) > duration (text proportion estimate)
    """
    audio = shot.get("actual_audio_duration")
    if audio and audio > 0:
        return float(audio)
    return shot.get("duration", 5)


def find_shot_audio(shot_idx: int, audio_dir: Path):
    """Find audio file for a shot."""
    audio_path = audio_dir / f"shot_{shot_idx}.mp3"
    if audio_path.exists():
        return audio_path
    return None


def split_subtitle_by_punctuation(text: str, max_chars: int = 30) -> List[str]:
    """Split long subtitle into shorter parts by punctuation marks.

    Splits on Chinese periods (。), commas (，), question marks (？),
    exclamation marks (！), and English equivalents.

    Args:
        text: Original subtitle text
        max_chars: Maximum characters per subtitle segment

    Returns:
        List of subtitle segments
    """
    if not text or len(text) <= max_chars:
        return [text] if text else []

    # Define punctuation marks for splitting (prioritized)
    # Chinese and English punctuation that naturally divides sentences
    punctuation = ['。', '；', '！', '？', '，', '.', ';', '!', '?','、', ',']

    parts = []
    current = ""

    for char in text:
        current += char
        if char in punctuation and len(current) > 5:  # At least 5 chars before split
            parts.append(current.strip())
            current = ""

    # Add remaining text
    if current.strip():
        parts.append(current.strip())

    # If splitting by punctuation still results in too long segments,
    # force split by character count
    result = []
    for part in parts:
        if len(part) > max_chars:
            # Split this part into smaller chunks
            for i in range(0, len(part), max_chars):
                chunk = part[i:i + max_chars]
                if chunk:
                    result.append(chunk)
        else:
            result.append(part)

    return result if result else [text[:max_chars]]


def generate_srt(shots: List[dict], durations: List[float], output: Path) -> None:
    """Generate SRT subtitle file based on per-shot target durations.

    v5.2 优化: 长字幕按标点符号智能拆分，而非简单截断
    """
    # 字幕最大显示时长（秒）- 作为拆分依据，不是截断
    max_display_time = 8.0
    max_chars_per_line = 30  # 每行最大字符数（视觉舒适度）

    output.parent.mkdir(parents=True, exist_ok=True)

    current_time = 0.0
    srt_lines = []
    subtitle_count = 0

    for i, (shot, dur) in enumerate(zip(shots, durations), 1):
        # 只对有对话的镜头生成字幕
        # 对话判断标准：文本包含引号（"「」""）或说话动词（说/道/问/答/喊/叫/喝）
        subtitle = shot.get("subtitle", "").strip()
        narration_segment = shot.get("narration_segment", "").strip()

        if not subtitle:
            current_time += dur
            continue

        # 检查是否是对话（有引号或说话动词）
        has_dialogue = any(c in subtitle for c in ['「', '」', '『', '』', '"', '"', '"', '“', '”'])
        # 说话动词必须后跟标点才视为对话（避免"一道道炊烟"的"道"误判）
        has_quote = any(c in subtitle for c in ['"', '"', '«', '»'])
        has_speaker = False
        for w in ['说', '道', '问', '答', '喊', '叫', '喝']:
            pos = subtitle.find(w)
            if pos >= 0 and pos + 1 < len(subtitle) and subtitle[pos + 1] in '：:,,，,.。':
                has_speaker = True
                break
        is_dialogue = has_quote or has_speaker

        if not is_dialogue:
            # 没有对话，跳过字幕
            current_time += dur
            continue

        start = current_time
        end = current_time + dur

        # 优化: 智能拆分长字幕，而非简单截断
        # 规则：
        # 1. 按标点拆分（如有多个句子）
        # 2. 每段字幕不超过 max_chars_per_line 字符
        # 3. 总时长按文字比例分配
        subtitle_parts = split_subtitle_by_punctuation(subtitle, max_chars_per_line)

        if len(subtitle_parts) == 1:
            # 单段字幕，直接使用
            if dur > max_display_time:
                # 如果单个镜头音频太长，警告但保持完整（因为已按标点拆分）
                print(f"  [WARN] Shot {i} subtitle duration {dur:.1f}s > {max_display_time}s (consider splitting in script)")

            subtitle_count += 1
            srt_lines.append(str(subtitle_count))
            srt_lines.append(f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}")
            srt_lines.append(subtitle)
            srt_lines.append("")
        else:
            # 多段字幕：按文字比例分配时间
            total_chars = sum(len(p) for p in subtitle_parts)
            temp_start = start
            for j, part_text in enumerate(subtitle_parts, 1):
                # 按字符比例分配时间
                part_ratio = len(part_text) / total_chars if total_chars > 0 else 1.0 / len(subtitle_parts)
                part_duration = dur * part_ratio

                part_start = temp_start
                part_end = temp_start + part_duration

                subtitle_count += 1
                srt_lines.append(str(subtitle_count))
                srt_lines.append(f"{seconds_to_srt_time(part_start)} --> {seconds_to_srt_time(part_end)}")
                srt_lines.append(part_text)
                srt_lines.append("")

                temp_start = part_end

            # 更新 end time to last part
            end = part_end

        current_time = end

    output.write_text("\n".join(srt_lines), encoding="utf-8")
    print(f"[OK] SRT generated: {output} ({subtitle_count} subtitles, total {current_time:.1f}s)")