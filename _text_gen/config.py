"""文本生成配置 — 从 .env.content（内容参数）+ .env.keys（API Key）读取参数.

供应商可切换，所有参数均可通过 .env.content 覆盖.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env.content")
load_dotenv(_PROJECT_ROOT / ".env.keys")

from shared.constants import TEXT_PROVIDER_DEFAULT  # 默认值单源 shared/constants.py



class TextGenCfg:
    """文本生成配置（所有参数均可通过 .env.content 覆盖）."""

    # ---- 供应商选择 ----
    # dashscope → 阿里云百炼 DashScope (Qwen)
    # deepseek  → DeepSeek (OpenAI 兼容接口) [默认]
    # agnes     → agnes ai (OpenAI 兼容协议)
    PROVIDER: str = os.getenv("TEXT_PROVIDER", TEXT_PROVIDER_DEFAULT)

    # ---- DashScope (百炼) ----
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    DASHSCOPE_BASE_URL: str = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
    )
    DASHSCOPE_TEXT_MODEL: str = os.getenv("DASHSCOPE_TEXT_MODEL", "qwen-plus")

    # ---- DeepSeek ----
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    # Stateless official API supports SSE, not background/retrieve jobs.
    DEEPSEEK_STREAM: bool = os.getenv("DEEPSEEK_STREAM", "false").lower() in ("1", "true", "yes")
    # 思考模式强度 (reasoning.effort): none/minimal/low/medium/high/xhigh/max
    # （none 关闭思考；minimal/low=低强度；medium/high/xhigh=高强度；max=最强）。
    # 同时作用于 Responses API（v4-flash/v4-pro）与 reasoner 老模型 chat 回退路径。
    # 注意：思考模式下 temperature 被 API 忽略；max_output_tokens 包含思维链 token。
    # 默认 high = 思考开启（高）——生产在 .env.content 显式设为 none 保持旧行为，
    # 由 tools/bench_reasoning.py 实测后决定是否切换。
    DEEPSEEK_REASONING_EFFORT: str = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")

    # DeepSeek 单次响应输出 token 上限（可选）。
    # 不给值 = 不传 max_output_tokens 给 API，由 DeepSeek 服务端默认决定。
    # 有值 = 按值传给 API（max_output_tokens 包含思维链 token——思考强度越高越容易截断）。
    # 推荐分镜生成场景：40000-50000（含思维链余量）；钩子：8000；修复轮：50000。
    # 兼容 .env 中 `KEY=`（空字符串）和注释掉（None）两种"不给值"写法。
    DEEPSEEK_MAX_TOKENS: Optional[int] = (
        int(os.getenv("DEEPSEEK_MAX_TOKENS") or "0") or None
    )

    # 列表型「预提取」调用（scene_objects / characters / scene_assets / props）的
    # 输出 token 上限（v6.62.6，09-09）。
    # 语义是**给重复退化封顶**，不是「给足余量」：09-08 实测这批调用正常输出
    # 596-1039 tokens，而模型偶发进入重复退化时会把上限当目标一路写到顶
    # （20000 tokens / 3m49s / 整份作废）。6000 ≈ 实测需求 6-10x 余量。
    # 供应商无关（不是模型能力上限，故不进 shared/model_caps.py 注册表），
    # 但**可调**——章节人物/场景/道具特别多导致正常输出逼近上限时，
    # 在 .env.content 调高 TEXT_EXTRACT_MAX_TOKENS 即可，不必改代码。
    TEXT_EXTRACT_MAX_TOKENS: int = int(os.getenv("TEXT_EXTRACT_MAX_TOKENS") or "6000")

    # 章节场景分析同样按操作配置上限，包含推理和正文。
    TEXT_SCENE_ANALYSIS_MAX_TOKENS: int = int(os.getenv("TEXT_SCENE_ANALYSIS_MAX_TOKENS") or "4000")

    # 三轴内容核验的操作上限；推理通道此上限包含思考 token。
    # 保持旧缺省，允许有效配置给较大的计划保留完整结果空间。
    CONTENT_VERIFY_MAX_TOKENS: int = int(os.getenv("CONTENT_VERIFY_MAX_TOKENS") or "4000")

    # 大纲生成/旧修订的操作上限，不覆盖供应商模型能力。
    TEXT_OUTLINE_MAX_TOKENS: int = int(os.getenv("TEXT_OUTLINE_MAX_TOKENS") or "4000")

    # ---- agnes (OpenAI 兼容) ----
    AGNES_API_KEY: str = os.getenv("AGNES_API_KEY", "")
    AGNES_API_KEY_2: str = os.getenv("AGNES_API_KEY_2", "")  # 备用 Key
    AGNES_API_KEY_3: str = os.getenv("AGNES_API_KEY_3", "")  # 第三个 Key
    AGNES_API_KEY_4: str = os.getenv("AGNES_API_KEY_4", "")  # 第四个 Key
    AGNES_API_KEY_5: str = os.getenv("AGNES_API_KEY_5", "")  # 第五个 Key
    AGNES_API_KEY_6: str = os.getenv("AGNES_API_KEY_6", "")  # 第六个 Key
    AGNES_API_KEY_7: str = os.getenv("AGNES_API_KEY_7", "")  # 第七个 Key
    AGNES_API_KEY_8: str = os.getenv("AGNES_API_KEY_8", "")  # 第八个 Key
    AGNES_BASE_URL: str = os.getenv("AGNES_BASE_URL", "")
    AGNES_TEXT_MODEL: str = os.getenv("AGNES_TEXT_MODEL", "agnes-2.0-flash")
    # 403 回退模型列表（逗号分隔）
    AGNES_TEXT_FALLBACK_MODELS: list[str] = [
        m.strip()
        for m in os.getenv("AGNES_TEXT_FALLBACK_MODELS", "").split(",")
        if m.strip()
    ]
    AGNES_TEXT_ENDPOINT: str = os.getenv("AGNES_TEXT_ENDPOINT", "/v1/chat/completions")
    AGNES_TEXT_ENABLE_THINKING: bool = os.getenv("AGNES_TEXT_ENABLE_THINKING", "1") == "1"
    # 提交重试: 5xx/连接错误时的最大重试次数与间隔（秒）
    AGNES_TEXT_SUBMIT_RETRIES: int = int(os.getenv("AGNES_TEXT_SUBMIT_RETRIES", "3"))
    AGNES_TEXT_SUBMIT_RETRY_DELAY: int = int(os.getenv("AGNES_TEXT_SUBMIT_RETRY_DELAY", "30"))

    # ---- 火山方舟 Agent Plan（doubao-seed-2-1-turbo 文本通道，2026-09-03 接入）----
    # 鉴权复用 .env.keys 中 AGENT_PLAN_API_KEY（与图片/视频同专属 key，不通用）
    # 端点：POST {AGENT_PLAN_TEXT_BASE_URL}/chat/completions（OpenAI 兼容）
    AGENT_PLAN_API_KEY: str = os.getenv("AGENT_PLAN_API_KEY", "")
    AGENT_PLAN_BASE_URL: str = os.getenv(
        "AGENT_PLAN_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )
    #: **文本通道专用**端点：留空 ⇒ 回落到共享的 `AGENT_PLAN_BASE_URL`。
    #: 2026-10-02（用户决定）：方舟 Agent Plan 订阅到期，真实 step01 跑批改用
    #: opencode go 网关（`https://opencode.ai/zen/go/v1`）；而**图片通道仍是方舟**
    #: （`IMAGE_PROVIDER=agent_plan`，见 `shared/config.py` 的同名端点）——两者共用
    #: 一个 `AGENT_PLAN_BASE_URL` 会把图片也一起换掉，所以文本侧单开一个名字。
    AGENT_PLAN_TEXT_BASE_URL: str = (
        os.getenv("AGENT_PLAN_TEXT_BASE_URL", "").strip() or AGENT_PLAN_BASE_URL
    )
    AGENT_PLAN_TEXT_MODEL: str = os.getenv(
        "AGENT_PLAN_TEXT_MODEL", "doubao-seed-2-1-turbo"
    )
    AGENT_PLAN_TEXT_RPM: int = int(os.getenv("AGENT_PLAN_TEXT_RPM", "10"))
    # 网关路由头（`x-opencode-session`）。opencode go 网关**要求**它（缺失直接
    # 400 MissingSessionID，2026-10-02 实测）；火山方舟不需要，但多一个未知头无影响。
    # 留空 ⇒ provider 自动生成**进程内稳定**的会话 id（文档只要求"每次会话稳定"，
    # 不限格式）——见 `agent_plan_provider._AUTO_SESSION_ID`。
    AGENT_PLAN_SESSION_ID: str = os.getenv("AGENT_PLAN_SESSION_ID", "")
    AGENT_PLAN_TEXT_REASONING_EFFORT: str = os.getenv(
        "AGENT_PLAN_TEXT_REASONING_EFFORT", "none"
    )
    # 0=不传 → 走 agent_plan_provider.py 的 16384 fallback
    # （不要走 65536 —— 实测 70k input + 65536 output 容量下生成 3-5 分钟慢章）
    # 默认 None（不传），显式设 AGENT_PLAN_TEXT_MAX_TOKENS 才传值。
    AGENT_PLAN_TEXT_MAX_TOKENS: Optional[int] = (
        int(os.getenv("AGENT_PLAN_TEXT_MAX_TOKENS") or "0") or None
    )

    # provider → 该 provider 的默认文本模型属性名（v6.68 per-model prompt 剖面用：
    # model=None 的调用点（GAP 重生成等）按默认模型解析剖面，保证 prompt 与
    # 实际提交的模型一致）
    _PROVIDER_DEFAULT_MODEL_KEYS = {
        "dashscope": "DASHSCOPE_TEXT_MODEL",
        "deepseek": "DEEPSEEK_MODEL",
        "agnes": "AGNES_TEXT_MODEL",
        "agent_plan": "AGENT_PLAN_TEXT_MODEL",
    }

    @classmethod
    def default_text_model(cls) -> str:
        """当前 provider 的默认文本模型 ID；未注册 provider 返回空串."""
        _key = cls._PROVIDER_DEFAULT_MODEL_KEYS.get(cls.PROVIDER, "")
        return getattr(cls, _key, "") if _key else ""

    @classmethod
    def validate(cls) -> None:
        """验证当前供应商所需配置是否完整."""
        if cls.PROVIDER == "dashscope" and not cls.DASHSCOPE_API_KEY:
            raise RuntimeError("TEXT_PROVIDER=dashscope 需要 DASHSCOPE_API_KEY")
        if cls.PROVIDER == "deepseek" and not cls.DEEPSEEK_API_KEY:
            raise RuntimeError("TEXT_PROVIDER=deepseek 需要 DEEPSEEK_API_KEY")
        if cls.PROVIDER == "agent_plan" and not cls.AGENT_PLAN_API_KEY:
            raise RuntimeError(
                "TEXT_PROVIDER=agent_plan 需要 AGENT_PLAN_API_KEY（.env.keys 中设置，"
                "Agent Plan 专属 key，与 VOLCENGINE_API_KEY 不通用）"
            )
        if cls.PROVIDER == "agnes":
            # M7 修复 2026-08-14：补 BASE_URL 校验（此前只查 Key，URL 为空时
            # 请求打到相对路径/异常，错误信息难定位）。
            # #25 修复 2026-08-14：backup keys 口径与 _image_gen/config.py 对齐——
            # 配置了任一 AGNES_API_KEY_2..8 也算有效，不强制必须有主 Key。
            has_key = bool(cls.AGNES_API_KEY) or any(
                getattr(cls, f"AGNES_API_KEY_{i}", "") for i in range(2, 9)
            )
            if not has_key:
                raise RuntimeError("TEXT_PROVIDER=agnes 需要至少配置一个 AGNES_API_KEY")
            if not cls.AGNES_BASE_URL:
                raise RuntimeError("TEXT_PROVIDER=agnes 需要 AGNES_BASE_URL")


# 单例
config = TextGenCfg()
