# -*- coding: utf-8 -*-
"""分级脚本预检：把「靠 LLM 审计才发现」的缺陷类型做成**确定性**检查器（零调用）.

背景（2026-11-14 U6 S03）：R4 的 6 条问题里，只有「跨字段 24 字重复注入」有确定性
判据（`shared/prompt_contradiction`），其余 5 类全靠审计模型读出来——

  · 峰值帧 layout 漏掉在镜角色（shot10 BLOCKING-1）
  · 关键帧 stage 与动作进度不符（shot13 BLOCKING-2、shot7）
  · 同一镜内硬切到另一地点（shot13）
  · 台词/音效里的乐器与 motion_desc 不一致（shot1 笛 vs 箫）
  · 人物状态链断裂、道具跨镜断链（shot11、shot5/shot7）

只靠「逐镜人工修补」是不通用的：下一个候选、下一章还会再犯。本工具把这些类别
固化成判据，对**任意** `script_result.json` 都能跑，用于在提交/送审前自查。

判据（全部只读字段，不调模型、不写盘）：

  C1 `layout_presence_gap`   关键帧序列内部，某在镜角色在前后两帧都在、中间帧缺席，
                             且该帧描述无「离场/没入/出画」证据 → hard
  C2 `prop_chain_break`      道具在同一角色身上从有到无，且目标帧描述无释放证据、
                             也没有别的角色接走 → advisory（镜内 + 跨镜两种）
  C3 `entity_class_conflict` 同一实体类（乐器/载具）在同一镜的不同字段取到不同值
                             （音效写「箫」、动作写「笛」）→ advisory
  C4 `location_drift`        镜内关键帧描述出现「硬切/转至/来到…」+ 与 `location`
                             不一致的地点词 → advisory
  C5 `stage_progress_mismatch` stage=`pre` 但该帧描述已是完成态（已/完成/结束）→ advisory
  C6 `duplicate_injection`   跨字段 24 字重复注入（step02a 真实入口的硬阻断判据）→ hard
  C7 `image_prompt_leak`     图片提示词里的运镜词泄漏等 → advisory

另带 `--include-rules` 时把 `step01_script_gen.quality_issues` 的 9 个单镜规则族
一并跑一遍，让本工具成为「一个入口看全部确定性判据」。

用法：
  python tools/script_preflight.py <script.json> [--json out.json] [--fail-on hard|advisory|none]
退出码：0 无达阈问题 / 4 有达阈问题（便于接 CI 或提交前门禁）。
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXIT_CLEAN = 0
EXIT_FINDINGS = 4

# ── 证据词表（判据的「免报」白名单，宁可漏报也不要把正常叙事判成缺陷）──────────
_EXIT_EVIDENCE = ("离场", "离船", "出画", "没入", "不见", "消失", "退去", "转身走",
                  "退开", "走开", "离开", "腾空而去", "遁走", "隐入", "倒地不省")
_RELEASE_EVIDENCE = ("放下", "收起", "收回", "递给", "交给", "抛", "扔", "丢", "脱手",
                     "松手", "酒水洒落", "洒落", "打落", "落地", "插回", "挂回", "收起",
                     "弃", "掉落", "脱手", "交给", "递与", "搁在", "搁下", "搁", "摆在",
                     "放置", "放在", "置于", "搭在", "停在", "垂落船舷", "插在")
_TRANSFER_HINT = ("接过", "接住", "握住", "拿起", "接过", "夺", "抢")

#: 实体类词表：同类只能取一个值（同一镜不同字段互相矛盾时才有意义）
_ENTITY_CLASSES = {
    "乐器": {
        "笛": ("笛声", "竹笛", "横笛", "长笛", "笛"),
        "箫": ("箫声", "洞箫", "竹箫", "排箫", "箫"),
        "琴": ("古琴", "琴声", "琴音", "抚琴", "琴"),
        "筝": ("古筝", "筝声", "筝"),
        "琵琶": ("琵琶",),
        "二胡": ("二胡",),
        "鼓": ("鼓声", "战鼓", "擂鼓", "鼓"),
    },
}

#: 地点后缀：用来从「硬切至别庄大门」里认出「别庄大门」是个地点
_PLACE_SUFFIX = ("门", "院", "楼", "堂", "桥", "岸", "巷", "庄", "街", "城", "宅",
                 "房", "屋", "渡", "亭", "阁", "廊", "厅", "殿", "寺", "观", "铺", "店")
_CUT_VERB = ("硬切至", "硬切到", "切至", "切到", "转至", "转到", "来到", "进入",
             "走出", "冲出", "驶入", "驶出", "直奔", "抵达")

_COMPLETED_HINT = ("已", "完成", "结束", "之后", "过后")


def _norm(text) -> str:
    return str(text or "")


def _entries(group) -> list[dict]:
    return [e for e in (group or []) if isinstance(e, dict)]


def _frames(shot: dict) -> list[tuple[str, list[dict], str]]:
    """按时间顺序返回 [(标签, layout 条目, 该帧叙事文本)].

    只取**关键帧序列**（kfs），ff/lf 另行处理——ff/lf 的描述与 kf 描述粒度不同，
    混在一起比对会制造大量假阳性（例如尸体在 ff 未被点名属正常叙事取舍）。
    """
    layouts = shot.get("frame_layouts") or {}
    kfs = layouts.get("kfs") or []
    keyframes = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
    out: list[tuple[str, list[dict], str]] = []
    for i, group in enumerate(kfs):
        desc = ""
        if i < len(keyframes):
            desc = _norm(keyframes[i].get("desc"))
        out.append(("kf%d" % i, _entries(group), desc))
    return out


def _props_of(entries: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for e in entries:
        name = _norm(e.get("name"))
        if not name:
            continue
        out.setdefault(name, set()).update(_norm(p) for p in (e.get("props") or []) if _norm(p))
    return out


def _same_prop(a: str, b: str) -> bool:
    """道具同名判定：允许「青瓷酒壶 / 酒壶 / 酒壶（青瓷）」这类修饰差异."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 2 and short in long


