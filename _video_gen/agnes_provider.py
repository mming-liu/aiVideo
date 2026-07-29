"""agnes ai 视频生成 — 异步轮询模式.

提交: POST /v1/videos
轮询: GET  /agnesapi?video_id=<VIDEO_ID>

模型: agnes-video-v2.0
  - text-to-video: prompt + width + height + num_frames + frame_rate
  - image-to-video: 附加 image 字段 (data URL base64)

403 自动回退: 主模型返回 403 时切换到 AGNES_VIDEO_FALLBACK_MODELS 中的下一个,
回退在实例生命周期内保持 (与 DashScope 一致).
"""

import base64
import mimetypes
import os
import time
from typing import Optional

import requests

from .models import VideoGenerationRequest, VideoGenerationResult
from .service import VideoGenerationService
from .config import config
from shared.error_fmt import parse_retry_after
from shared.key_scheduler import KeyScheduler


_SYNC_PREFIX = "__sync__"


class AgnesVideoService(VideoGenerationService):
    """agnes ai 视频生成实现 (agnes-video-v2.0)."""

    def __init__(self) -> None:
        config.validate()
        # 构建完整模型队列: [主模型, 回退1, 回退2, ...]
        self._model_queue: list[str] = [
            config.AGNES_VIDEO_MODEL
        ] + config.AGNES_VIDEO_FALLBACK_MODELS
        self._current_model_index: int = 0

        # 多 API Key 支持（与 text/image provider 一致）
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

        # 多 key 冷却调度：视频免费档约 1 次/60s/key，留 1s 余量
        # N 个 key 轮询提交，吞吐提升 N 倍
        self._key_scheduler = KeyScheduler(
            len(self._api_keys),
            cooldown=float(os.getenv("AGNES_VIDEO_KEY_COOLDOWN", "61")),
            name="video-keys",
        )
        # task_id -> 提交时所用 key 下标（查询必须用同一 key，跨 key 查不到任务）
        self._task_keys: dict[str, int] = {}

    # ------------------------------------------------------------------
    # API Key 轮换
    # ------------------------------------------------------------------
    def get_key_index(self) -> int:
        """返回当前使用的 key 索引."""
        return self._current_key_index

    # ------------------------------------------------------------------
    # 模型回退
    # ------------------------------------------------------------------
    @property
    def _current_model(self) -> str:
        return self._model_queue[self._current_model_index]

    def _advance_model(self) -> Optional[str]:
        """切换到下一个回退模型. 返回新模型名，无可用模型时返回 None."""
        if self._current_model_index + 1 >= len(self._model_queue):
            return None
        self._current_model_index += 1
        return self._current_model

    # ------------------------------------------------------------------
    # 首帧图编码
    # ------------------------------------------------------------------
    @staticmethod
    def _encode_image(path: Optional[str]) -> Optional[str]:
        """读取本地图片，返回 data URL (base64).

        Agnes video API 使用标准 base64（保留 = padding）。
        确保图片编码后 len % 4 == 0，否则解码会失败报 "Incorrect padding"。
        之前错误地使用了 .rstrip("=")，仅当文件大小是 3 的倍数时无害，
        其余情况会破坏 4 字节对齐。现已修复为标准 base64。
        """
        if not path:
            return None
        try:
            p = os.path.abspath(path)
            if not os.path.isfile(p):
                return None
            with open(p, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode("ascii")
            mime, _ = mimetypes.guess_type(p)
            return f"data:{mime or 'image/png'};base64,{b64}"
        except Exception:
            return None

    @staticmethod
    def _encode_images(paths: list[str]) -> list[str]:
        """编码多个图片路径为 data URL 列表."""
        return [img for path in paths if (img := AgnesVideoService._encode_image(path))]

    # ------------------------------------------------------------------
    # num_frames 约束
    # ------------------------------------------------------------------
    MAX_NUM_FRAMES = 441  # Agnes 硬限制

    @staticmethod
    def _clamp_num_frames(raw: int) -> int:
        """Agnes 约束: num_frames = 8n + 1，且 <= 441."""
        if raw < 1:
            return 1
        n = round((raw - 1) / 8)
        n = max(n, 0)
        result = 8 * n + 1
        # 硬限制：不超过 441
        if result > AgnesVideoService.MAX_NUM_FRAMES:
            result = AgnesVideoService.MAX_NUM_FRAMES
        return result

    # ------------------------------------------------------------------
    # 响应字段解析
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_video_id(data: dict) -> Optional[str]:
        """从创建任务响应中提取 video_id."""
        for key in ("video_id", "id", "task_id"):
            v = data.get(key)
            if v:
                return str(v)
        # 嵌套在 data 字段中
        d = data.get("data") or {}
        if isinstance(d, dict):
            for key in ("video_id", "id", "task_id"):
                v = d.get(key)
                if v:
                    return str(v)
        return None

    @staticmethod
    def _extract_video_url(output: dict) -> Optional[str]:
        """从轮询响应中提取视频下载 URL."""
        for key in ("video_url", "url", "remixed_from_video_id"):
            v = output.get(key)
            if v and isinstance(v, str) and v.startswith("http"):
                return str(v)
        urls = output.get("video_urls")
        if urls and isinstance(urls, list) and urls[0]:
            return str(urls[0])
        d = output.get("data") or {}
        if isinstance(d, dict):
            for key in ("video_url", "url", "remixed_from_video_id"):
                v = d.get(key)
                if v and isinstance(v, str) and v.startswith("http"):
                    return str(v)
        return None

    @staticmethod
    def _extract_status(output: dict) -> str:
        """从轮询响应中提取任务状态，优先用 internal_status."""
        # Agnes 返回 internal_status (completed/in_progress)，比 status 更可靠
        s = output.get("internal_status") or output.get("status") or output.get("task_status") or ""
        return str(s).upper()

    # ------------------------------------------------------------------
    # 创建任务（带 403 回退 + 5xx 自动重试）
    # ------------------------------------------------------------------
    def create_task(self, req: VideoGenerationRequest) -> str:
        # 日志：从 tee_logger 输出到 step02.log，与 _image_gen 保持一致
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        _vlog = get_logger(chapter_dir() / "step02.log", console_level="INFO")

        image_data_url = self._encode_image(req.image_path)
        if req.image_path and not image_data_url:
            _vlog.verbose(f"  [镜头{req.clip_index}] 首帧图 {req.image_path} 读取失败，"
                  f"降级为 text-to-video")
        # 尾帧：与首帧同时提供时启用关键帧动画模式（首尾双锚定钳制风格漂移）
        last_frame_data_url = self._encode_image(req.last_frame_image_path)
        if req.last_frame_image_path and not last_frame_data_url:
            _vlog.verbose(f"  [镜头{req.clip_index}] 尾帧图 {req.last_frame_image_path} 读取失败，"
                  f"降级为 image-to-video")
        # 多关键帧模式（extra_body.image 数组，3-4 帧完整动作序列）
        extra_keyframes = []
        if req.extra_keyframe_paths:
            extra_keyframes = self._encode_images(req.extra_keyframe_paths)
            if len(extra_keyframes) >= 2:
                _vlog.verbose(f"  [镜头{req.clip_index}] 多关键帧模式: {len(extra_keyframes)} 帧锚定 "
                      f"(完整动作序列 A->B->C...)")

        # 计算尺寸与帧数：优先使用 req.size，否则回退到 config
        if req.size:
            # req.size 格式: "720x1280"
            size_parts = req.size.lower().split("x")
            if len(size_parts) == 2:
                width = int(size_parts[0])
                height = int(size_parts[1])
            else:
                width = config.AGNES_VIDEO_WIDTH
                height = config.AGNES_VIDEO_HEIGHT
        else:
            width = config.AGNES_VIDEO_WIDTH
            height = config.AGNES_VIDEO_HEIGHT
        frame_rate = config.AGNES_VIDEO_FRAME_RATE
        num_frames = config.AGNES_VIDEO_NUM_FRAMES
        if num_frames <= 0 and req.duration > 0:
            raw = req.duration * frame_rate
            # Agnes 约束: num_frames = 8n + 1，取最接近的合法值
            num_frames = self._clamp_num_frames(raw)

        _submit_retry = 0
        _first_attempt = True       # 首次尝试才打印完整 prompt
        _consecutive_5xx = 0        # 连续 5xx 计数（全局故障检测）

        while True:
            model = self._current_model
            # 冷却调度：取最早可用的 key（阻塞直到有 key 出冷却）
            key_idx = self._key_scheduler.acquire()
            self._current_key_index = key_idx
            headers = {
                "Authorization": f"Bearer {self._api_keys[key_idx]}",
                "Content-Type": "application/json",
            }
            payload: dict = {
                "model": model,
                "prompt": req.prompt,
                "width": width,
                "height": height,
                "frame_rate": frame_rate,
            }
            if num_frames > 0:
                payload["num_frames"] = num_frames
            if req.negative_prompt:
                payload["negative_prompt"] = req.negative_prompt
            if req.seed is not None:
                payload["seed"] = req.seed
            if req.num_inference_steps is not None:
                payload["num_inference_steps"] = req.num_inference_steps
            # 多关键帧模式（3-4 帧）优先于双关键帧模式
            if extra_keyframes and len(extra_keyframes) >= 2:
                payload["extra_body"] = {
                    "image": extra_keyframes,
                    "mode": "keyframes",
                }
            elif image_data_url and last_frame_data_url:
                # 双关键帧模式（首尾双锚定）作为回退
                payload["extra_body"] = {
                    "image": [image_data_url, last_frame_data_url],
                    "mode": "keyframes",
                }
                _vlog.verbose(f"  [镜头{req.clip_index}] 关键帧模式: 首帧+尾帧双锚定")
            elif image_data_url:
                payload[config.AGNES_VIDEO_IMAGE_FIELD] = image_data_url
                # 日志含 b64 长度，便于排查 padding 相关错误
                b64_body = image_data_url.split(",", 1)[-1]
                _vlog.verbose(f"  [镜头{req.clip_index}] 参考图编码完成 "
                      f"(len={len(b64_body)}, mod4={len(b64_body)%4})")

            if _first_attempt:
                _vlog.verbose(f"  [镜头{req.clip_index}] [prompt -> {model}] "
                      f"{req.prompt[:200]}{'...' if len(req.prompt) > 200 else ''}")
                _first_attempt = False

            # 记录完整请求日志
            import json
            _log_payload = {k: v for k, v in payload.items()}
            # 图片数据只记录长度，避免日志过大
            if "image" in _log_payload and _log_payload["image"]:
                _log_payload["image"] = f"[base64 data, len={len(_log_payload['image'])}]"
            if "extra_body" in _log_payload and isinstance(_log_payload.get("extra_body"), dict):
                _eb = _log_payload["extra_body"]
                if "image" in _eb and _eb["image"]:
                    if isinstance(_eb["image"], list):
                        _eb["image"] = [f"[base64 data, len={len(x)}]" if isinstance(x, str) and x.startswith("data:") else x for x in _eb["image"]]
                    else:
                        _eb["image"] = f"[base64 data, len={len(_eb['image'])}]"
            _vlog.verbose(f"[DEBUG] video_submit: POST {self._submit_url()}")
            _vlog.verbose(f"[DEBUG] video_submit: headers={json.dumps({k: v if k != 'Authorization' else f'Bearer ***{v[-8:]}' for k, v in headers.items()}, ensure_ascii=False)}")
            _vlog.verbose(f"[DEBUG] video_submit: payload={json.dumps(_log_payload, ensure_ascii=False, indent=2)}")

            try:
                res = requests.post(
                    self._submit_url(), json=payload, headers=headers, timeout=120
                )
                # 记录响应日志
                _vlog.verbose(f"[DEBUG] video_submit response: status={res.status_code}, body={res.text[:500]}")
            except Exception as e:
                # 连接错误也做重试
                _submit_retry += 1
                if _submit_retry <= config.AGNES_VIDEO_SUBMIT_RETRIES:
                    _vlog.verbose(f"  [镜头{req.clip_index}] 提交异常: {e}，"
                          f"{config.AGNES_VIDEO_SUBMIT_RETRY_DELAY}s 后重试 "
                          f"({_submit_retry}/{config.AGNES_VIDEO_SUBMIT_RETRIES})")
                    time.sleep(config.AGNES_VIDEO_SUBMIT_RETRY_DELAY)
                    continue
                # 当前 key 超时耗尽，惩罚后切 key 重试
                self._key_scheduler.penalize(key_idx, 60)
                if self._key_scheduler.n_active() > 1:
                    _vlog.verbose(f"  [镜头{req.clip_index}] Key {key_idx + 1} 超时耗尽，"
                          f"冷却 60s，调度下一个可用 Key")
                    _submit_retry = 0
                    continue
                raise RuntimeError(f"Agnes 视频提交异常（重试{_submit_retry}次后仍失败）: {e}")

            # ---- 200 ----
            if res.status_code == 200:
                data = res.json()

                # sync 模式: 直接返回 video_url（用前缀标记）
                if config.AGNES_VIDEO_MODE == "sync":
                    vurl = self._extract_video_url(data)
                    if vurl:
                        _vlog.verbose(f"  [镜头{req.clip_index}] 任务已创建(同步) [{model}] "
                              f"-> 直接返回 video_url")
                        return f"{_SYNC_PREFIX}{vurl}"
                    raise RuntimeError(
                        f"Agnes 同步模式但未返回 video_url: {str(data)[:300]}"
                    )

                # async 模式
                video_id = self._extract_video_id(data)
                if not video_id:
                    raise RuntimeError(
                        f"Agnes 未返回 video_id: {str(data)[:300]}"
                    )
                # 绑定任务与提交 key：后续查询必须用同一 key
                self._task_keys[video_id] = key_idx
                if image_data_url and last_frame_data_url:
                    mode_tag = "keyframes"
                elif image_data_url:
                    mode_tag = "image-to-video"
                else:
                    mode_tag = "text-to-video"
                _vlog.verbose(f"  [镜头{req.clip_index}] 任务已创建 [{model}] ({mode_tag}) "
                      f"[Key {key_idx + 1}] -> video_id: {video_id}")
                return video_id

            # ---- 5xx: 服务端暂时不可用 ----
            # 5xx 是服务端全局故障，不是 key 限流。短罚时（10s）快速探测，
            # 避免当前逐个 key 等 9s （acquire 内部最多等 5s）都把 key 罚 60s。
            # 连续 5 个 key 都 5xx 说明服务端全员故障，统一等 30s 后再试。
            if 500 <= res.status_code < 600:
                _consecutive_5xx += 1
                self._key_scheduler.penalize(key_idx, 10)  # 短罚时，快速跳过
                if self._key_scheduler.n_active() > 1 and _consecutive_5xx < self._key_scheduler.n_active():
                    _vlog.verbose(f"  [镜头{req.clip_index}] 服务端 {res.status_code}，"
                          f"Key {key_idx + 1} (5xx #{_consecutive_5xx})，切下一个 Key")
                    continue
                # 所有 key 都返回 5xx，或只有一个 key：统一等待后重试
                _submit_retry += 1
                if _submit_retry <= config.AGNES_VIDEO_SUBMIT_RETRIES:
                    wait = 30  # 全局故障统一等 30s
                    _vlog.verbose(f"  [镜头{req.clip_index}] 所有 Key 均 {res.status_code}，"
                          f"全局故障，{wait}s 后重试 "
                          f"({_submit_retry}/{config.AGNES_VIDEO_SUBMIT_RETRIES})")
                    time.sleep(wait)
                    _consecutive_5xx = 0
                    continue
                raise RuntimeError(
                    f"Agnes 视频提交失败 [5xx 全局故障] (重试{_submit_retry}次): "
                    f"{res.text[:500]}"
                )

            # ---- 401/402: 认证失败或欠费，拉黑该 Key 并调度下一个 ----
            if res.status_code in (401, 402):
                self._key_scheduler.disable(key_idx)
                if self._key_scheduler.n_active() > 0:
                    _vlog.verbose(f"  [镜头{req.clip_index}] HTTP {res.status_code}，"
                          f"Key {key_idx + 1} 已拉黑，调度下一个可用 Key")
                    continue
                raise RuntimeError(
                    f"Agnes API Key 全部无效/欠费 [HTTP {res.status_code}]: "
                    f"{res.text[:300]}"
                )

            # ---- 429: 速率限制，按服务端剩余时间冷却该 Key，调度下一个 ----
            if res.status_code == 429:
                _submit_retry += 1
                if _submit_retry <= config.AGNES_VIDEO_SUBMIT_RETRIES:
                    wait = parse_retry_after(res.text)
                    cool = (wait + 0.5) if wait is not None \
                        else self._key_scheduler._cooldown
                    self._key_scheduler.penalize(key_idx, cool)
                    _vlog.verbose(f"  [镜头{req.clip_index}] 速率限制 429，Key {key_idx + 1} "
                          f"冷却 {cool:.1f}s，调度下一个可用 Key "
                          f"({_submit_retry}/{config.AGNES_VIDEO_SUBMIT_RETRIES})")
                    continue
                raise RuntimeError(
                    f"Agnes 视频速率限制 [429] (重试{_submit_retry}次): "
                    f"{res.text[:500]}"
                )

            # ---- 403: 额度耗尽或无权限，尝试回退 ----
            if res.status_code == 403:
                next_model = self._advance_model()
                if next_model:
                    _vlog.verbose(f"  [镜头{req.clip_index}] [!] 模型 {model} 返回 403"
                          f"（额度耗尽/无权限），自动切换到 {next_model}")
                    continue  # 用新模型重试
                else:
                    raise RuntimeError(
                        f"所有模型均返回 403，已无可用回退模型。"
                        f"尝试过: {', '.join(self._model_queue)}"
                    )

            # ---- 其他错误 ----
            raise RuntimeError(
                f"Agnes 视频提交失败 [{res.status_code}]: {res.text[:500]}"
            )

    # ------------------------------------------------------------------
    # URL 构造
    # ------------------------------------------------------------------
    def _submit_url(self) -> str:
        return f"{config.AGNES_BASE_URL.rstrip('/')}/v1/videos"

    def _query_url(self, video_id: str) -> str:
        """轮询端点: GET /agnesapi?video_id=<VIDEO_ID>."""
        return (f"{config.AGNES_BASE_URL.rstrip('/')}"
                f"{config.AGNES_VIDEO_POLL_ENDPOINT}?video_id={video_id}")

    # ------------------------------------------------------------------
    # 查询任务
    # ------------------------------------------------------------------
    def query_task(self, task_id: str) -> dict:
        # 必须用提交该任务时的 key 查询（任务归属提交账号，跨 key 查不到）
        bound_key_idx = self._task_keys.get(task_id, self._current_key_index)
        bound_key = self._api_keys[bound_key_idx]
        url = self._query_url(task_id)
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        _vlog = get_logger(chapter_dir() / "step02.log", console_level="INFO")
        _vlog.verbose(f"[DEBUG] query_task: GET {url}")
        _vlog.verbose(f"[DEBUG] query_task: key_idx={bound_key_idx}, key={bound_key[:30]}...")
        _vlog.verbose(f"[DEBUG] query_task: header=Authorization: Bearer {bound_key[:40]}...")
        try:
            res = requests.get(
                url,
                headers={"Authorization": f"Bearer {bound_key}"},
                timeout=120,
            )
            _vlog.verbose(f"[DEBUG] query_task: status={res.status_code}, body={res.text[:300]}")
        except Exception as e:
            raise RuntimeError(f"Agnes 查询异常: {e}")
        if res.status_code != 200:
            raise RuntimeError(
                f"Agnes 查询失败 [{res.status_code}]: {res.text[:300]}"
            )
        return res.json()

    # ------------------------------------------------------------------
    # 轮询等待 + 下载
    # ------------------------------------------------------------------
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str
    ) -> VideoGenerationResult:
        # 日志：从 tee_logger 输出到 step02.log，与 create_task 保持一致
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        _vlog = get_logger(chapter_dir() / "step02.log", console_level="INFO")

        # sync 模式: task_id 实际是 video_url
        if task_id.startswith(_SYNC_PREFIX):
            return self._download(req, task_id[len(_SYNC_PREFIX):])

        attempt = 0
        # 下次查询前的等待时间；429 时改为服务端返回的剩余时间 + 0.5s
        next_wait = config.AGNES_VIDEO_POLL_INTERVAL
        while True:
            attempt += 1
            if attempt > config.AGNES_VIDEO_MAX_POLL_ATTEMPTS:
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"轮询超时 ({config.AGNES_VIDEO_MAX_POLL_ATTEMPTS} 次)",
                )

            time.sleep(next_wait)
            next_wait = config.AGNES_VIDEO_POLL_INTERVAL
            # 调试日志：显示查询用的 key
            bound_key_idx = self._task_keys.get(task_id, self._current_key_index)
            _vlog.verbose(f"  [镜头{req.clip_index}] [DEBUG] query_task key_idx={bound_key_idx}")
            try:
                output = self.query_task(task_id)
            except RuntimeError as e:
                err = str(e)
                # 429 限流: 不中断轮询循环，按服务端提示的剩余时间精确等待后继续
                if "[429]" in err:
                    wait = parse_retry_after(err)
                    next_wait = (wait + 0.5) if wait is not None \
                        else config.AGNES_VIDEO_POLL_INTERVAL * 2
                    _vlog.verbose(f"  [镜头{req.clip_index}] 查询限流 429，"
                          f"等待 {next_wait:.1f}s 后继续轮询")
                    continue
                raise
            status = self._extract_status(output)
            url_val = output.get('url', '')
            _vlog.verbose(f"  [镜头{req.clip_index}] [DEBUG] raw_status={output.get('internal_status')}, parsed={status}, has_url={bool(url_val)}")

            if status in ("SUCCEEDED", "SUCCESS", "COMPLETED", "DONE", "COMPLETE"):
                video_url = self._extract_video_url(output)
                _vlog.verbose(f"  [镜头{req.clip_index}] [DEBUG] extracted video_url={video_url[:80] if video_url else 'None'}...")
                if not video_url:
                    # 残量诊断日志，方便排查新增响应结构
                    _vlog.verbose(f"  [镜头{req.clip_index}] [DEBUG] 未找到 video_url, "
                          f"响应: {str(output)[:500]}")
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error="任务成功但未找到 video_url",
                    )
                _vlog.verbose(f"  [镜头{req.clip_index}] [DEBUG] calling _download with {video_url[:80]}...")
                return self._download(req, video_url)

            if status in ("FAILED", "FAIL", "ERROR"):
                reason = (output.get("message") or output.get("error")
                          or output.get("code") or "未知")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"API 失败: {reason}",
                )

            _vlog.verbose(f"  [镜头{req.clip_index}] 生成中... ({status}, "
                  f"第 {attempt}/{config.AGNES_VIDEO_MAX_POLL_ATTEMPTS} 次轮询)")

    # ------------------------------------------------------------------
    # 下载视频到本地
    # ------------------------------------------------------------------
    def _download(self, req: VideoGenerationRequest, video_url: str) -> VideoGenerationResult:
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        _vlog = get_logger(chapter_dir() / "step02.log", console_level="INFO")
        try:
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            save_path = os.path.join(config.OUTPUT_DIR, f"clip_{req.clip_index}.mp4")

            _vlog.verbose(f"  [镜头{req.clip_index}] [DEBUG] downloading from {video_url[:80]}...")
            resp = requests.get(video_url, timeout=120)
            resp.raise_for_status()
            video_bin = resp.content
            if len(video_bin) < 20 * 1024:
                raise RuntimeError(f"下载内容过小 ({len(video_bin)} bytes)，可能是错误页面")
            with open(save_path, "wb") as f:
                f.write(video_bin)

            size_mb = len(video_bin) / (1024 * 1024)
            _vlog.verbose(f"  [镜头{req.clip_index}] [OK] 已保存 -> {save_path} ({size_mb:.1f} MB)")
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=True,
                save_path=save_path,
            )
        except Exception as e:
            _vlog.verbose(f"  [镜头{req.clip_index}] [FAIL] 下载失败: {e}")
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=False,
                error=f"下载失败: {e}",
            )
