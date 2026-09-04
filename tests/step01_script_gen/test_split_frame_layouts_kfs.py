"""v6.57+ B9 拆分镜 frame_layouts.kfs 截取的测试.

timecode._do_pre_split_oversized_shots 拆分时：
  - 截取 keyframes 后半段到 shot_b
  - 同步截取 frame_layouts.kfs（按 desc 匹配原 kfs）

v6.57+ FrameSpecConsistencyConstraint 强制：kfs 数量 == keyframes 数量。
所以 B9 的核心价值：拆分后 shot_b.frame_layouts.kfs 数量必须等于 shot_b.keyframes，
否则会触发 FrameSpecConsistency HARD → regen 修复轮。

覆盖：
  1. 拆镜后 shot_b.keyframes 数量 + shot_b.frame_layouts.kfs 数量一致
  2. 拆镜后 shot_b.frame_layouts.kfs 内容是原 kfs 的子集
  3. 拆镜后 shot_b.frame_layouts 仍有 ff/lf 桶（与 kfs 桶独立）
  4. shot 无 frame_layouts 时不报错
  5. shot 全部 kf 都在前半段时（split_ratio 大），shot_b 拿不到 kfs
  6. 原 kfs 与 layout.kfs 数量不等时不崩（部分对应）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_oversized_shot(*, with_frame_layouts: bool = True,
                          kfs_layout_match: bool = True,
                          shot_cap: float = 8.0) -> dict:
    """构造一个会被拆镜的长 shot（含 keyframes + frame_layouts）.

    设计要点：narr 需让 _find_split_point_within_cap 在 [0.2*len, 0.8*len] 区间
    找到"两段都 ≤ cap"的句号。30-50 字符 narr + cap=8s 是甜区：
      - 估时 > cap（9-12s），触发拆镜
      - 区间 [0.2*len, 0.8*len] 至少含 1 个句号切点，两段都 ≤ cap
    """
    # 35 字 narr 估时 ≈ 10s（> 8s cap），切两段都 ≤ 8s ✓
    narr = (
        "杨旭沿街奔跑，脚步急促。他忽然停下脚步，侧耳倾听。"
        "江面上传来阵阵笛声。"
    )
    # 4 个 kf：3 在前半段 + 1 在后半段
    # duration=10s，split 在 ≈ t=5（按 narr 比例 0.5 估）
    orig_kfs = [
        {"desc": "杨旭缓步", "t_sec": 1.0, "stage": "pre"},
        {"desc": "杨旭停下侧耳", "t_sec": 3.5, "stage": "pre"},
        {"desc": "杨旭按住刀柄", "t_sec": 4.5, "stage": "approach"},
        {"desc": "杨旭拔刀迎敌", "t_sec": 7.0, "stage": "climax"},
    ]
    shot = {
        "index": 1,
        "prompt": "杨旭沿江边行走",
        "ff_desc": "远景：江边月色",
        "lf_desc": "近景：杨旭拔刀",
        "motion_desc": "杨旭缓步走，侧耳听，按刀戒备，拔刀迎敌",
        "narration_segment": narr,
        "subtitle": narr,
        "duration_sec": 10.0,
        "duration": 10.0,
        "location": "江边",
        "keyframes": orig_kfs,
    }
    if with_frame_layouts:
        kfs_layout = []
        for kf in orig_kfs:
            item = {
                "name": kf.get("desc", ""),
                "on_screen": True,
                "position": "画中",
                "pose": "站立",
                "gaze_target": "",
                "props": ["刀"],
            }
            kfs_layout.append(item)
        if not kfs_layout_match and with_frame_layouts:
            # 故意让 kfs_layout 比 orig_kfs 少 1 个
            kfs_layout = kfs_layout[:-1]
        shot["frame_layouts"] = {
            "ff": [{
                "name": "杨旭", "on_screen": True, "position": "画中",
                "pose": "行走", "gaze_target": "前方", "props": ["刀"],
            }],
            "lf": [{
                "name": "杨旭", "on_screen": True, "position": "画中",
                "pose": "拔刀", "gaze_target": "敌人", "props": ["刀"],
            }],
            "kfs": kfs_layout,
        }
    return shot


def test_split_preserves_kf_layout_count_alignment():
    """拆镜后 shot_b.keyframes 数量 == shot_b.frame_layouts.kfs 数量."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shot = _make_oversized_shot()
    shots_list = [shot]
    # cap=8s = MAX_SHOT_DURATION，让"两段都 ≤ cap"能成立
    assert _do_pre_split_oversized_shots(shots_list, shot_cap=8.0) == 1
    assert len(shots_list) == 2
    shot_a, shot_b = shots_list

    # shot_b 的 keyframes 和 frame_layouts.kfs 数量一致（v6.57 S 约束需要）
    assert len(shot_b["keyframes"]) == len(shot_b["frame_layouts"]["kfs"])
    # 原 kf 有 1 个在 t=7 落在后半段（duration=10s, split ≈ t=5）
    assert len(shot_b["keyframes"]) >= 1


