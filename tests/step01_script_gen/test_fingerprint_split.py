# -*- coding: utf-8 -*-
"""指纹分层：**判定版本** vs **生成/修复提示词版本**（v6.112，总计划 §1.3）.

审核 P2-5 的边界（用户 2026-09-24 决策）：

  - 判定版本指纹（`RULE_SOURCE_FINGERPRINT`，参与门禁与审计复用）要**覆盖判定
    实现**——`constraints/` 70+ 条硬修复与 `prompt_semantic/` 关键词表直接决定
    告警与阻塞口径，此前不在指纹里，于是「只改判定、不改内容」的改动会让旧报告
    被错误地复用；
  - 但**不要把整个 `main.py` 一股脑塞进判定指纹**：改修复提示词改变的是稿件内容，
    内容变化已由 `script_revision` 覆盖，塞进去只会让门禁无谓失效。这类改动记在
    `PROMPT_SOURCE_FINGERPRINT`，**只作诊断、不参与准入或复用**。
"""
from __future__ import annotations

import hashlib
import inspect
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 指纹分层之前，判定指纹覆盖的文件（用于证明新目录确实**追加**进去了）
_LEGACY_JUDGMENT_FILES = (
    "step01_script_gen/quality_issues.py",
    "step01_script_gen/state_updates.py",
    "step01_script_gen/validation.py",
    "step01_script_gen/timecode.py",
    "step01_script_gen/split_visuals.py",
    "shared/style_prefix.py",
    "shared/vimax_bridge.py",
    "step01_script_gen/audit.py",
    "step01_script_gen/cross_field_checks.py",
    "step01_script_gen/kf_structural_guard.py",
    "shared/motion_segments.py",
    "shared/quality_gate.py",
    "shared/semantic_barrier.py",
    "_text_gen/deepseek_schema.py",
)


def _legacy_digest() -> str:
    digest = hashlib.sha256()
    for rel in _LEGACY_JUDGMENT_FILES:
        digest.update((_PROJECT_ROOT / rel).read_bytes())
    return digest.hexdigest()[:16]


def test_judgment_fingerprint_covers_constraints_and_semantic_tables():
    """判定指纹必须覆盖 `constraints/` 与 `prompt_semantic/`（漏了它们就漏了判定）."""
    from step01_script_gen.quality_issues import _compute_rule_source_fingerprint

    src = inspect.getsource(_compute_rule_source_fingerprint)
    for sub in ("constraints", "prompt_semantic"):
        assert ('"%s"' % sub) in src, "判定指纹未覆盖目录 %s" % sub
        d = _PROJECT_ROOT / "step01_script_gen" / sub
        assert d.is_dir() and list(d.rglob("*.py")), "%s 目录不存在或没有实现" % sub
    # 硬修复引擎与世界书版本索引确实被覆盖（不是空目录）
    names = {p.name for p in (_PROJECT_ROOT / "step01_script_gen" / "constraints").rglob("*.py")}
    assert "engine.py" in names
    assert len(names) > 50, "constraints/ 应包含 70+ 条约束实现"
    assert ("""rules_index.py""") in src, "约束框架的版本索引也要进判定指纹"
    assert (_PROJECT_ROOT / "step01_script_gen" / "rules_index.py").exists()


def test_judgment_fingerprint_excludes_generation_prompt_sources():
    """`main.py` / `outline.py` / `prompts.py` **不得**进判定指纹（用户决策的边界）."""
    from step01_script_gen.quality_issues import _compute_rule_source_fingerprint

    src = inspect.getsource(_compute_rule_source_fingerprint)
    for name in ("main.py", "outline.py", "prompts.py"):
        assert ('"%s"' % name) not in src, (
            "%s 不该进判定指纹——改提示词只改变稿件内容，内容变化由 revision 覆盖"
            % name)


def test_prompt_fingerprint_covers_generation_and_repair_prompt_sources():
    from step01_script_gen.quality_issues import _compute_prompt_source_fingerprint

    src = inspect.getsource(_compute_prompt_source_fingerprint)
    for name in ("prompts.py", "outline.py", "main.py"):
        assert ('"%s"' % name) in src, "提示词版本指纹未覆盖 %s" % name
        assert (_PROJECT_ROOT / "step01_script_gen" / name).exists()
    assert "prompts" in src and "rglob" in src, "prompts/ 目录（各站点模板）也要覆盖"
    # 2026-11-19 S04：main.py 拆到 orchestrator/ 后，提示词指纹必须覆盖该目录
    assert '"orchestrator"' in src, "提示词版本指纹未覆盖 orchestrator/"


def test_judgment_fingerprint_excludes_orchestrator():
    """拆出来的编排实现与 main.py 同一边界：不进判定指纹."""
    from step01_script_gen.quality_issues import _compute_rule_source_fingerprint
    src = inspect.getsource(_compute_rule_source_fingerprint)
    assert "orchestrator" not in src


def test_new_directories_really_change_the_judgment_fingerprint():
    """端到端：判定指纹 ≠ 只哈希旧清单的值——证明新目录**确实**参与计算."""
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    assert RULE_SOURCE_FINGERPRINT != _legacy_digest()


def test_the_two_fingerprints_are_distinct_hex16():
    from step01_script_gen.quality_issues import (PROMPT_SOURCE_FINGERPRINT,
                                                  RULE_SOURCE_FINGERPRINT)

    for fp in (RULE_SOURCE_FINGERPRINT, PROMPT_SOURCE_FINGERPRINT):
        assert len(fp) == 16 and all(c in "0123456789abcdef" for c in fp), fp
    assert RULE_SOURCE_FINGERPRINT != PROMPT_SOURCE_FINGERPRINT


def test_manifest_records_prompt_fingerprint_without_gating_on_it(tmp_path):
    """manifest 记提示词版本；`verify` 只认 revision + 摘要，不受它影响."""
    from shared.quality_gate import (STEP01_ARTIFACT_FILES,
                                     build_step01_commit_manifest,
                                     compute_script_revision,
                                     verify_step01_commit_manifest)
    from step01_script_gen.quality_issues import (PROMPT_SOURCE_FINGERPRINT,
                                                  RULE_SOURCE_FINGERPRINT)

    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    for name in STEP01_ARTIFACT_FILES:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    revision = compute_script_revision(script)
    manifest = build_step01_commit_manifest(
        run_id="r", created_at="now", revision=revision,
        rule_source_fingerprint=RULE_SOURCE_FINGERPRINT, rule_version="v",
        audit_scope="final_after_deterministic_fixes", chapter_dir=tmp_path,
        status="COMMITTED", prompt_source_fingerprint=PROMPT_SOURCE_FINGERPRINT)

    assert manifest["prompt_source_fingerprint"] == PROMPT_SOURCE_FINGERPRINT
    from shared.quality_gate import write_step01_commit_manifest
    write_step01_commit_manifest(tmp_path, manifest)
    # 真实 consumer：不因多了一个诊断字段而拒绝
    verify_step01_commit_manifest(tmp_path, script, stage="step01")
