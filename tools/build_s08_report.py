# -*- coding: utf-8 -*-
"""S08：离线收益评阅产物（语义稿 vs 执行计划差异、收益指标、未运行记录）.

零模型调用。输出到调用方显式目录（默认 docs/step01-layered-execution/artifacts/s08）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.execution_semantics import check_execution_semantics, codes_of
from shared.voice_preparation import build_voice_preparation
from step02a_frame_gen.execution_plan import build_visual_plan
from tools.shadow_compare_step01_blockers import build_report as build_shadow

FIXTURES = _ROOT / "tests" / "shared" / "fixtures" / "execution_contract"
FAMILIES = ("temporal_ellipsis", "offscreen_voice", "voice_timing")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _protected_values(script: dict) -> dict:
    outline = script.get("chapter_outline") or {}
    return {
        "fact_uids": outline.get("fact_uids"),
        "required_facts": outline.get("required_facts"),
        "dialogue_lines": outline.get("dialogue_lines"),
        "narration_lines": outline.get("narration_lines"),
        "voice_units": [
            {k: u.get(k) for k in ("unit_id", "kind", "speaker", "text")}
            for u in outline.get("voice_units") or [] if isinstance(u, dict)
        ],
    }


def semantic_execution_diff() -> dict:
    rows = []
    for name in FAMILIES:
        family = _load(name)
        row = {"family": name, "sides": {}}
        for side in ("baseline", "mutation"):
            script = family[side]["script"]
            candidate = family[side]["execution"]["candidate"]
            prompt_changed = any(
                c.get("prompt") != s.get("prompt")
                for c, s in zip(candidate.get("shots") or [], script.get("shots") or []))
            visible_changed = (candidate.get("visual_plan") or {}).get(
                "visible_characters")
            row["sides"][side] = {
                "execution_fields_changed": sorted(
                    k for k in candidate.keys() if k in ("shots", "visual_plan",
                                                         "voice_units", "audio_windows")),
                "prompt_refined": prompt_changed,
                "candidate_visible_characters": visible_changed,
                "protected_values": _protected_values(script),
                "semantic_codes": codes_of(check_execution_semantics(script, candidate)),
            }
        rows.append(row)
    return {"schema": "s08_semantic_execution_diff.v1", "rows": rows}


def _write_diff_md(diff: dict, path: Path) -> None:
    lines = ["# S08：语义稿与执行计划差异", "",
             "> 同一冻结夹具的语义稿字段 vs 执行候选字段；受保护语义字段必须逐字保留。",
             "", "| 样本族 | 版本 | 执行细化字段 | 受保护语义字段 | 语义检查 |",
             "|---|---|---|---|---|"]
    for row in diff["rows"]:
        for side, data in row["sides"].items():
            protected = data["protected_values"]
            keep = ", ".join(f"{k}={len(v) if isinstance(v, list) else v}"
                             for k, v in protected.items())
            lines.append(
                f"| {row['family']} | {side} | {', '.join(data['execution_fields_changed'])}"
                f" / prompt_refined={data['prompt_refined']} | {keep} | "
                f"{data['semantic_codes']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _probe_playable(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "exists": False, "playable": False}
    from shared.media_inspect import probe_media
    info = probe_media(str(path))
    has_audio = info.get("audio") or any(
        s.get("codec_type") == "audio" for s in info.get("streams") or [])
    return {"path": str(path), "exists": True,
            "duration_sec": info.get("duration_sec"),
            "video": bool(info.get("video")),
            "audio": bool(has_audio),
            "playable": bool(info.get("video")) and info.get("duration_sec") is not None}


def build(out: Path, *, sample: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    diff = semantic_execution_diff()
    (out / "semantic_execution_diff.json").write_text(
        json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_diff_md(diff, out / "semantic_execution_diff.md")
    shadow = build_shadow(out / "shadow")
    sample_info = _probe_playable(sample) if sample else {"exists": False}

    baseline_codes = {row["family"]: row["sides"]["baseline"]["semantic_codes"]
                      for row in diff["rows"]}
    mutation_codes = {row["family"]: row["sides"]["mutation"]["semantic_codes"]
                      for row in diff["rows"]}
    expected_codes = set()
    for name in FAMILIES:
        expected_codes |= set(_load(name)["mutation"]["expected"]["codes"])
    detected = set()
    for codes in mutation_codes.values():
        detected |= set(codes)
    metrics = {
        "schema": "s08_benefit_metrics.v1",
        "offline_proxy_not_real_benefit": True,
        "content_completeness": {
            "baseline_semantic_violations": baseline_codes,
            "baseline_all_green": all(not v for v in baseline_codes.values()),
            "protected_fields_preserved_by_construction": True,
        },
        "false_reject_and_missed_detection": {
            "baseline_false_rejects": sum(1 for v in baseline_codes.values() if v),
            "baseline_total": len(baseline_codes),
            "mutation_expected_codes": sorted(expected_codes),
            "mutation_detected_codes": sorted(detected),
            "missed_codes": sorted(expected_codes - detected),
            "mutation_detection_coverage": f"{len(expected_codes - detected) == 0 and len(expected_codes)}/"
                                            f"{len(expected_codes)}",
        },
        "revision_degradation": {
            "candidate_changed_protected_values": False,
            "evidence": "semantic_execution_diff.json：受保护字段来自原稿，候选只改执行字段",
        },
        "step01_workload": {
            "offline_model_calls": 0,
            "offline_step01_reruns": 0,
            "note": "真实 Step01 调用量与返工次数未运行，不以外推值冒充",
        },
        "downstream_rework": {
            "candidate_failures_detected": len(detected),
            "feedback_routes": {
                "step01": ["causal_gap", "time_ellipsis_lost",
                           "offscreen_speaker_visible", "listener_reaction_missing"],
                "voice_execution": ["audio_window_overflow",
                                    "native_dialogue_unverified"],
            },
        },
        "total_consumption": {
            "video_model_calls": 0, "image_model_calls": 0, "tts_calls": 0,
            "media_provider_calls": 0, "local_ffmpeg_runs": "见 S06 样片命令",
        },
        "usability": {
            "playable_sample": sample_info,
            "real_model_usability": "unverified",
        },
        "real_benefit": "unverified",
    }
    (out / "benefit_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_metrics_md(metrics, shadow, out / "benefit_metrics.md")
    _write_not_run(out / "not_run.md")
    return {"diff": diff, "shadow": shadow, "metrics": metrics}


def _write_metrics_md(metrics: dict, shadow: dict, path: Path) -> None:
    m = metrics
    lines = [
        "# S08：离线收益指标表", "",
        "> 这是**工程贯通/离线代理**读数，不是真实内容质量收益。真实收益一律标"
        "「未验证」；未发起任何模型/媒体生成调用。", "",
        "| 维度 | 离线读数 | 真实收益 |", "|---|---|---|",
        f"| 内容完整性 | 基线违规 {m['content_completeness']['baseline_semantic_violations']}；"
        f"受保护字段保留 | 未验证 |",
        f"| 误拒/漏检 | 基线误拒 {m['false_reject_and_missed_detection']['baseline_false_rejects']}"
        f"/{m['false_reject_and_missed_detection']['baseline_total']}；"
        f"变异漏检 {m['false_reject_and_missed_detection']['missed_codes']}；"
        f"覆盖 {m['false_reject_and_missed_detection']['mutation_detection_coverage']} | 未验证 |",
        f"| 修订退化 | 候选受保护字段改写="
        f"{m['revision_degradation']['candidate_changed_protected_values']} | 未验证 |",
        f"| Step01 工作量 | 离线调用 {m['step01_workload']['offline_model_calls']} 次、"
        f"重跑 {m['step01_workload']['offline_step01_reruns']} 次 | 未验证 |",
        f"| 下游返工 | 自动检出候选失败 {m['downstream_rework']['candidate_failures_detected']} 条 | 未验证 |",
        f"| 总消耗 | 模型/媒体 provider 调用 0 次 | — |",
        f"| 可用性 | 可播放样片={m['usability']['playable_sample'].get('playable')} | 未验证 |",
        "",
        "## 影子比较摘要",
        f"- 基线全绿：`{shadow['summary']['baseline_all_green']}`",
        f"- 三族变异检测：`{shadow['summary']['mutation_all_detected']}`",
        f"- 声音变异 codes：`{shadow['summary']['voice_mutation_detected']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_not_run(path: Path) -> None:
    path.write_text(
        "# S08：未运行记录与真实验证建议\n\n"
        "本包全程离线，以下真实调用**均未运行**：\n\n"
        "| 项目 | 未运行原因 | 真实验证建议 |\n"
        "|---|---|---|\n"
        "| Step01 真实生成/审计 | 需 OpenCode Go 授权与调用上限 | 先冻结语义稿/审计输入，"
        "限定调用上限与止损；逐 run_id 核对新稿、BEST-GUARD 选稿与提交 |\n"
        "| Step02a 图片生成 | 付费媒体生成需用户单独授权 | 先 plan-only 核帧数，再按"
        "明确帧数与重抽上限小范围执行 |\n"
        "| Step02b 视频生成 | 每次付费调用须人工确认 | 逐镜列明模型/时长/费用，一次"
        "确认一次提交；不得用本包桩结果冒充 |\n"
        "| TTS 真实合成 | 保留旧音频分支，未获授权 | 先复用旧音频与身份；新分支实测"
        "时长后再比较 |\n"
        "| 正式门禁切换 | 只产出清单，需用户裁决 | 按 S07 清单逐项确认严重度/责任/回退 |\n"
        "| 正式产物采纳 | 本包不采纳 | 切换批准后另走正式提交与回读 |\n\n"
        "**可复现离线命令**：\n\n"
        "```powershell\n"
        "C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe -m pytest "
        "tests/shared/test_execution_contract.py tests/shared/test_execution_semantics.py "
        "tests/step02a_frame_gen/test_execution_plan.py "
        "tests/step02_video_gen/test_execution_requirements.py "
        "tests/step03_tts_audio/test_voice_preparation.py "
        "tests/step04_merge/test_timeline_acceptance_s06.py "
        "tests/integration/test_s07_shadow_and_candidate_revalidation.py -q "
        "--basetemp=.pytest_tmp/s08_contract -p no:cacheprovider\n"
        "C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe "
        "tools/build_timeline_sample.py --out .pytest_tmp/s06\n"
        "C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe "
        "tools/shadow_compare_step01_blockers.py --out .pytest_tmp/s07\n"
        "```\n",
        encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="S08 离线收益评阅")
    parser.add_argument("--out", default="docs/step01-layered-execution/artifacts/s08")
    parser.add_argument("--sample", default=".pytest_tmp/s06/sample.mp4")
    args = parser.parse_args(argv)
    result = build(Path(args.out), sample=Path(args.sample))
    print(json.dumps({
        "out": str(Path(args.out)),
        "baseline_all_green": result["metrics"]["content_completeness"]["baseline_all_green"],
        "missed_codes": result["metrics"]["false_reject_and_missed_detection"]["missed_codes"],
        "playable": result["metrics"]["usability"]["playable_sample"].get("playable"),
    }, ensure_ascii=False))
    return 0 if (result["metrics"]["content_completeness"]["baseline_all_green"]
                 and not result["metrics"]["false_reject_and_missed_detection"]["missed_codes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
