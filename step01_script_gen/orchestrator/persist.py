# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.persist —— 资产提取、运行快照、修复轮落盘.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import os
import json

from step01_script_gen.orchestrator import runtime as _mrt
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
# ============================================================================
# 6. 场景/道具资产提取（v7 新增）
# ============================================================================

def _extract_assets_and_update_script(script: dict) -> None:
    """从分镜脚本提取场景资产 + 道具资产，注册入库并写回 script.

    依赖 script 的 shots[].scene/location/prompt（脚本生成/收敛之后才能调用）。
    两个失败点（LLM 提取失败 / 注册失败）都只 WARN 不阻断流水线：
    下游 step02a 资产生成与 step02b r2v 参考在 bank 为空时自动跳过。

    Args:
        script: script_result.json dict（原地修改，写入 scene_assets/prop_assets）
    """
    # -- 场景/道具资产提取（v6.43 08-25 改为并发跑）--
    scene_assets_bank = _mrt.SceneAssetsBank()
    scene_assets_bank.load()
    prop_assets_bank = _mrt.PropAssetsBank()
    prop_assets_bank.load()

    _scene_assets: list = []
    _prop_assets: list = []
    if _mrt.SCENE_ASSET_EXTRACTION_ENABLED or _mrt.PROP_ASSET_EXTRACTION_ENABLED:
        _mrt._logger.info("[INFO] 并发提取 场景资产/道具资产（2 路并发）...")
        from step01_script_gen.concurrent_extract import extract_assets_concurrent
        _asset_results = extract_assets_concurrent(
            script,
            scene_assets_bank,
            prop_assets_bank,
            enable_scene_assets=_mrt.SCENE_ASSET_EXTRACTION_ENABLED,
            enable_props=_mrt.PROP_ASSET_EXTRACTION_ENABLED,
            log_func=_mrt._logger.info,
        )

        if _asset_results.get("scene_assets") is not None:
            _scene_assets = _asset_results["scene_assets"]
            for _sa in _scene_assets:
                scene_assets_bank.register_scene(_sa, _mrt.NOVEL_CHAPTER)
            scene_assets_bank.save()
            _mrt._logger.info(f"[INFO] 已提取 {len(_scene_assets)} 个场景资产（已保存到场景资产库）")

        if _asset_results.get("prop_assets") is not None:
            _prop_assets = _asset_results["prop_assets"]
            for _pa in _prop_assets:
                prop_assets_bank.register_prop(_pa, _mrt.NOVEL_CHAPTER)
            prop_assets_bank.save()
            _mrt._logger.info(f"[INFO] 已提取 {len(_prop_assets)} 个道具资产（已保存到道具资产库）")

    # 场景/道具资产描述写回 script，供 step02a 资产生成与 step02b r2v 参考引用
    _scene_assets_out = {}
    for _sid in scene_assets_bank.get_all_identifiers():
        _sa = scene_assets_bank.get_scene(_sid)
        if _sa:
            _scene_assets_out[_sid] = {
                k: _sa[k] for k in ("location", "time_of_day", "weather", "color_tone",
                                    "wide_desc", "near_desc", "consistent_terms")
                if k in _sa
            }
    # 场景别名 canonical merge：同一物理场景的细分命名只保留一个主场景，
    # 避免 15 个 scene asset 造成 step02a 重复生图和 shot_count_bounds 假超限。
    _scene_alias_map = {}
    _scene_canonical = {}
    for _scene_name in sorted(_scene_assets_out, key=lambda x: (len(x), x)):
        _base = next(
            (_b for _b in sorted(_scene_canonical, key=len, reverse=True)
             if len(_b) >= 2 and _scene_name.startswith(_b)),
            None,
        )
        if _base is None:
            _scene_canonical[_scene_name] = dict(_scene_assets_out[_scene_name])
            _scene_alias_map[_scene_name] = _scene_name
            continue
        _dst = _scene_canonical[_base]
        _src = _scene_assets_out[_scene_name]
        for _k, _v in _src.items():
            if _k == "consistent_terms":
                _merged = list(dict.fromkeys(list(_dst.get(_k) or []) + list(_v or [])))
                if _merged:
                    _dst[_k] = _merged
            elif not _dst.get(_k) and _v:
                _dst[_k] = _v
        _scene_alias_map[_scene_name] = _base
    if len(_scene_canonical) < len(_scene_assets_out):
        _scene_assets_out = _scene_canonical
        for _s in list(script.get("shots") or []) + ([script.get("hook")] if isinstance(script.get("hook"), dict) else []):
            if isinstance(_s, dict) and _s.get("scene") in _scene_alias_map:
                _s["scene"] = _scene_alias_map[_s["scene"]]
        _mrt._logger.info(f"[ASSET] 场景别名合并：{len(_scene_alias_map)} → {len(_scene_canonical)}")
    script["scene_assets"] = _scene_assets_out

    _prop_assets_out = {}
    # v6.40 08-22：prop_assets 字段 = 本次 LLM 提的 ∪ bank 中 last_chapter >= NOVEL_CHAPTER - 1 的
    # 且 ∉ audit_orphan_props.hallucinated_now 集合
    # 之前 L1148 遍历 bank 全部 → 把 LLM 幻觉的历史累积条目（如活虾/嫩藕/肥鸡）
    # 也写进 script_result.json → 下游 step02a/r2v 看到这些幻觉条目。
    # 改为并集：本次 LLM 提的（保证新提的真用道具）+ bank 中"本章或上章用过且非幻觉"的
    # （避免 LLM 不稳定漏提"舴艋舟"等已 stable 的道具，同时排除"活虾"等历史幻觉）。
    # audit_orphan_props 用 alias + 分镜全文反查识别幻觉，弥补"last_chapter 区分不了
    # 真用 vs 幻觉"的盲区。
    from step01_script_gen.asset_extractor import audit_orphan_props
    _audit = audit_orphan_props(
        prop_assets_bank, current_chapter=_mrt.NOVEL_CHAPTER, current_script=script,
        orphan_window=3,
    )
    _hallucinated = set(_audit.get("hallucinated_now", []))
    if _hallucinated:
        _mrt._logger.info(
            f"[INFO] audit 拦截 bank 幻觉 prop: {len(_hallucinated)} 个 → {sorted(_hallucinated)}"
        )
    # 1) 本次 LLM 提的（优先，含新提的真用道具）
    for _pa in _prop_assets:
        _pid = _pa.get("identifier", "").strip()
        if not _pid:
            continue
        _prop_assets_out[_pid] = {
            k: _pa[k] for k in ("category", "appearance", "consistent_terms")
            if k in _pa
        }
    # 2) bank 中本章或上章用到且非幻觉的（LLM 漏提兜底）
    _cutoff = _mrt.NOVEL_CHAPTER - 1
    for _pid in prop_assets_bank.get_all_identifiers():
        if _pid in _prop_assets_out or _pid in _hallucinated:
            continue
        _pa = prop_assets_bank.get_prop(_pid)
        if not _pa:
            continue
        _last = _pa.get("last_chapter")
        if _last is None or _last < _cutoff:
            continue
        _prop_assets_out[_pid] = {
            k: _pa[k] for k in ("category", "appearance", "consistent_terms")
            if k in _pa
        }
    script["prop_assets"] = _prop_assets_out
    # v6.62.1（09-06）：分镜未引用的道具过滤——本次 LLM 提的道具若在任何分镜
    # 文本（prompt/ff/lf/motion/kf/main_subject）都无命中（标准名/别名都不出现），
    # step02a 仍会为它生成白底资产图（烧额度），step02b r2v 也永不引用。
    # 「马车」09-06 实测：原文有、本章分镜没拍到 → 6 道具里白生成 1 张。
    # 用 consistent_terms（含别名）做覆盖判定，命中任一分镜文本才保留。
    _shot_text_pool = []
    for _s in script.get("shots", []) + ([script.get("hook")] if isinstance(script.get("hook"), dict) else []):
        if not isinstance(_s, dict):
            continue
        _parts = [str(_s.get(f) or "") for f in
                  ("prompt", "ff_desc", "lf_desc", "motion_desc", "main_subject")]
        for _kf in _s.get("keyframes") or []:
            if isinstance(_kf, dict):
                _parts.append(str(_kf.get("desc") or ""))
        _shot_text_pool.append(" ".join(_parts))
    _unused = []
    _PROP_ACTION_VERBS = ("持", "握", "提", "拿", "举", "执", "放", "横放", "挂",
                          "撑", "牵", "推", "掷", "抛", "拿好", "托", "抱", "挥")
    for _pid in list(_prop_assets_out):
        _terms = [str(t) for t in (_prop_assets_out[_pid].get("consistent_terms") or [])]
        _probe_terms = [str(_pid), *_terms]
        # 单字别名（车/箫/伞）在散文里歧义命中率高（09-06 实测「车轮大的红日」
        # 的「车」把未出镜的「马车」误判已引用），但一刀切剔除又把真用的
        # 「箫」（双手持箫/箫横放膝头）误删。单字 probe 收紧为
        # 「动词+单字」形态（持箫/推车/撑伞）才算引用；≥2 字 probe 子串照旧。
        _long_terms = [t for t in _probe_terms if len(t) >= 2]
        _short_terms = [t for t in _probe_terms if len(t) == 1]
        _short_patterns = [f"{v}{t}" for t in _short_terms for v in _PROP_ACTION_VERBS]

        def _hit(text: str) -> bool:
            if any(t in text for t in _long_terms):
                return True
            return any(p in text for p in _short_patterns)

        _referenced = any(_hit(text) for text in _shot_text_pool if text)
        if not _referenced:
            _unused.append(_pid)
            _prop_assets_out.pop(_pid)
    if _unused:
        _mrt._logger.info(
            f"[INFO] 分镜未引用的道具已从 prop_assets 剔除（不生成资产图）: {sorted(_unused)}"
        )
    if _scene_assets_out or _prop_assets_out:
        _mrt._logger.info(
            f"[INFO] 场景资产 {len(_scene_assets_out)} 个 / 道具资产 {len(_prop_assets_out)} 个 已写入 script_result.json"
        )


