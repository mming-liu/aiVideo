"""shared.motion_render.render - 固定 HTML/SVG + Playwright 定时 seek + FFmpeg 编码（计划 §A1/§A5）。

三条硬约束（写进实现，不靠约定）
--------------------------------
1. **所有动画是 ``seek(t_sec)`` 的纯时间函数**：模板 JS 里没有 ``requestAnimationFrame``、
   没有 ``Date``/``performance.now``/``setTimeout``、没有随机数、没有网络加载。
   取帧只做两件事：``evaluate(t => window.__motion_seek(t))`` 然后截图。
2. **帧号 0 起、帧数用 ``shared.media_contract.quantize_frame_count``**：
   seek 序列是 ``i / fps``（``i = 0..frame_count-1``），终点只采 ``(frame_count-1)/fps``。
3. **RGBA 全画幅透明 PNG**：``omitBackground`` 截图 → ``frame_%05d.png``；单片展示另编
   H.264/yuv420p 无声 MP4（alpha 直通，透明区合成到显式背景色，仅作展示）。

runtime 缺失时的行为
--------------------
本机（以及任何未安装 Playwright/Chromium 的环境）**不做隐式下载**：
``runtime_status()`` 返回结构化 ``available=False`` + 缺项 + 安装说明，渲染返回
``status="unavailable"/not_run``，manifest 照实记录。**不伪造成功**。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from shared.media_contract import MERGE_FPS, frame_start_sec, quantize_frame_count

from .schema import pointer_join
from .validate import SpecLayout

# ---------------------------------------------------------------- 常量

TEMPLATE_DIR_ENV = "MOTION_TEMPLATE_DIR"
BROWSERS_PATH_ENV = "MOTION_PLAYWRIGHT_BROWSERS_PATH"
NODE_BIN_ENV = "MOTION_NODE_BIN"

#: 依赖锁定：``tools/motion_runtime/package.json`` 里的 playwright 版本必须与这里一致。
PINNED_PLAYWRIGHT = "1.56.0"

DEFAULT_DISPLAY_BG = (32, 32, 32)
FRAME_PATTERN = "frame_%05d.png"

STATUS_PASS = "pass"
STATUS_NOT_RUN = "not_run"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FAIL = "fail"


# ---------------------------------------------------------------- 帧计划

def frame_count_for(duration_sec: float, fps: int = MERGE_FPS) -> int:
    """唯一量化函数（``shared.media_contract``），不足 1 帧非法。"""
    return quantize_frame_count(float(duration_sec), int(fps))


def frame_plan(duration_sec: float, fps: int = MERGE_FPS) -> list[float]:
    """seek 时间序列：``[i / fps for i in 0..frame_count-1]``。

    终点 ``(frame_count-1)/fps`` **严格小于**量化时长——不重复采最后一帧之外的点。
    """
    n = frame_count_for(duration_sec, fps)
    return [frame_start_sec(i, fps) for i in range(n)]


def seek_endpoint(duration_sec: float, fps: int = MERGE_FPS) -> float:
    n = frame_count_for(duration_sec, fps)
    return frame_start_sec(n - 1, fps)


# ---------------------------------------------------------------- 运行底座

def motion_runtime_dir() -> Path:
    """``tools/motion_runtime/``（本包自有的隔离 Node 工作目录）。"""
    return Path(__file__).resolve().parents[2] / "tools" / "motion_runtime"


def template_dir() -> Path:
    override = os.environ.get(TEMPLATE_DIR_ENV)
    if override:
        return Path(override)
    return motion_runtime_dir() / "templates"


def _browsers_path() -> Path:
    override = os.environ.get(BROWSERS_PATH_ENV)
    if override:
        return Path(override)
    return motion_runtime_dir() / ".browsers"


def node_binary() -> str | None:
    override = os.environ.get(NODE_BIN_ENV)
    if override and Path(override).exists():
        return override
    return shutil.which("node")


def _chromium_executable(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    for child in sorted(root.glob("chromium*")):
        for name in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe",
                     "chrome-linux/chrome", "chrome-headless-shell-win64/headless_shell.exe"):
            candidate = child / name
            if candidate.is_file():
                return candidate
    return None


def runtime_status() -> dict:
    """运行底座可用性（**只探测，不安装**）。返回结构化事实供 not_run 记录。"""
    rt = motion_runtime_dir()
    node = node_binary()
    node_version = None
    if node:
        try:
            node_version = subprocess.run([node, "--version"], capture_output=True, text=True,
                                          timeout=30, check=False).stdout.strip()
        except Exception:
            node_version = None
    pkg = rt / "node_modules" / "playwright" / "package.json"
    pw_version = None
    if pkg.is_file():
        try:
            pw_version = json.loads(pkg.read_text(encoding="utf-8")).get("version")
        except Exception:
            pw_version = None
    browsers = _browsers_path()
    chrome = _chromium_executable(browsers)
    missing: list[str] = []
    if not node:
        missing.append("node")
    if pw_version is None:
        missing.append("playwright_module")
    if chrome is None:
        missing.append("chromium_browser")
    return {
        "available": not missing,
        "missing": missing,
        "node": {"path": node, "version": node_version},
        "playwright": {"package_json": str(pkg), "version": pw_version,
                       "pinned": PINNED_PLAYWRIGHT},
        "chromium": {"browsers_path": str(browsers),
                     "executable": str(chrome) if chrome else None},
        "runtime_dir": str(rt),
        "install_hint": (
            f"在隔离目录安装（本仓库不自动下载）："
            f"cd \"{rt}\" && npm ci --ignore-scripts && "
            f"set PLAYWRIGHT_BROWSERS_PATH={browsers} && npx playwright install chromium"
        ),
        "download_facts": DOWNLOAD_FACTS,
    }


#: 依赖分发事实（2026-10-03 通过 npm registry 元数据与 CDN HEAD 核实，**未下载**）。
DOWNLOAD_FACTS = {
    "playwright_npm_unpacked_bytes": 3876497,
    "playwright_core_npm_unpacked_bytes": 8228434,
    "chromium_build_revision": "1194",
    "chromium_browser_version": "141.0.7390.37",
    "chromium_win64_zip_bytes": 156153766,
    "chromium_url": ("https://cdn.playwright.dev/dbazure/download/playwright/builds/"
                     "chromium/1194/chromium-win64.zip"),
    "verified_at": "2026-10-03",
    "note": "大小来自 registry/CDN 元数据（HEAD Content-Length），未在本机下载；安装体积另含 node_modules 落盘开销。",
}


# ---------------------------------------------------------------- HTML 模板

def _load_template_asset(name: str) -> str:
    p = template_dir() / name
    if not p.is_file():
        raise FileNotFoundError(f"模板资产缺失: {p}")
    return p.read_text(encoding="utf-8")


def build_spec_html(spec: Mapping[str, Any], canvas: Mapping[str, Any],
                    layout: SpecLayout, *, asset_map: Mapping[str, str],
                    mode: str = "animated") -> str:
    """把 spec + 版式烘焙成自包含 HTML（模板固定、无外网加载）。

    ``asset_map``: asset_id → 页面内相对 URL（由 shooter 路由到冻结文件）。
    """
    tmpl = str(spec["template"])
    skeleton = _load_template_asset("spec.html")
    engine = _load_template_asset("motion.js")
    model = {
        "mode": mode,
        "template": tmpl,
        "width": int(canvas["width"]),
        "height": int(canvas["height"]),
        "fps": int(canvas["fps"]),
        "duration_sec": float(spec["duration_sec"]),
        "frame_count": frame_count_for(float(spec["duration_sec"]), int(canvas["fps"])),
        "rect": [int(v) for v in spec["rect"]],
        "font_size_px": int(spec["font_size_px"]),
        "line_height_px": layout.line_height_px,
        "color": [float(v) if i == 3 else int(v) for i, v in enumerate(spec["color"])],
        "font_url": asset_map.get(str(spec["font_asset_id"]), ""),
        "segments": [
            {
                "segment_id": s.segment_id,
                "x": s.x, "y": s.y, "block_height": s.block_height,
                "lines": [{"text": ln.text, "width_px": ln.width_px,
                           "cp_start": ln.cp_start, "cp_end": ln.cp_end} for ln in s.lines],
                "start_sec": float(seg["start_sec"]),
                "end_sec": float(seg["end_sec"]),
                # 该 segment 在其 source_text 中的 code point 区间（与 Python 切片同源）
                "char_start": int(seg["char_start"]),
                "char_end": int(seg["char_end"]),
                "cp_len": int(seg["char_end"]) - int(seg["char_start"]),
            }
            for s, seg in zip(layout.segments, spec["segments"])
        ],
        "nodes": [
            {"node_id": str(n["node_id"]), "label": str(n["label"]),
             "x": layout.node_points.get(str(n["node_id"]), (0.0, 0.0))[0],
             "y": layout.node_points.get(str(n["node_id"]), (0.0, 0.0))[1],
             "lines": list(layout.label_lines.get(str(n["node_id"]), [str(n["label"])]))}
            for n in (spec.get("nodes") or [])
        ],
        "edges": [
            {"edge_id": str(e["edge_id"]), "from_node": str(e["from_node"]),
             "to_node": str(e["to_node"]),
             "start_sec": float(e["start_sec"]), "end_sec": float(e["end_sec"])}
            for e in (spec.get("edges") or [])
        ],
        "subtitle_exclusion_rect": [int(v) for v in canvas["subtitle_exclusion_rect"]],
        "safe_rect": [int(v) for v in canvas["safe_rect"]],
    }
    html = skeleton.replace("__MOTION_ENGINE__", engine)
    html = html.replace("__MOTION_MODEL__", json.dumps(model, ensure_ascii=False))
    html = html.replace("__CANVAS_W__", str(model["width"]))
    html = html.replace("__CANVAS_H__", str(model["height"]))
    return html


# ---------------------------------------------------------------- shooter 协议

@dataclass
class FrameRequest:
    index: int
    t_sec: float
    out_path: Path


@dataclass
class ShooterSpec:
    """一次取帧任务的输入（由 ``render_spec`` 组装，便于记录与重放）。

    ``requests`` 的顺序就是**取帧顺序**——允许乱序（判别性用例按乱序取帧，
    逐字节比对同一 t 的 PNG），``to_dict`` 记录请求摘要供留档。
    """

    html_path: Path
    width: int
    height: int
    fps: int
    mode: str
    requests: list[FrameRequest] = field(default_factory=list)
    #: 页面内相对 URL → 冻结文件绝对路径（字体等；shooter 用路由兑现，页面不联网）
    asset_files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "html_path": str(self.html_path), "width": self.width, "height": self.height,
            "fps": self.fps, "mode": self.mode,
            "assets": dict(self.asset_files),
            "requests": [{"index": r.index, "t_sec": r.t_sec, "out": str(r.out_path)}
                         for r in self.requests],
        }


class FrameShooter(Protocol):  # pragma: no cover - 协议声明
    def shoot(self, spec: ShooterSpec) -> list[Path]:
        ...


class ShooterUnavailable(Exception):
    """取帧底座不可用（缺 node/playwright/chromium）——结构化，不伪造成功。"""

    def __init__(self, missing: Sequence[str], detail: str = ""):
        super().__init__(f"渲染底座不可用，缺少: {list(missing)}; {detail}")
        self.missing = list(missing)
        self.detail = detail


class RuntimeUnavailable(RuntimeError):
    def __init__(self, payload: ShooterUnavailable):
        super().__init__(str(payload))
        self.payload = payload


class BrowserRejected(RuntimeError):
    """浏览器侧**权威测量**拒绝（版式溢出 / code point 不一致）——是拒收，不是底座缺失。"""

    def __init__(self, reason: str, measure: Mapping[str, Any]):
        super().__init__(f"浏览器测量拒收: {reason}")
        self.reason = reason
        self.measure = dict(measure)


class PlaywrightShooter:
    """真实底座：Node + Playwright 定时 seek 截图（缺 runtime ⇒ 结构化 unavailable）。"""

    def __init__(self, *, runtime: dict | None = None, node: str | None = None,
                 timeout_sec: int = 900):
        self.runtime = runtime or runtime_status()
        self.node = node or self.runtime.get("node", {}).get("path")
        self.timeout_sec = int(timeout_sec)

    def available(self) -> bool:
        return bool(self.runtime.get("available"))

    def shoot(self, spec: ShooterSpec) -> list[Path]:
        if not self.available():
            raise RuntimeUnavailable(ShooterUnavailable(
                self.runtime.get("missing") or ["unknown"],
                str(self.runtime.get("install_hint") or "")))
        driver = motion_runtime_dir() / "shoot.mjs"
        if not driver.is_file():
            raise RuntimeUnavailable(ShooterUnavailable(["shoot.mjs"], str(driver)))
        with tempfile.TemporaryDirectory(prefix="motion_shoot_") as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps({
                "html": str(spec.html_path),
                "out_dir": tmp,
                "width": spec.width,
                "height": spec.height,
                "fps": spec.fps,
                "mode": spec.mode,
                "browsers_path": str(_browsers_path()),
                "assets": dict(spec.asset_files),
                "times": [r.t_sec for r in spec.requests],
            }, ensure_ascii=False), encoding="utf-8")
            env = dict(os.environ)
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(_browsers_path())
            proc = subprocess.run(
                [self.node, str(driver), "--plan", str(plan_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout_sec, check=False, env=env)
            result_path = Path(tmp) / "result.json"
            if not result_path.is_file():
                raise RuntimeUnavailable(ShooterUnavailable(
                    [f"shoot.mjs rc={proc.returncode}"],
                    (proc.stderr or proc.stdout or "")[-2000:]))
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if not payload.get("ok", False):
                raise BrowserRejected(payload.get("error") or "browser_rejected",
                                      payload.get("measure") or {})
            produced = [Path(p) for p in payload.get("frames") or []]
            if len(produced) != len(spec.requests):
                raise RuntimeUnavailable(ShooterUnavailable(
                    ["frame_count_mismatch"],
                    f"shooter 返回 {len(produced)} 帧，请求 {len(spec.requests)} 帧"))
            for req, src in zip(spec.requests, produced):
                req.out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(req.out_path))
            return [r.out_path for r in spec.requests]


# ---------------------------------------------------------------- 帧校验

def _browser_reject_problems(exc: "BrowserRejected", sid: str) -> list[dict]:
    """浏览器权威测量 → 结构化拒收问题（layout_overflow / cp_len_mismatch）。"""
    measure = exc.measure or {}
    problems: list[dict] = []
    for item in measure.get("overflow") or []:
        problems.append({"code": "layout_overflow", "axis": "layout",
                         "path": pointer_join("specs", sid),
                         "message": (f"{item.get('where')} 在真实字体下放不下："
                                     f"需 {item.get('needed_px'):.1f}px，可用 {item.get('available_px')}px"),
                         "detail": dict(item)})
    for item in measure.get("cp_mismatch") or []:
        problems.append({"code": "cp_len_mismatch", "axis": "layout",
                         "path": pointer_join("specs", sid),
                         "message": (f"{item.get('where')} 的 code point 数与 Python 侧不一致"
                                     f"（python={item.get('python_cp_len')}, browser={item.get('browser_cp_len')}）"),
                         "detail": dict(item)})
    if not problems:
        problems.append({"code": "browser_rejected", "axis": "render",
                         "path": pointer_join("specs", sid),
                         "message": f"浏览器测量拒收: {exc.reason}", "detail": dict(measure)})
    return problems


def verify_frame_sequence(frames: Sequence[Path], *, width: int, height: int,
                          expected_count: int) -> list[dict]:
    """校验 PNG 序列：数量、全画幅、RGBA、**真的写了 alpha**（不只查通道数）。"""
    from PIL import Image

    problems: list[dict] = []
    if len(frames) != expected_count:
        problems.append({"code": "frame_count_mismatch", "axis": "render", "path": "",
                         "message": f"帧数 {len(frames)} != 期望 {expected_count}",
                         "detail": {"actual": len(frames), "expected": expected_count}})
    for i, path in enumerate(frames):
        path = Path(path)
        if not path.is_file():
            problems.append({"code": "frame_missing", "axis": "render", "path": "",
                             "message": f"帧文件缺失: {path}", "detail": {"index": i}})
            continue
        try:
            with Image.open(path) as im:
                size = im.size
                mode = im.mode
                alpha = im.getchannel("A") if "A" in im.getbands() else None
                extrema = alpha.getextrema() if alpha is not None else None
        except Exception as exc:
            problems.append({"code": "frame_unreadable", "axis": "render", "path": "",
                             "message": f"帧无法解码: {path}: {exc}", "detail": {"index": i}})
            continue
        if tuple(size) != (int(width), int(height)):
            problems.append({"code": "frame_size_mismatch", "axis": "render", "path": "",
                             "message": f"帧 {i} 尺寸 {size} != 画幅 {(width, height)}",
                             "detail": {"index": i, "actual": list(size)}})
        if mode != "RGBA":
            problems.append({"code": "frame_not_rgba", "axis": "render", "path": "",
                             "message": f"帧 {i} 模式 {mode} != RGBA", "detail": {"index": i}})
            continue
        if extrema is None or extrema[0] != 0:
            problems.append({"code": "frame_alpha_opaque", "axis": "render", "path": "",
                             "message": f"帧 {i} 没有透明像素（透明输出未生效）",
                             "detail": {"index": i, "alpha_extrema": list(extrema) if extrema else None}})
    return problems


def frames_identical(a: Path, b: Path) -> bool:
    """逐字节比较（乱序 seek 的判别：同一 t 的 PNG 必须完全一致）。"""
    try:
        return Path(a).read_bytes() == Path(b).read_bytes()
    except OSError:
        return False


# ---------------------------------------------------------------- 渲染一个 spec

@dataclass
class SpecRenderResult:
    spec_id: str
    status: str
    frames_dir: Path | None = None
    frame_count: int = 0
    mp4_path: Path | None = None
    problems: list[dict] = field(default_factory=list)
    detail: str = ""
    seek_times: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id, "status": self.status,
            "frames_dir": str(self.frames_dir) if self.frames_dir else None,
            "frame_count": self.frame_count,
            "mp4_path": str(self.mp4_path) if self.mp4_path else None,
            "problems": list(self.problems), "detail": self.detail,
            "seek_first": self.seek_times[0] if self.seek_times else None,
            "seek_last": self.seek_times[-1] if self.seek_times else None,
        }


def render_spec(spec: Mapping[str, Any], canvas: Mapping[str, Any], layout: SpecLayout, *,
                work_dir: Path, asset_paths: Mapping[str, Path], shooter: FrameShooter | None,
                mode: str = "animated", encode_mp4: bool = True,
                ffmpeg: str | None = None) -> SpecRenderResult:
    """渲染单个 spec：写 HTML → 定时 seek 取帧 → 校验 → 可选编码展示 MP4。

    ``asset_paths``: asset_id → **已冻结**的绝对路径；页面内 URL 用 asset_id 派生
    （只允许本包 id 字符集，无重名冲突，且不经由文件系统路径猜身份）。
    """
    sid = str(spec["spec_id"])
    fps = int(canvas["fps"])
    width, height = int(canvas["width"]), int(canvas["height"])
    times = frame_plan(float(spec["duration_sec"]), fps)
    asset_urls = {str(aid): f"assets/{aid}" for aid in asset_paths}
    asset_files = {f"assets/{aid}": str(Path(p).resolve()) for aid, p in asset_paths.items()}
    work_dir.mkdir(parents=True, exist_ok=True)
    html_path = work_dir / f"{sid}.html"
    html_path.write_text(build_spec_html(spec, canvas, layout, asset_map=asset_urls, mode=mode),
                         encoding="utf-8")

    if shooter is None or (hasattr(shooter, "available") and not shooter.available()):
        status = runtime_status()
        return SpecRenderResult(
            spec_id=sid, status=STATUS_UNAVAILABLE, problems=[{
                "code": "render_runtime_unavailable", "axis": "render",
                "path": pointer_join("specs", sid),
                "message": "渲染底座不可用（缺 Playwright/Chromium），如实记 not_run，不伪造成功",
                "detail": {"missing": status.get("missing"), "install_hint": status.get("install_hint")},
            }], detail="render=not_run", seek_times=times)

    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = work_dir / ".tmp_frames"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    requests = [FrameRequest(index=i, t_sec=t, out_path=tmp_dir / (FRAME_PATTERN % i))
                for i, t in enumerate(times)]
    shoot_spec = ShooterSpec(html_path=html_path, width=width, height=height, fps=fps,
                             mode=mode, requests=requests, asset_files=asset_files)
    try:
        shooter.shoot(shoot_spec)
    except RuntimeUnavailable as exc:
        return SpecRenderResult(
            spec_id=sid, status=STATUS_UNAVAILABLE, problems=[{
                "code": "render_runtime_unavailable", "axis": "render",
                "path": pointer_join("specs", sid),
                "message": f"渲染底座不可用: {exc}",
                "detail": {"missing": exc.payload.missing, "detail": exc.payload.detail},
            }], detail="render=not_run", seek_times=times)
    except BrowserRejected as exc:
        problems = _browser_reject_problems(exc, sid)
        return SpecRenderResult(spec_id=sid, status=STATUS_FAIL, problems=problems,
                                detail=f"browser_rejected:{exc.reason}", seek_times=times)
    except Exception as exc:
        return SpecRenderResult(
            spec_id=sid, status=STATUS_FAIL, problems=[{
                "code": "shooter_failed", "axis": "render", "path": pointer_join("specs", sid),
                "message": f"取帧失败: {type(exc).__name__}: {exc}",
                "detail": {"request": shoot_spec.to_dict()},
            }], detail="shooter_failed", seek_times=times)

    problems = verify_frame_sequence([r.out_path for r in requests],
                                     width=width, height=height, expected_count=len(times))
    if problems:
        return SpecRenderResult(spec_id=sid, status=STATUS_FAIL, problems=problems,
                                detail="帧序列校验失败", seek_times=times)
    frames: list[Path] = []
    for i, req in enumerate(requests):
        final = frames_dir / (FRAME_PATTERN % i)
        shutil.move(str(req.out_path), str(final))
        frames.append(final)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    mp4 = None
    if encode_mp4 and frames:
        mp4 = work_dir / f"{sid}.mp4"
        enc_problems = encode_display_mp4(frames_dir, mp4, width=width, height=height, fps=fps,
                                          ffmpeg=ffmpeg)
        if enc_problems:
            return SpecRenderResult(spec_id=sid, status=STATUS_FAIL, frames_dir=frames_dir,
                                    frame_count=len(frames), problems=enc_problems,
                                    detail="展示 MP4 编码失败", seek_times=times)
    return SpecRenderResult(spec_id=sid, status=STATUS_PASS, frames_dir=frames_dir,
                            frame_count=len(frames), mp4_path=mp4, seek_times=times)


# ---------------------------------------------------------------- FFmpeg

def ffmpeg_binary() -> str | None:
    return shutil.which("ffmpeg")


def encode_display_mp4(frames_dir: Path, out_path: Path, *, width: int, height: int, fps: int,
                       background: tuple[int, int, int] = DEFAULT_DISPLAY_BG,
                       ffmpeg: str | None = None) -> list[dict]:
    """PNG 序列 → H.264/yuv420p **无声** MP4（展示用；alpha 直通合成到显式背景）。

    透明区在 yuv420p 下无法表达，因此展示片显式合成到 ``background``；RGBA PNG
    序列才是叠加产物。``alpha=straight`` 是 §15「RGBA与字体」的显式口径。
    """
    exe = ffmpeg or ffmpeg_binary()
    if not exe:
        return [{"code": "ffmpeg_missing", "axis": "encode", "path": "",
                 "message": "找不到 ffmpeg，可执行文件缺失"}]
    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    if not frames:
        return [{"code": "encode_no_frames", "axis": "encode", "path": "",
                 "message": f"帧序列为空，拒绝编码: {frames_dir}"}]
    color = "0x%02x%02x%02x" % tuple(int(c) for c in background)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", str(int(fps)), "-i", str(Path(frames_dir) / FRAME_PATTERN),
        "-f", "lavfi", "-i", f"color=c={color}:s={int(width)}x{int(height)}:r={int(fps)}",
        # shortest=1 必写：lavfi 背景是无限源，overlay 默认 eof_action=repeat 会**永远编下去**
        "-filter_complex", "[1:v][0:v]overlay=format=auto:alpha=straight:shortest=1[out]",
        "-map", "[out]", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-frames:v", str(len(frames)),
        "-r", str(int(fps)), "-movflags", "+faststart", str(out_path),
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=False)
    except OSError as exc:
        return [{"code": "ffmpeg_exec_failed", "axis": "encode", "path": "",
                 "message": f"ffmpeg 启动失败: {exc}"}]
    if proc.returncode != 0 or not out_path.is_file():
        return [{"code": "ffmpeg_encode_failed", "axis": "encode", "path": "",
                 "message": f"ffmpeg rc={proc.returncode}",
                 "detail": {"stderr": (proc.stderr or "")[-2000:], "argv": argv}}]
    return []


# ---------------------------------------------------------------- 联系表 / 关键帧

def compose_contact_sheet(items: Sequence[dict], out_path: Path, *, font_path: Path,
                          columns: int = 3, cell_pad: int = 12, label_px: int = 18) -> list[dict]:
    """把若干 (label, png) 合成一张 PNG 联系表（PIL，纯离线）。

    ``items``: ``[{"label": str, "image": Path}]``；缺图项记为 ``contact_item_missing``。
    """
    from PIL import Image, ImageDraw, ImageFont

    problems: list[dict] = []
    loaded: list[tuple[str, Any]] = []
    for item in items:
        p = Path(item["image"])
        if not p.is_file():
            problems.append({"code": "contact_item_missing", "axis": "demo", "path": "",
                             "message": f"联系表素材缺失: {p}",
                             "detail": {"label": item.get("label")}})
            continue
        loaded.append((str(item.get("label") or p.stem), Image.open(p).convert("RGBA")))
    if not loaded:
        return problems + [{"code": "contact_sheet_empty", "axis": "demo", "path": "",
                            "message": "联系表没有任何素材"}]
    try:
        font = ImageFont.truetype(str(font_path), label_px)
    except Exception:
        font = ImageFont.load_default()
    cols = max(1, min(int(columns), len(loaded)))
    rows = (len(loaded) + cols - 1) // cols
    cell_w = max(im.width for _, im in loaded)
    cell_h = max(im.height for _, im in loaded) + label_px + 8
    sheet = Image.new("RGBA", (cols * (cell_w + cell_pad) + cell_pad,
                              rows * (cell_h + cell_pad) + cell_pad), (24, 24, 28, 255))
    draw = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(loaded):
        cx = cell_pad + (i % cols) * (cell_w + cell_pad)
        cy = cell_pad + (i // cols) * (cell_h + cell_pad)
        draw.text((cx, cy), label, fill=(235, 235, 240, 255), font=font)
        sheet.alpha_composite(im, (cx, cy + label_px + 4))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return problems


def copy_keyframes(frames_dir: Path, out_dir: Path, indexes: Sequence[int],
                   prefix: str) -> tuple[list[Path], list[dict]]:
    """挑若干帧作为关键帧截图（缺帧如实报，不静默跳过）。"""
    out: list[Path] = []
    problems: list[dict] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in indexes:
        src = Path(frames_dir) / (FRAME_PATTERN % int(i))
        if not src.is_file():
            problems.append({"code": "keyframe_missing", "axis": "demo", "path": "",
                             "message": f"关键帧缺失: {src}", "detail": {"index": int(i)}})
            continue
        dst = out_dir / f"{prefix}_f{i:05d}.png"
        shutil.copyfile(src, dst)
        out.append(dst)
    return out, problems


__all__ = [
    "frame_count_for", "frame_plan", "seek_endpoint", "runtime_status", "motion_runtime_dir",
    "template_dir", "node_binary", "build_spec_html", "FrameRequest", "ShooterSpec",
    "FrameShooter", "PlaywrightShooter", "ShooterUnavailable", "RuntimeUnavailable",
    "BrowserRejected", "verify_frame_sequence",
    "frames_identical", "render_spec", "SpecRenderResult", "encode_display_mp4",
    "compose_contact_sheet", "copy_keyframes", "ffmpeg_binary",
    "STATUS_PASS", "STATUS_NOT_RUN", "STATUS_UNAVAILABLE", "STATUS_FAIL",
    "FRAME_PATTERN", "DOWNLOAD_FACTS", "PINNED_PLAYWRIGHT", "DEFAULT_DISPLAY_BG",
]
