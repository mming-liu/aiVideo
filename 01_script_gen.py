from dotenv import load_dotenv
import json
import os
import re
from chapter_reader import extract_section
import storyboard_refs

from _text_gen import config, create_service, TextGenerationRequest

load_dotenv()

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))  # 默认第 1 章
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "8000"))  # 给 LLM 的原文上限，默认 8000 确保单章完整

# ---------------------------------------------------------------------------
# 1. 从小说提取指定章节
# ---------------------------------------------------------------------------

def read_chapter(path: str = "锦衣夜行_77119小说.txt", chapter: int = 1,
                 max_chars: int = 8000) -> str:
    """提取小说指定章节，截取前 max_chars 字符（控制 token 用量）."""
    full_text = extract_section(path, chapter)
    # 截取前 max_chars 字符
    truncated = full_text[:max_chars]
    if len(full_text) > max_chars:
        print(f"[INFO] 章节原文共 {len(full_text)} 字符，"
              f"截取前 {max_chars} 字符用于生成分镜")
    return truncated


# ---------------------------------------------------------------------------
# 2. 构建 prompt
# ---------------------------------------------------------------------------

PROMPT_TEMPLATE = """
你是一位专业的古风电影分镜导演，也是一位资深的中国古装剧摄影指导。请严格基于以下小说原文，生成一部约 55 秒抖音竖屏（9:16）短视频的电影级分镜脚本。

## 核心铁律（违反则不合格）
1. 旁白 = 字幕 = 同一段原文：每个镜头的 subtitle 必须等于其 narration_segment，禁止另选句子。
2. 画面必须忠实呈现 narration_segment/subtitle 的原文场景：原文写什么，画面就拍什么，禁止生成"通用古风画面"。
3. 每个镜头的 prompt 必须是一段电影化视觉描述，包含景别、机位、运镜、主体动作、环境光影、色彩情绪，且必须出现原文中的关键名词/动作。
4. **强制覆盖完整情节弧线**：6 个镜头必须覆盖原文从头到尾的全部关键情节转折，包括开头氛围渲染 AND 中间事件发展 AND 结尾高潮/冲突/转折。禁止只取前半段风景描写而忽略后半段的冲突事件。

## 可用镜头语言（必须从中选择并写入 prompt）

景别（shot size，6 个镜头至少出现 3 种不同景别，禁止连续同景别）：
{shot_sizes}

机位角度（camera angle）：
{camera_angles}

运镜方式（camera movement）：
{camera_movements}

景深/构图（可选，用于强化画面控制）：
{depth_of_field}

光影氛围（lighting mood，根据场景时间选择）：
{lighting_moods}

色彩情绪（color palette，古风权谋常用）：
{color_palettes}

古风人物外貌/服装参考（用于保持人物一致）：
{costumes}

古风场景元素参考：
{locations}

## 每个镜头的 prompt 必须按以下格式生成

```
古风权谋卧底影视剧，9:16竖屏，电影柔光，暗调低饱和，真人写实古装，无动漫，高清1080P，[景别]，[机位角度]，[运镜方式]，[主体人物外貌+具体动作链+微观物理细节]，[环境场景+时间+光源方向+天气/氛围]，[色彩情绪]，[画面必须出现的原文关键词]，[情绪词]
```

prompt 生成要求：
- 必须以固定前缀开头（古风权谋卧底影视剧，9:16竖屏，电影柔光，暗调低饱和，真人写实古装，无动漫，高清1080P），但后半部分必须是具体电影化描述，不能是简单场景概述。
- 主体动作必须具体：不说"走路"，说"双手负后缓步前行，衣摆带风"；不说"看"，说"眯眼凝视，眉心微蹙"。
- 必须包含至少一个微观物理细节：如"指尖发白"、"额角汗珠"、"衣袂翻飞"、"水面涟漪"、"烛光摇曳"。
- 必须出现原文关键词：如 narration_segment 里有"烈日"、"土皮"、"娃娃"、"箫音"、"老农"、"牛"，prompt 里必须有对应视觉元素。
- 景别不得连续重复：相邻两个镜头不能是同一种景别。
- 情绪弧线：每个镜头必须有明确的情绪词（如"燥热慵懒"、"清幽孤寂"、"紧张压抑"、"温暖怀旧"）。

## 步骤（必须严格按顺序执行）

第1步 — 将原文均匀拆分为 6 个连续段落，不跳段不遗漏，覆盖原文从头到尾的全部情节转折。每段应对应原文约 700-900 字。

**关键约束：
- 前 2 个镜头：覆盖段落 1-2（原文开头部分，氛围渲染）
- 中间 2 个镜头：覆盖段落 3-4（原文中间部分，事态发展）
- 最后 2 个镜头：必须覆盖段落 5-6（原文结尾部分，高潮/冲突/转折事件）
- 禁止 6 个镜头全部取自原文前半段。最后 2 个镜头的 narration_segment 必须从原文后半段摘取。
- 如果原文结尾有刺杀、死亡、冲突、逃离、反转等强事件，最后 1-2 个镜头必须呈现这些事件，不得用风景/氛围描写替代。**

第2步 — 为每个段落生成以下字段：
  - narration_segment：从该段落中摘取最核心的 1 句话作为该镜头的旁白，约 10-18 字。严格使用原文原句，不改写。
  - subtitle：**必须与 narration_segment 文字 100% 相同。**
  - prompt：按上面格式生成的电影化视觉描述。必须包含：景别、机位、运镜、主体外貌与动作、环境光影、色彩情绪、原文关键词、情绪词。
  - duration：估算朗读该 narration_segment 的秒数（中文约 4 字/秒 + 1s 停顿）。

第3步 — narration 字段：将 6 个 narration_segment 按顺序拼接成完整旁白。总字数控制在 100-150 字。

## 校验清单（生成前逐条自检，不合格重做）
- shot[i].subtitle == shot[i].narration_segment（逐字相同）
- narration == 所有 subtitle 按顺序拼接
- 每个 prompt 中必须出现对应 narration_segment 的核心名词或动作
- 6 个镜头中至少使用 3 种不同景别，无连续同景别
- 每个 prompt 长度控制在 80-150 字
- **shot[5] 和 shot[6] 的 narration_segment 必须来自原文的后半段（约 50% 位置之后），覆盖结尾的关键事件（冲突/刺杀/死亡/逃离/反转等），禁止又是风景描写**

## 输出纯 JSON 格式（不要 markdown 代码块标记）
{{
  "narration": "6个narration_segment拼接（=6个subtitle拼接），总字数 80-120 字",
  "negative_prompt": "卡通、动漫、畸形五官、明亮强光、现代服装、3D渲染",
  "shots": [
    {{
      "index": 1,
      "duration": 5,
      "narration_segment": "太阳像火炉般烘烤着青州府大地",
      "subtitle": "太阳像火炉般烘烤着青州府大地",
      "prompt": "古风权谋卧底影视剧，9:16竖屏，电影柔光，暗调低饱和，真人写实古装，无动漫，高清1080P，远景，极低机位仰拍，缓拉镜头，干裂黄褐色大地上蒸腾着扭曲热浪，光腚娃娃赤脚奔跑揭土皮，远处枯柳耷拉蝉鸣虚化，烈日高悬天空正中，从地面裂缝缓慢上移至孩童嬉戏，暖金与焦褐色调交织，燥热慵懒"
    }},
    ...共 6 个镜头，每个镜头 duration 4-7 秒，总和 30-36 秒
  ]
}}

小说原文：
{novel_text}
"""

