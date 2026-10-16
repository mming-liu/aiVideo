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

## 候选事务改造：**明确拒绝**（2026-10-03，计划 §15「绕过入口」行）

本工具的 `run`（真实生成）仍走**旧提交路径**：不冻结请求、不保证"一次确认一次
POST"，且 `_poll_video` 的生产分支会把结果副本写进正式 `clips/`——与"实验不碰正式
素材"直接冲突。改造它需要接入候选事务，成本高且超出本包范围，因此这里**显式拒绝**
真实生成（零 POST、零正式写入）并给出替代入口：

* 候选生成：`python -m step02b_video_gen.main --chapter N --shots … --candidate-root ROOT/video`
  （prepared 冻结 + 一次 POST + attempt 隔离 + accepted 事务）；
* 离线规划/回放：`python tools/video_reroll.py plan|replay|report`。

`plan` / `score` 两个离线子命令不受影响。

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

#: 真实生成被明确拒绝的出口码（计划 §15「绕过入口」行；不提供绕过开关）
ENHANCEMENT_REFUSAL_CODE = "enhancement_path_not_wired"

ENHANCEMENT_REFUSAL_MESSAGE = (
    "本工具的 run（真实生成）尚未接入候选事务，已明确拒绝：旧提交路径不冻结请求、"
    "不保证「一次确认一次 POST」，且会把结果副本写进正式 clips/。"
    "请改用候选入口 `python -m step02b_video_gen.main --chapter N --shots … "
    "--candidate-root ROOT/video`（prepared 冻结 + 一次 POST + attempt 隔离 + accepted 事务），"
    "或离线入口 `python tools/video_reroll.py plan|replay|report`。plan/score 不受影响。"
)
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
    """**拒绝**真实生成：本工具的生成路径尚未接入候选事务（计划 §15「绕过入口」行）.

    原因（可复核）：本函数经 ``_process_one_shot_skip_frames(..., candidate_context=None)``
    走旧提交路径——不冻结请求、不保证"一次确认一次 POST"，且生产分支会把结果副本写进
    正式 ``clips/``。为避免**重复提交**与**覆盖正式素材**，这里在任何提交之前返回明示
    拒绝（``api_generated_videos=0``、零 PUT/POST、零正式写入），并指向替代入口。

    契约：返回 ``generated=False`` + ``code=ENHANCEMENT_REFUSAL_CODE``；绝不提交。
    """
    groups = VARIANT_GROUPS.get(str(manifest.get("variant") or ""), {})
    failures = []
    if not groups:
        # 未知变体是**独立于**下面那条拒绝的输入错误：这个输入在改造前也一样被拒，
        # 因此单独报出（保留原失败出口），不与「生成路径未接通」混为一谈。
        failures.append(
            f"未知变体 {manifest.get('variant')!r}：未定义 A/B 分组，拒绝生成")
    failures.append(ENHANCEMENT_REFUSAL_MESSAGE)
    return {"generated": False, "groups": groups, "videos": [], "failures": failures,
            "production_untouched": None, "api_generated_videos": 0,
            "code": ENHANCEMENT_REFUSAL_CODE,
            "output_dir": str(chapter_path() / "experiments" / experiment_id)}


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
    # v6.111（审核 P1-4）曾让这里"真的生成"：按 A/B 两组在实验目录内调用 step02b
    # 的逐镜入口。但该路径是**旧提交路径**——不冻结请求、不保证"一次确认一次 POST"，
    # 且生产分支会把结果副本写进正式 `clips/`。2026-10-03（计划 §15「绕过入口」行）
    # 起 `_generate_isolated` 改为**明确拒绝**：零 POST、零正式写入。这里保留调用点，
    # 使拒绝成为 run 的唯一出口，而不是悄悄退回 ready。
    manifest_path = chapter_path() / "experiments" / experiment_id / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else build_manifest(experiment_id))
    result = _generate_isolated(experiment_id, manifest)
    out.update(result)
    out["generation_started"] = bool(result.get("api_generated_videos"))
    out["manifest"] = str(manifest_path)
    if not result["generated"]:
        out["status"] = result.get("code") or "blocked"
        if result.get("code") == ENHANCEMENT_REFUSAL_CODE:
            out["next"] = ENHANCEMENT_REFUSAL_MESSAGE
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
            # 候选/增强参数在这里**只用于识别并拒绝**（计划 §15「绕过入口」行）：
            # 该工具未接入候选事务，绝不能借这些参数产生提交或写入正式素材。
            p.add_argument("--candidate-root", default=None)
            p.add_argument("--plan-only", action="store_true")
            p.add_argument("--resume", default=None)
    args = ap.parse_args(argv)
    exp_dir = experiment_dir(args.experiment_id)

    if args.cmd == "run" and (args.candidate_root or args.plan_only or args.resume):
        print(json.dumps({"status": ENHANCEMENT_REFUSAL_CODE,
                          "code": ENHANCEMENT_REFUSAL_CODE,
                          "message": ENHANCEMENT_REFUSAL_MESSAGE,
                          "generation_started": False,
                          "api_generated_videos": 0}, ensure_ascii=False))
        return 2

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