def test_split_kf_layout_is_subset_of_orig():
    """shot_b.frame_layouts.kfs 内容是原 kfs layout 的子集."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shot = _make_oversized_shot()
    orig_layout_kfs = list(shot["frame_layouts"]["kfs"])

    shots_list = [shot]
    _do_pre_split_oversized_shots(shots_list, shot_cap=8.0)
    shot_a, shot_b = shots_list

    # shot_b.frame_layouts.kfs 内容是原 kfs layout 的子集
    new_layout_kfs = shot_b["frame_layouts"]["kfs"]
    # 新 layout 的每个 name 都在原 layout name 集合里
    orig_names = {item["name"] for item in orig_layout_kfs}
    for item in new_layout_kfs:
        assert item["name"] in orig_names


def test_split_preserves_ff_lf_buckets():
    """shot_b.frame_layouts 的 ff/lf 桶与 kfs 桶独立，不被截取影响."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shot = _make_oversized_shot()
    shots_list = [shot]
    _do_pre_split_oversized_shots(shots_list, shot_cap=8.0)
    shot_a, shot_b = shots_list

    # ff/lf 桶都还在（拆镜只动 kfs 桶）
    assert "ff" in shot_b["frame_layouts"]
    assert "lf" in shot_b["frame_layouts"]
    assert isinstance(shot_b["frame_layouts"]["ff"], list)
    assert isinstance(shot_b["frame_layouts"]["lf"], list)


def test_split_no_frame_layouts_no_crash():
    """shot 无 frame_layouts 时拆镜不报错."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shot = _make_oversized_shot(with_frame_layouts=False)
    shots_list = [shot]
    # 不应抛异常
    _do_pre_split_oversized_shots(shots_list, shot_cap=8.0)
    shot_a, shot_b = shots_list

    # shot_a 没 frame_layouts，shot_b（deepcopy 复制）也没有；截取逻辑跳过
    assert "frame_layouts" not in shot_b or shot_b.get("frame_layouts") is None


def test_split_layout_kfs_shorter_than_orig_kfs_no_crash():
    """原 frame_layouts.kfs 比 orig_kfs 少（kfs_layout_match=False）时不崩."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    shot = _make_oversized_shot(kfs_layout_match=False)
    # 原 layout kfs 应该是 3 个（少 1 个）
    assert len(shot["frame_layouts"]["kfs"]) == 3
    assert len(shot["keyframes"]) == 4

    shots_list = [shot]
    # 不应抛 IndexError
    _do_pre_split_oversized_shots(shots_list, shot_cap=8.0)
    shot_a, shot_b = shots_list

    # shot_b.keyframes 数量 == shot_b.frame_layouts.kfs 数量（一致性约束需要）
    assert len(shot_b["keyframes"]) == len(shot_b["frame_layouts"]["kfs"])


def test_split_all_kfs_in_first_half():
    """原 kf 全部在前半段时，shot_b 拿不到 kf——frame_layouts.kfs 也应为空.

    这是 **关键 bug 验证**：
      timecode.py line 202 条件 `len(new_kfs) > 0` 失败时，跳过
      frame_layouts.kfs 截取 → shot_b（deepcopy 复制）仍带原 layout.kfs
      → v6.57 S 约束报「kfs 数量 ≠ keyframes 数量」HARD → 触发 regen

    测试预期：发现这个 bug 并把测试标为"已知 bug"（先记录，修复后续上）
    """
    from step01_script_gen.timecode import _do_pre_split_oversized_shots

    # narr 长度让 _find_split_point_within_cap 找到切点
    # kf 全在前 4s，duration=10s 切点 ≈ t=5 → kf 全部在前半段
    narr = (
        "杨旭沿街奔跑，脚步急促。他忽然停下脚步，侧耳倾听。"
        "江面上传来阵阵笛声。"
    )
    shot = {
        "index": 1,
        "prompt": "杨旭",
        "ff_desc": "远景",
        "lf_desc": "近景",
        "motion_desc": "杨旭缓步走",
        "narration_segment": narr,
        "subtitle": narr,
        "duration_sec": 10.0,
        "duration": 10.0,
        "location": "江边",
        "keyframes": [
            {"desc": "杨旭缓步", "t_sec": 0.5, "stage": "pre"},
            {"desc": "杨旭停下", "t_sec": 1.5, "stage": "pre"},
        ],
        "frame_layouts": {
            "ff": [{"name": "杨旭", "on_screen": True, "position": "画中",
                    "pose": "行走", "gaze_target": "", "props": []}],
            "lf": [{"name": "杨旭", "on_screen": True, "position": "画中",
                    "pose": "停顿", "gaze_target": "", "props": []}],
            "kfs": [
                {"name": "杨旭缓步", "on_screen": True, "position": "画中",
                 "pose": "行走", "gaze_target": "", "props": []},
                {"name": "杨旭停下", "on_screen": True, "position": "画中",
                 "pose": "停", "gaze_target": "", "props": []},
            ],
        },
    }

    shots_list = [shot]
    _do_pre_split_oversized_shots(shots_list, shot_cap=8.0)
    # 拆镜成功
    assert len(shots_list) == 2
    shot_a, shot_b = shots_list

    # shot_b 应拿不到 kf（都在前半段）
    # 实际：35 字 narr 在 t≈2-3s 切，kf t=1.0/1.5 都在切点前 → 0 kf
    assert len(shot_b["keyframes"]) == 0, (
        f"期望 shot_b 无 kf，实际 {len(shot_b['keyframes'])} 个——"
        f"可能 narr 切点比预期晚"
    )
    # B9 修复后：frame_layouts.kfs 必为 0
    assert len(shot_b["frame_layouts"]["kfs"]) == 0
    # 一致性：kfs 数量 == keyframes 数量
    assert len(shot_b["keyframes"]) == len(shot_b["frame_layouts"]["kfs"])
