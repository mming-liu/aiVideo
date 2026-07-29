from vimax.interfaces import CharacterInScene, ImageOutput

"""
AI漫剧角色资产创建 - 基础图优先策略

方法论（来自 references/ai-character-asset-creation-guide.md）：
1. 先创建单张基础全景图，锁定核心特征（外观+质感+光影）
2. 基础图确认后，作为参考生成面部特写和三视图
3. 强制统一约束："脸型、发型、服装、身材比例、光源全部一致"

万能人物Prompt公式：
[风格/画风] + [主体身份/年龄/性别] + [外貌细节] + [服装配饰] + [光影氛围] + [质量标签]
"""


prompt_template_base = \
"""
Generate a photorealistic full-body character portrait based on the following description.
Use a wide 16:9 landscape canvas. The character should be centered in the image.

Identity: {identity}
Age: {age}
Gender: {gender}
Physical Features: {physical_features}
Clothing & Accessories: {clothing}
Lighting: Main light from 45° front-right, fill light on left, rim light from back-top, color temperature 5500K
Texture: Realistic skin texture, subtle pores, fabric folds, cuff wear, shoe dust
Quality: Cinematic film grain, subtle natural vignette, 8K HD, film-grade texture

The character should have a natural standing pose, relaxed arms at sides, realistic expression.
Not anime, not cartoon, not illustration.
"""


prompt_template_closeup = \
"""
Generate a detailed close-up portrait of character {identifier} based on the reference image.
Use a wide 16:9 landscape canvas. Focus on facial features, eyes, skin texture.
MUST match the reference: face shape, eye color, hairstyle, skin tone, lighting consistency.
Realistic skin texture with subtle pores, cinematic lighting from 45° front-right.
Not anime, not cartoon, not illustration.
"""


prompt_template_front = \
"""
Generate a photorealistic full-body, front-view portrait of character {identifier} based on the base reference image.
Use a wide 16:9 landscape canvas. The character should be centered with enough horizontal space.
MUST match base image: face shape, hairstyle, clothing, body proportions, light source all consistent.
Gazing straight ahead. Standing with arms relaxed at sides. Natural expression.
Realistic skin, fabric detail, cinematic lighting. Not anime, not cartoon, not illustration.
Features: {features}
Style: {style}
"""

prompt_template_side = \
"""
Generate a photorealistic full-body, side-view portrait of character {identifier} based on the base reference image.
Use a wide 16:9 landscape canvas. The character should be centered with enough horizontal space.
MUST match base image: face shape, hairstyle, clothing, body proportions, light source all consistent.
Facing left. Standing with arms relaxed at sides. Realistic skin, fabric detail, cinematic lighting.
Not anime, not cartoon, not illustration.
"""

prompt_template_back = \
"""
Generate a photorealistic full-body, back-view portrait of character {identifier} based on the base reference image.
Use a wide 16:9 landscape canvas. The character should be centered with enough horizontal space.
MUST match base image: hairstyle, clothing, body proportions, light source all consistent.
No facial features should be visible. Realistic fabric detail, cinematic lighting.
Not anime, not cartoon, not illustration.
"""


class CharacterPortraitsGenerator:
    """AI漫剧角色肖像生成器 - 基础图优先策略

    生成流程（遵循 references/ai-character-asset-creation-guide.md）：
    1. generate_base_portrait() - 生成基础全景图（首次创建角色时）
    2. generate_closeup_portrait() - 基于基础图生成面部特写
    3. generate_front/side/back_portrait() - 基于基础图生成三视图
    """

    def __init__(
        self,
        image_generator,
    ):
        self.image_generator = image_generator


    async def generate_base_portrait(
        self,
        character: CharacterInScene,
    ) -> ImageOutput:
        """生成基础全景图 - 锁定核心特征（外观+质感+光影）

        这是角色资产的第一步，生成的图将作为后续所有变体的参考。
        """
        # 从角色描述中提取信息
        identity = character.identifier_in_scene
        # 解析 static_features 和 dynamic_features
        static = character.static_features or ""
        dynamic = character.dynamic_features or ""

        # 构建完整 prompt（使用万能公式）
        prompt = prompt_template_base.format(
            identity=identity,
            age=static.get("age", "unknown") if isinstance(static, dict) else "unknown",
            gender=static.get("gender", "unknown") if isinstance(static, dict) else "unknown",
            physical_features=static if isinstance(static, str) else str(static),
            clothing=dynamic if isinstance(dynamic, str) else str(dynamic),
        )

        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
        )
        return image_output


    async def generate_closeup_portrait(
        self,
        character: CharacterInScene,
        base_image_path: str,
    ) -> ImageOutput:
        """基于基础图生成面部特写 - 锁定五官细节"""
        prompt = prompt_template_closeup.format(
            identifier=character.identifier_in_scene,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            reference_image_paths=[base_image_path],
        )
        return image_output


    async def generate_front_portrait(
        self,
        character: CharacterInScene,
        base_image_path: str,
        style: str = "",
    ) -> ImageOutput:
        """基于基础图生成正面全身照 - 强制一致性约束"""
        features = f"(static) {character.static_features}; (dynamic) {character.dynamic_features}"
        prompt = prompt_template_front.format(
            identifier=character.identifier_in_scene,
            features=features,
            style=style,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            reference_image_paths=[base_image_path],
        )
        return image_output


    async def generate_side_portrait(
        self,
        character: CharacterInScene,
        base_image_path: str,
    ) -> ImageOutput:
        """基于基础图生成侧面全身照"""
        prompt = prompt_template_side.format(
            identifier=character.identifier_in_scene,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            reference_image_paths=[base_image_path],
        )
        return image_output


    async def generate_back_portrait(
        self,
        character: CharacterInScene,
        base_image_path: str,
    ) -> ImageOutput:
        """基于基础图生成背面全身照"""
        prompt = prompt_template_back.format(
            identifier=character.identifier_in_scene,
        )
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            reference_image_paths=[base_image_path],
        )
        return image_output