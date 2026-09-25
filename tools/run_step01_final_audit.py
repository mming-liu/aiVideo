"""Run a final Step01 semantic audit bound to the frozen script revision.

⚠️ **诊断/补审计工具，不是生产闭环的一部分**（总计划 §4.4）。生产路径上的
final semantic audit 由 `step01_script_gen/main.py::_commit_step01_outputs()`
在唯一提交点里执行，并写入 commit manifest。本工具用于事后复算某一份已落盘
对象的审计结论，**不会**写 manifest、**不会**替换三份正式产物。

## v6.123：安全诊断模式（指定输入 → 诊断输出）

此前本工具**只**读正式稿、**只**写 `step01_audit.json`——拿它做快照实验会覆盖正式
审计产物（用户 2026-09-25 指出的阻塞）。现在：

    # 正式路径（与旧行为一致）：读 script_result.json → 写 step01_audit.json
    python -m tools.run_step01_final_audit

    # 诊断路径：读指定冻结稿 → 写 <稿所在目录>/diag/<稿名>.audit.json
    # （**不碰正式产物**，也不进 BEST-GUARD 候选池——见 v6.124.2）
    python -m tools.run_step01_final_audit \
        --script .../snapshots/script_result_20260925_173311.json

    # 同一 revision 独立审计两次 + 按「镜头＋事实证据」比对（用户口径）
    python -m tools.run_step01_final_audit --script <冻结稿> --repeat 2 --compare

红线：`--script` 指向非正式稿时，输出**默认**落在该稿同级的 `diag/` 子目录；
要覆盖正式产物必须显式给出正式稿路径（或默认无参路径），不存在"顺手写回正式文件"
的可能。诊断产物里带 `diagnostic: true` 与 `note`，与生产审计产物可机器区分。

## v6.124.2：诊断默认输出迁入 `diag/`（用户 2026-09-25 决定）

旧默认把诊断写在该稿**旁边**：被审计稿名是 `script_result_*.json` 时，产物名同样
以 `script_result_` 开头 → 落进 BEST-GUARD 的候选 glob → 2026-09-25 19:14 被选为
终稿 → 提交点 BLOCK。现在默认写 `diag/` 子目录（glob 非递归 → 彻底隔离）。
⚠️ `--out` 仍可指向任何位置，所以生产侧另有一道「非分镜稿不得进候选池」的护栏
（`step01_script_gen.main._non_script_snapshot_reason`）——两层都要在。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.chapter_reader import find_sections
from shared.config import NOVEL_CHAPTER, NOVEL_NAME, BOOK_INDEX, BOOK_NAME
from shared.json_io import atomic_dump_json
from shared.quality_gate import compute_script_revision
from step01_script_gen.audit import (run_same_model_audit_with_status,
                                     audit_prompt_version)
# v6.103：audit scope 的**单一事实源**在 step01 main（唯一提交点也用同一个常量）
# ——两边写不同字符串会让「revision+fingerprint+scope 全同才复用审计」永远不成立。
from step01_script_gen.main import (STEP01_AUDIT_SCOPE, _final_audit_focus,
                                    _audit_focus_version)
from step01_script_gen.quality_issues import RULE_VERSION, RULE_SOURCE_FINGERPRINT


def _read_raw_chapter(path: Path, chapter: int) -> str:
    sections = find_sections(str(path))
    target = next((s for s in sections if s["index"] == chapter), None)
    if target is None:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            return text[target["start"]:target["end"]]
        except UnicodeDecodeError:
            continue
    return ""


def _resolve_plan(script: dict) -> dict:
    """审计要传的章节大纲——**与生产提交点同一个键**（v6.117，2026-09-25 实测）.

    生产提交点读 `script["chapter_outline"]`（`main.py::_commit_step01_outputs` 的
    `plan=` 实参）。本工具此前读 `script.get("outline") or script.get("plan")`，而产物里
    **两个键都不存在** → 每次审计都带**空大纲**跑，结论与生产提交轮的审计不可比
    （同一个 revision 得出的 blocking 数就此没有可比性）。

    缺大纲时显式告警、不用静默空 dict——否则「工具审计 vs 内联审计」的差异会被误
    归因成模型波动。旧产物仍容忍 `outline`/`plan` 两个历史键。
    """
    plan = (script.get("chapter_outline") or script.get("outline")
            or script.get("plan") or {})
    if not plan:
        print("[WARN] script_result.json 无 chapter_outline——本次审计不带大纲，"
              "结论与生产提交轮的审计不可比")
    return plan


# ---------------------------------------------------------------------------
# v6.123：诊断输出路径 + 两次审计的结论比对
# ---------------------------------------------------------------------------
def _evidence_norm(text: str) -> str:
    """证据文本归一（比对用）：只留中英文数字，去掉标点/空白/引号差异."""
    return re.sub(r"[^\w一-鿿]+", "", str(text or ""))


def _shot_evidence_key(issue: dict) -> tuple:
    """「镜头＋事实证据」键——用户口径：不得只比问题代码."""
    return (str(issue.get("shot_id") or ""), _evidence_norm(issue.get("evidence")))


def compare_audits(a: dict, b: dict) -> dict:
    """两次独立审计的结论比对（**按镜头＋事实证据**，代码另列）.

    只比 `code` 会把「同一镜头、同一事实」的两次表述差异误判成一致/不一致；
    本函数同时给出三种视角，供人工裁决：

      - `same_shot_evidence`：两边都出现且证据一致（= 稳定复现的结论）；
      - `code_only`：`code` 集合的交集（**仅供参考**，不构成一致性证据）；
      - `code_matched_but_evidence_differs`：同一 `(镜头, code)` 但证据不同
        ——只比代码就会漏掉这一类；
      - `only_in_a` / `only_in_b`：单侧出现的结论。
    """
    _ia = [i for i in (a.get("issues") or []) if isinstance(i, dict)]
    _ib = [i for i in (b.get("issues") or []) if isinstance(i, dict)]
    _ka = {_shot_evidence_key(i) for i in _ia}
    _kb = {_shot_evidence_key(i) for i in _ib}
    _by_shot_code_a = {(str(i.get("shot_id") or ""), str(i.get("code") or "")): i
                       for i in _ia}
    _by_shot_code_b = {(str(i.get("shot_id") or ""), str(i.get("code") or "")): i
                       for i in _ib}
    _diff_evidence = []
    for _k in sorted(set(_by_shot_code_a) & set(_by_shot_code_b)):
        _ea = _by_shot_code_a[_k].get("evidence")
        _eb = _by_shot_code_b[_k].get("evidence")
        if _evidence_norm(_ea) != _evidence_norm(_eb):
            _diff_evidence.append({"shot_id": _k[0], "code": _k[1],
                                   "evidence_a": str(_ea or "")[:160],
                                   "evidence_b": str(_eb or "")[:160]})
    return {
        "run_a": {"status": a.get("status"), "issues": len(_ia),
                  "blocking": _count_blocking(_ia), "revision": a.get("input_revision"),
                  "usage": a.get("usage") or {}, "error": a.get("error") or ""},
        "run_b": {"status": b.get("status"), "issues": len(_ib),
                  "blocking": _count_blocking(_ib), "revision": b.get("input_revision"),
                  "usage": b.get("usage") or {}, "error": b.get("error") or ""},
        "same_shot_evidence": sorted(
            f"{s}|{ev[:40]}" for s, ev in (_ka & _kb)),
        "code_only": sorted({str(i.get("code")) for i in _ia}
                            & {str(i.get("code")) for i in _ib}),
        "code_matched_but_evidence_differs": _diff_evidence,
        "only_in_a": sorted(f"{s}|{i.get('code')}|{str(i.get('evidence') or '')[:60]}"
                            for s, i in ((str(x.get("shot_id") or ""), x) for x in _ia)
                            if _shot_evidence_key(i) not in _kb),
        "only_in_b": sorted(f"{s}|{i.get('code')}|{str(i.get('evidence') or '')[:60]}"
                            for s, i in ((str(x.get("shot_id") or ""), x) for x in _ib)
                            if _shot_evidence_key(i) not in _ka),
    }


def _count_blocking(issues: list) -> int:
    return sum(1 for i in issues
               if str(i.get("severity") or "").strip() in ("hard", "visual_risk",
                                                           "blocking"))


def repair_target_report(payload: dict, script: dict) -> list:
    """审计条目的成对目标是否可执行——**复用生产同一个判据**（不另写一套）.

    复用 `main._audit_repair_target_problems`：诊断结论若与生产判据不同源，
    就会出现「诊断说能修、提交点说不能修」这类无法归因的差异。
    """
    from step01_script_gen.main import _audit_repair_target_problems
    _shots_by_id = {f"shot_{s.get('index')}": s for s in (script.get("shots") or [])
                    if isinstance(s, dict)}
    if isinstance(script.get("hook"), dict):
        _shots_by_id["hook"] = script["hook"]
    return _audit_repair_target_problems(payload, _shots_by_id)


def _parse_args(argv=None) -> argparse.Namespace:
    _p = argparse.ArgumentParser(
        description="Step01 final semantic audit（生产补审计 / 安全诊断两用）")
    _p.add_argument("--script", default=None,
                    help="要审计的稿件（默认：章节正式 script_result.json）")
    _p.add_argument("--out", default=None,
                    help="审计产物路径。给了 --script 又没给 --out 时，默认落在"
                         "`<稿所在目录>/diag/<稿名>.audit.json`——**不会**碰正式"
                         "审计产物，也不会进 BEST-GUARD 候选池")
    _p.add_argument("--repeat", type=int, default=1,
                    help="独立审计次数（同一 revision，用于稳定性比对）")
    _p.add_argument("--compare", action="store_true",
                    help="--repeat >= 2 时按「镜头＋事实证据」比对结论")
    _p.add_argument("--note", default="",
                    help="写进诊断产物的说明（如「输入已人工补齐布局人物下标，"
                         "不是原始审计输出」）")
    _p.add_argument("--effort", default=None,
                    help="审计思考档位（none/minimal/low/medium/high/，仅本次运行；"
                         "不设 = 沿用 provider 全局 DEEPSEEK_REASONING_EFFORT）。"
                         "用于「固定输入 × 不同 effort × 重复运行」的稳定性实验")
    return _p.parse_args(argv)


def diag_out_base(script_path: Path, effort: str | None = None) -> Path:
    """诊断产物的默认位置：`<稿所在目录>/diag/<稿名>.audit[.<effort>].json`.

    v6.124.2（用户 2026-09-25 决定）：诊断**默认**输出必须落在 `diag/` 子目录。

    为什么：此前默认写成 `<稿名>.audit.json` **就放在稿件旁边**。当被审计的稿是
    `snapshots/script_result_*.json` 时，产物名同样以 `script_result_` 开头 →
    落进 BEST-GUARD 的候选 glob（`snapshots/script_result_*.json`，非递归）→
    被当成历史稿参与选稿。实测 2026-09-25 19:14：这份诊断（非分镜稿）被选为终稿，
    提交点随即 BLOCK。子目录 + 非递归 glob = 工具层彻底隔离。

    ⚠️ 这**不能替代生产护栏**：`--out` 仍可指向任何地方，任何人（含脚本）都可能
    把非分镜稿放进候选目录，所以 `main._non_script_snapshot_reason` 那道
    「先验明正身再评分」是必须的那一层。
    """
    _stem = script_path.with_suffix("").name
    _name = f"{_stem}.audit.json" if not effort else f"{_stem}.audit.{effort}.json"
    return script_path.parent / "diag" / _name


def resolve_out_paths(script_path: Path, official_script: Path,
                      official_audit: Path, out_arg: str | None,
                      runs: int) -> tuple:
    """`(out_base, [每次的产物路径], 是否正式稿)`——**诊断默认绝不写正式产物**.

    抽出成纯函数是为了能对「安全红线」本身写回归：非正式稿 + 没显式给 `--out`
    时，输出必须落在 `diag/` 子目录（既不碰正式产物，也不进 BEST-GUARD 候选池）。
    """
    is_official = script_path.resolve() == official_script.resolve()
    if out_arg:
        out_base = Path(out_arg)
    elif is_official:
        out_base = official_audit
    else:
        out_base = diag_out_base(script_path)
    _runs = max(1, int(runs))
    _paths = [out_base if _runs == 1 else
              out_base.with_name(f"{out_base.stem}.r{i + 1}{out_base.suffix}")
              for i in range(_runs)]
    return out_base, _paths, is_official


def main(argv=None) -> int:
    args = _parse_args(argv)
    root = ROOT
    chapter_dir = (root / "output" / "video" / NOVEL_NAME
                   / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}"
                   / f"chapter_{NOVEL_CHAPTER:03d}")
    official_script = chapter_dir / "script_result.json"
    official_audit = chapter_dir / "step01_audit.json"
    script_path = Path(args.script).resolve() if args.script else official_script
    # v6.124：(c) 稳定性实验的产物必须**按 effort 分开命名**，否则不同档位的结论
    # 会互相覆盖（同一 revision 多次运行本来就会重名）。
    # v6.124.2：默认位置统一走 `diag_out_base()`（`diag/` 子目录）——两条分支
    # 各写一遍路径正是「改一处漏一处」的温床。
    _out_arg = args.out
    if not _out_arg and args.effort and script_path.resolve() != official_script.resolve():
        _out_arg = str(diag_out_base(script_path, args.effort))
    out_base, _out_paths, is_official = resolve_out_paths(
        script_path, official_script, official_audit, _out_arg, args.repeat)
    # 诊断目录按需创建（`atomic_dump_json` 不建父目录）
    out_base.parent.mkdir(parents=True, exist_ok=True)

    script = json.loads(script_path.read_text(encoding="utf-8"))
    if is_official and official_audit.exists():
        _initial_backup = chapter_dir / "step01_audit.initial.json"
        if not _initial_backup.exists():
            shutil.copy2(official_audit, _initial_backup)

    novel_path = root / "锦衣夜行_77119小说.txt"
    novel_text = _read_raw_chapter(novel_path, NOVEL_CHAPTER)
    plan = _resolve_plan(script)
    metadata = {
        "input_revision": compute_script_revision(script),
        "rule_version": RULE_VERSION,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "audit_scope": STEP01_AUDIT_SCOPE,
        "script_path": str(script_path),
    }
    if not is_official:
        # 诊断标记（机器可读）：与生产审计产物区分，避免被当成正式结论复用
        metadata.update({
            "diagnostic": True,
            "note": args.note or "诊断审计（输入非正式稿，不写正式产物）",
            "official_script": str(official_script),
        })
    # v6.103：定向项由共享生成器产出（通用状态链 + 从确定性 blocking
    # 里动态取重点镜头），不再写死本小说的镜头号。
    audit_focus = _final_audit_focus(script, None)

    _prev_effort = os.environ.get("AUDIT_REASONING_EFFORT")
    if args.effort:
        os.environ["AUDIT_REASONING_EFFORT"] = str(args.effort)
    _effort_used = (os.getenv("AUDIT_REASONING_EFFORT") or "").strip() or "(provider 默认)"
    metadata["audit_reasoning_effort"] = _effort_used
    try:
        results = _run_audits(_out_paths, novel_text, plan, script, metadata,
                              audit_focus)
    finally:
        if _prev_effort is None:
            os.environ.pop("AUDIT_REASONING_EFFORT", None)
        else:
            os.environ["AUDIT_REASONING_EFFORT"] = _prev_effort

    if len(_out_paths) >= 2 and args.compare:
        _cmp = compare_audits(results[0], results[-1])
        _cmp_path = out_base.with_name(f"{out_base.stem}.compare.json")
        atomic_dump_json(_cmp_path, _cmp, ensure_ascii=False, indent=2)
        print(json.dumps({"compare": str(_cmp_path),
                          "same_shot_evidence": len(_cmp["same_shot_evidence"]),
                          "code_matched_but_evidence_differs":
                              len(_cmp["code_matched_but_evidence_differs"]),
                          "only_in_a": len(_cmp["only_in_a"]),
                          "only_in_b": len(_cmp["only_in_b"])},
                         ensure_ascii=False))

    _bad = [r for r in results if r.get("status") != "ok"]
    return 0 if not _bad else 2


def _run_audits(out_paths: list, novel_text: str, plan: dict, script: dict,
                metadata: dict, audit_focus) -> list:
    """逐次跑审计、落盘诊断产物、打印一行摘要（每次一个独立请求）."""
    results = []
    for _i, _out in enumerate(out_paths):
        result = run_same_model_audit_with_status(
            novel_text,
            plan,
            script,
            model=None,
            timeout_sec=int(os.getenv("AUDIT_TIMEOUT_SEC", "180")),
            audit_focus=audit_focus,
            metadata=metadata,
        )
        payload = {
            **metadata,
            "status": result.status,
            "model": None,
            "error": result.error,
            "audit_focus": audit_focus,
            "issues": [i.to_dict() for i in result.issues],
            "usage": getattr(result, "usage", None),
            # 验收口径要的三项：提示词版本 / schema 偏离 / 实际通道
            "audit_prompt_version": audit_prompt_version(),
            "audit_focus_version": _audit_focus_version(),
            "schema_deviations": list(getattr(result, "schema_deviations", None) or []),
            "channel_provider": getattr(result, "channel_provider", None),
            "channel_model": getattr(result, "channel_model", None),
            "run_index": _i + 1,
            "raw_response": (result.raw_text or "")[:20000],
        }
        payload["repair_target_problems"] = repair_target_report(payload, script)
        atomic_dump_json(_out, payload, ensure_ascii=False, indent=2)
        results.append(payload)
        print(json.dumps({
            "path": str(_out),
            "run": _i + 1,
            "status": result.status,
            "issues": len(result.issues),
            "blocking": _count_blocking(payload["issues"]),
            "repair_target_problems": len(payload["repair_target_problems"]),
            "revision": metadata["input_revision"],
            "prompt_version": payload.get("audit_prompt_version"),
            "effort": metadata.get("audit_reasoning_effort"),
            "usage": payload.get("usage") or {},
            "error": result.error,
        }, ensure_ascii=False))
    return results


if __name__ == "__main__":
    raise SystemExit(main())
