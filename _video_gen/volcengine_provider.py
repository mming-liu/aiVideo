"""火山引擎方舟 (Volcengine Ark) 视频生成 — Seedance 系列.

异步任务协议:
  POST {base}/contents/generations/tasks        # 创建任务 -> {id}
  GET  {base}/contents/generations/tasks/{id}   # 轮询 -> status / content.video_url

Auth: Authorization: Bearer {VOLCENGINE_API_KEY}

支持:
- T2V (文生视频): content 仅 text
- I2V (图生视频): content 含 first_frame / last_frame 图片
- 多关键帧/参考图: role=reference_image（首帧/尾帧优先占用 first_frame/last_frame）
- R2V 参考音频: role=reference_audio

官方文档: 火山方舟 视频生成 API
https://www.volcengine.com/docs/82379/1520758
"""

import base64
import mimetypes
import os
import re
import time
from functools import partial
from pathlib import Path
from typing import Optional

import requests

from .models import VideoGenerationRequest, VideoGenerationResult
from .service import VideoGenerationService
from .config import config
from shared.model_caps import (
    _model_prompt_cap,
    clamp_duration,
    resolve_resolution,
)
from shared.config import (
    VOLCENGINE_API_KEY,
    VOLCENGINE_BASE_URL,
    VOLCENGINE_T2V_MODEL,
    VOLCENGINE_T2V_FALLBACK_MODELS,
    VOLCENGINE_I2V_MODEL,
    VOLCENGINE_I2V_FALLBACK_MODELS,
    VOLCENGINE_VIDEO_POLL_INTERVAL,
    VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS,
    VOLCENGINE_VIDEO_RESOLUTION,
    VOLCENGINE_VIDEO_RATIO,
    VOLCENGINE_VIDEO_DURATION,
)
from shared.constants import VOLCENGINE_VIDEO_SUBMIT_RETRIES
from shared.log_utils import verbose_write

_verbose_write = partial(verbose_write, log_file="step02b.log")

def _append_audio(req: VideoGenerationRequest, content: list[dict], model: str) -> None:
    """追加参考音频（role=reference_audio）到 content.

    按模型能力门控：Seedance 1.0/1.5 不支持参考音频，传入可能 400。
    仅 2.0+/2.5 系列支持（参考 model_caps.supports_reference_audio）。
    """
    if not req.reference_audio:
        return
    from shared.model_caps import get_seedance_cap
    cap = get_seedance_cap(model)
    if not getattr(cap, "supports_reference_audio", False):
        _verbose_write(f"  [镜头{req.clip_index}] [WARN] {model} 不支持 reference_audio，"
              f"已忽略参考音频参数")
        return
    url = _encode_media(req.reference_audio)
    if url:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": url},
            "role": "reference_audio",
        })


# 权重等级（数字越小越优先保留）
_SENT_WEIGHT_HIGH = 0   # 高权重：ff/motion/lf 三段、角色锚定、空间物体、硬约束
_SENT_WEIGHT_MID = 1    # 中等：场景、人物站位、人物一致性
_SENT_WEIGHT_LOW = 2    # 低权重：色彩氛围、视觉衔接、空镜约束、风格收束

# 关键标签：识别高/低权重段
_HIGH_KEYWORDS = (
    "【视频起点】", "【视频中段】", "【视频终点】", "【角色锚定】", "【空间物体】",
    "【道具锚定】", "【人物站位清单】", "【场景环境】", "保持", "必须", "禁止", "严禁",
    "锁定五官", "不能", "不得",
)
_LOW_KEYWORDS = (
    "【色彩氛围】", "视觉衔接", "画面无任何字幕", "整段视频每一帧都保持",
    "禁止在画面中叠加", "禁止任何人物", "连续接续", "风格收束",
)


def _classify_sentence_weight(sent: str) -> int:
    """给单句打权重：基于关键词+标签+位置.

    优先级 0=高（必保留）/ 1=中（尽量保留）/ 2=低（超长时优先砍）
    """
    s = sent.strip()
    if not s:
        return _SENT_WEIGHT_LOW
    # 高权重关键词
    if any(k in s for k in _HIGH_KEYWORDS):
        return _SENT_WEIGHT_HIGH
    # 低权重关键词
    if any(k in s for k in _LOW_KEYWORDS):
        return _SENT_WEIGHT_LOW
    return _SENT_WEIGHT_MID