SYSTEM_PROMPT = ("你是一位专业的古风电影分镜导演兼摄影指导，擅长将小说原文转化为电影级视觉描述。"
                 "铁律：每个镜头的 subtitle 必须与 narration_segment 逐字相同，音字严格同步。"
                 "铁律：6 个镜头必须平均分布，覆盖原文从头到尾的完整情节，"
                 "前 2 镜取开头、中 2 镜取中部、后 2 镜必须取结尾（包括高潮/冲突/刺杀/死亡/逃离等事件）。"
                 "禁止 6 个镜头全部取自原文前半段。"
                 "每个镜头的 prompt 必须包含景别、机位、运镜、主体动作、环境光影、色彩情绪，并出现对应 narration_segment 的原文关键词。"
                 "只输出纯JSON，不要markdown代码块标记，不要额外文字。")


def _format_list(items: list) -> str:
    """将列表格式化为换行符分隔的字符串."""
    return "\n".join(f"  - {item}" for item in items)


def _format_dict(d: dict) -> str:
    """将字典格式化为 key: value 的字符串."""
    return "\n".join(f"  - {k}: {v}" for k, v in d.items())


def build_prompt(novel_text: str) -> str:
    """构建最终发送给 LLM 的 prompt，注入镜头语言参考表."""
    return PROMPT_TEMPLATE.format(
        novel_text=novel_text,
        shot_sizes=_format_list(storyboard_refs.SHOT_SIZES),
        camera_angles=_format_list(storyboard_refs.CAMERA_ANGLES),
        camera_movements=_format_list(storyboard_refs.CAMERA_MOVEMENTS),
        depth_of_field=_format_list(storyboard_refs.DEPTH_OF_FIELD),
        lighting_moods=_format_dict(storyboard_refs.LIGHTING_MOODS),
        color_palettes=_format_dict(storyboard_refs.COLOR_PALETTES),
        costumes=_format_list(storyboard_refs.ANCIENT_COSTUMES),
        locations=_format_list(storyboard_refs.ANCIENT_LOCATIONS),
    )


