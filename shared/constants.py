"""shared.constants — 跨模块通用常量（重试次数 / 兜底值），与配置解耦.

背景（硬编码清理 2026-09）：各业务点的重试次数历史上散落为 10 处裸字面量
（1/2/3/4/10 次不等），常量化的目的是**命名 + 单点可查**，不是统一数值——
各处保留原值、原行为。若某处重试需要运行时可调，应升格为对应 config.py 的
env 变量，而不是再加字面量。

本模块只允许 import 标准库，禁止依赖项目内其他模块（避免循环导入）。
"""

# ---- 供应商默认值 ----
# TEXT_PROVIDER 默认值（供应商选择出现于 shared/config.py 全局段与
# _text_gen/config.py 包配置两处，默认值必须同源；main.py 挂载门与
# doubao_hardfix 自动注册钩子也引用此常量）
TEXT_PROVIDER_DEFAULT = "deepseek"

# ---- 分镜 JSON 解析（step01 main，解析失败重试） ----
SCRIPT_PARSE_RETRIES = 3          # range(1, 1 + SCRIPT_PARSE_RETRIES)
SCRIPT_PARSE_RETRY_SLEEP_SEC = 5  # 每次重试前 sleep 秒数

# ---- 视频提交 / 拷帧（step02 submit_poll） ----
VIDEO_SUBMIT_RETRIES = 3   # 提交失败重试：range(1, 1 + VIDEO_SUBMIT_RETRIES)
FRAME_COPY_RETRIES = 10    # 帧图片拷贝重试：range(FRAME_COPY_RETRIES)
# 注：agnes 视频提交重试走 AGNES_VIDEO_SUBMIT_RETRIES env（默认 10），
# 与上面 3 次的差异是历史行为，未强行对齐（见 _video_gen/agnes_provider）。

# ---- 视频 provider 提交重试 ----
AGENT_PLAN_VIDEO_SUBMIT_RETRIES = 4  # range(1, 1 + N)
VOLCENGINE_VIDEO_SUBMIT_RETRIES = 4

# ---- 文本 provider ----
AGENT_PLAN_TEXT_ATTEMPTS = 2  # range(1, 1 + N)

# ---- 图片 ----
HALLUCINATION_DETECT_RETRIES = 3  # 视觉幻觉检测失败重试
BAILIAN_IMAGE_429_RETRIES = 2     # 百炼图片 429 冷却重试
ASSET_GEN_ATTEMPTS = 2            # 场景/道具资产图生成尝试次数：range(1, 1 + N)

# ---- TTS ----
TTS_MAX_RETRIES = 3

# ---- 视频单镜时长兜底（秒） ----
# step01 在 active_video_duration_cap() 返回 0（注册表未命中）时使用的兜底上限。
# 与 model_caps docstring 的「≤10s 引导」、timecode 8s 硬上限是三个不同口径：
# 这里只是"查不到表时不设防"之前的最后闸门，勿与两者混用。
DEFAULT_VIDEO_DURATION_FALLBACK = 12