def _seedance_compress_prompt(full_prompt: str, max_chars: int,
                              ref_tags: list[str] | None = None) -> str:
    """Seedance prompt 智能压缩（避免静默截断丢关键约束）.

    关键事实（2026-08 火山方舟官方）：Seedance 视频接口硬约束中文有效 prompt
    上限 ≈ 500 字符（注册表 _model_prompt_cap），**超长部分静默截断不报错**；
    @图片引用标记不计入字符。
    粗暴 req.prompt[:500] 尾部截断会砍掉 prompt 末尾的关键约束（"动作连贯
    性/风格收束/视觉衔接/r2v 参考图清单"），导致角色漂移、抽卡暴涨等 bug。

    本函数做"语义优先"压缩：
    1. 剥离 @图N / @图片1 等引用标记（不计入 max_chars）
    2. 按中文标点（。！？；\n）切分句子
    3. 给每句打权重（高=必保留/中=尽量保留/低=可砍）
    4. 按原顺序累积到 max_chars 停止（不砍半句）
    5. ref_tags 拼在最前面（视频模型先看到参考图引用）

    Args:
        full_prompt: 完整 prompt 字符串
        max_chars: 正文最大字符数（不含 ref_tags；由调用方按 cap 动态推导）
        ref_tags: 已抽取的 @引用 标记列表；None 时自动从 full_prompt 抽
    Returns:
        压缩后的 prompt（ref_tags 在前 + 保留句子按原顺序）
    """
    if not full_prompt:
        return full_prompt

    # 1. 剥离 @图N / @图片1 等引用标记
    if ref_tags is None:
        # 匹配 @ 后跟非空白的连续字符（支持中文、空格前截止）
        ref_tags = re.findall(r"@\S+", full_prompt)
    content = re.sub(r"@\S+", "", full_prompt)
    content = content.strip()
    if not content:
        # 全部是 @引用，直接返回
        return " ".join(ref_tags)

    # 2. 按中文标点切分（保留分隔符，句级粒度不砍半句）
    # 正则：句末标点（。！？；）+ 换行/段分隔
    parts = re.split(r"(?<=[。！？；\n])\s*", content)
    sentences = [p.strip() for p in parts if p.strip()]

    # 3. 给每句打权重
    weighted = [(i, _classify_sentence_weight(s), s) for i, s in enumerate(sentences)]

    # 4. 两阶段累积：先保证所有高权重段必入选（无论 max_chars 多紧），
    # 再按原顺序填充中等权重段，最后用剩余空间装低权重段。
    # 原算法按 weight+orig_idx 排序后累积，会让低权重（原 idx 小）先填满、
    # 高权重（原 idx 大）因剩余空间不够被砍——修复后高权重强制保留。
    high_idx = [i for i, w, _ in weighted if w == _SENT_WEIGHT_HIGH]
    mid_idx = [i for i, w, _ in weighted if w == _SENT_WEIGHT_MID]
    low_idx = [i for i, w, _ in weighted if w == _SENT_WEIGHT_LOW]
    # 5. 累积字符到 max_chars：按"高权重全选 → 中权重按原顺序填剩余 → 低权重收尾"
    # 分两阶段：第一阶段高+中（按原顺序累积），超出 max_chars 的中权重段跳过
    selected: list[int] = []
    total = 0
    for i in high_idx + mid_idx:
        slen = len(sentences[i])
        if total + slen > max_chars:
            continue
        selected.append(i)
        total += slen
    # 第二阶段：剩余空间装低权重（按原顺序）
    for i in low_idx:
        slen = len(sentences[i])
        if total + slen > max_chars:
            continue
        selected.append(i)
        total += slen

    # 6. 按原顺序输出保留的句子
    selected_sorted = sorted(set(selected))
    kept = [sentences[i] for i in selected_sorted]
    body = "".join(kept)

    # 7. ref_tags 拼在最前面（不计入 max_chars）
    if ref_tags:
        return " ".join(ref_tags) + " " + body
    return body


