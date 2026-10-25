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


def _TEXT_PROVIDER_DEFAULT_GAP_ATTEMPTS() -> str:
    """GAP 重试默认值按 TEXT_PROVIDER 区分（v6.62.1，09-06）.

    agent_plan（doubao）路径 09-06 实测 2 次 GAP 重试全部劣化回退（重试版
    总时长塌缩 + 台词失锚），白烧 ~5 分钟——默认 1 次。deepseek 保留 2 次。
    返回 str 供 int() 解析（保持本文件"load 时算好常量"的惯例）。
    """
    provider = (os.getenv("TEXT_PROVIDER") or "").strip().lower()
    return "1" if provider == "agent_plan" else "2"
CHARACTER_EXTRACTION_ENABLED = os.getenv("CHARACTER_EXTRACTION_ENABLED", "1").strip() == "1"
# v6.6: 场景物体尺寸提取（从原文上下文推导船/车/厅堂等的合理尺寸，替代硬编码，防止画面比例漂移）
SCENE_OBJECT_EXTRACTION_ENABLED = os.getenv("SCENE_OBJECT_EXTRACTION_ENABLED", "1").strip() == "1"
# v7: 场景资产提取（大场景/近景功能场景描述 + 地点/时间段锚定，供资产图生成与 r2v 参考）
SCENE_ASSET_EXTRACTION_ENABLED = os.getenv("SCENE_ASSET_EXTRACTION_ENABLED", "1").strip() == "1"
# v7: 道具资产提取（剧本核心道具 + 外观描述，供道具资产图生成与 r2v 参考）
PROP_ASSET_EXTRACTION_ENABLED = os.getenv("PROP_ASSET_EXTRACTION_ENABLED", "1").strip() == "1"
# 改编层总开关（默认关；见 shared/voice_adaptation.py 与 adaptation_offline_spec.md）
VOICE_ADAPTATION_ENABLED = os.getenv("VOICE_ADAPTATION_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "on")
# v7: 场景/道具资产图生成开关（step02a 帧生成前）
ASSET_GEN_ENABLED = os.getenv("ASSET_GEN_ENABLED", "1").strip() == "1"
# v7: 近景功能场景图（从大场景派生）开关，关闭时只生成大场景图
SCENE_NEAR_SHOT_ENABLED = os.getenv("SCENE_NEAR_SHOT_ENABLED", "1").strip() == "1"
# v6.64（P4.5，09-11）：多路径择优候选数。
# 默认 1 = 关闭（兼容现有行为）。doubao 不稳定时建议 2-3：生成 N 个独立首版，
# 取质量分最高的进入 GAP 循环。用 token 换稳定性，对模型方差问题最有效。
# 按 TEXT_PROVIDER 区分默认值：doubao 方差大默认 2，deepseek 稳定默认 1。
def _SCRIPT_CANDIDATE_TOPK_DEFAULT() -> str:
    provider = (os.getenv("TEXT_PROVIDER") or "").strip().lower()
    return "3" if provider == "agent_plan" else "1"
SCRIPT_CANDIDATE_TOPK = int(
    os.getenv("SCRIPT_CANDIDATE_TOPK", "") or _SCRIPT_CANDIDATE_TOPK_DEFAULT()
)

# v6.66: 跨模型候选池——逗号分隔模型 ID 列表，候选按序号轮转分配。
# 空/未设置 = 全部候选用当前文本 provider 的默认模型。
# 显式设置时，模型 ID 必须属于当前 TEXT_PROVIDER；Step01 当前只用 deepseek-flash。
SCRIPT_CANDIDATE_MODELS = [
    m.strip() for m in os.getenv("SCRIPT_CANDIDATE_MODELS", "").split(",")
    if m.strip()
]
HOOK_ENABLED = os.getenv("HOOK_ENABLED", "1").strip() == "1"
COMPLIANCE_STRICT = os.getenv("COMPLIANCE_STRICT", "0").strip() == "1"
COMPLIANCE_AUTO_FIX = os.getenv("COMPLIANCE_AUTO_FIX", "1").strip() == "1"
GAP_ANALYSIS_ENABLED = os.getenv("GAP_ANALYSIS_ENABLED", "1").strip() == "1"
# GAP 重试上限。v6.62.1（09-06）：按文本供应商区分默认值——
#   - deepseek：2 次（重试版质量常有提升，历史数据支持）
#   - agent_plan（doubao）：1 次（09-06 chapter_001 实测 2 次重试全部劣化回退：
#     「重试版总时长 60s < 上一版 66s + 台词失锚 4 条」，白烧 ~5 分钟/2 次 LLM 调用）
# 可用 GAP_MAX_REGEN_ATTEMPTS 显式覆盖（配置文件仍可写死，env 优先）。
GAP_MAX_REGEN_ATTEMPTS = int(
    os.getenv("GAP_MAX_REGEN_ATTEMPTS", "") or _TEXT_PROVIDER_DEFAULT_GAP_ATTEMPTS()
)

# v6.7: 镜头数严重超限时的有界收敛重生成。
# 触发条件：实际镜头数 > SHOT_COUNT_MAX（如默认 25）。超限时把「合并冗余镜头」反馈
# 追加到 prompt 重新生成一次，防止 LLM 输出 32+ 镜拖垮下游（视频生成成本/时长翻倍）。
# 仍是软收敛：重生成失败或仍超限则保留原版，不中断流水线。
SHOT_COUNT_REGEN_ENABLED = os.getenv("SHOT_COUNT_REGEN_ENABLED", "1").strip() == "1"
# v6.35（08-15，P18 闭环）：GAP 通过后高价值告警（开篇冲突/爆点密度/威胁源朝向/
# 关键帧质量/内容密度）触发「定向修复轮」——只重写告警镜头，不重生成全脚本。
# 有界：最多 REPAIR_MAX_ROUNDS 轮，每轮最多修 6 镜；失败/无告警即停。
REPAIR_ROUNDS_ENABLED = os.getenv("REPAIR_ROUNDS_ENABLED", "1").strip() == "1"
# CLAUDE.md 记载默认 50000，由 chapter_reader 读取（shared/chapter_reader.py）
PROMPT_MAX_CHARS = int(os.getenv("PROMPT_MAX_CHARS", "50000"))


# ============================================================================
# 供应商配置
# ============================================================================
from shared.constants import TEXT_PROVIDER_DEFAULT as _TEXT_PROVIDER_DEFAULT

VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "agnes")
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "agnes")
TEXT_PROVIDER = os.getenv("TEXT_PROVIDER", _TEXT_PROVIDER_DEFAULT)
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
BAILIAN_T2I_MODEL = os.getenv("BAILIAN_T2I_MODEL", "qwen-image-3.0-pro")
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
# 分辨率 = 目标偏好（省钱用 720P，须在模型能力内——wan2.7/happyhorse 官方支持 720P/1080P），
# 非模型约束；模型时长上限由 shared/model_caps 注册表按模型解析（wan2.7/happyhorse=15s），不在此写死
BAILIAN_VIDEO_RESOLUTION = os.getenv("BAILIAN_VIDEO_RESOLUTION", "720P")  # 720P 或 1080P
BAILIAN_VIDEO_RATIO = os.getenv("BAILIAN_VIDEO_RATIO", "9:16")  # 16:9, 9:16, 1:1
BAILIAN_VIDEO_DURATION = int(os.getenv("BAILIAN_VIDEO_DURATION", "5"))  # 兜底默认时长（step01 duration_sec 优先）
BAILIAN_VIDEO_POLL_INTERVAL = int(os.getenv("BAILIAN_VIDEO_POLL_INTERVAL", "15"))
BAILIAN_VIDEO_MAX_POLL_ATTEMPTS = int(os.getenv("BAILIAN_VIDEO_MAX_POLL_ATTEMPTS", "120"))
# 图片参数（默认由各 provider 内部档位控制，此处保留 env 覆盖接口）
# BAILIAN_IMAGE_SIZE 和 BAILIAN_IMAGE_N 已废弃，由 agnes_provider 的 size 档位取代


