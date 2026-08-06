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
PROMPT_BASE = (
    "Photorealistic {style} portrait of {identity}. "
    "{physical_features}. {clothing}. "
    "Lighting: main light 45{deg} front-right, fill light from left, "
    "rim light from back-top, color temperature 5500K, unified shadow direction. "
    "Texture: realistic skin texture, subtle pores, fabric folds, "
    "natural fiber wrinkles, slight cuff wear, natural shoe dust. "
    "Quality: subtle film grain, micro-contrast, natural vignette, "
    "8K HD resolution, film-grade texture, photorealistic. "
    "Full body, centered composition, white background, 16:9 landscape canvas. "
    "Natural standing pose, arms relaxed at sides, realistic expression. "
    "Not anime, not cartoon, not illustration, not 3D render."
)

# -- 侧面全身照 Prompt（基于基础图，强制一致性约束） --
PROMPT_SIDE = (
    "Photorealistic full-body side-view portrait of {identifier}, "
    "referencing the provided base image. "
    "MUST match base image: face shape, hairstyle, clothing, body proportions, "
    "ALL light source direction, color temperature 5500K completely consistent. "
    "Facing left. Standing, arms relaxed, natural expression. "
    "Lighting: main light 45{deg} front-right, fill light left, rim light back-top. "
    "Texture: realistic skin texture, fabric folds, natural material. "
    "Quality: photorealistic, film-grade texture. White background, 16:9 landscape. "
    "Not anime, not cartoon, not illustration, not 3D render."
)

# -- 背面全身照 Prompt（基于基础图，强制一致性约束） --
PROMPT_BACK = (
    "Photorealistic full-body back-view portrait of {identifier}, "
    "referencing the provided base image. "
    "MUST match base image: hairstyle, clothing, body proportions, "
    "ALL light source direction, color temperature 5500K completely consistent. "
    "No facial features visible. Standing, natural posture. "
    "Lighting: main light 45{deg} front-right, rim light back-top. "
    "Texture: realistic fabric detail, hair texture, material folds. "
    "Quality: photorealistic, film-grade texture. White background, 16:9 landscape. "
    "Not anime, not cartoon, not illustration, not 3D render."
)

# -- 面部特写 Prompt（基于基础图，独立高清锁脸，组合参考图法核心） --
# 来源：抖音@赛博子 组合参考图法——三视图 + 单独高清面部特写同时输入，
# 模型优先锁定面部特征，再匹配全身造型，大幅降低变脸。面部特写是"变脸"
# 重灾区，独立一张高清正面脸部图作为 r2v 面部锁定锚点。
PROMPT_FACE_CLOSEUP = (
    "Photorealistic close-up face portrait of {identifier}, "
    "referencing the provided base image. "
    "MUST match base image: face shape, eyes, nose, mouth, eyebrow arch, "
    "skin tone, hairstyle and hairline, ALL light source direction, "
    "color temperature 5500K completely consistent. "
    "Head-and-shoulders composition, face filling the frame, "
    "looking straight at camera, neutral expression. "
    "Texture: realistic skin texture, subtle pores, natural eye detail, "
    "fine hair strands. "
    "Quality: photorealistic, film-grade texture, sharp facial detail, "
    "8K HD resolution. White background, 16:9 landscape. "
    "Not anime, not cartoon, not illustration, not 3D render."
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
            style=style,
            deg=DEGREE,
        )

        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
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
            deg=DEGREE,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
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
            deg=DEGREE,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
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
        """
        prompt = PROMPT_FACE_CLOSEUP.format(
            identifier=character.identifier_in_scene,
            deg=DEGREE,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            reference_image_paths=[base_image_path],
            save_path=save_path,
        )
        return image_output