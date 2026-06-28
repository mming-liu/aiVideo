from dotenv import load_dotenv
import requests
import json
import os
import re
from chapter_reader import extract_section

load_dotenv()

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
CHAPTER_INDEX = int(os.getenv("NOVEL_CHAPTER", "1"))  # 默认第 1 章
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "2000"))  # 给 LLM 的原文上限

# Provider 切换: "dashscope" (阿里百炼 Qwen) 或 "deepseek" (DeepSeek 兼容接口)
SCRIPT_PROVIDER = os.getenv("SCRIPT_PROVIDER", "dashscope")

# DashScope (百炼) 配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
DASHSCOPE_MODEL = os.getenv("DASHSCOPE_TEXT_MODEL", "qwen-plus")

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ---------------------------------------------------------------------------
# 1. 从小说提取指定章节
# ---------------------------------------------------------------------------

def read_chapter(path: str = "锦衣夜行_77119小说.txt", chapter: int = 1,
                 max_chars: int = 2000) -> str:
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
你是一位专业的古风短视频分镜导演。请严格基于以下小说原文，生成一部约 55 秒抖音竖屏（9:16）短视频的分镜脚本。

核心原则：旁白 = 原文朗读、字幕 = 原文原句、画面 = 原文场景。三者必须来自同一段原文，按时间顺序严格对齐。

步骤（必须严格按顺序执行）：

第1步 — 将原文均匀拆分为 6 个连续段落（每段 ~300-350 字），不跳段不遗漏，覆盖原文从头到尾的全部情节转折。

第2步 — 为每个段落生成：
  - narration_segment：从该段落中摘取最核心的 1-2 句话作为旁白，约 15-25 字。严格使用原文原句，不改写。
  - subtitle：从该段落中摘取最有视觉张力的 1 句原文原话，15-22 字。
  - prompt：根据该段落描写的场景，生成 9:16 竖屏视觉描述。必须携带前缀「古风权谋卧底影视剧，9:16竖屏，电影柔光，暗调低饱和，烛火局部光影，浅景深缓慢向前推镜，真人写实古装，无动漫，高清1080P」。
  - duration：估算朗读该 narration_segment 的秒数（中文约 4 字/秒 + 1s 停顿）。

第3步 — narration 字段：将 6 个 narration_segment 按顺序拼接成完整旁白。总字数控制在 100-150 字（对应约 25-35 秒朗读）。

输出纯 JSON 格式（不要 markdown 代码块标记）：
{{
  "narration": "6段拼接后的完整旁白（总字数 100-150 字，约 25-35 秒朗读）",
  "negative_prompt": "卡通、动漫、畸形五官、明亮强光、现代服装、3D渲染",
  "shots": [
    {{"index": 1, "duration": 5, "prompt": "古风权谋卧底影视剧，9:16竖屏...", "subtitle": "原文原句摘录"}},
    ...共 6 个镜头，每个镜头 duration 4-7 秒，总和 30-36 秒
  ]
}}

小说原文：
{novel_text}
"""

SYSTEM_PROMPT = "你是一位专业的短视频分镜导演，擅长古风权谋题材。严格以原文为准——旁白摘录原文段落，字幕直接摘原文原句。只输出纯JSON，不要markdown代码块标记，不要额外文字。"


# ---------------------------------------------------------------------------
# 3. Provider 实现
# ---------------------------------------------------------------------------

def _call_dashscope(prompt: str) -> str:
    """阿里云百炼 DashScope (Qwen) 调用."""
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}"}
    payload = {
        "model": DASHSCOPE_MODEL,
        "input": {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        },
        "parameters": {
            "result_format": "json",
            "max_tokens": 3000,
            "temperature": 0.7,
        },
    }

    print(f"[INFO] 正在调用 DashScope ({DASHSCOPE_MODEL}) 生成分镜脚本...")
    res = requests.post(DASHSCOPE_BASE_URL, json=payload, headers=headers, timeout=120)
    if not res.ok:
        print(f"[ERROR] HTTP {res.status_code}: {res.text[:500]}")
    res.raise_for_status()
    api_response = res.json()

    # 从 DashScope 响应结构中提取文本
    output = api_response.get("output", {})
    choices = output.get("choices", [])
    if choices and isinstance(choices, list):
        text = choices[0].get("message", {}).get("content", "")
    else:
        text = output.get("text", "")

    if not text:
        raise ValueError(
            f"API 返回文本为空，完整响应: "
            f"{json.dumps(api_response, ensure_ascii=False)[:500]}"
        )
    return text


def _call_deepseek(prompt: str) -> str:
    """DeepSeek API (OpenAI 兼容接口) 调用."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "使用 DeepSeek 需要安装 openai 库: pip install openai"
        )

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    print(f"[INFO] 正在调用 DeepSeek ({DEEPSEEK_MODEL}) 生成分镜脚本...")
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=3000,
        temperature=0.7,
    )
    return response.choices[0].message.content


def gen_spy_script(novel_text: str) -> str:
    """根据 provider 配置调用对应的 LLM 生成分镜脚本（返回纯文本）."""
    prompt = PROMPT_TEMPLATE.format(novel_text=novel_text)

    if SCRIPT_PROVIDER == "deepseek":
        return _call_deepseek(prompt)
    else:  # 默认 dashscope
        return _call_dashscope(prompt)


# ---------------------------------------------------------------------------
# 4. 解析响应 → 提取并保存分镜 JSON
# ---------------------------------------------------------------------------

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

    return script


# ---------------------------------------------------------------------------
# 5. 主流程
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[INFO] 当前 Script Provider: {SCRIPT_PROVIDER}")

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
        print(f"    画面: {shot.get('prompt', 'N/A')[:100]}...")
        print()