# ============================================================================
# 火山引擎方舟 (Volcengine Ark) 配置 - Seedream 文生图 / Seedance 视频
# ============================================================================
# 官方文档:
#   图片生成 (Seedream): POST {base}/images/generations
#   视频生成 (Seedance): POST {base}/contents/generations/tasks + 轮询
# API Key 优先读 VOLCENGINE_API_KEY，兼容 ARK_API_KEY 写法
VOLCENGINE_API_KEY = os.getenv("VOLCENGINE_API_KEY", os.getenv("ARK_API_KEY", ""))
VOLCENGINE_BASE_URL = os.getenv(
    "VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
)
# ---- 模型能力矩阵（值来自官方文档，2026-08 核对，实现见 shared/model_caps.py）----
# Seedream 图片：像素区间 + 档位 + 参考图上限
#   5.0 pro  档位 1K/1.5K/2K      像素 [921600, 4624220]  参考图 ≤10
#   5.0 lite 档位 2K/3K/4K        像素 [3686400, 16777216] 参考图 ≤14
#   4.5      档位 2K/4K           像素 [3686400, 16777216] 参考图 ≤14
#   4.0      档位 1K/2K/4K        像素 [921600, 16777216]  参考图 ≤14
#   所有档位均不支持 negative_prompt / seed
# Seedance 视频：时长/分辨率/参考图(reference_image)/有声(generate_audio)
#   2.5         时长 [4,30]  480p/720p        参考图✓ 有声✓ 首尾帧ratio仅adaptive
#   2.0(含fast/mini) 时长[4,15] 480p~4k(fast/mini仅480p/720p) 参考图✓ 有声✓
#   1.5 pro     时长 [4,12]  480p/720p/1080p  参考图✗ 有声✓
#   1.0 系列    时长 [2,12]  480p/720p/1080p  参考图✗ 有声✗
# 调试省钱：VOLCENGINE_T2I_MODEL=doubao-seedream-4-0-250828（1K 档/低像素）；
#           VOLCENGINE_I2V_MODEL=doubao-seedance-2-0-260128（r2v 且便宜）或 1.0 系列（纯文生视频）
# 文生图模型 (Seedream)，403 回退到 T2I_FALLBACK
VOLCENGINE_T2I_MODEL = os.getenv("VOLCENGINE_T2I_MODEL", "doubao-seedream-4-5-251128")
VOLCENGINE_T2I_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("VOLCENGINE_T2I_FALLBACK_MODELS", "doubao-seedream-4-0-250828").split(",")
    if m.strip()
]
# 可选 override：非空则强制所有图片用该 size（档位 1K/2K/4K 或 像素 WxH），
# 仍会按模型能力表收敛；空=按分镜 req.size/ratio 推导
VOLCENGINE_IMAGE_SIZE = os.getenv("VOLCENGINE_IMAGE_SIZE", "")
VOLCENGINE_IMAGE_RPM = int(os.getenv("VOLCENGINE_IMAGE_RPM", "10"))  # 限速参考
# 文生视频模型 (Seedance)，T2V / I2V 分开配置 + 各自 403 回退
VOLCENGINE_T2V_MODEL = os.getenv("VOLCENGINE_T2V_MODEL", "doubao-seedance-1-5-pro-251215")
VOLCENGINE_T2V_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("VOLCENGINE_T2V_FALLBACK_MODELS", "doubao-seedance-1-0-lite-t2v-250408").split(",")
    if m.strip()
]
VOLCENGINE_I2V_MODEL = os.getenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-5-pro-251215")
VOLCENGINE_I2V_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("VOLCENGINE_I2V_FALLBACK_MODELS", "doubao-seedance-1-0-pro-250528").split(",")
    if m.strip()
]
# 视频参数 override：非空/非 0 则强制该值，仍按模型能力表夹取（2.5 首尾帧 ratio
# 强制 adaptive 不可覆盖）；空/0=按分镜 req 推导（分辨率/比例按 req.size，时长按分镜 duration_sec）
VOLCENGINE_VIDEO_RESOLUTION = os.getenv("VOLCENGINE_VIDEO_RESOLUTION", "")  # 480p/720p/1080p/4k
VOLCENGINE_VIDEO_RATIO = os.getenv("VOLCENGINE_VIDEO_RATIO", "")  # 16:9 / 9:16 / 1:1
VOLCENGINE_VIDEO_DURATION = int(os.getenv("VOLCENGINE_VIDEO_DURATION", "0"))  # 秒，0=按分镜时长
VOLCENGINE_VIDEO_POLL_INTERVAL = int(os.getenv("VOLCENGINE_VIDEO_POLL_INTERVAL", "8"))
VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS = int(os.getenv("VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS", "120"))


