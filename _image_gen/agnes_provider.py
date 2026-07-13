"""agnes ai 图片生成 — OpenAI 兼容协议.

POST {AGNES_BASE_URL}/v1/images/generations，OpenAI images generations 格式.
响应解析: 优先 data[0].url（下载），其次 data[0].b64_json（解码）.

403 自动回退到 AGNES_IMAGE_FALLBACK_MODELS 中的下一个模型.
5xx/连接错误自动重试（AGNES_IMAGE_SUBMIT_RETRIES 次）.

模型: agnes-image-2.1-flash
- 文生图: model + prompt + size
- 图生图: extra_body.image 传入输入图像 URL/Base64
- response_format 必须放在 extra_body 中，禁止放顶层
- 不需要 tags: ["img2img"]
"""

import base64
import os
import time
from typing import Optional

import requests

from .config import config
from .models import ImageGenerationRequest, ImageGenerationResult
from .service import ImageGenerationService
from shared.content_policy_fix import auto_fix_prompt


class AgnesImageService(ImageGenerationService):
    """agnes ai (OpenAI 兼容) 图片生成实现.

    403 回退: 主模型返回 403 时，自动切换到 AGNES_IMAGE_FALLBACK_MODELS 中的下一个.
    回退在单次调用级别（与 TTS/文本一致）.
    """

    _SUBMIT_TIMEOUT = int(os.getenv("AGNES_IMAGE_SUBMIT_TIMEOUT", "120"))  # 默认 120s

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        self._model_queue: list[str] = [
            config.AGNES_IMAGE_MODEL
        ] + config.AGNES_IMAGE_FALLBACK_MODELS
        # 多 Key 切换（最多8个）
        self._api_keys = [config.AGNES_API_KEY]
        if config.AGNES_API_KEY_2:
            self._api_keys.append(config.AGNES_API_KEY_2)
        if config.AGNES_API_KEY_3:
            self._api_keys.append(config.AGNES_API_KEY_3)
        if config.AGNES_API_KEY_4:
            self._api_keys.append(config.AGNES_API_KEY_4)
        if config.AGNES_API_KEY_5:
            self._api_keys.append(config.AGNES_API_KEY_5)
        if config.AGNES_API_KEY_6:
            self._api_keys.append(config.AGNES_API_KEY_6)
        if config.AGNES_API_KEY_7:
            self._api_keys.append(config.AGNES_API_KEY_7)
        if config.AGNES_API_KEY_8:
            self._api_keys.append(config.AGNES_API_KEY_8)
        self._current_key_index: int = 0

    @property
    def _api_key(self) -> str:
        return self._api_keys[self._current_key_index]

    def _switch_to_backup_key(self) -> bool:
        """切换到备用 API Key. 返回是否切换成功."""
        if self._current_key_index + 1 < len(self._api_keys):
            self._current_key_index += 1
            return True
        return False

    def _advance_index(self, idx: int) -> Optional[int]:
        """返回下一个回退模型的下标，无可用模型时返回 None."""
        if idx + 1 >= len(self._model_queue):
            return None
        return idx + 1

    # ------------------------------------------------------------------
    # 图片编码（图生图用）
    # ------------------------------------------------------------------
    @staticmethod
    def _encode_image(path: Optional[str]) -> Optional[str]:
        """读取本地图片，返回 data URL (base64).

        Python base64.b64encode 输出始终是 4 的倍数（自动补齐 =），
        直接使用标准输出，不再做额外 strip/re-pad 处理——这在某些场景下
        反而会引入 padding 不匹配的问题.
        """
        if not path:
            return None
        try:
            import mimetypes
            p = os.path.abspath(path)
            if not os.path.isfile(p):
                return None
            mime, _ = mimetypes.guess_type(p)
            mime = mime or "image/png"
            with open(p, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{b64}"
        except Exception:
            return None

    def generate(self, req: ImageGenerationRequest) -> ImageGenerationResult:
        url = f"{config.AGNES_BASE_URL.rstrip('/')}/v1/images/generations"
        idx = 0
        last_error = ""

        # 图生图：编码参考图
        ref_data_url = None
        if req.reference_image_path:
            ref_data_url = self._encode_image(req.reference_image_path)
            if ref_data_url:
                pass  # 静默
            else:
                print(f"  [镜头{req.shot_index}] 参考图读取失败，降级为 text-to-image")

        while idx < len(self._model_queue):
            model = self._model_queue[idx]
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }

            # 基础 payload（使用档位式 size + ratio，文档推荐方式）
            payload: dict = {
                "model": model,
                "prompt": req.prompt,
                "size": req.size,  # 如 "2K"
                "ratio": req.ratio,  # 如 "9:16"
            }

            # 添加 negative_prompt（文档支持）
            if req.negative_prompt:
                payload["negative_prompt"] = req.negative_prompt

            # 添加 seed（文档支持，用于可复现结果）
            if req.seed is not None:
                payload["seed"] = req.seed

            # extra_body: response_format 必须放这里（禁止放顶层）
            extra: dict = {
                "response_format": "url",
            }

            # 图生图: 在 extra_body.image 传入输入图像
            if ref_data_url:
                extra["image"] = [ref_data_url]
                b64_body = ref_data_url.split(",", 1)[-1]
                print(f"  [镜头{req.shot_index}] 参考图编码完成 "
                      f"(len={len(b64_body)}, mod4={len(b64_body)%4})")

            payload["extra_body"] = extra

            print(f"  [镜头{req.shot_index}] 正在生成首帧 [{model}]...")
            _submit_retry = 0
            try:
                while True:
                    try:
                        res = requests.post(url, json=payload, headers=headers,
                                          timeout=self._SUBMIT_TIMEOUT)
                        break
                    except Exception as e:
                        _submit_retry += 1
                        if _submit_retry <= config.AGNES_IMAGE_SUBMIT_RETRIES:
                            print(f"  [镜头{req.shot_index}] 提交异常: {e}，"
                                  f"{config.AGNES_IMAGE_SUBMIT_RETRY_DELAY}s 后重试 "
                                  f"({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                            time.sleep(config.AGNES_IMAGE_SUBMIT_RETRY_DELAY)
                        else:
                            last_error = f"Agnes 图片异常（重试{_submit_retry}次）: {e}"
                            break
                if last_error:
                    idx = len(self._model_queue)
                    break

                # ---- 422: 内容违规，自動修復並重試 ----
                if res.status_code == 422:
                    error_text = res.text
                    fixed_prompt = auto_fix_prompt(req.prompt, error_text)
                    if fixed_prompt and _submit_retry < config.AGNES_IMAGE_SUBMIT_RETRIES:
                        _submit_retry += 1
                        print(f"  [鏡頭{req.shot_index}] 內容違規 [422]，嘗試修復 prompt "
                              f"({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                        req.prompt = fixed_prompt
                        payload["prompt"] = fixed_prompt
                        time.sleep(2)  # 短暫等待後重試
                        continue
                    last_error = f"Agnes 內容違規 [422]（已重試{_submit_retry}次仍失敗）: {error_text[:300]}"
                    break

                # ---- 401/402: 认证失败/余额不足，停止重试 ----
                if res.status_code in (401, 402):
                    last_error = f"Agnes 错误 [{res.status_code}]（停止重试）: {res.text[:300]}"
                    break

                # ---- 408/499/502/503/504/520/522/524: 临时错误，等待后重试 ----
                if res.status_code in (408, 499, 502, 503, 504, 520, 522, 524):
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_IMAGE_SUBMIT_RETRIES:
                        print(f"  [镜头{req.shot_index}] 临时错误 {res.status_code}，"
                              f"{config.AGNES_IMAGE_SUBMIT_RETRY_DELAY}s 后重试 "
                              f"({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                        time.sleep(config.AGNES_IMAGE_SUBMIT_RETRY_DELAY)
                        continue
                    last_error = f"Agnes 图片失败 [{res.status_code}] (重试{_submit_retry}次): {res.text[:300]}"
                    idx = len(self._model_queue)
                    break

                # ---- 5xx: 服务端暂时不可用，等待后重试 ----
                if 500 <= res.status_code < 600:
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_IMAGE_SUBMIT_RETRIES:
                        print(f"  [镜头{req.shot_index}] 服务端 {res.status_code}，"
                              f"{config.AGNES_IMAGE_SUBMIT_RETRY_DELAY}s 后重试 "
                              f"({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                        time.sleep(config.AGNES_IMAGE_SUBMIT_RETRY_DELAY)
                        continue
                    last_error = f"Agnes 图片失败 [{res.status_code}] (重试{_submit_retry}次): {res.text[:300]}"
                    idx = len(self._model_queue)
                    break

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

                # ---- 401/402: 认证失败或支付问题，优先切换 API Key ----
                if res.status_code in (401, 402):
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_IMAGE_SUBMIT_RETRIES:
                        if self._switch_to_backup_key():
                            print(f"  [镜头{req.shot_index}] HTTP {res.status_code}，"
                                  f"切换到 API Key {self._current_key_index + 1}/{len(self._api_keys)} 并重试")
                        else:
                            last_error = (f"Agnes API Key 无效/过期 [HTTP {res.status_code}]，"
                                          f"所有 Key 均已尝试: {res.text[:300]}")
                            break
                        continue
                    last_error = (f"Agnes 认证失败 [HTTP {res.status_code}] "
                                  f"(重试{_submit_retry}次): {res.text[:300]}")
                    break

                # ---- 429: 速率限制，优先切换 API Key（不同 key 限速独立）----
                if res.status_code == 429:
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_IMAGE_SUBMIT_RETRIES:
                        if self._switch_to_backup_key():
                            print(f"  [镜头{req.shot_index}] 速率限制 429，"
                                  f"切换到 API Key {self._current_key_index + 1}/{len(self._api_keys)} 并重试")
                        else:
                            delay = config.AGNES_IMAGE_SUBMIT_RETRY_DELAY + 30
                            print(f"  [镜头{req.shot_index}] 速率限制 429，所有 Key 已用完，"
                                  f"等待 {delay}s 后重试 ({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                            time.sleep(delay)
                        continue
                    last_error = (f"Agnes 图片速率限制 [429] "
                                  f"(重试{_submit_retry}次): {res.text[:300]}")
                    break
                # ---- 403: 额度耗尽或无权限，尝试回退 ----
                if res.status_code == 403:
                    # 先尝试切换备用 API Key
                    if self._switch_to_backup_key():
                        continue
                    # Key 切换失败再尝试模型回退
                    nxt = self._advance_index(idx)
                    if nxt is not None:
                        print(f"  [镜头{req.shot_index}] [!] 图片模型 {model} 返回 403"
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
