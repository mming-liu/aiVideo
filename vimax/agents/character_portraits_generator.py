from shared.style_prefix import asset_style_prefix
from vimax.interfaces import CharacterInScene, ImageOutput

"""
AI漫剧角色资产创建 - 基础图优先策略

方法论（来自 references/ai-character-asset-creation-guide.md）：
1. 先创建单张基础全景图，锁定核心特征（外观+质感+光影）
2. 基础图确认后，作为参考生成侧面/背面照
3. 强制统一约束："脸型、发型、服装、身材比例、光源全部一致"

万能人物Prompt公式：
[风格/画风] + [主体身份/年龄/性别] + [外貌细节] + [服装配饰] + [光影氛围] + [质量标签]
"""

# -- 基础全景图 Prompt（首次创建角色时，锁定核心特征） --
# 遵循万能公式：风格 + 身份 + 外貌细节 + 服装配饰 + 光影氛围 + 质量标签
# v6.23：人物资产硬约束（seedance-storyboard 吸收）——含鞋子防半身、表情中性、
# 不手持物件、真实成人比例，保证肖像图稳定可作 r2v 参考。
PROMPT_BASE = (
    "{style}. Character portrait of {identity}. {physical_features}. {clothing}. "
    "Full body from head to toe, including shoes, centered on a white background. "
    "Neutral expression, eyes open, hands relaxed at sides, holding nothing. "
    "Clear face, hairstyle, clothing and body proportions consistent with the character description."
)
PROMPT_SIDE = (
    "{style}. Full-body side-view portrait of {identifier}, facing left. "
    "Reference the base image for face shape, hairstyle, clothing and proportions. "
    "Keep its visual style and lighting. White background, entire figure including shoes."
)
PROMPT_BACK = (
    "{style}. Full-body back-view portrait of {identifier}. "
    "Reference the base image for hairstyle, clothing and proportions; no facial features visible. "
    "Keep its visual style and lighting. White background, entire figure including shoes."
)
PROMPT_FACE_CLOSEUP = (
    "{style}. Close-up face portrait of {identifier}, {physical_features}. "
    "Reference the base image for face shape, eyes, nose, mouth, skin tone and hairline. "
    "Head-and-shoulders composition, face filling the frame, neutral expression. "
    "Keep the base image visual style and lighting. White background, clear facial detail."
)

DEGREE = "°"  # °


class CharacterPortraitsGenerator:
    """AI漫剧角色肖像生成器 - 基础图优先策略

    生成流程（遵循 references/ai-character-asset-creation-guide.md）：
    1. generate_base_portrait() — 生成基础全景图，锁定核心特征（首次创建角色时）
    2. generate_side_portrait() — 基于基础图生成侧面全身照
    3. generate_back_portrait() — 基于基础图生成背面全身照
    4. generate_face_closeup() — 基于基础图生成独立高清面部特写（组合参考图法，
       与三视图同时输入，模型优先锁定面部特征，降低变脸）
    """

    def __init__(self, image_generator):
        self.image_generator = image_generator

    async def generate_base_portrait(
        self,
        character: CharacterInScene,
        style: str = "",
        save_path: str | None = None,
    ) -> ImageOutput:
        """生成基础全景图 — 锁定核心特征（外观+质感+光影）

        这是角色资产的第一步，生成的图将作为后续侧面/背面照的参考源。
        save_path: 肖像直接落盘路径（否则走 provider 默认帧路径，会污染 hook 首帧）
        """
        identity = character.identifier_in_scene
        static = (character.static_features or "").strip()
        dynamic = (character.dynamic_features or "").strip()

        prompt = PROMPT_BASE.format(
            identity=identity,
            physical_features=static,
            clothing=dynamic,
            style=style or asset_style_prefix("9:16"),
            deg=DEGREE,
        )

        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="9:16",
            save_path=save_path,
        )
        return image_output

    async def generate_side_portrait(
        self,
        character: CharacterInScene,
        base_image_path: str,
        save_path: str | None = None,
    ) -> ImageOutput:
        """基于基础图生成侧面全身照 — 强制一致性约束"""
        prompt = PROMPT_SIDE.format(
            identifier=character.identifier_in_scene,
            style=asset_style_prefix("9:16"),
            deg=DEGREE,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="9:16",
            reference_image_paths=[base_image_path],
            save_path=save_path,
        )
        return image_output

    async def generate_back_portrait(
        self,
        character: CharacterInScene,
        base_image_path: str,
        save_path: str | None = None,
    ) -> ImageOutput:
        """基于基础图生成背面全身照 — 强制一致性约束"""
        prompt = PROMPT_BACK.format(
            identifier=character.identifier_in_scene,
            style=asset_style_prefix("9:16"),
            deg=DEGREE,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="9:16",
            reference_image_paths=[base_image_path],
            save_path=save_path,
        )
        return image_output

    async def generate_face_closeup(
        self,
        character: CharacterInScene,
        base_image_path: str,
        save_path: str | None = None,
    ) -> ImageOutput:
        """基于基础图生成独立高清面部特写 — 组合参考图法核心.

        与正面/侧面/背面全身照同时输入 AI（r2v 参考图），模型优先锁定面部
        特征（脸型/五官/发际线），再匹配全身造型，显著降低变脸（AI漫剧角色
        面部一致性教程，抖音@赛博子）。

        v1.1：static_features（含脸型词）拼入 prompt，锁脸防变脸——
        脸型词跨章一致，面部特写图与全身肖像共享同一脸型。
        """
        prompt = PROMPT_FACE_CLOSEUP.format(
            identifier=character.identifier_in_scene,
            style=asset_style_prefix("9:16"),
            physical_features=(character.static_features or "").strip(),
            deg=DEGREE,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            ratio="9:16",
            reference_image_paths=[base_image_path],
            save_path=save_path,
        )
        return image_output