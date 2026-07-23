"""故事板生成服务 — 核心逻辑

利用现有的 _image_gen 服务生成故事板图片。
"""

import os
import asyncio
from pathlib import Path
from typing import Optional
import time
import hashlib

from .models import (
    StoryboardType,
    StoryboardRequest,
    StoryboardResult,
    StoryboardImage,
)
from . import templates
from _image_gen.models import ImageGenerationRequest
from _image_gen import service as image_service


# 输出目录：默认放在 storyboards/ 子目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STORYBOARD_DIR = _PROJECT_ROOT / "output" / "storyboards"


def _get_storyboard_output_dir(storyboard_type: str) -> Path:
    """获取故事板输出目录"""
    output_dir = _STORYBOARD_DIR / storyboard_type
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _compute_prompt_hash(prompt: str) -> str:
    """计算 prompt 的哈希值用于缓存检查"""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


class StoryboardGenerator:
    """故事板生成器

    支持7种故事板类型，自动调用图片生成服务。
    """

    def __init__(self, provider: Optional[str] = None):
        """初始化

        Args:
            provider: 图片供应商名称，默认从 .env 读取
        """
        self.image_service = image_service.create_service(provider)

    def generate(self, request: StoryboardRequest) -> StoryboardResult:
        """同步生成故事板（主入口）

        Args:
            request: 故事板生成请求

        Returns:
            StoryboardResult: 包含生成的图片列表
        """
        # 计算 prompt
        prompt = templates.get_storyboard_prompt(request)

        # 根据类型确定输出
        storyboard_type = request.storyboard_type.value

        # 计算输出目录
        output_dir = _get_storyboard_output_dir(storyboard_type)
        timestamp = int(time.time())
        storyboard_dir = output_dir / f"{storyboard_type}_{timestamp}"
        storyboard_dir.mkdir(parents=True, exist_ok=True)

        # 构建图片生成请求
        img_req = ImageGenerationRequest(
            prompt=prompt,
            shot_index=0,
            # 故事板使用 2K 尺寸保证清晰度
            size="2K",
            ratio="9:16" if storyboard_type in ["per_shot", "character_set"] else "16:9",
            frame_desc=f"storyboard_{storyboard_type}",
        )

        result = StoryboardResult(storyboard_type=storyboard_type)

        try:
            # 调用图片生成服务
            img_result = self.image_service.generate(img_req)

            # 记录结果
            image = StoryboardImage(
                name="main",
                prompt=prompt,
                save_path=img_result.save_path,
                success=img_result.success,
                error=img_result.error,
            )
            result.images.append(image)

            if img_result.success:
                # 生成缩略版/不同尺寸用于预览
                self._generate_variants(prompt, storyboard_dir, request)

            result.success = img_result.success
            result.error = img_result.error

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.images.append(StoryboardImage(
                name="main",
                prompt=prompt,
                success=False,
                error=str(e),
            ))

        return result

    def _generate_variants(
        self,
        prompt: str,
        output_dir: Path,
        request: StoryboardRequest,
    ) -> list[str]:
        """生成变体版本（可选）

        为同一 prompt 生成不同尺寸/比例的变体。
        """
        variants = []
        storyboard_type = request.storyboard_type.value

        # 16:9 变体（用于某些场景类故事板）
        if storyboard_type in ["scene_fixed", "scene_plan", "scene_overview"]:
            variant_req = ImageGenerationRequest(
                prompt=prompt,
                shot_index=0,
                size="2K",
                ratio="16:9",
                frame_desc=f"storyboard_{storyboard_type}_16x9",
            )
            try:
                result = self.image_service.generate(variant_req)
                if result.success:
                    variants.append(result.save_path)
            except Exception:
                pass

        return variants

    async def generate_async(self, request: StoryboardRequest) -> StoryboardResult:
        """异步生成故事板（未来扩展用）

        目前同步实现，后续可扩展为真正的异步。
        """
        return self.generate(request)


# ============== 便捷函数 ==============


def generate_character_storyboard(
    characters: list,
    style_reference: str = "",
    output_dir: Optional[Path] = None,
) -> StoryboardResult:
    """快速生成角色设定故事板

    Args:
        characters: 角色规格列表 [{"name": "...", "description": "...", ...}]
        style_reference: 风格参考描述
        output_dir: 自定义输出目录（可选）

    Returns:
        StoryboardResult
    """
    from .models import CharacterSpec, StoryboardRequest

    char_specs = [
        CharacterSpec(
            name=c["name"],
            description=c["description"],
            features_static=c.get("features_static", ""),
            features_dynamic=c.get("features_dynamic", ""),
            personality=c.get("personality", ""),
        )
        for c in characters
    ]

    request = StoryboardRequest(
        storyboard_type=StoryboardType.CHARACTER_SET,
        characters=char_specs,
        style_reference=style_reference,
    )

    generator = StoryboardGenerator()
    return generator.generate(request)


def generate_scene_fixed_storyboard(
    scene: dict,
    characters: list = None,
    output_dir: Optional[Path] = None,
) -> StoryboardResult:
    """快速生成场景固定故事板

    Args:
        scene: 场景规格 {"name": "...", "description": "...", "time_period": "...", "mood": "...", "props": [...]}
        characters: 角色列表（可选）
        output_dir: 自定义输出目录（可选）

    Returns:
        StoryboardResult
    """
    from .models import SceneSpec, StoryboardRequest, CharacterSpec

    scene_spec = SceneSpec(
        name=scene.get("name", ""),
        description=scene.get("description", ""),
        time_period=scene.get("time_period", ""),
        mood=scene.get("mood", ""),
        props=scene.get("props", []),
    )

    char_specs = []
    if characters:
        char_specs = [
            CharacterSpec(name=c["name"], description=c.get("description", ""))
            for c in characters
        ]

    request = StoryboardRequest(
        storyboard_type=StoryboardType.SCENE_FIXED,
        scene=scene_spec,
        characters=char_specs,
    )

    generator = StoryboardGenerator()
    return generator.generate(request)


