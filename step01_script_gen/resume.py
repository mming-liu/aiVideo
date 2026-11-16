# -*- coding: utf-8 -*-
"""S05：从**明确的修订点**恢复 Step01（正式入口，2026-11-07）.

为什么是独立模块而不是 main() 里的一个开关：

  · 时长带 / 提示版本等运行身份在 **import 时**就进入 shared.config 与
    step01_script_gen.main 的模块常量；
  · 恢复的目标恰恰是"用**冻结时**的身份再跑一次"，所以必须先读清单、再设置
    env、最后 import main。把开关放进 main() 会在 config 已经求值之后才生效，
    于是 _reusable_final_audit() 永远匹配不上（实测：默认配置不可复用，
    恢复 60-90 / 70-80 后可复用）。

用法：

    python -m step01_script_gen.resume --manifest <chapter>/step01_resume.json --dry-run
    python -m step01_script_gen.resume --manifest <chapter>/step01_resume.json

--dry-run 只做身份核验并打印将要消费的阶段，**不调用模型、不写正式产物**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

MANIFEST_NAME = "step01_resume.json"
MANIFEST_VERSION = 1

REQUIRED_KEYS = (
    "manifest_version", "run_id", "stage",
    "chapter_dir", "novel_name", "book_index", "book_name", "novel_chapter",
    "snapshot_path", "snapshot_sha256", "script_revision",
    "audit_path", "audit_revision", "audit_status",
    "rule_source_fingerprint", "audit_prompt_version", "audit_focus_version",
    "audit_scope", "duration_band", "channel_provider", "channel_model",
    "novel_text_path", "novel_sha256", "plan_path", "plan_sha256",
)


class ResumeError(RuntimeError):
    """恢复身份不成立（缺字段 / 摘要不符 / 修订点不匹配）。"""


def _sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def freeze_text(chapter_dir, text: str) -> tuple:
    """把一段章节文本按内容摘要冻结到 <chapter>/_resume/，返回 (path, sha256)."""
    chapter = Path(chapter_dir)
    data = (text or "").encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    out_dir = chapter / "_resume"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("novel_%s.txt" % digest[:12])
    if not out.exists() or _sha256_file(out) != digest:
        out.write_bytes(data)
    return str(out), digest


def _freeze_novel_text(chapter: Path, source_path) -> tuple:
    """把**已提取的章节文本**冻结进章节目录，恢复/ dry-run 只读它.

    为什么不能在这里 read_chapter：那条链在场景缓存缺失时会调用模型并写缓存
    （shared/chapter_reader 的缓存未命中分支）——"dry-run 零调用"就保不住。
    所以 build 必须显式给一个纯文本文件，这里只做复制与摘要。
    """
    if not source_path:
        raise ResumeError(
            "--build 必须显式给 --novel-text <章节文本文件>；"
            "构建清单时不得解析原文（会触发场景分析调用模型）")
    src = Path(source_path)
    if not src.exists():
        raise ResumeError("章节文本文件不存在: %s" % src)
    text = src.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    out_dir = chapter / "_resume"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("novel_%s.txt" % digest[:12])
    if not out.exists() or _sha256_file(out) != digest:
        # 必须按字节写：文本模式的 newline 转换会让"文件摘要"与"字符串摘要"不一致
        out.write_bytes(text.encode("utf-8"))
    return str(out), digest


def build_manifest(chapter_dir, *, snapshot_path, audit_path=None,
                   novel_text_path=None, plan_path=None, run_id: str = "",
                   stage: str = "commit") -> dict:
    """从现有产物生成恢复清单（不猜路径：snapshot 必须显式给）."""
    from shared.config import duration_band
    from shared.quality_gate import compute_script_revision

    chapter = Path(chapter_dir).resolve()
    plan_path = Path(plan_path) if plan_path else (chapter / "chapter_outline.cache.json")
    if not plan_path.exists():
        raise ResumeError("缺少计划（chapter_outline.cache.json）——无法冻结计划身份")
    snap = Path(snapshot_path).resolve()
    if not snap.exists():
        raise ResumeError("快照不存在: %s" % snap)
    script = _read_json(snap)
    if isinstance(script.get("script"), dict):
        script = script["script"]

    audit_p = audit_path
    if not audit_p:
        for name in ("step01_audit.json", "step01_audit.blocked.json"):
            cand = chapter / name
            if cand.exists():
                audit_p = cand
                break
    if not audit_p:
        raise ResumeError("找不到审计文件（step01_audit[.blocked].json）")
    audit_p = Path(audit_p).resolve()
    audit = _read_json(audit_p)
    # 快照与审计必须是**同一修订点**：BLOCK 时快照存入口稿、审计可能是修后稿，
    # 两者错配的清单会让恢复在错误的身份上继续（2026-11-07 实测）。
    _novel_frozen_path, _novel_frozen_sha = _freeze_novel_text(chapter, novel_text_path)
    _rev_snap = compute_script_revision(script)
    _rev_audit = str(audit.get("input_revision") or "")
    if _rev_audit != _rev_snap:
        raise ResumeError("快照与审计不是同一修订点（快照 %s / 审计 %s）——拒绝生成清单"
                          % (_rev_snap[:12], _rev_audit[:12]))

    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id or str(audit.get("run_id") or ""),
        "stage": stage,
        "chapter_dir": str(chapter),
        "novel_name": os.getenv("NOVEL_NAME", ""),
        "book_index": int(os.getenv("BOOK_INDEX", "1") or 1),
        "book_name": os.getenv("BOOK_NAME", ""),
        "novel_chapter": int(os.getenv("NOVEL_CHAPTER", "1") or 1),
        "snapshot_path": str(snap),
        "snapshot_sha256": _sha256_file(snap),
        "script_revision": compute_script_revision(script),
        "audit_path": str(audit_p),
        "audit_revision": str(audit.get("input_revision") or ""),
        "audit_status": str(audit.get("status") or ""),
        "rule_source_fingerprint": str(audit.get("rule_source_fingerprint") or ""),
        "audit_prompt_version": str(audit.get("audit_prompt_version") or ""),
        "audit_focus_version": str(audit.get("audit_focus_version") or ""),
        "audit_scope": str(audit.get("audit_scope") or ""),
        # 审计记录里写着它被产出时的时长带——那是这份审计的**身份**。
        # 用当前进程的生效带会把"别的章的采纳配置"写进清单（实测 60/120/105/115）。
        "duration_band": dict(audit.get("duration_band") or duration_band()),
        "channel_provider": str(audit.get("channel_provider") or ""),
        "channel_model": str(audit.get("model") or ""),
        "novel_text_path": _novel_frozen_path,
        "novel_sha256": _novel_frozen_sha,
        "plan_path": str(plan_path),
        "plan_sha256": _sha256_file(plan_path) if plan_path.exists() else "",
        "unresolved": [i.get("code") for i in (audit.get("issues") or [])
                       if isinstance(i, dict)][:50],
        "usage": audit.get("usage") or {},
    }


PENDING_STATUS = "pending_reaudit"


def build_pending_manifest(chapter_dir, *, audited_snapshot_path, audit_path,
                           pending_path, round_no: int, novel_text_path=None,
                           plan_path=None, run_id: str = "") -> dict:
    """生成「**已审稿** + **待复审稿**」双块清单（M-A2，2026-11-17）.

    保留原硬约束：`audit` 只能证明 `snapshot_path` 那一份稿——两者 revision
    必须相同（不符直接 `ResumeError`，与 `build_manifest` 同一条判据）。

    新增的 `pending_candidate` 是**另一份、尚未审计**的稿（修复轮采纳稿）：
    显式标注 `requires_fresh_audit=True`，并写 `next_action`。于是恢复入口可以
    "从待复审稿开始、重审一次"，而不会把旧审计结论盖到新稿上。
    """
    from shared.quality_gate import compute_script_revision

    man = build_manifest(chapter_dir, snapshot_path=audited_snapshot_path,
                         audit_path=audit_path, novel_text_path=novel_text_path,
                         plan_path=plan_path, run_id=run_id, stage=PENDING_STATUS)
    pending = Path(pending_path).resolve()
    if not pending.is_file():
        raise ResumeError("待复审稿不存在: %s" % pending)
    pscript = _read_json(pending)
    if isinstance(pscript.get("script"), dict):
        pscript = pscript["script"]
    prev = compute_script_revision(pscript)
    if prev == str(man["script_revision"]):
        # 同 revision 不需要"待复审"——那等于拿旧审计盖新稿
        raise ResumeError("待复审稿与已审稿同 revision（%s）：不需要复审，拒绝生成清单"
                          % prev[:12])
    man["pending_candidate"] = {
        "path": str(pending),
        "sha256": _sha256_file(pending),
        "script_revision": prev,
        "round": int(round_no),
        "requires_fresh_audit": True,
        "status": "pending_reaudit",
    }
    man["next_action"] = "reaudit_pending_candidate"
    man["audited"] = {
        "snapshot_path": man["snapshot_path"],
        "snapshot_sha256": man["snapshot_sha256"],
        "script_revision": man["script_revision"],
        "audit_path": man["audit_path"],
        "audit_revision": man["audit_revision"],
        "audit_status": man["audit_status"],
    }
    return man


def validate_static(man: dict) -> None:
    """**不导入任何项目模块**的基础校验（必须在 apply_config 之前跑）.

    shared.quality_gate 会间接 import shared.config，而 DURATION_* 是 import 期
    常量——先算 revision 再用清单改 env，配置就永远对不上（复核已复现）。
    所以这里只碰 hashlib/json/pathlib：字段、摘要、审计 JSON 自身、band 形状。
    """
    if not isinstance(man, dict):
        raise ResumeError("清单不是对象")
    missing = [k for k in REQUIRED_KEYS
               if k not in man or (man.get(k) in ("", None) and k != "duration_band")]
    if isinstance(man.get("duration_band"), dict):
        missing = [k for k in missing if k != "duration_band"]
    if missing:
        raise ResumeError("清单缺字段: %s" % missing)
    if int(man.get("manifest_version") or 0) != MANIFEST_VERSION:
        raise ResumeError("清单版本不支持: %r" % man.get("manifest_version"))

    snap = Path(man["snapshot_path"])
    if not snap.exists():
        raise ResumeError("快照不存在: %s" % snap)
    if _sha256_file(snap) != str(man["snapshot_sha256"]):
        raise ResumeError("快照摘要不符（文件已被改动）: %s" % snap)
    novel = Path(man["novel_text_path"])
    if not novel.exists():
        raise ResumeError("冻结章节文本不存在: %s" % novel)
    if _sha256_file(novel) != str(man["novel_sha256"]):
        raise ResumeError("冻结章节文本摘要不符: %s" % novel)
    plan = Path(man["plan_path"])
    if not plan.exists():
        raise ResumeError("计划不存在: %s" % plan)
    if _sha256_file(plan) != str(man["plan_sha256"]):
        raise ResumeError("计划摘要不符（计划已被改动）: %s" % plan)
    if str(man["audit_status"]).lower() != "ok":
        raise ResumeError("审计状态不是 ok（失败审计不得复用）: %s" % man["audit_status"])
    audit = _read_json(Path(man["audit_path"]))
    for key in ("rule_source_fingerprint", "audit_prompt_version",
                "audit_focus_version", "audit_scope"):
        if str(audit.get(key) or "") != str(man.get(key) or ""):
            raise ResumeError("审计字段 %s 与清单不符（审计文件被替换？）" % key)
    band = man["duration_band"]
    if set(band) != {"min", "max", "target_min", "target_max"}:
        raise ResumeError("duration_band 字段不全: %s" % band)


def validate_semantic(man: dict) -> None:
    """应用配置**之后**的语义校验：修订点必须与审计、快照一致."""
    from shared.quality_gate import compute_script_revision

    script = _read_json(Path(man["snapshot_path"]))
    if isinstance(script.get("script"), dict):
        script = script["script"]
    rev = compute_script_revision(script)
    if rev != str(man["script_revision"]):
        raise ResumeError("快照修订点不符：实算 %s != 清单 %s" % (rev, man["script_revision"]))
    if str(man["audit_revision"]) != rev:
        raise ResumeError("审计修订点与快照不符：审计 %s != 快照 %s"
                          % (man["audit_revision"], rev))


def apply_config(man: dict) -> None:
    """把清单里的运行身份写进 env —— **必须在 import main 之前**调用."""
    band = man["duration_band"]
    os.environ["DURATION_MIN_SEC"] = str(band["min"])
    os.environ["DURATION_MAX_SEC"] = str(band["max"])
    os.environ["DURATION_TARGET_MIN_SEC"] = str(band["target_min"])
    os.environ["DURATION_TARGET_MAX_SEC"] = str(band["target_max"])
    for key, env in (("novel_name", "NOVEL_NAME"), ("book_name", "BOOK_NAME")):
        if man.get(key):
            os.environ[env] = str(man[key])
    os.environ["BOOK_INDEX"] = str(man.get("book_index") or 1)
    os.environ["NOVEL_CHAPTER"] = str(man.get("novel_chapter") or 1)
    os.environ["OUTPUT_BASE"] = str(Path(man["chapter_dir"]).parent.parent.parent)


def _ensure_send_budget_file(path: Path) -> bool:
    """恢复入口首次运行时初始化零预算文件；已存在则原样保留（跨恢复不清零）。

    根因（2026-11-16 冻结稿续验）：本入口只设置 ``STEP01_SEND_BUDGET_FILE``
    指向章节目录，却从未创建文件；``shared.send_budget.allow()`` 读不到文件
    即 fail-closed「发送预算文件不可读（拒绝发送）」，导致修复/审计在发送边界
    被拒、零 HTTP 尝试就 BLOCK。主流程会预置该文件，恢复入口是后加的路径，
    漏了这一步。仅在文件不存在时写 ``{sends:0,repairs:0,audits:0}``。

    返回 True 表示本次新建，False 表示文件已存在（未改动）。
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sends": 0, "repairs": 0, "audits": 0},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return True