def _has_any(text: str, words) -> bool:
    return any(w and w in text for w in words)


def check_layout_presence(shot: dict) -> list[dict]:
    """C1：关键帧序列里「在-不在-在」的角色 = 漏画（无离场证据时）."""
    frames = _frames(shot)
    if len(frames) < 3:
        return []
    idx = int(shot.get("index") or 0)
    present = [{_norm(e.get("name")) for e in ents if e.get("on_screen", True)}
               for _label, ents, _d in frames]
    issues = []
    names = set().union(*present) if present else set()
    for name in sorted(names):
        if not name:
            continue
        for i in range(1, len(frames) - 1):
            if name in present[i]:
                continue
            before = any(name in present[j] for j in range(0, i))
            after = any(name in present[j] for j in range(i + 1, len(present)))
            if not (before and after):
                continue
            desc = frames[i][2]
            if _has_any(desc, _EXIT_EVIDENCE):
                continue
            issues.append({
                "code": "layout_presence_gap",
                "severity": "hard",
                "shot": idx,
                "frame": frames[i][0],
                "message": ("%s 是 %s 与 %s 的中间帧，但该帧 frame_layouts 里没有 %s，"
                            "描述也无离场证据 ⇒ 出图会漏掉这个在镜角色"
                            % (frames[i][0], frames[i - 1][0], frames[i + 1][0], name)),
            })
    return issues


