"""DashScope 视频生成 — HTTP 直连版（从 waoowaoo 适配）

使用原始 HTTP 代替 dashscope SDK，支持:
- KF2V 首尾帧模式 (wan2.2-kf2v-flash)
- 普通 i2v 模式 (wan2.1-i2v-plus, wan2.7-i2v)
- 异步任务提交 + 轮询
- results 数组格式支持
- 403 自动回退
"""

import os
import time
import base64
from typing import Optional

import requests

from .models import VideoGenerationRequest, VideoGenerationResult
from .service import VideoGenerationService
from .config import config

from shared.log_utils import verbose_write as _verbose_write


# ===================================================================
# 端点与常量（从 waoowaoo 源码提取）
# ===================================================================

# 普通 i2v 端点
I2V_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
# KF2V 首尾帧端点
KF2V_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/image2video/video-synthesis"
# 任务查询端点
TASK_QUERY_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/tasks"

# 支持首尾帧的模型
KF2V_CAPABLE_MODELS = {"wan2.2-kf2v-flash", "wanx2.1-kf2v-plus", "wan2.7-i2v"}
# 仅首尾帧模式的模型（不支持普通 i2v）
KF2V_ONLY_MODELS = {"wan2.2-kf2v-flash", "wanx2.1-kf2v-plus"}


def _is_first_last_frame(req: VideoGenerationRequest) -> bool:
    """判断是否使用首尾帧模式。"""
    return bool(req.last_frame_image_path) and bool(req.image_path)


def _model_supports_kf2v(model: str) -> bool:
    return model in KF2V_CAPABLE_MODELS


def _model_is_kf2v_only(model: str) -> bool:
    return model in KF2V_ONLY_MODELS


def _upload_image(image_path: str) -> str:
    """将本地图片转为 data URL（base64）。"""
    if not image_path or not os.path.exists(image_path):
        raise FileNotFoundError(f"图片不存在: {image_path}")
    with open(image_path, "rb") as f:
        img_data = f.read()
    ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    b64 = base64.b64encode(img_data).decode("ascii")
    return f"data:image/{ext};base64,{b64}"


def _build_submit_body(req: VideoGenerationRequest, model: str) -> dict:
    """构建视频提交请求体。"""
    use_kf2v = _is_first_last_frame(req) and _model_supports_kf2v(model)

    # 校验：KF2V-only 模型必须有尾帧
    if _model_is_kf2v_only(model) and not req.last_frame_image_path:
        raise ValueError(
            f"模型 {model} 仅支持首尾帧模式，需要提供 last_frame_image_path"
        )

    # 构建 input
    if use_kf2v:
        input_data = {
            "first_frame_url": _upload_image(req.image_path),
            "last_frame_url": _upload_image(req.last_frame_image_path),
        }
    elif req.image_path:
        input_data = {
            "img_url": _upload_image(req.image_path),
        }
    else:
        input_data = {
            "img_url": None,  # 纯文本到视频？DashScope 不支持
        }
        raise ValueError("需要提供 image_path（DashScope 视频生成需要首帧图片）")

    if req.prompt:
        input_data["prompt"] = req.prompt

    # 参数
    params = {}
    size = (req.size or config.VIDEO_SIZE).replace("x", "*")
    if size:
        params["size"] = size
    if config.DASHSCOPE_VIDEO_DURATION:
        params["duration"] = config.DASHSCOPE_VIDEO_DURATION
    params["prompt_extend"] = True

    body = {"model": model, "input": input_data}
    if params:
        body["parameters"] = params

    return body


def _parse_submit_response(res: requests.Response) -> str:
    """解析提交响应，返回 task_id。"""
    data = res.json()
    if not res.ok:
        code = (data.get("code") or "") if isinstance(data, dict) else ""
        message = (data.get("message") or "") if isinstance(data, dict) else ""
        raise RuntimeError(
            f"视频提交失败 ({res.status_code}): {code} {message}".strip()
        )

    task_id = ""
    if isinstance(data, dict):
        output = data.get("output") or {}
        task_id = (output.get("task_id") or "") if isinstance(output, dict) else ""

    if not task_id:
        raise RuntimeError("提交成功但未获取到 task_id")
    return task_id


def _read_media_url(data: dict) -> Optional[str]:
    """从任务查询响应中提取媒体 URL（适配多种响应格式）。"""
    output = data.get("output") or {}

    # 直接字段
    if isinstance(output, dict):
        for key in ("video_url", "image_url"):
            val = output.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    # results 数组
    results = output.get("results") if isinstance(output, dict) else None
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            for key in ("video_url", "image_url", "url"):
                val = first.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()

    return None


