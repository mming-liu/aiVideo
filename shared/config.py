"""统一配置管理 — 所有模块从这里读取 .env.content / .env.keys 配置。

使用方式：
    from shared.config import CHAPTER_INDEX, SHOT_COUNT_MIN, AGNES_API_KEY, ...

这样可以确保配置只在一处加载，避免重复加载。
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# 确保只加载一次：.env.content 存放内容参数，.env.keys 存放 API Key
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")


# ============================================================================
# 章节与书本配置
# ============================================================================
NOVEL_CHAPTER = int(os.getenv("NOVEL_CHAPTER", "1"))
BOOK_INDEX = int(os.getenv("BOOK_INDEX", "1"))
NOVEL_NAME = os.getenv("NOVEL_NAME", "锦衣夜行")
BOOK_NAME = os.getenv("BOOK_NAME", "杀青州")


# ============================================================================
# 分镜生成配置（step01）
# ============================================================================
# v4.1 起镜头数量交由 LLM 决定（prompt 不再写死范围）。这两个变量已**不再进入 prompt**，
# 仅作为 validation 的 sanity 软护栏：实际镜头数落在这个很宽的区间之外才 WARN（不阻断），
# 用于兜住 LLM 抽风输出 2 镜或 50 镜这类废结果。
SHOT_COUNT_MIN = int(os.getenv("SHOT_COUNT_MIN", "4"))
SHOT_COUNT_MAX = int(os.getenv("SHOT_COUNT_MAX", "40"))
CHARACTER_EXTRACTION_ENABLED = os.getenv("CHARACTER_EXTRACTION_ENABLED", "1").strip() == "1"
# v6.6: 场景物体尺寸提取（从原文上下文推导船/车/厅堂等的合理尺寸，替代硬编码，防止画面比例漂移）
SCENE_OBJECT_EXTRACTION_ENABLED = os.getenv("SCENE_OBJECT_EXTRACTION_ENABLED", "1").strip() == "1"
# v7: 场景资产提取（大场景/近景功能场景描述 + 地点/时间段锚定，供资产图生成与 r2v 参考）
SCENE_ASSET_EXTRACTION_ENABLED = os.getenv("SCENE_ASSET_EXTRACTION_ENABLED", "1").strip() == "1"
# v7: 道具资产提取（剧本核心道具 + 外观描述，供道具资产图生成与 r2v 参考）
PROP_ASSET_EXTRACTION_ENABLED = os.getenv("PROP_ASSET_EXTRACTION_ENABLED", "1").strip() == "1"
# v7: 场景/道具资产图生成开关（step02a 帧生成前）
ASSET_GEN_ENABLED = os.getenv("ASSET_GEN_ENABLED", "1").strip() == "1"
# v7: 近景功能场景图（从大场景派生）开关，关闭时只生成大场景图
SCENE_NEAR_SHOT_ENABLED = os.getenv("SCENE_NEAR_SHOT_ENABLED", "1").strip() == "1"
HOOK_ENABLED = os.getenv("HOOK_ENABLED", "1").strip() == "1"
COMPLIANCE_STRICT = os.getenv("COMPLIANCE_STRICT", "0").strip() == "1"
COMPLIANCE_AUTO_FIX = os.getenv("COMPLIANCE_AUTO_FIX", "1").strip() == "1"
GAP_ANALYSIS_ENABLED = os.getenv("GAP_ANALYSIS_ENABLED", "1").strip() == "1"
GAP_MAX_REGEN_ATTEMPTS = int(os.getenv("GAP_MAX_REGEN_ATTEMPTS", "2"))
# v6.7: 镜头数严重超限时的有界收敛重生成。
# 触发条件：实际镜头数 > SHOT_COUNT_MAX（如默认 25）。超限时把「合并冗余镜头」反馈
# 追加到 prompt 重新生成一次，防止 LLM 输出 32+ 镜拖垮下游（视频生成成本/时长翻倍）。
# 仍是软收敛：重生成失败或仍超限则保留原版，不中断流水线。
SHOT_COUNT_REGEN_ENABLED = os.getenv("SHOT_COUNT_REGEN_ENABLED", "1").strip() == "1"
# CLAUDE.md 记载默认 50000，由 chapter_reader 读取（shared/chapter_reader.py）
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "50000"))


# ============================================================================
# 供应商配置
# ============================================================================
VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "agnes")
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "agnes")
TEXT_PROVIDER = os.getenv("TEXT_PROVIDER", "deepseek")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "dashscope_sdk")

# 导演风格配置（可选）：用于视频 prompt 添加电影感前缀
# 有效值：空字符串=无风格，wong_kar_wai=王家卫， Villeneuve=维伦纽瓦 等
CINEMATIC_STYLE = os.getenv("CINEMATIC_STYLE", "")

# 图片生成参考图数量：1=单张（默认，向后兼容），2+=多张参考图上限
# 多张时同框多角色可各自传入肖像，提升人物一致性
IMAGE_MULTI_REF = int(os.getenv("IMAGE_MULTI_REF", "1"))

# 图片生成后视觉质检（ds-vision-skill 接入，step02a 帧生成后自动检查首帧+尾帧）
IMAGE_QC_ENABLED = os.getenv("IMAGE_QC_ENABLED", "1").strip() == "1"  # 总开关（默认开启）
IMAGE_QC_TIMEOUT_SEC = int(os.getenv("IMAGE_QC_TIMEOUT_SEC", "300"))  # 单次质检超时（Agnes 端点慢，默认 300s）
# 是否把 critical 级问题写入 sidecar（默认 1=写 critical，让 submit_poll 遇 critical 自动删帧重生成）。
# 2026-08-09 审计 P0-3：此前默认 0 把 critical 封顶成 warning，导致 submit_poll 的
# 删帧重生成逻辑永远读不到 critical、成为死代码——QC 白检不修复。现统一为「critical 即重生成」。
IMAGE_QC_WRITE_CRITICAL = os.getenv("IMAGE_QC_WRITE_CRITICAL", "1").strip() == "1"


# ============================================================================
# Agnes 视频配置（直接读取环境变量，灵活支持任意数量）
# ============================================================================
AGNES_API_KEY = os.getenv("AGNES_API_KEY", "")
AGNES_API_KEY_2 = os.getenv("AGNES_API_KEY_2", "")
AGNES_API_KEY_3 = os.getenv("AGNES_API_KEY_3", "")
AGNES_API_KEY_4 = os.getenv("AGNES_API_KEY_4", "")
AGNES_API_KEY_5 = os.getenv("AGNES_API_KEY_5", "")
AGNES_API_KEY_6 = os.getenv("AGNES_API_KEY_6", "")
AGNES_API_KEY_7 = os.getenv("AGNES_API_KEY_7", "")
AGNES_API_KEY_8 = os.getenv("AGNES_API_KEY_8", "")
AGNES_BASE_URL = os.getenv("AGNES_BASE_URL", "")
AGNES_VIDEO_MODEL = os.getenv("AGNES_VIDEO_MODEL", "agnes-video-v2.0")
AGNES_VIDEO_WIDTH = int(os.getenv("AGNES_VIDEO_WIDTH", "720"))
AGNES_VIDEO_HEIGHT = int(os.getenv("AGNES_VIDEO_HEIGHT", "1280"))
AGNES_VIDEO_FRAME_RATE = int(os.getenv("AGNES_VIDEO_FRAME_RATE", "24"))
AGNES_VIDEO_MODE = os.getenv("AGNES_VIDEO_MODE", "async")
AGNES_VIDEO_POLL_INTERVAL = int(os.getenv("AGNES_VIDEO_POLL_INTERVAL", "8"))
AGNES_VIDEO_MAX_POLL_ATTEMPTS = int(os.getenv("AGNES_VIDEO_MAX_POLL_ATTEMPTS", "120"))


# ============================================================================
# Agnes 图片配置
# ============================================================================
AGNES_IMAGE_MODEL = os.getenv("AGNES_IMAGE_MODEL", "agnes-image-2.1-flash")
# 实际图片尺寸由各 provider 的档位式 size 参数控制（如 "2K"、"1024x1792"）
# 见 _image_gen/agnes_provider.py 和 vimax_adapters.py


# ============================================================================
# 百炼 (Bailian) 配置 - 文生图/图生视频/文生视频
# ============================================================================
# 地域: cn-beijing (北京) 或 ap-southeast-1 (新加坡)
BAILIAN_REGION = os.getenv("BAILIAN_REGION", "cn-beijing")
BAILIAN_WORKSPACE_ID = os.getenv("BAILIAN_WORKSPACE_ID", "")
BAILIAN_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")  # 复用 DASHSCOPE_API_KEY

# ============================================================================
# Token Plan（阿里云编程额度）配置 - 手工调试脚本用
# ============================================================================
# key 存于 .env.keys（已被 gitignore，不会提交）；端点独立于百炼 Workspace。
# 用途：调试脚本（图片/视频生成）从 TOKEN_PLAN_API_KEY 读取，不再硬编码。
TOKEN_PLAN_API_KEY = os.getenv("TOKEN_PLAN_API_KEY", "")
TOKEN_PLAN_BASE_URL = os.getenv(
    "TOKEN_PLAN_BASE_URL", "https://token-plan.cn-beijing.maas.aliyuncs.com"
)

# 文生图模型 (qwen-image)
BAILIAN_T2I_MODEL = os.getenv("BAILIAN_T2I_MODEL", "qwen-image-2.0-pro-2026-04-22")
# 文生视频模型 (wan2.7-t2v)
BAILIAN_T2V_MODEL = os.getenv("BAILIAN_T2V_MODEL", "wan2.7-t2v-2026-06-12")
# 图生视频模型 (wan2.7-i2v / happyhorse-i2v)
BAILIAN_I2V_MODEL = os.getenv("BAILIAN_I2V_MODEL", "wan2.7-i2v-2026-04-25")
# 图生视频备用模型
BAILIAN_I2V_FALLBACK_MODELS = [
    m.strip() for m in os.getenv("BAILIAN_I2V_FALLBACK_MODELS", "wan2.7-i2v-2026-04-25,happyhorse-1.0-i2v,happyhorse-1.1-i2v").split(",")
    if m.strip()
]
# 参考图生视频模型 (wan2.7-r2v / happyhorse-1.1-r2v)
BAILIAN_R2V_MODEL = os.getenv("BAILIAN_R2V_MODEL", "wan2.7-r2v-2026-06-12")
# 视频参数
BAILIAN_VIDEO_RESOLUTION = os.getenv("BAILIAN_VIDEO_RESOLUTION", "720P")  # 720P 或 1080P
BAILIAN_VIDEO_RATIO = os.getenv("BAILIAN_VIDEO_RATIO", "9:16")  # 16:9, 9:16, 1:1
BAILIAN_VIDEO_DURATION = int(os.getenv("BAILIAN_VIDEO_DURATION", "5"))  # 2-15秒
BAILIAN_VIDEO_POLL_INTERVAL = int(os.getenv("BAILIAN_VIDEO_POLL_INTERVAL", "15"))
BAILIAN_VIDEO_MAX_POLL_ATTEMPTS = int(os.getenv("BAILIAN_VIDEO_MAX_POLL_ATTEMPTS", "120"))
# 图片参数（默认由各 provider 内部档位控制，此处保留 env 覆盖接口）
# BAILIAN_IMAGE_SIZE 和 BAILIAN_IMAGE_N 已废弃，由 agnes_provider 的 size 档位取代


# ============================================================================
# DashScope 配置
# ============================================================================
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2video/generation",
)
DASHSCOPE_VIDEO_MODEL = os.getenv("DASHSCOPE_VIDEO_MODEL", "wan2.7-t2v")
DASHSCOPE_VIDEO_DURATION = int(os.getenv("DASHSCOPE_VIDEO_DURATION", "0"))
DASHSCOPE_POLL_INTERVAL = int(os.getenv("DASHSCOPE_POLL_INTERVAL", "8"))
DASHSCOPE_MAX_POLL_ATTEMPTS = int(os.getenv("DASHSCOPE_MAX_POLL_ATTEMPTS", "120"))


# ============================================================================
# DeepSeek (文本生成) 配置
# ============================================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


# ============================================================================
# TTS 配置（实际生效值见 tts/config.py 的 DASHSCOPE_TTS_RATE/VOLUME）
# ============================================================================


# ============================================================================
# 视频生成通用配置
# ============================================================================
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "0"))  # 0=自动匹配镜头数
VIDEO_MAX_RETRIES = int(os.getenv("VIDEO_MAX_RETRIES", "3"))


# ============================================================================
# 合并配置（step04）
# ============================================================================
MERGE_PRESET = os.getenv("MERGE_PRESET", "ultrafast")