def check_prop_chain(shot: dict, prev_shot: dict | None,
                     next_shot: dict | None = None) -> list[dict]:
    """C2：道具从「有」到「无」且无释放证据（镜内 = advisory；跨镜 = 断链又回来才算）.

    跨镜口径刻意收窄：只有「上一镜有 → 本镜没有 → 下一镜又有」这种**断了又回来**
    的形态才报 advisory（U6 S03 的 shot5 钓杆/酒壶正是这个形态）。只是「这一镜没再用」
    属于正常叙事取舍，记为 info 而不计入告警——否则每章都会刷一片噪声，
    真正的断链反而被淹没。
    """
    idx = int(shot.get("index") or 0)
    frames = _frames(shot)
    issues = []

    def _emit(frame_label: str, name: str, prop: str, gained_by_other: bool,
              scope: str, severity: str):
        if gained_by_other:
            return
        issues.append({
            "code": "prop_chain_break",
            "severity": severity,
            "shot": idx,
            "frame": frame_label,
            "message": "%s 的「%s」在%s消失，描述里没有释放/被打落证据，也没有别人接走" %
                       (name, prop, scope),
        })

    # 镜内：kf_i → kf_{i+1}
    for i in range(len(frames) - 1):
        cur, nxt = _props_of(frames[i][1]), _props_of(frames[i + 1][1])
        desc_next = frames[i + 1][2]
        on_screen_next = {_norm(e.get("name")) for e in frames[i + 1][1]
                          if e.get("on_screen", True)}
        if _has_any(desc_next, _RELEASE_EVIDENCE) or _has_any(desc_next, _TRANSFER_HINT):
            continue
        for name, props in cur.items():
            if name not in on_screen_next:
                # 角色本身已出画/不在该帧：道具记账无意义（不是断链）
                continue
            for prop in props:
                if any(_same_prop(prop, p) for p in nxt.get(name, ())):
                    continue
                if _norm(prop) in desc_next:
                    # 描述里仍提到该道具 ⇒ 只是 layout 少写，不是真断链
                    continue
                gained = any(_same_prop(prop, p) for other, ps in nxt.items()
                             if other != name for p in ps)
                _emit(frames[i + 1][0], name, prop, gained, "下一关键帧", "advisory")

    # 跨镜：上一镜 lf → 本镜 ff（只有 sandwich 才升级为 advisory）
    if isinstance(prev_shot, dict):
        prev_lf = _props_of(_entries((prev_shot.get("frame_layouts") or {}).get("lf")))
        cur_ff = _props_of(_entries((shot.get("frame_layouts") or {}).get("ff")))
        next_ff = _props_of(_entries((next_shot.get("frame_layouts") or {}).get("ff"))) \
            if isinstance(next_shot, dict) else {}
        cur_text = _norm(shot.get("ff_desc")) + _norm(shot.get("motion_desc"))
        ff_on_screen = {_norm(e.get("name")) for e in
                        _entries((shot.get("frame_layouts") or {}).get("ff"))
                        if e.get("on_screen", True)}
        if not (_has_any(cur_text, _RELEASE_EVIDENCE) or _has_any(cur_text, _TRANSFER_HINT)):
            for name, props in prev_lf.items():
                if name not in ff_on_screen:
                    # 本镜首帧里根本没有这个角色（先按「在场连续性」处理，别记成道具断链）
                    continue
                for prop in props:
                    if any(_same_prop(prop, p) for p in cur_ff.get(name, ())):
                        continue
                    gained = any(_same_prop(prop, p) for other, ps in cur_ff.items()
                                 if other != name for p in ps)
                    if gained:
                        continue
                    if _has_any(cur_text, (_norm(prop),)):
                        # 本镜文本仍提到该道具 → 只是 layout 没写，不是真断链
                        continue
                    back = any(_same_prop(prop, p) for p in next_ff.get(name, ()))
                    _emit("ff", name, prop, gained, "上一镜 lf→本镜 ff",
                          "advisory" if back else "info")
    return issues


def check_entity_class_conflict(shot: dict) -> list[dict]:
    """C3：同类实体（乐器/载具）在不同字段取不同值."""
    idx = int(shot.get("index") or 0)
    issues = []
    audio = _norm(shot.get("audio_cue"))
    visual = " ".join(_norm(shot.get(k)) for k in ("motion_desc", "prompt", "ff_desc", "lf_desc"))
    for cls, table in _ENTITY_CLASSES.items():
        def found(text):
            return {key for key, words in table.items() if _has_any(text, words)}
        a, v = found(audio), found(visual)
        if a and v and not (a & v):
            issues.append({
                "code": "entity_class_conflict",
                "severity": "advisory",
                "shot": idx,
                "frame": "audio/visual",
                "message": "%s 类取值不一致：音效写 %s，画面写 %s" %
                           (cls, "/".join(sorted(a)), "/".join(sorted(v))),
            })
    return issues


