"""TTS 数据模型 — 供应商无关。

2026-10-03 计划 §T1：新增 `SpeechUnit v1`（一次合成的**完整身份**）与
`ProviderCapabilities`（provider 的**能力事实**，按官方依据登记）。

三条口径写死在这里，其它模块不得各自解释：

1. **模型 ID 与音色 ID 分开**：`model_id/model_revision` 是「用哪个模型」，
   `speaker`（= 新字段）是「用哪个声线」；旧的 `voice_id` 只作旧请求兼容别名。
   注册表适配不能把 Sambert ID 当 Qwen 模型 ID。
2. **instruction 不拼进发声文本**：它是 provider 的独立参数；不支持 instruction
   的 provider 必须显式拒绝，而不是把它混进 `text` 让模型念出来。
3. **不假装参数生效**：`rate`/`volume`/`instruction` 是否真的进了请求，由
   `ProviderCapabilities` 声明；`params_strict=True`（新路径）时不支持即拒收，
   `params_strict=False`（旧请求兼容）时按能力事实记录 `*_applied=False`。
"""
from dataclasses import dataclass, field
from typing import Optional

#: 声音单元种类（与 `step01_plan_feasibility.VOICE_KIND_*` 同字面量）
KIND_DIALOGUE = "dialogue"
KIND_NARRATION = "narration"
LEGAL_KINDS = (KIND_DIALOGUE, KIND_NARRATION)


@dataclass
class TTSRequest:
    """语音合成请求（与供应商无关）。"""

    text: str = ""              # 待转换的文本
    model: str = ""             # 供应商晶牌的模型名
    rate: float = 0.92          # 语速系数，范围因供应商而异
    volume: int = 50            # 音量，范围因供应商而异
    format: str = "mp3"         # 输出格式：mp3 / wav / pcm
    sample_rate: int = 16000    # 采样率
    voice_id: str = ""          # 音色 ID（#20 修复 2026-08-14：voice_registry 分配的音色
                                # 借此字段传到 provider；此前 TTSRequest 无此字段，qwen3
                                # provider 的 getattr(req, "voice_id", None) 恒为 None）
    # ---- 2026-10-03 §T1/T2a 新增（全部有默认值，旧调用点语义不变） ----
    speaker: str = ""           # 声线 ID（与 model 分开；空 ⇒ 用 voice_id 兼容别名）
    model_revision: str = ""    # 模型修订号（provider 有则填）
    instruction: str = ""       # 风格指令（**不拼进 text**；provider 支持才下发）
    language: str = "zh"        # 语言
    mapping_version: str = ""   # 音色映射版本（identity 用）
    params_strict: bool = False  # True: 不支持 rate/volume/instruction 即拒收（新路径）

    def effective_speaker(self) -> str:
        """生效声线：`speaker` 优先，旧调用点的 `voice_id` 作兼容别名。"""
        return str(self.speaker or self.voice_id or "").strip()


@dataclass(frozen=True)
class ProviderCapabilities:
    """provider 的**能力事实**（不支持的参数必须显式声明，不许假装生效）.

    `evidence` 记录该组数字的官方依据（文档/端点/模型卡）——能力值不能凭印象写，
    也不能把能力上限复制进 `.env` 当配置。
    """

    provider: str
    max_chars_per_call: Optional[int] = None   # 单次请求的正文字符上限（None = 未声明）
    supports_instruction: bool = False
    supports_rate: bool = False
    supports_volume: bool = False
    supports_speaker: bool = False
    legal_speakers: tuple = ()                 # 合法声线白名单（空 = 不校验具体值）
    output_formats: tuple = ("mp3",)
    sample_rates: tuple = ()
    model_id: str = ""
    model_revision: str = ""
    real_model: bool = True                    # False = 桩/本地未运行（不得宣称收益）
    evidence: str = ""

    def describe(self) -> dict:
        return {
            "provider": self.provider,
            "max_chars_per_call": self.max_chars_per_call,
            "supports_instruction": self.supports_instruction,
            "supports_rate": self.supports_rate,
            "supports_volume": self.supports_volume,
            "supports_speaker": self.supports_speaker,
            "legal_speakers": list(self.legal_speakers),
            "output_formats": list(self.output_formats),
            "sample_rates": list(self.sample_rates),
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "real_model": self.real_model,
            "evidence": self.evidence,
        }


