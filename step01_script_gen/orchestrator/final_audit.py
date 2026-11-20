# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.final_audit —— 终审、审计复用、诊断台账、冻结预算续验.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import os
from pathlib import Path
import json

from step01_script_gen.orchestrator import runtime as _mrt
from step01_script_gen.orchestrator import repair_apply as _mra
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _audit_blocking_records(payload: "dict | None") -> list:
    """审计 payload 里的 blocking 条目（与 `require_semantic_gate` 同口径）."""
    return [
        issue for issue in ((payload or {}).get("issues") or [])
        if isinstance(issue, dict)
        and str(issue.get("severity") or "").strip() in ("hard", "visual_risk", "blocking")
    ]


def _audit_issue_allowed_fields(issue: dict) -> list[str]:
    """审计条目声明的可执行目标字段（field_path + repair_targets 的并集）."""
    out: list[str] = []
    fp = str(issue.get("field_path") or "").strip()
    if fp:
        out.append(fp)
    for target in issue.get("repair_targets") or []:
        if isinstance(target, dict):
            path = str(target.get("field_path") or "").strip()
            if path:
                out.append(path)
    return out


def _shot_semantic_digest(shot: dict) -> str:
    """镜头内容摘要（排除 revision/hash 等运行字段），用于增量审计作用域."""
    import hashlib as _hashlib
    import json as _json
    fields = ("ff_desc", "motion_desc", "lf_desc", "prompt", "camera_movement",
              "keyframes", "frame_layouts", "narration_segment", "subtitle",
              "duration_sec", "scene", "location", "main_subject", "plan_refs")
    payload = {k: shot.get(k) for k in fields}
    blob = _json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return _hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _baseline_chapter_dir():
    """增量审计作用域的**只读基线章目录**.

    默认 = 当前章目录（普通生产提交：基线就是上一次 COMMITTED 的稿子）。
    隔离审计（`tools/audit_frozen_candidate.py`）在候选根里没有 COMMITTED 基线，
    由包内 `baseline_chapter_dir` 或 `STEP01_BASELINE_CHAPTER_DIR` **显式声明**
    生产章目录，只读加载后计算 `_active_audit_scope`。

    只认显式声明：普通 pytest 单元测试/无声明运行绝不读取现场产物，避免测试
    依赖开发机上的 `.tmp`/`output`（AGENTS.md「内容无关性与夹具」）。
    """
    raw = (os.getenv("STEP01_BASELINE_CHAPTER_DIR") or "").strip()
    if raw:
        return Path(raw)
    return _mrt.CHAPTER_DIR


def _committed_baseline_script():
    """读取基线章目录里已 COMMITTED 的稿；不可用/未验证时返回 None."""
    import json as _json
    from shared.quality_gate import verify_step01_commit_manifest
    base_dir = _baseline_chapter_dir()
    path = base_dir / "script_result.json"
    manifest_path = base_dir / "step01_commit.json"
    if not path.exists() or not manifest_path.exists():
        return None
    try:
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(manifest.get("status") or "") != "COMMITTED":
            return None
        script = _json.loads(path.read_text(encoding="utf-8"))
        verify_step01_commit_manifest(base_dir, script, stage="step01",
                                      expected_chapter=_mrt.NOVEL_CHAPTER)
        return script
    except Exception:
        return None


def _changed_shot_ids(script: dict, baseline: dict) -> set:
    base = {str(s.get("index")): s for s in (baseline.get("shots") or [])
            if isinstance(s, dict)}
    cand = {str(s.get("index")): s for s in (script.get("shots") or [])
            if isinstance(s, dict)}
    out = set()
    for sid in set(base) | set(cand):
        if sid not in base or sid not in cand:
            out.add(sid)
        elif _shot_semantic_digest(base[sid]) != _shot_semantic_digest(cand[sid]):
            out.add(sid)
    return out


def _downgrade_out_of_delta_audit_issues(audit, active_scope, *,
                                         contracts_ok: bool) -> int:
    """局部修订时，把完全落在未改动镜头上的新增审计 blocking 降为 warning.

    确定性质量门 _q_blocking 不受此函数影响；只有 LLM 审计条目、且其全部
    目标镜头都在 active_scope 之外时才降级，避免未改动镜的采样波动阻断局部提交。
    """
    if not contracts_ok or not active_scope or not isinstance(audit, dict):
        return 0
    changed = 0
    for issue in audit.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("severity") or "").strip() not in ("hard", "visual_risk", "blocking"):
            continue
        ids = {str(t.get("shot_id") or "").strip()
               for t in (issue.get("repair_targets") or []) if isinstance(t, dict)}
        ids.discard("")
        if not ids:
            sid = str(issue.get("shot_id") or "").strip()
            if sid:
                ids.add(sid)
        if ids and not (ids & active_scope):
            issue["severity"] = "warning"
            issue["out_of_delta_scope"] = True
            changed += 1
            _mrt._logger.warn(
                "[COMMIT] 审计项 %s@shot%s 落在未改动镜头（增量作用域 %s），降级为 warning"
                % (issue.get("code"), issue.get("shot_id"),
                   ",".join(sorted(active_scope))))
    return changed


def _downgrade_stage_enum_false_positives(audit: "dict | None", script: dict, *,
                                            contracts_ok: bool) -> int:
    """把「已合法 stage 却要求改成 process/result」的审计 blocking 降为 warning.

    `shared/kf_time.KF_STAGE_ENUM` 是 stage 的唯一权威枚举；审计模板若不声明它，
    模型会把合法值误判为非法。仅当一条 blocking 的全部目标都是
    `keyframes[N].stage`、当前值均在枚举内、且审计文本要求 process/result 时才降级。
    """
    import re as _re
    from shared.kf_time import KF_STAGE_ENUM
    if not contracts_ok or not isinstance(audit, dict):
        return 0
    by_idx = {str(s.get("index")): s for s in (script.get("shots") or [])
              if isinstance(s, dict)}
    stage_re = _re.compile(r"^keyframes\[(\d+)\]\.stage$")
    changed = 0
    for issue in audit.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("severity") or "").strip() not in ("hard", "visual_risk", "blocking"):
            continue
        targets = []
        for target in issue.get("repair_targets") or []:
            if isinstance(target, dict):
                targets.append((str(target.get("shot_id") or issue.get("shot_id") or ""),
                                str(target.get("field_path") or "")))
        if not targets:
            targets = [(str(issue.get("shot_id") or ""),
                        str(issue.get("field_path") or ""))]
        if not targets or any(not stage_re.match(path) for _sid, path in targets):
            continue
        text = " ".join(str(issue.get(k) or "") for k in
                        ("message", "minimal_fix", "evidence", "impact"))
        if not any(tok in text.lower() for tok in ("process", "result", "非法", "不合法")):
            continue
        legal = True
        for sid, path in targets:
            shot = by_idx.get(sid)
            match = stage_re.match(path)
            if not shot or not match:
                legal = False
                break
            kfs = shot.get("keyframes") or []
            idx = int(match.group(1))
            stage = str((kfs[idx] or {}).get("stage") or "").strip() if idx < len(kfs) else ""
            if stage not in KF_STAGE_ENUM:
                legal = False
                break
        if legal:
            issue["severity"] = "warning"
            issue["_downgraded_stage_enum"] = True
            changed += 1
            _mrt._logger.warn(
                "[COMMIT] 审计项 %s@shot%s 把合法 stage 枚举误判为非法，降级为 warning"
                % (issue.get("code"), issue.get("shot_id")))
    return changed