# ---------------------------------------------------------------------------
# 3. 文本生成（通过 _text_gen 服务）
# ---------------------------------------------------------------------------

def gen_spy_script(novel_text: str) -> str:
    """调用文本生成服务生成分镜脚本（返回纯文本）."""
    prompt = build_prompt(novel_text)
    service = create_service()
    req = TextGenerationRequest(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        max_tokens=4000,
        temperature=0.7,
    )
    result = service.generate(req)
    if not result.success:
        raise RuntimeError(f"文本生成失败: {result.error}")
    return result.text


# ---------------------------------------------------------------------------
# 4. 解析响应 → 提取并保存分镜 JSON
# ---------------------------------------------------------------------------

def _validate_script(script: dict) -> None:
    """校验分镜脚本的基本约束，给出警告但不阻断."""
    shots = script.get("shots", [])
    narration = script.get("narration", "")

    for i, shot in enumerate(shots, 1):
        seg = shot.get("narration_segment", "").strip()
        sub = shot.get("subtitle", "").strip()
        prompt = shot.get("prompt", "")

        if seg and sub and seg != sub:
            print(f"[WARN] 镜头{i}: subtitle 与 narration_segment 不一致")
            print(f"       subtitle={sub}")
            print(f"       narration_segment={seg}")

        if seg and seg not in prompt and not any(kw in prompt for kw in seg.split()[:3]):
            print(f"[WARN] 镜头{i}: prompt 可能缺少原文关键词，请检查")
            print(f"       prompt={prompt[:80]}...")

    if shots and narration:
        reconstructed = "".join(s.get("subtitle", "") for s in shots)
        if narration.replace("，", "").replace("。", "") != reconstructed.replace("，", "").replace("。", ""):
            print("[WARN] narration 与所有 subtitle 拼接不完全一致（可能因标点/空格差异）")


def parse_response(raw_text: str) -> dict:
    """从 LLM 返回的文本中提取分镜 JSON."""
    print(f"[INFO] 模型返回文本长度: {len(raw_text)} 字符")

    # 清理 markdown 代码块标记
    cleaned = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # 解析 JSON
    try:
        script = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[WARN] JSON 解析失败，尝试修复... 前 200 字符: {cleaned[:200]}")
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            script = json.loads(cleaned[start : end + 1])
        else:
            raise ValueError(f"无法解析模型返回的 JSON: {e}") from e

    _validate_script(script)
    return script


# ---------------------------------------------------------------------------
# 5. 主流程
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[INFO] 当前 Text Provider: {config.PROVIDER}")

    # Step 1 — 读取指定章节
    novel_content = read_chapter(chapter=CHAPTER_INDEX, max_chars=PROMPT_MAX_CHARS)
    print(f"[INFO] 选取第 {CHAPTER_INDEX} 章前 {len(novel_content)} 字符作为素材")
    print(f"[INFO] 原文预览: {novel_content[:100]}...\n")

    # Step 2 — 调用 API 生成分镜
    raw_response = gen_spy_script(novel_content)

    # Step 3 — 解析并保存
    script = parse_response(raw_response)

    OUTPUT_FILE = "script_result.json"
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    # Step 4 — 打印摘要
    print(f"\n{'='*60}")
    print(f"分镜脚本已保存至 {OUTPUT_FILE}")
    print(f"{'='*60}")
    print(f"旁白文案 ({len(script.get('narration', ''))} 字):")
    print(f"  {script.get('narration', 'N/A')[:200]}...\n")
    print(f"负面提示词:")
    print(f"  {script.get('negative_prompt', 'N/A')}\n")

    shots = script.get("shots", [])
    print(f"共 {len(shots)} 个镜头:\n")
    for shot in shots:
        print(f"  镜头{shot.get('index', '?')} ({shot.get('duration', '?')}s)")
        print(f"    字幕: {shot.get('subtitle', 'N/A')}")
        print(f"    画面: {shot.get('prompt', 'N/A')[:120]}...")
        print()
