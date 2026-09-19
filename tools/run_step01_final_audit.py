"""Run a final Step01 semantic audit bound to the frozen script revision."""
from __future__ import annotations

import json
import os
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
from step01_script_gen.audit import run_same_model_audit_with_status
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


def main() -> int:
    root = ROOT
    chapter_dir = root / "output" / "video" / NOVEL_NAME / f"part_{BOOK_INDEX:02d}_{BOOK_NAME}" / f"chapter_{NOVEL_CHAPTER:03d}"
    script_path = chapter_dir / "script_result.json"
    audit_path = chapter_dir / "step01_audit.json"
    initial_backup = chapter_dir / "step01_audit.initial.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    if audit_path.exists() and not initial_backup.exists():
        shutil.copy2(audit_path, initial_backup)

    novel_path = root / "锦衣夜行_77119小说.txt"
    novel_text = _read_raw_chapter(novel_path, NOVEL_CHAPTER)
    plan = script.get("outline") or script.get("plan") or {}
    metadata = {
        "input_revision": compute_script_revision(script),
        "rule_version": RULE_VERSION,
        "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
        "audit_scope": "final_after_deterministic_fixes",
        "script_path": str(script_path),
    }
    audit_focus = [
        "逐镜检查上一镜 LF → 本镜 FF → 全部 KF → 本镜 LF → 下一镜 FF 的状态链；最多返回 20 条，证据简洁。",
        "shot 7.5/8.5/9.5：核对修复后的 motion 时间前缀、主体文本和完整状态链，确认没有负时间或截断角色名。",
        "shot 2：核对首帧布局与 char_layout 是否来自同一站位来源；不要自动分散人物位置。",
        "shot 5：核对纵身跃上、落在船梢、俯身查看与 layout pose 是否一致。",
        "shot 9.5：核对群演哄笑、转身离去与张十三叫住他们的主体及在场状态。",
        "检查武器释放后是否持续释放，直到明确重新持有。",
        "检查是否存在动作重复、遗漏、未来状态提前或无事件依据的动作补充。",
        "未决项必须保留并给出原文证据，不得为了通过门禁而删除证据。",
    ]
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
        "raw_response": (result.raw_text or "")[:20000],
    }
    atomic_dump_json(audit_path, payload, ensure_ascii=False, indent=2)
    print(json.dumps({
        "path": str(audit_path),
        "status": result.status,
        "issues": len(result.issues),
        "revision": metadata["input_revision"],
        "fingerprint": metadata["rule_source_fingerprint"],
    }, ensure_ascii=False))
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
