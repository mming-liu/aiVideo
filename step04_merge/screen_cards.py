# -*- coding: utf-8 -*-
"""step04_merge.screen_cards —— 卡片清单到 `OverlayEntry` 的**适配入口**（主专题 §6.3）.

职责边界：

- **Step01 生产来源关系**（`presentation.screen_cards` + `fact_bindings`）：计划里就有；
- **Step04 准备阶段生产资产与时窗**（`screen_card_plan.v1`）：本模块所在；
- **消费**：只产 `OverlayEntry`，最终时间线仍是 `step04_merge.timeline` 的单源。

本模块**不**解析 `presentation_contract` 的内部结构（那是 `shared/presentation_contract`
与 `shared/screen_cards/plan.py` 的责任），也**不**碰正式产物——资产落在当前 merge
attempt 的 `work/cards/`（路径单源 `shared.paths.screen_card_asset_dir`）。

失败语义（§7 矩阵）：

| 输入/故障 | 行为 |
|---|---|
| 新模式稿 + 没有卡片 | **合法**，不强制添加卡片（零叠加） |
| 旧模式稿 + 带卡片清单 | **拒收**（旧模式没有卡片合同） |
| `--screen-card-plan` 与当前稿不一致 | 拒收（逐项核 card_id/正文/绑定/profile） |
| 实际窗口短于阅读需求 | `presentation_window_insufficient`，候选失败，正式稿不变 |
| 新模式稿漏卡片输入且无法生产 | 拒收，**不导出缺字视频** |
"""
from __future__ import annotations

import json
from pathlib import Path

from shared.presentation_contract import MODE_LEGACY, MODE_HYBRID, plan_mode


class ScreenCardError(RuntimeError):
    """卡片链路契约违例（调用方按候选失败处理；**正式产物不换**）."""

    def __init__(self, code: str, reason: str, **extra):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.extra = extra

    def as_problem(self) -> dict:
        _p = {"code": self.code, "kind": "card", "path": "",
              "reason": self.reason, "detail": self.reason}
        _p.update(self.extra)
        return _p


def card_relations_for(script) -> tuple:
    """稿件 → 卡片来源关系 + 呈现模式 → `(relations, mode, problems)`.

    稿件里没有 `chapter_outline`（老稿/纯视频合并）⇒ 返回空关系 + 旧模式，**不是错误**。

    ⚠️ `problems` 非空 ⇒ **关系为空**，调用方必须按候选失败处理（准备阶段转
    `ScreenCardError`）。卡片消费**没有**"跳过坏项继续画"这一档：那会导出缺卡成片。
    """
    from shared.screen_cards.plan import card_relations
    _plan = (script or {}).get("chapter_outline")
    if not isinstance(_plan, dict) or not _plan:
        return [], MODE_LEGACY, []
    _mode = plan_mode(_plan)
    if _mode == MODE_HYBRID:
        _probs: list = []
        _rel = card_relations(_plan, problems=_probs)
        return _rel, MODE_HYBRID, _probs
    # 旧模式**不接受**卡片：清单里出现卡片就是合同冲突
    from shared.presentation_contract import PRESENTATION_SCHEMA
    _pres = _plan.get("presentation")
    _cards = (_pres or {}).get("screen_cards") if isinstance(_pres, dict) else None
    if isinstance(_cards, list) and _cards:
        return [], _mode, [{"code": "card_plan_on_legacy_script", "kind": "card",
                            "path": "presentation.screen_cards",
                            "reason": "当前稿是 %s 模式却有 %d 张文字卡——旧模式没有卡片"
                                      "合同（presentation.schema=%s）"
                                      % (_mode, len(_cards), PRESENTATION_SCHEMA),
                            "detail": "旧模式稿带卡片"}]
    if _cards is not None and not isinstance(_cards, list):
        # 旧模式的 `screen_cards` 不是数组：既不是"没有卡片"（那是省略或 `[]`），
        # 也不是"有卡片"——**读不出来就不猜**，交调用方按失败处理。
        return [], _mode, [{"code": "card_plan_field_type", "kind": "card",
                            "path": "presentation.screen_cards",
                            "reason": "`presentation.screen_cards` 必须是数组（现在是 %s）"
                                      % type(_cards).__name__,
                            "detail": "旧模式稿的卡片字段类型非法"}]
    return [], _mode, []


def _plan_digest_of_script(script) -> str:
    """**当前稿的来源计划摘要**——自动生成与外部清单**共用这一个函数**.

    复核 2026-10-05 P1：清单里的 `source_plan_digest` 此前只被"记下来"，没有和当前稿
    核过——改掉那个字段仍然通过。这里按**当前稿的呈现关系**实算：卡片清单必须指向
    这一份计划，`plan_revision`/`plan_verification` 等诊断元数据不参与（它们的变化
    不是内容变化）。
    """
    import hashlib as _hl
    _plan = (script or {}).get("chapter_outline")
    if not isinstance(_plan, dict) or not _plan:
        return ""
    _body = {k: v for k, v in _plan.items()
             if k not in ("plan_revision", "plan_verification", "plan_replan",
                          "plan_contract_version", "prop_state_candidates")}
    _t = json.dumps(_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    default=str)
    return _hl.sha256(_t.encode("utf-8")).hexdigest()