def _downgrade_locked_only_audit_issues(audit: "dict | None", *,
                                        contracts_ok: bool) -> int:
    """把仅指向已过契约门的锁定字段的 blocking 审计项降为 warning.

    前置台词完整性门与事件履约契约门已通过时，`narration_segment` / `subtitle`
    等轮级锁定字段不可能再被修复轮修改（P1-2 的 `_split_executable_allowed`）。
    若审计仍把它列为 blocking 且没有任何可执行画面/结构字段，保留 blocking 会
    形成「改则破前置门、不改则 0 轮修复必 BLOCK」的三向死锁。此处只降级这类
    **零可执行目标**条目；仍有可执行字段的条目原样保留 blocking。
    """
    if not contracts_ok or not isinstance(audit, dict):
        return 0
    changed = 0
    for issue in audit.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("severity") or "").strip() not in ("hard", "visual_risk", "blocking"):
            continue
        executable, locked = _mra._split_executable_allowed(_audit_issue_allowed_fields(issue))
        if locked and not executable:
            issue["severity"] = "warning"
            issue["_downgraded_locked_only"] = True
            changed += 1
            _mrt._logger.warn(
                "[COMMIT] 审计项 %s@shot%s 仅指向已过契约门的锁定字段 %s，降级为 warning"
                % (issue.get("code"), issue.get("shot_id"), ",".join(locked)))
    return changed


#: 单条审计问题的成对目标**最多覆盖的镜头数** = 联动组上限（`_REPAIR_MAX_SHOTS_PER_GROUP`）。
#: 定义在这里而不是从 audit.py 反向 import：本文件是消费方，且审计侧只负责**声明**，
#: 上限属于修复侧的容量契约。两侧同值由 `test_audit_oversize_target_split.py` 锁定。
_AUDIT_MAX_TARGET_SHOTS = 6


def _split_oversize_repair_targets(rt, *, cap: int = _AUDIT_MAX_TARGET_SHOTS) -> list:
    """把成对目标按**镜头**分块（每块 ≤ `cap` 镜，块内保留该镜的全部目标）.

    v6.143（2026-09-27 十次连跑根因 P2）：实测 4/10 次 BLOCK 的直接原因是
    **单条审计问题**声明了 7/8/10/12/14 个镜头的成对目标，而修复侧联动组上限是 6
    且**拒绝拆组**（拆组=半条修复）→ `_linked_repair_groups` 判 `oversize` →
    不调 LLM、不再发审计，直接 BLOCK。

    取证（`docs/step01-ten-run-problem-report-2026-09-27.md` P2）：逐轮扫描审计台账里
    `repair_targets` 覆盖镜头数 > 6 的条目，`run 02/04/06/10` 四次**全部是单条声明**
    （7/7+12/14/8+10 镜），没有一次是多条合并出来的组。

    拆的**只是「一条问题的声明范围」**，不是「联动事务」：
      - 同一镜头内的多条目标**始终留在同一块里** → 镜内原子性（帧+布局的联动修）
        完全不变；
      - 跨镜目标本来就该按契约「每一镜各写一条」声明（见 `audit._AUDIT_RULES_TEMPLATE`
        的 repair_targets 段），模型写成一条时，拆条正是把它还原成契约形状；
      - 块与块之间是**独立**的可修单元（systemic 缺陷如「stage 取值非法遍布全篇」
        本来就逐镜独立），所以不存在「半条修复」。

    Returns:
        `[chunk, ...]`；`rt` 为 `None`（旧条目）/ `[]`（显式无目标）/ 块数 ≤ 1 时
        **原样**返回单元素列表——三态语义不在本函数里改变。
    """
    if not isinstance(rt, list) or not rt:
        return [rt]
    _by_shot: dict = {}
    for _t in rt:
        if not isinstance(_t, dict):
            continue
        _sid = str(_t.get("shot_id") or "").strip()
        _by_shot.setdefault(_sid, []).append(_t)
    if len(_by_shot) <= cap:
        return [rt]
    _keys = list(_by_shot)
    out: list = []
    for _i in range(0, len(_keys), cap):
        _chunk: list = []
        for _k in _keys[_i:_i + cap]:
            _chunk.extend(_by_shot[_k])
        out.append(_chunk)
    return out


def _audit_payload_to_issues(payload: "dict | None") -> list:
    """把审计 payload 里的 blocking 条目还原成 `Issue`（供受限修复轮消费）.

    v6.119 审核实测的**断点**：本函数此前**不传** `repair_targets`——模型就算正确输出
    了成对目标，进到修复轮时也已经丢了，授权重新退回旧的单字段 `field_path` 推导。
    整条成对授权链在生产提交路径上是**死的**（离线单测覆盖不到，因为它直接构造
    `Issue` 而不过这一跳）。

    `repair_targets` 的三态必须原样带走（`None`=旧条目 / `[]`=显式无目标 /
    非空=成对），**不能**用 `or []` 把前两者合并——那正是上一条要修的静默降级。

    v6.143（P2）：**单条声明超限（> `_AUDIT_MAX_TARGET_SHOTS` 镜）时按镜拆成多条
    Issue**（见 `_split_oversize_repair_targets`）。这里是审计 payload → `Issue` 的
    唯一转换点，也是联动组分组（`_linked_repair_groups`）的唯一输入源；不在这一跳拆，
    超限声明就会带着 7-14 个镜头进分组、被判 `oversize` 后整轮 BLOCK。
    """
    out: list = []
    _split_n = 0
    for rec in _audit_blocking_records(payload):
        _rt = rec.get("repair_targets")
        if _rt is not None and not isinstance(_rt, list):
            _rt = []          # 形状非法 → 按「显式无目标」处理（fail-closed）
        _chunks = _split_oversize_repair_targets(_rt)
        if len(_chunks) > 1:
            _split_n += len(_chunks) - 1
            _mrt._logger.warn(
                "[COMMIT] 审计问题 %s 的成对目标覆盖 %d 镜 > 上限 %d → 按镜拆成 %d 条"
                "（镜内目标保持整组；这是声明粒度，不是拆联动事务）"
                % (rec.get("code"), len({str(t.get("shot_id")) for t in _rt
                                        if isinstance(t, dict)}),
                   _AUDIT_MAX_TARGET_SHOTS, len(_chunks)))
        for _chunk in _chunks:
            out.append(_mrt.Issue(
                code=str(rec.get("code") or "model_audit_issue"),
                severity="visual_risk",
                message=str(rec.get("message") or ""),
                shot_id=rec.get("shot_id"),
                frame_id=str(rec.get("frame_id") or ""),
                entity_id=str(rec.get("entity_id") or ""),
                field_path=str(rec.get("field_path") or ""),
                evidence=str(rec.get("evidence") or ""),
                repair_targets=_chunk,
                repairable=True,
            ))
    if _split_n:
        _mrt._metrics_inc("commit.audit_targets_split", _split_n)
    return out


def _audit_payload_to_relations(payload: "dict | None") -> dict:
    """审计 payload → `{表层称谓: identifier}`（§14 P2 的 `characters[].aliases` 窄协议）.

    只做**形状归一**，不做语义扩展：

      · 缺 `character_aliases`（旧响应）⇒ 空表 = **未声明任何身份关系**。消费者
        （`repair_effects.resolve_frame_actor_binding`）据此判 `ambiguous`，
        **不得**当成「是另一个实体」——「没写」与「写了没关系」是两件事。
      · `identifier` / `aliases` 缺失或类型不对 ⇒ **整条丢弃**（不猜意图）；
      · 同一表层写法被多条声明指向不同 identifier ⇒ **互相矛盾**，该写法整条丢弃
        （矛盾的关系比没有关系更危险：拿它裁决会静默选边）。丢弃次数打日志。
      · 表层写法等于 identifier 自身 ⇒ 无信息，丢弃。

    代码只**派生编号**（这里是「写法 → 身份」的映射），关系本身由生产者给。
    """
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("character_aliases")
    if not isinstance(raw, list):
        return {}
    out: dict = {}
    conflicts: list = []
    blocked: set = set()
    dropped = 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        ident = str(item.get("identifier") or "").strip()
        aliases = item.get("aliases")
        if not ident or not isinstance(aliases, list):
            dropped += 1
            continue
        for a in aliases:
            if not isinstance(a, str):
                dropped += 1
                continue
            name = a.strip()
            if not name or name == ident:
                dropped += 1
                continue
            if name in blocked:
                continue
            if name in out and out[name] != ident:
                conflicts.append((name, out[name], ident))
                out.pop(name, None)
                blocked.add(name)
                continue
            out[name] = ident
    if dropped:
        _mrt._logger.info("[AUDIT] 身份关系声明丢弃 %d 项（形状不合法）" % dropped)
    if conflicts:
        _mrt._logger.warn("[AUDIT] 身份关系声明**互相矛盾**！同一个称谓被指向不同角色，"
                     "已整条丢弃（不猜）：%s" % conflicts[:4])
    return out


