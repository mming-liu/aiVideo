# -*- coding: utf-8 -*-
"""chapter_001 终稿数据修复（2026-09-12 全修执行脚本）.

对 script_result.json 应用 0912 机制修复并做叙事手术：
  1. post_fix（motion 三归一 / prompt 截尾 / …新清洗）
  2. 现存「火星四溅」按水体上下文替换为水花系（注入门控只管新增，存量要换）
  3. shot4 lf 黑影负向约束（不得露出水面成人形剪影）
  4. run_hard_fix（含新约束：kf 归一 / 12+12.5 合并 / 10→11 侧翻转交换）
  5. 合并镜时长钳回 8s 硬上限；新增「马车夜奔」结尾镜（旁白逐字原文）
  6. hook ff_desc 重写（清掉「.5米」残片/重复短语，强化"张十三在船板上不在水中"）
  7. _compute_timecodes 重算 + 落盘（先写 .bak）

用法：python tools/fix_ch1_repair_0912.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CHAPTER = Path(
    "output/video/锦衣夜行/part_01_杀青州/chapter_001/script_result.json")
NOVEL = "锦衣夜行_77119小说.txt"


def main() -> None:
    from shared.chapter_reader import extract_section
    from step01_script_gen.validation import post_fix_shot_prompts
    from step01_script_gen.constraints import run_hard_fix
    from step01_script_gen.timecode import _compute_timecodes
    from step01_script_gen.main import _score_script

    novel = extract_section(NOVEL, 1)
    script = json.loads(CHAPTER.read_text(encoding="utf-8"))
    shots = script["shots"]
    print(f"[1] 载入：{len(shots)} 镜 + hook")

    # ---- 0. 先归一 objects_bank（同物异名合并）——必须在 hard_fix 之前：
    # 锚定约束按 bank 重新注入，bank 未归一则修复后被重新注回双规格 ----
    from shared.scene_objects_bank import SceneObjectsBank
    _bank = SceneObjectsBank()
    _bank.load()
    _bank.save()  # save() 内部触发 _consolidate_aliases
    print(f"[0] objects_bank 已归一：{_bank.get_all_identifiers()}")

    # ---- 1. post_fix（含 0912 新清洗）----
    script = post_fix_shot_prompts(script)
    print("[2] post_fix 完成")

    # ---- 2. 存量火星→水花（水体上下文）----
    for s in shots + ([script["hook"]] if script.get("hook") else []):
        for f in ("motion_desc", "ff_desc", "lf_desc", "prompt"):
            t = s.get(f)
            if t and "火星四溅" in t and any(
                    w in t for w in ("水下", "水面", "水底", "水中", "船底")):
                s[f] = t.replace("火星四溅", "水花四溅")
    print("[3] 存量火星/水花替换完成")

    # ---- 3. shot4 黑影负向 ----
    for s in shots:
        if s.get("index") == 4:
            guard = ("黑影完全处于水面之下，仅是一团暗影轮廓，不得露出水面，"
                     "不得形成人形头部剪影")
            lf = s.get("lf_desc", "") or ""
            if "不得露出水面" not in lf:
                s["lf_desc"] = f"{lf.rstrip('。')}。{guard}。"
            pr = s.get("prompt", "") or ""
            if "不得露出水面" not in pr and pr:
                s["prompt"] = f"{pr.rstrip('。')}。船边水下黑影{guard}。"
    print("[4] shot4 黑影负向完成")

    # ---- 4. hard_fix final（含新约束：kf 归一/重复合并/侧翻转交换）----
    script = run_hard_fix(script, is_final_pass=True, novel_text=novel)
    print("[5] hard_fix 完成（含 0912 新约束）")

    # ---- 5.5 船体规格归一 + 重复「可见」折叠（在 hard_fix 之后：
    # 锚定约束会用别名再注入同规格，清理必须在其后跑一次）----
    import re as _re
    _CANON_BOAT = "无篷舴艋舟（长5.5米宽1.5米，可容2-3人）"
    _n_boat = 0
    for s in script["shots"] + ([script["hook"]] if script.get("hook") else []):
        for f in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
            t = s.get(f)
            if not t or "舟" not in t:
                continue
            t2 = _re.sub(r"无篷舴艋舟（[^）]{0,60}）", _CANON_BOAT, t)
            t2 = _re.sub(r"(?<!艋)(?<!篷)舟（[^）]{0,60}）", _CANON_BOAT, t2)
            t2 = _re.sub(
                r"(" + _re.escape(_CANON_BOAT) + r"可见)(，\s*" + _re.escape(_CANON_BOAT)
                + r"可见)+", r"", t2)
            if t2 != t:
                s[f] = t2
                _n_boat += 1
    print(f"[5.5] 船体规格归一/锚定折叠：{_n_boat} 处")

    # ---- 5. 合并镜时长钳回 8s 硬上限 ----
    for s in script["shots"]:
        try:
            if float(s.get("duration_sec") or 0) > 8.0:
                s["duration_sec"] = 8.0
        except (TypeError, ValueError):
            pass

    # ---- 6. 马车夜奔结尾镜（旁白逐字原文，治"结尾自废武功"）----
    idxs = [s.get("index") for s in script["shots"]]
    if 13 not in idxs:
        script["shots"].append({
            "index": 13,
            "duration_sec": 6.0,
            "scene": "云河镇夜奔",
            "location": "云河镇官道",
            "main_subject": "张十三，听香",
            "shot_size": "远景",
            "camera_movement": "拉远",
            "narration_segment": (
                "一柱香的时间之后，云河镇杨府别庄的大门洞开，张十三驾着马车"
                "疾驰而出，迅速投入了茫茫夜色之中……"),
            "subtitle": "",
            "ff_desc": (
                "深夜，云河镇杨府别庄大门洞开，一辆马车疾驰冲出门洞，青衣小帽的"
                "张十三亲自坐在车辕上驾车，缰绳拉紧，车灯一点暖黄划破夜色；车厢"
                "帘幕垂落，听香在车厢内。官道两旁柳影婆娑，冷蓝月夜"),
            "lf_desc": (
                "马车疾驰在官道上渐行渐远，车身缩成夜色中的一个小点，只余车灯"
                "一点暖黄，最终没入茫茫夜色，官道空无一人，柳影婆娑"),
            "motion_desc": (
                "前2秒：杨府别庄大门洞开，马车疾驰冲出门洞，车灯划破夜色；"
                "2-4秒：马车疾驰上官道，车轮滚滚，柳影向后掠过；"
                "4-6秒：马车渐行渐远，没入茫茫夜色，只余车灯一点"),
            "keyframes": [
                {"stage": "pre", "t_sec": 1.5,
                 "desc": "深夜，杨府别庄大门洞开，马车疾驰冲出门洞，张十三坐车辕驾车，车灯暖黄"},
                {"stage": "after", "t_sec": 4.5,
                 "desc": "马车疾驰在官道上渐行渐远，没入茫茫夜色，只余车灯一点暖黄"},
            ],
            "char_layout": [
                {"name": "张十三", "position": "车辕上驾车", "status": "缰绳拉紧，目视前方"},
                {"name": "听香", "position": "车厢内", "status": "帘幕垂落，身影隐约"},
            ],
            "prompt": (
                "[张十三: 国字脸，身材矫健，面容冷峻，杨旭贴身伴当] "
                "古风真人竖屏短视频，远景镜头，固定机位，冷蓝月夜。深夜，云河镇"
                "杨府别庄大门洞开，一辆马车疾驰冲出门洞，青衣小帽的张十三亲自坐"
                "在车辕上驾车，缰绳拉紧，车灯一点暖黄划破夜色，车厢帘幕垂落。官"
                "道两旁柳影婆娑，马车疾驰而去渐行渐远，最终没入茫茫夜色。画面冷"
                "蓝暗调，悬念收尾，电影质感，高清1080P。"),
        })
        print("[6] 马车夜奔镜已追加（index=13）")

    # ---- 7. hook ff_desc 重写（清残片 + 空间强化）----
    hook = script.get("hook")
    if hook:
        hook["ff_desc"] = (
            "深夜照月湾，冷蓝月光，一叶窄身无篷舴艋舟（长5.5米宽1.5米）浮于"
            "水面。画右船梢的船板上站着青衣小帽的张十三，双脚在船板上（不在水"
            "中），右手后引刚挥出又收回，厉声喝止；画左船头站着披头散发的听香，"
            "半边脸微肿，身体蜷缩僵住，望向张十三，尖叫戛然而止。一支短刃自画外"
            "斜入画面，刀尖抵近张十三身侧，即将触及。水面泛着银辉，波光轻晃")
        pr = hook.get("prompt", "") or ""
        if "不在水中" not in pr:
            hook["prompt"] = pr.replace(
                "画右船梢站着青衣小帽的年轻男子张十三",
                "画右船梢的船板上站着青衣小帽的年轻男子张十三（双脚踩在船板上，"
                "不在水中）", 1) if "画右船梢站着" in pr else pr
    print("[7] hook 文本强化完成")

    # ---- 8. 重算时间轴 + 落盘 ----
    _compute_timecodes(script)
    backup = CHAPTER.with_suffix(".json.bak_before_0912_repair")
    backup.write_text(CHAPTER.read_text(encoding="utf-8"), encoding="utf-8")
    CHAPTER.write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    score = _score_script(script, novel)
    n_shots = len(script["shots"])
    print(f"[8] 已落盘（备份 {backup.name}）：{n_shots} 镜 + hook | "
          f"打分器（含新重复惩罚）: {score['detail']}")


if __name__ == "__main__":
    main()