def _poll_task(task_id: str, api_key: str, clip_index: int) -> VideoGenerationResult:
    """轮询任务直到完成。"""
    url = f"{TASK_QUERY_ENDPOINT}/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(1, config.DASHSCOPE_MAX_POLL_ATTEMPTS + 1):
        time.sleep(config.DASHSCOPE_POLL_INTERVAL)

        try:
            res = requests.get(url, headers=headers, timeout=30)
            if not res.ok:
                _verbose_write(f"  [镜头{clip_index}] 查询失败 ({res.status_code})，重试...")
                continue

            data = res.json()
            if not isinstance(data, dict):
                continue

            # 读取状态（支持多级结构）
            task_status = ""
            output = data.get("output") or {}
            if isinstance(output, dict):
                task_status = (output.get("task_status") or "").upper()
            if not task_status:
                task_status = (data.get("task_status") or "").upper()

            _verbose_write(f"  [镜头{clip_index}] 生成中... ({task_status})")

            if task_status in ("FAILED", "CANCELED", "CANCELLED"):
                # 读取错误码
                code = ""
                if isinstance(output, dict):
                    code = output.get("code") or output.get("message") or ""
                if not code and isinstance(data, dict):
                    code = data.get("code") or data.get("message") or ""
                return VideoGenerationResult(
                    clip_index=clip_index,
                    success=False,
                    error=f"视频生成失败: {code or '未知错误'}",
                )

            if task_status in ("SUCCEEDED", "SUCCESS"):
                media_url = _read_media_url(data)
                if not media_url:
                    return VideoGenerationResult(
                        clip_index=clip_index,
                        success=False,
                        error="任务完成但未找到视频 URL",
                    )
                return _download_video(media_url, clip_index)

        except requests.RequestException as e:
            _verbose_write(f"  [镜头{clip_index}] 查询异常: {e}")
            continue

    return VideoGenerationResult(
        clip_index=clip_index,
        success=False,
        error=f"轮询超时 ({config.DASHSCOPE_MAX_POLL_ATTEMPTS} 次)",
    )


def _download_video(video_url: str, clip_index: int) -> VideoGenerationResult:
    """下载视频到本地。"""
    try:
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        save_path = os.path.join(config.OUTPUT_DIR, f"clip_{clip_index}.mp4")

        resp = requests.get(video_url, timeout=120)
        resp.raise_for_status()
        video_bin = resp.content
        if len(video_bin) < 20 * 1024:
            raise RuntimeError(f"下载内容过小 ({len(video_bin)} bytes)，可能是错误页面")
        with open(save_path, "wb") as f:
            f.write(video_bin)

        size_mb = len(video_bin) / (1024 * 1024)
        _verbose_write(f"  [镜头{clip_index}] [OK] 已保存 -> {save_path} ({size_mb:.1f} MB)")
        return VideoGenerationResult(
            clip_index=clip_index,
            success=True,
            save_path=save_path,
        )
    except Exception as e:
        return VideoGenerationResult(
            clip_index=clip_index,
            success=False,
            error=f"下载失败: {e}",
        )


# ===================================================================
# HTTP 视频服务（替代 SDK 版）
# ===================================================================

class DashScopeHttpVideoService(VideoGenerationService):
    """基于原始 HTTP 的 DashScope 视频生成（从 waoowaoo 适配）。

    相比 SDK 版本的优势：
    - 支持 KF2V 首尾帧模式
    - 更全面的错误码提取
    - 支持 results 数组格式的响应
    - 支持 data URL 上传图片（无需公网 URL）
    - 更清晰的日志输出
    """

    def __init__(self) -> None:
        config.validate()
        self._model_queue: list[str] = (
            [config.DASHSCOPE_VIDEO_MODEL] + config.DASHSCOPE_VIDEO_FALLBACK_MODELS
        )
        self._current_model_index: int = 0

    @property
    def _current_model(self) -> str:
        return self._model_queue[self._current_model_index]

    def _advance_model(self) -> Optional[str]:
        if self._current_model_index + 1 >= len(self._model_queue):
            return None
        self._current_model_index += 1
        return self._current_model

    def create_task(self, req: VideoGenerationRequest) -> str:
        while True:
            model = self._current_model
            use_kf2v = _is_first_last_frame(req)

            # 选择端点
            if use_kf2v and _model_supports_kf2v(model):
                endpoint = KF2V_ENDPOINT
            else:
                endpoint = I2V_ENDPOINT

            body = _build_submit_body(req, model)

            mode_desc = "KF2V首尾帧" if use_kf2v else "i2v"
            _verbose_write(f"  [镜头{req.clip_index}] [{mode_desc} -> {model}] "
                  f"{req.prompt[:200]}{'...' if len(req.prompt) > 200 else ''}")

            try:
                res = requests.post(
                    endpoint,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
                        "Content-Type": "application/json",
                        "X-DashScope-Async": "enable",
                    },
                    timeout=60,
                )

                if res.status_code == 403:
                    next_model = self._advance_model()
                    if next_model:
                        _verbose_write(f"  [镜头{req.clip_index}] [!] {model} 403，切换到 {next_model}")
                        continue
                    raise RuntimeError(
                        f"所有模型均返回 403: {', '.join(self._model_queue)}"
                    )

                task_id = _parse_submit_response(res)
                _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 -> task_id: {task_id}")
                return task_id

            except requests.RequestException as e:
                raise RuntimeError(f"HTTP 请求异常: {e}")

    def query_task(self, task_id: str) -> dict:
        url = f"{TASK_QUERY_ENDPOINT}/{task_id}"
        headers = {"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"}
        res = requests.get(url, headers=headers, timeout=30)
        if not res.ok:
            raise RuntimeError(f"查询任务失败: {res.status_code}")
        return res.json()

    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str
    ) -> VideoGenerationResult:
        return _poll_task(task_id, config.DASHSCOPE_API_KEY, req.clip_index)
