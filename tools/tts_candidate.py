"""tools.tts_candidate - T 包离线候选 CLI：plan / replay / inspect（计划 §T4、§11）.

```text
python tools/tts_candidate.py plan    --script FILE --out-dir DIR
python tools/tts_candidate.py replay  --fixture DIR --out-dir DIR
python tools/tts_candidate.py inspect --manifest FILE [--video-candidates FILE]
```

三个子命令的**边界**（§T4）：

- `plan`：只生产 `SpeechUnits`（`<out-dir>/speech_units.json`），不建 attempt、不发请求；
- `replay`：走**真实 Step03 候选入口**（`step03_tts_audio.main._run_candidate`）+ **桩
  provider** 产出**各不相同**的测试音 WAV，再经同一套「无损分段 → PCM 拼接 → 消费格式
  编码 → 身份侧车 → attempt/manifest/accepted」事务；不发任何真实模型请求；
- `inspect`：独立消费者——按 manifest 逐 unit 核**媒体 + 身份 + 摘要**；给了
  `--video-candidates` 时再走 `shared.candidate_inputs.resolve_candidate_inputs`
  做跨包解析（这是 S/C/I 阶段消费这份 manifest 的同一入口）。

fixture 目录形态（`replay`）：`script.json`（或 `script_result.json`）是必填；
`video_manifest.json`（可选）是视频候选 manifest——给了就作为素材来源与 scope/
script_revision 的**权威来源**（与声音候选 manifest 交叉核对时要逐字一致）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.enhancement_io import (EXIT_INVALID_INPUT, EXIT_OK, EXIT_REJECTED,
                                   EXIT_TOOL_FAIL, EnhancementError)
from step03_tts_audio.cli import ENV_VOICE_REGISTRY_PATH

#: 桩 provider 在候选 manifest 里登记的名字（**不是**任何真实模型）
STUB_PROVIDER_NAME = "tts_candidate_stub"


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path:
        return path
    bin_dir = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if bin_dir:
        for cand in (Path(bin_dir) / (name + ".exe"), Path(bin_dir) / name):
            if cand.exists():
                return str(cand)
    raise RuntimeError(f"找不到 {name}（replay 需要宿主 FFmpeg）")


class StubTTSProvider:
    """候选桩 provider：**每个 unit 一个不同频率的测试音**（可判别，不是静音）.

    - 能力事实按真声明（`supports_speaker` / `supports_instruction` / 单次上限），
      这样分段、参数判定、speaker 校验都会真的走到；
    - 写出**真实 WAV**并返回它的实际路径（与请求后缀不同也在所不惜——正是
      `save_path` 契约要覆盖的形态）；
    - 文本越长音越长（有上限），使时长窗口判据会被真实触发。
    """

    PROVIDER_NAME = STUB_PROVIDER_NAME

    def __init__(self, *, base_freq: int = 420, step: int = 60,
                 max_chars_per_call: int = 60, sample_rate: int = 24000):
        self.base_freq = int(base_freq)
        self.step = int(step)
        self.max_chars_per_call = int(max_chars_per_call)
        self.sample_rate = int(sample_rate)
        self.calls: list = []

    def capabilities(self):
        from tts.models import ProviderCapabilities
        return ProviderCapabilities(
            provider=STUB_PROVIDER_NAME, max_chars_per_call=self.max_chars_per_call,
            supports_instruction=True, supports_rate=False, supports_volume=False,
            supports_speaker=True, legal_speakers=("stub-narrator", "stub-role-a", "stub-role-b"),
            output_formats=("wav",), sample_rates=(self.sample_rate,),
            model_id="stub-tts-v1", model_revision="stub-r1", real_model=False,
            evidence="离线桩：只做协议/分段/身份/事务验收，**不**代表任何模型音质或性能")

    def synthesize(self, req, output=None):
        from tts.models import TTSFailureResult, TTSSuccessResult
        text = str(getattr(req, "text", "") or "")
        if not text:
            return TTSFailureResult(error="空正文", code="empty_text", field_path="text")
        speaker = str(getattr(req, "effective_speaker", lambda: "")() or "")
        if speaker not in self.capabilities().legal_speakers:
            return TTSFailureResult(
                error="非法 speaker %r（桩的合法集合: %s）"
                      % (speaker, list(self.capabilities().legal_speakers)),
                code="speaker_not_legal", field_path="speaker")
        freq = self.base_freq + (int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % 12) * self.step
        dur = min(0.12 * len(text) + 0.35, 5.0)
        out = Path(output).with_suffix(".wav") if output else None
        if out is None:
            return TTSFailureResult(error="缺少 output 路径", code="invalid_input",
                                    field_path="output")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([_tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi",
                        "-i", "sine=frequency=%d:duration=%.3f:sample_rate=%d"
                        % (freq, dur, self.sample_rate),
                        "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                       check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
        self.calls.append({"chars": len(text), "freq": freq, "dur": round(dur, 3),
                           "speaker": speaker, "path": str(out)})
        return TTSSuccessResult(save_path=str(out), actual_format="wav",
                                actual_sample_rate=self.sample_rate,
                                applied={"instruction": bool(getattr(req, "instruction", "")),
                                         "rate": False, "volume": False})


# ---------------------------------------------------------------- 子命令

def _register_stub_capabilities() -> None:
    """把桩的能力事实登记进 provider 能力表（`plan` 要按它预演分段）.

    桩的 provider 名不是任何真实模型；登记只影响本进程的这次 CLI 调用。
    """
    from tts.service import register_capabilities
    register_capabilities(STUB_PROVIDER_NAME, StubTTSProvider().capabilities())


def _cmd_plan(args) -> int:
    _register_stub_capabilities()
    # 音色注册表隔离：`plan` 也要分配 profile（说话人/旁白各一条），
    # 绝不能让 get_registry() 隐式写生产 global/voice_registry.json（§T4）。
    out_dir = Path(args.out_dir).resolve()
    os.environ[ENV_VOICE_REGISTRY_PATH] = str(out_dir / "voice_registry.json")
    from step03_tts_audio.shot_tts import build_speech_units, current_scope, script_revision_of
    from tts.synthesis_job import write_speech_unit_plan

    script_path = Path(args.script)
    if not script_path.is_file():
        print(json.dumps({"error": "script_missing", "path": str(script_path)},
                         ensure_ascii=False))
        return EXIT_INVALID_INPUT
    script = json.loads(script_path.read_text(encoding="utf-8"))
    scope = {"chapter": int(args.chapter or 1), "book_index": 1, "book_name": ""}
    revision = script_revision_of(script)
    units = build_speech_units(script, scope=scope, script_revision=revision,
                               provider_name=args.provider or None)
    target = write_speech_unit_plan(out_dir, units, scope=scope,
                                   script_revision=revision,
                                   provider=args.provider or "", source="plan")
    print(json.dumps({"ok": True, "plan": str(target), "n_units": len(units),
                      "units": [{"unit_id": u.unit_id, "kind": u.kind,
                                 "chars": len(u.text), "provider": u.provider,
                                 "speaker": u.speaker, "model_id": u.model_id}
                                for u in units]}, ensure_ascii=False))
    return EXIT_OK


def _adopt_candidate_env(candidate_root: Path, attempt_id: str) -> None:
    """在 import step03 入口**之前**登记隔离（日志/注册表不落生产）."""
    from step03_tts_audio.cli import (ENV_ATTEMPT_ID, ENV_CANDIDATE_ROOT,
                                      ENV_VOICE_REGISTRY_PATH)
    os.environ[ENV_CANDIDATE_ROOT] = str(candidate_root)
    os.environ[ENV_ATTEMPT_ID] = str(attempt_id)
    os.environ[ENV_VOICE_REGISTRY_PATH] = str(candidate_root / "voice_registry.json")


def _fixture_script(fixture: Path) -> Path:
    for name in ("script.json", "script_result.json"):
        p = fixture / name
        if p.is_file():
            return p
    raise EnhancementError("fixture_script_missing",
                           f"fixture 目录里没有 script.json / script_result.json: {fixture}",
                           exit_code=EXIT_INVALID_INPUT, field_path="fixture")


def _cmd_replay(args) -> int:
    from step03_tts_audio.cli import new_attempt_id
    from shared.media_inputs import load_candidate_clips

    _register_stub_capabilities()
    fixture = Path(args.fixture)
    if not fixture.is_dir():
        print(json.dumps({"error": "fixture_missing", "path": str(fixture)},
                         ensure_ascii=False))
        return EXIT_INVALID_INPUT
    candidate_root = Path(args.out_dir).resolve()
    attempt_id = args.attempt_id or new_attempt_id("tts-replay")
    _adopt_candidate_env(candidate_root, attempt_id)

    # 真实入口（候选分支）——import 必须在 env 登记之后，日志才能落 attempt 内
    import step03_tts_audio.main as stage3
    from step03_tts_audio.shot_tts import script_revision_of

    script = json.loads(_fixture_script(fixture).read_text(encoding="utf-8"))
    clips_ctx = None
    video_manifest = args.video_candidates or (
        str(fixture / "video_manifest.json")
        if (fixture / "video_manifest.json").is_file() else None)
    if video_manifest:
        clips_ctx = load_candidate_clips(video_manifest, expect_package="video")
    # scope/script_revision 与视频候选 manifest **逐字一致**（交叉核对会核它）
    scope = (dict(clips_ctx.scope) if clips_ctx is not None and clips_ctx.scope
             else {"chapter": int(args.chapter or 1), "book_index": 1, "book_name": ""})
    revision = (clips_ctx.script_revision if clips_ctx is not None
                and clips_ctx.script_revision else script_revision_of(script))
    source = (clips_ctx.source if clips_ctx is not None and clips_ctx.source else "fixture")

    provider = StubTTSProvider()
    try:
        artifacts = stage3._run_candidate(
            candidate_root=candidate_root, attempt_id=attempt_id,
            experiment_id=args.experiment_id or "tts-replay", scope=scope,
            revision=revision, source=source, clips_ctx=clips_ctx, script=script,
            provider=provider)
    except EnhancementError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False))
        return exc.exit_code
    except Exception as exc:
        print(json.dumps({"error": "replay_failed", "message": str(exc)},
                         ensure_ascii=False))
        return EXIT_TOOL_FAIL

    manifest = candidate_root / "attempts" / attempt_id / "manifest.json"
    print(json.dumps({
        "ok": True, "attempt_id": attempt_id, "manifest": str(manifest),
        "n_units": len(artifacts), "provider_calls": len(provider.calls),
        "real_model": "not_run",
        "units": [{"unit_id": a.unit_id, "kind": a.kind, "path": str(a.path),
                   "segments": len(a.segments), "duration_sec": a.duration_sec}
                  for a in artifacts],
    }, ensure_ascii=False))
    return EXIT_OK


def _cmd_inspect(args) -> int:
    from shared.audio_identity import verify_identity
    from shared.enhancement_io import sha256_file

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(json.dumps({"error": "manifest_missing", "path": str(manifest_path)},
                         ensure_ascii=False))
        return EXIT_INVALID_INPUT
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list = []
    if str(manifest.get("package")) != "tts":
        problems.append("package 不是 tts: %r" % manifest.get("package"))
    units = manifest.get("units") or []
    if not units:
        problems.append("units 为空（没有声音单元可消费）")
    report = []
    for rec in units:
        unit_id = rec.get("unit_id")
        path = Path(str(rec.get("path") or ""))
        item = {"unit_id": unit_id, "kind": rec.get("kind"), "path": str(path),
                "status": "pass", "issues": []}
        if not path.is_absolute():
            item["issues"].append("path 不是绝对路径")
        elif not path.is_file():
            item["issues"].append("媒体不存在")
        else:
            actual = sha256_file(path)
            if str(rec.get("sha256") or "") != actual:
                item["issues"].append("manifest sha256 与盘上不一致")
            ident = rec.get("voice_identity") or {}
            if str(ident.get("text_hash") or "") != str(rec.get("text_sha256") or ""):
                item["issues"].append("text_sha256 与 voice_identity.text_hash 不一致")
            reason = verify_identity(path, ident)
            if reason:
                item["issues"].append(reason)
            item["duration_sec"] = (ident or {}).get("duration_sec")
            item["request_key"] = (ident or {}).get("request_key")
            item["segments"] = len((ident or {}).get("segments") or [])
        if item["issues"]:
            item["status"] = "failed"
            problems.extend("%s: %s" % (unit_id, i) for i in item["issues"])
        report.append(item)

    cross = None
    if args.video_candidates:
        from shared.candidate_inputs import resolve_candidate_inputs
        try:
            resolved = resolve_candidate_inputs(args.video_candidates, str(manifest_path),
                                                expect_scope=manifest.get("scope"),
                                                expect_script_revision=manifest.get("script_revision"))
            cross = {"ok": True, "n_units": len(resolved.get("units") or {}),
                     "scope": resolved.get("scope"),
                     "script_revision": resolved.get("script_revision"),
                     "units": sorted((resolved.get("units") or {}).keys())}
        except EnhancementError as exc:
            cross = {"ok": False, "payload": exc.payload()}
            problems.append("跨包解析被拒收: %s" % exc.message)

    print(json.dumps({"ok": not problems, "manifest": str(manifest_path),
                      "n_units": len(units), "units": report, "problems": problems,
                      "cross_package": cross,
                      "real_model": (manifest.get("capabilities") or {}).get("real_model",
                                                                            "not_run")},
                     ensure_ascii=False, indent=2))
    return EXIT_OK if not problems else EXIT_REJECTED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="T 包离线候选 CLI（默认不发任何真实模型请求）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="只生产 SpeechUnits（不建 attempt、不发请求）")
    p_plan.add_argument("--script", required=True, metavar="FILE")
    p_plan.add_argument("--out-dir", required=True, metavar="DIR")
    p_plan.add_argument("--provider", default="", metavar="NAME",
                        help="按该 provider 的能力上限预演分段（不实例化 provider）")
    p_plan.add_argument("--chapter", type=int, default=None, metavar="N")
    p_plan.set_defaults(func=_cmd_plan)

    p_replay = sub.add_parser("replay", help="真实候选入口 + 桩 provider（产出可判别测试音）")
    p_replay.add_argument("--fixture", required=True, metavar="DIR")
    p_replay.add_argument("--out-dir", required=True, metavar="DIR")
    p_replay.add_argument("--video-candidates", default=None, metavar="FILE")
    p_replay.add_argument("--attempt-id", default=None, metavar="ID")
    p_replay.add_argument("--experiment-id", default=None, metavar="ID")
    p_replay.add_argument("--chapter", type=int, default=None, metavar="N")
    p_replay.set_defaults(func=_cmd_replay)

    p_inspect = sub.add_parser("inspect", help="独立消费者：核媒体 + 身份（可选跨包解析）")
    p_inspect.add_argument("--manifest", required=True, metavar="FILE")
    p_inspect.add_argument("--video-candidates", default=None, metavar="FILE")
    p_inspect.set_defaults(func=_cmd_inspect)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except EnhancementError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False))
        return exc.exit_code
    except KeyboardInterrupt:
        return EXIT_TOOL_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