def generate_motion_keyframes_storyboard(
    motion: dict,
    output_dir: Optional[Path] = None,
) -> StoryboardResult:
    """快速生成动作拆解故事板

    Args:
        motion: 动作规格 {"name": "...", "description": "...", "duration_seconds": 3.0, "keyframes": [...]}
        output_dir: 自定义输出目录（可选）

    Returns:
        StoryboardResult
    """
    from .models import MotionSpec, StoryboardRequest

    motion_spec = MotionSpec(
        name=motion.get("name", ""),
        description=motion.get("description", ""),
        duration_seconds=motion.get("duration_seconds", 3.0),
        difficulty=motion.get("difficulty", "medium"),
        keyframes=motion.get("keyframes", []),
    )

    request = StoryboardRequest(
        storyboard_type=StoryboardType.MOTION_KEYFRAMES,
        motion=motion_spec,
    )

    generator = StoryboardGenerator()
    return generator.generate(request)


def generate_per_shot_storyboard(
    shots: list,
    output_dir: Optional[Path] = None,
) -> StoryboardResult:
    """快速生成逐镜执行故事板

    Args:
        shots: 逐镜参数列表，每个包含 duration, shot_size, camera_movement, lighting 等
        output_dir: 自定义输出目录（可选）

    Returns:
        StoryboardResult
    """
    from .models import PerShotSpec, ShotSize, CameraMovement, LightingStyle, StoryboardRequest

    shot_specs = []
    for i, shot in enumerate(shots):
        shot_specs.append(PerShotSpec(
            shot_index=i,
            duration_seconds=shot.get("duration_seconds", 3.0),
            shot_size=ShotSize(shot.get("shot_size", "medium")),
            camera_movement=CameraMovement(shot.get("camera_movement", "static")),
            lighting=LightingStyle(shot.get("lighting", "natural")),
            narration=shot.get("narration", ""),
            sound_effect=shot.get("sound_effect", ""),
            prompt_supplement=shot.get("prompt_supplement", ""),
        ))

    request = StoryboardRequest(
        storyboard_type=StoryboardType.PER_SHOT,
        shots=shot_specs,
    )

    generator = StoryboardGenerator()
    return generator.generate(request)


def generate_from_character_bank(
    chapter_num: int | None = None,
    style_reference: str = "",
) -> StoryboardResult:
    """从已有 CharacterBank 自动生成角色设定故事板.

    读取 characters_bank.json 中所有已注册角色，自动生成四视图。
    这是一个"存量对接"的桥接函数。

    Args:
        chapter_num: 仅生成指定章节的角色，None 表示全部
        style_reference: 风格参考描述

    Returns:
        StoryboardResult
    """
    from shared.character_bank import CharacterBank
    from .models import CharacterSpec, StoryboardRequest

    cb = CharacterBank()
    cb.load()

    characters = []
    for identifier, char in cb._data.get("characters", {}).items():
        if chapter_num is not None:
            first = char.get("first_chapter", 0)
            last = char.get("last_chapter", 0)
            if not (first <= chapter_num <= last):
                continue
        characters.append(CharacterSpec(
            name=identifier,
            description=f"{char.get('static_features', '')} {char.get('dynamic_features', '')}".strip(),
            features_static=char.get("static_features", ""),
            features_dynamic=char.get("dynamic_features", ""),
            personality="",
        ))

    if not characters:
        raise ValueError(f"章节 {chapter_num} 无已注册角色，请先运行 step01 或确保 characters_bank 有数据")

    request = StoryboardRequest(
        storyboard_type=StoryboardType.CHARACTER_SET,
        characters=characters,
        style_reference=style_reference,
    )

    generator = StoryboardGenerator()
    return generator.generate(request)


def generate_scene_from_script(
    script_path: str | Path,
    scene_name: str = "",
) -> StoryboardResult:
    """从 script_result.json 自动提取场景信息生成场景固定故事板.

    读取分镜脚本中的场景描述和道具列表。

    Args:
        script_path: script_result.json 的路径
        scene_name: 场景名称（空则自动取脚本中的场景）

    Returns:
        StoryboardResult
    """
    import json
    from .models import SceneSpec, CharacterSpec, StoryboardRequest

    script_path = Path(script_path)
    if not script_path.exists():
        raise FileNotFoundError(f"脚本文件不存在: {script_path}")

    with open(script_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 提取场景信息
    scene_desc = scene_name or data.get("scene_name", "") or data.get("location", "")

    # 提取角色
    characters = []
    for char_data in data.get("characters", []):
        characters.append(CharacterSpec(
            name=char_data.get("name", char_data.get("identifier", "")),
            description=char_data.get("description", ""),
            features_static=char_data.get("static_features", ""),
            features_dynamic=char_data.get("dynamic_features", ""),
        ))

    scene_spec = SceneSpec(
        name=scene_desc or "Scene",
        description=scene_desc,
        props=[],  # 道具由 LLM 分析或手动指定
    )

    request = StoryboardRequest(
        storyboard_type=StoryboardType.SCENE_FIXED,
        scene=scene_spec,
        characters=characters,
    )

    generator = StoryboardGenerator()
    return generator.generate(request)