# ============================================================================
# 火山方舟 Agent Plan (/api/plan/v3) 配置 — 2026-08-30 新增
# ============================================================================
# Agent Plan 是火山方舟为 Agent / 自动化场景提供的新通道，与标准方舟（/api/v3）
# 端点不同，**专属 key 不通用**（普通 ARK key 报 401）。使用本通道可获得
# 官方为 Agent 设计的 fuel 包/计量规则（"AFP 燃料"），但部分功能（如角色
# 形象 reference、参考音频）按 Small 套餐当前无权限（仍可正常生图生视频）。
# 切换方式：.env.content 设 IMAGE_PROVIDER=agent_plan / VIDEO_PROVIDER=agent_plan；
#           现有 volcengine provider 不受影响，保持默认。
AGENT_PLAN_API_KEY = os.getenv("AGENT_PLAN_API_KEY", "")
AGENT_PLAN_BASE_URL = os.getenv(
    "AGENT_PLAN_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
)
# 文生图（默认 Seedream 5.0 lite 免费档，对齐 _image_gen/volcengine 同款 3 张参考图上限）
AGENT_PLAN_T2I_MODEL = os.getenv("AGENT_PLAN_T2I_MODEL", "doubao-seedream-5-0-lite")
# 文生视频（Seedance 2.0 — 4-15s/支持 reference_image/generate_audio）
AGENT_PLAN_T2V_MODEL = os.getenv("AGENT_PLAN_T2V_MODEL", "doubao-seedance-2-0")
# 视频参数 override
AGENT_PLAN_VIDEO_RESOLUTION = os.getenv("AGENT_PLAN_VIDEO_RESOLUTION", "")  # 空=按 req.size 推导
AGENT_PLAN_VIDEO_RATIO = os.getenv("AGENT_PLAN_VIDEO_RATIO", "")  # 空=按 req.size 推导
AGENT_PLAN_VIDEO_DURATION = int(os.getenv("AGENT_PLAN_VIDEO_DURATION", "0"))  # 0=按分镜 duration_sec
AGENT_PLAN_VIDEO_POLL_INTERVAL = int(os.getenv("AGENT_PLAN_VIDEO_POLL_INTERVAL", "20"))
AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS = int(os.getenv("AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS", "25"))
# 套餐并发护栏（Agent Plan 按 Small 套餐并发数 2，避免按 VPS 申请 10 并发被拒）
AGENT_PLAN_MAX_CONCURRENCY = int(os.getenv("AGENT_PLAN_MAX_CONCURRENCY", "2"))
# 视频提交限速（2026-09-13 审计修复）：此前视频侧只有并发信号量、无 RPM 调度，
# 提交突发全靠 429 事后重试；与图片侧 AGENT_PLAN_IMAGE_RPM 同口径
AGENT_PLAN_VIDEO_RPM = int(os.getenv("AGENT_PLAN_VIDEO_RPM", "10"))
# 文生文（分镜/钩子/修复轮）— 默认 doubao-seed-2-1-turbo（2026-09-03 接入）
# 鉴权复用 AGENT_PLAN_API_KEY（与图片/视频同专属 key）
# 端点：POST {base}/chat/completions（OpenAI 兼容）
AGENT_PLAN_TEXT_MODEL = os.getenv("AGENT_PLAN_TEXT_MODEL", "doubao-seed-2-1-turbo")
# AGENT_PLAN_TEXT_RPM 已删（唯一消费者 _text_gen/config.py 自读同 env，双份定义易漂移）
AGENT_PLAN_IMAGE_RPM = int(os.getenv("AGENT_PLAN_IMAGE_RPM", "10"))  # 限速参考
# 思考模式强度：none/minimal/low/medium/high/xhigh/max（none 关闭；与 deepseek 路径同款）
AGENT_PLAN_TEXT_REASONING_EFFORT = os.getenv("AGENT_PLAN_TEXT_REASONING_EFFORT", "none")
# 单次输出 token 上限（0/None = 不传 max_tokens）
AGENT_PLAN_TEXT_MAX_TOKENS: Optional[int] = (
    int(os.getenv("AGENT_PLAN_TEXT_MAX_TOKENS") or "0") or None
)