def load_pending_script(man: dict) -> tuple:
    """读清单里的 `pending_candidate`（M-A2）：返回 `(script | None, note)`.

    校验顺序即失败语义：**文件存在 → 摘要相符 → 修订点相符 → 与已审稿不同**。
    最后一条是"审计只证明同 revision 稿件"的落地——同 revision 的"待复审稿"没有
    意义，只会让人拿旧结论盖章，所以直接拒。
    """
    pending = man.get("pending_candidate")
    if not isinstance(pending, dict) or not pending.get("path"):
        return None, ""
    from shared.quality_gate import compute_script_revision

    pp = Path(str(pending["path"]))
    if not pp.is_file():
        raise ResumeError("待复审稿不存在: %s" % pp)
    if pending.get("sha256") and _sha256_file(pp) != str(pending["sha256"]):
        raise ResumeError("待复审稿摘要不符（文件已被改动）: %s" % pp)
    script = _read_json(pp)
    if isinstance(script.get("script"), dict):
        script = script["script"]
    rev = compute_script_revision(script)
    if (str(pending.get("script_revision") or "")
            and rev != str(pending["script_revision"])):
        raise ResumeError("待复审稿修订点不符：实算 %s != 清单 %s"
                          % (rev[:12], str(pending["script_revision"])[:12]))
    if rev == str(man.get("script_revision") or ""):
        raise ResumeError("待复审稿与已审稿同 revision（%s）：没有待复审内容"
                          % rev[:12])
    return script, ("从待复审稿继续（revision=%s，round=%s）"
                    % (rev[:12], pending.get("round")))