def check_location_drift(shot: dict) -> list[dict]:
    """C4：镜内硬切到另一个地点（`location` 之外的场景词）."""
    idx = int(shot.get("index") or 0)
    home = _norm(shot.get("location")) or _norm(shot.get("scene"))
    if not home:
        return []
    issues = []
    for label, _ents, desc in _frames(shot):
        for verb in _CUT_VERB:
            pos = desc.find(verb)
            if pos < 0:
                continue
            tail = desc[pos + len(verb): pos + len(verb) + 14]
            tail = re.split(r"[，。；、,;]", tail)[0]
            if not tail:
                continue
            if any(ch in tail for ch in home):
                continue
            if not any(tail.endswith(sfx) or sfx in tail for sfx in _PLACE_SUFFIX):
                continue
            issues.append({
                "code": "location_drift",
                "severity": "advisory",
                "shot": idx,
                "frame": label,
                "message": "本镜 location=%s，但 %s 写「%s%s」⇒ 镜内硬切到别的地点" %
                           (home, label, verb, tail),
            })
            break
    return issues


def check_stage_progress(shot: dict) -> list[dict]:
    """C5：stage=pre 但该帧描述已是完成态（已/完成/结束/之后）."""
    idx = int(shot.get("index") or 0)
    keyframes = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
    issues = []
    for i, kf in enumerate(keyframes):
        stage = _norm(kf.get("stage")).strip().lower()
        desc = _norm(kf.get("desc"))
        if stage != "pre" or not desc:
            continue
        hit = [w for w in _COMPLETED_HINT if w in desc]
        if not hit:
            continue
        issues.append({
            "code": "stage_progress_mismatch",
            "severity": "advisory",
            "shot": idx,
            "frame": "kf%d" % i,
            "message": "stage=pre（动作之前）但描述含完成态 %s ⇒ 动作已在进行/结束" % hit,
        })
    return issues


def check_duplicate_injection(shot: dict) -> list[dict]:
    """C6：跨字段 24 字重复注入（step02a 真实入口的硬阻断判据）."""
    from shared.prompt_contradiction import find_contradictions
    idx = int(shot.get("index") or 0)
    text = "\n".join(_norm(shot.get(k)) for k in
                     ("prompt", "ff_desc", "lf_desc", "motion_desc"))
    return [{
        "code": "duplicate_injection",
        "severity": "hard",
        "shot": idx,
        "frame": "prompt/ff/lf/motion",
        "message": str(p),
    } for p in (find_contradictions(text) or [])]


def check_image_prompt(script: dict) -> list[dict]:
    """C7：图片提示词质量（运镜词泄漏等）.

    `collect_image_prompt_quality` 会经 `shared.quality_gate.attach_gate_metadata`
    → `ensure_script_revision` 给传入的 script **补写** `script_revision`——那是生产
    质量门的既有约定，本工具不去改它；但预检是**只读**入口，调用方传进来的对象不能
    被悄悄改写（否则同一份 script 在预检前后 digest/revision 会漂移）。故传副本。
    """
    from step02a_frame_gen.image_prompt_quality import collect_image_prompt_quality
    report = collect_image_prompt_quality(copy.deepcopy(script), write=False)
    out = []
    for w in report.get("warnings") or []:
        if not isinstance(w, dict):
            continue
        out.append({
            "code": "image_prompt_leak",
            "severity": "advisory",
            "shot": w.get("shot"),
            "frame": str(w.get("field") or ""),
            "detail": w.get("code") or "",
            "message": str(w.get("message") or w.get("detail") or ""),
        })
    return out


