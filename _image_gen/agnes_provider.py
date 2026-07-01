"""agnes ai 图片生成 — OpenAI 兼容协议.

POST {AGNES_BASE_URL}{AGNES_IMAGE_ENDPOINT}，OpenAI images generations 格式.
响应解析: 优先 data[0].url（下载），其次 data[0].b64_json（解码）.
403 自动回退到 AGNES_IMAGE_FALLBACK_MODELS 中的下一个模型.
"""

import base64
import os
from typing import Optional

import requests

from .config import config
from .models import ImageGenerationRequest, ImageGenerationResult
from .service import ImageGenerationService


class AgnesImageService(ImageGenerationService):
    """agnes ai (OpenAI 兼容) 图片生成实现.

    403 回退: 主模型返回 403 时，自动切换到 AGNES_IMAGE_FALLBACK_MODELS 中的下一个.
    回退在单次调用级别（与 TTS/文本一致）.
    """

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        self._model_queue: list[str] = [
            config.AGNES_IMAGE_MODEL
        ] + config.AGNES_IMAGE_FALLBACK_MODELS

    def _advance_index(self, idx: int) -> Optional[int]:
        """返回下一个回退模型的下标，无可用模型时返回 None."""
        if idx + 1 >= len(self._model_queue):
            return None
        return idx + 1

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        url = f"{config.AGNES_BASE_URL.rstrip('/')}{config.AGNES_IMAGE_ENDPOINT}"
        idx = 0
        last_error = ""

        while idx < len(self._model_queue):
            model = self._model_queue[idx]
            headers = {
                "Authorization": f"Bearer {config.AGNES_API_KEY}",
                "Content-Type": "application/json",
            }
            payload: dict = {
                "model": model,
                "prompt": req.prompt,
                "n": 1,
                "size": req.size,
            }
            # 部分兼容后端支持 negative_prompt 字段，存在则附带
            if req.negative_prompt:
                payload["negative_prompt"] = req.negative_prompt

            print(f"  [镜头{req.shot_index}] 正在生成首帧图 [{model}]...")
            try:
                res = requests.post(url, json=payload, headers=headers, timeout=180)

                # ---- 200: 解析图片 ----
                if res.status_code == 200:
                    data = res.json().get("data", [])
                    if not data:
                        last_error = f"Agnes 图片返回无 data: {res.text[:300]}"
                        break
                    item = data[0]
                    img_url = item.get("url")
                    b64 = item.get("b64_json")

                    try:
                        if img_url:
                            img_bytes = requests.get(img_url, timeout=180).content
                        elif b64:
                            img_bytes = base64.b64decode(b64)
                        else:
                            last_error = "Agnes 图片响应缺少 url/b64_json"
                            break

                        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
                        save_path = os.path.join(
                            config.OUTPUT_DIR, f"shot_{req.shot_index}.png"
                        )
                        with open(save_path, "wb") as f:
                            f.write(img_bytes)

                        size_kb = len(img_bytes) / 1024
                        if idx > 0:
                            print(f"  [INFO] 图片已回退到模型: {model}")
                        print(f"  [镜头{req.shot_index}] [OK] 首帧已保存 -> "
                              f"{save_path} ({size_kb:.1f} KB)")
                        return ImageGenerationResult(success=True, save_path=save_path)
                    except Exception as e:
                        last_error = f"图片下载/保存失败: {e}"
                        break

                # ---- 403: 额度耗尽或无权限，尝试回退 ----
                if res.status_code == 403:
                    nxt = self._advance_index(idx)
                    if nxt is not None:
                        print(f"  [镜头{req.shot_index}] ⚠ 图片模型 {model} 返回 403"
                              f"（额度耗尽/无权限），自动切换到 {self._model_queue[nxt]}")
                        idx = nxt
                        continue
                    last_error = (f"所有图片模型均返回 403，已无可用回退模型。"
                                  f"尝试过: {', '.join(self._model_queue)}")
                    break

                # ---- 其他错误 ----
                last_error = f"Agnes 图片失败 ({res.status_code}): {res.text[:300]}"
                break

            except Exception as e:
                last_error = f"Agnes 图片异常: {e}"
                break

        return ImageGenerationResult(success=False, error=last_error)
