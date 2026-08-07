"""图片视觉质检 — 封装 ds-vision-skill 的 vision-router.ps1.

在 step02a 帧生成后对首帧/尾帧做内容级质检（人脸/肢体/多余物体/水印/图文一致性），
发现问题时仅告警 + 写 sidecar（默认封顶为 warning，不触发 step02b 自动重生成）。

调用契约（已实测验证）：
    powershell -NoProfile -ExecutionPolicy Bypass -File <router> -Path <img> -Prompt <q> -Intent auto -Json
    - stdout 恒为 JSON（成功/失败都输出 JSON envelope），exit 0=成功 / 1=总失败
    - 成功 envelope 读 result 字段（分析文本）；失败读 metadata.error / metadata.attempts[]
    - env：AGNES_API_KEY（必需）、AGNES_BASE_URL、VISION_TIMEOUT_SEC（Agnes 慢，设 300）

软失败原则：timeout / exit≠0 / 解析失败 → 视为"未检出问题"+ 日志 WARN，绝不阻断流水线。

sidecar 契约（与 step02_video_gen/submit_poll.py 的 _check_keyframe_hallucination 兼容）：
    {"has_hallucination": bool, "issues": [{"category","description","severity"}]}
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# 图片质检配置（与 shared/config.py 保持一致；此处直接读 env，避免循环依赖）
QC_ENABLED = os.getenv("IMAGE_QC_ENABLED", "1").strip() == "1"
QC_TIMEOUT_SEC = int(os.getenv("IMAGE_QC_TIMEOUT_SEC", "300"))
QC_WRITE_CRITICAL = os.getenv("IMAGE_QC_WRITE_CRITICAL", "0").strip() == "1"

# ds-vision-skill router 相对项目根的位置
_ROUTER_REL = Path(".agents") / "skills" / "ds-vision-skill" / "scripts" / "vision-router.ps1"

# 质检中"物理逻辑错误"关键词（检测到时自动提升为 critical，对齐现有 image_hallucination_detector）
_PHYSICS_VIOLATION_KEYWORDS = [
    "漂浮", "半透明", "消散", "透明", "发光光束", "光束", "光环", "融合",
    "穿透", "肢体融合", "身体融合", "人船融合", "人与物融合",
    "半透明漂浮", "不自然的发光", "违反物理", "不符合物理逻辑",
]

# 关键词回退扫描（parse_verdict 解析失败时启用，对齐现有 detector 的 _parse_response fallback）
_FALLBACK_KEYWORDS = ["多指", "畸形", "换脸", "多出", "水印", "半透明", "光束", "漂浮", "融合", "穿透"]

# 有效 category 白名单（对齐现有 detector + subject_consistency 图文一致性）
_VALID_CATEGORIES = {
    "face_consistency", "body_deformity", "unexpected_object",
    "text_watermark", "scene_consistency", "subject_consistency",
}


@dataclass
class QcIssue:
    """质检检出的单条问题."""

    category: str
    description: str
    severity: str  # "critical" | "warning"


@dataclass
class QcResult:
    """单图质检结果."""

    has_hallucination: bool
    issues: list[QcIssue] = field(default_factory=list)
    ok: bool = False  # False=质检本身失败（超时/无路由/解析失败），非图片问题
    raw_result: str = ""
    error: str = ""


# ============================================================================
# 基础定位
# ============================================================================

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def find_router_ps1() -> Path:
    """定位 vision-router.ps1（ds-vision-skill 的入口脚本）."""
    router = _project_root() / _ROUTER_REL
    if not router.exists():
        raise FileNotFoundError(f"ds-vision-skill router 不存在: {router}（未安装？）")
    return router


def find_powershell() -> str:
    """定位 powershell.exe（本机仅 Windows PowerShell 5.1，无 pwsh）."""
    exe = os.getenv("POWERSHELL_EXE", "").strip()
    if exe:
        return exe
    path = shutil.which("powershell")
    if not path:
        raise FileNotFoundError("未找到 powershell.exe，图像质检无法运行")
    return path


def child_env() -> dict:
    """构造传给子进程的 env：继承 os.environ，注入 QC 专属变量.

    - VISION_TIMEOUT_SEC：覆盖 router 默认 90s（Agnes 端点响应慢）
    - AGNES_API_KEY：.env.keys 里裸键未启用（项目用 _9/_10/_11），
      若进程 env 无裸键则注入第一个可用的 AGNES_API_KEY_n 兜底。
    """
    env = dict(os.environ)
    env["VISION_TIMEOUT_SEC"] = str(QC_TIMEOUT_SEC)
    if not env.get("AGNES_API_KEY"):
        for i in range(2, 21):
            key = env.get(f"AGNES_API_KEY_{i}")
            if key:
                env["AGNES_API_KEY"] = key
                break
    return env


# ============================================================================
# QC prompt 构建
# ============================================================================

def _truncate(text: str, limit: int = 220) -> str:
    """截断文本到 limit 字符（用于 prompt 注入，控制 token 量）."""
    if not text:
        return ""
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + "..." if len(text) > limit else text


def _shot_characters(shot: dict, characters: list | None) -> str:
    """提取本镜应出现的人物（镜头内角色 + 全局角色名匹配）."""
    names: list[str] = []
    for key in ("main_subject", "characters", "character"):
        v = shot.get(key)
        if v:
            if isinstance(v, str):
                names.append(v)
            elif isinstance(v, list):
                names.extend(str(x) for x in v)
    if characters:
        for c in characters:
            cid = c.get("id") or c.get("name") if isinstance(c, dict) else c
            if cid:
                names.append(str(cid))
    # 去重保持顺序
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return "、".join(out) if out else "未知"


def build_qc_prompt(shot: dict, frame_kind: str, characters: list | None = None,
                    empty_scene: bool = False) -> str:
    """构建质检 prompt（中文，要求视觉模型只返回严格 JSON）.

    prompt 不含字面双引号或 $（PowerShell -File 参数引号安全），中文安全。
    """
    # 场景 / 主体
    scene = shot.get("scene") or shot.get("location") or ""
    subject = shot.get("main_subject") or ""
    # 画面描述（首帧用 ff_desc，尾帧用 lf_desc）
    desc = shot.get("ff_desc") or shot.get("lf_desc") or shot.get("motion_desc") or ""
    if frame_kind == "last_frame":
        desc = shot.get("lf_desc") or shot.get("ff_desc") or shot.get("motion_desc") or desc
    # 期望道具/关键词
    core_keywords = shot.get("core_keywords") or []
    if isinstance(core_keywords, list):
        core_keywords = "、".join(str(k) for k in core_keywords)
    chars = _shot_characters(shot, characters)

    lines = [
        "你是专业图片质检员，检查这张古风分镜图的视觉问题。",
        "本镜预期内容：",
        f"场景：{_truncate(scene or '未指定', 80)}",
        f"主体：{_truncate(subject or '未指定', 80)}",
        f"应出现人物：{chars}",
        f"应出现道具或关键词：{_truncate(core_keywords or '无', 100)}",
        f"画面描述：{_truncate(desc, 220)}",
        "",
        "检查以下问题类别，只看图里实际存在、明显的问题，不要吹毛求疵：",
        "1. face_consistency 人物五官/脸型异常、扭曲、模糊不清",
        "2. body_deformity 肢体畸形（多指/少指/扭曲/关节错位/比例失调）",
        "3. unexpected_object 多余物体、漂浮/穿透/半透明等违反物理逻辑的现象",
        "4. text_watermark 乱码文字、水印、logo",
        "5. subject_consistency 画面主体人物/场景/道具与预期明显不符",
    ]
    if empty_scene:
        lines.append("6. 本镜是空镜，画面中禁止出现任何人；若出现人物则属于严重问题。")
    lines.extend([
        "",
        "判定规则：",
        "- 物理逻辑错误（漂浮/穿透/半透明/融合）或关键人物缺失或空镜出现人物 → critical",
        "- 轻微瑕疵（背景小物、风格笔触、留白、水墨晕染）→ 不算问题，不得误判",
        "- 该作品是古风水墨风格，水墨、留白、笔触、墨晕均属正常艺术风格",
        "",
        "只返回一个 JSON 对象，不要任何其他内容：",
        '{"has_hallucination":true或false,"issues":[{"category":"类别","description":"描述","severity":"critical或warning"}]}',
        "category 只能从以上列表选；has_hallucination 为 true 当且仅当存在至少一个 critical 或两个以上 warning。",
    ])
    return "\n".join(lines)


# ============================================================================
# 子进程调用
# ============================================================================

def _invoke_router(frame_path: Path, prompt: str) -> subprocess.CompletedProcess:
    """调用 vision-router.ps1（一次图片质检）."""
    cmd = [
        find_powershell(),
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(find_router_ps1()),
        "-Path", str(frame_path.resolve()),
        "-Prompt", prompt,
        "-Intent", "auto",
        "-Json",
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=QC_TIMEOUT_SEC + 40,
        env=child_env(),
    )


# ============================================================================
# 解析
# ============================================================================

def _extract_result_text(stdout: str) -> str:
    """从 router 的 JSON envelope 中提取 result 字段（成功/失败都返回 envelope）."""
    try:
        data = json.loads(stdout)
        if isinstance(data, dict) and data.get("result"):
            return str(data["result"])
    except Exception:
        pass
    return ""


def _map_category(raw: str) -> str:
    """把模型返回的 category 归一化到白名单."""
    raw = (raw or "").strip().lower()
    for cat in _VALID_CATEGORIES:
        if cat in raw:
            return cat
    # 中英文别名兜底
    alias = {
        "人脸": "face_consistency", "五官": "face_consistency", "面部": "face_consistency",
        "肢体": "body_deformity", "手": "body_deformity", "畸形": "body_deformity",
        "多余": "unexpected_object", "物体": "unexpected_object", "物理": "unexpected_object",
        "漂浮": "unexpected_object", "水印": "text_watermark", "文字": "text_watermark",
        "场景": "scene_consistency", "主体": "subject_consistency", "人物": "subject_consistency",
        "一致性": "subject_consistency", "不符": "subject_consistency",
    }
    for k, v in alias.items():
        if k in raw:
            return v
    return "unexpected_object"


def _severity_of(category: str, description: str, raw_severity: str) -> str:
    """确定 severity：物理违规自动升级 critical，其余沿用模型判定."""
    if any(kw in description for kw in _PHYSICS_VIOLATION_KEYWORDS):
        return "critical"
    if category == "unexpected_object" and any(kw in description for kw in ("漂浮", "穿透", "半透明", "融合", "光束")):
        return "critical"
    sev = (raw_severity or "").strip().lower()
    return "critical" if sev in ("critical", "严重", "高") else "warning"


def parse_verdict(text: str) -> QcResult:
    """解析视觉模型返回的文本 → QcResult.

    流程：剥离 fenced code block → json.loads → 映射 issues → 物理违规升级；
    解析失败则关键词回退扫描（对齐现有 image_hallucination_detector._parse_response）。
    """
    if not text or not text.strip():
        return QcResult(has_hallucination=False, issues=[], ok=False,
                        raw_result=text, error="视觉模型返回空结果")

    # 1. 剥离 fenced code block
    content = text.strip()
    if "```" in content:
        parts = content.split("```")
        # fenced 块内部位于奇数索引段；可能带语言标识（如 json），需剥离首行
        for cand in parts:
            cand = cand.strip()
            if cand.startswith("{"):
                content = cand
                break
            # 语言标识 + JSON：去掉首行语言标识再判断
            if cand and "\n" in cand:
                body = cand.split("\n", 1)[1].strip()
                if body.startswith("{"):
                    content = body
                    break
        else:
            content = parts[-1].strip()

    # 2. 尝试 json.loads
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return _result_from_json(data, text)
    except Exception:
        pass

    # 3. 尝试提取 {} 子串（模型可能夹带说明文字）
    try:
        start, end = content.find("{"), content.rfind("}")
        if 0 <= start < end:
            data = json.loads(content[start:end + 1])
            if isinstance(data, dict):
                return _result_from_json(data, text)
    except Exception:
        pass

    # 4. 关键词回退
    text_lower = text.lower()
    has = any(k in text_lower for k in _FALLBACK_KEYWORDS)
    issues = []
    if has:
        severity = "critical" if any(k in text_lower for k in
                                     ("漂浮", "半透明", "光束", "融合", "穿透", "多指", "畸形")) else "warning"
        issues.append(QcIssue(
            category="unexpected_object",
            description="检测到潜在视觉问题（解析失败，关键词匹配）",
            severity=severity,
        ))
    return QcResult(has_hallucination=has, issues=issues, ok=True, raw_result=text,
                    error="" if has else "未检出问题（解析失败但无关键词命中）")


def _result_from_json(data: dict, raw: str) -> QcResult:
    """从已解析的 JSON dict 构建 QcResult."""
    issues: list[QcIssue] = []
    has = bool(data.get("has_hallucination", False))

    for item in data.get("issues", []) or []:
        if not isinstance(item, dict):
            continue
        category = _map_category(item.get("category", ""))
        description = str(item.get("description", "") or "")
        severity = _severity_of(category, description, str(item.get("severity", "warning")))
        issues.append(QcIssue(category=category, description=description, severity=severity))
        if severity == "critical":
            has = True

    # ≥2 warning 视为检出问题（对齐现有判定标准）
    if sum(1 for i in issues if i.severity == "warning") >= 2:
        has = True

    return QcResult(has_hallucination=has, issues=issues, ok=True, raw_result=raw)


# ============================================================================
# sidecar
# ============================================================================

def sidecar_path(frame_path: Path) -> Path:
    """sidecar 路径：<frame_stem>_hallucination.json（与 submit_poll 消费契约一致）."""
    p = Path(frame_path)
    return p.with_name(p.stem + "_hallucination.json")


def should_run_qc(frame_path: Path) -> bool:
    """是否需要对某帧执行质检：总开关开启 + sidecar 缺失或过期."""
    if not QC_ENABLED:
        return False
    try:
        frame = Path(frame_path)
        if not frame.exists():
            return False
        sc = sidecar_path(frame)
        if not sc.exists():
            return True
        # sidecar 比帧旧 → 需要重新质检（帧可能已重生成）
        return sc.stat().st_mtime < frame.stat().st_mtime
    except Exception:
        return False


def write_sidecar(frame_path: Path, result: QcResult) -> Path:
    """写质检 sidecar（submit_poll 兼容格式）.

    IMAGE_QC_WRITE_CRITICAL=0（默认）时所有 issues severity 封顶为 warning，
    避免触发 step02b 的自动删帧重生成；日志仍显示真实 severity。
    """
    sc = sidecar_path(frame_path)
    issues_data = []
    for i in result.issues:
        severity = i.severity
        if not QC_WRITE_CRITICAL and severity == "critical":
            severity = "warning"
        issues_data.append({
            "category": i.category,
            "description": i.description,
            "severity": severity,
        })
    data = {
        "has_hallucination": bool(result.has_hallucination),
        "issues": issues_data,
    }
    sc.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return sc


# ============================================================================
# 顶层 API
# ============================================================================

def qc_image(frame_path: str | Path, prompt: str | None = None,
             shot: dict | None = None, frame_kind: str = "first_frame",
             characters: list | None = None, empty_scene: bool = False) -> QcResult:
    """对单张图片执行视觉质检（同步，软失败不抛异常）.

    Args:
        frame_path: 图片路径
        prompt: 自定义 prompt（不传则由 shot 自动构建）
        shot: 镜头 dict（构建 QC prompt 用）
        frame_kind: first_frame | last_frame
    Returns:
        QcResult：ok=False 表示质检本身失败（超时/无路由/解析失败）
    """
    frame = Path(frame_path)
    if not frame.exists():
        return QcResult(has_hallucination=False, issues=[], ok=False,
                        raw_result="", error=f"图片不存在: {frame}")

    if prompt is None:
        prompt = build_qc_prompt(shot or {}, frame_kind, characters, empty_scene)

    try:
        proc = _invoke_router(frame, prompt)
    except subprocess.TimeoutExpired:
        return QcResult(has_hallucination=False, issues=[], ok=False,
                        raw_result="", error=f"质检超时（{QC_TIMEOUT_SEC}s）")
    except Exception as e:
        return QcResult(has_hallucination=False, issues=[], ok=False,
                        raw_result="", error=f"质检调用异常: {e}")

    # stdout 恒为 JSON envelope（成功/失败都解析）；exit 0=成功
    result_text = _extract_result_text(proc.stdout or "")

    # 失败 envelope（exit≠0 或 result 为空且 metadata 有 error）
    if proc.returncode != 0 or not result_text:
        error = proc.stderr.strip() or proc.stdout.strip()[:200]
        # 尝试读失败 envelope 的 metadata.error
        try:
            data = json.loads(proc.stdout or "{}")
            if isinstance(data, dict):
                md = data.get("metadata") or {}
                error = md.get("error") or error
        except Exception:
            pass
        return QcResult(has_hallucination=False, issues=[], ok=False,
                        raw_result=proc.stdout or "", error=error[:300])

    return parse_verdict(result_text)


# 便捷入口：给 vimax_bridge 用
def maybe_qc_sync(frame_path: str | Path, shot: dict, frame_kind: str,
                  characters: list | None = None, empty_scene: bool = False) -> QcResult:
    """同步质检入口（带 should_run_qc 守卫）；由 vimax_bridge 的 async 包装调用."""
    if not should_run_qc(frame_path):
        return QcResult(has_hallucination=False, issues=[], ok=True, raw_result="", error="skipped")
    return qc_image(frame_path, shot=shot, frame_kind=frame_kind,
                    characters=characters, empty_scene=empty_scene)