def _target_of_record(_rec) -> str:
    """审计条目（dict 或 `Issue`）→ 事务 target 名（`shot_5` / `hook`）.

    **唯一**口径在 `repair_fields._target_id_of`。这里此前是第二份实现
    （`f"shot_{_ai.shot_id}"`）——2026-09-25 实测的 BLOCK 正好是两份实现
    「改一处漏一处」的代价：模型第 2 轮把 `shot_id` 写成 `"shot5"`，归一必须
    同时覆盖**成对目标**与**问题定位**两处，任何一处漏掉都会让授权/落点错位。
    """
    from step01_script_gen.repair_fields import _target_id_of
    _sid = (_rec.get("shot_id") if isinstance(_rec, dict)
            else getattr(_rec, "shot_id", None))
    return _target_id_of(_sid)


def _audit_repair_target_problems(audit: "dict | None", shots_by_id: dict) -> list:
    """审计 blocking 条目的**成对目标契约**是否可执行（v6.119，用户口径）.

    三类都算「不可执行」：

      ① 缺 `repair_targets` —— 契约未遵守（schema 里它是 required）；
      ② 显式 `[]` —— 该 blocking 问题没有可授权的定向修复；
      ③ 任一条目标无效（镜头不存在 / 路径不是该镜的真实字段）—— **整条**拒绝，
         不允许留下半组继续修。

    只查 blocking 条目：advisory/info 不驱动定向修复，不构成阻塞。

    返回可读原因（空列表 = 全部条目契约可执行）。

    **v6.142（09-26 用户决定，P1-3）：调用方不再据此就地 BLOCK**。旧口径
    「有一条不可执行就整轮 BLOCK、一轮修复都不发」被 20:25 run 证伪——一条坏
    目标（shot 3 的 12 条越界路径）把同轮其它可修目标全部连坐。现在调用方只
    **告警 + 保留 blocking**，本轮不授权这条、其余目标照常修；改不动的终局由
    「提交前修复轮零改善」判据给出。所以本函数是**诊断**，不是**判决**。
    """
    from step01_script_gen.repair_fields import (validate_repair_targets,
                                                 _is_structural_code)
    out: list = []
    for rec in _audit_blocking_records(audit):
        code = str(rec.get("code") or "model_audit_issue")
        tag = f"shot={rec.get('shot_id')} code={code}"
        if "repair_targets" not in rec:
            out.append(f"{tag}：缺 `repair_targets`（schema required）——审计未按契约输出")
            continue
        declared = rec.get("repair_targets")
        if not isinstance(declared, list) or not declared:
            out.append(f"{tag}：`repair_targets` 为空——该 blocking 问题"
                       f"没有可授权的定向修复目标")
            continue
        _valid, rejected = validate_repair_targets(
            declared, shots_by_id, structural=_is_structural_code(code))
        if rejected:
            out.append(f"{tag}：{len(rejected)} 条目标无效（{rejected[:3]}）——整条拒绝")
    return out


def _terminal_blocking_records(audit: "dict | None",
                              blocking: list) -> tuple:
    """终局 BLOCK 的 manifest 记录：审计**调用失败**时丢弃陈旧清单.

    v6.152（C1，2026-09-29 五次跑实测）：终局若是审计调用失败（超时 / 传输耗尽 /
    解析耗尽），`blocking` 还停在**上一次成功审计那一轮**的清单上——run 02 的
    manifest 因此报 22 条、run 04 报 6 条，而真因只是「这次审计没跑出来」。
    调用失败 **不等于** 稿件有这些语义缺陷，把它当成 blocking 清单会同时误导读数
    （下游按 code 统计）与污染欠账台账。

    返回 `(blocking_for_manifest, audit_status, audit_call_failed)`：

    - 审计跑出结论（`status == "ok"`）或本 run **根本没调用审计**
      （台词完整性门直接判定 → `audit is None`）：原样返回，`audit_call_failed=False`；
    - 审计调用失败：返回**只含本次失败**的一条记录，并置 `audit_call_failed=True`，
      由调用方决定是否把原清单降级为诊断字段、以及**跳过欠账登记**。
    """
    _status = (str((audit or {}).get("status") or "").strip().lower()
               if isinstance(audit, dict) else "")
    if not audit or _status == "ok":
        return blocking, _status, False
    return ([{
        "code": "audit_call_failed",
        "severity": "blocking",
        "shot_id": None,
        "transient": True,
        "message": ("final semantic audit %s（调用失败 != 0 issue；"
                    "本条为本次终局原因，不是稿件缺陷清单）" % _status),
    }], _status, True)


def _quality_blocking_records(quality: "dict | None") -> list:
    """确定性质量门里的 blocking 条目（与 `require_gate` 同口径）.

    v6.103（第二十一次重跑实测）：提交循环此前**只看审计 blocking**，确定性
    quality 为 BLOCK 时既不进修复轮、也不写进 manifest —— 结果一条 9 条阻塞的
    稿子一路走到 staged 自验才被拦下，`blocking_count` 还报 0，原因写成
    「staged 自验失败」。「质量门阻塞」和「审计阻塞」都是 blocking，必须在
    同一处判定、同一处记账（否则日志与 manifest 都在误导）。
    """
    q = quality or {}
    records = [r for r in (q.get("hard") or []) if isinstance(r, dict)]
    records += [r for r in (q.get("visual_risk") or []) if isinstance(r, dict)]
    if q.get("checker_failed"):
        records.append({"code": "CHECK_FAILED", "severity": "hard",
                        "shot_id": None, "message": "质量检查器异常"})
    return records


# 定向审查项的**模板**（v6.111，审核 P2-5）：`_final_audit_focus` 从这里取文案，
# 版本哈希随之自动跟随——手写「版本号记得改」是迟早会忘的约定。
_AUDIT_FOCUS_TEMPLATES: tuple = (
    "逐镜检查上一镜 LF → 本镜 FF → 全部 KF → 本镜 LF → 下一镜 FF 的状态链；"
    "最多返回 20 条，证据简洁。",
    "检查武器/道具释放后是否持续释放，直到明确重新获得。",
    "检查是否存在动作重复、遗漏、未来状态提前或无事件依据的动作补充。",
    "未决项必须保留并给出原文证据，不得为了通过门禁而删除证据。",
)


