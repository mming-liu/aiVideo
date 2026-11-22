# -*- coding: utf-8 -*-
# <split:generated>
"""step01_script_gen.orchestrator.repair_apply —— 补丁与替换镜头的应用、字段写入审计.

顶层语句原样迁自 `step01_script_gen/main.py`（2026-11-19 S00–S10 拆分）。
跨模块引用一律经模块属性访问（如 `_rt.X`），测试对
`step01_script_gen.main.X` 的打桩由门面（`orchestrator/_facade.py`）转发到这里。
"""
from __future__ import annotations

import re

from step01_script_gen.orchestrator import runtime as _mrt
# </split:generated>

# ---- 以下顶层语句原样迁自 step01_script_gen/main.py（tools/split_step01_main.py 生成）----
def _build_novel_slice_for_picked(picked: dict, shots_by_id: dict,
                                   novel_text: str, ctx: int = 200) -> str:
    """08-20 mode B：按 picked 镜的 narration_segment 切原文片段，前后各扩 ctx 字。

    返回拼接好的字符串（带 shot_xx 标头），多个 picked 镜的切片按时间序合并（重叠自动合并）。
    """
    if not picked or not novel_text:
        return ""

    # 1) 收集 picked 镜的 (shot_id, narration) 列表
    items = []
    for tid in picked:
        if tid == "global":
            continue
        s = shots_by_id.get(tid)
        if not s:
            continue
        narr = (s.get("narration_segment") or s.get("narration") or "").strip()
        if not narr:
            continue
        items.append((tid, narr, s.get("index")))

    if not items:
        return ""

    # 2) 每个 narration 在 novel 里定位 → (start, end) → 扩 ctx
    slices = []
    for tid, narr, idx in items:
        s, e = _mrt._locate_narration_in_novel(narr, novel_text)
        if s < 0:
            continue
        s2 = max(0, s - ctx)
        e2 = min(len(novel_text), e + ctx)
        slices.append((s2, e2, tid, idx))

    if not slices:
        return ""

    # 3) 按 start 排序 + 重叠合并（间隔 < 100 字视为连续）
    slices.sort(key=lambda x: x[0])
    merged = [slices[0]]
    for cur in slices[1:]:
        last = merged[-1]
        if cur[0] <= last[1] + 100:  # 重叠或相邻
            new_end = max(last[1], cur[1])
            new_ids = f"{last[2]},{cur[3]}"
            merged[-1] = (last[0], new_end, last[2] + "+" + cur[2], None)
        else:
            merged.append(cur)

    # 4) 输出：每个合并段打 shot_ids 标头 + 原文片段
    out_lines = []
    for s, e, ids, _ in merged:
        seg = novel_text[s:e].replace("\n", " ").strip()
        if len(seg) > 1500:  # 单段过长截断
            seg = seg[:1500] + "..."
        out_lines.append(f"### 原文片段（{ids}）\n{seg}\n")

    total_len = sum(len(l) for l in out_lines)
    # 防止 prompt 爆炸：超 6k 字截断（理论上 picked 6 镜 × ~400 字 = 2.4k）
    if total_len > 6000:
        return out_lines[0] + "\n...（后续切片省略，控制总长 6k 字以内）..."
    return "\n".join(out_lines)


_PATCH_ALLOWED_RE = re.compile(
    r"^(prompt|ff_desc|lf_desc|motion_desc|color_tone)$"
    r"|^keyframes\[\d+\]\.(desc|t_sec|stage)$"
    r"|^keyframes\.append$"
    r"|^frame_layouts\.(ff|lf)\[\d+\]\.(pose|gaze_target|position|on_screen|surface|props)$"
    r"|^frame_layouts\.kfs\[\d+\]\[\d+\]\.(pose|gaze_target|position|on_screen|surface|props)$"
    r"|^frame_layouts\.kfs\.append$"
    r"|^char_layout\[\d+\]\.position$"
)


def _patch_tokens(field_path: str) -> list:
    tokens = []
    for part in str(field_path or "").split("."):
        m = re.match(r"^([A-Za-z_]+)((?:\[\d+\])*)$", part)
        if not m:
            return []
        tokens.append(m.group(1))
        tokens.extend(int(x) for x in re.findall(r"\[(\d+)\]", m.group(2)))
    return tokens


def _locked_signature_diff(before_sig: str, after_sig: str, *, limit: int = 8) -> list:
    """两个 `_locked_repair_signature` JSON 之间的**字段级**差异.

    v6.94：`locked_changed` 是第九次重跑后修复轮**唯一**的绑定闸门，而它的判据是
    「7 个字段的整串 JSON 变了没有」——不看变了什么、也不看本轮授权了什么。
    这里给出 `镜头.字段: 旧 → 新`，与 `_shot_field_diff` 同一做法：**先拿到字段名，
    再决定修法**，不猜根因。
    """
    import json as _json

    try:
        b = {str(x.get("id")): x for x in _json.loads(before_sig)}
        a = {str(x.get("id")): x for x in _json.loads(after_sig)}
    except Exception:
        return []
    out: list = []
    for sid in sorted(set(b) | set(a), key=str):
        sb, sa = b.get(sid), a.get(sid)
        if not isinstance(sb, dict) or not isinstance(sa, dict):
            out.append("%s: 新增/删除" % sid)
            if len(out) >= limit:
                return out
            continue
        for f in sorted(set(sb) | set(sa)):
            if f == "id":
                continue
            if sb.get(f) != sa.get(f):
                out.append("%s.%s: %r → %r" % (sid, f, sb.get(f), sa.get(f)))
                if len(out) >= limit:
                    return out
    return out


#: 修复轮**实际执行**的锁定契约——`_locked_repair_signature` 逐字取自这几项。
#: v6.149：**修复可执行性预检**必须用它，不能只读 `_FINAL_LOCKED_FIELDS`——后者对
#: 「已授权字段」有例外，而轮级签名照旧锁住这几项（实测：审计把 `narration_segment`
#: 列为目标 → 授权通过 → 修复改了它 → `locked_changed` 回退，白花一轮修复调用）。
_ROUND_LOCKED_SHOT_FIELDS = ("duration_sec", "narration_segment", "subtitle",
                             "scene", "location", "variation_type")


def _locked_repair_signature(script: dict) -> str:
    import json as _json
    out = []
    units = []
    if isinstance(script.get("hook"), dict):
        units.append(("hook", script["hook"]))
    units.extend((str(s.get("index")), s) for s in (script.get("shots") or []))
    for sid, shot in units:
        if not isinstance(shot, dict):
            continue
        rec = {"id": sid, "index": shot.get("index")}
        for f in _ROUND_LOCKED_SHOT_FIELDS:      # 轮级锁定契约：单源
            rec[f] = shot.get(f)
        out.append(rec)
    return _json.dumps(out, ensure_ascii=False, sort_keys=True)


def _rescale_shot_timeline(shot: dict, old_d: float, new_d: float) -> None:
    """把镜内时间轴按 `new_d / old_d` 等比缩放（只动时间标签，不改任何文字描述）.

    2026-11-07（D4）：`duration_budget` 的合法修法是缩短镜头，但只改 `duration`
    会让 `motion_desc` 的时段终点与 `keyframes[*].t_sec` 越过新镜长，立刻触发
    `action_time_out_of_range` / `kf_time_out_of_shot`（后者是 HARD）——修一条换两条。
    这里在**已授权 duration** 的补丁落地后统一缩放：时段与帧保持相对位置，文字不动。
    """
    if old_d <= 0 or new_d <= 0 or new_d >= old_d:
        return
    _k = float(new_d) / float(old_d)
    import re as _re

    def _scale(m):
        _a, _b = float(m.group(1)), float(m.group(2))
        return "%g-%g秒" % (round(_a * _k, 2), round(_b * _k, 2))

    _motion = str(shot.get("motion_desc") or "")
    if _motion:
        shot["motion_desc"] = _re.sub(
            r"(\d+(?:\.\d+)?)\s*[-－–~～至到]\s*(\d+(?:\.\d+)?)\s*秒", _scale, _motion)
    for _kf in (shot.get("keyframes") or []):
        if isinstance(_kf, dict) and _kf.get("t_sec") is not None:
            try:
                _kf["t_sec"] = round(float(_kf["t_sec"]) * _k, 2)
            except (TypeError, ValueError):
                pass
    shot["duration_sec"] = round(float(new_d), 2)


