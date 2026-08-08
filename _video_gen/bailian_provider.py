"""Bailian (阿里云百炼) 视频生成 Provider.

v5: 支持 wan2.7 和 happyhorse 模型，区分 T2V(文生视频) 和 I2V(图生视频)。
使用 dashscope SDK v1.26+ API 格式。
"""
import os
import base64
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from dashscope import VideoSynthesis
import dashscope

from _video_gen import VideoGenerationRequest, VideoGenerationResult
from _video_gen.service import VideoGenerationService
from shared.config import (
    BAILIAN_WORKSPACE_ID,
    BAILIAN_REGION,
    BAILIAN_VIDEO_RESOLUTION,
    BAILIAN_VIDEO_RATIO,
    BAILIAN_VIDEO_DURATION,
    BAILIAN_T2V_MODEL,
    BAILIAN_I2V_MODEL,
    BAILIAN_I2V_FALLBACK_MODELS,
    BAILIAN_R2V_MODEL,
    BAILIAN_VIDEO_POLL_INTERVAL,
    BAILIAN_VIDEO_MAX_POLL_ATTEMPTS,
)

# 初始化 Bailian workspace URL
if BAILIAN_WORKSPACE_ID and BAILIAN_REGION:
    dashscope.base_url = f"https://{BAILIAN_WORKSPACE_ID}.{BAILIAN_REGION}.maas.aliyuncs.com"


from shared.log_utils import verbose_write as _verbose_write  # noqa: E402

# 模型列表从 shared/config.py 读取（可通过 .env 覆盖）
# 示例 .env 配置:
#   BAILIAN_T2V_MODEL=wan2.7-t2v-2026-06-12,happyhorse-1.1-t2v
#   BAILIAN_I2V_MODEL=wan2.7-i2v-2026-04-25
#   BAILIAN_I2V_FALLBACK_MODELS=happyhorse-1.0-i2v,happyhorse-1.1-i2v
DEFAULT_T2V_MODELS = [m.strip() for m in BAILIAN_T2V_MODEL.split(",") if m.strip()] if BAILIAN_T2V_MODEL else ["wan2.7-t2v-2026-06-12"]
DEFAULT_I2V_MODELS = (
    [BAILIAN_I2V_MODEL] + BAILIAN_I2V_FALLBACK_MODELS
    if BAILIAN_I2V_MODEL else
    ["wan2.7-i2v-2026-04-25", "happyhorse-1.0-i2v", "happyhorse-1.1-i2v"]
)
DEFAULT_R2V_MODELS = (
    [m.strip() for m in BAILIAN_R2V_MODEL.split(",") if m.strip()]
    if BAILIAN_R2V_MODEL else ["wan2.7-r2v-2026-06-12"]
)