def check_rules(shot: dict) -> list[dict]:
    """可选：把质量门的 9 个单镜规则族一起跑（不改变它们的口径，只是汇总）."""
    from step01_script_gen import quality_issues as qi
    idx = int(shot.get("index") or 0)
    families = ("_action_time_issues", "_state_order_issues", "_pose_conflicts",
                "_surface_conflicts", "_onscreen_prop_issues", "_prop_release_owner_issues",
                "_kf_segment_missing_issues", "_keyframe_timeline_issues",
                "_kf_person_visibility_issues")
    out = []
    for name in families:
        try:
            found = getattr(qi, name)(shot, idx) or []
        except Exception as exc:                                  # noqa: BLE001
            out.append({"code": "checker_error", "severity": "advisory", "shot": idx,
                        "frame": name, "message": str(exc)})
            continue
        for item in found:
            out.append({
                "code": "rule:" + _norm(item.get("code") or name),
                "severity": _norm(item.get("severity") or "advisory"),
                "shot": idx,
                "frame": _norm(item.get("frame_id")),
                "message": _norm(item.get("message")),
            })
    return out


def run(script: dict, include_rules: bool = False) -> list[dict]:
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    issues: list[dict] = []
    for pos, shot in enumerate(shots):
        prev = shots[pos - 1] if pos > 0 else None
        nxt = shots[pos + 1] if pos + 1 < len(shots) else None
        issues.extend(check_layout_presence(shot))
        issues.extend(check_prop_chain(shot, prev, nxt))
        issues.extend(check_entity_class_conflict(shot))
        issues.extend(check_location_drift(shot))
        issues.extend(check_stage_progress(shot))
        issues.extend(check_duplicate_injection(shot))
        if include_rules:
            issues.extend(check_rules(shot))
    issues.extend(check_image_prompt(script))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="分级脚本预检（确定性，零调用）")
    parser.add_argument("script")
    parser.add_argument("--json", default=None, help="把完整报告写到该路径")
    parser.add_argument("--include-rules", action="store_true",
                        help="同时跑 quality_issues 的 9 个单镜规则族")
    parser.add_argument("--fail-on", choices=("hard", "advisory", "none"), default="hard")
    parser.add_argument("--show-info", action="store_true",
                        help="同时列出 info 级（如「这一镜只是没再用某道具」）")
    args = parser.parse_args()

    path = Path(args.script)
    script = json.loads(path.read_text(encoding="utf-8-sig"))
    issues = run(script, include_rules=args.include_rules)

    hard = [i for i in issues if i.get("severity") == "hard"]
    advis = [i for i in issues if i.get("severity") == "advisory"]
    infos = [i for i in issues if i.get("severity") not in ("hard", "advisory")]
    report = {
        "schema": "step01_preflight/1",
        "script": str(path),
        "shot_count": len(script.get("shots") or []),
        "hard_count": len(hard),
        "advisory_count": len(advis),
        "info_count": len(infos),
        "by_code": {},
        "issues": issues,
    }
    for i in issues:
        report["by_code"][i["code"]] = report["by_code"].get(i["code"], 0) + 1

    print("脚本：%s（%d 镜）" % (path, report["shot_count"]))
    print("hard %d / advisory %d / info %d" %
          (report["hard_count"], report["advisory_count"], report["info_count"]))
    for i in hard:
        print("  [HARD] shot %s %s  %s" % (i.get("shot"), i.get("code"), i.get("message")))
    for i in advis:
        print("  [adv ] shot %s %s  %s" % (i.get("shot"), i.get("code"), i.get("message"))[:200])
    if args.show_info:
        for i in infos:
            print("  [info] shot %s %s  %s" % (i.get("shot"), i.get("code"), i.get("message"))[:200])

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告已写出：%s" % args.json)

    if args.fail_on == "hard" and hard:
        return EXIT_FINDINGS
    if args.fail_on == "advisory" and issues:
        return EXIT_FINDINGS
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