#: 能力未知时的**保守**声明（fail-closed）：不声明支持任何可选参数、不声明长度上限
UNKNOWN_CAPABILITIES = ProviderCapabilities(
    provider="unknown", max_chars_per_call=None, supports_instruction=False,
    supports_rate=False, supports_volume=False, supports_speaker=False,
    evidence="未登记（任何未登记 provider 一律按不支持处理）",
)


@dataclass
class SpeechUnit:
    """一次合成的完整身份（计划 §T1 的 `SpeechUnit v1` 字段表）.

    字段与计划逐项对应：scope、script_revision、unit_id、kind、源文本位置与完整
    text、planned_window、voice_profile_id、provider、model_id/model_revision、
    speaker、instruction、language、rate/volume、format/sample_rate、mapping_version。

    `params_strict` / `legacy_voice_signature` 不属于 v1 身份字段，是 T 包内部的
    **接线开关**：新路径严格（不支持即拒收），旧请求兼容路径按能力事实记账。
    """

    unit_id: str = ""
    kind: str = ""
    text: str = ""
    scope: dict = field(default_factory=dict)
    script_revision: str = ""
    source_locator: str = ""            # 源文本位置，如 `shots[3]` / `narration_lines[1]`
    source_span: Optional[tuple] = None  # 源文本内的 code point 半开区间 [start, end)
    planned_window: Optional[tuple] = None  # (start_sec, end_sec)（None = 不做窗口验收）
    voice_profile_id: str = ""
    provider: str = ""
    model_id: str = ""
    model_revision: str = ""
    speaker: str = ""
    instruction: str = ""
    language: str = "zh"
    rate: float = 1.0
    volume: Optional[float] = None
    format: str = "mp3"
    sample_rate: int = 16000
    mapping_version: str = ""
    params_strict: bool = True
    legacy_voice_signature: Optional[dict] = None
    #: 计划里的编号（对白=镜头序号、hook=0；旁白=N 编号）——manifest 的
    #: `shot_index`/`narration_index` 由它给出（消费侧不按数组位置猜身份）。
    source_index: Optional[int] = None
    voice_role: str = ""  # explicit offscreen ownership; absent on legacy units

    def as_request(self, *, text: str, output: str | None = None) -> TTSRequest:
        """按本单元的身份构造请求（**逐段**调用：text 是该段的正文）."""
        return TTSRequest(
            text=text, model=self.model_id, rate=self.rate,
            volume=int(self.volume) if self.volume is not None else 0,
            format=self.format, sample_rate=self.sample_rate,
            voice_id=self.speaker, speaker=self.speaker,
            model_revision=self.model_revision, instruction=self.instruction,
            language=self.language, mapping_version=self.mapping_version,
            params_strict=self.params_strict,
        )


@dataclass
class TTSSuccessResult:
    """合成成功。

    `save_path` 是 provider **实际写出**的路径（可能是 .wav 等与实际格式一致的后缀）——
    消费方必须读它，不能 stat 假定的原后缀。
    """

    success: bool = True
    audio_bytes: bytes = b""
    save_path: Optional[str] = None
    #: 实际交付格式（provider 自报；无法自报时为 ""，由消费侧按真实解码判定）
    actual_format: str = ""
    actual_sample_rate: int = 0
    #: 本段请求的证据（provider 侧记录，供 identity 的分段记录使用）
    applied: dict = field(default_factory=dict)


@dataclass
class TTSFailureResult:
    """合成失败。"""

    success: bool = False
    error: str = ""
    #: 结构化失败码（如 `unsupported_parameter` / `speaker_not_legal` / `not_run`）
    code: str = ""
    #: provider 自报状态（not_run = 无权重/未启用，**不是成功**）
    status: str = ""
    field_path: str = ""


# Union type
TTSServiceResult = TTSSuccessResult | TTSFailureResult
