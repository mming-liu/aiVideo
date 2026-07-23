#!/usr/bin/env python
"""故事板生成命令行工具

用法示例:
    # 生成角色设定故事板
    python _storyboard/main.py character -c "name:张三,description:身穿青色长袍,features_static:头戴斗笠,features_dynamic:沉默寡言"

    # 生成场景固定故事板
    python _storyboard/main.py scene-fixed -s "description:破败的山庙,time_period:夜晚,mood:阴森,props:佛像,蜡烛,供桌"

    # 生成动作拆解故事板
    python _storyboard/main.py motion-keyframes -m "name:打斗,description:一套连贯的拳法,duration:3,keyframes:起势|冲拳|摆拳|踢腿|收势"

    # 生成逐镜执行故事板
    python _storyboard/main.py per-shot -h "duration:3,shot_size:medium,camera:static,lighting:natural"

    # 查看所有可用类型
    python _storyboard/main.py --help
"""

import argparse
import sys
from pathlib import Path

# 添加项目根目录到路径
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from _storyboard.models import (
    StoryboardType,
    CharacterSpec,
    SceneSpec,
    MotionSpec,
    PerShotSpec,
    ShotSize,
    CameraMovement,
    LightingStyle,
    StoryboardRequest,
)
from _storyboard.service import StoryboardGenerator


def parse_key_value(kv_str: str, sep: str = ",", eq: str = ":") -> dict:
    """解析 key:value,key:value 格式的字符串"""
    result = {}
    if not kv_str:
        return result
    for item in kv_str.split(sep):
        if eq in item:
            key, value = item.split(eq, 1)
            result[key.strip()] = value.strip()
    return result


def parse_list_value(list_str: str, sep: str = "|") -> list:
    """解析 a|b|c 格式的列表字符串"""
    if not list_str:
        return []
    return [s.strip() for s in list_str.split(sep) if s.strip()]


def cmd_character(args):
    """生成角色设定故事板"""
    char_data = parse_key_value(args.char, eq=",")
    characters = [CharacterSpec(
        name=char_data.get("name", "Unknown"),
        description=char_data.get("description", ""),
        features_static=char_data.get("features_static", ""),
        features_dynamic=char_data.get("features_dynamic", ""),
        personality=char_data.get("personality", ""),
    )]

    request = StoryboardRequest(
        storyboard_type=StoryboardType.CHARACTER_SET,
        characters=characters,
        style_reference=args.style or "",
    )

    generator = StoryboardGenerator()
    result = generator.generate(request)

    if result.success:
        print(f"✓ 角色设定故事板生成成功")
        print(f"  图片: {result.images[0].save_path}")
    else:
        print(f"✗ 生成失败: {result.error}")
        sys.exit(1)


def cmd_scene_fixed(args):
    """生成场景固定故事板"""
    scene_data = parse_key_value(args.scene, eq=",")
    scene = SceneSpec(
        name=scene_data.get("name", "Scene"),
        description=scene_data.get("description", ""),
        time_period=scene_data.get("time_period", ""),
        mood=scene_data.get("mood", ""),
        props=parse_list_value(scene_data.get("props", "")),
    )

    request = StoryboardRequest(
        storyboard_type=StoryboardType.SCENE_FIXED,
        scene=scene,
    )

    generator = StoryboardGenerator()
    result = generator.generate(request)

    if result.success:
        print(f"✓ 场景固定故事板生成成功")
        print(f"  图片: {result.images[0].save_path}")
    else:
        print(f"✗ 生成失败: {result.error}")
        sys.exit(1)


def cmd_scene_plan(args):
    """生成场景规划故事板"""
    scene_data = parse_key_value(args.scene, eq=",")
    scene = SceneSpec(
        name=scene_data.get("name", "Scene"),
        description=scene_data.get("description", ""),
        layout=scene_data.get("layout", ""),
        props=parse_list_value(scene_data.get("props", "")),
    )

    request = StoryboardRequest(
        storyboard_type=StoryboardType.SCENE_PLAN,
        scene=scene,
    )

    generator = StoryboardGenerator()
    result = generator.generate(request)

    if result.success:
        print(f"✓ 场景规划故事板生成成功")
        print(f"  图片: {result.images[0].save_path}")
    else:
        print(f"✗ 生成失败: {result.error}")
        sys.exit(1)


