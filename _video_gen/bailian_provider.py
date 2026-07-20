"""Bailian (阿里云百炼) 视频生成 Provider.

v5: 支持 wan2.7 和 happyhorse 模型，区分 T2V(文生视频) 和 I2V(图生视频)。
使用 dashscope SDK v1.26+ API 格式。
"""
import os
import base64
import time
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
    BAILIAN_VIDEO_POLL_INTERVAL,
    BAILIAN_VIDEO_MAX_POLL_ATTEMPTS,
)

# 初始化 Bailian workspace URL
if BAILIAN_WORKSPACE_ID and BAILIAN_REGION:
    dashscope.base_url = f"https://{BAILIAN_WORKSPACE_ID}.{BAILIAN_REGION}.maas.aliyuncs.com"
    print(f"[INFO] Bailian Video API URL: {dashscope.base_url}")

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


class BailianVideoService(VideoGenerationService):
    """Bailian 视频生成服务 (阿里云 dashscope).

    支持:
    - T2V (text-to-video): wan2.7-t2v
    - I2V (image-to-video): wan2.7-i2v, happyhorse 系列

    模型差异:
    - Wan2.7 T2V: 支持 negative_prompt, prompt_extend, audio_url
    - HappyHorse T2V: 不支持上述参数
    - Wan2.7 I2V: 支持 first_frame + last_frame，支持 ratio 参数
    - HappyHorse I2V: 只支持 first_frame，不支持 ratio 参数
    """

    def __init__(
        self,
        api_keys: list[str] | None = None,
        t2v_models: list[str] | None = None,
        i2v_models: list[str] | None = None,
        resolution: str | None = None,
        ratio: str | None = None,
        duration: int | None = None,
    ):
        # 简单限速：每次请求后等待
        self._cooldown = 1.0

        # 模型列表
        self._t2v_model_queue = t2v_models or DEFAULT_T2V_MODELS
        self._i2v_model_queue = i2v_models or DEFAULT_I2V_MODELS
        self._current_i2v_model_index = 0

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
            print(f"  [WARN] 编码图片失败 {image_path}: {e}")
            return None

    # =======================================================================
    # 创建任务 - 根据是否有图片决定 T2V 还是 I2V
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        # Bailian 限速：每个请求等待冷却
        time.sleep(self._cooldown)

        # 判断是 T2V 还是 I2V（根据是否有首帧图）
        has_first_frame = bool(req.image_path)
        has_last_frame = bool(req.last_frame_image_path)

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
            "duration": self._duration,
            "prompt_extend": True,   # 默认开启智能改写
            "watermark": False,      # 默认不添加水印
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        print(f"  [镜头{req.clip_index}] [Wan T2V] 正在提交任务 [{model}]...")
        print(f"    prompt: {req.prompt[:60]}...")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            print(f"  [镜头{req.clip_index}] 任务已创建 [Wan T2V] -> task_id: {task_id}")
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
            "duration": self._duration,
            "watermark": True,  # HappyHorse 默认添加水印
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        print(f"  [镜头{req.clip_index}] [HappyHorse T2V] 正在提交任务 [{model}]...")
        print(f"    prompt: {req.prompt[:60]}...")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            print(f"  [镜头{req.clip_index}] 任务已创建 [HappyHorse T2V] -> task_id: {task_id}")
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
            print(f"  [镜头{req.clip_index}] [!] HappyHorse 需要首帧图片")
            return None

        # HappyHorse 只支持 first_frame
        media = [{"type": "first_frame", "url": f"data:image/png;base64,{first_frame_b64}"}]

        kwargs: dict = {
            "model": model,
            "prompt": req.prompt,
            "media": media,
            "resolution": self._resolution,  # 必须是 720P 或 1080P
            "duration": self._duration,
            "watermark": True,
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        print(f"  [镜头{req.clip_index}] [HappyHorse I2V] 正在提交任务 [{model}]...")
        print(f"    prompt: {req.prompt[:60]}...")
        print(f"    首帧: {req.image_path}")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            print(f"  [镜头{req.clip_index}] 任务已创建 [HappyHorse I2V] -> task_id: {task_id}")
            return task_id

        print(f"  [镜头{req.clip_index}] [!] HappyHorse 模型错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}")
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
            print(f"  [镜头{req.clip_index}] [!] Wan 需要首帧或尾帧图片")
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
            "duration": self._duration,
            "watermark": False,
        }

        if req.seed is not None:
            kwargs["seed"] = req.seed

        print(f"  [镜头{req.clip_index}] [Wan I2V] 正在提交任务 [{model}]...")
        print(f"    prompt: {req.prompt[:60]}...")
        if first_frame_b64:
            print(f"    首帧: {req.image_path}")
        if last_frame_b64:
            print(f"    尾帧: {req.last_frame_image_path}")

        rsp = VideoSynthesis.async_call(**kwargs)

        if rsp.status_code == 200:
            task_id = rsp.output["task_id"]
            print(f"  [镜头{req.clip_index}] 任务已创建 [Wan I2V] -> task_id: {task_id}")
            return task_id

        print(f"  [镜头{req.clip_index}] [!] Wan 模型错误 [{rsp.status_code}]: {rsp.code} - {rsp.message}")
        return None

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
            print(f"  [镜头{req.clip_index}] 任务状态: {status} (已等待 {int(time.time() - start_time)}s)")

            # status: PENDING, RUNNING, SUCCEEDED, FAILED
            if status == "SUCCEEDED":
                video_url = rsp.output.video_url
                # 打印计费信息
                if hasattr(rsp.output, 'usage') and rsp.output.usage:
                    print(f"  [镜头{req.clip_index}] 计费信息: {rsp.output.usage}")
                # 下载视频
                return self._download(req, video_url)

            if status == "FAILED":
                err_code = rsp.output.code if hasattr(rsp.output, 'code') else "unknown"
                err_msg = rsp.output.message if hasattr(rsp.output, 'message') else "unknown"
                print(f"  [镜头{req.clip_index}] 任务失败! 错误码: {err_code}, 错误信息: {err_msg}")
                print(f"  [镜头{req.clip_index}] 完整响应: {rsp.output}")
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

        print(f"  [镜头{req.clip_index}] 下载视频: {video_url}")

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
            print(f"  [镜头{req.clip_index}] 视频已保存 -> {save_path}")
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=True,
                save_path=str(save_path),
            )
        except Exception as e:
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