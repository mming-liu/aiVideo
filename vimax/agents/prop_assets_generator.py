"""AI漫剧道具资产生成 - 白底道具资产图

方法论（references/ai-scene-prop-asset-guide.md）：
  3. 剧本道具提取：提取故事中的核心道具（如背篓）
  4. 道具资产图：单独生成规范的道具资产图，为后续人物贴图和视频生成
     提供稳定基础（棚拍白底、材质/颜色/结构/磨损完全一致）

与 CharacterPortraitsGenerator 同构：白底横屏、跳过风格前缀与合规清洗
（英文 prompt 为主，避免中文合规替换破坏英文词汇）。
"""
from __future__ import annotations
from shared.style_prefix import asset_style_prefix

# 道具资产图：纯白背景、无人物、完整入镜、材质/颜色/结构/磨损一致
PROMPT_PROP = (
    "{style}. prop asset image, 16:9 landscape canvas, "
    "pure white seamless background, "
    "the scene contains ONLY the same {category} {identifier}, "
    "no people, no hands, no other props. "
    "{appearance}. "
    "Soft even studio lighting, very subtle shadow, "
    "real {category} texture, real handmade crafting detail, "
    "complete prop in frame, not cropped at top/bottom. "
    "High resolution, clean, standard, suitable as a film prop asset reference. "
    "Keep the selected visual style."
)


class PropAssetsGenerator:
    """AI漫剧道具资产生成器.

    生成流程：
    1. generate_prop() — 基于外观描述生成白底道具资产图（首次）
    """

    def __init__(self, image_generator):
        self.image_generator = image_generator

    async def generate_prop(self, prop: dict, save_path: str | None = None) -> object:
        """生成白底道具资产图.

        prop: prop_assets bank 条目（含 identifier/category/appearance）
        save_path: 资产图直接落盘路径（否则走 provider 默认帧路径，会污染 hook 首帧）
        Returns: ImageOutput
        """
        identifier = (prop.get("identifier") or "").strip()
        category = (prop.get("category") or "道具").strip()
        appearance = (prop.get("appearance") or "").strip()
        if not identifier:
            raise ValueError("道具缺少 identifier，无法生成资产图")
        if not appearance:
            raise ValueError(f"道具 {identifier} 缺少 appearance，无法生成资产图")
        prompt = PROMPT_PROP.format(
            category=category,
            style=asset_style_prefix("16:9"),
            identifier=identifier,
            appearance=appearance,
        )
        return await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="16:9",
            save_path=save_path,
        )


# 从 bank 条目中提取道具数据（供 asset_gen 调用，与 PropAssetsBank 结构解耦）
def prop_from_bank(entry: dict) -> dict:
    """把 PropAssetsBank 条目转成生成器所需 dict."""
    return {
        "identifier": entry.get("identifier", ""),
        "category": entry.get("category", ""),
        "appearance": entry.get("appearance", ""),
    }