def cmd_scene_overview(args):
    """生成场景概览故事板"""
    scene_data = parse_key_value(args.scene, eq=",")
    scene = SceneSpec(
        name=scene_data.get("name", "Scene"),
        description=scene_data.get("description", ""),
        time_period=scene_data.get("time_period", ""),
        mood=scene_data.get("mood", ""),
        layout=scene_data.get("layout", ""),
        props=parse_list_value(scene_data.get("props", "")),
    )

    request = StoryboardRequest(
        storyboard_type=StoryboardType.SCENE_OVERVIEW,
        scene=scene,
    )

    generator = StoryboardGenerator()
    result = generator.generate(request)

    if result.success:
        print(f"✓ 场景概览故事板生成成功")
        print(f"  图片: {result.images[0].save_path}")
    else:
        print(f"✗ 生成失败: {result.error}")
        sys.exit(1)


def cmd_motion_keyframes(args):
    """生成动作拆解故事板"""
    motion_data = parse_key_value(args.motion, eq=",")
    motion = MotionSpec(
        name=motion_data.get("name", "Motion"),
        description=motion_data.get("description", ""),
        duration_seconds=float(motion_data.get("duration", "3.0")),
        difficulty=motion_data.get("difficulty", "medium"),
        keyframes=parse_list_value(motion_data.get("keyframes", "")),
    )

    request = StoryboardRequest(
        storyboard_type=StoryboardType.MOTION_KEYFRAMES,
        motion=motion,
    )

    generator = StoryboardGenerator()
    result = generator.generate(request)

    if result.success:
        print(f"✓ 动作拆解故事板生成成功")
        print(f"  图片: {result.images[0].save_path}")
    else:
        print(f"✗ 生成失败: {result.error}")
        sys.exit(1)


def cmd_motion_design(args):
    """生成动作设计故事板"""
    import json

    # 尝试解析 JSON
    if args.motion.startswith("{"):
        motion_data = json.loads(args.motion)
    else:
        motion_data = parse_key_value(args.motion, eq=",")

    if args.scene:
        if args.scene.startswith("{"):
            scene_data = json.loads(args.scene)
        else:
            scene_data = parse_key_value(args.scene, eq=",")
        scene = SceneSpec(
            name=scene_data.get("name", "Scene"),
            description=scene_data.get("description", ""),
            layout=scene_data.get("layout", ""),
        )
    else:
        scene = None

    motion = MotionSpec(
        name=motion_data.get("name", "Motion"),
        description=motion_data.get("description", ""),
        duration_seconds=motion_data.get("duration_seconds", 5.0),
        keyframes=motion_data.get("keyframes", []),
    )

    request = StoryboardRequest(
        storyboard_type=StoryboardType.MOTION_DESIGN,
        motion=motion,
        scene=scene,
    )

    generator = StoryboardGenerator()
    result = generator.generate(request)

    if result.success:
        print(f"✓ 动作设计故事板生成成功")
        print(f"  图片: {result.images[0].save_path}")
    else:
        print(f"✗ 生成失败: {result.error}")
        sys.exit(1)


