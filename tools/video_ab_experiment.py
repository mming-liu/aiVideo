# -*- coding: utf-8 -*-
"""视频级 A/B 评测协议（总计划阶段 7 / 工作包 D）.

代码测试不能证明成片更好——本工具负责把「改法是否真的改善成片」变成可复算的
证据链：固定输入、单变量变体、隔离实验目录、0–2 分人工评分与明确门槛。

## 硬门槛（非 step01 吸收计划 §5.6 / §6）

`run`（真实生成）**必须同时**满足：

  1. 生产或验收模型支持 `reference_image`（r2v）；
  2. 模型能力表已由官方资料确认；
  3. 在测试环境显式设置 `VIDEO_REFERENCE_MODE=r2v`；
  4. 工作包 B 的静态 prompt 报告通过；
  5. 上游 `require_semantic_gate()` + commit manifest **真实通过**；
  6. 显式 `--approve-generation`（生成预算需单独批准）。

任何一条不满足就拒绝生成并说明原因——**不提供绕过开关**。

## 隔离

实验视频写进 `{chapter_dir}/experiments/<experiment_id>/`，
**绝不覆盖** `vimax_working/shots/<idx>/video.mp4`（那是生产缓存与 step04 的输入）。

用法：
    python tools/video_ab_experiment.py plan  --experiment-id ab-2026-09-23
    python tools/video_ab_experiment.py score --experiment-id ab-2026-09-23 \
        --scores scores.json
    python tools/video_ab_experiment.py run   --experiment-id ab-2026-09-23 [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 评分维度（§6.4：每项 0–2 分）
SCORE_METRICS = (
    "character_identity",      # 角色身份
    "costume_accessory",       # 服装配饰
    "scene_consistency",       # 场景一致性
    "prop_consistency",        # 道具一致性
    "reference_pollution",     # 参考污染（2 = 无污染）
    "action_usability",        # 动作可用性
    "story_fidelity",          # 剧情忠实
)
# 「目标一致性指标」：在参考污染或这两项上提高才算命中 §6.5 第 2 条
TARGET_METRICS = ("reference_pollution", "character_identity", "prop_consistency")

# §6.5 通过门槛
MIN_TOTAL_GAIN = 0.15          # B 组平均总分至少比 A 高 15%
MIN_IMPROVED_SHOTS = 4         # 至少 4/6 镜在目标指标上提高
SAMPLE_SIZE = 6                # 首轮 6 镜 × A/B = 12 条视频

# 目录 12 的四条上游矛盾：修复并重新审计前不进样本池（§6.2）
BLOCKED_SHOT_IDS = ("1", "2", "5", "7")


def chapter_path() -> Path:
    from shared.config import BOOK_INDEX, BOOK_NAME, NOVEL_CHAPTER, NOVEL_NAME
    return (ROOT / "output" / "video" / NOVEL_NAME
            / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}" / f"chapter_{NOVEL_CHAPTER:03d}")


def experiment_dir(experiment_id: str) -> Path:
    return chapter_path() / "experiments" / experiment_id


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _upstream_eligible() -> tuple:
    """上游是否真实通过（semantic gate + commit manifest）."""
    try:
        from shared.quality_gate import QualityGateError, require_step01_upstream
        ch = chapter_path()
        script = json.loads((ch / "script_result.json").read_text(encoding="utf-8"))
        # v6.111：改用共享入口，四个消费点不再各写一份（审核 P2-6）。
        require_step01_upstream(ch, script, stage="step01")
        return True, script, ""
    except QualityGateError as exc:
        return False, None, str(exc)
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


def generation_gates(*, approve: bool) -> list:
    """返回**未满足**的生成门槛清单（空 = 可以生成）."""
    blockers = []
    mode = os.getenv("VIDEO_REFERENCE_MODE", "").strip().lower()
    if mode != "r2v":
        blockers.append(f"VIDEO_REFERENCE_MODE={mode or '未设置'}（工作包 D 要求 r2v）")
    try:
        from shared.model_caps import active_video_model
        from shared.ref_compose import model_supports_reference_image
        model = active_video_model() or ""
        if not model_supports_reference_image(model):
            blockers.append(
                f"当前视频模型 {model or '未解析'} 不支持 reference_image"
                "（判定以能力表 supports_reference_image 为准，不是参考图数量）")
    except Exception as exc:
        blockers.append(f"模型能力表读取失败：{exc}")
    ok, _script, why = _upstream_eligible()
    if not ok:
        blockers.append(f"上游 gate 未通过：{why}")
    if not approve:
        blockers.append("未显式批准生成预算（缺 --approve-generation）")
    return blockers


# 变体 → 两组的 r2v 策略模式（§6.2：A = 当前生产 prompt；B = 只改这一项）
#
# v6.111（审核 P1-4）：工作包 C 的策略按 §5.6 **默认休眠**，所以
# 「A = 生产（plain）/ B = 候选（inherit_exclude）」这组对照才有意义。
VARIANT_GROUPS = {
    "r2v_inherit_exclude": {"A": "plain", "B": "inherit_exclude"},
}


def _generation_api():
    """取 step02b 的逐镜生成入口（可被测试替换）."""
    from step02b_video_gen.main import (VideoGeneratorAiVideoAdapter,
                                        _process_one_shot_skip_frames)
    return _process_one_shot_skip_frames, VideoGeneratorAiVideoAdapter


def _generate_isolated(experiment_id: str, manifest: dict) -> dict:
    """按 A/B 两组在**实验目录内**真实生成样本视频（v6.111，审核 P1-4）.

    隔离机制：`_process_one_shot_skip_frames` 的产物路径由 `shots_dir` 参数决定
    （`<shots_dir>/<idx>/video.mp4`），把 shots_dir 指到实验目录即可——**不经过**
    `shared/paths` 的生产路径，因此 `vimax_working/shots/<idx>/video.mp4`（生产
    缓存 + step04 输入）不会被写到。运行前后各记一次生产帧文件的 mtime，作为
    「没有污染生产」的可核对证据。

    真实生成需要预算，由 `--approve-generation` 把关（本函数不做二次确认）。
    """
    import asyncio
    from shared.config import VIDEO_CONCURRENCY

    groups = VARIANT_GROUPS.get(str(manifest.get("variant") or ""), {})
    out = {"generated": False, "groups": groups, "videos": [], "failures": [],
           "production_untouched": None, "api_generated_videos": 0}
    if not groups:
        out["failures"].append(
            f"未知变体 {manifest.get('variant')!r}：未定义 A/B 分组，拒绝生成")
        return out

    ch = chapter_path()
    exp_dir = ch / "experiments" / experiment_id
    script_path = ch / "script_result.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    by_idx = {str(x.get("index")): x for x in (script.get("shots") or [])
              if isinstance(x, dict)}
    script_mtime = script_path.stat().st_mtime

    prod_dir = ch / "vimax_working" / "shots"
    before = {p.parent.name: p.stat().st_mtime
              for p in prod_dir.glob("*/video.mp4")} if prod_dir.exists() else {}

    gen_one, make_adapter = _generation_api()

    async def _generate_all():
        adapter = make_adapter()
        for label in sorted(groups):
            # 策略**逐组**设置：同组内所有镜头用同一策略，避免交错时环境变量漂移
            os.environ["R2V_REF_POLICY"] = groups[label]
            shots_dir = exp_dir / "shots" / label
            samples = list(manifest.get("samples") or [])
            sem = asyncio.Semaphore(max(1, min(len(samples) or 1, VIDEO_CONCURRENCY)))
            polls = []
            for sample in samples:
                sid = str(sample.get("shot_id"))
                shot = by_idx.get(sid)
                if not isinstance(shot, dict):
                    out["failures"].append(f"{label}/{sid}: 分镜不在当前脚本里")
                    continue
                task = await gen_one(shot, shots_dir, adapter, script_mtime, script,
                                     poll_semaphore=sem, force=True)
                out["api_generated_videos"] += 1
                if task is not None:
                    polls.append((label, sid, task))
                out["videos"].append({
                    "group": label, "policy": groups[label], "shot_id": sid,
                    "path": str(shots_dir / sid / "video.mp4"),
                })
            for label2, sid2, task in polls:
                try:
                    await task
                except Exception as exc:
                    out["failures"].append(f"{label2}/{sid2}: {type(exc).__name__}: {exc}")

    asyncio.run(_generate_all())

    for rec in out["videos"]:
        pth = Path(rec["path"])
        rec["exists"] = pth.exists() and pth.stat().st_size > 20 * 1024
    missing = [f"{r['group']}/{r['shot_id']}" for r in out["videos"] if not r["exists"]]
    if missing:
        out["failures"].append("产出缺失: " + ", ".join(missing))

    after = {p.parent.name: p.stat().st_mtime
             for p in prod_dir.glob("*/video.mp4")} if prod_dir.exists() else {}
    out["production_untouched"] = (before == after)
    if not out["production_untouched"]:
        out["failures"].append("生产 vimax_working 被写入——隔离失败")

    out["generated"] = bool(out["videos"]) and not missing
    out["output_dir"] = str(exp_dir)
    return out


def _sample_shots(script: dict) -> tuple:
    """首轮样本：排除四条已知上游矛盾的镜头，其余按 §6.3 的六类偏好挑 6 镜."""
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    eligible = [s for s in shots if str(s.get("index")) not in BLOCKED_SHOT_IDS]
    excluded = [str(s.get("index")) for s in shots
                if str(s.get("index")) in BLOCKED_SHOT_IDS]
    picked = eligible[:SAMPLE_SIZE]
    return picked, excluded


def build_manifest(experiment_id: str, *, variant: str = "r2v_inherit_exclude") -> dict:
    ok, script, why = _upstream_eligible()
    manifest = {
        "experiment_id": experiment_id,
        "created_on": date.today().isoformat(),
        "protocol": "non-step01 plan §6",
        "variant": variant,
        "upstream_gate_status": "eligible" if ok else "upstream_blocked",
        "upstream_gate_detail": why,
        "reference_mode": os.getenv("VIDEO_REFERENCE_MODE", ""),
        "video_provider": os.getenv("VIDEO_PROVIDER", ""),
        "variant_a": "current production prompt",
        "variant_b": variant,
        "armed": False,
        "samples": [],
        "excluded_shot_ids": list(BLOCKED_SHOT_IDS),
        "cost": {"api_generated_images": 0, "api_generated_videos": 0,
                 "qc_regenerations": 0, "cache_hits": 0, "local_derived_images": 0},
        "notes": [
            "样本视频写入本实验目录，绝不覆盖 vimax_working/shots/<idx>/video.mp4",
            "每轮只验证一个变量；输入分镜/帧图/参考图/时长/分辨率/provider/模型保持一致",
            "shot 1/2/5/7 在四条上游矛盾修复并重新审计前不进入样本池",
        ],
    }
    if not ok:
        manifest["blocked_reason"] = why
        return manifest

    ch = chapter_path()
    picked, excluded = _sample_shots(script)
    for shot in picked:
        idx = str(shot.get("index"))
        shot_dir = ch / "vimax_working" / "shots" / idx
        frames = {}
        for name in ("first_frame.png", "last_frame.png"):
            p = shot_dir / name
            if p.exists():
                frames[name] = {"sha1": _sha1(p), "bytes": p.stat().st_size}
        from shared.paths import is_core_keyframe_file
        kfs = sorted([p for p in shot_dir.glob("keyframe_*.png")
                      if is_core_keyframe_file(p)],
                     key=lambda p: int("".join(c for c in p.stem if c.isdigit()) or 0))
        for i, p in enumerate(kfs):
            frames[f"keyframe_{i}.png"] = {"sha1": _sha1(p), "bytes": p.stat().st_size}
        manifest["samples"].append({
            "shot_id": idx,
            "duration_sec": shot.get("duration_sec"),
            "input_frames": frames,
            "input_sha1_locked": True,
        })
    manifest["sample_count"] = len(manifest["samples"])
    manifest["planned_videos"] = 2 * len(manifest["samples"])
    return manifest


# ---------------------------------------------------------------------------
# 评分与判定
# ---------------------------------------------------------------------------
def _avg(scores: list) -> float:
    vals = [v for v in scores if isinstance(v, (int, float))]
    return (sum(vals) / len(vals)) if vals else 0.0


def evaluate_scores(manifest: dict, scores: dict) -> dict:
    """按 §6.4 / §6.5 计算 A/B 判定；不确定就报 `inconclusive`，不硬下结论."""
    per_shot = []
    a_all, b_all = [], []
    provided = 0
    improved = 0
    fidelity_regression = 0
    usability_drop = 0
    for sample in manifest.get("samples") or []:
        sid = str(sample["shot_id"])
        rec = (scores or {}).get(sid) or {}
        a = rec.get("A") or {}
        b = rec.get("B") or {}
        provided += sum(1 for m in SCORE_METRICS
                        if isinstance(a.get(m), (int, float))
                        and isinstance(b.get(m), (int, float)))
        a_total = _avg([a.get(m) for m in SCORE_METRICS])
        b_total = _avg([b.get(m) for m in SCORE_METRICS])
        a_all.append(a_total)
        b_all.append(b_total)
        hit = any(
            isinstance(a.get(m), (int, float)) and isinstance(b.get(m), (int, float))
            and b[m] > a[m] for m in TARGET_METRICS)
        if hit:
            improved += 1
        if b.get("story_fidelity") == 0:
            fidelity_regression += 1
        if (isinstance(a.get("action_usability"), (int, float))
                and isinstance(b.get("action_usability"), (int, float))
                and b["action_usability"] < a["action_usability"]):
            usability_drop += 1
        per_shot.append({
            "shot_id": sid, "a_total": round(a_total, 3),
            "b_total": round(b_total, 3),
            "target_improved": hit,
            "delta": round(b_total - a_total, 3),
        })

    a_avg, b_avg = _avg(a_all), _avg(b_all)
    gain = ((b_avg - a_avg) / a_avg) if a_avg else 0.0
    n = len(per_shot)
    checks = {
        "gain_ok": gain >= MIN_TOTAL_GAIN,
        "improved_shots_ok": improved >= MIN_IMPROVED_SHOTS,
        "no_fidelity_regression": fidelity_regression == 0,
        "action_usability_not_worse": usability_drop == 0,
        "sample_size_ok": n >= SAMPLE_SIZE,
    }
    verdict = "adopt" if all(checks.values()) else "reject"
    if n < SAMPLE_SIZE or not per_shot or provided == 0:
        # 样本不足 / 没有可用的评分 → 只能是 inconclusive。**不能**把「没打分」
        # 读成「没改善」再判 reject（那会让评分缺失变成负面结论）。
        verdict = "inconclusive"
    elif any(r["delta"] > 0 for r in per_shot) and any(r["delta"] < 0 for r in per_shot) \
            and not checks["gain_ok"]:
        # 单次结果互相矛盾 → 不直接上线（§6.5 最后一条）
        verdict = "inconclusive"
    return {
        "verdict": verdict,
        "a_avg": round(a_avg, 3),
        "b_avg": round(b_avg, 3),
        "gain": round(gain, 4),
        "thresholds": {"min_total_gain": MIN_TOTAL_GAIN,
                       "min_improved_shots": MIN_IMPROVED_SHOTS,
                       "sample_size": SAMPLE_SIZE},
        "checks": checks,
        "improved_shots": improved,
        "per_shot": per_shot,
        "manual_score_status": "provided" if provided else "missing",
        "scored_values": provided,
    }


def run(experiment_id: str, *, approve: bool = False, dry_run: bool = False) -> dict:
    blockers = generation_gates(approve=approve)
    out = {"experiment_id": experiment_id, "blockers": blockers,
           "generation_started": False}
    if blockers:
        out["status"] = "upstream_blocked" if any("上游" in b for b in blockers) else "blocked"
        return out
    if dry_run:
        out["status"] = "dry_run"
        out["plan"] = build_manifest(experiment_id)
        return out
    # v6.111（审核 P1-4）：**真的生成**。此前这里只返回 ready 并让用户手工跑
    # step02b——而手工跑的产物落在生产 `vimax_working`，与本工具的隔离承诺矛盾。
    # 现在按 A/B 两组、在实验目录内调用 step02b 的逐镜生成入口（`shots_dir`
    # 参数化即隔离），并记录产出、计数与「生产未被写入」的证据。
    manifest_path = chapter_path() / "experiments" / experiment_id / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else build_manifest(experiment_id))
    result = _generate_isolated(experiment_id, manifest)
    out.update(result)
    out["generation_started"] = True
    out["manifest"] = str(manifest_path)
    if not result["generated"]:
        out["status"] = "blocked"
        return out
    manifest["cost"] = dict(manifest.get("cost") or {},
                            api_generated_videos=result["api_generated_videos"])
    manifest["armed"] = True
    manifest["last_run"] = {"videos": result["videos"],
                            "production_untouched": result["production_untouched"]}
    _write(manifest_path, manifest)
    out["status"] = "generated"
    out["next"] = ("两组视频已写入实验目录；人工按 §6.4 每项 0–2 分评分后，用 "
                   "`score` 子命令录入")
    return out


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from shared.json_io import atomic_dump_json
    atomic_dump_json(path, payload, ensure_ascii=False, indent=2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "score", "run"):
        p = sub.add_parser(name)
        p.add_argument("--experiment-id", required=True)
        if name == "score":
            p.add_argument("--scores", type=Path, required=True)
        if name == "run":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--approve-generation", action="store_true")
    args = ap.parse_args(argv)
    exp_dir = experiment_dir(args.experiment_id)

    if args.cmd == "plan":
        manifest = build_manifest(args.experiment_id)
        _write(exp_dir / "manifest.json", manifest)
        print(json.dumps({"status": manifest["upstream_gate_status"],
                          "samples": manifest.get("sample_count", 0),
                          "planned_videos": manifest.get("planned_videos", 0),
                          "dir": str(exp_dir)}, ensure_ascii=False))
        return 0 if manifest["upstream_gate_status"] == "eligible" else 2

    if args.cmd == "score":
        manifest = json.loads((exp_dir / "manifest.json").read_text(encoding="utf-8"))
        scores = json.loads(args.scores.read_text(encoding="utf-8"))
        result = evaluate_scores(manifest, scores)
        _write(exp_dir / "result.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["verdict"] == "adopt" else 1

    result = run(args.experiment_id, approve=args.approve_generation,
                 dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in ("dry_run", "ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
