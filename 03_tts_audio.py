"""语音合成 — 从分镜脚本读取旁白，TTS 生成配音.

支持多供应商，通过 .env 的 TTS_PROVIDER 切换:
  dashscope_sdk   → DashScope Python SDK (WebSocket)
  dashscope_http  → DashScope HTTP REST 端点
  openai          → OpenAI 官方 /v1/audio/speech
  openai_compat   → 任何兼容 OpenAI 格式的第三方 API
"""

import json
import sys
from pathlib import Path

from tts import create_provider, TTSRequest
from tts.config import config

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPT_FILE = _PROJECT_ROOT / "script_result.json"


# ---------------------------------------------------------------------------
# 业务逻辑
# ---------------------------------------------------------------------------

def load_narration(path: Path = SCRIPT_FILE) -> str:
    """从分镜脚本 JSON 中提取 narration 字段."""
    if not path.exists():
        raise FileNotFoundError(
            f"分镜脚本不存在: {path}\n请先运行 01_script_gen.py"
        )
    with open(path, "r", encoding="utf-8") as f:
        script = json.load(f)

    narration = script.get("narration", "")
    if not narration:
        raise ValueError(f"分镜脚本中 narration 字段为空: {path}")

    return narration


def gen_narration_tts(text: str, output: str = config.OUTPUT_PATH) -> str:
    """调用 TTS 供应商，生成旁白配音."""
    provider = create_provider()

    req = TTSRequest(
        text=text,
        model=config.MODEL,
        rate=config.RATE,
        volume=config.VOLUME,
        format=config.FORMAT,
        sample_rate=config.SAMPLE_RATE,
    )

    print(f"[INFO] TTS 文本长度: {len(text)} 字")
    print(f"[INFO] 供应商: {config.PROVIDER}")
    print(f"[INFO] 模型: {req.model} | 语速: {req.rate} | 音量: {req.volume}")
    print("[INFO] 正在调用 TTS ...")

    result = provider.synthesize(req, output=output)

    if not result.success:
        raise RuntimeError(result.error)

    size_kb = len(result.audio_bytes) / 1024
    print(f"[OK] 配音生成完成: {result.save_path} ({size_kb:.1f} KB)")
    return result.save_path


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 1. 读取旁白
    narration = load_narration()
    print(f"旁白({len(narration)}字):\n{narration}\n")

    # 2. 确认执行
    answer = input("确认生成配音？(y/N): ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消。")
        sys.exit(0)

    # 3. TTS 生成
    try:
        config.validate()
        gen_narration_tts(narration)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
