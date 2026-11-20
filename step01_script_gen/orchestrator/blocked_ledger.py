# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.blocked_ledger —— 阻塞修订台账.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import json

from step01_script_gen.orchestrator import runtime as _mrt
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
# 「已知语义欠账」台账文件名（v6.115，审核）
_BLOCKED_LEDGER_NAME = "_blocked_revisions.json"


def _blocked_ledger_path():
    return _mrt.OUTPUT_FILE.parent / "snapshots" / _BLOCKED_LEDGER_NAME


def _revision_aliases_or_empty(script) -> dict:
    """`{版本号: revision}`——一份稿件在**各版 revision 算法**下的身份（失败 → 空表）.

    2026-09-30 复核第 4 轮 P1：换哈希算法不能让未解决的语义欠账失去匹配。
    第 5 轮 P1-2 补：身份**按版本**使用（判同/欠账都不许把各版本混成一个等价关系），
    所以这里返回的是版本字典而不是扁平列表。计算失败只降级为「少记兼容身份」，
    **绝不**因此打断 BLOCK / 选稿路径。
    """
    try:
        from shared.quality_gate import script_revision_aliases
        return {int(k): str(v) for k, v in dict(script_revision_aliases(script)).items()}
    except Exception as exc:
        _mrt._logger.warn(f"[BEST-GUARD] revision 别名计算失败（台账少记兼容身份）: {exc}")
        return {}


def _current_criteria() -> dict:
    """当前判据指纹（规则源 + 提示源）——台账按它判断「欠账是否仍然成立」."""
    return {"rules": str(_mrt.RULE_SOURCE_FINGERPRINT or ""),
            "prompts": str(_mrt.PROMPT_SOURCE_FINGERPRINT or "")}


