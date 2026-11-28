# -*- coding: utf-8 -*-
"""规则源指纹卫生与确定性差异复核（2026-11-28 O2）.

三段：
1. `_module_ast_digest`：注释 / 空行 / CRLF 不改变摘要，逻辑与字面量必变；
2. 符号清单：`inject_global_config_into_script` 已移出，且剩余符号的 AST 闭包
   不再触及渲染配置簇（有人把它加回来时必须同步更新口径）；
3. `tools/diff_step01_gate.py`：语义 blocking 判定、文案变化单列、
   `safe_for_offline_refresh` 三条件与 CLI 退出码。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.quality_issues import (  # noqa: E402
    _STEP01_STYLE_PREFIX_SYMBOLS, _module_ast_digest, _module_symbol_digest,
    _compute_rule_source_fingerprint, RULE_SOURCE_FINGERPRINT,
)

_STYLE = _ROOT / "shared" / "style_prefix.py"

_BASE_SOURCE = '''\
"""module docstring"""

X = 1


def rule(value):
    if value > 10:
        return "too_big"
    return "ok"
'''


# ---------------------------------------------------------------------------
# 1) AST 归一化摘要
# ---------------------------------------------------------------------------
def test_comments_blank_lines_and_crlf_do_not_change_digest(tmp_path):
    plain = tmp_path / "plain.py"
    plain.write_text(_BASE_SOURCE, encoding="utf-8", newline="\n")

    noisy = tmp_path / "noisy.py"
    noisy_text = (
        "# 顶部注释\n\n"
        '"""module docstring"""\n'
        "\n\n"
        "X = 1  # 行尾注释\n"
        "\n"
        "\n"
        "def rule(value):\n"
        "    # 函数内注释\n"
        '    if value > 10:\n'
        '        return "too_big"\n'
        "    return \"ok\"   # 又一个注释\n"
        "\n\n"
    )
    noisy.write_text(noisy_text, encoding="utf-8", newline="\n")

    crlf = tmp_path / "crlf.py"
    crlf.write_bytes(_BASE_SOURCE.replace("\n", "\r\n").encode("utf-8"))

    digests = {_module_ast_digest(p) for p in (plain, noisy, crlf)}
    assert len(digests) == 1, "注释/空行/CRLF 不应改变 AST 摘要"


def test_threshold_and_literal_changes_change_digest(tmp_path):
    base = tmp_path / "base.py"
    base.write_text(_BASE_SOURCE, encoding="utf-8", newline="\n")
    baseline = _module_ast_digest(base)

    threshold = tmp_path / "threshold.py"
    threshold.write_text(_BASE_SOURCE.replace("> 10", "> 11"), encoding="utf-8",
                         newline="\n")
    literal = tmp_path / "literal.py"
    literal.write_text(_BASE_SOURCE.replace('"too_big"', '"too_large"'),
                       encoding="utf-8", newline="\n")
    branch = tmp_path / "branch.py"
    branch.write_text(_BASE_SOURCE.replace('return "ok"', 'return "fine"'),
                      encoding="utf-8", newline="\n")

    assert _module_ast_digest(threshold) != baseline
    assert _module_ast_digest(literal) != baseline
    assert _module_ast_digest(branch) != baseline


def test_unparsable_and_missing_modules_are_marked_stably(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("def oops(:\n", encoding="utf-8")
    missing = tmp_path / "missing.py"
    assert _module_ast_digest(broken) == _module_ast_digest(missing) == b"<unreadable>"


def test_docstring_edits_are_treated_as_source_changes(tmp_path):
    """保守口径：docstring 是字符串字面量，改它算判定源码变了（记录在案）."""
    base = tmp_path / "base.py"
    base.write_text(_BASE_SOURCE, encoding="utf-8", newline="\n")
    changed = tmp_path / "changed.py"
    changed.write_text(_BASE_SOURCE.replace('"""module docstring"""',
                                            '"""another docstring"""'),
                       encoding="utf-8", newline="\n")
    assert _module_ast_digest(base) != _module_ast_digest(changed)


# ---------------------------------------------------------------------------
# 2) 符号清单与「展示元数据解耦」
# ---------------------------------------------------------------------------
def test_display_metadata_symbol_is_not_fingerprinted():
    assert "inject_global_config_into_script" not in _STEP01_STYLE_PREFIX_SYMBOLS
    # 其余判定符号仍然在册（删了就会漏掉真实规则变化）
    for name in ("sanitize_script_shots", "sanitize_prompt", "normalize_ff_desc",
                 "detect_lf_midstate", "detect_ff_multi_action",
                 "validate_fps_consistency", "is_empty_scene",
                 "validate_sensitive_words"):
        assert name in _STEP01_STYLE_PREFIX_SYMBOLS, name


def _closure_names(source: str, roots) -> set[str]:
    import ast

    tree = ast.parse(source)
    defs: dict = {}
    for node in tree.body:
        name = getattr(node, "name", None)
        if isinstance(name, str):
            defs.setdefault(name, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs.setdefault(target.id, node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defs.setdefault(node.target.id, node)
    selected: set[str] = set()
    stack = [n for n in roots if n in defs]
    while stack:
        name = stack.pop()
        if name in selected:
            continue
        selected.add(name)
        for sub in ast.walk(defs[name]):
            if isinstance(sub, ast.Name) and sub.id in defs and sub.id not in selected:
                stack.append(sub.id)
    return selected


def test_remaining_symbols_do_not_reach_render_config():
    source = _STYLE.read_text(encoding="utf-8")
    closure = _closure_names(source, _STEP01_STYLE_PREFIX_SYMBOLS)
    for forbidden in ("get_global_render_config", "_GLOBAL_RENDER_DEFAULTS",
                      "frame_canvas_ratio", "inject_global_config_into_script"):
        assert forbidden not in closure, forbidden


def test_render_config_edits_no_longer_move_the_symbol_digest(tmp_path):
    """把「只改渲染元数据」做成影子文件：在册符号的摘要必须不变.

    这正是 `6a4a3e7` 指纹漂移的机制——闭包把展示元数据卷进判定指纹。若有人把
    `inject_global_config_into_script` 加回清单，下半段断言会立刻失败。
    """
    source = _STYLE.read_text(encoding="utf-8")
    marker = "推镜速度约 0.3 倍常规"
    assert marker in source
    shadow = tmp_path / "style_prefix_shadow.py"
    shadow.write_text(source.replace(marker, "推镜速度约 0.4 倍常规", 1), encoding="utf-8")

    assert (_module_symbol_digest(_STYLE, _STEP01_STYLE_PREFIX_SYMBOLS)
            == _module_symbol_digest(shadow, _STEP01_STYLE_PREFIX_SYMBOLS))

    with_injector = tuple(_STEP01_STYLE_PREFIX_SYMBOLS) + ("inject_global_config_into_script",)
    assert (_module_symbol_digest(_STYLE, with_injector)
            != _module_symbol_digest(shadow, with_injector)), \
        "把展示元数据符号加回清单后，渲染配置改动必须能被该机制看见"


def test_fingerprint_is_stable_in_process_and_across_processes():
    assert RULE_SOURCE_FINGERPRINT == _compute_rule_source_fingerprint()
    code = ("from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT as f;"
            "print(f)")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    outs = set()
    for _ in range(2):
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(_ROOT), env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
        assert proc.returncode == 0, proc.stderr[-800:]
        outs.add(proc.stdout.strip())
    assert outs == {RULE_SOURCE_FINGERPRINT}


# ---------------------------------------------------------------------------
# 3) diff_step01_gate：语义 blocking 与 safe_for_offline_refresh
# ---------------------------------------------------------------------------
def _rebuild_manifest(ch: Path, script: dict) -> None:
    """产物被改过之后同步重建提交清单（否则 verify 会按摘要拒收）."""
    from shared import quality_gate
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT as fp

    audit = json.loads((ch / "step01_audit.json").read_text(encoding="utf-8"))
    quality = json.loads((ch / "step01_quality.json").read_text(encoding="utf-8"))
    manifest = quality_gate.build_step01_commit_manifest(
        run_id="run-test", created_at="2026-11-28T00:00:00+08:00",
        revision=script["script_revision"],
        rule_source_fingerprint=str(quality.get("rule_source_fingerprint") or fp),
        rule_version=quality_gate.QUALITY_RULE_VERSION,
        audit_scope=str(audit.get("audit_scope") or "final_after_deterministic_fixes"),
        chapter_dir=ch, status="COMMITTED")
    quality_gate.write_step01_commit_manifest(ch, manifest)


def _write_chapter(root: Path, *, chapter: int = 1):
    """最小「已采纳」章目录：三份产物 + 提交清单（摘要由正式构造器生成）."""
    from shared import config, quality_gate
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT as fp

    ch = root / "output" / "novel" / "part_01_book" / ("chapter_%03d" % chapter)
    ch.mkdir(parents=True)
    script = {"shots": [{"index": 1}], "timeline": [{"index": 1}],
              "hook": {"index": 0, "prompt": "x"}, "chapter_index": chapter,
              "total_duration": 60.0}
    revision = quality_gate.ensure_script_revision(script)
    (ch / "script_result.json").write_text(json.dumps(script, ensure_ascii=False),
                                           encoding="utf-8")
    band = config.duration_band()
    quality = {"status": "PASS", "report_type": "quality_gate", "blocking": False,
               "hard": [], "visual_risk": [], "advisory": [], "video_advisory": [],
               "hard_count": 0, "visual_risk_count": 0,
               "blocking_categories": [], "script_revision": revision,
               "rule_version": quality_gate.QUALITY_RULE_VERSION,
               "rule_source_fingerprint": fp, "duration_band": band}
    (ch / "step01_quality.json").write_text(json.dumps(quality, ensure_ascii=False),
                                            encoding="utf-8")
    audit = {"status": "ok", "input_revision": revision,
             "rule_source_fingerprint": fp,
             "audit_scope": "final_after_deterministic_fixes", "duration_band": band,
             "issues": []}
    (ch / "step01_audit.json").write_text(json.dumps(audit, ensure_ascii=False),
                                          encoding="utf-8")
    manifest = quality_gate.build_step01_commit_manifest(
        run_id="run-test", created_at="2026-11-28T00:00:00+08:00", revision=revision,
        rule_source_fingerprint=fp, rule_version=quality_gate.QUALITY_RULE_VERSION,
        audit_scope="final_after_deterministic_fixes", chapter_dir=ch,
        status="COMMITTED")
    quality_gate.write_step01_commit_manifest(ch, manifest)
    return ch, script, quality


def _receipt(ch: Path, script: dict, *, chapter: int = 1) -> dict:
    import hashlib

    from tools import production_context as pc

    digest = hashlib.sha256((ch / "script_result.json").read_bytes()).hexdigest()
    return {
        "schema": pc.RECEIPT_SCHEMA,
        "status": "adopted_and_verified",
        "formal_chapter": str(ch),
        "output_base_override": str(ch.parents[2]),
        "script_revision": script["script_revision"],
        "scope": {"novel": "novel", "book_index": 1, "book_name": "book",
                  "chapter": chapter},
        "formal_sha256": {"script_result.json": digest},
        "effective_config": {},
    }


@pytest.fixture()
def clean_env(monkeypatch):
    from tools import production_context as pc

    for key in pc.CONTEXT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _issue(code="rule_x", severity="hard", shot=1, message="有问题"):
    return {"code": code, "severity": severity, "shot_id": shot, "frame_id": "",
            "entity_id": "", "field_path": "prompt", "message": message,
            "related_shot_ids": [], "repair_targets": None, "repairable": False}


def test_diff_reports_identical_reports_as_safe(tmp_path, clean_env):
    from tools import diff_step01_gate as dg
    from tools import production_context as pc

    ch, script, quality = _write_chapter(tmp_path)
    receipt = _receipt(ch, script)
    clean_env.setattr(pc, "recompute_step01_quality", lambda *a, **k: dict(quality))
    report = dg.build_report(receipt)
    assert report["safe_for_offline_refresh"] is True
    assert report["blocking_issues_added"]["count"] == 0
    assert report["warnings_added"]["count"] == 0


def test_diff_separates_semantic_additions_from_text_changes(tmp_path, clean_env):
    from tools import diff_step01_gate as dg
    from tools import production_context as pc

    ch, script, quality = _write_chapter(tmp_path)
    receipt = _receipt(ch, script)

    # 同一定位、只改文案：不算新增 blocking
    text_only = dict(quality, advisory=[_issue(severity="advisory", message="旧文案")])
    (ch / "step01_quality.json").write_text(json.dumps(text_only, ensure_ascii=False),
                                            encoding="utf-8")
    _rebuild_manifest(ch, script)
    clean_env.setattr(pc, "recompute_step01_quality", lambda *a, **k: dict(
        quality, advisory=[_issue(severity="advisory", message="新文案")]))
    report = dg.build_report(receipt)
    assert report["warnings_added"]["count"] == 0
    assert report["warnings_removed"]["count"] == 0
    assert len(report["warning_messages_changed"]) == 1
    assert report["safe_for_offline_refresh"] is True

    # 新增语义 blocking：unsafe
    clean_env.setattr(pc, "recompute_step01_quality", lambda *a, **k: dict(
        quality, hard=[_issue()], hard_count=1, status="BLOCK", blocking=True,
        blocking_categories=["rule_x"]))
    report = dg.build_report(receipt)
    assert report["blocking_issues_added"]["count"] == 1
    assert report["safe_for_offline_refresh"] is False


def test_diff_is_unsafe_when_status_blocks_or_audit_not_ok(tmp_path, clean_env):
    from tools import diff_step01_gate as dg
    from tools import production_context as pc

    ch, script, quality = _write_chapter(tmp_path)
    receipt = _receipt(ch, script)

    clean_env.setattr(pc, "recompute_step01_quality",
                      lambda *a, **k: dict(quality, status="BLOCK"))
    assert dg.build_report(receipt)["safe_for_offline_refresh"] is False

    audit = json.loads((ch / "step01_audit.json").read_text(encoding="utf-8"))
    audit["status"] = "blocked"
    (ch / "step01_audit.json").write_text(json.dumps(audit, ensure_ascii=False),
                                          encoding="utf-8")
    _rebuild_manifest(ch, script)
    clean_env.setattr(pc, "recompute_step01_quality", lambda *a, **k: dict(quality))
    report = dg.build_report(receipt)
    assert report["audit_status_old"] == "blocked"
    assert report["safe_for_offline_refresh"] is False


def test_diff_cli_exit_codes(tmp_path, clean_env, capsys):
    from tools import diff_step01_gate as dg
    from tools import production_context as pc

    ch, script, quality = _write_chapter(tmp_path)
    receipt_path = ch / "step01_adoption.json"
    receipt_path.write_text(json.dumps(_receipt(ch, script), ensure_ascii=False),
                            encoding="utf-8")

    clean_env.setattr(pc, "recompute_step01_quality", lambda *a, **k: dict(quality))
    assert dg.main(["--receipt", str(receipt_path), "--json"]) == 0

    clean_env.setattr(pc, "recompute_step01_quality", lambda *a, **k: dict(
        quality, hard=[_issue()], hard_count=1, status="BLOCK", blocking=True))
    assert dg.main(["--receipt", str(receipt_path), "--json"]) == 0
    assert dg.main(["--receipt", str(receipt_path), "--json", "--require-safe"]) == 1
