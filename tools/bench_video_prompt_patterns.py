# -*- coding: utf-8 -*-
"""视频 prompt 基线与差异报告（总计划阶段 7 / 工作包 B）.

固定 step02 **实际提交给视频模型**的 prompt，使以后修改参考图说明、压缩策略或
provider 路由时能看到准确的 before/after——而不是比较 step01 的聚合 `prompt`。

## 契约（非 step01 吸收计划 §4）

  - **不导入、不运行 step01**；
  - fixture 输入**不被函数修改**（只读深拷贝）；
  - 报告明确区分三个层级：
      1. `stage_concise`   —— `_build_seedance_concise_prompt()` 的输出
      2. `stage_with_refs` —— 追加参考图用途/清单之后的 prompt
      3. `provider_prompt` —— provider 最终压缩结果（`_seedance_compress_prompt`）
    只比较第 3 层才是「模型真正收到的文本」；
  - **不调用任何外部 API**；同一输入重复运行结果一致；
  - 报告带上游 gate 状态与审计 revision/blocking 数——**静态 prompt 通过不代表
    可以生成**（`upstream_blocked` 时 verdict 不得报 PASS 为「可生成」）；
  - image / text / r2v 三种模式都有快照级回归。

用法：
    python tools/bench_video_prompt_patterns.py [--cases N] [--out-dir DIR]
                                                [--md]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "video_prompt_benchmark.json"
REPORT_JSON = "video_prompt_benchmark.json"
REPORT_MD = "video_prompt_benchmark.md"


# ---------------------------------------------------------------------------
# 层级化 prompt 复现（与 step02_video_gen/prompt_builder 的组装顺序同构）
# ---------------------------------------------------------------------------
def stage_concise(shot: dict) -> str:
    from step02_video_gen.prompt_builder import _build_seedance_concise_prompt
    return _build_seedance_concise_prompt(shot)


def stage_reference_budget(used_chars: int):
    """与运行时同源的剩余预算（`r2v_reference_budget`）——两边算一份."""
    from step02_video_gen.prompt_builder import r2v_reference_budget
    return r2v_reference_budget(used_chars)


def stage_reference_block(case: dict, ref_paths: list, characters: list,
                          budget: int | None = None) -> str:
    from step02_video_gen.prompt_builder import (
        _build_reference_list, _build_r2v_purpose_list)
    mode = str(case.get("mode") or "text")
    shot = case.get("shot") or {}
    if mode == "r2v":
        # 与运行时同口径：把「cap − 已组装部分」的剩余预算传下去
        return _build_r2v_purpose_list(
            shot, registry=case.get("registry") or {},
            characters=characters, ref_paths=ref_paths, max_chars=budget)
    if mode == "image":
        return _build_reference_list(
            ref_paths[0] if ref_paths else None,
            ref_paths[1] if len(ref_paths) > 1 else None,
            ref_paths[2:], mode="A", ref_kind="image")
    return ""


def stage_provider(prompt: str, cap: int) -> str:
    from _video_gen.volcengine_provider import _seedance_compress_prompt
    return _seedance_compress_prompt(prompt, max_chars=cap)


def _materialize_refs(case: dict, work: Path) -> list:
    """把 fixture 声明的参考图清单落成真实的小文件.

    用途分类（`_classify_reference_purpose`）按**路径**判断（文件名/父目录/
    资产库路径），所以只有真实存在的路径才能走完整逻辑。这些是 1x1 占位图，
    不进任何模型——只为让路径判定可运行且可重复。
    """
    from PIL import Image
    paths: list = []
    for i, ref in enumerate(case.get("reference_manifest") or []):
        rel = str(ref.get("path") or f"ref_{i}.png")
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 4), (10, 10, 10)).save(target)
        paths.append(target)
    return paths


def _digest(paths: list) -> list:
    out = []
    for p in paths:
        try:
            out.append({"path": Path(p).name,
                        "sha1": hashlib.sha1(Path(p).read_bytes()).hexdigest()[:12],
                        "bytes": Path(p).stat().st_size})
        except Exception:
            out.append({"path": str(p), "sha1": None, "bytes": None})
    return out


def _cap_for(case: dict) -> int:
    override = case.get("prompt_cap")
    if override:
        return int(override)
    try:
        from shared.model_caps import active_video_prompt_cap
        return int(active_video_prompt_cap())
    except Exception:
        return 500


def evaluate_case(case: dict, work: Path) -> dict:
    original = copy.deepcopy(case)
    ref_paths = _materialize_refs(case, work)
    characters = list(case.get("characters") or [])

    # ⚠️ cap 必须在**导入 step02 / shared.config 之后**再取：`shared.config` 首次
    # 导入时会加载 .env（VIDEO_PROVIDER 等），而 `active_video_model()` 依赖它——
    # 顺序反了会拿到未加载 .env 的默认模型（实测 wan2.7→5000 而 Seedance→500，
    # 同一个报告里两个 cap）。
    concise = stage_concise(case.get("shot") or {})
    cap = _cap_for(case)
    block = stage_reference_block(
        case, ref_paths, characters,
        budget=stage_reference_budget(len(concise)))
    with_refs = concise + (("\n\n" + block) if block else "")
    provider_prompt = stage_provider(with_refs, cap)

    expected = list(case.get("expected_fragments") or [])
    forbidden = list(case.get("forbidden_fragments") or [])
    hit = [f for f in expected if f in with_refs]
    missing = [f for f in expected if f not in with_refs]
    leaked = [f for f in forbidden if f in with_refs]

    upstream = _upstream_status()
    verdict = "PASS"
    reasons = []
    if missing:
        verdict, _ = "WARN", reasons.append("缺少预期片段: %s" % missing)
    if leaked:
        verdict = "BLOCK"
        reasons.append("出现禁止片段: %s" % leaked)
    if len(provider_prompt) > cap:
        verdict = "BLOCK"
        reasons.append("provider 压缩后仍超 cap %d（实际 %d）"
                       % (cap, len(provider_prompt)))
    if upstream != "eligible":
        reasons.append("上游 gate=%s：静态 prompt 结果不得当作可生成依据" % upstream)

    assert case == original, "fixture 输入被函数修改"
    return {
        "case_id": case.get("case_id"),
        "purpose": case.get("purpose"),
        "mode": case.get("mode"),
        "provider": os.getenv("VIDEO_PROVIDER", ""),
        "cap": cap,
        "stage_concise": concise,
        "stage_with_refs": with_refs,
        "provider_prompt": provider_prompt,
        "len_stage_concise": len(concise),
        "len_stage_with_refs": len(with_refs),
        "len_provider_prompt": len(provider_prompt),
        "reference_manifest": case.get("reference_manifest") or [],
        "reference_files": _digest(ref_paths),
        "expected_fragments": expected,
        "missing_fragments": missing,
        "leaked_fragments": leaked,
        "matched_fragments": hit,
        "compression_changed": provider_prompt != with_refs,
        # 参考图策略的可观测状态（§5.4 优先级：图号与用途 > 正向继承 >
        # 跨角色隔离 > 排除项细节）。预算不足时**先丢排除细节、再丢跨角色隔离**，
        # 编号与用途永不裁掉——这里把这个取舍显式记下来，便于人工 review。
        # 只对 r2v 有意义：image/text 模式不套用继承/排除规则（首帧/尾帧的
        # 构图与姿势本来就是视频起点/终点）。
        "reference_policy": {
            "applies": str(case.get("mode")) == "r2v",
            "inherit_exclude_present": ("不取" in block
                                        if str(case.get("mode")) == "r2v" else False),
            "cross_character_isolation_present": (
                "禁止角色之间交换" in block
                if str(case.get("mode")) == "r2v" else False),
            "trimmed": (bool(block) and "不取" not in block
                        if str(case.get("mode")) == "r2v" else False),
        },
        "affects_image_and_text_modes": bool(
            str(case.get("mode")) in ("image", "text")
            and block
            and "参考图用途" in block),
        "upstream_gate_status": upstream,
        "verdict": verdict,
        "reasons": reasons,
    }


def _upstream_status() -> str:
    try:
        from shared.config import BOOK_INDEX, BOOK_NAME, NOVEL_CHAPTER, NOVEL_NAME
        from shared.quality_gate import QualityGateError, require_step01_upstream
        chapter = (ROOT / "output" / "video" / NOVEL_NAME
                   / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}"
                   / f"chapter_{NOVEL_CHAPTER:03d}")
        # v6.111（审核 P2-6）：同 `require_step01_upstream`（两道门），
        # 不再只验 manifest。
        require_step01_upstream(chapter, stage="step01")
        return "eligible"
    except QualityGateError:
        return "upstream_blocked"
    except Exception:
        return "upstream_blocked"


def run(cases: list, *, out_dir: Path | None = None, write_md: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="prompt_bench_") as td:
        results = [evaluate_case(c, Path(td)) for c in cases]
    report = {
        "report_version": 1,
        "cases": results,
        "summary": {
            "total": len(results),
            "pass": len([r for r in results if r["verdict"] == "PASS"]),
            "warn": len([r for r in results if r["verdict"] == "WARN"]),
            "block": len([r for r in results if r["verdict"] == "BLOCK"]),
            "upstream_gate_status": results[0]["upstream_gate_status"] if results else None,
        },
        "notes": [
            "阶段 1 = _build_seedance_concise_prompt；阶段 2 = 追加参考图用途/清单；"
            "阶段 3 = provider 压缩结果（模型真正收到的文本）",
            "上游 gate 未通过时，本报告的 PASS 只代表 prompt 组装正确，"
            "不代表可以发起生成",
        ],
        "api_calls": 0,
    }
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        from shared.json_io import atomic_dump_json
        atomic_dump_json(out / REPORT_JSON, report, ensure_ascii=False, indent=2)
        if write_md:
            (out / REPORT_MD).write_text(_render_md(report), encoding="utf-8")
    return report


def _render_md(report: dict) -> str:
    lines = ["# 视频 prompt 基线报告", "",
             f"- 用例：{report['summary']['total']}（PASS {report['summary']['pass']} / "
             f"WARN {report['summary']['warn']} / BLOCK {report['summary']['block']}）",
             f"- 上游 gate：{report['summary']['upstream_gate_status']}",
             "- API 调用：0（纯离线）", ""]
    for c in report["cases"]:
        lines.append(f"## {c['case_id']}（{c['mode']}）")
        lines.append(f"- 用途：{c['purpose']}")
        lines.append(f"- verdict：**{c['verdict']}**" + (f" —— {'; '.join(c['reasons'])}"
                                                         if c["reasons"] else ""))
        lines.append(f"- 字符数：concise {c['len_stage_concise']} → "
                     f"with_refs {c['len_stage_with_refs']} → "
                     f"provider {c['len_provider_prompt']}（cap {c['cap']}）")
        lines.append(f"- 参考图：{', '.join(r['path'] for r in c['reference_files']) or '无'}")
        pol = c["reference_policy"]
        if pol["applies"]:
            lines.append(
                f"- 参考图策略：继承/排除 "
                f"{'有' if pol['inherit_exclude_present'] else '无'}"
                f"（预算裁剪：{'是' if pol['trimmed'] else '否'}），"
                f"跨角色隔离 "
                f"{'有' if pol['cross_character_isolation_present'] else '无'}")
        else:
            lines.append("- 参考图策略：不适用（image/text 模式不套用继承/排除规则）")
        lines.append("")
        lines.append("```text")
        lines.append(c["provider_prompt"])
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def load_cases(limit: int | None = None) -> list:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    return cases[:limit] if limit else cases


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=int, default=None, help="只跑前 N 个用例")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--md", action="store_true", help="同时产出 Markdown 报告")
    args = ap.parse_args(argv)
    report = run(load_cases(args.cases), out_dir=args.out_dir, write_md=args.md)
    print(json.dumps(report["summary"], ensure_ascii=False))
    for c in report["cases"]:
        print(f"  [{c['verdict']}] {c['case_id']} ({c['mode']}) "
              f"{c['len_provider_prompt']}/{c['cap']}")
    return 0 if report["summary"]["block"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