def run(manifest_path, *, dry_run: bool = False, max_sends: int = 0,
        hook_max_attempts: int = 4, audit_repair_rounds: int = 1,
        no_hook: bool = False, accept_fresh_audit: bool = False) -> int:
    man = _read_json(Path(manifest_path))
    # 审计副本可以不在正式章节目录里（避免发布前改正式产物）
    os.environ["STEP01_AUDIT_SOURCE"] = str(man.get("audit_path") or "")
    # 本包 §5：跨恢复发送预算（文件在章节目录，重启不清零）。
    if not os.getenv("STEP01_SEND_BUDGET_FILE"):
        os.environ["STEP01_SEND_BUDGET_FILE"] = str(Path(man["chapter_dir"]) / "step01_send_budget.json")
    if not dry_run:
        _ensure_send_budget_file(Path(os.environ["STEP01_SEND_BUDGET_FILE"]))
    # 顺序不可交换：静态校验不得 import shared.quality_gate（它会拉起
    # shared.config，DURATION_* 从此锁死），必须先改 env 再做语义校验。
    validate_static(man)
    apply_config(man)
    validate_semantic(man)
    # 2026-11-16：冻结续验的钩子/修复范围（在 import main 之前生效）。
    os.environ["HOOK_MAX_ATTEMPTS"] = str(int(hook_max_attempts))
    if no_hook:
        os.environ["HOOK_ENABLED"] = "0"

    import step01_script_gen.main as m
    from shared.config import duration_band
    # 续验只允许“1 次修复 + 1 次最终审计”：显式覆盖 import 期常量。
    m.STEP01_AUDIT_REPAIR_MAX_ROUNDS = max(0, int(audit_repair_rounds))

    chapter = Path(man["chapter_dir"]).resolve()
    got = Path(str(m.CHAPTER_DIR)).resolve()
    if got != chapter:
        raise ResumeError("章节目录不符：main 解析为 %s，清单为 %s" % (got, chapter))
    if dict(duration_band()) != dict(man["duration_band"]):
        raise ResumeError("时长带未按清单生效：%s != %s"
                          % (duration_band(), man["duration_band"]))

    script = _read_json(Path(man["snapshot_path"]))
    if isinstance(script.get("script"), dict):
        script = script["script"]

    # M-A2（2026-11-17）：**待复审稿优先**。清单里若有 `pending_candidate`，本轮从
    # 那份**尚未审计**的修复稿继续，而不是从已审稿把修复再跑一遍。
    # 旧审计仍然只证明已审稿（`audited` 块），绝不盖到新稿上。
    _pending = man.get("pending_candidate")
    _pending_script, _pending_note = load_pending_script(man)
    if _pending_script is not None:
        script = _pending_script

    # 冻结原文与计划：恢复必须在**同一份**输入与计划上继续，而不是读"当前"的。
    # **不调用 read_chapter**：场景缓存缺失时它会调模型，dry-run 就不能保证零调用。
    novel_text = Path(man["novel_text_path"]).read_text(encoding="utf-8")
    if hashlib.sha256(novel_text.encode("utf-8")).hexdigest() != str(man["novel_sha256"]):
        raise ResumeError("原文摘要不符（冻结文本被改动，拒绝恢复）")
    plan_path = Path(man["plan_path"])
    cache = _read_json(plan_path)
    plan = cache.get("outline") if isinstance(cache, dict) else None
    if not isinstance(plan, dict):
        raise ResumeError("冻结计划文件里没有 outline（plan_path 不是大纲缓存？）")

    # M2.3 / CP1（2026-11-16）：**计划已核验**（静态 + 语义 + 冻结身份全过）——
    # 阶段边界即落盘。恢复入口从这里往下才可能花调用，落一个检查点让"卡在哪一步"
    # 变成可读数据。
    try:
        from shared import step01_checkpoint as _ckpt
        _ckpt.write(chapter, _ckpt.CP1_PLAN_VERIFIED, script=script,
                    extra={"run_id": man.get("run_id"),
                           "plan_sha256": man.get("plan_sha256"),
                           "novel_sha256": man.get("novel_sha256")})
    except Exception:                              # noqa: BLE001
        pass

    # 审计复用是"本次恢复还要不要再花一次审计"的唯一判据：按真实入口问一句。
    # S05：核的是**内容身份**（原文 + 计划 + 送审稿 + 规则/提示版本 + 时长带 + 通道），
    # 而不只是 revision/指纹——原文或计划换了也照样复用是此前的漏洞。旧审计没有
    # 身份字段时，`_reusable_final_audit` 会明确拒收（复用证据不足）。
    if _pending_note:
        # 待复审稿**不是**"审计可复用"的场景：旧审计只证明已审稿，本轮必须重审。
        # 这里不打印复用失配键——那份失配是"设计使然"，不是异常。
        reusable = None
        print(json.dumps({
            "status": "PENDING_CANDIDATE_REAUDIT", "note": _pending_note,
            "audited_revision": str(man["script_revision"])[:16],
            "pending_revision": str((_pending or {}).get("script_revision") or "")[:16],
            "next_action": man.get("next_action") or "reaudit_pending_candidate",
        }, ensure_ascii=False, indent=1))
    else:
        expected_source = m._final_audit_source_identity(
            script, novel_text, plan, man.get("channel_model"))
        reusable = m._reusable_final_audit(script, expected_source_identity=expected_source)
        if reusable is None:
            # M2.3（2026-11-16）：**代码变更后必然走到这里**。`PROMPT_SOURCE_FINGERPRINT`
            # 哈希整个 `main.py`，改一行注释就换身份——旧审计按契约确实不可复用。此前
            # 只报一句"拒绝恢复"，等于把"必须重审"和"清单坏了"混为一谈。现在：
            #   · 默认仍 fail-closed（不悄悄花一次审计）；
            #   · `--accept-fresh-audit` 显式接受"本轮重新审计"，并打印失配键。
            _miss = m.last_reuse_miss()
            if not accept_fresh_audit:
                raise ResumeError(
                    "审计不可复用（key=%s：%s）——拒绝恢复；确需在代码/输入变更后重审，"
                    "请加 `--accept-fresh-audit`"
                    % (_miss.get("key") or "unknown", _miss.get("text") or ""))
            print(json.dumps({"status": "AUDIT_WILL_BE_REFRESHED",
                              "reuse_miss": _miss}, ensure_ascii=False, indent=1))

    if dry_run:
        _rev_out = (str((_pending or {}).get("script_revision") or man["script_revision"])
                    if _pending_note else str(man["script_revision"]))
        print(json.dumps({
            "ok": True, "chapter_dir": str(chapter), "run_id": man["run_id"],
            "script_revision": _rev_out,
            # M2.3：不可复用时如实报 false（旧实现硬写 True，等于让预检表撒谎）
            "audit_reusable": reusable is not None,
            "audit_issues": len((reusable or {}).get("issues") or []),
            "reuse_miss": (m.last_reuse_miss() or {}).get("key", ""),
            "pending_candidate": (str((_pending or {}).get("path") or "")
                                  if _pending_note else ""),
            "next_action": man.get("next_action") or "",
            "novel_sha256": man["novel_sha256"][:16],
            "plan": str(plan_path.name), "plan_sha256": man["plan_sha256"][:16],
            "duration_band": man["duration_band"],
            "channel": [man["channel_provider"], man["channel_model"]],
            "next": (_pending_note or
                     ("commit（含定向修复；审计复用，不重复调用）" if reusable is not None
                      else "commit（旧审计不可复用 → 本轮重新审计 1 次）")),
        }, ensure_ascii=False, indent=1))
        return 0

    if not m._acquire_step01_lock():
        raise ResumeError("章节锁被占用（另有 Step01 在跑同一章）")
    # 2026-11-16：冻结续验的发送边界上限（总尝试 + 钩子分类尝试；0=不限）。
    _real_post = None
    _guard = None
    _prev_guard = None
    if max_sends and int(max_sends) > 0:
        import requests as _requests
        from shared.send_cap import CallCounter, SendBudgetExceeded
        from shared.outbound_guard import install as _install_guard
        _real_post = _requests.post
        # M3.2：`transport=None` ⇒ 守卫内部持有 `requests.Session`（连接池复用 +
        # 瞬态 SSL 后重建池）。守卫自身用 `session.post` 发送，不会与上面的
        # `requests.post = guard` 形成递归。
        _guard = CallCounter(None, cap=int(max_sends),
                             log_dir=chapter / "calls",
                             hook_cap=int(hook_max_attempts))
        _requests.post = _guard
        # M2.1：装进进程内安装点——`_text_gen` 的**逻辑调用层**才会登记
        # （未安装时 `begin_for_schema` 是 no-op，计数只剩尝试层）。
        _prev_guard = _install_guard(_guard.guard)
    try:
        from shared.call_ledger import set_ledger_path
        set_ledger_path(chapter / "step01_calls.jsonl")
        bank = m.CharacterBank(); bank.load()
        objects = m.SceneObjectsBank(); objects.load()
        try:
            _result, summary = m._commit_step01_outputs(
                script, novel_text,
                plan=plan, hook_ctx={"bank": bank, "objects_bank": objects},
                audit_model=man["channel_model"] or None,
                label="恢复运行", source_revision=man["script_revision"])
        except SendBudgetExceeded as exc:
            print(json.dumps({
                "status": "BLOCK", "reason": "send_budget_exhausted",
                "detail": str(exc),
                "attempts": _guard.attempts,
                "responses_received": _guard.responses_received,
                "hook_attempts": _guard.hook_attempts,
                "hook_responses": _guard.hook_responses,
                "hook_blocked": _guard.hook_blocked,
            }, ensure_ascii=False, indent=1))
            return 2
        out = {"status": summary.get("status"), "run_id": summary.get("run_id"),
               "repair_outcome": summary.get("repair_outcome"),
               "reused_audit": summary.get("reused_audit"),
               "blocking": summary.get("blocking"),
               "source_revision": man["script_revision"]}
        if _guard is not None:
            _snap = _guard.snapshot()
            out.update({"attempts": _guard.attempts,
                        "http_attempts": _snap["http_attempts"],
                        "logical_calls": _snap["logical_calls"],
                        "responses_received": _guard.responses_received,
                        "hook_attempts": _guard.hook_attempts,
                        "hook_responses": _guard.hook_responses,
                        "hook_blocked": _guard.hook_blocked})
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out["status"] == "COMMITTED" else 2
    finally:
        if _prev_guard is not None or _guard is not None:
            from shared.outbound_guard import install as _install_guard
            _install_guard(_prev_guard)
        if _real_post is not None:
            import requests as _requests
            _requests.post = _real_post
        try:
            m._release_step01_lock()
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Step01 从冻结修订点恢复")
    ap.add_argument("--manifest", required=True, help="step01_resume.json 路径")
    ap.add_argument("--dry-run", action="store_true", help="只核验身份，不调用模型")
    ap.add_argument("--max-sends", type=int, default=0,
                    help="冻结续验的发送边界总尝试上限（0=不限；含内部重试）")
    ap.add_argument("--hook-max-attempts", type=int, default=4,
                    help="钩子分类请求尝试上限（发送边界；默认 4）")
    ap.add_argument("--audit-repair-rounds", type=int, default=1,
                    help="续验的定向修复轮上限（默认 1；随后 1 次最终审计）")
    ap.add_argument("--no-hook", action="store_true",
                    help="续验跳过钩子阶段（HOOK_ENABLED=0）")
    ap.add_argument("--accept-fresh-audit", action="store_true",
                    help="允许在旧审计不可复用时继续（本轮重新审计）；默认 fail-closed")
    ap.add_argument("--build", action="store_true",
                    help="由现有产物生成清单（需 --snapshot）")
    ap.add_argument("--chapter-dir", default="", help="--build 时的章节目录")
    ap.add_argument("--snapshot", default="", help="--build 时显式指定快照文件")
    ap.add_argument("--audit", default="", help="--build 时显式指定配对审计文件")
    ap.add_argument("--plan", default="", help="--build 时显式指定冻结计划文件")
    ap.add_argument("--novel-text", default="",
                    help="--build 时显式指定**已提取**的章节文本文件（不触发场景分析）")
    args = ap.parse_args(list(argv or sys.argv[1:]))
    try:
        if args.build:
            man = build_manifest(args.chapter_dir or os.getcwd(),
                                 snapshot_path=args.snapshot,
                                 audit_path=(args.audit or None),
                                 novel_text_path=args.novel_text,
                                 plan_path=(args.plan or None))
            out = Path(args.manifest)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
            print(str(out))
            return 0
        return run(args.manifest, dry_run=args.dry_run,
                   max_sends=args.max_sends,
                   hook_max_attempts=args.hook_max_attempts,
                   audit_repair_rounds=args.audit_repair_rounds,
                   accept_fresh_audit=args.accept_fresh_audit,
                   no_hook=args.no_hook)
    except ResumeError as exc:
        print("[RESUME] 拒绝恢复: %s" % exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())



