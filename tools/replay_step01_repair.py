"""在**冻结稿**上回放一次真实定向修复（诊断工具，不是生产闭环的一部分）.

用途（用户 2026-09-25 验证计划第 2 步）：把一份已知的审计问题集喂给**真实的**修复
事务（真模型调用、真实授权/验收/回退逻辑），在一份**冻结稿**上验证修复能力，
而不必跑整章 step01。

## 红线

1. **不写任何正式产物**：`script_result.json` / `step01_audit.json` /
   `step01_quality.json` / `step01_commit.json` / `_staged/` 一律拒绝写入（显式报错）。
   输出只落在 `--out`（修后稿）与 `<out>.replay.json`（诊断报告）。
2. **诊断输入必须自带标记**：`--issues` 指的那份审计 payload 若是人工修补过的
   （例如补全了布局人物下标），必须用 `--note` 写明；工具会把 `diagnostic: true`
   与 note 原样写进报告，避免被当成原始审计输出复用。
3. **不加修复轮**：`max_rounds` 固定 1（提交点的生产口径），不接受上调。

## 用法

    python -m tools.replay_step01_repair \
        --script output/.../snapshots/script_result_20260925_145317.json \
        --issues /tmp/diag_issues.json \
        --out /tmp/replay_repaired.json \
        --note "诊断输入：布局人物下标已人工补全，不是原始审计输出"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.chapter_reader import find_sections
from shared.config import NOVEL_CHAPTER, NOVEL_NAME, BOOK_INDEX, BOOK_NAME
from shared.json_io import atomic_dump_json
from shared.quality_gate import compute_script_revision

_OFFICIAL_NAMES = ("script_result.json", "step01_audit.json", "step01_quality.json",
                   "step01_commit.json", "step01_repair_rounds.json",
                   "step01_audit_rounds.json")


def _chapter_dir() -> Path:
    return (ROOT / "output" / "video" / NOVEL_NAME
            / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}"
            / f"chapter_{NOVEL_CHAPTER:03d}")


def _read_raw_chapter() -> str:
    path = ROOT / "锦衣夜行_77119小说.txt"
    sections = find_sections(str(path))
    target = next((s for s in sections if s["index"] == NOVEL_CHAPTER), None)
    if target is None:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            return text[target["start"]:target["end"]]
        except UnicodeDecodeError:
            continue
    return ""


def _guard_out_path(out: Path) -> None:
    """拒绝把诊断输出写进正式产物（红线 1）."""
    if out.name in _OFFICIAL_NAMES:
        raise SystemExit(
            f"[ABORT] --out 指向正式产物名 `{out.name}`——诊断回放不得覆盖正式产物；"
            f"请换一个诊断路径（如 <某稿>.repaired.json）")
    if out.parent.resolve() == _chapter_dir().resolve():
        raise SystemExit(
            f"[ABORT] --out 落在章节目录根部（{out.parent}）——那里的文件名就是正式"
            f"产物口径；请写到 snapshots/ 或临时目录")


def replay(script_path: Path, issues_path: Path, out_path: Path,
           note: str, model: str | None, log_path: Path | None) -> dict:
    import step01_script_gen.main as m

    script = json.loads(script_path.read_text(encoding="utf-8"))
    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = {"issues": payload}
    issues = m._audit_payload_to_issues(payload)
    if not issues:
        raise SystemExit("[ABORT] --issues 里没有任何 blocking 条目（可授权目标为空）")

    # 日志隔离：本回放的详细日志写 `--log`（默认 <out>.log），
    # 官方 step01.log 不再被诊断流量污染（约束引擎另有自己的 logger，见报告说明）。
    if log_path is None:
        log_path = out_path.with_suffix(".log")
    _orig_logger = getattr(m, "_logger", None)
    try:
        from shared.tee_logger import get_logger
        m._logger = get_logger(log_path, console_level="INFO")
    except Exception:
        pass

    diag: list = []
    block: list = []
    try:
        repaired = m._repair_shot_warnings(
            json.loads(json.dumps(script)), _read_raw_chapter(),
            model=model,
            audit_issues=issues,
            max_rounds=1,                    # 红线 3：不加修复轮
            repair_diag=diag,
            block_reasons=block,
        )
    finally:
        if _orig_logger is not None:
            m._logger = _orig_logger

    before_rev = compute_script_revision(script)
    after_rev = compute_script_revision(repaired)
    report = {
        "diagnostic": True,
        "note": note or "诊断回放（输入非生产审计输出）",
        "script_path": str(script_path),
        "issues_path": str(issues_path),
        "out_path": str(out_path),
        "log_path": str(log_path),
        "input_revision": before_rev,
        "output_revision": after_rev,
        "changed": before_rev != after_rev,
        "max_rounds": 1,
        "block_reasons": block,
        "repair_diag": diag,
        "issue_targets": [
            {"code": i.code, "shot_id": i.shot_id,
             "repair_targets": list(i.repair_targets or [])}
            for i in issues],
    }
    atomic_dump_json(out_path, repaired, ensure_ascii=False, indent=2)
    atomic_dump_json(out_path.with_suffix(".replay.json"), report,
                     ensure_ascii=False, indent=2)
    return report


def main(argv=None) -> int:
    _p = argparse.ArgumentParser(description="冻结稿上的真实修复回放（诊断）")
    _p.add_argument("--script", required=True, help="冻结稿路径")
    _p.add_argument("--issues", required=True,
                    help="审计 payload（含 repair_targets）；人工修补过的必须用 --note 标注")
    _p.add_argument("--out", required=True, help="修后稿输出路径（诊断路径，不得是正式产物名）")
    _p.add_argument("--note", default="",
                    help="诊断输入说明（如「布局人物下标已人工补全，不是原始审计输出」）")
    _p.add_argument("--model", default=None, help="覆盖修复模型（默认用生产配置）")
    _p.add_argument("--log", default=None, help="本次回放的日志文件（默认 <out>.log）")
    args = _p.parse_args(argv)

    out_path = Path(args.out).resolve()
    _guard_out_path(out_path)
    script_path = Path(args.script).resolve()
    issues_path = Path(args.issues).resolve()
    for _pth in (script_path, issues_path):
        if not _pth.exists():
            raise SystemExit(f"[ABORT] 输入不存在：{_pth}")

    report = replay(script_path, issues_path, out_path, args.note, args.model,
                    Path(args.log).resolve() if args.log else None)
    _diag0 = (report["repair_diag"] or [{}])[0] if report["repair_diag"] else {}
    print(json.dumps({
        "out": report["out_path"],
        "report": str(out_path.with_suffix(".replay.json")),
        "log": report["log_path"],
        "changed": report["changed"],
        "input_revision": report["input_revision"][:12],
        "output_revision": report["output_revision"][:12],
        "block_reasons": report["block_reasons"],
        "targets": _diag0.get("targets"),
        "groups": _diag0.get("groups"),
        "applied": _diag0.get("accepted"),
        "resolved": _diag0.get("resolved"),
        "new_blocking_keys": _diag0.get("new_blocking_keys"),
        "rolled_back_groups": _diag0.get("rolled_back_groups"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