# ============================================================================
# MiniMax 配置 - image-01 文生图 / Hailuo v1 视频 / MiniMax-H3 v2 视频
# ============================================================================
# 官方文档（国际站 api.minimax.io）:
#   图片生成: POST {base}/v1/image_generation
#   视频生成 v1 (Hailuo 旧模型，便宜): POST {base}/v1/video_generation
#   视频生成 v2 (MiniMax-H3 新模型):  POST {base}/v2/video_generation
# 仅接入 MiniMax 系列模型（image-01 / Hailuo-02 / Hailuo-2.3 / 2.3-Fast /
# T2V-01 / I2V-01 / MiniMax-H3），403 回退不混入其他厂商模型。
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io")
# 文生图/图生图模型（image-01 支持 T2I 与 I2I；image-01-live 仅 I2I 更快）
MINIMAX_T2I_MODEL = os.getenv("MINIMAX_T2I_MODEL", "image-01")
MINIMAX_T2I_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("MINIMAX_T2I_FALLBACK_MODELS", "image-01-live").split(",")
    if m.strip()
]
MINIMAX_IMAGE_RPM = int(os.getenv("MINIMAX_IMAGE_RPM", "10"))  # 限速参考
# 视频 v1 (Hailuo 旧模型): T2V / I2V 分开 + 各自 403 回退（仅 MiniMax 系列）
MINIMAX_T2V_MODEL = os.getenv("MINIMAX_T2V_MODEL", "MiniMax-Hailuo-02")
MINIMAX_T2V_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "MINIMAX_T2V_FALLBACK_MODELS", "MiniMax-Hailuo-2.3,T2V-01-Director,T2V-01"
    ).split(",")
    if m.strip()
]
MINIMAX_I2V_MODEL = os.getenv("MINIMAX_I2V_MODEL", "MiniMax-Hailuo-2.3-Fast")
MINIMAX_I2V_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "MINIMAX_I2V_FALLBACK_MODELS",
        "MiniMax-Hailuo-02,MiniMax-Hailuo-2.3,I2V-01-Director,I2V-01-live,I2V-01",
    ).split(",")
    if m.strip()
]
# 视频 v2 (MiniMax-H3 新模型，贵): 首尾帧/多关键帧/参考图/参考音频
MINIMAX_H3_MODEL = os.getenv("MINIMAX_H3_MODEL", "MiniMax-H3")
# 视频参数（v1 支持 512P/720P/768P/1080P；时长默认 6，768P 可 6 或 10，1080P 仅 6）
# MINIMAX_VIDEO_RESOLUTION **已失效**：provider _resolve_v1_resolution 按 req.size 短边定档
# （step02b 固定 720x1280→768P），仅在 size 解析失败时作回退；step01 时长上限也已按 req.size
# 同源推导（shared/model_caps.active_video_duration_cap）。想改分辨率需改 step02b _make_request 的 size。
MINIMAX_VIDEO_RESOLUTION = os.getenv("MINIMAX_VIDEO_RESOLUTION", "768P")
# 注：MINIMAX_VIDEO_DURATION 已删除（全仓无消费者——provider 用 req.duration or 6，
# 时长规则见 shared/model_caps.MINIMAX_V1_*；时长上限经 active_video_duration_cap）。
MINIMAX_VIDEO_POLL_INTERVAL = int(os.getenv("MINIMAX_VIDEO_POLL_INTERVAL", "15"))
MINIMAX_VIDEO_MAX_POLL_ATTEMPTS = int(os.getenv("MINIMAX_VIDEO_MAX_POLL_ATTEMPTS", "120"))


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
# DashScope 视频子端点（http_dashscope 直连版用；SDK 版走 DASHSCOPE_BASE_URL）
DASHSCOPE_VIDEO_I2V_ENDPOINT = os.getenv(
    "DASHSCOPE_VIDEO_I2V_ENDPOINT",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
)
DASHSCOPE_VIDEO_KF2V_ENDPOINT = os.getenv(
    "DASHSCOPE_VIDEO_KF2V_ENDPOINT",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis",
)
DASHSCOPE_TASK_QUERY_ENDPOINT = os.getenv(
    "DASHSCOPE_TASK_QUERY_ENDPOINT",
    "https://dashscope.aliyuncs.com/api/v1/tasks",
)


