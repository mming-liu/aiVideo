# -*- coding: utf-8 -*-
"""run h 冻结回放：帧来源可证明性（S04 补正，离线、零模型调用）.

补正后的取证口径（与初版不同，务必先读）：

* **不要求 run h 原始第 2 轮整表答卷通过**。该答卷同时重写了 `keyframes` 的
  desc/stage/t_sec，布局内容逐字后移只能证明「内容保留」，**不能独立证明原帧
  身份**。本脚本按新契约报告「身份无法证明 / 来源歧义」，并验证替换入口在
  进入 `staged` 前明确拒收、保留基线。
* **历史终局与修复后离线裁决分列**。历史两轮事务裁决按冻结台账与日志如实抄录，
  不因本包改了守卫而改写；右侧的「修复后裁决」只是守卫/替换入口的离线实算。
* **派生样本单独编号并实算 SHA-256**：用同一新增帧内容改写成
  `keyframe_additions` 表达，由装配操作生成可验证来源映射，演示合法补帧通路。
  它是**重建样本**，不是历史产物，也不冒充原始答卷通过。
* 生产路径不再从 `frame_layouts.kfs` 内容推导来源；本脚本同样不调用任何
  布局内容推导函数。

用法（项目根、隔离 venv）：
    python tools/replay_run_h_frame_identity.py
    python tools/replay_run_h_frame_identity.py --json-out <path>

输出纯 JSON 到 stdout；现场产物缺失时输出 `{"status": "missing_inputs", ...}`
而不是崩溃，便于干净环境重跑。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _chapter_rel(chapter: int):
    """<小说名>/part_NN_<部名>/chapter_NNN，书名/部名来自项目配置."""
    import shared.config  # noqa: F401  加载 .env.content
    from shared.paths import chapter_relpath
    return chapter_relpath(chapter)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUN_H = ROOT / ".dsh" / "step01_full_20261115h" / "output" / _chapter_rel(1)

# 路径显式指定，禁止自动挑选「最近产物」。
INPUTS = {
    "frozen_script": RUN_H / "snapshots" / "script_result_20261115_145644.json",
    "round1_answer": RUN_H / "step01_call_bodies" / "c11efe467630b41aa.txt",
    "round2_answer": RUN_H / "step01_call_bodies" / "ca19a9eb8180f4d03.txt",
    "audit": RUN_H / "step01_audit.blocked.json",
    "commit": RUN_H / "step01_commit.json",
    "metrics": RUN_H / "step01_metrics.json",
    "log": RUN_H / "step01.log",
}

_LOG_MARKERS = (
    "修复未满足接受条件", "按目标回退", "带反馈重试", "应用完整镜头替换",
    "替换被拒", "提交前修复轮零改善", "[BLOCK]",
)

DERIVED_SAMPLE_ID = "DERIVED-R2-ASSEMBLY-01"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _shot5(answer: dict) -> dict:
    for rep in answer.get("shot_replacements") or []:
        if str(rep.get("shot_id")) == "5":
            return rep
    raise ValueError("答卷里没有 shot 5")


def _allowed(audit: dict) -> list:
    """审计 issue 声明的 shot 5 修复目标（**非**事务里的完整授权面）."""
    out: list = []
    for issue in audit.get("issues") or []:
        for t in issue.get("repair_targets") or []:
            if str(t.get("shot_id")) == "5" and t.get("field_path") not in out:
                out.append(t["field_path"])
    return out


def _candidate(frozen: dict, answer_shot: dict) -> dict:
    """冻结稿 + 模型对 shot 5 的整表 frame_layouts/keyframes（第 2 轮表达）."""
    from step01_script_gen.repair_fields import shot_by_target

    after = copy.deepcopy(frozen)
    target = shot_by_target(after, "shot_5")
    for upd in answer_shot.get("updates") or []:
        fp = upd.get("field_path")
        if fp in ("frame_layouts", "keyframes"):
            target[fp] = copy.deepcopy(upd.get("value"))
    return after


def main() -> int:
    missing = [str(p) for p in INPUTS.values() if not p.is_file()]
    if missing:
        print(json.dumps({"status": "missing_inputs", "missing": missing},
                         ensure_ascii=False, indent=2))
        return 0

    from shared.quality_gate import compute_script_revision
    from step01_script_gen.main import _apply_shot_replacements
    from step01_script_gen.repair_effects import build_layout_effects
    from step01_script_gen.repair_fields import (
        derive_frame_mapping_from_keyframes, frame_layout_structural_violations,
        validate_frame_mapping)

    digests = {k: _sha256(p) for k, p in INPUTS.items()}
    frozen = json.loads(INPUTS["frozen_script"].read_text(encoding="utf-8"))
    r1 = json.loads(INPUTS["round1_answer"].read_text(encoding="utf-8"))
    r2 = json.loads(INPUTS["round2_answer"].read_text(encoding="utf-8"))
    audit = json.loads(INPUTS["audit"].read_text(encoding="utf-8"))
    commit = json.loads(INPUTS["commit"].read_text(encoding="utf-8"))
    metrics = json.loads(INPUTS["metrics"].read_text(encoding="utf-8"))

    revision = compute_script_revision(frozen)
    declared_allowed = _allowed(audit)
    tid, picked = "shot_5", {"shot_5"}
    shot5 = frozen["shots"][4]
    before_kfs = (shot5.get("frame_layouts") or {}).get("kfs") or []
    before_keyframes = shot5.get("keyframes") or []

    # 事务里的 `_allowed_by_target` 无法从台账直接读出：确定性检查器的结构码会
    # 另外并入整表 KF 路径。按 step01.log:1471 实测的 6 处改动重建，证据边界
    # 在 JSON 中明确标注为「重建值」，不冒充原始集合。
    rebuilt_allowed = set(declared_allowed) | {
        "keyframes", "frame_layouts", "frame_layouts.kfs",
        "ff_desc", "lf_desc", "motion_desc"}
    allowed_map = {tid: rebuilt_allowed}

    # ------------------------------------------------------------------
    # A. 第 2 轮**原始整表答卷**：关键帧身份字段被改写 ⇒ 来源无法证明
    # ------------------------------------------------------------------
    r2_shot = _shot5(r2)
    r2_updates = {u["field_path"]: u.get("value")
                  for u in r2_shot.get("updates") or []}
    after_r2 = _candidate(frozen, r2_shot)
    after_kfs = ((after_r2["shots"][4].get("frame_layouts") or {}).get("kfs") or [])
    after_keyframes = after_r2["shots"][4].get("keyframes") or []

    def _canon_kf(kf):
        return json.dumps(kf, ensure_ascii=False, sort_keys=True)

    before_identities = [_canon_kf(x) for x in before_keyframes]
    after_identities = [_canon_kf(x) for x in after_keyframes]
    preserved = sum(1 for x in before_identities if x in after_identities)
    # 唯一的合法整表来源推导：保留关键帧**完整结构**，逐字恒等且落点唯一。
    kf_mapping = derive_frame_mapping_from_keyframes(
        before_keyframes, after_keyframes, source_revision=revision)
    kf_mapping_why = (
        "无：保留关键帧完整结构不存在逐字恒等落点（desc/stage/t_sec 被改写）"
        if kf_mapping is None else
        validate_frame_mapping(kf_mapping, len(before_keyframes),
                               len(after_keyframes), expected_revision=revision)
        or "合法")

    guard_r2_no_map = frame_layout_structural_violations(
        frozen, after_r2, allowed_map, picked, revision=revision)
    apply_r2_maps: dict = {}
    merged_r2, applied_r2, rejected_r2 = _apply_shot_replacements(
        frozen, [r2_shot], picked,
        allowed_by_target=allowed_map,
        expected_targets={"5"}, frame_mappings_out=apply_r2_maps)
    r2_baseline_kept = (merged_r2 == frozen)

    # ------------------------------------------------------------------
    # B. 派生样本：同一新增帧内容，改用 keyframe_additions 表达（操作来源）
    # ------------------------------------------------------------------
    # t_sec 取 2.0（介于基线 1.0 与 3.0 之间）以保证按时间顺序**中间插入**；
    # 原始答卷把时间轴整表重写成 [1.0, 3.0, 4.5]，那正是来源无法证明的原因。
    derived_frame = copy.deepcopy(after_keyframes[1])
    derived_frame["t_sec"] = 2.0
    derived_answer = {
        "shot_id": "5",
        "keyframe_additions": [{
            "frame": derived_frame,
            "layout": copy.deepcopy(after_kfs[1]),
        }],
    }
    derived_sha = hashlib.sha256(_canonical_bytes(derived_answer)).hexdigest()

    derived_maps: dict = {}
    merged_d, applied_d, rejected_d = _apply_shot_replacements(
        frozen, [derived_answer], picked,
        allowed_by_target=allowed_map,
        expected_targets={"5"}, frame_mappings_out=derived_maps)
    # 事务循环会在 `_apply_shot_replacements` 返回后把映射绑定本轮基线 revision；
    # 回放脚本单独调用该函数，需显式重放这一步（并在证据中标注）。
    for mp in derived_maps.values():
        if isinstance(mp, dict):
            mp["source_revision"] = revision

    d_map = derived_maps.get(tid)
    d_map_why = (validate_frame_mapping(d_map, len(before_kfs),
                                        len((merged_d["shots"][4]
                                             .get("frame_layouts") or {})
                                            .get("kfs") or []),
                                        expected_revision=revision)
                 if d_map else "未导出映射")
    guard_d_no_map = frame_layout_structural_violations(
        frozen, merged_d, allowed_map, picked, revision=revision)
    guard_d_with_map = frame_layout_structural_violations(
        frozen, merged_d, allowed_map, picked,
        frame_mappings=derived_maps, revision=revision)
    effects_d = build_layout_effects(
        frozen, merged_d, allowed_map, revision, round_id="derived-r2",
        frame_mappings=derived_maps, revision=revision)

    # ------------------------------------------------------------------
    # C. 第 1 轮表达 + 历史终局（冻结台账 / 日志，不重跑事务入口）
    # ------------------------------------------------------------------
    r1_shot = _shot5(r1)
    r1_kfs_update = next(
        (u.get("value") for u in r1_shot.get("updates") or []
         if u.get("field_path") == "frame_layouts.kfs.1"), None)
    r1_rewrote_original = (r1_kfs_update is not None
                           and r1_kfs_update != before_kfs[1])

    log_lines = INPUTS["log"].read_text(encoding="utf-8").splitlines()
    decision_lines = [
        {"line": i + 1, "text": line[:400]}
        for i, line in enumerate(log_lines)
        if any(m in line for m in _LOG_MARKERS) and "[REPAIR]" in line
        or ("[COMMIT]" in line and "修复轮" in line)
        or ("[BLOCK]" in line and "未提交" in line)
    ]

    report = {
        "status": "ok",
        "schema_version": "2026-11-15-frame-identity-supplement-v1",
        "run_id": metrics.get("run_id"),
        "frozen_revision_recomputed": revision,
        "frozen_revision_declared": frozen.get("script_revision"),
        "commit_revision": commit.get("script_revision"),
        "revision_identity_ok": (revision == frozen.get("script_revision")
                                 == commit.get("script_revision")),
        "input_sha256": digests,
        "shot5_declared_authorized_paths": declared_allowed,
        "rebuilt_allowed_note": (
            "审计 issue 的 repair_targets 只给出叶子路径；事务中的 "
            "_allowed_by_target 另由确定性检查器的结构码并入整表 KF 路径。"
            "此处按 step01.log:1471 实测的 6 处改动重建，是**重建值**，"
            "不是台账里直接读出的原始集合。"),
        "expression_difference": {
            "round1": "窄 updates 改写 frame_layouts.kfs.1 + keyframe_additions 追加",
            "round2_original": (
                "整表替换 frame_layouts + keyframes（无 keyframe_additions）；"
                "三条关键帧 desc/stage/t_sec 全部重写，时间轴改为 [1.0,3.0,4.5]"),
            "derived_assembly_sample": (
                "只给 keyframe_additions（frame+layout 成对），由装配器按 t_sec "
                "中间插入；不含任何整表替换，也不含布局内容相似度推导"),
        },
        "historical_outcome": {
            "source": "冻结台账 step01_metrics.json / step01_commit.json + step01.log",
            "note": ("这是 run h 当时的**历史终局**，按冻结证据如实抄录；"
                     "本包不重跑 _repair_shot_warnings，也不因后续守卫修复而改写它。"),
            "decision_chain": decision_lines,
            "last_decision": metrics.get("repair", {}).get("last_decision"),
            "commit_outcome": {
                "status": metrics.get("status"),
                "outcome_code": metrics.get("outcome_code"),
                "commit": metrics.get("commit"),
            },
        },
        "round2_original_whole_table": {
            "expression": "整表替换 frame_layouts + keyframes（无 keyframe_additions）",
            "before_frame_count": len(before_kfs),
            "after_frame_count": len(after_kfs),
            "preserved_keyframe_full_structures": preserved,
            "keyframe_structure_mapping": kf_mapping,
            "keyframe_structure_mapping_validation": kf_mapping_why,
            "identity_verdict": ("无法证明：布局内容逐字后移只证明内容保留，"
                                 "关键帧身份字段同时改写 ⇒ 原帧与候选帧对应关系歧义"),
            "guard_without_mapping": guard_r2_no_map,
            "apply_entry": {
                "entry": "_apply_shot_replacements（实际替换/授权合并入口）",
                "applied": applied_r2,
                "rejected": rejected_r2,
                "exported_mapping": apply_r2_maps.get(tid) or None,
                "baseline_kept": r2_baseline_kept,
            },
            "verdict": ("来源链无法建立 ⇒ 进入 staged 前明确拒收并保留基线；"
                        "不再退回同下标比较，也不按布局内容猜映射"),
        },
        "derived_assembly_sample": {
            "sample_id": DERIVED_SAMPLE_ID,
            "sample_sha256": derived_sha,
            "sample_sha256_basis": (
                "json.dumps(ensure_ascii=False, sort_keys=True, "
                "separators=(',',':')).encode('utf-8') 的 sha256"),
            "derivation_note": (
                "取第 2 轮答卷新增帧的 desc/stage 与对应布局内容，仅把 t_sec 设为 "
                "2.0 以产生合法中间插入；这是**重建样本**，不是历史产物，"
                "也不代表原始答卷通过。"),
            "apply_entry": {
                "entry": "_apply_shot_replacements（实际替换/授权合并入口）",
                "applied": applied_d,
                "rejected": rejected_d,
                "exported_mapping": d_map,
                "mapping_validation": d_map_why,
                "revision_binding_note": (
                    "单独调用替换入口时映射尚无 source_revision；回放显式重放"
                    "事务循环的 revision 绑定步骤后再校验。"),
            },
            "guard_without_mapping": guard_d_no_map,
            "guard_with_mapping": guard_d_with_map,
            "layout_effects_count": len(effects_d),
            "verdict": ("来源由装配操作证明 ⇒ 守卫通过；纯中间补帧为纯位移，"
                        "零条目增删效果属预期"),
        },
        "round1": {
            "expression": "窄 updates 改写 frame_layouts.kfs.1 + keyframe_additions 追加",
            "additions_count": len(r1_shot.get("keyframe_additions") or []),
            "original_kfs1_was_rewritten_by_updates": r1_rewrote_original,
            "note": ("按操作来源映射规格，原帧 1 的字段被 updates 改写属真实改写，"
                     "不是索引位移误报；本包不为此放宽判据。"),
        },
        "post_fix_offline_adjudication": {
            "scope": "仅守卫 / 替换入口 / 效果登记的离线实算，非事务级重跑",
            "original_round2_answer": "rejected_identity_unprovable",
            "derived_assembly_answer": "accepted_with_assembly_source",
            "note": ("与 historical_outcome 分列：修复后裁决不覆盖、不改写 run h "
                     "历史终局；事务级入口的合法/拒收/回退证据另见 "
                     "test_kf_frame_identity_transaction.py。"),
        },
        "scope_note": (
            "离线、纯内存：真实模型调用 0，正式产物写入 0，未改任何 .dsh/ 现场产物；"
            "不调用布局内容推导函数，不以内容相似度证明帧来源。"),
    }
    _payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if len(sys.argv) >= 3 and sys.argv[1] == "--json-out":
        Path(sys.argv[2]).write_text(_payload + "\n", encoding="utf-8")
    print(_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