def cmd_per_shot(args):
    """生成逐镜执行故事板"""
    import json

    # 支持单Shot 或 JSON 数组
    if args.shots.startswith("["):
        shots_data = json.loads(args.shots)
    else:
        # 单个shot
        shot_data = parse_key_value(args.shots, eq=",")
        shots_data = [shot_data]

    shot_specs = []
    for i, s in enumerate(shots_data):
        shot_specs.append(PerShotSpec(
            shot_index=i,
            duration_seconds=s.get("duration_seconds", s.get("duration", 3.0)),
            shot_size=ShotSize(s.get("shot_size", s.get("shot_size", "medium"))),
            camera_movement=CameraMovement(s.get("camera_movement", s.get("camera", "static"))),
            lighting=LightingStyle(s.get("lighting", "natural")),
            narration=s.get("narration", ""),
            sound_effect=s.get("sound_effect", s.get("sound", "")),
            prompt_supplement=s.get("prompt_supplement", ""),
        ))

    request = StoryboardRequest(
        storyboard_type=StoryboardType.PER_SHOT,
        shots=shot_specs,
    )

    generator = StoryboardGenerator()
    result = generator.generate(request)

    if result.success:
        print(f"✓ 逐镜执行故事板生成成功")
        print(f"  图片: {result.images[0].save_path}")
    else:
        print(f"✗ 生成失败: {result.error}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="故事板生成工具 - 7种可控视频生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 角色设定故事板
  python _storyboard/main.py character -c "name:张三,description:身穿青袍,features_static:头戴斗笠"

  # 场景固定故事板
  python _storyboard/main.py scene-fixed -s "description:破败山庙,time_period:夜,mood:阴森"

  # 动作拆解故事板
  python _storyboard/main.py motion-keyframes -m "name:打斗,description:拳法,duration:3"

  # 逐镜执行故事板 (JSON格式)
  python _storyboard/main.py per-shot -h '[{"duration":3,"shot_size":"medium"}]'

可用类型:
  character        - 角色设定故事板（四视图+动作参考）
  scene-fixed      - 场景固定故事板（单一场景固定底图）
  scene-plan       - 场景规划故事板（空间结构+动线）
  scene-overview   - 场景概览故事板（鸟瞰图）
  motion-keyframes - 动作拆解故事板（关键帧拆解）
  motion-design    - 动作设计故事板（分镜+动线）
  per-shot         - 逐镜执行故事板（完整参数表）
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="故事板类型")

    # 角色设定
    p_char = subparsers.add_parser("character", help="生成角色设定故事板")
    p_char.add_argument("-c", "--char", required=True, help="角色参数，格式: name:张三,description:描述,...")
    p_char.add_argument("-s", "--style", default="", help="风格参考")
    p_char.set_defaults(func=cmd_character)

    # 场景固定
    p_sf = subparsers.add_parser("scene-fixed", help="生成场景固定故事板")
    p_sf.add_argument("-s", "--scene", required=True, help="场景参数，格式: description:描述,time_period:时间,mood:氛围,props:道具列表")
    p_sf.set_defaults(func=cmd_scene_fixed)

    # 场景规划
    p_sp = subparsers.add_parser("scene-plan", help="生成场景规划故事板")
    p_sp.add_argument("-s", "--scene", required=True, help="场景参数")
    p_sp.set_defaults(func=cmd_scene_plan)

    # 场景概览
    p_so = subparsers.add_parser("scene-overview", help="生成场景概览故事板")
    p_so.add_argument("-s", "--scene", required=True, help="场景参数")
    p_so.set_defaults(func=cmd_scene_overview)

    # 动作拆解
    p_mk = subparsers.add_parser("motion-keyframes", help="生成动作拆解故事板")
    p_mk.add_argument("-m", "--motion", required=True, help="动作参数，格式: name:名称,description:描述,duration:时长,keyframes:关键帧列表")
    p_mk.set_defaults(func=cmd_motion_keyframes)

    # 动作设计
    p_md = subparsers.add_parser("motion-design", help="生成动作设计故事板")
    p_md.add_argument("-m", "--motion", required=True, help="动作参数 (JSON 或 key:value 格式)")
    p_md.add_argument("-s", "--scene", default="", help="场景参数 (可选)")
    p_md.set_defaults(func=cmd_motion_design)

    # 逐镜执行
    p_ps = subparsers.add_parser("per-shot", help="生成逐镜执行故事板")
    p_ps.add_argument("-h", "--shots", required=True, help="镜头参数 (JSON 数组)")
    p_ps.set_defaults(func=cmd_per_shot)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()