# ============================================================================
# 6. 主流程
# ============================================================================

# 08-21：mode 合并（v1-v4 渐进后正式落地）
# 删除了 08-20 重构的 A/B/C 三套反馈聚合模式（mode A 精简、mode B +原文、mode C 全片分布）。
# 原因：v3 同步 4 类约束后，三种 mode 90% 内容相同；v4 加决策树/正反例/跨镜一致性后，
# 差异化消失；用户几乎不切换 mode（A 实验后 C 胜出成为默认）。保留 3 套代码只增维护成本。
# 合并后 unified 模式：4 类约束 + 决策树 + 3 组正反例 + 跨镜一致性 + 全片错误分布 + 原文切片
# （原 mode B 的原文切片对 grounding 类问题很有用，无副作用全量注入）。


def _snapshot_log_delta(log_file, dest, snap_dir) -> None:
    """只快照**本次 run 的增量**日志（v6.112）.

    原来用 `shutil.copy2` 整份复制**累积**日志，而日志逐 run 增长 → snapshots/
    占用接近平方增长（实测 20 份 ≈ 449MB）。改成按字节偏移切片：偏移记在
    `snapshots/_log_offset`（上一次快照时的 EOF），本次只写 [offset, EOF) 这一段。
    快照文件仍是**可直接 grep 的纯文本**，只是范围限定为本次 run——文件头有一行
    `#` 注释标出字节区间，避免被误读成完整日志。

    偏移小于 0 或大于当前文件大小（日志被重建/截断）时退化为全量复制并重置偏移。
    """
    try:
        offset = int((snap_dir / "_log_offset").read_text(encoding="utf-8").strip() or "0")
    except Exception:
        offset = 0
    size = log_file.stat().st_size
    if offset < 0 or offset > size:
        offset = 0
    with log_file.open("rb") as fh:
        fh.seek(offset)
        blob = fh.read()
    header = b"# step01.log delta bytes=%d..%d (v6.112 per-run slice)" % (offset, size)
    dest.write_bytes(header + bytes([10]) + blob)
    try:
        (snap_dir / "_log_offset").write_text(str(size), encoding="utf-8")
    except Exception:
        pass