def _load_blocked_ledger() -> dict:
    """台账原样读出（读坏/缺失 → 空 dict，不阻断选稿）."""
    try:
        with open(_blocked_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_blocked_revisions() -> dict:
    """**在现行判据下仍然欠账**的身份，按**登记版本**分组：`{版本号: {revision}}`.

    （旧签名是 `set`；改按版本分组的原因见 `shared.quality_gate.script_revision_aliases`
    ——各版本身份不能混成一个等价关系。）

    2026-09-27（用户裁决 4）：台账条目带上登记时的判据指纹。判据/提示变了
    （`rules` / `prompts` 指纹不同）→ 那条欠账是**旧判据下**的结论，不再据此
    排除该稿：判据本身可能已经修掉（本轮 `frame_presence_consistency` 的时态误判、
    缺段重复计量都是这一类）。指纹不符的条目在选稿日志里显式列出，不静默丢弃。

    没有指纹的老条目（本改动之前登记的）按**仍然欠账**处理——保守方向：
    宁可少选一份旧稿，不可凭无法核对的旧结论放它回来。
    """
    data = _load_blocked_ledger()
    revs = [str(x) for x in (data.get("revisions") or [])]
    crit = data.get("criteria")
    cur = _current_criteria()
    # 2026-09-30 复核第 5 轮 P1-2：每条欠账记**登记时的算法版本**，选稿按版本比对。
    # 复核第 6 轮更正：**「旧记录只可能是 v1」这个假设要撤回**——上一版代码已经写出过
    # `"revision_version": 2` 但没有逐条 `entry_versions` 的台账（值本身就是 v2 算的），
    # 一律当 v1 会让同一份稿件的旧 v2 欠账**查不到**（离线已复现）。三级推断：
    #   ① 逐条 `entry_versions[rev]` → 用它（现行写法，唯一权威）；
    #   ② 顶层 `revision_version` + 无逐条映射（单版本台账）→ **按该版本解释**；这类
    #      台账无法逐条区分 v1/v2，故同时按 v1 登记（欠账宁可多排除一稿，不因迁移丢失，
    #      与台账既有的保守口径一致）；
    #   ③ 两者都没有（真·旧台账）→ v1。
    _ver_raw = (data.get("entry_versions")
                if isinstance(data.get("entry_versions"), dict) else {})
    _alias_raw = (data.get("alias") if isinstance(data.get("alias"), dict) else {})
    _ledger_ver = 0
    try:
        _ledger_ver = int(data.get("revision_version") or 0)
    except (TypeError, ValueError):
        _ledger_ver = 0

    def _versions_of(rev: str) -> set:
        _r = str(rev)
        if _r in _ver_raw:                       # ① 逐条权威
            try:
                return {int(_ver_raw[_r])}
            except (TypeError, ValueError):
                return {1}
        if _ledger_ver >= 2:                     # ② 单版本台账（旧 v2 欠账）
            return {1, _ledger_ver} if _r not in _alias_raw else {_ledger_ver}
        return {1}                               # ③ 真·旧台账

    def _put(dest: dict, rev: str) -> None:
        for _v in _versions_of(rev):
            dest.setdefault(_v, set()).add(str(rev))

    if not isinstance(crit, dict):
        out: dict = {}
        for _r in revs:
            _put(out, _r)
        return out
    _issues = data.get("issues") if isinstance(data.get("issues"), dict) else {}
    out, stale = {}, []
    _partly: list = []
    for rev in revs:
        _rec = crit.get(rev)
        _iss = _issues.get(rev)
        if isinstance(_iss, list) and _iss:
            # v6.149：**逐条**判——只把「所依赖判据已变」的那些条目作废，
            # 其余仍然排除该稿（旧口径是任何指纹一动就整批放行）。
            _force = [it for it in _iss
                      if isinstance(it, dict) and _debt_issue_in_force(it)]
            if _force:
                _put(out, rev)
                if len(_force) < len(_iss):
                    _partly.append((rev, len(_iss) - len(_force)))
                continue
            stale.append(rev)
            continue
        # 老条目（无逐条记录）：退回整条指纹口径
        if isinstance(_rec, dict) and (_rec.get("rules"), _rec.get("prompts")) != (
                cur["rules"], cur["prompts"]):
            stale.append(rev)
            continue
        _put(out, rev)
    if _partly:
        _mrt._logger.info(
            "[BEST-GUARD] 台账里 %d 份欠账稿**部分**问题所依赖的判据已变"
            "（按具体问题计入，其余仍排除）：%s"
            % (len(_partly), ["%s×%d" % (r[:12], n) for r, n in _partly[:6]]))
    if stale:
        _mrt._logger.info(
            "[BEST-GUARD] 台账里 %d 份欠账稿是在**旧判据**下登记的（规则/提示指纹已变）"
            "——本轮不据此排除，按现行判据重新评估：%s"
            % (len(stale), [r[:12] for r in stale[:6]]))
    return out


def _snapshot_cross_field_blocking(script: dict):
    """快照在**现行确定性判据**下的跨字段 blocking（v6.149 候选资格初筛用）.

    与提交点同源（`cross_field_issues`），而不是质量门的风险计数——实测
    2026-09-29：快照 `script_result_20260929_053150.json` 以「风险1」入选，一进
    提交点就带 `result_action_support` 被判 blocking，反复被选中、反复 BLOCK。
    读坏/异常 → 空表（不阻断选稿）。
    """
    from step01_script_gen.cross_field_checks import cross_field_issues
    try:
        return [i for i in (cross_field_issues(script)[0] or [])
                if str(getattr(i, "severity", "") or "")
                in ("hard", "visual_risk", "blocking")]
    except Exception as exc:
        # v6.150（复核发现 4）：**校验器异常 ≠ 没有问题**。旧实现返回空表，与「无
        # blocking」不可区分——校验器一旦坏掉，所有快照都会「干净」地进候选池。
        # 返回 None 表示**未能校验**，调用方据此让该快照本次失去候选资格。
        _mrt._logger.warn("[BEST-GUARD] 快照跨字段校验器异常（本次不具候选资格）: %s" % exc)
        return None


def _debt_issue_records(blocking) -> list:
    """把 BLOCK 清单转成**逐条欠账记录**（v6.149，复核根因 3）.

    每条记 `code/shot/field` + **该条所依赖的判据指纹**。判据分流：
      · `model_audit_*` → **审计提示版本** `audit_prompt_version()`；
      · 其余（确定性）→ **规则源指纹** `RULE_SOURCE_FINGERPRINT`。

    为什么必须逐条：旧口径只在整条 entry 上记「规则源 + 提示源」两个**全局**指纹，
    任何一处提示改动都会让**整批**欠账（实测 59 份）一起恢复候选资格，「改无关提示
    不能自动洗掉欠账」这条就守不住。分流之后，无关提示改动不再移动
    `audit_prompt_version()`，审计类欠账继续排除。
    """
    from step01_script_gen.audit import audit_prompt_version
    out: list = []
    for b in (blocking or []):
        if not isinstance(b, dict):
            continue
        code = str(b.get("code") or "").strip()
        if not code:
            continue
        if code.startswith("model_audit_"):
            kind, fp = "audit", str(audit_prompt_version() or "")
        else:
            kind, fp = "rules", str(_mrt.RULE_SOURCE_FINGERPRINT or "")
        out.append({"code": code,
                    "shot": str(b.get("shot_id") if b.get("shot_id") is not None else ""),
                    "field": str(b.get("field_path") or ""),
                    "crit_kind": kind, "crit_fp": fp})
    return out


def _debt_issue_in_force(rec: dict) -> bool:
    """这条欠账的判据**未变** → 仍然成立（现行判据下还会报同一条）."""
    from step01_script_gen.audit import audit_prompt_version
    kind = str(rec.get("crit_kind") or "")
    if kind == "audit":
        return str(rec.get("crit_fp") or "") == str(audit_prompt_version() or "")
    if kind == "rules":
        return str(rec.get("crit_fp") or "") == str(_mrt.RULE_SOURCE_FINGERPRINT or "")
    return True          # 认不出判据 → 保守：按仍然欠账


def _record_blocked_revision(revision: str, issues=None, versions=None) -> None:
    """把 BLOCK 的 revision 记进台账（v6.115，审核）.

    为什么需要：BEST-GUARD 按**确定性**分数与风险排序，它**不知道**某份历史快照
    还欠哪些**语义**问题——实测 run 17/18 反复选回 21:55 的旧快照，而那份保留着
    Rule-7 造成的旧文本（审计每轮重报）。台账让选稿能避开已知欠账的旧稿。

    `versions`：`{算法版本号: revision}`。台账只记**主 key 的登记版本**
    （`entry_versions`），选稿侧据此按版本比对——旧版本身份只用于匹配明确属于那个
    版本的旧记录（2026-09-30 复核第 4/5 轮）。

    v6.142（09-26 P1-4）：同时记 `debt[revision]` = **该稿卷入过几次 BLOCK 的
    run**（每次登记 +1）。台账原本只是个集合——「全部快照都欠账」时整表放开、
    改按分数/风险选，于是**欠账最重的那份照样被反复选中**（P1-4 实损形态）。
    有了次数，饱和放开时就能「优先选欠账最少的一份」，且被选中者下一轮计数
    +1、自然降位——棘轮不再失效。

    2026-09-27（用户裁决 4）：同时记 `criteria[revision]` = 登记时的**判据指纹**
    （规则源 + 提示源）。判据/提示改过之后，「已知 BLOCK」不再等于「现在还会
    BLOCK」（本轮就有两条：`frame_presence_consistency` 的时态误判、缺段重复计量）
    ——没有这个字段，修好的判据也换不回那份稿的重评机会。
    """
    if not revision:
        return
    try:
        path = _blocked_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        cur, debt, crit, iss, vers = [], {}, {}, {}, {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                _data = json.load(f) or {}
            cur = list(_data.get("revisions") or [])
            # 容错口径与 `_load_blocked_debt` 同一处（`_coerce_debt_map`）——
            # 一条脏计数**不得**让整张台账写不进去（那等于静默废掉护栏）
            debt = _coerce_debt_map(_data.get("debt"), cur)
            _raw_crit = _data.get("criteria")
            if isinstance(_raw_crit, dict):
                crit = {str(k): v for k, v in _raw_crit.items() if isinstance(v, dict)}
            _raw_iss = _data.get("issues")
            if isinstance(_raw_iss, dict):
                iss = {str(k): v for k, v in _raw_iss.items() if isinstance(v, list)}
            # 登记版本表（2026-09-30 复核第 5 轮 P1-2）：`{主 revision: 算法版本号}`
            _raw_ver = _data.get("entry_versions")
            if isinstance(_raw_ver, dict):
                _tmp: dict = {}
                for _k, _v in _raw_ver.items():
                    try:
                        _tmp[str(_k)] = int(_v)
                    except (TypeError, ValueError):
                        continue
                vers = _tmp
        except Exception:
            cur, debt, crit, iss, vers = [], {}, {}, {}, {}
        cur = [str(x) for x in cur if str(x) != revision]
        cur.append(str(revision))
        cur = cur[-64:]
        debt = {k: v for k, v in debt.items() if k in set(cur)}
        debt[str(revision)] = int(debt.get(str(revision), 0)) + 1
        # 2026-09-30 复核第 4/5 轮：记下**这条身份是哪个版本的算法产生的**。换算法
        # 后选稿按版本比对才能命中它（旧欠账不消失），同时不会让「同一段文字、契约
        # 不同」的另一份稿被它连坐。`debt` / `criteria` / `issues` 仍只按主 revision
        # 记一次——版本号不是一次新的欠账，不重复计数。
        vers = {k: v for k, v in vers.items() if k in set(cur)}
        from shared.quality_gate import SCRIPT_REVISION_VERSION as _rv_ver
        vers[str(revision)] = int(_rv_ver)
        # 2026-09-27（用户裁决 4）：记下**登记时的判据指纹**——下次读台账才知道
        # 这条欠账是不是在现行判据下得出的（判据变了要重新评估，不能一直排除）。
        crit = {k: v for k, v in crit.items() if k in set(cur)}
        crit[str(revision)] = _current_criteria()
        # v6.149：逐条欠账（含各自依赖的判据指纹）。**只补不覆盖**——同一条 revision
        # 多次 BLOCK 时，把新出现的条目并进去，已排除过的问题不会因覆盖而丢。
        if issues:
            _old_iss = iss.get(str(revision)) or []
            _merged, _seen = [], set()
            # v6.150（复核发现 2）：**新的在前**——同一 `(code, shot, field)` 在现行
            # 判据下再次 BLOCK 时，必须由新记录**覆盖**旧版。旧实现把旧条目排在前面，
            # 于是新记录被去重跳过、台账留着**已过期**的判据指纹，该稿下次又被判「可重评」
            # 放回候选池（正是要治的那条）。`[:40]` 截断同理——留最新的 40 条。
            for _it in list(issues) + list(_old_iss):
                if not isinstance(_it, dict):
                    continue
                _k = (str(_it.get("code")), str(_it.get("shot")), str(_it.get("field")))
                if _k in _seen:
                    continue
                _seen.add(_k)
                _merged.append(_it)
            iss[str(revision)] = _merged[:40]
        iss = {k: v for k, v in iss.items() if k in set(cur)}
        from shared.json_io import atomic_dump_json
        atomic_dump_json(path, {"revisions": cur, "debt": debt, "criteria": crit,
                                "issues": iss, "entry_versions": vers,
                                "revision_version": int(_rv_ver)},
                         ensure_ascii=False, indent=2)
    except Exception as exc:
        _mrt._logger.warn(f"[BEST-GUARD] 欠账台账写入失败（不影响结论）: {exc}")


def _coerce_debt_map(raw, revisions) -> dict:
    """把台账里的 `debt` 段规整成 `{revision: episodes}`（**读写两侧共用**）.

    容错口径只有一处，读写必须一致——否则「读出来是 1 轮、再登记一次却变回 1」
    这种自相矛盾会让棘轮计数无声漂移：
      - 段缺失 / 不是 dict（老台账）→ 表内每条记 **1 轮**（至少欠过一次；记 0 会
        让老条目在饱和放开时无条件胜出，正是 P1-4 要治的方向）；
      - 单条脏值 → 只按「1 轮」计它自己，**不牵连同表其余条目**；
      - 不在 `revisions` 表内的键丢弃（表只留最近 64 条，计数跟着表走）。
    """
    _revs = {str(x) for x in (revisions or [])}
    if not isinstance(raw, dict):
        return {r: 1 for r in _revs}
    out: dict = {}
    for k, v in raw.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            out[str(k)] = 1
    return {k: v for k, v in out.items() if k in _revs}


def _load_blocked_debt() -> dict:
    """台账里的「欠账轮次」：`{revision: episodes}`（读坏/缺失返回空 dict）.

    2026-09-27（用户裁决 4）：**不再参与选稿排序**（候选池里已没有欠账稿）。
    保留读取口径是为了**可见性**：选稿日志会打出「欠账最重的几份」，
    以及台账写侧的容错读数（`_coerce_debt_map` 读写同源）。

    老台账（只有 `revisions`、没有 `debt`）→ 一律按 1 轮计：它们至少欠过一次，
    但不知道欠了几次；按 1 计不会让任何一份凭空占优（不写 = 0 会让老条目
    无条件胜出，正是要治的那个方向）。
    """
    try:
        with open(_blocked_ledger_path(), "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return _coerce_debt_map(data.get("debt"), data.get("revisions"))
    except Exception:
        return {}


def _snapshot_new_candidate(script: dict) -> str:
    """给「BEST-GUARD 选择之前的新生成候选」留一份独立诊断快照，返回路径（失败 ""）.

    v6.123（用户口径）：此前的做法是——若 BEST-GUARD 选中历史稿，新稿只在内存里存在
    到 `script = _bg_selected` 那一刻；失败快照写的是**被选稿**，成功快照写的也是
    被选稿。于是「本次生成的质量」在事后没有任何落盘证据，而日志却宣称
    「本次 N 分版本保留在 snapshots/」。

    文件名用 `candidate_pre_bestguard_<ts>.json`（**不带** `script_result_` 前缀）：
    BEST-GUARD 的候选扫描就是按 `script_result_*.json` 找文件的，这份未定稿
    （无 `chapter_outline`、未过 hook QC）不得进入候选池。轮转在
    `_snapshot_run_artifacts()` 里按同一上限处理。
    """
    try:
        import time as _time

        snap_dir = _mrt.OUTPUT_FILE.parent / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        from shared.json_io import atomic_dump_json as _adj
        _path = snap_dir / ("candidate_pre_bestguard_%s.json"
                            % _time.strftime("%Y%m%d_%H%M%S"))
        _adj(_path, script, ensure_ascii=False, indent=2)
        return str(_path)
    except Exception as _snap_e:
        try:
            _mrt._logger.warn(f"[BEST-GUARD] 新候选快照写入失败（不影响主流程）: {_snap_e}")
        except Exception:
            pass
        return ""


def _non_script_snapshot_reason(obj) -> str:
    """该 JSON 是否**不是分镜稿** → 返回跳过原因（空串 = 是稿件）.

    v6.124.2（用户 2026-09-25 决定）：BEST-GUARD 的候选必须先**验明正身**再评分。

    为什么必须挡在评分**之前**：`_candidate_quality_summary()` 在一份没有分镜的
    JSON 上查不出任何 issue，于是 `structural_ok=True`、`integrity_ok=True`、
    `blocking_risk_count=0`，`_quality_rank()` 给出 `(0, 0, 0)`——**优于任何真实
    稿件**（真稿至少带着 blocking 风险）。于是「**没有数据**」被读成「最干净」，
    这正是准则 7 的失败形态，而且落在选稿这个最关键的位置上。

        实测（2026-09-25 19:14）：审计诊断产物
        `snapshots/script_result_20260925_173311.audit.compare.json`（我的 (c)
        实验输出，非分镜稿）被选为终稿 → 公共管线审计它 → 报
        `empty_storyboard` + `missing_end_hook`（无字段可授权）→ 提交点 BLOCK。

    **刻意只校验最小结构**（用户口径：「别把旧版合法快照误挡掉」）：只要求
    顶层有非空的 `shots` 数组、元素是对象、且至少一个对象带 `index`/`prompt`。
    不校验 `timeline`/`characters` 等——那会把最小的合法稿件与旧版快照一起误挡，
    而本项目自己的最小稿件夹具就只有 `shots`（见
    `tests/step01_script_gen/test_best_guard.py::_script_with_dialogue`）。
    """
    if not isinstance(obj, dict):
        return "不是 JSON 对象"
    shots = obj.get("shots")
    if not isinstance(shots, list) or not shots:
        return "缺 shots 或 shots 为空数组"
    if not any(isinstance(s, dict) and (s.get("index") is not None or s.get("prompt"))
               for s in shots):
        return "shots 里没有可识别的镜头对象（缺 index/prompt）"
    return ""