def _beat_to_shot(script, beat_uid: str):
    """节拍 uid → **镜头**（唯一映射链，工作包 3 第 2 条）.

    **稳定节拍 uid → 当前版本节拍编号（`beat_uids` 的数组位置）→ 实际分镜
    `index` → clip 的 kind 与稳定镜头身份**。

    ⚠️ 分镜里的镜号字段是 **`index`**，不是 `shot_index`（复核 2026-10-05 P1）。旧实现
    读 `shot_index` 得到 `None`，`.get(..., 0)` 又把它变成 **0**——于是卡片一律绑到
    「hook 或第 0 号镜头」，查不到就报"没有承载镜头"，而真正的原因是**读错了字段名**。
    这里显式只认 `index`；取不到就返回 `None` 并**结构化报出**，绝不默认成 0
    （默认 0 = 静默绑到 hook 上）。

    无承载镜头/旧计划引用/拆镜后缺映射都返回 `None`——**不按相似内容猜绑定**（§4）。
    """
    _pres = ((script or {}).get("chapter_outline") or {}).get("presentation") or {}
    _uids = [str(x) for x in (_pres.get("beat_uids") or [])]
    try:
        _idx = _uids.index(str(beat_uid))
    except ValueError:
        return None, 0
    _beat_no = _idx + 1                     # 数组位置 = 当前版本的展示编号 B{n}
    _shots = (script or {}).get("shots") or []
    _hits = []
    for _s in _shots:
        _refs = _s.get("plan_refs") or {}
        for _b in (_refs.get("beats") or []):
            try:
                if int(_b) == _beat_no:
                    _hits.append(_s)
            except (TypeError, ValueError):
                continue
    if len(_hits) != 1:
        # 0 = 无承载镜头；>1 = 该节拍被拆到多个镜头（第一版：取首个承载镜头，
        # 但**如实标记**，由调用方决定是不是"延续到后续镜头"那种需要分段的情况）
        return (_hits[0] if _hits else None), len(_hits)
    return _hits[0], 1


def _shot_index_of(shot) -> int | None:
    """分镜 → **镜号**（`index`）。取不到返回 `None`——**不默认 0**（0 是 hook）."""
    for _k in ("index", "shot_index"):
        _v = (shot or {}).get(_k)
        if _v is None:
            continue
        try:
            return int(_v)
        except (TypeError, ValueError):
            continue
    return None