def _snapshot_run_artifacts(script: dict | None = None, *, audit: dict | None = None,
                            novel_text: str = "") -> None:
    """跑批快照（v6.50 P3，08-28）：script + step01.log 时间戳备份到 snapshots/.

    output 不进 git（P2 教训：想回滚 LLM 原始输出时无 .bak 无 raw 缓存），
    每次落盘后快照一份，只保留最近 N 份（N=SNAPSHOTS_KEEP env，默认 20，
    单源 shared/paths.MAX_SNAPSHOTS_PER_CHAPTER；按 script_result_*.json 计，
    配对删除同名 step01_*.log）。快照失败只告警，不影响主流程。
    """
    try:
        import shutil
        import time as _time
        snap_dir = _mrt.OUTPUT_FILE.parent / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = _time.strftime("%Y%m%d_%H%M%S")
        _snap_path = None
        if script is not None:
            from shared.json_io import atomic_dump_json as _adj
            _snap_path = snap_dir / f"script_result_{ts}.json"
            _adj(_snap_path, script, ensure_ascii=False, indent=2)
        # S07（复核）：修后稿落盘时**同步**保存不可变审计副本与恢复清单，
        # 按同一 revision 显式关联——否则下一轮的同名审计会覆盖，旧清单指向错配。
        if _snap_path is not None and isinstance(audit, dict):
            try:
                from step01_script_gen import resume as _resume
                _audit_copy = snap_dir / f"step01_audit_{ts}.json"
                _adj(_audit_copy, audit, ensure_ascii=False, indent=2)
                _novel_p, _novel_sha = _resume.freeze_text(_mrt.OUTPUT_FILE.parent, novel_text)
                _man = _resume.build_manifest(
                    _mrt.OUTPUT_FILE.parent, snapshot_path=_snap_path,
                    audit_path=_audit_copy, novel_text_path=_novel_p,
                    run_id=_mrt._step01_run_id(), stage="block")
                _man_p = snap_dir / f"step01_resume_{ts}.json"
                _adj(_man_p, _man, ensure_ascii=False, indent=2)
                # 写入后**读回核验**：路径与 revision 必须一致，否则恢复清单不可用
                _back = json.loads(_man_p.read_text(encoding="utf-8"))
                _rev_now = _resume._read_json(_snap_path)
                from shared.quality_gate import compute_script_revision as _csr
                if str(_back.get("script_revision")) != _csr(_rev_now):
                    raise RuntimeError("恢复清单 revision 与快照不一致")
                _mrt._logger.info("[SNAPSHOT] 修后稿+审计+恢复清单已按 revision 关联: %s"
                             % _man_p.name)
            except Exception as _pair_e:
                _mrt._logger.warn("[SNAPSHOT] 配对审计/恢复清单写入失败（恢复将不可用）: %s"
                             % _pair_e)
        log_file = _mrt.OUTPUT_FILE.parent / "step01.log"
        if log_file.exists():
            _snapshot_log_delta(log_file, snap_dir / f"step01_{ts}.log", snap_dir)
        snaps = sorted(snap_dir.glob("script_result_*.json"))
        # 轮转上限单源 shared/paths.MAX_SNAPSHOTS_PER_CHAPTER（env SNAPSHOTS_KEEP，默认 20）
        from shared.paths import MAX_SNAPSHOTS_PER_CHAPTER
        for old in snaps[:-MAX_SNAPSHOTS_PER_CHAPTER]:
            old.unlink(missing_ok=True)
            log_pair = old.name.replace("script_result_", "step01_").replace(".json", ".log")
            (snap_dir / log_pair).unlink(missing_ok=True)
        # v6.123：BEST-GUARD 选择前的候选快照（独立前缀）按**同一上限**轮转——
        # 它不进候选池，但同样不能无限增长。
        _cand_snaps = sorted(snap_dir.glob("candidate_pre_bestguard_*.json"))
        for old in _cand_snaps[:-MAX_SNAPSHOTS_PER_CHAPTER]:
            old.unlink(missing_ok=True)
    except Exception as _snap_e:
        try:
            _mrt._logger.warn(f"[SNAPSHOT] 快照失败（不影响主流程）: {_snap_e}")
        except Exception:
            pass


