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

import base64
import io
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import requests
from PIL import Image

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
# v1.1：新增 prompt_semantics（画面是否符合实际生成 prompt 的语义，如发色/朝向/手持物）
# v6.52：新增 extra_person（人数断言——画面实体人数超出 char_layout 名单+1）
_VALID_CATEGORIES = {
    "face_consistency", "body_deformity", "unexpected_object",
    "text_watermark", "scene_consistency", "subject_consistency",
    "prompt_semantics", "extra_person",
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
    # v6.53 三层架构产出（第 1/2 层），sidecar 与尝试日志消费
    person_count: int | None = None
    answers: dict | None = None
    # v6.54.1：L1 grounding 原始实体条目（{label, desc, bbox}，bbox 归一 0-1000），
    # build_repair_plan 据此选 bbox 调 _doodle_refine_first_frame 做局部修复
    entities: list[dict] | None = None


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

    v1.1：新增 prompt_semantics 类别——用「实际生成图片的最终 prompt」与图片
    内容比对，检测语义偏差（发色/朝向/手持物/姿势），而不只是生成畸形。
    最终 prompt 来自 shot["ff_desc_final"]/["lf_desc_final"]（生成处挂载），
    未挂载时回退到 shot["ff_desc"]/["lf_desc"]。
    """
    # 场景 / 主体
    scene = shot.get("scene") or shot.get("location") or ""
    subject = shot.get("main_subject") or ""
    # 实际生成图片的最终 prompt（生成处挂载），优先于原始 desc
    desc = shot.get("ff_desc_final") or shot.get("ff_desc") or shot.get("lf_desc") or shot.get("motion_desc") or ""
    if frame_kind == "last_frame":
        desc = shot.get("lf_desc_final") or shot.get("lf_desc") or shot.get("ff_desc") or shot.get("motion_desc") or desc
    elif frame_kind == "keyframe":
        # 关键帧：用关键帧实际最终 prompt 核对（kf_{i}_desc_final 由 _gen_keyframes_async 挂载）
        desc = (shot.get("kf_0_desc_final") or shot.get("kf_desc_final")
                or shot.get("ff_desc_final") or shot.get("ff_desc")
                or shot.get("lf_desc") or shot.get("motion_desc") or desc)
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
        f"画面描述：{_truncate(desc, 500)}",
        "",
        "检查以下问题类别，只看图里实际存在、明显的问题，不要吹毛求疵：",
        "1. face_consistency 人物五官/脸型异常、扭曲、模糊不清",
        "2. body_deformity 肢体畸形（多指/少指/扭曲/关节错位/比例失调）",
        "3. unexpected_object 多余物体、漂浮/穿透/半透明等违反物理逻辑的现象",
        "4. text_watermark 乱码文字、水印、logo",
        "5. subject_consistency 画面主体人物/场景/道具与预期明显不符",
    ]
    if not empty_scene:
        # v1.1：语义核对——画面是否符合实际生成的最终 prompt
        lines.append(
            "6. prompt_semantics 画面是否符合以上「画面描述」（重点核对："
            "人物发色/服装颜色、面部朝向（画左/画右/面向谁）、手持物品/姿势、"
            "场景要素、人物数量——与描述明显不符才算问题；古风水墨风格笔触不算）"
        )
    # v6.52 防线1：人数断言——给视觉模型一个具体数字去清点（模糊的「人物数量」
    # 检查项会被跳过；hook ff 画了 4 人 QC 判 0 问题实测）。容忍 +1（威胁源
    # 双实例/背景 1 路人），超出即 critical（IMAGE_QC_WRITE_CRITICAL=1 删帧重生成）。
    _cl_entities = [str(it.get("name", "")).strip() for it in (shot.get("char_layout") or [])
                    if isinstance(it, dict) and str(it.get("name", "")).strip()]
    if _cl_entities and not empty_scene:
        _tol0 = int(os.getenv("QC_PERSON_TOLERANCE", "0"))
        lines.append(
            f"8. extra_person 清点画面中的实体人物数（蒙面/背影/水中半身都算，"
            f"水面倒影/水下暗影轮廓/画中画像不算）：本镜角色名单共 "
            f"{len(_cl_entities)} 人（{'、'.join(_cl_entities)}），"
            f"画面实体人数若多于 {len(_cl_entities) + _tol0} → critical"
        )
    if empty_scene:
        lines.append("7. 本镜是空镜，画面中禁止出现任何人；若出现人物则属于严重问题。")
    lines.extend([
        "",
        "判定规则：",
        "- 物理逻辑错误（漂浮/穿透/半透明/融合）或关键人物缺失或空镜出现人物 → critical",
        "- prompt_semantics 语义偏差（发色/朝向/手持物不符）→ warning，不升级 critical",
    ])
    # P4 修复：关键锚点（服装色/人物数量/威胁源状态）不符按配置升级严重度。
    # QC_SEMANTICS_CRITICAL=1 → critical（删帧重生成，当场拦住）；默认 warning（只记录）。
    # v6.53 A.0：人数容差从写死的 +1 改为 QC_PERSON_TOLERANCE（默认 0——
    # try1 的 4 人图 vs 名单 3 人曾因 +1 容差漏放，0 容差下 critical）。
    _tol = int(os.getenv("QC_PERSON_TOLERANCE", "0"))
    if os.getenv("QC_SEMANTICS_CRITICAL", "0") == "1":
        lines.append("- **关键锚点**（服装颜色、人物数量、威胁源状态如刺客是否出水）与「画面描述」明显不符 → critical（会删帧重生成）")
    else:
        lines.append("- **关键锚点**（服装颜色、人物数量、威胁源状态如刺客是否出水）与「画面描述」明显不符 → warning（设 QC_SEMANTICS_CRITICAL=1 可升级 critical 删帧重生成）")
    if _cl_entities and not empty_scene:
        allowed = len(_cl_entities) + _tol
        lines.append(
            f"- 人数断言：名单 {len(_cl_entities)} 人，容忍名单外 {_tol} 人——"
            f"画面实体人数多于 {allowed} → critical"
        )
    lines.extend([
        "- 轻微瑕疵（背景小物、风格笔触、留白、水墨晕染）→ 不算问题，不得误判",
        "- 该作品是古风水墨风格，水墨、留白、笔触、墨晕均属正常艺术风格",
        "",
        "",
        "⚠️ 这是【已生成图片的质检任务】，不是生成任务：",
        "- 禁止重新生成图像、禁止输出修改后的画面描述、禁止提供调整建议",
        "- 只对上图做检查并输出判定 JSON，任何多余文字都会导致质检作废",
        "- 若画面出现纯白/影棚背景、参考图版式混入、或上下拼接的两截画面，"
        "属 unexpected_object critical（参考图样式污染）",
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
    # v1.1：语义偏差词优先归 prompt_semantics（发色/朝向/服装/手持物/姿势/数量与描述不符）
    semantic_hints = (
        "发色", "朝向", "服装", "手持", "姿势", "发丝", "颜色不符",
        "画面与描述", "不符描述", "与描述不符", "未按描述", "语义",
        "发色不符", "朝向不符", "头发", "青绿", "绿色头发",
    )
    if any(h in raw for h in semantic_hints):
        return "prompt_semantics"
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

    # 4. 对话式响应守卫（v6.50，08-29）：视觉模型把质检当成了生成任务、
    #    返回"重新生成/如需调整"式聊天文本——此时没有质检结论可言。
    #    旧行为是在散文上做关键词回退扫描：碰巧含触发词→critical、
    #    碰巧没有→clean sidecar——同一张图两次结果相反（非确定性根源）。
    #    改为：一律判质检失败（ok=False），调用方日志可见、不写 clean sidecar，
    #    下次重新质检。
    _CHAT_MARKERS = ("如需调整", "重新生成", "我将继续", "根据您描述", "请提供", "可以告知")
    if any(k in text for k in _CHAT_MARKERS):
        return QcResult(has_hallucination=False, issues=[], ok=False,
                        raw_result=text, error="视觉模型返回对话式响应而非质检 JSON（已拒收）")

    # 5. 关键词回退（仅对非对话式纯文本判定）
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

    IMAGE_QC_WRITE_CRITICAL=0 时所有 issues severity 封顶为 warning（仅告警）；
    =1（默认）时写真实 severity，submit_poll 遇 critical 自动删帧重生成（审计 P0-3）。
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

    # P4 修复：空串/纯空白也算「未提供」——否则视觉模型拿到空的「画面描述」，
    # prompt_semantics 必然全过（sidecar clean 实为「没查」而非「没问题」）。
    if not (prompt and prompt.strip()):
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


# ============================================================================
# v6.53 三层架构：第 1/2 层（ARK grounding + 锚点二值问答）
#
# 背景：免费池视觉模型"判对错"不可救——hook ff 4 人图两次判 0 问题，它不数
# 人数、只扫整体像不像。本层把"数数"与"对错"从 LLM 拿回代码：
#   LLM 只做检测（出实体框+属性行 / 答二值问题——它擅长的）
#   代码做计数断言 + 属性指纹查重 + 锚点符合性判定（它擅长的）
# ============================================================================

_ARK_VISION_MODEL = os.getenv("QC_ARK_MODEL", "doubao-seed-1-6-250615")
# Agnes 视觉响应慢（router 给它配 300s）——检测层默认 150s，单渠道失败自动
# 落下一个渠道；末渠道重试 1 次。
_ARK_TIMEOUT_SEC = int(os.getenv("QC_ARK_TIMEOUT_SEC", "150"))
_COLOR_WORDS = ("白", "黑", "青", "蓝", "红", "绿", "灰", "紫", "金", "银", "粉", "褐")
# 检测模型属性行是英文（grounding prompt 为英文）——英文色词归一到中文键，
# 与 characters features（中文）的颜色统计对齐
_EN_COLOR_MAP = (("white", "白"), ("black", "黑"), ("cyan", "青"), ("blue", "蓝"),
                 ("red", "红"), ("green", "绿"), ("gray", "灰"), ("grey", "灰"),
                 ("purple", "紫"), ("gold", "金"), ("silver", "银"), ("pink", "粉"),
                 ("brown", "褐"))
_GROUNDING_PROMPT = (
    "Detect every distinct person and the main vehicle/furniture object in this image. "
    'Return ONLY a JSON array, one entry per entity: '
    '[{"label":"person","desc":"<clothing color + headwear + pose, <=20 words>","bbox":[x1,y1,x2,y2]}] '
    "where bbox is normalized to 0-1000. Include partially visible persons (in water, behind objects). "
    "Do NOT count reflections, shadows, or painted figures. No other text."
)


def _vision_chat(frame_path: str | Path, prompt: str) -> str:
    """检测/问答共用的视觉对话传输层，按可用性链式回落，全失败抛异常.

    实测（2026-08-29）：
    - ARK doubao-seed 视觉模型全部 404（该账号仅开通图片/视频生成，未开通
      视觉 chat）——保留 ark 分支以备开通后自动生效。
    - agnes-2.5-flash（AGNES_BASE_URL/v1/chat/completions，router 同款）
      grounding 完美：try2 好图正好 3 人、属性行准确，且免费。
    - dashscope qwen-vl-max 同样可用（输出偶发 JSON 畸形，_parse_entity_array
      已做容错修复）。
    """
    attempts = []
    agnes_key = os.getenv("AGNES_API_KEY") or ""
    if agnes_key:
        base = (os.getenv("AGNES_BASE_URL") or "https://api.agnes-ai.cn").rstrip("/")
        attempts.append(("agnes", f"{base}/v1/chat/completions",
                         os.getenv("AGNES_VISION_MODEL", "agnes-2.5-flash"), agnes_key))
    ds_key = os.getenv("DASHSCOPE_API_KEY") or ""
    if ds_key:
        attempts.append(("dashscope", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                         os.getenv("QC_DASHSCOPE_VISION_MODEL", "qwen-vl-max"), ds_key))
    ark_key = os.getenv("VOLCENGINE_API_KEY") or os.getenv("ARK_API_KEY") or ""
    if ark_key:
        base = os.getenv("VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        attempts.append(("ark", f"{base.rstrip('/')}/chat/completions", _ARK_VISION_MODEL, ark_key))

    if not attempts:
        raise RuntimeError("无可用视觉理解渠道（AGNES/DASHSCOPE/VOLCENGINE key 均未配置）")
    data_url = _ark_image_data_url(frame_path)
    last_err = ""
    for name, url, model, key in attempts:
        # Agnes 响应慢且偶发读超时——超时重试 1 次（4xx 不重试）
        tries = 2 if name == "agnes" else 1
        for attempt_i in range(tries):
            try:
                resp = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ]}], "temperature": 0, "max_tokens": 1500,
                    },
                    timeout=_ARK_TIMEOUT_SEC,
                )
                if resp.status_code != 200:
                    last_err = f"{name} {resp.status_code}: {resp.text[:120]}"
                    break
                data = resp.json()
                return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            except Exception as e:
                last_err = f"{name}: {e}"
                if attempt_i < tries - 1:
                    continue
                break
    raise RuntimeError(f"视觉检测渠道全部失败：{last_err}")


def _parse_entity_array(text: str) -> list[dict]:
    """解析实体 JSON 数组；容忍模型输出的畸形括号（qwen 实测「420}}」）."""
    if not text or not text.strip():
        raise RuntimeError("检测模型返回空结果")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError(f"无 JSON 数组: {text[:120]}")
    raw = m.group(0)
    try:
        entries = json.loads(raw)
    except Exception:
        entries = []
        # 逐对象抽取重建（对象内不含嵌套花括号）
        for obj in re.findall(r"\{[^{}]*\}", raw):
            try:
                entries.append(json.loads(obj))
            except Exception:
                continue
    if not isinstance(entries, list):
        raise RuntimeError("检测结果非数组")
    return [e for e in entries if isinstance(e, dict)]


def _detect_entities(frame_path: str | Path) -> list[dict]:
    """grounding 检测：返回实体条目 [{label, desc, bbox}, ...]；失败抛异常."""
    return _parse_entity_array(_vision_chat(frame_path, _GROUNDING_PROMPT))


def _ark_image_data_url(frame_path: str | Path, max_side: int = 1024) -> str:
    """图片转 base64 data URL（长边压到 max_side 控 payload/延迟）."""
    img = Image.open(frame_path).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        ratio = max_side / max(w, h)
        img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _expected_persons(shot: dict, frame_kind: str, characters: list | None) -> int | None:
    """本帧应出现人数 = char_layout 实体数；威胁源仅非实体呈现时从名单剔除."""
    names = [str(it.get("name", "")).strip() for it in (shot.get("char_layout") or [])
             if isinstance(it, dict) and str(it.get("name", "")).strip()]
    if not names:
        return None
    _THREAT = ("神秘人", "刺客", "杀手", "黑衣人", "蒙面人", "黑影")
    has_threat_name = any(n in _THREAT for n in names)
    if has_threat_name:
        try:
            from shared.vimax_bridge import _threat_visible_in
            if frame_kind == "first_frame":
                desc = shot.get("ff_desc_final") or shot.get("ff_desc") or ""
            elif frame_kind == "last_frame":
                desc = shot.get("lf_desc_final") or shot.get("lf_desc") or ""
            else:
                desc = " ".join(filter(None, [shot.get("ff_desc", ""), shot.get("lf_desc", "")]))
            if _threat_visible_in(desc, characters) is not True:
                names = [n for n in names if n not in _THREAT]
        except Exception:
            pass
    return len(names)


def _color_of(attrs: str) -> str:
    """从属性行抽主服装色（中英文表都查，返回中文键；无则空）."""
    t = attrs or ""
    for en, zh in _EN_COLOR_MAP:
        if en in t.lower():
            return zh
    for w in _COLOR_WORDS:
        if w in t:
            return w
    return ""


# ── v6.54.1 QC 驱动局部修复：critical 分类 → bbox 局部 i2i 改写 ──────────
# 原理：局部失败（睁眼/刀接触/红痕/篷/发光）重抽 = 十颗骰子全重掷；
# bbox 圈定问题区域走 _doodle_refine_first_frame（红框 i2i，框外保持原样）
# 1 次命中的概率远高于整张重抽。全局失败（人数/复制/动作错/位置错）编辑
# ≈ 重画，直接 regenerate。

# (名称, 问题文案匹配正则, 修复指令, bbox 选择器)
_LOCALIZED_FIX_PATTERNS: list[tuple[str, str, str, str]] = (
    ("knife_contact", r"刀具|接触|切入|刺入",
     "让刀具悬停在空中，与任何人的身体保持明显距离，不接触不切入人体",
     "knife_zone"),
    ("red_mark", r"红色痕迹|伤口|血迹|红污|洇痕",
     "去除人物身上所有红色痕迹、伤口与血迹，皮肤与衣物干净",
     "all_persons"),
    ("glowing", r"发光|光刃|光束|光环",
     "移除画面中的发光物体与光效，刀具为普通哑光金属质感",
     "all_persons"),
    ("eyes_closed", r"双目紧闭|闭眼|闭目|睁眼",
     "让躺着的人闭上双眼，头部与身体姿态保持不变",
     "lying_face_top45"),
    ("canopy", r"篷|顶棚|遮盖物",
     "移除船的顶棚与篷布，舱面完全敞开",
     "boat_upper_half"),
)


def _bbox_valid(b) -> bool:
    try:
        x1, y1, x2, y2 = (float(v) for v in b)
        return x2 > x1 and y2 > y1
    except (TypeError, ValueError):
        return False


def _is_lying(entity: dict) -> bool:
    desc = str(entity.get("desc", "")).lower()
    if any(w in desc for w in ("lying", "躺", "卧", "仰", "横")):
        return True
    b = entity.get("bbox")
    if _bbox_valid(b):
        x1, y1, x2, y2 = (float(v) for v in b)
        w, h = x2 - x1, y2 - y1
        if h > 0 and w / h > 1.5:  # 横宽人形 ≈ 卧姿
            return True
    return False


def _is_knife_holder(entity: dict) -> bool:
    desc = str(entity.get("desc", "")).lower()
    return any(w in desc for w in ("holding", "持", "握", "刀", "匕首", "剑",
                                   "knife", "blade", "sword"))


def _knife_contact_zone(holders: list[dict], lying: list[dict]) -> list[float] | None:
    """持刀者 ∩ 躺卧者 接触区域（v6.32.1，2026-08-29）.

    修复 knife_contact critical 的修复 bbox 选区——旧版用并集导致区域经常
    超 70% 画幅，触发 `_bbox_area_pct > 70% → regenerate` 闸口，局部修复
    永不触发。新版按几何分三档：

    1. **交集 + 20% 边距**（推荐）：持刀者框 ∩ 躺卧者框即接触点所在，
       向外扩 20% 边距覆盖接触带周边。90% 刺杀镜命中此档。
    2. **连接带**（少见）：两框不相交但有间隙时，取两框最近边缘之间的
       矩形带（厚度 = 较小框短边的 1/4 + 100px 等价坐标）。
    3. **并集兜底**（罕见）：间隙 > 较大框短边时（持刀者离躺卧者太远，
       实际不太可能刺中）退回并集，避免修复区域退化为零。

    Args:
        holders: 持刀者实体列表（已 bbox 验证）
        lying: 躺卧者实体列表（已 bbox 验证）

    Returns:
        单个 4 元素 bbox 列表 [x1, y1, x2, y2]（0-1000 归一坐标），
        或 None（无可用数据时）。
    """
    if not holders or not lying:
        return None
    h = holders[0]["bbox"]
    l = lying[0]["bbox"]
    hx1, hy1, hx2, hy2 = (float(v) for v in h)
    lx1, ly1, lx2, ly2 = (float(v) for v in l)

    # 1. 交集 + 20% 边距
    ix1, iy1 = max(hx1, lx1), max(hy1, ly1)
    ix2, iy2 = min(hx2, lx2), min(hy2, ly2)
    if ix2 > ix1 and iy2 > iy1:
        # 交集存在 → 外扩 20% 边距（按交集尺寸）
        mx, my = (ix2 - ix1) * 0.2, (iy2 - iy1) * 0.2
        return [
            max(0.0, ix1 - mx), max(0.0, iy1 - my),
            min(1000.0, ix2 + mx), min(1000.0, iy2 + my),
        ]

    # 2. 不相交 → 连接带
    #    计算两框在 x/y 方向的重叠 vs 间隙；找较小间隙方向作为「通道」，
    #    沿另一方向取两框重叠区间；带宽 = 较小框短边的 1/4。
    #    间隙 > 较大框短边 → 退回并集（相距过远，修复无意义）
    h_w, h_h = hx2 - hx1, hy2 - hy1
    l_w, l_h = lx2 - lx1, ly2 - ly1
    max_short = max(min(h_w, h_h), min(l_w, l_h))
    # 投影到 x 轴：水平间隙
    if hx2 < lx1:
        gap_x = lx1 - hx2
        x1, x2 = hx2, lx1
    elif lx2 < hx1:
        gap_x = hx1 - lx2
        x1, x2 = lx2, hx1
    else:
        gap_x = 0  # x 方向重叠
        x1, x2 = max(hx1, lx1), min(hx2, lx2)
    # 投影到 y 轴：水平间隙
    if hy2 < ly1:
        gap_y = ly1 - hy2
        y1, y2 = hy2, ly1
    elif ly2 < hy1:
        gap_y = hy1 - ly2
        y1, y2 = ly2, hy1
    else:
        gap_y = 0
        y1, y2 = max(hy1, ly1), min(hy2, ly2)
    # 较小间隙方向作为通道；若都 0（不可能——已知无交集）选 x 方向
    if gap_x == 0 and gap_y == 0:
        # 不相交必有至少一个 gap > 0，这里兜底
        gap_x = abs(hx1 - lx1)
    gap = min(gap_x, gap_y) if (gap_x > 0 and gap_y > 0) else (gap_x or gap_y)
    if gap > max_short:
        # 3. 相距过远 → 并集兜底
        return [
            min(hx1, lx1), min(hy1, ly1),
            max(hx2, lx2), max(hy2, ly2),
        ]
    # 连接带：带宽 = 较小框短边的 1/4（最多 100 归一坐标）
    band = min(max_short / 4, 100.0)
    # 沿间隙方向取两框之间，垂直方向取两框重叠区间
    if gap_x <= gap_y:
        # 间隙在 x 方向 → 带沿 x 方向延伸
        cx1, cx2 = x1, x2
        cy1 = max(0.0, min(hy1, ly1) - band / 2)
        cy2 = min(1000.0, max(hy2, ly2) + band / 2)
    else:
        # 间隙在 y 方向 → 带沿 y 方向延伸
        cy1, cy2 = y1, y2
        cx1 = max(0.0, min(hx1, lx1) - band / 2)
        cx2 = min(1000.0, max(hx2, lx2) + band / 2)
    return [cx1, cy1, cx2, cy2]


def _select_bboxes_for_pattern(selector: str,
                               person_entities: list[dict],
                               all_entities: list[dict]) -> list[list[float]]:
    """按选择器收集目标 bbox（0-1000 归一坐标）."""
    boxes: list[list[float]] = []
    if selector == "all_persons":
        boxes = [e["bbox"] for e in person_entities if _bbox_valid(e.get("bbox"))]
    elif selector == "knife_zone":
        # 2026-08-29：knife_contact 改交集（v6.32.1）。
        # 旧实现：持刀者 ∪ 躺卧者（并集），修复区域过大、超过 70% 画幅
        # 触发 `_bbox_area_pct > 70% → regenerate` 闸口，导致局部修复永不触发。
        # 新实现：取交集（接触点所在）+ 20% 边距；不相交取两框最近边缘的
        # 连接带；相距过远（罕见）兜底用并集。详见 _knife_contact_zone。
        holders = [e for e in person_entities
                   if _is_knife_holder(e) and _bbox_valid(e.get("bbox"))]
        lying = [e for e in person_entities
                 if _is_lying(e) and _bbox_valid(e.get("bbox"))]
        zone = _knife_contact_zone(holders, lying)
        if zone:
            boxes.append(zone)
    elif selector == "lying_face_top45":
        for e in person_entities:
            if _is_lying(e) and _bbox_valid(e.get("bbox")):
                x1, y1, x2, y2 = (float(v) for v in e["bbox"])
                boxes.append([x1, y1, x2, y1 + (y2 - y1) * 0.45])  # 脸部=框顶 45%
    elif selector == "boat_upper_half":
        for e in all_entities:
            if str(e.get("label", "")).lower() != "person" and _bbox_valid(e.get("bbox")):
                x1, y1, x2, y2 = (float(v) for v in e["bbox"])
                boxes.append([x1, y1, x2, y1 + (y2 - y1) * 0.5])
    return boxes


def _union_bboxes(boxes: list[list[float]]) -> tuple[float, float, float, float]:
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    return (x1, y1, x2, y2)


def _bbox_area_pct(bbox: tuple[float, float, float, float]) -> float:
    """归一 0-1000 坐标的面积占比（%），总画幅 = 1e6."""
    x1, y1, x2, y2 = bbox
    return (x2 - x1) * (y2 - y1) / 1e6 * 100


def build_repair_plan(shot: dict, frame_kind: str, result) -> dict:
    """QC critical 分类：局部可修 → edit（bbox + 指令）；全局 → regenerate.

    任一 critical 不匹配局部模式（人数/复制/动作错/位置错等）或 bbox 不可得
    或并集区域 >70% 画幅 → 判非局部，走原重抽路径。
    """
    issues = [i for i in (getattr(result, "issues", None) or [])
              if getattr(i, "severity", "") == "critical"]
    if not issues:
        return {"action": "regenerate", "reason": "无 critical", "matched_patterns": []}
    entities = getattr(result, "entities", None) or []
    persons = [e for e in entities if str(e.get("label", "")).lower() == "person"]

    matched: list[str] = []
    instructions: list[str] = []
    all_boxes: list[list[float]] = []
    unmatched: list[str] = []
    for issue in issues:
        d = getattr(issue, "description", "") or ""
        for name, pat, instruction, selector in _LOCALIZED_FIX_PATTERNS:
            if re.search(pat, d):
                matched.append(name)
                instructions.append(instruction)
                all_boxes.extend(_select_bboxes_for_pattern(selector, persons, entities))
                break
        else:
            unmatched.append(d[:40])

    if unmatched:
        return {"action": "regenerate",
                "reason": f"非局部失败: {'; '.join(unmatched)}",
                "matched_patterns": matched}
    if not all_boxes:
        return {"action": "regenerate",
                "reason": "无可选目标 bbox（检测层缺实体）",
                "matched_patterns": matched}

    region = _union_bboxes(all_boxes)
    area = _bbox_area_pct(region)
    if area > 70:
        return {"action": "regenerate",
                "reason": f"修复区域占画幅 {area:.0f}%，非局部",
                "matched_patterns": matched}

    bbox_ratio = tuple(round(c / 1000.0, 4) for c in region)  # 0-1000 → 0-1
    return {
        "action": "edit",
        "bbox_ratio": bbox_ratio,
        "edit_prompt": (f"修改图中红框区域：{'；'.join(instructions)}。"
                        "红框外的画面内容严格保持原样，不得改动构图、人物与其他元素。"),
        "matched_patterns": matched,
        "area_pct": round(area, 1),
    }


def _assert_action_consistency(persons: list[dict], prompt_text: str) -> list[QcIssue]:
    """通用对账（象限 B）：图中人物动作/持物 vs prompt 允许集.

    hook ff「推船随行」实测：检测属性行出现 pushing 而描述无「推」——动作
    对账自动拦，无需为每个新失败手写问题。强动作（改变场景/物体状态）→
    critical；姿态类（模型用词宽泛，误报高）→ warning。持物按允许集比对
    （prompt 提及任一可持物词即放行）。
    """
    issues: list[QcIssue] = []
    t = prompt_text or ""
    for p in persons:
        desc = str(p.get("desc", "")).lower()
        who = str(p.get("desc", ""))[:26]
        for en, zhs, strong in _ACTION_EN_ZH:
            if en not in desc:
                continue
            if en == "holding":
                if not any(w in t for w in _HOLDING_ALLOWED):
                    issues.append(QcIssue(
                        category="action_mismatch",
                        description=f"图中人物持有物品（{who}），描述未提及任何可持物",
                        severity="critical" if strong else "warning",
                    ))
                continue
            if not any(z in t for z in zhs):
                issues.append(QcIssue(
                    category="action_mismatch",
                    description=f"图中人物动作「{zhs[0]}」({en}) 未出现在描述中（{who}）",
                    severity="critical" if strong else "warning",
                ))
    return issues


def _assert_duplicate_chars(persons: list[dict], expected: int | None,
                            characters: list | None) -> list[QcIssue]:
    """属性指纹查重：同色实体对数 > 名单同色对数 → critical（「杨旭重复」形态）.

    颜色指纹取属性行的主服装色。名单侧颜色从 characters 的
    static_features/dynamic_features 文本统计；名单缺颜色信息时（无法比对）
    仅在人数本身已超名单时由计数断言拦截，此处不误报。
    """
    if len(persons) < 2 or expected is None:
        return []
    observed: dict[str, int] = {}
    for p in persons:
        c = _color_of(str(p.get("desc", "")))
        if c:
            observed[c] = observed.get(c, 0) + 1
    roster_text = " ".join(
        str((c or {}).get("static_features", "")) + str((c or {}).get("dynamic_features", ""))
        for c in (characters or []))
    if not roster_text.strip():
        return []
    issues: list[QcIssue] = []
    for color, n in observed.items():
        if n < 2:
            continue
        roster_n = roster_text.count(color)
        if roster_n < 2:
            issues.append(QcIssue(
                category="extra_person",
                description=f"{color}色人物出现 {n} 次，名单中该色角色不足 2 个（角色被复制）",
                severity="critical",
            ))
    return issues


# ── 第 2 层：锚点 → 二值问答 ────────────────────────────────────────
# (触发词, 问题, 期望答案, 是否关键锚)。期望答案由锚的极性直接给出，
# 避免"问题→锚"反向匹配的歧义。

# ── v6.54 通用对账层 ────────────────────────────────────────────────
# 设计原则：不枚举历史失败（打地鼠），对账「事实源」。失败只可能落在三个
# 象限，各有一个通用机制：
#   A. prompt 写了、图没画   → 逐句断言对账（本函数）+ 否定句式自动翻转
#   B. 图里有了、prompt 没写 → 实体动作/持物对账（_assert_action_consistency）
#   C. 恒真规则              → 人数/复制（L1）+ 首帧禁红/禁接触（规则类，保留）

# 不可判定子句：含抽象/叙事/氛围词 → 跳过（视觉模型判不了，硬判必误报）
_CLAIM_SKIP_WORDS = ("气氛", "危机", "仿佛", "即将", "定格", "一触即发", "惊觉",
                     "蓄势", "张力", "情绪", "叙事", "视觉焦点", "前一刻",
                     "静态布局", "稳定", "不闪烁")

# 结构性词：子句含任一 → 关键断言（画错 = 构图/叙事损坏，critical）
_CLAIM_KEY_WORDS = (
    "画左", "画右", "画中", "前景", "中景", "远景", "船头", "船尾", "船中",
    "中舱", "甲板", "船舷", "水中", "水下", "水面", "岸边", "门口", "车辕",
    "仰卧", "仰面", "躺", "跪", "蹲", "探出", "俯身", "倒",
    "刀", "匕首", "短刃", "武器", "持", "握", "蒙面",
    "白袍", "血", "伤", "洇", "红", "伞", "灯笼", "马车", "箫",
    "肩宽", "船宽",
)

# 否定句式可视觉判定的目标词（「无X/没有X/不见X」自动生成期望 no 的检查）
_NEGATION_TARGETS = ("篷", "伞", "刀", "剑", "匕首", "武器", "血", "血迹", "红",
                     "伤", "痕", "帽", "冠", "灯", "灯笼", "文字", "水印", "发光",
                     "面容", "武器")

# 图中英文动作词 → 中文允许集（任一中文出现在 prompt 即算允许）
# 只收「改变场景/物体状态」的强动作——姿态类（躺/站/坐）误报率高单独降级
_ACTION_EN_ZH = (
    ("pushing", ("推",), True),
    ("pulling", ("拉", "拽"), True),
    ("carrying", ("背", "扛", "抬"), True),
    ("rowing", ("撑", "划"), True),
    ("throwing", ("掷", "扔", "抛"), True),
    ("climbing", ("攀", "爬"), True),
    ("riding", ("骑",), True),
    ("holding", ("持", "拿", "握", "撑", "提"), True),
    ("pointing", ("指", "戟指"), False),
    ("covering", ("捂", "掩", "遮"), False),
    ("crying", ("哭", "泣", "啜"), False),
    ("smiling", ("笑",), False),
    ("jumping", ("跃", "跳"), False),
    ("running", ("跑", "奔"), False),
    ("walking", ("走", "行"), False),
    # 姿态类：误报高（模型用词宽泛），只产 warning
    ("lying", ("卧", "躺"), False),
    ("kneeling", ("跪",), False),
    ("standing", ("站", "立"), False),
    ("sitting", ("坐",), False),
)
# 持物允许集：prompt 出现任一词即认为「持物」被允许
_HOLDING_ALLOWED = ("刀", "匕首", "剑", "短刃", "伞", "桨", "橹", "盘", "灯",
                    "笼", "缰", "竿", "杆", "箫", "扇", "酒", "信")


def _split_clauses(text: str) -> list[str]:
    """画面描述 → 原子子句（。！？；与，两级切分，保留 4-40 字的可判定段）."""
    clauses: list[str] = []
    for sent in re.split(r"[。！？；\n]", text or ""):
        for sub in re.split(r"[，]", sent):
            sub = sub.strip("，。；、（）() \t")
            if 4 <= len(sub) <= 40:
                clauses.append(sub)
    # 去重保序
    seen: set[str] = set()
    out = []
    for c in clauses:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def build_claim_checks(shot: dict, frame_kind: str) -> list[dict]:
    """通用对账（象限 A）：画面描述逐句 → 「画面是否表现：X」二值检查.

    通用性：不枚举历史失败。任何「prompt 写了但图没画」的子句自动成为检查项；
    否定句式（无X/没有X/不见X 且 X 可视觉判定）自动翻转为期望 no。
    抽象/氛围子句跳过（不可判定，硬判必误报）。关键子句（含结构性词）优先。
    """
    if frame_kind == "first_frame":
        text = shot.get("ff_desc") or shot.get("ff_desc_final", "") or ""
    elif frame_kind == "last_frame":
        text = shot.get("lf_desc") or shot.get("lf_desc_final", "") or ""
    else:
        text = " ".join(filter(None, [shot.get("ff_desc", ""), shot.get("lf_desc", "")]))
    # 只对账「叙述+锚块」：组装 prompt 里的【强制】等约束条款不是画面断言，
    # 混入会污染拆句。ff_desc 含【角色锚定】/船体尾句——它们本身就是可判定
    # 的视觉断言（听香在中舱边缘/神秘人半身浸水中）。剥掉【】标记保留内容。
    text = re.sub(r"【[^】]*】", "", text)
    text = re.sub(r"identity locked[^，。]*", "", text)
    try:
        # 尺寸锚（肩宽三倍/无篷）注入在组装期、不在 ff_desc 字段——此处单独
        # 取回作为断言（船宽类失败的通用覆盖）
        from shared.vimax_bridge import _inject_object_size_anchor
        size_line = _inject_object_size_anchor(shot, "")
        if size_line.strip("。"):
            text = text + "。" + size_line
    except Exception:
        pass

    qs: list[dict] = []
    # 常真规则（规则类，非补丁）：首帧 = 动作开始前
    if frame_kind == "first_frame":
        qs.append({"q": "画面任何人身上是否出现红色痕迹、伤口或血迹？", "expect": "no", "key": True})
        if any(w in text for w in ("刀", "匕首", "刺")):
            qs.append({"q": "刀具是否已接触或切入任何人的身体？", "expect": "no", "key": True})
        if "发光" not in text and any(w in text for w in ("刀", "匕首", "短刃")):
            qs.append({"q": "图中是否有发光的物体或光刃？", "expect": "no", "key": True})

    for clause in _split_clauses(text):
        if any(w in clause for w in _CLAIM_SKIP_WORDS):
            continue
        if re.search(r"[长宽高]\s*\d", clause):
            continue  # 绝对米数子句视觉不可判定（相对比例子句才生成检查）
        # 否定句式自动翻转：「无篷」「没有武器」「不见面容」→ 期望 no 的存在性检查
        neg = re.search(r"(?:无|没有|不见|不得|禁止)([^，。；、\s]{1,6})", clause)
        if neg:
            target = neg.group(1)
            if any(t in target for t in _NEGATION_TARGETS):
                hit_key = any(t in target for t in _CLAIM_KEY_WORDS)
                qs.append({"q": f"画面中是否出现「{target}」？",
                           "expect": "no", "key": hit_key})
                continue
        # 正向断言：含角色/部位指示（他/她/其/手/身/目/发/袍/衣…）的可判定子句
        if not re.search(r"[\u4e00-\u9fff]{2,4}|他|她|其", clause):
            continue
        key = any(w in clause for w in _CLAIM_KEY_WORDS)
        qs.append({"q": f"画面是否表现：{clause}？", "expect": "yes", "key": key})

    qs.sort(key=lambda q: not q["key"])
    return qs[:10]


def build_targeted_questions(shot: dict, frame_kind: str) -> list[dict]:
    """兼容包装：v6.54 起逐句对账取代手写锚问题表（原表已删——打地鼠范式）."""
    return build_claim_checks(shot, frame_kind)


def _ask_yes_no(frame_path: str | Path, questions: list[dict]) -> dict[str, str]:
    """批量二值问答：一次调用答 N 题；返回 {问题: yes|no|unclear}.

    解析失败 = unclear（关键锚按答错处理，次要锚忽略——保守判定）。
    """
    numbered = "\n".join(f"{i}. {q['q']}（只答 yes 或 no）" for i, q in enumerate(questions, 1))
    prompt = ("Answer these questions about the image. Reply with ONLY lines like "
              f"'1: yes'.\n{numbered}")
    text = _vision_chat(frame_path, prompt)
    answers: dict[str, str] = {q["q"]: "unclear" for q in questions}
    for i, q in enumerate(questions, 1):
        m = re.search(rf"{i}\s*[:：.、]\s*(yes|no|是|否)", text or "", re.I)
        if m:
            answers[q["q"]] = "yes" if m.group(1).lower() in ("yes", "是") else "no"
    return answers


# 便捷入口：给 vimax_bridge 用
def maybe_qc_sync(frame_path: str | Path, shot: dict, frame_kind: str,
                  characters: list | None = None, empty_scene: bool = False,
                  prompt: str | None = None) -> QcResult:
    """同步质检入口（带 should_run_qc 守卫）；由 vimax_bridge 的 async 包装调用.

    v6.53 三层架构（QC_PROVIDER=auto 默认，检测链 agnes→dashscope→ark）：
      第 1 层  ARK grounding 出实体框+属性行 → **代码**数人数 vs char_layout
               名单（容差 QC_PERSON_TOLERANCE，默认 0）+ 属性指纹查角色复制
      第 2 层  锚点 → 二值问答（build_targeted_questions），关键锚答错 critical
      第 3 层  既有 router 整体质检——**只产 warning，无放行权**
    ark 失败且 provider=ark → 回落 router 全权（legacy 行为，宁可误放不可漏闸全无）；
    provider=router → 纯 legacy；provider=off → 跳过。

    prompt: 自定义质检 prompt（v1.1 传实际生成图片的最终 prompt；None 则由
    build_qc_prompt 自动构建，内部优先取 shot["ff_desc_final"]/["lf_desc_final"]）。
    """
    if not should_run_qc(frame_path):
        return QcResult(has_hallucination=False, issues=[], ok=True, raw_result="", error="skipped")
    provider = os.getenv("QC_PROVIDER", "auto").strip().lower()
    if provider == "off":
        return QcResult(has_hallucination=False, issues=[], ok=True, raw_result="",
                        error="QC_PROVIDER=off")

    router_result = qc_image(frame_path, prompt=prompt, shot=shot, frame_kind=frame_kind,
                             characters=characters, empty_scene=empty_scene)

    if provider not in ("ark", "auto"):
        return router_result

    all_issues: list[QcIssue] = []
    person_count: int | None = None
    answers: dict | None = None
    entities: list[dict] | None = None
    ark_ok = True
    ark_error = ""
    try:
        # ── 第 1 层：grounding 出框 + 代码计数/查重 ──
        entries = _detect_entities(frame_path)
        entities = entries
        persons = [e for e in entries if str(e.get("label", "")).lower() == "person"]
        person_count = len(persons)
        expected = _expected_persons(shot, frame_kind, characters)
        if expected is not None:
            tol = int(os.getenv("QC_PERSON_TOLERANCE", "0"))
            if abs(person_count - expected) > tol:
                all_issues.append(QcIssue(
                    category="extra_person",
                    description=f"图中实体人数 {person_count} vs 名单 {expected}（容差 {tol}）",
                    severity="critical",
                ))
        all_issues.extend(_assert_duplicate_chars(persons, expected, characters))
        # v6.54 象限 B：图中动作/持物 vs prompt 允许集（推船类失败通用拦截）
        _claim_text = (shot.get("ff_desc_final") or shot.get("ff_desc", "") or "") + " " +             (shot.get("motion_desc", "") or "")
        all_issues.extend(_assert_action_consistency(persons, _claim_text))

        # ── 第 2 层：锚点 → 二值问答 ──
        questions = build_targeted_questions(shot, frame_kind)
        if questions:
            answers = _ask_yes_no(frame_path, questions)
            for item in questions:
                got = (answers or {}).get(item["q"], "unclear")
                if got == item["expect"]:
                    continue
                if got == "unclear" and not item["key"]:
                    continue  # 次要锚解析失败不罚；关键锚 unclear 按答错拦
                sev = "critical" if item["key"] else "warning"
                all_issues.append(QcIssue(
                    category="anchor_mismatch",
                    description=f"「{item['q']}」答 {got}，期望 {item['expect']}",
                    severity=sev,
                ))
    except Exception as e:  # ARK 不可用 → 回落
        ark_ok = False
        ark_error = f"ARK 质检失败回落 router: {e}"

    if not ark_ok and provider == "ark":
        # ARK 全挂：router 结果全权（legacy），否则本帧完全无闸
        out = router_result
        out.person_count = person_count
        out.answers = answers
        out.entities = entities
        if ark_error:
            out.error = (out.error + "；" + ark_error).strip("；")
        return out

    # 第 3 层：router 整体质检强制降级为 warning（无放行权）
    for issue in router_result.issues:
        all_issues.append(QcIssue(category=issue.category,
                                  description=f"[router] {issue.description}",
                                  severity="warning"))

    has_critical = any(i.severity == "critical" for i in all_issues)
    warn_n = sum(1 for i in all_issues if i.severity == "warning")
    has_hallucination = has_critical or warn_n >= 2
    return QcResult(has_hallucination=has_hallucination, issues=all_issues, ok=True,
                    raw_result=router_result.raw_result,
                    error=ark_error,
                    person_count=person_count, answers=answers,
                    entities=entities)