def _audit_focus_version() -> str:
    """final audit 定向审查项的版本（模板 + 动态段拼装规则）."""
    import hashlib
    import json as _json
    blob = _json.dumps(
        {"templates": list(_AUDIT_FOCUS_TEMPLATES),
         "dynamic": "quality.visual_risk 镜头号清单（本节内容随本轮 blocking 变化）",
         "limit": 8},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _final_audit_focus(script: dict, quality: "dict | None") -> list:
    """final audit 的定向审查项.

    **不写死任何镜头号**（换小说代码不用改）：通用状态链检查 + 从本轮确定性
    blocking 里动态取出的「重点镜头」。
    """
    focus = list(_AUDIT_FOCUS_TEMPLATES)
    performance = [str(rec.get("shot_id"))
                   for rec in (quality or {}).get("advisory") or []
                   if rec.get("code") == "emotion_visualization"]
    if performance:
        focus.append(
            "表演语义复核（镜头 %s）：词表未命中示例动作仅是候选，不能据此判错。"
            "区分外貌/发式修饰与当前表演；按人物及对应帧核对是否已有具体可见的"
            "表情或动作，不要求固定措辞。确实只有抽象表演而无可执行画面时，"
            "按实际影响报告问题、原字段证据及成对修复目标。"
            % "、".join(dict.fromkeys(performance)))
    ids: list = []
    for rec in (quality or {}).get("visual_risk") or []:
        sid = rec.get("shot_id")
        if sid is None:
            continue
        key = "hook" if str(sid) in ("0", "hook") else str(sid)
        if key not in ids:
            ids.append(key)
    if ids:
        focus.append(
            "重点核对镜头 %s 的跨字段一致性（在场状态 / 道具持有 / 帧阶段 / "
            "结果动作支撑）。" % "、".join(ids[:8])
        )
    return focus


def _final_audit_metadata(script: dict, *, run_id: str = "") -> dict:
    """final audit 的审查元数据（**唯一源**）.

    请求构造（`_run_final_semantic_audit`）与**请求身份**（`_final_audit_identity`）
    必须用同一份——元数据会**逐字进提示**，两边各写一份就会让"身份相同"与
    "请求逐字相同"分家，而复用判据的全部依据就是后者。
    """
    from shared.quality_gate import compute_script_revision
    from step01_script_gen.audit import AUDIT_TEMPERATURE, audit_prompt_version

    return {
        "input_revision": compute_script_revision(script),
        "rule_version": _mrt.RULE_VERSION,
        "rule_source_fingerprint": _mrt.RULE_SOURCE_FINGERPRINT,
        "duration_band": _mrt.duration_band(),
        # v6.112：生成/修复提示词版本——**诊断字段**，不参与复用判定
        # （改提示词改变的是稿件内容，内容变化已由 revision 覆盖）。
        "prompt_source_fingerprint": _mrt.PROMPT_SOURCE_FINGERPRINT,
        "audit_scope": _mrt.STEP01_AUDIT_SCOPE,
        # v6.111（审核 P2-5 / 总计划 §1.3）：模型、温度与 prompt 版本都要进元数据，
        # 并参与复用判定——只比 revision + 规则指纹 + scope 时，改了审计提示词
        # 旧结论仍会被复用。
        "audit_prompt_version": audit_prompt_version(),
        "audit_focus_version": _audit_focus_version(),
        "audit_temperature": AUDIT_TEMPERATURE,
        "run_id": run_id,
    }


def _final_audit_identity(script: dict, novel_text: str, plan: "dict | None",
                          focus: "list | None", model: "str | None",
                          run_id: str) -> str:
    """本次即将发出的审计请求的身份（见 `audit.final_audit_request_identity`）."""
    from step01_script_gen.audit import final_audit_request_identity

    return final_audit_request_identity(
        novel_text, plan, script, audit_focus=focus,
        metadata=_final_audit_metadata(script, run_id=run_id), model=model)


def _final_audit_source_identity(script: dict, novel_text: str, plan: "dict | None",
                                 model: "str | None" = None) -> str:
    """**来源身份**：不含定向项（focus）的内容身份，供恢复入口在**不重建质量门**时核对.

    为什么需要第二把尺子（S05，2026-11-11）：完整请求身份包含 `audit_focus`，而
    focus 由当轮确定性质量门派生——恢复入口 dry-run 不允许重跑质量门（那会拉起
    配置与检查器），于是它算不出完整身份，只能退回「revision + 指纹」这几条弱判据，
    **原文或计划换了也照样复用**。来源身份覆盖「原文 + 计划 + 送审稿 + 规则/提示版本
    + 时长带 + 通道/模型」，这些在恢复时全都可以从冻结清单与快照重算。
    """
    import hashlib as _hashlib
    import json as _json
    from shared.quality_gate import compute_script_revision
    from step01_script_gen.audit import audit_prompt_version, resolve_audit_channel

    _provider, _chan_model = resolve_audit_channel()
    blob = {
        "novel_sha256": _hashlib.sha256((novel_text or "").encode("utf-8")).hexdigest(),
        "plan": plan or {},
        "script_revision": compute_script_revision(script),
        "rule_source_fingerprint": _mrt.RULE_SOURCE_FINGERPRINT,
        "prompt_source_fingerprint": _mrt.PROMPT_SOURCE_FINGERPRINT,
        "audit_scope": _mrt.STEP01_AUDIT_SCOPE,
        "audit_prompt_version": audit_prompt_version(),
        "duration_band": _mrt.duration_band(),
        "channel_provider": _provider or "",
        "model": _chan_model or model or "",
    }
    return _hashlib.sha256(
        _json.dumps(blob, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _memoizable_final_audit(audit: "dict | None") -> "dict | None":
    """可进「循环内复用」memo 的审计结论——**只有真的跑出结论的**才返回原对象.

    调用失败（超时 / 请求失败 / 解析失败 / 预算耗尽）一律返回 `None`：失败**不能**
    冒充可复用结论（复核之十九 P1 "失败审计不能冒充可复用结论"）。抽成独立函数是
    为了让这条判据**可被直接断言**——写在循环里就只能靠"构造一个走不到的路径"来测。
    """
    if not isinstance(audit, dict):
        return None
    if str(audit.get("status") or "").strip().lower() != "ok":
        return None
    if not _mrt.duration_band_matches(audit):
        return None
    return audit


def _run_final_semantic_audit(script: dict, novel_text: str, plan: "dict | None", *,
                              model: "str | None" = None, focus: "list | None" = None,
                              run_id: str = "") -> dict:
    """对**当前不可变对象**跑一次 final semantic audit，返回完整 payload.

    契约（总计划 §4.3）：`status / model / error / issues / input_revision /
    rule_source_fingerprint / audit_scope` 必需；`created_at` / `run_id` 为诊断
    字段，不参与准入语义。
    """
    from step01_script_gen.audit import run_same_model_audit_with_status

    metadata = _final_audit_metadata(script, run_id=run_id)
    # M4（2026-11-18）：严格模式下先跑送审前确定性预审门；发现 hard 时**先经
    # 已有 run_hard_fix 做一次确定性局部自愈**（不调 LLM），再按三态决定：
    #   healed            → 只把修复稿交还调用方（本函数不写盘、不采纳）；
    #   needs_llm_repair  → 保持 blocked 原语义，交既有修复轮，不放宽判据；
    #   unresolved        → 保持 blocked 原语义，给出可读原因。
    from step01_script_gen.commit_gate import (heal_pre_audit_hard,
                                               pre_audit_enabled)
    if pre_audit_enabled():
        _heal = heal_pre_audit_hard(script)
        _heal_status = str(_heal.get("status") or "")
        if _heal_status == "healed":
            _mrt._logger.warn(
                "[AUDIT] 送审前确定性预审门：局部自愈成功（hard %d→0，repairs=%d）"
                "——修复稿交由调用方决定是否送审，本函数不写盘、不采纳"
                % (len(_heal.get("hard_before") or []),
                   len(_heal.get("repairs") or [])))
            return {
                **metadata,
                "status": "pre_audit_healed",
                "audit_status": "pre_audit_healed",
                "model": model or "",
                "channel_provider": "",
                "schema_deviations": [],
                "usage": None,
                "error": "",
                "issues": list(_heal.get("hard_before") or []),
                "healed_script": _heal.get("script"),
                "healed_report": {
                    "repairs": _heal.get("repairs") or [],
                    "hard_before": len(_heal.get("hard_before") or []),
                    "hard_after": len(_heal.get("hard_after") or []),
                },
            }
        if _heal_status in ("needs_llm_repair", "unresolved"):
            _hard_left = list(_heal.get("hard_after")
                              or _heal.get("hard_before") or [])
            _mrt._logger.warn(
                "[AUDIT] 送审前确定性预审门拒绝：status=%s（%s）"
                % (_heal_status, _heal.get("reason") or ""))
            return {
                **metadata,
                "status": "blocked",
                "audit_status": "blocked",
                "model": model or "",
                "channel_provider": "",
                "schema_deviations": [],
                "usage": None,
                # 保留既有 `pre_audit_hard` 前缀（原入口判据不变），三态细节
                # 放 `pre_audit_status` / `reason`，消费方可区分「需修复轮」与
                # 「无法判定」。
                "error": "pre_audit_hard: %d deterministic finding(s); "
                         "status=%s; %s"
                         % (len(_hard_left), _heal_status,
                            _heal.get("reason") or ""),
                "issues": _hard_left,
                "pre_audit_status": _heal_status,
            }
        # clean → 继续原有终审请求（strict 模式下 hard 已被确定性层消掉）
    # 定向项只解析一次：请求里发的那一份、身份里算的那一份必须是同一份，
    # 否则「身份相同」与「请求逐字相同」会分家（S05）。
    _focus_now = focus or _final_audit_focus(script, None)
    _mrt._metrics_inc("calls.final_audit")
    _allow, _deadline, _to_info = _mrt._audit_budget_allowance()
    _mrt._logger.info("[AUDIT] 本次审计可用 %ss（%s）｜总预算剩余 %s 秒｜截止时刻 %s"
                 % (_allow, _to_info.get("why") or "-",
                    _to_info.get("left") if _to_info.get("left") is not None else "未知",
                    ("不限" if _deadline is None else "now+%ss" % _allow)))
    result = run_same_model_audit_with_status(
        novel_text, plan, script, model=model,
        timeout_sec=int(_to_info.get("cap") or _mrt.AUDIT_TIMEOUT_CAP_SEC),
        deadline_ts=_deadline,
        audit_focus=_focus_now,
        metadata=metadata,
    )
    # M04：四项读数分别记（剩余预算 / 审计允许时长 / 服务端等待 / 流式耗时）。
    # 只进 metrics 与日志，**不改 payload 形状**（单次成功产物与历史逐字一致）。
    _budget_timing = _mrt._audit_budget_timing(
        result, allowance_sec=_allow,
        left_sec=_to_info.get("left"), phase="final_audit")
    _mrt._metrics_set("audit.budget_timing", _budget_timing)
    _mrt._logger.info(
        "[AUDIT][BUDGET] 剩余预算=%s｜审计允许=%ss｜服务端等待=%ss｜"
        "流式耗时=%s（来源=%s）"
        % (_budget_timing["remaining_budget_sec"],
           _budget_timing["audit_allowance_sec"],
           _budget_timing["server_wait_sec"],
           _budget_timing["stream_elapsed_sec"],
           _budget_timing["stream_timing_source"]))
    # v6.134：parse_failed 有界重试后的**真实**调用次数（audit.AuditResult.attempts）。
    # 仅 ≠1 时进产物——单次成功的 payload 形状与历史完全一致（同 raw_text_head 口径）。
    _attempts = int(getattr(result, "attempts", 1) or 1)
    # v6.117（方案 A）：审计可能走**专用**通道（`AUDIT_TEXT_PROVIDER`），此时调用方
    # 传来的 `model` 是生成侧的名字、并非实际提交的模型。产物与台账必须记**实际**值，
    # 否则「这份审计由哪个通道/模型产出」在事后无从核对。
    _deviations = [str(d) for d in (getattr(result, "schema_deviations", None) or [])]
    if _deviations:
        _mrt._logger.warn("[AUDIT] 响应体与 AUDIT_SCHEMA 有偏离（已归一，结论仍可用）: "
                     + "；".join(_deviations))
    payload = {
        **metadata,
        "status": result.status,
        # S05（2026-11-11）：同一事实用**同一个名字**贯穿三处（审计 payload / 提交摘要 /
        # 预算收据）。`status` 保留给既有消费者；`audit_status` 是贯通字段。
        "audit_status": result.status,
        "model": getattr(result, "channel_model", "") or model,
        "channel_provider": getattr(result, "channel_provider", "") or "",
        "schema_deviations": _deviations,
        # v6.118：provider 自报用量原样落盘（取不到 = None，**不填估算**）
        "usage": getattr(result, "usage", None),
        "error": result.error,
        "issues": [i.to_dict() for i in result.issues],
        # §14 P2（2026-10-03）：**身份关系声明**（`characters[].aliases` 窄协议）。
        # 审计说「某称谓与某已在场人物是重复实体」时必须把关系写出来，否则修复端
        # 只剩「照做」或「猜」两条路——实测第 21 轮 ch2 就是「模型删对了、约束又加
        # 回来、却被记成没修好」。空表 = 未声明（旧响应缺该键的兼容读法）。
        "character_aliases": list(getattr(result, "character_aliases", None) or []),
        "created_at": _mrt._step01_now(),
        # v6.133（2026-09-26 实跑 BLOCK：final audit parse_failed，00:58 那次重跑）：
        # 失败时**必须**留下原始响应头——`AuditResult.raw_text` 本来带着原文，但原先
        # 没进 payload，台账里只剩一句 "not valid JSON or lacks issues[]"，于是
        # 「根本不是 JSON」与「是 JSON 但形状不符」这两种**处置完全不同的**故障
        # 事后无从区分（前者要改通道/提示词，后者要改 shape 判据）。
        # 同"修复调用失败"的同一标准：失败原因进结构化诊断，而不是只活在 step01.log。
        **({"raw_text_head": str(getattr(result, "raw_text", "") or "")[:_DIAG_RAW_HEAD_MAX],
            "raw_text_len": len(str(getattr(result, "raw_text", "") or ""))}
           if result.status != "ok" else {}),
        # v6.134：重试发生时记录真实调用次数（≠1 才出现，单次调用产物形状不变）
        **({"attempts": _attempts} if _attempts != 1 else {}),
        # v6.134.1（审核）：被重试覆盖的失败尝试留档（坏响应头部/总长/该次用量）——
        # 仅非空时出现；「重试后成功」的 payload 不再丢失坏响应证据
        **({"failed_attempts": list(getattr(result, "failed_attempts", None) or [])}
           if getattr(result, "failed_attempts", None) else {}),
        # A3（复核 2026-10-04 第三轮）：**授权元数据补全**那次调用的完整记录
        # （`call_id` / 成败 / 响应头与长度 / 用量 / 请求与补上条数，或 `{"skipped": 原因}`）。
        # 只在真的走到这一步时出现——没缺字段的轮次 payload 形状与历史完全一致。
        **({"completion": getattr(result, "completion", None)}
           if getattr(result, "completion", None) is not None else {}),
    }
    # S05（2026-11-11）：把「这条结论对应哪一份请求」写进产物本身。
    #   · `content_identity` = 完整请求身份（含定向项），提交循环据此判复用；
    #   · `source_identity`  = 不含定向项的内容身份，恢复入口据此判失配。
    # 两者都是**内容**身份：`run_id` 已从请求字节里剔除（见 `audit._DIAGNOSTIC_METADATA_KEYS`），
    # 同一份冻结输入重跑得到同一个值。
    payload["content_identity"] = _final_audit_identity(
        script, novel_text, plan, _focus_now, model, run_id)
    payload["source_identity"] = _final_audit_source_identity(
        script, novel_text, plan, model)
    # S03：影子裁决报告（诊断产物，不参与准入、不改写 payload）
    _write_audit_adjudication(payload, script)
    return payload


_AUDIT_DIAG_NAME = "step01_audit_rounds.json"
#: S03（2026-11-11）：影子裁决报告的产物名。**诊断产物**：不参与准入、不被
#: `_reusable_final_audit` 读取，也不影响 blocking 计数——正式采纳仍由现行门禁决定。
_ADJUDICATION_NAME = "step01_audit_adjudication.json"


def _write_audit_adjudication(audit: dict, script: dict) -> None:
    """把本轮审计结论写成影子裁决报告（S03）.

    失败一律只 WARN：裁决报告是**诊断**，不能因为它写不出去就改变本轮结论。
    处置语义见 `step01_script_gen.audit_adjudication` 的模块文档。
    """
    _off = str(os.getenv("STEP01_AUDIT_ADJUDICATION", "1") or "").strip().lower()
    if _off in ("0", "false", "off", "no"):
        return
    try:
        from step01_script_gen.audit_adjudication import (
            build_shadow_report, dump_report, render_report_text)
        report = build_shadow_report(audit, script, context={"source": "final_audit"})
        dump_report(_mrt.CHAPTER_DIR / _ADJUDICATION_NAME, report)
        _mrt._logger.info(render_report_text(report).replace("\n", " ｜ "))
    except Exception as exc:
        _mrt._logger.warn(f"[ADJUDICATION] 影子裁决报告写入失败（不影响结论）: {exc}")


# 台账保留条数：单条约等于一次审计的完整 issue 列表（约 10KB），40 条 ≈ 400KB 封顶。
_AUDIT_DIAG_KEEP = max(5, int(os.getenv("AUDIT_DIAG_KEEP", "40") or "40"))


def _read_json_list_quiet(path) -> list:
    """读一个 JSON 数组；缺失/损坏/不是数组一律当空表（台账是诊断文件）."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _active_text_provider() -> str:
    """本轮审计**实际**走的文本通道（TEXT_PROVIDER），用于区分通道级差异."""
    try:
        from _text_gen import config as _text_cfg
        return str(getattr(_text_cfg, "PROVIDER", "") or "")
    except Exception:
        return ""


def _record_audit_diag(audit: "dict | None", *, phase: str, round_index: int,
                       reused: bool = False, run_id: str = "",
                       script: "dict | None" = None,
                       reuse_source: str = "") -> None:
    """每轮审计的诊断台账（v6.112，总计划 §1.3 观测点）.

    为什么需要它：正式产物 `step01_audit.json` 只保留**最后一轮**结论，修复轮之间
    到底发生了什么无从区分——同一个 shot 从 blocking 变 advisory，可能是
    （i）关联镜头的修改顺带修好了它，也可能是（ii）同一份内容两次审计给出不同
    severity。审计 payload 此前不落盘，日志只打 blocking 计数与 (shot, code)，
    没有 issue 文本，两种解释因此**无法区分**。

    本台账把每轮审计的 轮次 / 输入 revision / 完整 issue / 模型与实际 provider
    追加落盘到 `step01_audit_rounds.json`：**不覆盖**正式三份产物，也**不参与**
    任何准入或复用判定。写失败只告警。
    """
    _payload = audit if isinstance(audit, dict) else {}
    issues = [i for i in (_payload.get("issues") or []) if isinstance(i, dict)]
    _blocking = [i for i in issues
                 if str(i.get("severity") or "").strip() in ("hard", "visual_risk", "blocking")]
    rec = {
        "recorded_at": _mrt._step01_now(),
        "run_id": run_id or str(_payload.get("run_id") or ""),
        "phase": phase,
        "round": int(round_index),
        "reused": bool(reused),
        "status": str(_payload.get("status") or ""),
        "error": _payload.get("error"),
        "model": _payload.get("model"),
        # v6.117（方案 A）：审计可能走**专用**通道（`AUDIT_TEXT_PROVIDER`），此时全局
        # `TEXT_PROVIDER` 不是审计实际用的通道。优先记该次审计自报的通道，没有才退回
        # 全局值——未配置专用通道时行为与之前完全一致。
        "provider": (str(_payload.get("channel_provider") or "").strip()
                     or _active_text_provider()),
        "input_revision": _payload.get("input_revision"),
        "rule_source_fingerprint": (_payload.get("rule_source_fingerprint")
                                    or _mrt.RULE_SOURCE_FINGERPRINT),
        "duration_band": _payload.get("duration_band"),
        "prompt_source_fingerprint": _mrt.PROMPT_SOURCE_FINGERPRINT,
        "audit_scope": _payload.get("audit_scope"),
        "audit_prompt_version": _payload.get("audit_prompt_version"),
        "audit_focus_version": _payload.get("audit_focus_version"),
        "audit_temperature": _payload.get("audit_temperature"),
        "issue_count": len(issues),
        "blocking_count": len(_blocking),
        # v6.117（方案 A）：通道是否真的在守 schema 的可核对证据（归一化前的偏离）
        "schema_deviations": [str(d) for d in (_payload.get("schema_deviations") or [])],
        # A3：补全调用的记录（见 `_audit_payload`）——只在该轮真的走到补全时出现
        **({"completion": _payload.get("completion")}
           if _payload.get("completion") is not None else {}),
        # v6.118：本轮的 token 用量（provider 自报）。**取不到就是 None**——不填估算，
        # 否则「实际消耗」与「我们的猜测」在台账里再也分不开。
        "usage": _payload.get("usage"),
        "blocking": [(str(i.get("shot_id")), str(i.get("code"))) for i in _blocking][:20],
        "issues": issues,
    }
    # v6.133：审计**失败**轮带上原始响应头与总长（成功轮不带这两个键）；
    # v6.134：parse_failed 重试发生时带真实调用次数（payload 仅 ≠1 时携带）；
    # v6.134.1：被重试覆盖的失败尝试留档（payload 仅非空时携带）
    for _k in ("raw_text_head", "raw_text_len", "attempts", "failed_attempts"):
        if _payload.get(_k) is not None:
            rec[_k] = _payload.get(_k)
    # v6.153（复核之十九 P1）：复用**来源**——`in_loop` = 本次提交循环里沿用上一轮
    # 已完成的同身份审计（未发生调用）；`disk` = 沿用盘上正式产物/sidecar 的结论。
    # 只在复用轮出现，成功的新调用轮不带这个键（形状与历史一致）。
    if reuse_source:
        rec["reuse_source"] = str(reuse_source)
    if not rec["input_revision"] and script is not None:
        from shared.quality_gate import compute_script_revision
        try:
            rec["input_revision"] = compute_script_revision(script)
        except Exception as _degrade_exc:
            from shared.degrade import note_degraded as _note_degraded
            _note_degraded("step01_script_gen/main.py:_record_audit_diag", _degrade_exc)
    try:
        from shared.json_io import atomic_dump_json
        path = _mrt.CHAPTER_DIR / _AUDIT_DIAG_NAME
        rows = _read_json_list_quiet(path)
        rows.append(rec)
        atomic_dump_json(path, rows[-_AUDIT_DIAG_KEEP:], ensure_ascii=False, indent=2)
    except Exception as exc:
        _mrt._logger.warn(f"[AUDIT] 每轮审计台账写入失败（不影响结论）: {exc}")


# 失败响应留档上限（字符）：只留头部，够判"是不是 JSON / 断在哪"即可，避免台账膨胀。
_DIAG_RAW_HEAD_MAX = 2000

_REPAIR_DIAG_NAME = "step01_repair_rounds.json"
# 保留条数：单条约 1KB，与审计台账同样封顶
_REPAIR_DIAG_KEEP = max(5, int(os.getenv("REPAIR_DIAG_KEEP", "40") or "40"))


def _record_repair_diag(*, run_id: str, attempt: int, records: list) -> None:
    """定向修复事务的逐轮诊断台账（v6.119，2026-09-25 用户决定）.

    为什么单独一个文件、而不并进 `step01_audit_rounds.json`：两者粒度不同
    （审计轮 vs 修复轮），且审计台账的字段契约已被既有测试锁定；混进同一数组会
    逼消费方按形状过滤。

    v6.133（用户 2026-09-25~26）：台账里现在有**四类**记录，靠 `kind` 区分——
    `call_ok`（成功调用，带 max_tokens 与 reasoning/output token 用量）/
    `call_failed`（调用异常或响应被截断：整份丢弃、稿件没变）/
    `patch_unusable`（响应完整但补丁不可用：解析失败 / 结构无效 / 空补丁 /
    全被校验拒）；其余（有 `accepted`/`resolved` 字段的）是修复事务本身的逐轮诊断。
    三类调用记录此前都没有，BLOCK 的直接原因只会被外层记成"零改善"。

    为什么必须有它：2026-09-25 离线取证实测——一次真实重跑只留下 `new=3` 这个
    **计数**。`new_blocking_keys` 原本只在「整轮回退」分支打印，而该轮走的是
    「**按目标回退**」：`_decision` 被回退后的决策覆盖（其 keys 已是空列表），
    那条日志再也不会执行 → 「哪三条新阻塞」事后无从回答。本台账把**回退前**捕获的
    keys、落点目标、被回退的目标与联动组、修复前后的 revision 一起留下来。

    与审计台账同样的边界：**不覆盖**正式三份产物、不参与任何准入判定；写失败只告警。
    """
    if not records:
        return
    try:
        from shared.json_io import atomic_dump_json
        path = _mrt.CHAPTER_DIR / _REPAIR_DIAG_NAME
        rows = _read_json_list_quiet(path)
        for _rec in records:
            rows.append({"recorded_at": _mrt._step01_now(), "run_id": run_id,
                         "commit_round": int(attempt), **dict(_rec)})
        atomic_dump_json(path, rows[-_REPAIR_DIAG_KEEP:], ensure_ascii=False, indent=2)
    except Exception as exc:
        _mrt._logger.warn(f"[REPAIR] 修复轮诊断台账写入失败（不影响结论）: {exc}")


def _reuse_miss_key(audit: dict, *, revision: str,
                    expected_identity: "str | None",
                    expected_source_identity: "str | None",
                    expected_identity_without_focus: "str | None") -> str:
    """**复用失配键**（M2.3，2026-11-16）："" = 可复用；否则第一个失配项的名字.

    只做判定与命名，不写日志、不读盘——这样单测能直接钉住"到底哪一项不符"。
    为什么需要它：`19:00` 与 `22:35` 两次都只留下一句
    「content_identity 不匹配（原文/计划/定向项/契约已变）」，四个可能原因被
    挤在一句话里，导致文档把根因误归因到"确定性层改了内容身份"（已证伪）。
    现在把每一项单独命名，并把**定向项**从其余四项里分出来：
    给了 `expected_identity_without_focus`（同稿同契约、focus 置空的身份）时，
    若盘上身份恰好等于它 ⇒ 失配点只可能是 `audit_focus`。
    """
    if str(audit.get("status") or "").strip().lower() != "ok":
        return "status_not_ok"
    # 2026-11-18：**具体契约项先于聚合身份**。`content_identity` 包含稿件 revision，
    # 若先比它，任何 revision/规则/提示词变化都会被笼统报成 content_identity——
    # 19:00 的失配就是这样被误读成「同稿的隐藏字段变了」，实际是送审稿 revision
    # 不同（00:38 审计 e8f63762 vs 19:00 稿件 64a347a4）。先报具体键，聚合身份
    # 只在其余项全同、仍不相等时才作为「原文/计划/契约内容变化」的兜底。
    if str(audit.get("input_revision") or "").strip() != revision:
        return "input_revision"
    if str(audit.get("rule_source_fingerprint") or "").strip() != _mrt.RULE_SOURCE_FINGERPRINT:
        return "rule_source_fingerprint"
    if not _mrt.duration_band_matches(audit):
        return "duration_band"
    if str(audit.get("audit_scope") or "").strip() != _mrt.STEP01_AUDIT_SCOPE:
        return "audit_scope"
    # v6.111：提示词/focus 版本也要一致（改提示词后旧结论不得复用）。
    from step01_script_gen.audit import audit_prompt_version as _apv
    if str(audit.get("audit_prompt_version") or "") != _apv():
        return "audit_prompt_version"
    if str(audit.get("audit_focus_version") or "") != _audit_focus_version():
        return "audit_focus_version"
    # v6.117：审计**通道**也是被审对象的一部分（配了专用通道后旧通道结论不复用）。
    from step01_script_gen.audit import resolve_audit_channel as _rac
    _chan_provider, _chan_model = _rac()
    if _chan_provider is not None:
        if str(audit.get("channel_provider") or "").strip().lower() != _chan_provider:
            return "channel_provider"
        if str(audit.get("model") or "").strip() != _chan_model:
            return "channel_model"
    # S05：内容身份（原文/计划/送审稿/契约/定向项）——给了期望值就必须逐字相符。
    if expected_identity is not None:
        _got = str(audit.get("content_identity") or "").strip()
        if not _got:
            return "content_identity_missing"
        if _got != expected_identity:
            if expected_identity_without_focus and _got == expected_identity_without_focus:
                return "audit_focus"
            return "content_identity"
    if expected_source_identity is not None:
        _got_src = str(audit.get("source_identity") or "").strip()
        if not _got_src:
            return "source_identity_missing"
        if _got_src != expected_source_identity:
            return "source_identity"
    return ""


#: 失配键 → 人读文案（日志与文档共用一套措辞，避免再出现"四因一句"）
#: 最近一次复用判定的失配读数（诊断用；进程内最后一次覆盖）
_LAST_REUSE_MISS: dict = {}


def last_reuse_miss() -> dict:
    """最近一次 `_reusable_final_audit` 的失配读数（空 dict = 最近一次可复用/未判定）."""
    return dict(_LAST_REUSE_MISS)


def _record_reuse_miss(key: str, audit: dict, *, src, revision: str = "") -> None:
    """把失配键与**两边读数**记进 `_LAST_REUSE_MISS`，并留一行可检索日志."""
    from step01_script_gen.audit import audit_prompt_version as _apv
    from step01_script_gen.audit import resolve_audit_channel as _rac
    _cp, _cm = _rac()
    _vals = {
        "input_revision": {"audit": str(audit.get("input_revision") or "")[:16],
                           "expected": str(revision or "")[:16]},
        "rule_source_fingerprint": {
            "audit": str(audit.get("rule_source_fingerprint") or ""),
            "expected": str(_mrt.RULE_SOURCE_FINGERPRINT)},
        "audit_scope": {"audit": str(audit.get("audit_scope") or ""),
                        "expected": str(_mrt.STEP01_AUDIT_SCOPE)},
        "duration_band": {"audit": dict(audit.get("duration_band") or {})},
        "audit_prompt_version": {"audit": str(audit.get("audit_prompt_version") or ""),
                                 "expected": str(_apv())},
        "audit_focus_version": {"audit": str(audit.get("audit_focus_version") or ""),
                                "expected": str(_audit_focus_version())},
        "channel_provider": {"audit": str(audit.get("channel_provider") or ""),
                             "expected": str(_cp or "")},
        "channel_model": {"audit": str(audit.get("model") or ""),
                          "expected": str(_cm or "")},
    }
    _LAST_REUSE_MISS.clear()
    _LAST_REUSE_MISS.update({
        "key": key,
        "text": _mrt._REUSE_MISS_TEXT.get(key, key),
        "source": str(src),
        "audit_revision": str(audit.get("input_revision") or "")[:16],
        "detail": dict({"field": key}, **(_vals.get(key) or {})),
    })
    try:
        _mrt._logger.warn("[COMMIT] 旧审计不可复用：%s｜key=%s｜审计=%s"
                     % (_mrt._REUSE_MISS_TEXT.get(key, key), key, str(src)))
    except Exception:
        pass


def _reusable_final_audit(script: dict, *, expected_identity: "str | None" = None,
                          expected_source_identity: "str | None" = None,
                          expected_identity_without_focus: "str | None" = None) -> "dict | None":
    """旧审计可复用的条件：**revision + 规则指纹 + audit scope 全部相同且为 ok**.

    总计划 §1.1 的既定决策。三者任一不同都必须重新审计——否则等于拿旧规则/旧稿
    的结论给新稿盖章。`created_at`/`run_id` 只是诊断字段，不参与复用判定。

    S05（2026-11-11）：上述三条**只看稿子与规则，看不到原文和计划**。同一份分镜
    配上不同原文、或计划换了，revision 与指纹都可能一致，于是旧结论照样被复用。
    现在把内容身份并进判据：

      · `expected_identity`        —— 完整请求身份（提交循环用，含定向项）；
      · `expected_source_identity` —— 不含定向项的来源身份（恢复入口用）。

    给了期望身份就**必须逐字相符**；旧审计没有身份字段时判「复用证据不足」并拒收，
    **不得**退回 revision 弱判据冒充核验。不给期望身份只保留给历史调用与单测
    （生产入口必须传）。
    """
    from shared.quality_gate import compute_script_revision

    revision = compute_script_revision(script)
    _reject_reason = ""
    # v6.109（总计划 §1.3「同一 revision 不通过反复调用 full audit 获取一次偶然
    # PASS」）：BLOCK 时审计其实**已经跑过**并给出了未决条目，只是没写成正式产物。
    # 若下一次运行又拿到同一 revision，重新调用全量审计就是抽签——两次审计结论不同
    # 只因模型波动，而不是因为稿子变好了。所以除正式产物外，也读 BLOCK 时留下的
    # sidecar，把它当作**本轮未决清单**复用：让修复轮去修这些条目，而不是再掷一次骰子。
    # 修复真正落地后 revision 会变，下一次审计自然是对新对象的全新调用。
    # 恢复入口可以指向章节目录**之外**的不可变审计副本（STEP01_AUDIT_SOURCE）：
    # 否则要先把副本塞进正式章节目录，那等于在发布前改正式产物。
    _audit_sources = []
    _ov = str(os.getenv("STEP01_AUDIT_SOURCE") or "").strip()
    if _ov:
        _audit_sources.append(Path(_ov))
    for name in ("step01_audit.json", "step01_audit.blocked.json"):
        _audit_sources.append(_mrt.CHAPTER_DIR / name)
    for _src in _audit_sources:
        audit = _mrt._read_json_quiet(_src)
        if not audit:
            continue
        # M2.3：逐项判定并**命名**失配键（"哪一项不符"必须是数据，不是猜）。
        _miss = _reuse_miss_key(
            audit, revision=revision, expected_identity=expected_identity,
            expected_source_identity=expected_source_identity,
            expected_identity_without_focus=expected_identity_without_focus)
        if _miss:
            _record_reuse_miss(_miss, audit, src=_src, revision=revision)
            _reject_reason = _reject_reason or _mrt._REUSE_MISS_TEXT.get(_miss, _miss)
            continue
        _LAST_REUSE_MISS.clear()
        return audit
    if _reject_reason:
        _mrt._logger.warn("[COMMIT] 旧审计不可复用：%s" % _reject_reason)
    return None


def _clear_staged_dir() -> None:
    """清掉 `_staged/`（v6.103，第二十二次重跑实测）.

    `_staged/` 只在**自验通过那一刻**才有意义（它的内容与提交后的正式产物一致）。
    上一轮 BLOCK 留下的 staged 副本会在章节目录里滞留，看起来像「权威副本」——
    实测就是这样：run 2 在 staged 自验失败留下的 `_staged/step01_quality.json`
    在 run 3 BLOCK 之后仍然存在。BLOCK 路径一律清掉，诊断证据改由 BLOCK
    manifest（含 blocking 明细）+ snapshots/ 承担。
    """
    import shutil as _shutil
    try:
        _shutil.rmtree(_mrt.CHAPTER_DIR / "_staged", ignore_errors=True)
    except Exception:
        pass


def _write_blocked_audit_sidecar(audit: "dict | None") -> None:
    """BLOCK 时把本轮审计结论落成 sidecar（v6.109）.

    正式产物 `step01_audit.json` 在 BLOCK 时**不能**替换（它必须与另外两份产物
    同一 revision）。但审计确实跑过了，结论必须留下来：否则下一次运行拿到同一
    revision 时会重新调用一次全量审计——那就是「反复抽样博一次偶然 PASS」
    （总计划 §1.3）。sidecar 只被 `_reusable_final_audit` 读，不参与准入。
    """
    if not isinstance(audit, dict) or audit.get("input_revision") is None:
        return
    try:
        from shared.json_io import atomic_dump_json
        atomic_dump_json(_mrt.CHAPTER_DIR / "step01_audit.blocked.json", audit,
                         ensure_ascii=False, indent=2)
    except Exception as exc:
        _mrt._logger.warn(f"[COMMIT] BLOCK 审计 sidecar 写入失败（不影响结论）: {exc}")


def _budget_freeze_root(run_id: str = "") -> str:
    """冻结候选的**隔离根**：永远在 `.dsh/` 下，不碰 `./output` 正式产物."""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(_mrt._MAIN_FILE)))
    return os.path.join(_root, ".dsh", "step01_budget_frozen",
                        str(run_id or "default").strip() or "default")


def _freeze_budget_exhausted(script: dict, *, action: str, admission: dict,
                             run_id: str = "") -> dict:
    """预算不足：保存冻结候选 + 稿件到隔离根，返回 `budget_exhausted` 记录.

    记录里**不含任何旧审计结果**（`prior_audit_reused=False`）；续验必须重新
    跑完整审计 / 提交链，不借用旧结论冒充本轮通过。
    """
    from step01_script_gen.repair_budget import freeze_candidate
    from shared.json_io import atomic_dump_json as _dump
    _run = str(run_id or "").strip() or _mrt._step01_run_id()
    _root = _budget_freeze_root(_run)
    _meta = _final_audit_metadata(script, run_id=_run)
    rec = freeze_candidate(
        script=script, input_revision=str(_meta.get("input_revision") or ""),
        audit_config=admission.get("audit_config") or {},
        rule_fingerprint=str(_meta.get("rule_source_fingerprint") or ""),
        admission=admission, action=action, output_root=_root)
    try:
        os.makedirs(_root, exist_ok=True)
        _dump(os.path.join(_root, "frozen_candidate.json"), rec)
        _dump(os.path.join(_root, "frozen_script.json"), script)
        rec["files"] = ["frozen_candidate.json", "frozen_script.json"]
        rec["written"] = True
    except Exception as _e:
        rec["written"] = False
        rec["write_error"] = "%s: %s" % (type(_e).__name__, _e)
        _mrt._logger.warn("[BUDGET] 冻结候选落盘失败（终局不变）: %s" % _e)
    _mrt._logger.error("[BUDGET] %s 准入失败（剩余 %.1fs < 需要 %.1fs）→ "
                  "budget_exhausted；冻结候选=%s"
                  % (action, float(admission.get("remaining_budget_sec") or 0),
                     float(admission.get("required_sec") or 0), _root))
    return rec


def _verify_budget_resume(record: dict, script: dict, *,
                          admission: dict | None = None) -> dict:
    """冻结稿续验：三项身份核对通过才允许进入现有完整审计 / 提交链."""
    from step01_script_gen.repair_budget import verify_frozen_candidate
    _meta = _final_audit_metadata(script)
    return verify_frozen_candidate(
        record, script=script,
        input_revision=str(_meta.get("input_revision") or ""),
        audit_config=_mrt._final_audit_reserve_config(),
        rule_fingerprint=str(_meta.get("rule_source_fingerprint") or ""),
        budget_admission=admission)