def _split_executable_allowed(allowed) -> tuple:
    """把某目标的授权字段拆成 `(可执行字段, 命中的锁定字段)`（v6.149 可执行性预检）.

    修复轮**实际执行**的锁定契约是 `_ROUND_LOCKED_SHOT_FIELDS`（轮级签名），比
    `_FINAL_LOCKED_FIELDS` 严：后者对「已授权字段」有例外，前者没有。授权面里出现
    锁定字段 = 这条问题**最终修复不可执行**——预检据此把它摘出来（保留 blocking、
    交上游阶段），而不是让它进修复调用再被 `locked_changed` 回退。
    """
    _locked = {str(f) for f in _ROUND_LOCKED_SHOT_FIELDS}
    _all = [str(a) for a in (allowed or [])]
    return [a for a in _all if a not in _locked], sorted(set(_all) & _locked)


def _apply_repair_patches(script: dict, patches: list, picked: set[str]) -> tuple[dict, int]:
    """Apply narrow field patches only to picked shots; reject unknown paths.

    ⚠️ **已停用，禁止在运行时自动切回**（决定 2，2026-09-20）。

    修复轮自 v6.78 起改为 `_apply_shot_replacements`（完整镜头事务）。
    `shot_replacements[]` 是**唯一继续演进**的修复契约；模型输出不合格时保留
    修复前镜头、记录拒绝原因并进入完整镜头 regen——**不得**自动回退到本函数。
    旧字段补丁恰好重新引入本轮要消除的字段不一致与"审计后语义写入"，
    不能作为生产降级方案（见 implementation-review 决定 2）。

    保留本函数仅因还有单测引用，跑批验证通过后应连同
    `_PATCH_ALLOWED_RE` / `REPAIR_PATCH_SCHEMA` / test_repair_patches.py 一并删除。
    """
    import copy as _copy
    merged = _copy.deepcopy(script)
    applied = 0
    for patch in patches or []:
        if not isinstance(patch, dict):
            continue
        sid = str(patch.get("shot_id") or "").strip()
        field_path = str(patch.get("field_path") or "").strip()
        value = patch.get("value")
        if not sid or not field_path or not _PATCH_ALLOWED_RE.match(field_path):
            continue
        target = None
        if sid in ("0", "hook"):
            if "hook" in picked:
                target = merged.get("hook")
        else:
            # Patches are scoped to explicitly picked shots.  A valid field on
            # an unpicked shot must still be rejected.
            if f"shot_{sid}" not in picked:
                continue
            for shot in merged.get("shots", []):
                if str(shot.get("index")) == sid:
                    target = shot
                    break
        if not isinstance(target, dict):
            continue
        import json as _patch_json_value
        if field_path == "keyframes.append":
            try:
                item = value if isinstance(value, dict) else _patch_json_value.loads(str(value))
            except Exception:
                continue
            if not isinstance(item, dict) or not str(item.get("desc") or "").strip():
                continue
            try:
                t_sec = float(item.get("t_sec"))
            except (TypeError, ValueError):
                continue
            try:
                duration = float(target.get("duration_sec") or target.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if t_sec < 0 or (duration > 0 and t_sec > duration + 0.01):
                continue
            item = dict(item)
            item["t_sec"] = round(t_sec, 3)
            kfs = target.setdefault("keyframes", [])
            if not isinstance(kfs, list):
                continue
            kfs.append(item)
            kfs.sort(key=lambda k: float(k.get("t_sec") or k.get("time_ratio") or 0)
                     if isinstance(k, dict) else 0)
            applied += 1
            continue
        if field_path == "frame_layouts.kfs.append":
            try:
                item = value if isinstance(value, list) else _patch_json_value.loads(str(value))
            except Exception:
                continue
            if not isinstance(item, list) or not item:
                continue
            if not all(isinstance(x, dict) and str(x.get("name") or "").strip()
                       for x in item):
                continue
            layout = target.setdefault("frame_layouts", {})
            if not isinstance(layout, dict):
                continue
            layout.setdefault("kfs", []).append(item)
            applied += 1
            continue
        tokens = _patch_tokens(field_path)
        if not tokens:
            continue
        cur = target
        ok = True
        for tok in tokens[:-1]:
            try:
                cur = cur[tok]
            except Exception:
                ok = False
                break
        if not ok:
            continue
        last = tokens[-1]
        if not isinstance(cur, dict) or last not in cur:
            continue
        if last == "on_screen":
            raw_value = str(value).strip().lower()
            if raw_value not in ("1", "0", "true", "false", "yes", "no", "是", "否"):
                continue
            value = raw_value in ("1", "true", "yes", "是")
        elif last == "props":
            try:
                value = value if isinstance(value, list) else _patch_json_value.loads(str(value))
            except Exception:
                value = [x.strip() for x in str(value).split(",") if x.strip()]
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                continue
            value = [x.strip() for x in value if x.strip()]
        elif last == "t_sec":
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            try:
                duration = float(target.get("duration_sec") or target.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if value < 0 or (duration > 0 and value > duration + 0.01):
                continue
            value = round(value, 3)
        elif last in ("desc", "stage", "pose", "gaze_target", "position", "surface",
                      "prompt", "ff_desc", "lf_desc", "motion_desc", "color_tone"):
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value or len(value) > 2000:
                continue
            if last == "stage":
                from shared.kf_time import KF_STAGE_ENUM
                if value not in KF_STAGE_ENUM:
                    continue
            if last == "position":
                from step01_script_gen.scene_anchors import is_valid_position
                if not is_valid_position(
                    value, str(target.get("location") or target.get("scene") or "")
                ):
                    continue
        else:
            continue
        cur[last] = value
        applied += 1
    return merged, applied


# ===========================================================================
# v6.77（第四阶段）：修复轮「完整镜头事务」
#
# 旧路径（`_apply_repair_patches`）收字段级补丁，结构字段之间可以互不一致
# （补了 motion 没补 keyframes、补了 lf 没补 frame_layouts.kfs），且之后还要跑
# post_fix/hard_fix/timecode 去兜——接受判定比较的是「补丁 + 全部后处理副作用」，
# 后处理引入的新阻断会把本该通过的局部修复整轮拖回滚。
#
# 新路径收**完整镜头对象**，在副本里只校验该镜与其相邻边界，通过后原子替换；
# 未点名的镜头逐字节不动（见 `_repair_acceptance_decision` 的
# `non_target_changed` 判据）。本阶段同时**不再**在修复轮里跑全局拆镜
# （`_compute_timecodes(..., allow_split=False)`）。
# ===========================================================================
_REPLACEMENT_REQUIRED_KEYS: tuple = (
    "index", "narration_segment", "subtitle", "prompt", "motion_desc",
    "ff_desc", "lf_desc", "duration", "keyframes", "frame_layouts",
)
# 替换镜头的旁白允许比原镜长出的比例上限（「原文引用未越界」的代理判据；
# 完整原文锚定仍由 validation._check_grounding 在下游把关）
_REPLACEMENT_NARR_GROWTH = 1.5


def _validate_replacement_shot(orig: dict | None, new: dict, *,
                                is_hook: bool = False,
                                allowed: set | None = None) -> str:
    """校验一条「完整镜头替换」；返回空串=通过，否则返回拒绝原因（给日志）.

    `is_hook=True` 时按 `_HOOK` 的字段要求校验（hook 无 scene/location）。

    `allowed` 是本轮**授权修改的字段路径集合**（由该 target 的 warning/audit
    issue 推导，见 `repair_fields.allowed_repair_fields`）。给定后，替换镜头与
    原镜做 canonical 路径 diff，除授权路径与纯派生字段外任何差异都拒绝——
    二次审核发现 2：只锁一份**固定** mask 挡不住「修 prompt 时顺带改 motion/
    `temporal_plan`/KF」，那些改动虽被 revision 哈希记录，却没有得到授权。
    """
    from shared.kf_time import KF_STAGE_ENUM

    if not isinstance(new, dict):
        return "shot 不是对象"
    _req = _REPLACEMENT_REQUIRED_KEYS
    if is_hook:
        # hook 用 _HOOK：无 scene/location（那是普通镜头字段）
        _req = tuple(k for k in _req if k not in ("scene", "location"))
    missing = [k for k in _req if k not in new]
    if missing:
        return f"结构字段缺失（必须成套出现）: {missing}"
    if orig is not None and str(new.get("index")) != str(orig.get("index")):
        return f"index 被改动（{orig.get('index')} -> {new.get('index')}）"

    try:
        dur = float(new.get("duration_sec") or new.get("duration") or 0)
    except (TypeError, ValueError):
        return "duration 非数值"
    if dur <= 0:
        return "duration <= 0"
    if dur > 30.0:
        return f"duration {dur:.1f}s 异常偏大"

    # v6.80（发现 9 #2）：`duration_sec` 由**唯一纯派生函数**
    # （timecode.write_timeline_from_durations）从 `duration` 生成，
    # 替换镜头不得自带一个与之不一致的 `duration_sec`——两种口径并存会让
    # 「prompt 禁止改 duration_sec」与「schema 只强制 duration」互相矛盾。
    if "duration_sec" in new:
        try:
            ds = float(new.get("duration_sec"))
        except (TypeError, ValueError):
            return "duration_sec 非数值"
        try:
            dd = float(new.get("duration"))
        except (TypeError, ValueError):
            return "duration 非数值"
        if abs(ds - dd) > 0.011:
            return (f"duration_sec({ds:g}) 与 duration({dd:g}) 不一致——"
                    "duration_sec 由时间轴序列化函数统一派生，不得自带第二种口径")

    kfs = new.get("keyframes")
    if not isinstance(kfs, list):
        return "keyframes 不是数组"
    for j, kf in enumerate(kfs):
        if not isinstance(kf, dict):
            return f"keyframes[{j}] 不是对象"
        if not str(kf.get("desc") or "").strip():
            return f"keyframes[{j}].desc 为空"
        if str(kf.get("stage") or "") not in KF_STAGE_ENUM:
            return f"keyframes[{j}].stage 非法: {kf.get('stage')!r}"
        try:
            t = float(kf.get("t_sec"))
        except (TypeError, ValueError):
            return f"keyframes[{j}].t_sec 非数值"
        if t <= 0 or t > dur + 0.01:
            return f"keyframes[{j}].t_sec={t} 越出镜长 {dur}"

    layouts = new.get("frame_layouts")
    if not isinstance(layouts, dict):
        return "frame_layouts 不是对象"
    lkfs = layouts.get("kfs")
    if not isinstance(lkfs, list):
        return "frame_layouts.kfs 不是数组"
    if len(lkfs) != len(kfs):
        return f"frame_layouts.kfs 长度 {len(lkfs)} ≠ keyframes 长度 {len(kfs)}"

    if orig is not None:
        for fld in ("narration_segment", "subtitle"):
            old_len = len(str(orig.get(fld) or ""))
            new_len = len(str(new.get(fld) or ""))
            if old_len and new_len > old_len * _REPLACEMENT_NARR_GROWTH + 10:
                return (f"{fld} 长度 {old_len} -> {new_len}，超出 "
                        f"{_REPLACEMENT_NARR_GROWTH}x 增长上限（原文引用可能越界）")
        # v6.83（二次审核发现 2）：**按 issue 授权**——只有被问题点名的字段可改。
        if allowed is not None:
            from step01_script_gen.repair_fields import check_replacement_authorized
            reason = check_replacement_authorized(orig, new, allowed)
            if reason:
                return reason
    return ""


def _norm_target_id(raw) -> str:
    """把 target id 归一到 applier 的裸 id 口径（`shot_10` → `10`，`hook` 原样）.

    v6.84：修复轮的 REPAIR FEEDBACK 是按 `shot_<id>` 分组的（`_feedback_json["shots"]`
    的键），模型很自然地照抄这个格式回填 `shot_id`——而 applier 只认裸 `10`。实测
    2026-09-21 重跑：模型返回 `{"shot_id": "shot_10"}`，被 `_find()` 判成「未被点名
    或不存在」，整批（含合法的 hook 替换）一起回滚，`resolved=0`。

    **反馈格式在教模型一个 applier 不认的写法**——修 applier 一侧（宽容两种写法），
    而不是只改 prompt 措辞（模型仍会照抄分组键）。
    """
    sid = str(raw or "").strip()
    if sid.startswith("shot_"):
        sid = sid[5:]
    return sid


def _assemble_keyframe_additions(target: dict, additions,
                                 *, frame_mapping_out: dict | None = None) -> tuple:
    """把「帧 + 对应布局」的**成对数据**装配进完整镜头 → `(new_shot, 拒绝原因)`.

    S02（2026-11-15）：可选、仅关键字的 `frame_mapping_out` 就地写入**帧来源映射**
    （`before_count/after_count/old_to_new/added_indices`）。返回元组保持不变。
    身份由**装配前的下标**贯穿：`_sources[j]` 记候选第 j 帧来自哪个原帧，
    `None` 表示本轮新增。时间字段只用来定插入点，不参与身份判定。
    仅在装配成功时写入；中途失败由调用方作废候选、映射与效果。

    2026-09-27（用户决定）：缺段这类**联动**修复不再要求模型返回一个自洽的完整
    镜头。实测模型会把 `keyframes` 加到 4 而 `frame_layouts.kfs` 留在 3 →
    长度校验把整条替换拒收（两轮都没修成）。改为模型只给**成对的两半**
    `{"frame": {...}, "layout": [...]}`，由代码按 `t_sec` 的时间顺序把两半装进
    **同一声序号**。

    **代码只做配对与装配**：不从邻帧或 `motion_desc` 猜测人物姿态/持物等布局
    语义（那样的回填等于伪造布局）。缺任一半 → 拒收并记明原因。
    """
    # S02 补正：输出容器按**单次调用**初始化，失败目标不留上一轮/上一目标的旧映射。
    if isinstance(frame_mapping_out, dict):
        frame_mapping_out.clear()
    if not isinstance(additions, list) or not additions:
        return None, "keyframe_additions 必须是非空列表"
    import copy as _copy

    from shared.kf_time import get_kf_t_sec

    out = _copy.deepcopy(target)
    kfs = out.get("keyframes")
    if not isinstance(kfs, list):
        return None, "目标镜头没有 keyframes 列表"
    _fl = out.get("frame_layouts")
    lkfs = _fl.get("kfs") if isinstance(_fl, dict) else None
    if not isinstance(lkfs, list):
        return None, "目标镜头没有 frame_layouts.kfs 列表"
    # S02：装配前的原帧来源序号；与 kfs/lkfs 同步插入，保持三表同长。
    _before_count = len(kfs)
    _sources: list = list(range(_before_count))
    try:
        dur = float(out.get("duration_sec") or out.get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0

    for i, item in enumerate(additions):
        if not isinstance(item, dict):
            return None, f"keyframe_additions[{i}] 不是对象"
        frame, layout = item.get("frame"), item.get("layout")
        if not isinstance(frame, dict):
            return None, f"keyframe_additions[{i}] 缺 frame——必须与 layout **成对**给出"
        if not isinstance(layout, list):
            return None, f"keyframe_additions[{i}] 缺 layout——必须与 frame **成对**给出"
        if not str(frame.get("desc") or "").strip():
            return None, f"keyframe_additions[{i}] 的 frame 缺 desc"
        t = get_kf_t_sec(frame, out)
        if t is None:
            return None, f"keyframe_additions[{i}] 的 frame 缺可解析的 t_sec/time_ratio"
        if dur > 0 and not (0 < t <= dur + 0.01):
            return None, f"keyframe_additions[{i}] t_sec={t:g} 越出镜长 {dur:g}"
        # 按**时间顺序**定位插入点（与 keyframes 的升序约定一致），
        # `frame` 与 `layout` 插到**同一个下标**——配对由代码保证。
        idx = len(kfs)
        for j, kf in enumerate(kfs):
            tj = get_kf_t_sec(kf, out)
            if tj is not None and t < tj:
                idx = j
                break
        kfs.insert(idx, frame)
        lkfs.insert(idx, layout)
        _sources.insert(idx, None)      # 新增帧没有原帧来源

    if len(kfs) != len(lkfs):
        return None, ("装配后 keyframes（%d 项）与 frame_layouts.kfs（%d 项）长度不等——"
                      "整表替换 frame_layouts 时 kfs 必须与 keyframes 一一同序对应"
                      "（kfs[i] 是第 i 个关键帧的布局）；缺帧/缺段请用 "
                      "keyframe_additions（frame 与 layout 成对）补，不要整表重写 kfs"
                      % (len(kfs), len(lkfs)))
    if isinstance(frame_mapping_out, dict):
        _old_to_new: list = [None] * _before_count
        _added: list = []
        for _j, _src in enumerate(_sources):
            if _src is None:
                _added.append(_j)
            else:
                _old_to_new[_src] = _j
        frame_mapping_out.update({
            "before_count": _before_count,
            "after_count": len(_sources),
            "old_to_new": _old_to_new,
            "added_indices": _added,
            "source": "assembly",
        })
    return out, ""


def _repair_answer_example(shots_by_id: dict, picked, allowed_by_target=None,
                           compact: bool = False) -> str:
    """Render a valid answer envelope from the actual authorized objects.

    `compact=True`（S03 生产路径）：只给**结构规格**——shot_id、允许改的字段名与
    占位值；不再把整份镜头对象抄进示例。理由：镜头正文已经在 INPUT 1 里，示例再抄
    一遍只是让请求变大，并诱导模型把原值原样回抄（等于没改）。
    """
    import json
    answer = {"shot_replacements": []}
    for target_id in picked:
        if target_id not in shots_by_id:
            continue
        replacement = {"shot_id": str(target_id).removeprefix("shot_"), "shot": shots_by_id[target_id]}
        if allowed_by_target is not None and compact:
            # M02：由**同一份结构化授权记录**生成示例。三条旧缺陷一并修掉：
            # ①不再把精确叶子路径提升为顶层父字段；②占位值按当前稿件结构取
            # 类型；③补帧许可复用守卫的权威判断 `_whole_kfs_authorized`。
            from step01_script_gen.repair_authorization import (
                repair_authorization_spec)
            _spec = repair_authorization_spec(
                shots_by_id[target_id],
                allowed_by_target.get(target_id, set()))
            replacement = {"shot_id": replacement["shot_id"]}
            if _spec["writable_paths"]:
                replacement["updates"] = [
                    {"field_path": _p, "value": _spec["placeholders"][_p]}
                    for _p in _spec["writable_paths"]]
            if _spec["keyframe_additions_allowed"]:
                replacement["keyframe_additions"] = [{
                    "frame": {"t_sec": "<该缺段中点（秒）>",
                              "stage": "pre|approach|climax|react|exit|after",
                              "desc": "<描写该时段自己的动作，不要复述别的时段>"},
                    "layout": [{"name": "<该帧在场角色>", "on_screen": True,
                                "pose": "<姿态>", "props": [],
                                "support": {"id": "<支撑物>", "kind": "<类型>",
                                            "surface": "<表面>"},
                                "states": ["<状态>"]}],
                }]
        elif allowed_by_target is not None:
            from step01_script_gen.repair_fields import path_allowed
            updates = []
            allowed = allowed_by_target.get(target_id, set())
            # 2026-11-06：缺帧/缺段的授权面里有**整个 `keyframes`** 时，示例必须
            # 给「成对补帧」而不是整表覆盖——整表 `updates` 会诱导模型手抄
            # keyframes 却漏掉同序号的 frame_layouts.kfs（两表长度不等，整条拒收）。
            wants_kf = "keyframes" in allowed
            def _kf_path(path: str) -> bool:
                return (path == "keyframes" or path.startswith("keyframes.")
                        or path == "frame_layouts.kfs"
                        or path.startswith("frame_layouts.kfs."))
            def visit(value, path):
                if path and path_allowed(path, allowed):
                    if wants_kf and _kf_path(path):
                        return
                    updates.append({"field_path": path, "value": value})
                elif isinstance(value, dict):
                    for key, child in value.items():
                        visit(child, f"{path}.{key}" if path else key)
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        visit(child, f"{path}.{index}")
            visit(shots_by_id[target_id], "")
            replacement = {"shot_id": replacement["shot_id"]}
            if updates:
                replacement["updates"] = updates
            if wants_kf:
                replacement["keyframe_additions"] = [{
                    "frame": {"t_sec": "<该缺段中点（秒）>",
                              "stage": "pre|mid|after",
                              "desc": "…描写该时段自己的动作，不要复述别的时段…"},
                    "layout": [{"name": "…该帧在场角色…", "on_screen": True,
                                "pose": "…", "props": [],
                                "support": {"id": "…", "kind": "…", "surface": "…"},
                                "states": ["…"]}],
                }]
        if target_id == "hook":
            answer["hook_replacement"] = replacement
        else:
            answer["shot_replacements"].append(replacement)
    return json.dumps(answer, ensure_ascii=False, default=str)


def _duplicate_keyframe_additions(base: dict, additions) -> str:
    """同一响应里"整表替换"与"追加同一帧"并存 → 装配前拒绝（2026-11-07 实测）.

    round 3 的响应同时给了 updates.keyframes（1、3.5、4.5）和 keyframe_additions
    （4.5），四个追加帧都逐对象存在于替换表里；应用器先替换再追加 → 重复事件 +
    重复时刻（kf_event_duplicate / kf_time_not_strict），整轮被新阻塞回滚。
    代码只在**同一时刻重复**这一确定判据上拒绝，不猜语义。
    """
    from shared.kf_time import get_kf_t_sec
    times = set()
    for kf in (base.get("keyframes") or []):
        if not isinstance(kf, dict):
            continue
        t = get_kf_t_sec(kf, base)
        if t is not None:
            times.add(round(float(t), 3))
    for i, item in enumerate(additions or []):
        if not isinstance(item, dict):
            continue
        frame = item.get("frame")
        if not isinstance(frame, dict):
            continue
        t = get_kf_t_sec(frame, base)
        if t is None:
            continue
        if round(float(t), 3) in times:
            return ("keyframe_additions[%d] 与已存在帧时刻重复（t_sec=%g）——"
                    "同一响应里不得既整表替换又追加同一帧；请只用其中一种表达"
                    % (i, float(t)))
    return ""


def _apply_shot_replacements(script: dict, replacements: list,
                             picked: set[str],
                             hook_replacement: dict | None = None,
                             allowed_by_target: dict | None = None,
                             expected_targets: set | None = None,
                             *, frame_mappings_out: dict | None = None,
                             rejections_out: list | None = None) -> tuple[dict, int, list]:
    """把「完整镜头替换」**逐目标**应用到被点名的镜头（v6.86 用户决定 A1）.

    语义（v6.86 起）：

      - **逐目标提交**：每个通过单镜校验（字段授权 / 越权路径 diff / 锁定字段 /
        结构校验）的目标**独立生效**；某个目标缺项、重复或越权只影响它自己，
        不再否决同批其他合法目标。缺项作为**如实回报**进入 `rejected`。
      - **hook 独立承载**：hook 用 `_HOOK` 而非 `_SHOT`，字段要求不同，由
        `hook_replacement` 单独传入；塞进 `shot_replacements` 即拒。
      - 只替换被点名的镜头；未点名镜头逐字节不动。
      - 批次级安全网（全局完整性 / 非目标 revision / 阻塞退化 / 锁定字段签名）
        仍在**修复轮调用侧**，本函数不负责。

    v6.83 的「整批全有或全无」已被 A1 取代：实测 2026-09-21 22:21:22，模型漏回
    一个目标导致含合法替换的整批回滚、修复轮产出 0。

    Returns:
        (merged_script, 成功替换数, 拒绝原因列表)；
        一个都没提交时返回**原脚本**且计数为 0

    M03：`rejections_out` 给定时额外导出**生产者分类**的结构化拒收记录
    （`target/phase/code/field_path/allowed_paths/retryable/details`）。自由文本
    仍留在 `rejected` 里供日志与台账展示，分类判据一律不读文本。
    """
    import copy as _copy
    # S02 补正：映射输出容器按单次调用初始化；失败目标不残留上一次调用的结果。
    if isinstance(frame_mappings_out, dict):
        frame_mappings_out.clear()
    if isinstance(rejections_out, list):
        rejections_out.clear()
    merged = _copy.deepcopy(script)
    rejected: list[str] = []
    staged: list[tuple[dict, dict, str]] = []
    _seen_ids: set = set()
    from step01_script_gen.repair_feedback import (
        PHASE_APPLY, PHASE_ENGINEERING, feedback_record)

    def _reject(code, text, *, sid="", field_path="", allowed=None,
                phase=PHASE_APPLY, details=None) -> None:
        """拒收的**唯一**出口：字符串供展示，结构化记录供 M03 判据."""
        rejected.append(str(text))
        if isinstance(rejections_out, list):
            _tgt = "hook" if str(sid) in ("0", "hook") else (
                "shot_%s" % sid if str(sid) else "")
            rejections_out.append(feedback_record(
                code=code, target=_tgt, phase=phase,
                field_path=field_path,
                allowed_paths=sorted(str(a) for a in (allowed or ())),
                details=details or {}, text=str(text)))

    def _first_path(raw) -> str:
        """从答卷的**结构化数据**里取首个提交路径（不解析错误文本）."""
        if isinstance(raw, dict):
            for k in raw:
                return str(k)
        if isinstance(raw, list):
            for it in raw:
                if isinstance(it, dict) and it.get("field_path"):
                    return str(it["field_path"])
                if isinstance(it, dict) and "frame" in it:
                    return "keyframe_additions[].frame"
        return ""
    # S02：帧来源映射按目标导出，只在本目标通过装配/授权合并/替换校验后写入。
    from step01_script_gen.repair_fields import (
        _norm_keep_index, compose_frame_mappings,
        derive_frame_mapping_from_keyframes, identity_frame_mapping,
        validate_frame_mapping, verify_frame_identity_by_authorized_leaves)
    _stage_mappings: dict = {}

    def _find(sid: str):
        if sid in ("0", "hook"):
            return merged.get("hook") if "hook" in picked else None
        if f"shot_{sid}" not in picked:
            return None
        for sh in merged.get("shots", []):
            if isinstance(sh, dict) and str(sh.get("index")) == sid:
                return sh
        return None

    def _stage(item: dict, *, force_hook: bool = False) -> None:
        if not isinstance(item, dict):
            _reject("shape_invalid", "条目不是对象")
            return
        sid = _norm_target_id(item.get("shot_id"))
        new_shot = item.get("shot")
        forms = [key for key in ("shot", "updates", "field_patches", "keyframe_additions")
                 if key in item]
        # 2026-11-06：同一镜**可以**同时给「窄字段补丁」与「成对补帧」——两件事互不
        # 替代（实测 f13 既缺一段动作帧、尾帧布局又漏了被抬的人；只让选一种，另一
        # 半永远修不掉）。完整 `shot` 仍必须独占：它自带 keyframes/frame_layouts，
        # 再叠补丁无法判定以谁为准。
        _patch_forms = [k for k in forms if k in ("updates", "field_patches")]
        _combo = ("shot" not in forms and "keyframe_additions" in forms
                  and len(_patch_forms) <= 1)
        if (not forms or (len(forms) != 1 and not _combo)
                or any(item.get(k) is None for k in forms)):
            _reject("shape_invalid",
                    "同一目标只能提供一种替换形态（updates 与 keyframe_additions 可并用）",
                    sid=sid, details={"forms": list(forms)})
            return
        if "shot" in forms and not isinstance(new_shot, dict):
            _reject("shape_invalid", "shot 必须是对象", sid=sid)
            return
        if "shot" not in forms and set(item) != {"shot_id", *forms}:
            _reject("shape_invalid", "窄字段补丁条目含未知字段", sid=sid)
            return
        if not sid:
            _reject("shape_invalid", "shot_id 为空")
            return
        if sid in ("0", "hook") and not force_hook:
            # v6.83（二次审核发现 3）：hook 的字段要求与普通镜头不同（无
            # scene/location），必须由 `hook_replacement` 承载；塞进
            # `shot_replacements` 会按 `_SHOT` 校验出一个字段语义不符的对象。
            _reject("shape_invalid",
                    "hook 替换必须用 hook_replacement 承载，不得放进 shot_replacements",
                    sid=sid)
            return
        if force_hook:
            sid = "hook"
        target = _find(sid)
        if target is None:
            _reject("target_not_picked", f"shot {sid} 未被点名或不存在", sid=sid)
            return
        # S02：本轮基线的帧列表，映射的「原帧坐标」以它为准。
        _base_kfs = ((target.get("frame_layouts") or {}).get("kfs") or [])
        _base_keyframes = target.get("keyframes") or []
        _kf_base = target
        _asm_map: dict = {}
        _update_map: dict | None = None      # 基线 → 更新后（未装配前）
        if sid in _seen_ids:
            _reject("shape_invalid",
                    f"shot {sid} 重复出现（每个目标恰好一次，后者丢弃）", sid=sid)
            return
        # v6.86（用户决定 A1）：`expected_targets` 的成员校验下沉到**单条目**——
        # 「不在本轮点名集合内」是这一条的问题，不该否决别的目标。
        if expected_targets is not None and sid not in {str(t) for t in expected_targets}:
            _reject("target_not_picked",
                    f"shot {sid} 不在本轮点名集合内（该条丢弃）", sid=sid,
                    allowed=sorted(str(t) for t in (expected_targets or ())))
            return
        allowed = None
        if isinstance(allowed_by_target, dict):
            allowed = allowed_by_target.get(f"shot_{sid}", allowed_by_target.get(sid))
            if allowed is None and not force_hook:
                # 授权集合缺失 = 该目标没有明确字段路径 → 不得走定向修复
                _reject("unauthorized_path",
                        f"shot {sid}: 本轮未授权任何字段（问题未点名字段路径）",
                        sid=sid)
                return
            allowed = allowed or set()
        # 窄字段补丁（Scoped Field Patch）：当模型返回 `updates`（或 `field_patches`）
        # 而非完整 `shot` 对象时，先校验每个补丁路径在 `allowed` 内并装配到原镜副本，
        # 彻底消除模型漏抄未修改锁定字段引发的 `locked_changed` 误伤与 Token 浪费。
        _raw_updates = item.get("updates") if item.get("updates") is not None else item.get("field_patches")
        if new_shot is None and _raw_updates is not None:
            new_shot, _why = _mrt._apply_scoped_field_updates(target, _raw_updates, allowed=allowed)
            if _why:
                # M03：路径 / 授权集合来自**答卷与授权记录的结构化数据**，
                # `_why` 只是展示文本，不参与 code 判定。
                _reject("unauthorized_path", f"shot {sid}: {_why}", sid=sid,
                        field_path=_first_path(_raw_updates), allowed=allowed,
                        details={"submitted_paths": (
                            [str(k) for k in _raw_updates]
                            if isinstance(_raw_updates, dict)
                            else [str(x.get("field_path") or "")
                                  for x in (_raw_updates or [])
                                  if isinstance(x, dict)])})
                return
        # 2026-09-27（用户决定）：联动修复的**成对数据**形状——模型只给
        # 「帧 + 对应布局」，代码按时间顺序把两半装进同一声序号。缺任一半即拒收。
        # 2026-11-06：装配放在**字段补丁之后**，让「补一处字段 + 补一帧」能在同一
        # 条目里成立（补丁先落到镜头上，新帧再插进它的 keyframes/frame_layouts.kfs）。
        if item.get("keyframe_additions") is not None:
            _kf_base = new_shot if isinstance(new_shot, dict) else target
            _dup_why = _duplicate_keyframe_additions(
                _kf_base, item.get("keyframe_additions"))
            if _dup_why:
                _reject("pair_shape_invalid", f"shot {sid}: {_dup_why}", sid=sid,
                        field_path="keyframe_additions", allowed=allowed)
                return
            new_shot, _why = _assemble_keyframe_additions(
                _kf_base, item.get("keyframe_additions"),
                frame_mapping_out=_asm_map)
            if _why:
                _reject("pair_shape_invalid", f"shot {sid}: {_why}", sid=sid,
                        field_path=_first_path(item.get("keyframe_additions")),
                        allowed=allowed,
                        details={"kind": "keyframe_additions"})
                return
        # v6.91（用户决定，2026-09-22）：提交前把**未授权路径从原镜强制回填**。
        #
        # 原实现直接判越权 → 整条拒绝。实测 07:39 3/3 目标因此被拒：模型必须回填
        # 全部 24 个字段，而授权只覆盖少数几个路径，措辞略异即判越权。回填后
        # 「未授权路径 = 原样」由**构造保证**（差异只在授权路径内），
        # `check_replacement_authorized` 保留为纵深防御。
        if allowed is not None:
            new_shot, _echoed = _mrt._merge_authorized(target, new_shot, allowed)
            if _echoed:
                _mrt._logger.info(
                    f"[REPAIR] shot {sid}: 未授权路径已回填 {len(_echoed)} 处: "
                    f"{sorted(set(_echoed))[:5]}"
                )
            # v6.104.1（第二十四次重跑排查）：**必须能看出模型在授权字段上到底改了什么**。
            #
            # 该轮实测 `resolved=0` 却看不到任何依据：日志只有「未授权路径已回填
            # ['motion_desc']」，无法判断模型是「没加 KF」还是「加了但被回填」，
            # 只能靠翻 16MB 日志并撞上 581 字符的行截断。把授权字段的实际
            # canonical diff（含 `keyframes.__len__` 这种长度变化）打出来，
            # 「模型有没有真的补帧」一眼可判。
            try:
                _changed = _mrt.canonical_path_diff(target, new_shot)
            except Exception:
                _changed = []
            _kf_delta = (len(target.get("keyframes") or []),
                         len(new_shot.get("keyframes") or []))
            if _changed or _kf_delta[0] != _kf_delta[1]:
                _mrt._logger.info(
                    f"[REPAIR] shot {sid}: 授权字段实际改动 {len(_changed)} 处 "
                    f"（keyframes {_kf_delta[0]}->{_kf_delta[1]}）: {_changed[:8]}"
                )
            # S04（2026-11-11）：**关联字段回读**。改首帧准备态时，首帧布局与对应
            # 关键帧是同一件事的另一半；只改描述会让两者不一致，下一轮审计再报一条
            # 「跨字段矛盾」。这里把「哪些关联字段跟着变了」记进台账——语义是否真的
            # 修好仍由重验与提交点审计判定，这里不代替它们下结论。
            try:
                from step01_script_gen.repair_fields import (
                    related_check_fields as _rcf, related_readback as _rrb)
                _rel = _rcf(allowed, target)
                if _rel:
                    _rb = _rrb(target, new_shot, _rel)
                    _mrt._logger.info(
                        "[REPAIR] shot %s: 关联字段回读 %d 处（变 %d / 未变 %d）: %s"
                        % (sid, _rb["checked"], _rb["changed"], _rb["unchanged"],
                           [r["field"] for r in _rb["rows"] if r["changed"]][:6]))
                    # S04「越权回填」：模型改了关联字段、但那条路径不在本轮授权面里，
                    # 于是被原样回填。这里只报**事实**（请求过、已回填）；「该字段是否
                    # 确实需要改」由重验与提交点审计判定，不由这条日志下结论。
                    _related_all = {q for ps in _rel.values() for q in ps}
                    _gap = [p for p in (_echoed or [])
                            if any(p == q or p.startswith(q + ".") or q.startswith(p + ".")
                                   for q in _related_all)]
                    if _gap:
                        _mrt._logger.warn(
                            "[REPAIR] shot %s: 模型请求改动关联字段但不在授权范围"
                            "（已回填；是否确需改动由重验判定）: %s"
                            % (sid, sorted(_gap)[:4]))
            except Exception as _rb_err:
                _mrt._logger.warn("[REPAIR] shot %s: 关联字段回读失败（不影响本镜提交）: %s"
                             % (sid, _rb_err))
        # duration_sec 是派生值：授权合并后再同步，避免被回填成旧时长。
        if _raw_updates is not None and isinstance(new_shot, dict) and new_shot.get("duration") != target.get("duration"):
            new_shot["duration_sec"] = new_shot["duration"]
            # 2026-11-07（D4）：授权改 duration 且确实缩短时，按比例缩放镜内时间轴
            # （motion 时段与 kf 的 t_sec）——否则缩短镜长会立刻触发动作越界与
            # kf_time_out_of_shot，修一条换两条。
            if allowed is not None and "duration" in {str(a) for a in allowed}:
                try:
                    _old_d = float(target.get("duration_sec") or target.get("duration") or 0)
                    _new_d = float(new_shot.get("duration_sec") or new_shot.get("duration") or 0)
                except (TypeError, ValueError):
                    _old_d, _new_d = 0.0, 0.0
                if _old_d > 0 and 0 < _new_d < _old_d:
                    # 2026-11-22（S14 M04）：缩时只许削**空余**时长。低于基线镜头的
                    # 内容下限（与正式时间轴同一算法）= 加速朗读/动作换时长 ⇒ 拒收、
                    # 保留基线，不缩放时间轴去迁就。
                    from step01_script_gen.timecode import shot_content_floor
                    try:
                        _fps_r = float(script.get("fps", 24) or 24)
                    except (TypeError, ValueError, AttributeError):
                        _fps_r = 24.0
                    _floor = float(shot_content_floor(target, _fps_r))
                    if _new_d < _floor - 0.011:
                        _reject("duration_below_content_floor",
                                f"shot {sid}: duration {_new_d:g}s 低于内容下限 "
                                f"{_floor:.2f}s（发声/动作时段/关键帧）", sid=sid,
                                field_path="duration", allowed=allowed,
                                details={"floor_sec": round(_floor, 3),
                                         "requested_sec": _new_d})
                        return
                    # 新镜长 ≥ 内容下限 ⇒ 动作时段与关键帧都装得下：只削尾部空余，
                    # **不**等比缩放（缩放会把装得下的动作也加速）。
                    new_shot["duration_sec"] = round(float(_new_d), 2)
                    _mrt._logger.info(
                        "[REPAIR] shot %s: duration %.2f->%.2f（内容下限 %.2f；镜内时段不变）"
                        % (sid, _old_d, _new_d, _floor))
        # M03：`code` 由**结构化事实**判定，不解析 `_validate_replacement_shot`
        # 的原因文本（文本只用于展示）。
        #   ① 帧数与布局数不等 → 配对形状错误（可纠正）
        #   ② 授权 diff 有越权项 → 保护字段越权（保留基线，重验继续完整执行）
        #   ③ 其余 → 值/形状不合法（可纠正）
        _vr_code = "shape_invalid"
        _vr_path = ""
        if isinstance(new_shot, dict):
            _vk = new_shot.get("keyframes")
            _vl = (new_shot.get("frame_layouts") or {}).get("kfs") \
                if isinstance(new_shot.get("frame_layouts"), dict) else None
            if isinstance(_vk, list) and isinstance(_vl, list) \
                    and len(_vk) != len(_vl):
                _vr_code = "pair_shape_invalid"
                _vr_path = "frame_layouts.kfs"
            elif allowed is not None:
                from step01_script_gen.repair_fields import (
                    check_replacement_authorized as _cra)
                if _cra(target, new_shot, allowed):
                    _vr_code = "protected_field"
                    try:
                        _diff = _mrt.canonical_path_diff(target, new_shot)
                        _vr_path = str(_diff[0]) if _diff else ""
                    except Exception:
                        _vr_path = ""
        reason = _validate_replacement_shot(target, new_shot, is_hook=force_hook,
                                            allowed=allowed)
        if reason:
            _reject(_vr_code, f"shot {sid}: {reason}", sid=sid,
                    field_path=_vr_path, allowed=allowed,
                    details={"validator": "_validate_replacement_shot"})
            return
        # S02 补正（2026-11-15）------------------------------------------------
        # 来源链分两段，且都必须**可验证**：
        #   ① 字段更新 / 整表替换：基线 → 更新后（`_pre`）
        #   ② 成对补帧装配：更新后 → 候选（`_asm_map`）
        # 任一段无法建立 ⇒ 目标**在进入 staged 前**拒收、保留基线。旧实现「不导出
        # 映射 + 守卫退回同下标比较」无法保证拒收（缺映射时新增的空布局帧会被
        # 静默放过），已取消。
        # 布局内容一律**不参与**来源推导：整表替换只认保留关键帧完整结构，或
        # 帧数不变时逐帧证明「差异只在授权叶子」。
        _final_kfs = ((new_shot.get("frame_layouts") or {}).get("kfs") or []) \
            if isinstance(new_shot, dict) else []
        _final_kf = new_shot.get("keyframes") or [] if isinstance(new_shot, dict) else []
        _has_additions = item.get("keyframe_additions") is not None
        _has_updates = _raw_updates is not None
        _has_full_shot = "shot" in forms
        # 「更新阶段」的实际落点：有装配时取装配前快照（_kf_base），无装配时取
        # **授权合并后的最终候选**（new_shot）。旧代码一律取 _kf_base，而它在没有
        # keyframe_additions 时仍等于 target（基线），整表替换因此被误判成「帧结构
        # 没变」→ 拿到恒等映射 → 直到最终核对才因计数不符被拦，拒因也不是结构化
        # 的「来源无法证明」。S03 补正要求来源链不成立时在 staged 前明确拒收。
        _update_stage = _kf_base if _has_additions else new_shot
        _pre_kfs = ((_update_stage.get("frame_layouts") or {}).get("kfs") or []) \
            if isinstance(_update_stage, dict) else []
        _pre_kf = _update_stage.get("keyframes") or [] \
            if isinstance(_update_stage, dict) else []
        _identity_reason = (
            "帧来源无法证明：整表替换须逐字保留关键帧完整结构，或改用 "
            "updates（叶子）+ keyframe_additions 表达补帧")

        # ① 更新阶段 ---------------------------------------------------------
        _pre = None
        if _has_full_shot:
            # 用**合并后**的关键帧判身份（`merge_authorized` 会把未授权帧列表
            # 回填成基线值，合并前的值不是最终候选）。
            _pre = derive_frame_mapping_from_keyframes(_base_keyframes, _final_kf)
            if (_pre is None and len(_final_kfs) == len(_base_kfs)
                    and len(_final_kf) == len(_base_keyframes)
                    and verify_frame_identity_by_authorized_leaves(
                        _base_keyframes, _final_kf, allowed)):
                _pre = identity_frame_mapping(len(_base_kfs))
        elif _has_updates:
            _paths: list = []
            if isinstance(_raw_updates, dict):
                _paths = [str(k) for k in _raw_updates]
            elif isinstance(_raw_updates, list):
                _paths = [str(x.get("field_path") or "") for x in _raw_updates
                          if isinstance(x, dict)]
            _whole_container = any(
                _norm_keep_index(p) in ("keyframes", "frame_layouts",
                                        "frame_layouts.kfs") for p in _paths)
            if _whole_container or len(_pre_kfs) != len(_base_kfs) \
                    or len(_pre_kf) != len(_base_keyframes):
                # 更新阶段改变了帧结构 ⇒ 先取得可验证映射，再与插入映射组合。
                _pre = derive_frame_mapping_from_keyframes(_base_keyframes, _pre_kf)
            else:
                # 窄补丁只写具体叶子，索引身份由代码构造保证。
                _pre = identity_frame_mapping(len(_base_kfs))
        else:
            _pre = identity_frame_mapping(len(_base_kfs))
        if _pre is None:
            _reject("frame_identity_unproven", f"shot {sid}: {_identity_reason}",
                    sid=sid, field_path="frame_layouts.kfs", allowed=allowed,
                    details={"stage": "update_identity"})
            return

        # ② 装配阶段 ---------------------------------------------------------
        if _has_additions:
            if not _asm_map or _pre["after_count"] != _asm_map.get("before_count"):
                _reject("engineering_failure",
                        f"shot {sid}: 装配来源与更新阶段来源不连续，无法组合帧映射",
                        sid=sid, phase=PHASE_ENGINEERING,
                        details={"stage": "assembly_continuity"})
                return
            _stage_map = compose_frame_mappings(_pre, _asm_map)
            if len(_final_kfs) != _asm_map.get("after_count") \
                    or len(_final_kf) != _asm_map.get("after_count"):
                _reject("engineering_failure",
                        f"shot {sid}: 授权合并回填了装配后的帧结构，来源链失效",
                        sid=sid, phase=PHASE_ENGINEERING,
                        details={"stage": "assembly_post_merge"})
                return
        else:
            _stage_map = _pre

        # ③ 最终核对 ---------------------------------------------------------
        _map_why = validate_frame_mapping(
            _stage_map, len(_base_kfs), len(_final_kfs))
        if _map_why or len(_final_kf) != len(_final_kfs):
            _reject("engineering_failure",
                    f"shot {sid}: 帧映射未通过最终核对"
                    f"（{_map_why or '帧数与布局数不一致'}）",
                    sid=sid, phase=PHASE_ENGINEERING,
                    details={"stage": "mapping_final_check",
                             "why": str(_map_why or "")})
            return
        _seen_ids.add(sid)
        staged.append((target, new_shot, sid))
        _stage_mappings[sid] = _stage_map

    for _item in replacements or []:
        _stage(_item)
    if isinstance(hook_replacement, dict):
        _stage(hook_replacement, force_hook=True)

    # v6.86（**用户决定 A1**，2026-09-21）：**逐目标提交**，取代 v6.83 的
    # 「整批全有或全无」。
    #
    # 实测（2026-09-21 22:21:22）：
    #   `[REPAIR] 替换被拒 1 条: ["目标集合不完整：缺少 ['16']（本轮点名
    #    ['16','4','hook']）"]`
    # 模型回了镜头 4 与 hook 的替换、漏了镜头 16 → 整批回滚，`resolved=0`。
    # 而镜头 4 与 hook 的替换**本身已通过授权校验**，一并作废——修复轮产出 0。
    #
    # 为什么逐目标提交是安全的：`expected_targets` 要防的是「只提交一部分却当
    # 整批成功」，那是**报告失真**，不是**数据损坏**。数据侧的安全保证全部是
    # **单镜作用域**（`check_replacement_authorized` 字段授权 / `canonical_path_diff`
    # 越权路径 / `_final_field_mask_violations` 锁定字段 / 逐镜 revision gate /
    # 每镜时间不变量），而 replacement 是「完整镜头对象」、**不写任何跨镜字段**，
    # 所以不存在「只有跟别镜一起改才成立」的情况。
    #
    # 保留的批次级安全网（都在修复轮调用侧，不在此函数内）：
    #   - 全局完整性（checker 失败 → 整轮回滚）
    #   - **非目标镜头 revision**（`changed_shot_keys` 逐镜比对，动到未点名镜头即拒）
    #   - **阻塞退化**（本轮 blocking 身份新增即拒）
    #   - 锁定字段签名（`_locked_repair_signature`）
    # 缺项如今只作为**如实回报**出现在 `rejected` 里，不再否决已提交的目标。
    if not staged:
        if not rejected:
            _reject("shape_invalid", "未提供任何替换条目")
        return script, 0, rejected or ["未提供任何替换条目"]

    for _target, _new, _sid in staged:
        _target.clear()
        _target.update(_new)
    committed = len(staged)

    # S02：只有真正提交成功的目标才导出映射；被拒目标不留下有效映射。
    if isinstance(frame_mappings_out, dict):
        for _sid, _mp in _stage_mappings.items():
            if _mp is None:
                continue
            _key = "hook" if str(_sid) in ("0", "hook") else "shot_%s" % _sid
            frame_mappings_out[_key] = _mp

    if expected_targets is not None:
        got = {_sid for _, _, _sid in staged}
        missing = sorted({str(t) for t in expected_targets} - got)
        if missing:
            rejected.append(
                f"目标 {missing} 本轮未返回替换（其余 {committed} 个目标已提交）"
            )
    return merged, committed, rejected



# 最终字段 mask：被点名镜头里 **LLM 不得改动**的字段（v6.80，发现 9 #4）。
# 注意**不含 `duration_sec`**——它由 `timecode.write_timeline_from_durations`
# 从 `duration` 统一派生（唯一口径），模型只负责 `duration`。
# v6.83（二次审核发现 2）：补上 `duration`。修复 prompt 一直把「index / duration」
# 声明为 Mask 字段，运行时却只锁 index——「duration 不可改」这条契约在代码里没有
# 对应实现，LLM 顺带改时长不会被任何判据拦下。
_FINAL_LOCKED_FIELDS = (
    "index", "narration_segment", "subtitle", "scene", "location", "variation_type",
    "duration",
)
# 数值型 Mask 字段的比较容差：`write_timeline_from_durations` 会把 duration 定点到
# 2 位小数（`_align_frame_duration`），定点差不该被当成「改了时长」。
_LOCKED_NUMERIC_TOL = 0.011


def _final_field_mask_violations(before: dict, after: dict, picked: set[str],
                                 allowed_by_target: dict | None = None) -> list[str]:
    """找出被点名镜头里被改动的「锁定字段」中**本轮没有授权**的那些.

    发现 9 #4：上一版只在 patch 之后、后处理**之前**取一次 locked 快照，于是
    `post_fix` / `hard_fix` / 伤情兜底对锁定字段的写入逃过比较。

    v6.99（2026-09-23 第十四次重跑实测）：本闸门与**字段授权层**互相矛盾，
    把**授权过**的改动判成越权，整轮回退掉了 10 条已解决的阻塞：

    ```text
    [REPAIR] 接受条件: before=25+audit1 after=15 resolved=10 audit_resolved=1 new=0
    [REPAIR] LLM 改动了锁定字段 2 处: ['shot 4.duration: 4.0 -> 6.0', 'shot 7.duration: 4.0 -> 5.0']
    [REPAIR] 修复未满足接受条件（reason=locked_field_changed, ...），回退本轮修复
    ```

    而 `duration` **正是**时长类 issue 授权改的字段（`repair_fields._REPAIR_CODE_FIELDS`
    把 `shot_duration → ("duration",)`；消息里的「时长」也会被 `_fields_from_text`
    解析成 `duration`）。旁证：日志里 shot 7 的「未授权路径已回填」**不含** duration
    ——说明它确实在授权集合内、被 `merge_authorized` 有意放行。

    判据改为：**锁定字段中本轮未授权的那些**才算越权。授权层是**按 issue** 推导的
    具体判断，`_FINAL_LOCKED_FIELDS` 是粗粒度默认值——具体优先于默认，而默认仍
    兜住「没被授权的锁定字段」（这才是纵深防御的本意）。
    """
    out: list[str] = []

    def _by_key(script: dict) -> dict:
        d = {"hook": script.get("hook")} if isinstance(script.get("hook"), dict) else {}
        for i, sh in enumerate(script.get("shots") or []):
            if isinstance(sh, dict):
                d[str(sh.get("index", i))] = sh
        return d

    b, a = _by_key(before), _by_key(after)
    targets = {str(x)[5:] for x in picked if str(x).startswith("shot_")}
    if "hook" in picked:
        targets.add("hook")
    for key in sorted(targets):
        sb, sa = b.get(key), a.get(key)
        if not isinstance(sb, dict) or not isinstance(sa, dict):
            continue
        # v6.99：本轮**授权过**的字段不算越权（授权按 issue 推导，比默认值具体）
        _allowed = set()
        if allowed_by_target:
            for _p in (allowed_by_target.get("shot_%s" % key)
                       or allowed_by_target.get(key) or ()):
                _allowed.add(str(_p))
        for f in _FINAL_LOCKED_FIELDS:
            if f in _allowed:
                continue
            vb, va = sb.get(f), sa.get(f)
            if isinstance(vb, (int, float)) and isinstance(va, (int, float)) \
                    and not isinstance(vb, bool) and not isinstance(va, bool):
                if abs(float(vb) - float(va)) <= _LOCKED_NUMERIC_TOL:
                    continue
            if vb != va:
                out.append(f"shot {key}.{f}: {vb!r} -> {va!r}")
    return out


# ===========================================================================
# v6.83（二次审核发现 1 实施 #3）：落盘前的**唯一时间收口**
# ===========================================================================
def _record_time_field_write_audit(script: dict, tracker, *, label: str) -> list:
    """把阶段屏障的实测结果写进脚本（运行时键，不入盘）并告警；返回越权记录.

    `quality_issues._time_field_audit_issues` 会把它转成 blocking Issue——
    发现 1 指出「只写日志」是 fail-open：越权写入照样重建 timeline、算 revision、
    落盘，正式结果里看不出异常。
    """
    unauthorized = tracker.unauthorized_writes()
    structural = tracker.structural_writes()
    if unauthorized or structural:
        # 结构段写入也记录（**没有越权 ≠ 没有写入者**）——它们是「时间字段到底有
        # 几个写入者」的直接证据，正是本审计最初要回答的问题。
        script["_time_field_write_audit"] = {
            "unauthorized": [{"constraint": n, "diff": d} for n, d in unauthorized],
            "structural": [{"constraint": n, "diff": d} for n, d in structural],
        }
    if unauthorized:
        _mrt._logger.warn(
            "[WARN] 审计段越权写入时间字段（%d 个约束）：%s —— 链前 KF/时长检查的"
            "结论对最终对象不成立；已升级为 blocking issue（见 %s quality gate）"
            % (len(unauthorized), ", ".join(n for n, _ in unauthorized), label)
        )
    return unauthorized


def _record_semantic_write_audit(script: dict, tracker, *, label: str) -> list:
    """把阶段屏障的**语义/结构**写入结果写进脚本（运行时键）并告警；返回未受豁免记录.

    v6.98（2026-09-22 审核 P0-3）：屏障此前只快照时间字段，审计段里
    「删掉一整个镜头对象」这类写入零报告——`opening_empty_shot_remove` 在审计
    边界之后 `shots.pop(0)`，被删镜的 prompt / ff / lf / motion / KF / 布局 /
    姿态 / 道具随 `del` 一起消失，而「审计段只读」这条契约**只在注释里成立**。

    现在语义/结构写入分两类落盘（`quality_issues._semantic_write_audit_issues` 消费）：
      - `unauthorized` → **blocking**（`semantic_audit_write`）；
      - `sanctioned`（`STRUCTURAL_SANCTIONED_WRITERS`）→ 不阻塞，转 advisory 留证。
    """
    unauthorized = tracker.unauthorized_semantic_writes()
    sanctioned = tracker.sanctioned_semantic_writes()
    if unauthorized or sanctioned:
        script["_semantic_write_audit"] = {
            "unauthorized": [{"constraint": n, "diff": d} for n, d in unauthorized],
            "sanctioned": [{"constraint": n, "diff": d} for n, d in sanctioned],
        }
    if unauthorized:
        _mrt._logger.warn(
            "[WARN] 审计段越权写入语义/结构（%d 个约束）：%s —— 镜头增删/重排/语义改写"
            "会让链前结论对最终对象不成立；已升级为 blocking issue（见 %s quality gate）"
            % (len(unauthorized), ", ".join(n for n, _ in unauthorized), label)
        )
    if sanctioned:
        _mrt._logger.info(
            "[AUDIT] 审计段受豁免的结构写入：%s（不阻塞，已留 advisory 证据）"
            % ", ".join("%s(%d 处)" % (n, len(d)) for n, d in sanctioned)
        )
    return unauthorized