#: 修复轮快照前缀（M2.2）。固定名字便于事后按轮次定位，不参与 `script_result_*`
#: 的轮转（后者是「跑批快照」，本组是「修复事务的采纳凭据」）。
REPAIR_ROUND_PREFIX = "repair_round_"
#: 指向最近一次已采纳修复轮快照的指针文件（同目录，原子替换）。
REPAIR_ROUND_POINTER = "_repair_round_pointer.json"


def _persist_repair_round(script: dict, *, round_no: int, audit: "dict | None" = None,
                          novel_text: str = "") -> dict:
    """**修复轮落盘不变量**（M2.2，2026-11-16）：采纳即落盘，先落盘再复审.

    实测教训（2026-11-16 19:01 与 22:35 两次）：定向修复已经花钱且
    `accepted=true / resolved=1`，随后「修后复审」被发送上限或网络拦下，修后稿
    只存在于内存 → 整轮成果丢失，下一次只能从头再花 3 次调用。这里在
    `script = _cand` **之前**把它原子写入 `snapshots/repair_round_{k}.json`，
    并原子写指针 `snapshots/_repair_round_pointer.json`。

    为什么不直接改 `step01_resume.json`：`resume.build_manifest` 有一条硬不变量
    ——快照与审计必须同一 revision（`resume.py` 的 `build_manifest` 会直接抛
    `ResumeError`）。修后稿 revision 已变、盘上审计仍是修前那份，写进正式清单会
    让 `resume --dry-run` 失败。因此这里只落「快照 + 指针」，并把
    `requires_fresh_audit=True` 写成**数据**（"这份稿必须重审"），而不是靠人记。

    返回写入记录；**任何失败都不抛**（快照是保护性副作用，不能反过来打断提交）。
    """
    rec: dict = {"round": int(round_no), "path": "", "revision": "", "written": False,
                 "requires_fresh_audit": True, "error": ""}
    try:
        from shared.json_io import atomic_dump_json as _adj
        from shared.quality_gate import compute_script_revision as _csr
        import hashlib
        snap_dir = _mrt.OUTPUT_FILE.parent / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        path = snap_dir / ("%s%02d.json" % (REPAIR_ROUND_PREFIX, int(round_no)))
        _adj(path, script, ensure_ascii=False, indent=2)
        rev = _csr(script)
        # 读回核验：写盘成功但内容不是这份稿 = 更危险（后续会拿它当基线）。
        _back = json.loads(path.read_text(encoding="utf-8"))
        if _csr(_back) != rev:
            raise RuntimeError("快照读回 revision 不一致")
        rec.update({
            "path": str(path),
            "revision": rev,
            "written": True,
            "script_sha256": hashlib.sha256(
                json.dumps(script, ensure_ascii=False, sort_keys=True)
                .encode("utf-8")).hexdigest(),
            "audit_path": str((audit or {}).get("_path") or ""),
            "audit_revision": str((audit or {}).get("input_revision") or ""),
            "novel_chars": len(novel_text or ""),
        })
        _adj(snap_dir / REPAIR_ROUND_POINTER, rec, ensure_ascii=False, indent=2)
        # M2.3 / CP4：第 k 轮修复已完成——同一份稿同时进检查点索引
        from shared import step01_checkpoint as _ckpt
        try:
            rec["checkpoint"] = _ckpt.write(
                _mrt.OUTPUT_FILE.parent, _ckpt.CP4_REPAIR_ROUND_K_DONE, script=script,
                audit=audit, round_no=int(round_no),
                extra={"run_id": _mrt._step01_run_id()}, log=_mrt._logger)
        except Exception as _degrade_exc:  # noqa: BLE001
            from shared.degrade import note_degraded as _note_degraded
            _note_degraded("step01_script_gen/main.py:_persist_repair_round", _degrade_exc)
        # M-A2（2026-11-17）：写**双块恢复清单**——`audited`（旧审计只证明那一份稿）
        # + `pending_candidate`（本轮采纳稿，`requires_fresh_audit=true`）+ `next_action`。
        # 这样"从待复审稿恢复"是一条有据可查的路径，而不是靠人记"快照在 snapshots/"。
        try:
            from step01_script_gen import resume as _resume
            _aud_rev = str((audit or {}).get("input_revision") or "")
            _audited = None
            if _aud_rev:
                for _cand in sorted(snap_dir.glob("script_result_*.json"))[-20:]:
                    try:
                        _s = json.loads(_cand.read_text(encoding="utf-8"))
                        if isinstance(_s.get("script"), dict):
                            _s = _s["script"]
                        if _csr(_s) == _aud_rev:
                            _audited = _cand
                            break
                    except Exception:              # noqa: BLE001
                        continue
            _audit_path = str(os.getenv("STEP01_AUDIT_SOURCE") or "").strip()
            if not _audit_path:
                for _n in ("step01_audit.json", "step01_audit.blocked.json"):
                    _p = _mrt.OUTPUT_FILE.parent / _n
                    if _p.is_file():
                        _audit_path = str(_p)
                        break
            _novel_src = ""
            if novel_text:
                _nd = _mrt.OUTPUT_FILE.parent / "_resume"
                _nd.mkdir(parents=True, exist_ok=True)
                _ndig = hashlib.sha256(novel_text.encode("utf-8")).hexdigest()
                _np = _nd / ("novel_%s.txt" % _ndig[:12])
                if not _np.exists():
                    _np.write_bytes(novel_text.encode("utf-8"))
                _novel_src = str(_np)
            if _audited and _audit_path and _novel_src:
                _man = _resume.build_pending_manifest(
                    _mrt.OUTPUT_FILE.parent, audited_snapshot_path=_audited,
                    audit_path=_audit_path, pending_path=path,
                    round_no=int(round_no), novel_text_path=_novel_src)
                _mp = _mrt.OUTPUT_FILE.parent / _resume.MANIFEST_NAME
                _adj(_mp, _man, ensure_ascii=False, indent=2)
                rec["resume_manifest"] = {"path": str(_mp), "written": True,
                                          "next_action": _man.get("next_action")}
            else:
                rec["resume_manifest"] = {
                    "written": False,
                    "reason": ("缺少已审稿快照/审计/原文之一（audited=%s audit=%s novel=%s）"
                               % (bool(_audited), bool(_audit_path), bool(_novel_src)))}
        except Exception as _me:                   # noqa: BLE001
            rec["resume_manifest"] = {"written": False,
                                      "reason": "%s: %s" % (type(_me).__name__, _me)}
        try:
            _adj(snap_dir / REPAIR_ROUND_POINTER, rec, ensure_ascii=False, indent=2)
        except Exception:                          # noqa: BLE001
            pass
        _mrt._logger.info("[SNAPSHOT] 修复轮 %d 采纳稿已落盘（%s，revision=%s）——复审前即持久化"
                     % (int(round_no), path.name, rev[:12]))
    except Exception as _e:                       # noqa: BLE001
        rec["error"] = "%s: %s" % (type(_e).__name__, _e)
        try:
            _mrt._logger.warn("[SNAPSHOT] 修复轮 %d 采纳稿落盘失败（复审仍继续，但本次成果"
                         "不可恢复）: %s" % (int(round_no), _e))
        except Exception:
            pass
    return rec


def _paired_repair_budget() -> dict:
    """兼容壳：实现已迁到 ``repair_loop.paired_repair_budget``（M1，2026-11-18）."""
    from step01_script_gen.repair_loop import paired_repair_budget
    return paired_repair_budget()