def _int_duration(value) -> int:
    """强制把时长转成整数（API 要求 parameters.duration 为 int）.

    脚本里 duration 可能是 3.0 float（step01 时间码计算），直接传 API 会被拒：
    "Input should be a valid integer: parameters.duration"。
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 5


def _append_image_refs(prompt: str, n: int) -> str:
    """给 happyhorse R2V 的 prompt 追加 [Image N] 指代说明.

    happyhorse-1.1-r2v 文档要求用 [Image 1]、[Image 2] 标识指代 media 数组中
    对应位置的参考图像（顺序与 media 一致），否则模型不明确各参考图的用途。
    wan2.7-r2v 不需要（prompt_builder 已注入用途声明）。
    """
    if n <= 0:
        return prompt
    ref_lines = [
        "[Image {}] 为传入的第{}张参考图（顺序与参考图列表一致）。".format(i + 1, i + 1)
        for i in range(n)
    ]
    return prompt + "\n\n【参考图指代】" + " ".join(ref_lines)


class BailianVideoService(VideoGenerationService):
    """Bailian 视频生成服务 (阿里云 dashscope).

    支持:
    - T2V (text-to-video): wan2.7-t2v
    - I2V (image-to-video): wan2.7-i2v, happyhorse 系列
    - R2V (reference-to-video): wan2.7-r2v (最多5个参考图/视频，支持音频音色参考)

    模型差异:
    - Wan2.7 T2V: 支持 negative_prompt, prompt_extend, audio_url
    - HappyHorse T2V: 不支持上述参数
    - Wan2.7 I2V: 支持 first_frame + last_frame，支持 ratio 参数
    - HappyHorse I2V: 只支持 first_frame，不支持 ratio 参数
    - Wan2.7 R2V: 支持最多5个参考图/视频混合，音频音色参考，更强的角色一致性
    """

    def __init__(
        self,
        api_keys: list[str] | None = None,
        t2v_models: list[str] | None = None,
        i2v_models: list[str] | None = None,
        r2v_models: list[str] | None = None,
        resolution: str | None = None,
        ratio: str | None = None,
        duration: int | None = None,
    ):
        # 简单限速：每次请求后等待
        self._cooldown = 1.0

        # 模型列表
        self._t2v_model_queue = t2v_models or DEFAULT_T2V_MODELS
        self._i2v_model_queue = i2v_models or DEFAULT_I2V_MODELS
        self._r2v_model_queue = r2v_models or DEFAULT_R2V_MODELS
        self._current_i2v_model_index = 0
        self._current_r2v_model_index = 0

        # 输出参数（从配置读取，resolution 必须大写：720P 或 1080P）
        self._resolution = resolution or BAILIAN_VIDEO_RESOLUTION  # 默认: 720P
        self._ratio = ratio or BAILIAN_VIDEO_RATIO                   # 默认: 9:16
        self._duration = duration or BAILIAN_VIDEO_DURATION          # 默认: 5

        # 线程池用于图片编码
        self._executor = ThreadPoolExecutor(max_workers=4)

    def _advance_i2v_model(self) -> str | None:
        """切换到下一个 I2V 模型."""
        if self._current_i2v_model_index < len(self._i2v_model_queue) - 1:
            self._current_i2v_model_index += 1
            return self._i2v_model_queue[self._current_i2v_model_index]
        return None

    def _encode_image(self, image_path: str) -> str | None:
        """将图片文件编码为 base64 字符串（不包含 data: 前缀）."""
        if not image_path:
            return None
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            _verbose_write(f"  [WARN] 编码图片失败 {image_path}: {e}")
            return None

    # =======================================================================
    # 创建任务 - 根据是否有图片决定 T2V / I2V / R2V
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        # Bailian 限速：每个请求等待冷却
        time.sleep(self._cooldown)

        # 判断模式：T2V / I2V / R2V
        has_first_frame = bool(req.image_path)
        has_last_frame = bool(req.last_frame_image_path)
        has_refs = bool(req.reference_paths)

        # R2V: 有 reference_paths 时优先使用 R2V（角色/道具一致性保持）
        if has_refs:
            return self._create_r2v_task(req)

        if has_first_frame or has_last_frame:
            return self._create_i2v_task(req)
        else:
            return self._create_t2v_task(req)

    # =======================================================================
    # T2V (文生视频) - 区分 Wan 和 HappyHorse
    # =======================================================================
    def _create_t2v_task(self, req: VideoGenerationRequest) -> str:
        """创建文生视频任务."""
        model = self._t2v_model_queue[0]  # T2V 暂不支持回退

        if "happyhorse" in model.lower():
            return self._create_happyhorse_t2v_task(req, model)
        else:
            return self._create_wan_t2v_task(req, model)

    def _create_wan_t2v_task(self, req: VideoGenerationRequest, model: str) -> str:
        """创建 Wan2.7 文生视频任务.

        Wan2.7 T2V 支持:
        - prompt, negative_prompt, audio_url
        - resolution, ratio, duration, prompt_extend, watermark, seed
        """
        kwargs: dict = {
            "model": model,
            "prompt": req.prompt,
            "negative_prompt": req.negative_prompt or "",
            "resolution": self._resolution,
            "ratio": self._ratio,
            "duration": _int_duration(req.duration or self._duration),
            "prompt_extend": True,   # 默认开启智能改写
            "watermark": False,      # 默认不添加水印
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        _verbose_write(f"  [镜头{req.clip_index}] [Wan T2V] 正在提交任务 [{model}]...")
        # 完整请求参数日志（v1.1）：prompt 全文 + 全部 kwargs
        import json as _json
        _log_kwargs = dict(kwargs)
        # prompt_extend 内容不展开（bool），audio_url 若是 base64 只记长度
        if _log_kwargs.get("audio_url") and str(_log_kwargs["audio_url"]).startswith("data:"):
            _log_kwargs["audio_url"] = f"[base64 audio, len={len(_log_kwargs['audio_url'])}]"
        _verbose_write(f"[DEBUG] video_submit: model={model}")
        _verbose_write(f"[DEBUG] video_submit: prompt全文={req.prompt}")
        _verbose_write(f"[DEBUG] video_submit: params={_json.dumps({k: v for k, v in _log_kwargs.items() if k not in ('model', 'prompt')}, ensure_ascii=False)}")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [Wan T2V] -> task_id: {task_id}")
            return task_id

        # 403: 额度耗尽
        if rsp.status_code == 403:
            raise RuntimeError(f"T2V 模型 {model} 返回 403（额度耗尽/无权限）.")

        # 其他错误
        raise RuntimeError(f"API 错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}")

    def _create_happyhorse_t2v_task(self, req: VideoGenerationRequest, model: str) -> str:
        """创建 HappyHorse 文生视频任务.

        HappyHorse T2V 支持:
        - prompt (仅此必填)
        - resolution, duration, watermark, seed
        - 不支持: negative_prompt, prompt_extend, audio_url, ratio
        """
        kwargs: dict = {
            "model": model,
            "prompt": req.prompt,
            "resolution": self._resolution,
            "duration": _int_duration(req.duration or self._duration),
            "watermark": True,  # HappyHorse 默认添加水印
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        _verbose_write(f"  [镜头{req.clip_index}] [HappyHorse T2V] 正在提交任务 [{model}]...")
        _verbose_write(f"    prompt: {req.prompt[:60]}...")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [HappyHorse T2V] -> task_id: {task_id}")
            return task_id

        if rsp.status_code == 403:
            raise RuntimeError(f"T2V 模型 {model} 返回 403（额度耗尽/无权限）.")

        raise RuntimeError(f"API 错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}")

    # =======================================================================
    # I2V (图生视频) - 区分 Wan 和 HappyHorse
    # =======================================================================
    def _create_i2v_task(self, req: VideoGenerationRequest) -> str:
        """创建图生视频任务，根据模型类型分发到不同实现."""
        # 编码首帧/尾帧
        first_frame_b64 = self._encode_image(req.image_path) if req.image_path else None
        last_frame_b64 = self._encode_image(req.last_frame_image_path) if req.last_frame_image_path else None

        # 遍历模型队列，尝试创建任务
        for model in self._i2v_model_queue:
            if "happyhorse" in model.lower():
                task_id = self._create_happyhorse_i2v_task(req, model, first_frame_b64)
            else:
                # wan2.7 或其他模型
                task_id = self._create_wan_i2v_task(req, model, first_frame_b64, last_frame_b64)

            if task_id:
                return task_id

        raise RuntimeError(f"I2V 任务创建失败: 所有模型均失败")

    def _create_happyhorse_i2v_task(
        self, req: VideoGenerationRequest, model: str, first_frame_b64: str | None
    ) -> str | None:
        """创建 HappyHorse 图生视频任务.

        HappyHorse I2V 支持:
        - prompt
        - media (仅支持 first_frame)
        - resolution, duration, watermark, seed
        - 不支持: ratio, last_frame
        """
        if not first_frame_b64:
            _verbose_write(f"  [镜头{req.clip_index}] [!] HappyHorse 需要首帧图片")
            return None

        # HappyHorse 只支持 first_frame
        media = [{"type": "first_frame", "url": f"data:image/png;base64,{first_frame_b64}"}]

        kwargs: dict = {
            "model": model,
            "prompt": req.prompt,
            "media": media,
            "resolution": self._resolution,  # 必须是 720P 或 1080P
            "duration": _int_duration(req.duration or self._duration),
            "watermark": True,
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        _verbose_write(f"  [镜头{req.clip_index}] [HappyHorse I2V] 正在提交任务 [{model}]...")
        _verbose_write(f"    prompt: {req.prompt[:60]}...")
        _verbose_write(f"    首帧: {req.image_path}")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [HappyHorse I2V] -> task_id: {task_id}")
            return task_id

        _verbose_write(f"  [镜头{req.clip_index}] [!] HappyHorse 模型错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}")
        return None

    def _create_wan_i2v_task(
        self,
        req: VideoGenerationRequest,
        model: str,
        first_frame_b64: str | None,
        last_frame_b64: str | None,
    ) -> str | None:
        """创建 Wan2.7 图生视频任务.

        Wan2.7 I2V 支持:
        - prompt, negative_prompt
        - media (支持 first_frame + last_frame)
        - resolution, ratio, duration, watermark, seed
        """
        if not first_frame_b64 and not last_frame_b64:
            _verbose_write(f"  [镜头{req.clip_index}] [!] Wan 需要首帧或尾帧图片")
            return None

        # Wan2.7 支持双关键帧
        media = []
        if first_frame_b64:
            media.append({"type": "first_frame", "url": f"data:image/png;base64,{first_frame_b64}"})
        if last_frame_b64:
            media.append({"type": "last_frame", "url": f"data:image/png;base64,{last_frame_b64}"})

        kwargs: dict = {
            "model": model,
            "prompt": req.prompt,
            "media": media,
            "negative_prompt": req.negative_prompt or "",
            "resolution": self._resolution,
            "ratio": self._ratio,
            "duration": _int_duration(req.duration or self._duration),
            "watermark": False,
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        _verbose_write(f"  [镜头{req.clip_index}] [Wan I2V] 正在提交任务 [{model}]...")
        _verbose_write(f"    prompt: {req.prompt[:60]}...")
        if first_frame_b64:
            _verbose_write(f"    首帧: {req.image_path}")
        if last_frame_b64:
            _verbose_write(f"    尾帧: {req.last_frame_image_path}")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [Wan I2V] -> task_id: {task_id}")
            return task_id

        _verbose_write(f"  [镜头{req.clip_index}] [!] Wan 模型错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}")
        return None

    # =======================================================================
    # R2V (参考图/视频生视频) - Wan2.7 R2V
    # =======================================================================
    def _create_r2v_task(self, req: VideoGenerationRequest) -> str:
        """创建参考图生视频任务（固定用 R2V 模型队列首个模型）.

        模型差异（官方文档）：
        - wan2.7-r2v: 支持 negative_prompt；media 最多5个
        - happyhorse-1.1-r2v: 不支持 negative_prompt；media 1~9 个；
          prompt 用 [Image N] 指代参考图（文档要求）

        公共部分：media 项 type=reference_image，SDK 会把 base64 data URL
        自动上传并回填 input.media[].url。
        """
        model = self._r2v_model_queue[0]
        is_happyhorse = "happyhorse" in model.lower()
        # happyhorse 上限9，wan 上限5
        max_refs = 9 if is_happyhorse else 5

        ref_medias = []
        for ref_path in (req.reference_paths or [])[:max_refs]:
            b64 = self._encode_image(ref_path)
            if b64:
                # media 项 type 必须是 reference_image（官方文档），
                # 此前误用 'reference' 导致 API 报 Field required: input.media。
                ref_medias.append({'type': 'reference_image', 'url': f'data:image/png;base64,{b64}'})

        # 如果有首帧/尾帧，也作为参考
        first_frame_b64 = self._encode_image(req.image_path) if req.image_path else None
        last_frame_b64 = self._encode_image(req.last_frame_image_path) if req.last_frame_image_path else None
        if first_frame_b64:
            ref_medias.append({'type': 'reference_image', 'url': f'data:image/png;base64,{first_frame_b64}'})
        if last_frame_b64:
            ref_medias.append({'type': 'reference_image', 'url': f'data:image/png;base64,{last_frame_b64}'})

        if not ref_medias:
            _verbose_write(f'  [镜头{req.clip_index}] [!] R2V 需要至少一个参考图')
            return self._create_i2v_task(req)  # 回退到 I2V

        # 构建音频参考（可选）
        audio_url = None
        if req.reference_audio:
            # TODO: 需要上传音频获取 URL 或使用 base64
            pass

        # happyhorse 用 [Image N] 指代参考图（文档要求）；
        # wan 保持现状（用途声明已由 prompt_builder 注入）。
        prompt = req.prompt
        if is_happyhorse:
            prompt = _append_image_refs(prompt, len(ref_medias))

        kwargs: dict = {
            'model': model,
            'prompt': prompt,
            # 参考图用 media 参数（SDK 签名含 media，不含 references）
            'media': ref_medias,
            'resolution': self._resolution,
            'ratio': self._ratio,
            'duration': _int_duration(req.duration or self._duration),
            'watermark': False,
        }
        # happyhorse 不支持 negative_prompt；wan 支持
        if not is_happyhorse and req.negative_prompt:
            kwargs['negative_prompt'] = req.negative_prompt

        if audio_url:
            kwargs['reference_audio'] = audio_url
        if req.seed is not None:
            kwargs['seed'] = req.seed

        tag = 'HappyHorse R2V' if is_happyhorse else 'Wan R2V'
        _verbose_write(f'  [镜头{req.clip_index}] [{tag}] 正在提交任务 [{model}]...')
        _verbose_write(f'    prompt: {prompt[:60]}...')
        _verbose_write(f'    参考图数量: {len(ref_medias)}')

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output['task_id']
            _verbose_write(f'  [镜头{req.clip_index}] 任务已创建 [{tag}] -> task_id: {task_id}')
            return task_id

        _verbose_write(f'  [镜头{req.clip_index}] [!] {tag} 模型错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}')
        # R2V 失败回退到 I2V
        return self._create_i2v_task(req)

    # =======================================================================
    # 查询任务
    # =======================================================================
    def query_task(self, task_id: str) -> dict:
        rsp = VideoSynthesis.fetch(task_id)
        if rsp.status_code != 200:
            raise RuntimeError(f"查询任务失败: {rsp.code} - {rsp.message}")
        return rsp.output

    # =======================================================================
    # 轮询结果
    # =======================================================================
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str, timeout: int = 300
    ) -> VideoGenerationResult:
        """轮询直到视频生成完成或超时."""
        start_time = time.time()
        last_error = ""

        while time.time() - start_time < timeout:
            time.sleep(self._cooldown)

            # 查询状态
            try:
                rsp = VideoSynthesis.fetch(task_id)
            except Exception as e:
                last_error = str(e)
                time.sleep(2)
                continue

            status = rsp.output.task_status
            _verbose_write(f"  [镜头{req.clip_index}] 任务状态: {status} (已等待 {int(time.time() - start_time)}s)")

            # status: PENDING, RUNNING, SUCCEEDED, FAILED
            if status == "SUCCEEDED":
                video_url = rsp.output.video_url
                # 打印计费信息
                if hasattr(rsp.output, 'usage') and rsp.output.usage:
                    _verbose_write(f"  [镜头{req.clip_index}] 计费信息: {rsp.output.usage}")
                # 下载视频
                return self._download(req, video_url)

            if status == "FAILED":
                err_code = rsp.output.code if hasattr(rsp.output, 'code') else "unknown"
                err_msg = rsp.output.message if hasattr(rsp.output, 'message') else "unknown"
                _verbose_write(f"  [镜头{req.clip_index}] 任务失败! 错误码: {err_code}, 错误信息: {err_msg}")
                _verbose_write(f"  [镜头{req.clip_index}] 完整响应: {rsp.output}")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"生成失败: {err_code} - {err_msg}",
                )

            # PENDING 或 RUNNING：等待后继续轮询
            time.sleep(3)

        # 超时
        return VideoGenerationResult(
            clip_index=req.clip_index,
            success=False,
            error=f"轮询超时 ({timeout}s): {last_error}",
        )

    def _download(self, req: VideoGenerationRequest, video_url: str) -> VideoGenerationResult:
        """下载视频并保存到本地."""
        import urllib.request
        import tempfile
        import shutil

        # 保存路径 (使用 clip_index 命名)
        from shared.paths import vimax_working_dir

        work_dir = vimax_working_dir()
        save_dir = work_dir / "shots" / str(req.clip_index)
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / "video.mp4"

        _verbose_write(f"  [镜头{req.clip_index}] 下载视频: {video_url}")

        try:
            # 先下载到临时文件，再 copy 到目标路径（避免直接写入目标导致文件锁）
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
            urllib.request.urlretrieve(video_url, tmp_path)
            shutil.copy2(tmp_path, str(save_path))
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass
            _verbose_write(f"  [镜头{req.clip_index}] 视频已保存 -> {save_path}")
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=True,
                save_path=str(save_path),
            )
        except Exception as e:
            _verbose_write(f"  [镜头{req.clip_index}] 下载失败: {e}")
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=False,
                error=f"下载失败: {e}",
            )

    # =======================================================================
    # 批量生成（可选实现）
    # =======================================================================
    def batch_generate(
        self, requests: list[VideoGenerationRequest]
    ) -> list[VideoGenerationResult]:
        """批量生成（当前为串行执行，未来可优化为并发）."""
        results = []
        for req in requests:
            try:
                task_id = self.create_task(req)
                result = self.wait_for_completion(req, task_id)
            except Exception as e:
                result = VideoGenerationResult(
                    clip_index=req.clip_index, success=False, error=str(e)
                )
            results.append(result)
        return results