# ============================================================================
# DeepSeek (文本生成) 配置
# ============================================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")


# ============================================================================
# TTS 配置（实际生效值见 tts/config.py 的 DASHSCOPE_TTS_RATE/VOLUME）
# ============================================================================


# ============================================================================
# QC 视觉理解渠道（shared/image_qc.py 三层检测用；key 缺失时渠道自动跳过）
# ============================================================================
# ARK 渠道视觉模型（doubao-seed-1-6 视觉 chat；当前账号未开通时 404 自动落
# agnes/dashscope 渠道，见 image_qc._vision_chat）
QC_ARK_MODEL = os.getenv("QC_ARK_MODEL", "doubao-seed-1-6-250615")
# dashscope OpenAI 兼容端点（qwen-vl-max 视觉渠道）
QC_DASHSCOPE_BASE_URL = os.getenv(
    "QC_DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)


# ============================================================================
# 视频生成通用配置
# ============================================================================
# 视频提交并发（唯一默认定义处）：step02b main 与 submit_poll 均从这里读取。
# 注意：当前 .env.content 未设此值时默认 4；不要在别处再写 getenv 兜底（历史三处
# 三默认值 0/8/4 已单源化），0 值会产生 Semaphore(0) 永久死锁（submit_poll 已加 max(1) 防护）。
VIDEO_CONCURRENCY = int(os.getenv("VIDEO_CONCURRENCY", "4"))
VIDEO_MAX_RETRIES = int(os.getenv("VIDEO_MAX_RETRIES", "3"))


# ============================================================================
# 合并配置（step04）
# ============================================================================
MERGE_PRESET = os.getenv("MERGE_PRESET", "medium")
# 成片分辨率（宽x高）。调试期保持 720x1280 省生成额度；上线提到 1080x1920
# 时同步把 BAILIAN_VIDEO_RESOLUTION 改 1080P（视频按秒计费，0.6→1 元/秒）
MERGE_RESOLUTION = os.getenv("MERGE_RESOLUTION", "720x1280")
# 成片编码质量（x264 CRF，越小越清晰、文件越大；18=高质量，20=均衡）
MERGE_CRF = os.getenv("MERGE_CRF", "18")
# 音频模式：native=只保留视频原声（默认）；tts_mix=TTS 对白轨叠加 +
# 视频原声闪避（对视频原声不满意时切换，需先跑 step03 生成 audio/shot_N.mp3）
MERGE_AUDIO_MODE = os.getenv("MERGE_AUDIO_MODE", "native")
# 逐镜响度归一（EBU R128，消除镜头间音量跳变；关闭则退回固定增益）
MERGE_AUDIO_LOUDNORM = os.getenv("MERGE_AUDIO_LOUDNORM", "1")
