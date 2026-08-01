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
from shared.error_fmt import parse_retry_after
from shared.key_scheduler import KeyScheduler


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
        # 多 Key 切换（直接读取环境变量，灵活支持任意数量）
        self._api_keys = []
        for i in range(1, 21):  # 支持最多 20 个 key
            key = os.environ.get(f"AGNES_API_KEY_{i}" if i > 1 else "AGNES_API_KEY", "")
            if key:
                self._api_keys.append(key)
        if not self._api_keys:
            raise ValueError("没有配置任何 AGNES_API_KEY")
        self._current_key_index: int = 0

        # 多 key 冷却调度：图片免费档约 1 次/20s/key，留 1s 余量
        # N 个 key 轮询提交，吞吐提升 N 倍（原全局 21s 串行闸已移除）
        self._key_scheduler = KeyScheduler(
            len(self._api_keys),
            cooldown=float(os.getenv("IMAGE_GEN_INTERVAL", "21")),
            name="image-keys",
        )

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

        # 图生图模式：完全静默（不写日志），纯文生图模式正常
        _is_img2img = bool(req.reference_image_path or req.reference_image_paths)

        # 日志辅助
        if _is_img2img:
            def _verbose_write(msg):
                pass  # 图生图完全静默
        else:
            try:
                from shared.tee_logger import get_logger
                from shared.paths import chapter_dir
                __vl = get_logger(chapter_dir() / "step02.log", console_level="INFO")
                def _verbose_write(msg):
                    __vl.verbose(msg)
            except Exception:
                def _verbose_write(msg):
                    print(msg)

        _403_count = 0  # 连续 403 计数：轮完一圈可用 key 仍 403 则回退模型

        # 图生图：编码参考图（支持多张，同框多角色各自传入肖像）
        ref_data_urls = []
        for ref_path in req.reference_image_paths:
            url = self._encode_image(ref_path)
            if url:
                ref_data_urls.append(url)
            else:
                _verbose_write(f"  [镜头{req.shot_index}] 参考图读取失败 {ref_path}")
        if not ref_data_urls and req.reference_image_path:
            # 向后兼容：仅设置了单张 reference_image_path
            url = self._encode_image(req.reference_image_path)
            if url:
                ref_data_urls.append(url)
            else:
                _verbose_write(f"  [镜头{req.shot_index}] 参考图读取失败，降级为 text-to-image")

        while idx < len(self._model_queue):
            model = self._model_queue[idx]
            # 冷却调度：取最早可用的 key（阻塞直到有 key 出冷却）
            key_idx = self._key_scheduler.acquire()
            self._current_key_index = key_idx
            headers = {
                "Authorization": f"Bearer {self._api_keys[key_idx]}",
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

            # 添加 num_inference_steps（文档支持，提升生成质量）
            if req.num_inference_steps is not None:
                payload["num_inference_steps"] = req.num_inference_steps

            # extra_body: response_format 必须放这里（禁止放顶层）
            extra: dict = {
                "response_format": "url",
            }

            # 图生图: 在 extra_body.image 传入输入图像（支持多张）
            if ref_data_urls:
                extra["image"] = ref_data_urls

            payload["extra_body"] = extra

            frame_label = req.frame_desc if req.frame_desc else "首帧"
            _verbose_write(f"  [镜头{req.shot_index}] 正在生成{frame_label} [{model}]...")
            _submit_retry = 0
            # 完整请求日志（不含 base64 内容）
            import json
            _log_payload = {k: v for k, v in payload.items()}
            if "extra_body" in _log_payload and isinstance(_log_payload.get("extra_body"), dict):
                _eb = _log_payload["extra_body"]
                if "image" in _eb and _eb["image"]:
                    if isinstance(_eb["image"], list):
                        _eb["image"] = [f"[base64 data, len={len(x)}]" if isinstance(x, str) and x.startswith("data:") else x for x in _eb["image"]]
                    else:
                        _eb["image"] = f"[base64 data, len={len(_eb['image'])}]" if isinstance(_eb["image"], str) and _eb["image"].startswith("data:") else _eb["image"]
            _verbose_write(f"[DEBUG] image_submit: POST {url}")
            _verbose_write(f"[DEBUG] image_submit: headers={json.dumps({k: v if k != 'Authorization' else f'Bearer ***{v[-8:]}' for k, v in headers.items()}, ensure_ascii=False)}")
            _verbose_write(f"[DEBUG] image_submit: payload={json.dumps(_log_payload, ensure_ascii=False, indent=2)}")

            try:
                while True:
                    try:
                        res = requests.post(url, json=payload, headers=headers,
                                          timeout=self._SUBMIT_TIMEOUT)
                        break
                    except Exception as e:
                        _submit_retry += 1
                        if _submit_retry <= config.AGNES_IMAGE_SUBMIT_RETRIES:
                            _verbose_write(f"  [镜头{req.shot_index}] 提交异常: {e}，"
                                  f"{config.AGNES_IMAGE_SUBMIT_RETRY_DELAY}s 后重试 "
                                  f"({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                            time.sleep(config.AGNES_IMAGE_SUBMIT_RETRY_DELAY)
                        else:
                            self._key_scheduler.penalize(key_idx, 60)
                            _verbose_write(f"  [镜头{req.shot_index}] Key {key_idx + 1} 超时耗尽，"
                                  f"冷却 60s，调度下一个可用 Key")
                            if self._key_scheduler.n_active() > 1:
                                _submit_retry = 0
                                key_idx = self._key_scheduler.acquire()
                                self._current_key_index = key_idx
                                continue
                            last_error = f"Agnes 图片异常（重试{_submit_retry}次）: {e}"
                            break
                if last_error:
                    idx = len(self._model_queue)
                    break

                # 记录响应日志
                _verbose_write(f"[DEBUG] image_submit response: status={res.status_code}, body={res.text[:500]}")

                # ---- 422: 内容违规，自動修復並重試 ----
                if res.status_code == 422:
                    error_text = res.text
                    fixed_prompt = auto_fix_prompt(req.prompt, error_text)
                    if fixed_prompt and _submit_retry < config.AGNES_IMAGE_SUBMIT_RETRIES:
                        _submit_retry += 1
                        _verbose_write(f"  [鏡頭{req.shot_index}] 內容違規 [422]，嘗試修復 prompt "
                              f"({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                        req.prompt = fixed_prompt
                        payload["prompt"] = fixed_prompt
                        time.sleep(2)  # 短暫等待後重試
                        continue
                    last_error = f"Agnes 內容違規 [422]（已重試{_submit_retry}次仍失敗）: {error_text[:300]}"
                    break

                # ---- 401/402: 认证失败/欠费，拉黑该 Key 并调度下一个 ----
                if res.status_code in (401, 402):
                    self._key_scheduler.disable(key_idx)
                    if self._key_scheduler.n_active() > 0:
                        _verbose_write(f"  [镜头{req.shot_index}] HTTP {res.status_code}，"
                              f"Key {key_idx + 1} 已拉黑，调度下一个可用 Key")
                        continue
                    last_error = (f"Agnes API Key 全部无效/欠费 "
                                  f"[{res.status_code}]: {res.text[:300]}")
                    break

                # ---- 408/499/502/503/504/520/522/524: 临时错误，立即切换key ----
                if res.status_code in (408, 499, 502, 503, 504, 520, 522, 524):
                    _verbose_write(f"  [镜头{req.shot_index}] 临时错误 {res.status_code}，切key重试")
                    self._key_scheduler.penalize(key_idx, 5)  # 短冷却5秒
                    if self._key_scheduler.n_active() > 0:
                        time.sleep(2)  # 等待 2s 再切 key，避免秒级打穿所有 key
                        key_idx = self._key_scheduler.acquire()
                        self._current_key_index = key_idx
                        continue
                    # 没有其他key，等3秒后本key重试
                    time.sleep(3)
                    continue

                # ---- 5xx: 服务端错误，立即切换key ----
                if 500 <= res.status_code < 600:
                    _verbose_write(f"  [镜头{req.shot_index}] 服务端 {res.status_code}，切key重试")
                    self._key_scheduler.penalize(key_idx, 5)
                    if self._key_scheduler.n_active() > 0:
                        time.sleep(2)  # 等待 2s 再切 key，避免秒级打穿所有 key
                        key_idx = self._key_scheduler.acquire()
                        self._current_key_index = key_idx
                        continue
                    time.sleep(3)
                    continue

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
                            _img_resp = requests.get(img_url, timeout=180)
                            _img_resp.raise_for_status()
                            img_bytes = _img_resp.content
                            if len(img_bytes) < 5 * 1024:
                                raise RuntimeError(f"下载内容过小 ({len(img_bytes)} bytes)，可能是错误页面")
                        elif b64:
                            img_bytes = base64.b64decode(b64)
                        else:
                            last_error = "Agnes 图片响应缺少 url/b64_json"
                            break

                        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
                        # 首帧/尾帧用前缀区分（避免尾帧覆盖首帧的 shot_N.png）
                        frame_label = req.frame_desc if req.frame_desc else "首帧"
                        fname_prefix = {
                            "首帧": "ff",
                            "尾帧": "lf",
                        }.get(frame_label, "kf")
                        save_path = os.path.join(
                            config.OUTPUT_DIR, f"shot_{req.shot_index}_{fname_prefix}.png"
                        )
                        with open(save_path, "wb") as f:
                            f.write(img_bytes)

                        size_kb = len(img_bytes) / 1024
                        if idx > 0:
                            _verbose_write(f"  [INFO] 图片已回退到模型: {model}")
                        _verbose_write(f"  [镜头{req.shot_index}] [OK] {frame_label}已保存 -> "
                              f"{save_path} ({size_kb:.1f} KB)")

                        # 返回结果（含图片 URL，供幻觉检测使用）
                        result = ImageGenerationResult(
                            success=True,
                            save_path=save_path,
                            image_url=img_url,  # 原始 URL
                        )

                        # 图片幻觉检测（可选，默认开启）
                        if req.enable_hallucination_detection and img_url:
                            try:
                                from shared.image_hallucination_detector import (
                                    detect_image_hallucination,
                                )
                                # 需要公开可访问的 URL，Agnes 返回的 URL 通常是可下载的
                                _verbose_write(f"  [镜头{req.shot_index}] 正在进行幻觉检测...")
                                detection = detect_image_hallucination(
                                    image_url=img_url,
                                    expected_objects=req.expected_objects,
                                    reference_image_url=None,  # 可后续添加
                                )
                                result.hallucination_detected = detection.has_hallucination
                                if detection.issues:
                                    import json
                                    issues_data = [
                                        {"category": i.category, "description": i.description, "severity": i.severity}
                                        for i in detection.issues
                                    ]
                                    result.hallucination_issues = json.dumps(issues_data, ensure_ascii=False)

                                    # 保存幻觉检测结果到 JSON 文件（供视频生成时过滤）
                                    hallucination_file = save_path.replace(".png", "_hallucination.json")
                                    with open(hallucination_file, "w", encoding="utf-8") as f:
                                        json.dump({
                                            "has_hallucination": detection.has_hallucination,
                                            "issues": issues_data,
                                        }, f, ensure_ascii=False, indent=2)

                                if detection.has_hallucination:
                                    _verbose_write(f"  [镜头{req.shot_index}] [WARN] 检测到幻觉: {detection.issues}")
                                else:
                                    _verbose_write(f"  [镜头{req.shot_index}] 幻觉检测通过")
                            except Exception as e:
                                _verbose_write(f"  [镜头{req.shot_index}] 幻觉检测跳过: {e}")

                        return result
                    except Exception as e:
                        last_error = f"图片下载/保存失败: {e}"
                        break

                # ---- 429: 速率限制，按服务端剩余时间冷却该 Key，调度下一个 ----
                if res.status_code == 429:
                    _submit_retry += 1
                    if _submit_retry <= config.AGNES_IMAGE_SUBMIT_RETRIES:
                        wait = parse_retry_after(res.text)
                        cool = (wait + 0.5) if wait is not None \
                            else self._key_scheduler._cooldown
                        self._key_scheduler.penalize(key_idx, cool)
                        _verbose_write(f"  [镜头{req.shot_index}] 速率限制 429，Key {key_idx + 1} "
                              f"冷却 {cool:.1f}s，调度下一个可用 Key "
                              f"({_submit_retry}/{config.AGNES_IMAGE_SUBMIT_RETRIES})")
                        continue
                    last_error = (f"Agnes 图片速率限制 [429] "
                                  f"(重试{_submit_retry}次): {res.text[:300]}")
                    break
                # ---- 403: 额度耗尽或无权限，尝试回退 ----
                if res.status_code == 403:
                    _403_count += 1
                    # 先冷却该 Key 换下一个试（可能是单 Key 额度问题）；
                    # 轮完一圈可用 key 仍 403 → 模型问题，走模型回退
                    if _403_count < self._key_scheduler.n_active():
                        self._key_scheduler.penalize(key_idx, self._key_scheduler._cooldown)
                        continue
                    _403_count = 0
                    nxt = self._advance_index(idx)
                    if nxt is not None:
                        _verbose_write(f"  [镜头{req.shot_index}] [!] 图片模型 {model} 返回 403"
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