def screen_card_overlays(script, options, provisional, *, attempt=None, scope=None,
                         script_revision=None, narration_contract_ok=True):
    """Step04 的卡片接线 → `(OverlayEntry 元组, manifest 或 None)`.

    流程（§7）：校验 Step01 提交与输入身份 → 布局与时窗预检 → 绘制资产 → 资产哈希清单
    → 合成。`options.screen_card_plan` 给了就**校验**它；没给就**从当前稿生产**——
    新模式稿漏掉这条会拒收，而不是静默合成无卡视频。

    ⚠️ **候选隔离先于一切副作用**（工作包 3 第 6 条）：本包**只交候选产物**。因此要
    画卡片（建目录、写 PNG、合成）就必须已有 attempt 上下文——没有就**在动手之前**
    拒收，而不是先写一堆资产再发现没有隔离位置。旧入口的**正式输出**行为保持不变
    （那是既有的 legacy 路径，本包不接管）。
    """
    from shared.screen_cards.plan import (build_card_plan, card_relations, overlay_entries,
                                          segment_overlays, validate_card_plan)
    from shared.screen_cards import profile as _profile
    from shared.screen_cards import render as _render

    _relations, _mode, _rp = card_relations_for(script)
    if _rp:
        # **code 取自生产者记录**，不再写死一个 `card_plan_on_legacy_script`：准入失败
        # （`view_item_type` / `view_binding_carriers_type`…）与"旧模式稿带卡片"是不同
        # 故障，写死一个码会让调用方和日志把"卡片关系读不出来"读成"模式冲突"。
        raise ScreenCardError(str(_rp[0].get("code") or "card_plan_invalid"),
                              "；".join(str(x.get("reason") or "") for x in _rp),
                              problems=_rp)
    if not narration_contract_ok:
        raise ScreenCardError("card_voice_contract_conflict",
                              "声音合同未通过核对，拒绝合成卡片（卡片与对白分流依赖同一份合同）")

    _plan_path = getattr(options, "screen_card_plan", None)
    _timeline = provisional
    _scope = str(scope or getattr(_timeline, "scope", "") or "")
    _rev = str(script_revision or getattr(_timeline, "script_revision", "") or "")
    # **来源计划摘要由代码按当前稿算**（复核 2026-10-05 P1：此前只信清单自报的
    # `source_plan_digest`，改掉那个字段仍能通过）。两条路（自动生成 / 外部清单）
    # 用**同一个**来源摘要 ⇒ 清单与当前稿必须指向同一份计划。
    _src_digest = _plan_digest_of_script(script)

    # ---- ① 外部清单：逐项核当前稿（身份 / 正文 / 绑定 / profile / 资产 / 阅读时长）----
    if _plan_path is not None:
        _given = json.loads(Path(_plan_path).read_text(encoding="utf-8"))
        _probs = validate_card_plan(_given, timeline=_timeline, relations=_relations,
                                    prof=_profile.active_profile(),
                                    source_plan_digest=_src_digest)
        if _probs:
            raise ScreenCardError(_probs[0].get("code") or "card_plan_invalid",
                                  "；".join(str(x.get("reason")) for x in _probs[:3]),
                                  problems=_probs)
        _entries, _ep = overlay_entries(_given, _timeline)
        return _entries, _given

    # ---- ② 没有外部清单：从当前稿生产 ----
    if not _relations:
        # 旧模式 / 无卡片稿 ⇒ **零叠加合法**（不是失败）
        return (), None
    # **候选隔离先于副作用**（工作包 3 第 6 条）：要画卡片就得有 attempt 上下文。
    # 这里在建目录/写 PNG **之前**判——否则会先写一堆资产再发现没有隔离位置。
    if attempt is None or not str(getattr(attempt, "root", "") or ""):
        raise ScreenCardError(
            "card_isolation_context_missing",
            "生成文字卡资产需要候选隔离上下文（--candidate-root / attempt），"
            "当前没有——本包**只交候选产物**，不写正式输出；请补上候选根后重跑",
            mode=_mode, cards=len(_relations))
    from shared.paths import screen_card_asset_dir
    _asset_dir = screen_card_asset_dir(getattr(attempt, "root", None),
                                       getattr(attempt, "experiment_id", None))
    _prof = _profile.active_profile()
    _assets: dict = {}
    _bound: list = []
    for _r in _relations:
        _hit, _n = _beat_to_shot(script, _r["beat_uid"])
        if _hit is None:
            raise ScreenCardError(
                "card_beat_unbound",
                "卡片 %s 的节拍 %s 在当前稿里没有承载镜头（%s）——按结构绑定，不按相似"
                "内容猜" % (_r["card_id"], _r["beat_uid"],
                            "该节拍被拆到多个镜头" if _n > 1 else "无 plan_refs 引用"),
                card_id=_r["card_id"], beat_uid=_r["beat_uid"], hits=int(_n))
        # **节拍 → 镜头**这一步只走结构（`beat_uids` 位置 + 镜头 `plan_refs`），
        # 不按内容相似度猜——`plan_refs` 属于明确的计划版本，猜了就绑到别的镜头上去。
        _shot_no = _shot_index_of(_hit)
        if _shot_no is None:
            # **不默认 0**：0 是 hook 的独立身份，静默绑上去等于把卡片放到钩子上
            raise ScreenCardError(
                "card_shot_index_missing",
                "承载卡片 %s 的分镜没有可用的镜号字段（期望 `index`）：%r"
                % (_r["card_id"], sorted((_hit or {}).keys()))[:200],
                card_id=_r["card_id"], beat_uid=_r["beat_uid"])
        _bound.append(dict(_r, shot_index=_shot_no))
    for _r in _bound:
        _a, _ap = _render.render_card(_r["text"], _r["template"], _r["card_id"],
                                      _asset_dir / _r["card_id"], profile=_prof,
                                      placement=_r["placement"])
        if _ap:
            raise ScreenCardError(_ap[0].get("code") or "card_render_failed",
                                  "；".join(str(x.get("reason")) for x in _ap),
                                  problems=_ap)
        _assets[_r["card_id"]] = _a
    _manifest, _mp = build_card_plan(
        _timeline, relations=_bound, assets=_assets,
        # 与外部清单**同一个**来源摘要函数（自动生成 / 外部清单必须指向同一份计划）
        source_plan_digest=_src_digest,
        script_revision=_rev, scope=_scope, prof=_prof, out_dir=_asset_dir)
    if _mp:
        # 时窗/布局不足 ⇒ 候选失败（**不自动加时、不慢放、不截字**）
        raise ScreenCardError(_mp[0].get("code") or "presentation_window_insufficient",
                              "；".join(str(x.get("reason")) for x in _mp[:3]),
                              problems=_mp)
    # 自产清单也过**同一个准入函数**（工作包 2 第 1 条：自动生成与外部清单共用判据）
    _probs = validate_card_plan(_manifest, timeline=_timeline, relations=_bound,
                                prof=_prof, source_plan_digest=_src_digest)
    if _probs:
        raise ScreenCardError(_probs[0].get("code") or "card_plan_invalid",
                              "；".join(str(x.get("reason")) for x in _probs[:3]),
                              problems=_probs)
    _entries, _ep = overlay_entries(_manifest, _timeline)
    return _entries, _manifest


__all__ = ["ScreenCardError", "card_relations_for", "screen_card_overlays"]