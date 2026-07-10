"""TTS 鏁版嵁妯″瀷 鈥?渚涘簲鍟嗘棤鍏?"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TTSRequest:
    """璇煶鍚堟垚璇锋眰锛堜笌渚涘簲鍟嗘棤鍏筹級."""

    text: str             # 寰呰浆鎹㈢殑鏂囨湰
    model: str = ""       # 渚涘簲鍟嗕晶鐨勬ā鍨嬪悕绉?    rate: float = 0.92    # 璇€熺郴鏁帮紝鑼冨洿鍥犳彁渚涘晢鑰屽紓
    volume: int = 50      # 闊抽噺锛岃寖鍥村洜鎻愪緵鍟嗚€屽紓
    format: str = "mp3"   # 杈撳嚭鏍煎紡锛歮p3 / wav / pcm
    sample_rate: int = 16000  # 閲囨牱鐜?

@dataclass
class TTSSuccessResult:
    """鍚堟垚鎴愬姛."""

    success: bool = True
    audio_bytes: bytes = b""
    save_path: Optional[str] = None


@dataclass
class TTSFailureResult:
    """鍚堟垚澶辫触."""

    success: bool = False
    error: str = ""


# Union type
TTSServiceResult = TTSSuccessResult | TTSFailureResult

