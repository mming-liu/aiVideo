"""AI漫剧场景资产生成 - 大场景建立镜头 + 近景功能场景

方法论（references/ai-scene-prop-asset-guide.md）：
  1. 大场景建立镜头：确定地理环境、时间背景、天气色调和空间关系，
     所有后续镜头都以该图为统一参考（画面中不得出现人物）
  2. 近景功能场景：从大场景图派生（图生图参考），保证与同一地点的
     季节、光线、年代、画面风格一致（画面中不得出现人物）

与 CharacterPortraitsGenerator 同构：生成器 → 专用横屏适配器 → bank 持久化。
"""
from __future__ import annotations
from shared.style_prefix import asset_style_prefix

# 大场景建立镜头（wide_desc → 大场景图，无人物，横屏参考）
PROMPT_WIDE = (
    "{style}，16:9横屏，超广角全景建立镜头。"
    "画面中不得出现任何人物、身影、人影、轮廓或人形，也不得出现人物特写。"
    "{wide_desc}"
    "真实{env_material}纹理、自然空间纵深、清晰前中后景层次、轻微空气透视，"
    "保持指定画风，高清细节，画面自然可信。"
)

# 近景功能场景（near_desc → 近景图，引用大场景图，强制同地点）
PROMPT_NEAR = (
    "严格参考上传的{style}大场景参考图，提取并生成同一地点内部的近景功能场景。"
    "必须保持与参考图相同的季节、时间、天气、自然色调、光线方向、"
    "年代风格和画面风格。画面中不得出现任何人物、身影、人影、轮廓或人形。"
    "16:9横屏，近景/中近景构图，平视角度，镜头位于场景内部功能空间。"
    "{near_desc}"
    "保持指定画风与材质细节，高清自然，画面必须与大场景属于同一个地点，"
    "不能变成另一处场景。"
)

# v6.23：侧面全景（side_desc → 侧面全景图，引用大场景图，同一地点的侧面视角）
PROMPT_SIDE = (
    "严格参考上传的{style}大场景参考图，提取并生成同一地点的侧面全景视角。"
    "必须保持与参考图相同的季节、时间、天气、自然色调、光线方向、"
    "年代风格和画面风格。画面中不得出现任何人物、身影、人影、轮廓或人形。"
    "16:9横屏，侧面全景构图，展示地点的纵深与空间关系，镜头从侧面拍摄。{side_desc}"
    "保持指定画风与材质细节，高清自然，画面必须与大场景属于同一个地点。"
)


class SceneAssetsGenerator:
    """AI漫剧场景资产生成器 - 大场景优先 + 近景派生.

    生成流程：
    1. generate_wide() — 生成大场景建立镜头图，锁定地理/时间/天气色调（首次）
    2. generate_near() — 基于大场景图生成近景功能场景图（保持同一地点一致性）
    3. generate_side() — 基于大场景图生成侧面全景图（v6.23 多视图）
    """

    def __init__(self, image_generator):
        self.image_generator = image_generator

    async def generate_wide(self, scene: dict, style: str = "",
                            save_path: str | None = None) -> object:
        """生成大场景建立镜头图（无人物）.

        scene: scene_assets bank 条目（含 wide_desc/time_of_day/weather/color_tone）
        save_path: 资产图直接落盘路径（否则走 provider 默认帧路径，会污染 hook 首帧）
        Returns: ImageOutput
        """
        wide_desc = (scene.get("wide_desc") or "").strip()
        if not wide_desc:
            raise ValueError(f"场景 {scene.get('identifier', '?')} 缺少 wide_desc，无法生成大场景图")
        env_material = _guess_env_material(wide_desc)
        prompt = PROMPT_WIDE.format(
            style=style or asset_style_prefix("16:9"),
            wide_desc=wide_desc,
            env_material=env_material,
        )
        return await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="16:9",
            save_path=save_path,
        )

    async def generate_near(self, scene: dict, wide_image_path: str,
                            style: str = "",
                            save_path: str | None = None) -> object:
        """基于大场景图生成近景功能场景图（无人物）.

        near_desc 缺失时跳过（返回 None），下游只保留大场景图。
        """
        near_desc = (scene.get("near_desc") or "").strip()
        if not near_desc:
            return None
        prompt = PROMPT_NEAR.format(
            style=style or asset_style_prefix("16:9"),
            near_desc=near_desc,
        )
        return await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="16:9",
            reference_image_paths=[wide_image_path],
            save_path=save_path,
        )

    async def generate_side(self, scene: dict, wide_image_path: str,
                            style: str = "",
                            save_path: str | None = None) -> object:
        """基于大场景图生成侧面全景图（v6.23 多视图）.

        side_desc 缺失时用 wide_desc 兜底；无 wide_desc 则返回 None。
        """
        wide_desc = (scene.get("wide_desc") or "").strip()
        if not wide_desc:
            return None
        side_desc = (scene.get("side_desc") or wide_desc).strip()
        prompt = PROMPT_SIDE.format(style=style)
        return await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="16:9",
            reference_image_paths=[wide_image_path],
            save_path=save_path,
        )


# 环境材质词表：根据大场景描述推断"环境主体材质"，写进大场景 prompt 加强真实感
_ENV_MATERIAL_KEYWORDS = {
    "水面": "水、波纹与倒影",
    "水": "水、波纹与倒影",
    "湖": "水、波纹与倒影",
    "荷": "荷叶、芦苇与水生植物",
    "山": "山石、泥土与植被",
    "坡": "泥土、植被与碎石",
    "田": "泥土、作物与田埂",
    "树": "树皮、枝叶与地表植被",
    "林": "树皮、枝叶与地表植被",
    "街": "青石板、屋瓦与木构",
    "屋": "屋瓦、墙体与木构",
    "院": "青砖、土墙与庭院地面",
    "殿": "木构梁柱、石阶与墙面",
    "城": "砖石城墙与城门地面",
    "路": "泥土或石板路面",
}


def _guess_env_material(desc: str) -> str:
    """从描述中推断环境主体材质，用于大场景 prompt 的材质词."""
    for kw, mat in _ENV_MATERIAL_KEYWORDS.items():
        if kw in desc:
            return mat
    return "自然环境"


# 从 bank 条目中提取场景数据（供 asset_gen 调用，与 SceneAssetsBank 结构解耦）
def scene_assets_from_bank(entry: dict) -> dict:
    """把 SceneAssetsBank 条目转成生成器所需 dict."""
    return {
        "identifier": entry.get("identifier", ""),
        "wide_desc": entry.get("wide_desc", ""),
        "near_desc": entry.get("near_desc", ""),
        "time_of_day": entry.get("time_of_day", ""),
        "weather": entry.get("weather", ""),
        "color_tone": entry.get("color_tone", ""),
        "location": entry.get("location", ""),
    }