_STATUS_SUCCEEDED = ("succeeded", "success", "done", "completed")
# 官方 status 可选值：queued/running/cancelled/succeeded/failed/expired。
# expired=任务超时被服务端自动终止，也是终态失败（此前漏判，会傻轮询到超时）
_STATUS_FAILED = ("failed", "cancelled", "error", "expired")
# _submit 内部重试上限（429 限速/5xx 抖动/网络异常），与 403 回退机制正交。
# 403 由 _create_t2v_task/_create_i2v_task 切下一个回退模型，不消耗本计数器。
_SUBMIT_MAX_RETRIES = VOLCENGINE_VIDEO_SUBMIT_RETRIES  # 单源 shared/constants.py
# 轮询查询的瞬态错误容忍次数（查询接口 5xx/网络异常连续发生 N 次才判失败，
# 不再每次计入 attempts；与 VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS 配合使用）
_POLL_TRANSIENT_TOLERANCE = 5


def _encode_media(path: Optional[str]) -> Optional[str]:
    """读取本地文件，返回 base64 data URL（Seedance 接受 data URI）."""
    if not path:
        return None
    try:
        p = os.path.abspath(path)
        if not os.path.isfile(p):
            return None
        mime, _ = mimetypes.guess_type(p)
        if not mime:
            mime = "image/png"
        with open(p, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


# _clamp_duration / _RES_ORDER / _resolve_resolution 已上移 shared/model_caps.py
# （clamp_duration / resolve_resolution，volcengine 与 agent_plan 共用单实现）。


def _resolve_ratio(req: VideoGenerationRequest, cap, is_first_last_frames: bool = False) -> str:
    """按模型能力推导 ratio.

    Seedance 2.5 在首帧/首尾帧场景仅支持 adaptive（输出自动保持首帧宽高比），
    此为硬约束、不可被 override；其余模型/场景：VOLCENGINE_VIDEO_RATIO 非空则强制，
    否则按 req.size（如 720x1280）推导 1:1 / 16:9 / 9:16。
    """
    if cap.i2v_frames_ratio_adaptive and is_first_last_frames:
        return "adaptive"
    override = (VOLCENGINE_VIDEO_RATIO or "").strip().lower()
    if override in ("16:9", "9:16", "1:1", "adaptive"):
        return override
    size = (req.size or "").strip().lower().replace("*", "x")
    try:
        w, h = size.split("x")
        w, h = int(w), int(h)
    except (ValueError, AttributeError):
        return "9:16"
    if w == h:
        return "1:1"
    return "16:9" if w > h else "9:16"


class VolcengineVideoService(VideoGenerationService):
    """火山方舟 Seedance 视频生成服务.

    支持:
    - T2V (text-to-video)
    - I2V (image-to-video, first_frame / first_frame + last_frame)
    - 多关键帧/参考图 (extra_keyframe_paths / reference_paths -> reference_image)
    - 参考音频 (reference_audio -> reference_audio)

    403 自动回退: T2V / I2V 各自模型队列 + 回退列表.
    """

    def __init__(self) -> None:
        config.validate()
        if not VOLCENGINE_API_KEY:
            raise ValueError(
                "没有配置 VOLCENGINE_API_KEY（.env.keys 中设置，兼容 ARK_API_KEY）"
            )
        self._t2v_model_queue: list[str] = [VOLCENGINE_T2V_MODEL] + VOLCENGINE_T2V_FALLBACK_MODELS
        self._i2v_model_queue: list[str] = [VOLCENGINE_I2V_MODEL] + VOLCENGINE_I2V_FALLBACK_MODELS
        self._base_url = VOLCENGINE_BASE_URL.rstrip("/")

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {VOLCENGINE_API_KEY}",
            "Content-Type": "application/json",
        }

    # =======================================================================
    # 创建任务
    # =======================================================================
    def create_task(self, req: VideoGenerationRequest) -> str:
        # 判断模式：有参考图/首尾帧走 I2V 队列，否则 T2V 队列
        has_refs = bool(req.reference_paths or req.extra_keyframe_paths)
        has_frames = bool(req.image_path or req.last_frame_image_path)
        if has_refs or has_frames:
            return self._create_i2v_task(req)
        return self._create_t2v_task(req)

    def _build_content(self, req: VideoGenerationRequest, model: str) -> list[dict]:
        """构建 Seedance content 数组（text 在前，媒体在后，按场景语义分配 role）.

        官方文档规定 3 种场景互斥、不可混用（创建视频生成任务 API）：
        - 全模态参考生视频：role=reference_image（仅 Seedance 2.5 / 2.0 系列支持）
        - 图生视频-多关键帧：能力支持时全部 reference_image（prompt 用「图N」
          指代时序，实现 A→B→C→D）；不支持时退化为首尾两帧
        - 图生视频-首帧/首尾帧：role=first_frame / last_frame（所有 Seedance 支持）
        """
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        # v6.58 (Phase C-1): 结构化 camera_movement 字段提到 prompt 最前，提升权重。
        # 精简路径（_build_seedance_concise_prompt）已带"运镜：XXX"段，自动跳过；
        # 完整 prompt 路径才会真正注入。
        text = req.prompt
        if req.camera_movement and "运镜：" not in text[:20]:
            text = f"运镜：{req.camera_movement}\n{text}"
        content: list[dict] = [{"type": "text", "text": text}]

        def _append_image(path, role):
            url = _encode_media(path)
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}, "role": role})

        # ---- 全模态参考生视频（r2v）：reference_paths → reference_image ----
        if req.reference_paths:
            if not cap.supports_reference_image:
                raise RuntimeError(
                    f"{model} 不支持参考生视频（role=reference_image，仅 Seedance 2.5/2.0 系列）。"
                    f"r2v 模式请将 VOLCENGINE_I2V_MODEL 设为 2.0/2.5 模型"
                )
            from shared.ref_compose import get_ref_limit, fit_flat
            ref_limit = get_ref_limit(model)
            ref_paths = [p for p in (req.reference_paths or []) if p and Path(p).exists()]
            if len(ref_paths) > ref_limit:
                from shared.paths import shot_work_dir
                sheet_dir = shot_work_dir(req.clip_index)
                ref_paths = fit_flat(ref_paths, ref_limit, sheet_dir, f"r2v{req.clip_index}")
                _verbose_write(f"  [镜头{req.clip_index}] 参考图 {len(req.reference_paths)} 张超上限 "
                      f"{ref_limit}，无损压缩为 {len(ref_paths)} 张")
            for ref_path in ref_paths:
                _append_image(ref_path, "reference_image")
            _append_audio(req, content, model)
            return content

        # ---- 多关键帧：能力支持→全部 reference_image（全模态）；否则退化为首尾帧 ----
        kfs = [p for p in (req.extra_keyframe_paths or []) if p and Path(p).exists()]
        if kfs:
            if cap.supports_reference_image:
                for p in kfs:
                    _append_image(p, "reference_image")
                _append_audio(req, content, model)
                return content
            if len(kfs) > 2:
                _verbose_write(f"  [镜头{req.clip_index}] {model} 不支持参考生视频，"
                      f"多关键帧退化为首尾帧（丢弃中间 {len(kfs) - 2} 张）")
                kfs = [kfs[0], kfs[-1]]

        # ---- 首帧/首尾帧（所有 Seedance 模型支持）----
        if kfs:
            _append_image(kfs[0], "first_frame")
            if len(kfs) > 1:
                _append_image(kfs[-1], "last_frame")
        else:
            _append_image(req.image_path, "first_frame")
            _append_image(req.last_frame_image_path, "last_frame")
        _append_audio(req, content, model)
        return content

    def _submit(self, req: VideoGenerationRequest, model: str) -> str:
        """提交单个任务，返回 task_id；失败抛异常."""
        from shared.model_caps import get_seedance_cap
        cap = get_seedance_cap(model)
        # Seedance prompt 智能压缩（2026-08）：硬约束中文有效 prompt ≤500 字符，
        # 超长静默截断不报错。粗暴 [:cap_len] 尾部截断会砍掉末尾关键约束
        # （动作连贯性/风格收束/视觉衔接/r2v 参考图清单），导致角色漂移、
        # 抽卡暴涨等诡异 bug。改为按权重剥离 @图引用 + 删中段低权重句子。
        # 压缩触发阈值由注册表 cap 现算：cap - 20字@图预留 - 20字安全余量
        # （cap=500 时即历史硬编码的 460；cap 变化时阈值自动跟随）
        cap_len = _model_prompt_cap(model)
        if cap_len and len(req.prompt) > cap_len - 40:
            ref_tags = re.findall(r"@\S+", req.prompt)
            # 正文目标 = cap - ref_tags 长度 - 20 余量
            ref_len = sum(len(t) + 1 for t in ref_tags)
            target_body = max(200, cap_len - ref_len - 20)
            compressed = _seedance_compress_prompt(
                req.prompt, max_chars=target_body, ref_tags=ref_tags
            )
            if len(compressed) < len(req.prompt):
                _verbose_write(
                    f"  [镜头{req.clip_index}] [INFO] Seedance 智能压缩 prompt "
                    f"{len(req.prompt)}→{len(compressed)} 字符 "
                    f"（@图引用 {len(ref_tags)} 个，正文目标 {target_body}）"
                )
            req.prompt = compressed
        # 兜底：万一压缩后还超模型 cap（极端情况），再硬截断
        if cap_len and len(req.prompt) > cap_len:
            _verbose_write(
                f"  [镜头{req.clip_index}] [WARN] {model} 压缩后仍超 cap {cap_len}，"
                f"硬截断兜底"
            )
            req.prompt = req.prompt[:cap_len]
        # 首帧/首尾帧场景（图生视频-首帧/首尾帧，非全模态参考）：用于 2.5 ratio adaptive 判定
        is_first_last_frames = bool(req.image_path or req.last_frame_image_path) \
            and not req.reference_paths and not req.extra_keyframe_paths
        payload: dict = {
            "model": model,
            "content": self._build_content(req, model),
            "resolution": resolve_resolution(req.size, cap, VOLCENGINE_VIDEO_RESOLUTION),
            "ratio": _resolve_ratio(req, cap, is_first_last_frames),
            "duration": clamp_duration(VOLCENGINE_VIDEO_DURATION or req.duration, cap),
            "watermark": False,
        }
        # 有声视频：按 VIDEO_MODEL_VOICE 决定是否下发 generate_audio。
        # VIDEO_MODEL_VOICE=1（默认）→ 对话/背景音交给视频模型，开；
        # =0 → 语音走 step03 TTS，prompt_builder 已写「禁止任何人声」，必须关掉
        # 模型发声，否则人声 + step03 TTS 双重语音（与 prompt_builder _MODEL_VOICE 一致）。
        # 不支持的模型（1.0 系列）不传该字段（避免参数报错）。
        if cap.supports_generate_audio:
            voice_enabled = os.getenv("VIDEO_MODEL_VOICE", "1").strip() == "1"
            payload["generate_audio"] = voice_enabled
        if req.seed is not None and getattr(cap, "supports_seed", False):
            payload["seed"] = req.seed

        _verbose_write(f"  [镜头{req.clip_index}] [Seedance] 正在提交任务 [{model}]...")
        _verbose_write(f"[DEBUG] video_submit: model={model}, "
              f"resolution={payload['resolution']}, ratio={payload['ratio']}, "
              f"duration={payload['duration']}, seed={req.seed}")
        _verbose_write(f"[DEBUG] video_submit: prompt全文={req.prompt}")
        for i, item in enumerate(payload["content"]):
            if item["type"] == "text":
                _verbose_write(f"[DEBUG] video_submit: content[{i}] text")
            else:
                u = item.get("image_url") or item.get("audio_url") or {}
                raw = str(u.get("url", ""))
                _verbose_write(f"[DEBUG] video_submit: content[{i}] {item['type']} "
                      f"role={item.get('role')}, len={len(raw)}")

        # 提交重试：429 限速按服务端 retry-after 等待；5xx/网络异常按指数退避。
        # 重试耗尽抛 RuntimeError，由调用方（_create_t2v_task/_create_i2v_task）
        # 决定是否切下一个回退模型。403/400/404 不重试（403 由外层回退处理）。
        from shared.rate_limit import compute_429_wait
        last_error = ""
        backoff = 2.0
        for attempt in range(1, _SUBMIT_MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    f"{self._base_url}/contents/generations/tasks",
                    json=payload,
                    headers=self._headers,
                    timeout=180,
                )
            except requests.exceptions.RequestException as e:
                last_error = f"提交网络异常: {e}"
                _verbose_write(f"  [镜头{req.clip_index}] [!] {last_error}，"
                      f"{backoff:.0f}s 后重试 (尝试 {attempt}/{_SUBMIT_MAX_RETRIES})")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            _verbose_write(f"[DEBUG] video_submit response: status={resp.status_code}, "
                  f"body={resp.text[:500]}")

            # ---- 200: 成功 ----
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as e:
                    raise RuntimeError(f"Seedance 响应 JSON 解析失败: {e} ({resp.text[:200]})")
                task_id = data.get("id") or data.get("task_id")
                if not task_id:
                    raise RuntimeError(f"Seedance 未返回 task_id: {resp.text[:300]}")
                _verbose_write(f"  [镜头{req.clip_index}] 任务已创建 [Seedance] -> task_id: {task_id}")
                return str(task_id)

            # ---- 429: 限速，按服务端 retry-after 等待 ----
            if resp.status_code == 429:
                wait = compute_429_wait(resp.text, fallback_seconds=60.0, backoff_seconds=2.0)
                _verbose_write(f"  [镜头{req.clip_index}] [!] 429 限速，等待 {wait:.0f}s 后重试 "
                      f"(尝试 {attempt}/{_SUBMIT_MAX_RETRIES})")
                time.sleep(wait)
                continue

            # ---- 5xx: 服务端故障，指数退避 ----
            if 500 <= resp.status_code < 600:
                _verbose_write(f"  [镜头{req.clip_index}] [!] 服务端 {resp.status_code}，"
                      f"{backoff:.0f}s 后重试 (尝试 {attempt}/{_SUBMIT_MAX_RETRIES})")
                last_error = f"服务端 {resp.status_code}: {resp.text[:200]}"
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                continue

            # ---- 400/403/404 等：客户端问题，不重试（403 由外层回退处理）----
            break

        # 所有重试耗尽或命中客户端错误码
        status = resp.status_code if 'resp' in locals() else '?'
        body = resp.text[:500] if 'resp' in locals() else last_error
        raise RuntimeError(f"Seedance API 错误 [{status}]: {body}")

    def _create_t2v_task(self, req: VideoGenerationRequest) -> str:
        last_error = ""
        for model in self._t2v_model_queue:
            try:
                return self._submit(req, model)
            except RuntimeError as e:
                err = str(e)
                if "403" in err:
                    _verbose_write(f"  [镜头{req.clip_index}] [!] T2V 模型 {model} 返回 403，"
                          f"尝试下一个")
                    last_error = err
                    continue
                raise
        raise RuntimeError(f"T2V 任务创建失败: 所有模型均失败 ({last_error})")

    def _create_i2v_task(self, req: VideoGenerationRequest) -> str:
        from shared.model_caps import get_seedance_cap
        last_error = ""
        # 每次从头遍历：不再用 self._current_i2v_index 跨镜头共享状态
        # （修复并发竞态 + 与 T2V 行为对称）
        for model in self._i2v_model_queue:
            # r2v（reference_paths）能力前置过滤：1.0/1.5 系列不支持参考生视频，
            # 直接跳过，避免 _build_content 抛错中断整个队列
            if req.reference_paths and not get_seedance_cap(model).supports_reference_image:
                last_error = f"{model} 不支持参考生视频（仅 Seedance 2.5/2.0 系列）"
                _verbose_write(f"  [镜头{req.clip_index}] [!] {model} 不支持 r2v 参考图，尝试下一个")
                continue
            try:
                return self._submit(req, model)
            except RuntimeError as e:
                err = str(e)
                if "403" in err:
                    _verbose_write(f"  [镜头{req.clip_index}] [!] I2V 模型 {model} 返回 403，"
                          f"尝试下一个")
                    last_error = err
                    continue
                raise
        # 所有回退模型都不支持 r2v 时 → 自动降级为纯文生视频
        if req.reference_paths and not any(
            get_seedance_cap(m).supports_reference_image for m in self._i2v_model_queue
        ):
            _verbose_write(
                f"  [镜头{req.clip_index}] [!] 所有 I2V 模型均不支持 r2v 参考生视频，"
                f"自动降级为 text-to-video（丢弃 reference_paths）"
            )
            # 构造纯 text 请求：清空 reference_paths 走 T2V 队列
            req_text = VideoGenerationRequest(
                prompt=req.prompt,
                clip_index=req.clip_index,
                duration=req.duration,
                negative_prompt=req.negative_prompt,
                size=req.size,
                image_path=None,
                last_frame_image_path=None,
                extra_keyframe_paths=[],
                reference_paths=[],
                reference_audio=None,
                seed=req.seed,
            )
            return self._create_t2v_task(req_text)
        raise RuntimeError(f"I2V 任务创建失败: 所有模型均失败 ({last_error})")

    # =======================================================================
    # 查询任务
    # =======================================================================
    def query_task(self, task_id: str) -> dict:
        resp = requests.get(
            f"{self._base_url}/contents/generations/tasks/{task_id}",
            headers=self._headers,
            timeout=60,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Seedance 查询失败 [{resp.status_code}]: {resp.text[:300]}")
        return resp.json()

    # =======================================================================
    # 轮询结果
    # =======================================================================
    def wait_for_completion(
        self, req: VideoGenerationRequest, task_id: str, timeout: int | None = None
    ) -> VideoGenerationResult:
        start_time = time.time()
        last_error = ""
        if timeout is None:
            timeout = VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS * VOLCENGINE_VIDEO_POLL_INTERVAL

        attempts = 0
        transient_streak = 0  # 查询瞬态错误（5xx/网络）连续计数；不计入 attempts
        while attempts < VOLCENGINE_VIDEO_MAX_POLL_ATTEMPTS and time.time() - start_time < timeout:
            attempts += 1
            try:
                output = self.query_task(task_id)
            except RuntimeError as e:
                last_error = str(e)
                transient_streak += 1
                if transient_streak >= _POLL_TRANSIENT_TOLERANCE:
                    # 连续 N 次查询失败 → 视为任务异常，结束轮询
                    _verbose_write(
                        f"  [镜头{req.clip_index}] [!] 查询接口连续 {transient_streak} 次失败，"
                        f"放弃轮询: {last_error}"
                    )
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"轮询查询连续失败 ({transient_streak} 次): {last_error}",
                    )
                # 指数退避：2s / 4s / 8s 上限 30s
                wait = min(2.0 ** transient_streak, 30.0)
                _verbose_write(
                    f"  [镜头{req.clip_index}] [!] 查询失败 ({transient_streak}/{_POLL_TRANSIENT_TOLERANCE})，"
                    f"{wait:.0f}s 后重试: {last_error[:100]}"
                )
                time.sleep(wait)
                continue
            # 成功响应 → 重置瞬态计数，按正常间隔继续轮询
            transient_streak = 0

            status = str(output.get("status") or "").lower()
            _verbose_write(f"  [镜头{req.clip_index}] 任务状态: {status} "
                  f"(已等待 {int(time.time() - start_time)}s)")

            if status in _STATUS_SUCCEEDED:
                content = output.get("content") or {}
                video_url = content.get("video_url")
                if not video_url:
                    return VideoGenerationResult(
                        clip_index=req.clip_index,
                        success=False,
                        error=f"任务成功但未找到 video_url: {str(output)[:300]}",
                    )
                if output.get("usage"):
                    _verbose_write(f"  [镜头{req.clip_index}] 计费信息: {output['usage']}")
                return self._download(req, video_url)

            if status in _STATUS_FAILED:
                err = output.get("error") or {}
                code = err.get("code") if isinstance(err, dict) else ""
                msg = err.get("message") if isinstance(err, dict) else str(err)
                _verbose_write(f"  [镜头{req.clip_index}] 任务失败: {code} - {msg}")
                return VideoGenerationResult(
                    clip_index=req.clip_index,
                    success=False,
                    error=f"生成失败: {code} - {msg}",
                )

            # queued / running：按配置间隔继续轮询
            time.sleep(VOLCENGINE_VIDEO_POLL_INTERVAL)

        return VideoGenerationResult(
            clip_index=req.clip_index,
            success=False,
            error=f"轮询超时 ({timeout}s): {last_error}",
        )

    # =======================================================================
    # 下载
    # =======================================================================
    def _download(self, req: VideoGenerationRequest, video_url: str) -> VideoGenerationResult:
        import shutil
        import tempfile
        from shared.paths import shot_video_file

        save_path = shot_video_file(req.clip_index)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        _verbose_write(f"  [镜头{req.clip_index}] 下载视频: {video_url}")
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                tmp_path = tmp.name
            resp = requests.get(video_url, timeout=180)
            resp.raise_for_status()
            video_bin = resp.content
            if len(video_bin) < 20 * 1024:
                raise RuntimeError(f"下载内容过小 ({len(video_bin)} bytes)，可能是错误页面")
            with open(tmp_path, "wb") as f:
                f.write(video_bin)
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
            return VideoGenerationResult(
                clip_index=req.clip_index,
                success=False,
                error=f"下载失败: {e}",
            )
