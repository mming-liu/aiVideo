"""故事板 Prompt 模板 — 7种可控视频生成工具

每个模板都针对特定类型的storyboard进行优化，确保AI生成结果可预期、可控。
"""

from .models import StoryboardType, ShotSize, CameraMovement, LightingStyle


# ============== 1. 逐镜执行故事板模板 ==============
def per_shot_storyboard_prompt(shot: dict) -> str:
    """逐镜执行故事板 - 单镜头完整参数表

    Args:
        shot: 包含 duration, shot_size, camera_movement, lighting, narration, sound_effect, prompt_supplement 的字典
    """
    duration = shot.get("duration_seconds", 3.0)
    shot_size = shot.get("shot_size", "medium")
    camera = shot.get("camera_movement", "static")
    lighting = shot.get("lighting", "natural")
    narration = shot.get("narration", "")
    sound_effect = shot.get("sound_effect", "")
    supplement = shot.get("prompt_supplement", "")

    return f"""Generate a cinematic video shot with the following fixed parameters:

## Technical Parameters
- Duration: {duration} seconds
- Shot Size: {shot_size}
- Camera Movement: {camera}
- Lighting Style: {lighting}

## Content Parameters
{narration and f"- Narration/Dialogue: {narration}" or ""}
{sound_effect and f"- Sound Effect: {sound_effect}" or ""}
{"/n- Additional Prompt: " + supplement if supplement else ""}

## Constraints
- Generate exactly {duration} seconds of video
- Maintain consistent shot size throughout
- {camera_movement_constraint(camera)}
- {lighting_constraint(lighting)}

{prompt_suffix()}"""


def camera_movement_constraint(camera: str) -> str:
    """运镜约束"""
    constraints = {
        "static": "No camera movement - keep the frame completely still",
        "pan_left": "Smooth horizontal pan to the left at constant speed",
        "pan_right": "Smooth horizontal pan to the right at constant speed",
        "tilt_up": "Smooth vertical tilt upward",
        "tilt_down": "Smooth vertical tilt downward",
        "dolly_in": "Smooth dolly push forward into the subject",
        "dolly_out": "Smooth dolly pull away from the subject",
        "track_left": "Smooth lateral tracking movement to the left",
        "track_right": "Smooth lateral tracking movement to the right",
        "crane_up": "Smooth crane/gimbal upward movement",
        "crane_down": "Smooth crane/gimbal downward movement",
        "rotate": "Smooth orbital rotation around the subject",
        "handheld": "Subtle handheld shake for documentary feel",
    }
    return constraints.get(camera, constraints["static"])


def lighting_constraint(lighting: str) -> str:
    """光影约束"""
    constraints = {
        "natural": "Use natural lighting conditions appropriate to the time and setting",
        "chiiaroscuro": "Strong chiaroscuro with dramatic shadows, Rembrandt lighting",
        "backlight": "Backlit subject with rim light and silhouette effect",
        "soft_fill": "Soft, diffused lighting with minimal shadows",
        "hard_direct": "Hard, direct sunlight with sharp shadows",
        "candlelight": "Warm candlelight illumination, warm color temperature",
        "moonlight": "Cool blue moonlight, subtle and atmospheric",
        "sunset": "Golden hour warm lighting with long shadows",
    }
    return constraints.get(lighting, constraints["natural"])


# ============== 2. 角色设定故事板模板 ==============
def character_set_storyboard_prompt(characters: list, style: str = "") -> str:
    """角色设定故事板 - 生成角色四视图 + 动作参考

    为每个角色生成：
    1. 正面全身像（白底）
    2. 侧面全身像（白底）
    3. 背面全身像（白底）
    4. 动作参考图（常见动作姿态）
    """
    # 先构建角色规格部分
    char_specs = []
    for i, c in enumerate(characters):
        spec = f"""### Character {i+1}: {c['name']}
- Description: {c['description']}
- Static Features (clothing, hairstyle): {c.get('features_static', 'N/A')}
- Dynamic Features (expressions, habits): {c.get('features_dynamic', 'N/A')}
- Personality: {c.get('personality', 'N/A')}"""
        char_specs.append(spec)
    char_specs_str = chr(10).join(char_specs)

    style_part = f"\nStyle Reference: {style}" if style else ""
    return f"""Generate character reference images for storyboard creation.

## Task
Create a comprehensive character profile with multiple views for consistent character depiction across video shots.

## Character Specifications
{char_specs_str}

## Output Requirements

### View 1: Front View
- Full-body portrait, pure white background
- 16:9 landscape canvas
- Character centered, facing camera directly
- Arms relaxed at sides
- Natural expression
- Gazing straight ahead

### View 2: Side View
- Full-body portrait, pure white background
- 16:9 landscape canvas
- Character facing LEFT
- Arms relaxed at sides

### View 3: Back View
- Full-body portrait, pure white background
- 16:9 landscape canvas
- No facial features visible
- Show clothing details from behind

### View 4: Action Reference
- 2-3 common action poses for this character
- Natural, believable poses based on personality
- Include both static poses and movement snapshots

{style_part}
{prompt_suffix()}"""


# ============== 3. 场景固定故事板模板 ==============
def scene_fixed_storyboard_prompt(scene: dict, characters: list = None) -> str:
    """场景固定故事板 - 生成单一场景固定底图

    生成的场景图将作为后续所有镜头的背景基础，
    确保不会出现"上一幕有桌子下一幕没桌子"的矛盾。
    """
    scene_desc = scene.get("description", "")
    time_period = scene.get("time_period", "")
    mood = scene.get("mood", "")
    props = scene.get("props", [])
    props_str = ", ".join(props) if props else "None specified"

    char_context = ""
    if characters:
        char_parts = [f'- {c["name"]}: {c.get("description", "")}' for c in characters]
        char_context = "\n## Characters Present\n" + chr(10).join(char_parts)

    return f"""Generate a fixed scene base image for storyboard creation.

## Task
Create a detailed scene background that will serve as the consistent reference for all shots in this scene. Every subsequent shot MUST maintain the exact same layout, props, and spatial relationships.

## Scene Description
{scene_desc}

## Time & Atmosphere
- Time Period: {time_period or "Not specified"}
- Mood/Atmosphere: {mood or "Not specified"}

## Essential Props (MUST appear in scene)
{props_str}
{char_context}

## Constraints
- This is a FIXED BASE - all camera angles will be derived from this layout
- Every detail must be clearly visible and matchable
- Props must remain in EXACT positions across all shots
- Create depth with foreground/midground/background layers
- Use consistent lighting throughout

## Canvas
- 16:9 landscape orientation
- High detail suitable for reference

{prompt_suffix()}"""


# ============== 4. 场景规划故事板模板 ==============
def scene_plan_storyboard_prompt(scene: dict, characters: list = None) -> str:
    """场景规划故事板 - 生成场景平面图（空间结构、动线、道具位置）

    类似于"建筑图纸"，给AI一张场景的2D俯视图/侧视图，
    让它严格按空间逻辑生成镜头。
    """
    scene_desc = scene.get("description", "")
    layout = scene.get("layout", "")
    props = scene.get("props", [])
    props_str = ", ".join(props) if props else "None specified"

    char_context = ""
    if characters:
        char_parts = [f'- {c["name"]}: {c.get("description", "")}' for c in characters]
        char_context = "\n## Characters & Their Typical Positions\n" + chr(10).join(char_parts)

    return f"""Generate a scene planning diagram for storyboard creation.

## Task
Create a 2D floor plan / architectural diagram showing the spatial structure, movement paths, and prop placements. This serves as the "blueprint" for all camera work in this scene.

## Scene Description
{scene_desc}

## Layout Specification
{layout or "Standard room layout with focal points"}

## Key Props & Locations
{props_str}
{char_context}

## Output Requirements
- 2D floor plan view (top-down or isometric)
- Clearly mark: doors, windows, furniture, key props
- Show movement paths with directional arrows
- Label important spatial zones
- Include scale reference if possible

## Style
- Technical architectural drawing style
- Clear lines, labeled zones
- Can include subtle depth cues

{prompt_suffix()}"""


# ============== 5. 动作拆解故事板模板 ==============
def motion_keyframes_storyboard_prompt(motion: dict) -> str:
    """动作拆解故事板 - 把复杂动作拆解成关键帧

    将一个复杂动作（如打斗、舞蹈）拆解成多个单帧关键动作，
    AI按关键帧生成连续动作，避免肢体畸形。
    """
    motion_name = motion.get("name", "")
    motion_desc = motion.get("description", "")
    duration = motion.get("duration_seconds", 3.0)
    difficulty = motion.get("difficulty", "medium")
    keyframes = motion.get("keyframes", [])

    # 自动计算关键帧数量（每秒1帧）
    keyframe_count = len(keyframes) or max(3, int(duration))

    if keyframes:
        keyframe_parts = [f"### Keyframe {i+1}: {kf}" for i, kf in enumerate(keyframes)]
        keyframes_str = chr(10).join(keyframe_parts)
    else:
        keyframes_str = f"Generate {keyframe_count} evenly-spaced keyframes capturing the full motion arc"

    # 难度描述
    difficulty_desc = {
        "easy": "simple, natural movements",
        "medium": "some complex poses requiring attention",
        "hard": "highly intricate poses with multiple body parts",
    }.get(difficulty, "medium")

    return f"""Generate motion keyframe reference images for storyboard creation.

## Task
Break down a complex motion sequence into individual keyframes. Each keyframe captures a critical moment in the movement, ensuring smooth and anatomically correct motion.

## Motion Description
- Name: {motion_name}
- Description: {motion_desc}
- Duration: {duration} seconds
- Difficulty: {difficulty}

## Keyframes to Generate
{keyframes_str}

## Constraints
- Each keyframe must be anatomically correct
- Maintain consistent scale and perspective across frames
- Show smooth transitions between adjacent frames
- Pay special attention to: hand positions, footwork, body balance
- For {difficulty} difficulty: {difficulty_desc}

## Style
- Sequential image strip format (side by side)
- Clean background to focus on motion
- Clear enough to use as AI generation reference

{prompt_suffix()}"""


# ============== 6. 动作设计故事板模板 ==============
def motion_design_storyboard_prompt(motion: dict, scene: dict = None, characters: list = None) -> str:
    """动作设计故事板 - 分镜+动线标注的完整动作链

    设计完整的动作逻辑链（从A走到B、拿起道具），
    让AI按动线生成连贯镜头。
    """
    motion_name = motion.get("name", "")
    motion_desc = motion.get("description", "")
    duration = motion.get("duration_seconds", 5.0)
    keyframes = motion.get("keyframes", [])

    scene_info = ""
    if scene:
        scene_info = f"\n## Scene Context\n- Name: {scene.get('name', 'N/A')}\n- Description: {scene.get('description', 'N/A')}\n- Layout: {scene.get('layout', 'N/A')}"

    char_info = ""
    if characters:
        char_parts = [f'- {c["name"]}: {c.get("description", "")}' for c in characters]
        char_info = "\n## Characters Involved\n" + chr(10).join(char_parts)

    # 构建镜头分解
    if keyframes:
        shot_parts = [f"### Shot {i+1}: {kf}" for i, kf in enumerate(keyframes)]
        shot_breakdown = chr(10).join(shot_parts)
    else:
        shot_breakdown = "Design 3-5 logical shots covering the full motion sequence"

    return f"""Generate a motion design storyboard for video creation.

## Task
Design a complete action sequence with shot breakdown and movement path visualization. This tells the AI exactly HOW the character should move through space.

## Motion Sequence
- Name: {motion_name}
- Description: {motion_desc}
- Total Duration: {duration} seconds

## Shot Breakdown
{shot_breakdown}

{scene_info}
{char_info}

## Output Requirements
- Visual storyboard with multiple panels
- Each panel shows: camera angle, character position, movement direction
- Include movement paths (arrows showing trajectory)
- Show timing for each shot segment
- Indicate camera movement type for each shot

## Style
- Professional storyboard format
- Clear, instructional layout
- Suitable as production reference

{prompt_suffix()}"""


# ============== 7. 场景概览故事板模板 ==============
def scene_overview_storyboard_prompt(scene: dict, characters: list = None) -> str:
    """场景概览故事板 - 生成整个大场景的鸟瞰图

    标注所有关键位置、动线、道具分布，
    避免AI生成"局部合理但整体混乱"的场景。
    """
    scene_desc = scene.get("description", "")
    time_period = scene.get("time_period", "")
    mood = scene.get("mood", "")
    layout = scene.get("layout", "")
    props = scene.get("props", [])
    props_str = ", ".join(props) if props else "None specified"

    char_context = ""
    if characters:
        char_parts = [f'- {c["name"]}: {c.get("description", "")}' for c in characters]
        char_context = "\n## Characters & Typical Locations\n" + chr(10).join(char_parts)

    return f"""Generate a comprehensive scene overview for storyboard creation.

## Task
Create a bird's-eye (aerial) view of the entire scene, showing the macro structure, key locations, movement paths, and prop distribution. This provides the AI with complete spatial understanding.

## Scene Description
{scene_desc}

## Time & Atmosphere
- Time Period: {time_period or "Not specified"}
- Mood/Atmosphere: {mood or "Not specified"}

## Layout Structure
{layout or "Describe the spatial layout"}

## Key Props & Locations
{props_str}
{char_context}

## Output Requirements
- Bird's eye / aerial view perspective
- Show complete scene area with clear boundaries
- Mark and label: key locations, focal points, entry/exit points
- Indicate typical movement paths with arrows
- Show depth from foreground to background
- Include scale reference

## Canvas
- 16:9 landscape orientation
- High detail suitable for reference

## Constraints
- This overview must be internally consistent
- All marked locations must be visible and distinguishable
- Movement paths should make logical sense

{prompt_suffix()}"""


# ============== 通用后缀 ==============
def prompt_suffix() -> str:
    """所有模板的通用后缀"""
    return """
## Quality Requirements
- Photorealistic style
- High detail, no artifacts
- Consistent lighting and color grading
- Professional cinematography composition

## Important Notes
- This image will be used as a REFERENCE for AI video generation
- Maintain visual consistency with other storyboard elements
- Prioritize clarity and detail over artistic abstraction
- Ensure all specified elements are clearly visible"""


# ============== 主模板分发 ==============
def get_storyboard_prompt(request) -> str:
    """根据请求类型返回对应的 prompt"""
    from .models import (
        StoryboardType,
        CharacterSpec,
        SceneSpec,
        MotionSpec,
        PerShotSpec,
    )

    storyboard_type = request.storyboard_type

    if storyboard_type == StoryboardType.PER_SHOT:
        # 逐镜执行：处理多个 shot
        prompts = []
        for shot in request.shots:
            shot_dict = {
                "duration_seconds": shot.duration_seconds,
                "shot_size": shot.shot_size.value if isinstance(shot.shot_size, ShotSize) else shot.shot_size,
                "camera_movement": shot.camera_movement.value if isinstance(shot.camera_movement, CameraMovement) else shot.camera_movement,
                "lighting": shot.lighting.value if isinstance(shot.lighting, LightingStyle) else shot.lighting,
                "narration": shot.narration,
                "sound_effect": shot.sound_effect,
                "prompt_supplement": shot.prompt_supplement,
            }
            prompts.append(per_shot_storyboard_prompt(shot_dict))
        return "\n\n---\n\n".join(prompts)

    elif storyboard_type == StoryboardType.CHARACTER_SET:
        characters = [{"name": c.name, "description": c.description,
                       "features_static": c.features_static, "features_dynamic": c.features_dynamic,
                       "personality": c.personality} for c in request.characters]
        return character_set_storyboard_prompt(characters, request.style_reference)

    elif storyboard_type == StoryboardType.SCENE_FIXED:
        scene = {"name": request.scene.name,
                 "description": request.scene.description,
                 "time_period": request.scene.time_period,
                 "mood": request.scene.mood,
                 "props": request.scene.props} if request.scene else {}
        chars = [{"name": c.name, "description": c.description} for c in request.characters] if request.characters else None
        return scene_fixed_storyboard_prompt(scene, chars)

    elif storyboard_type == StoryboardType.SCENE_PLAN:
        scene = {"name": request.scene.name,
                 "description": request.scene.description,
                 "layout": request.scene.layout,
                 "props": request.scene.props} if request.scene else {}
        chars = [{"name": c.name, "description": c.description} for c in request.characters] if request.characters else None
        return scene_plan_storyboard_prompt(scene, chars)

    elif storyboard_type == StoryboardType.MOTION_KEYFRAMES:
        motion = {"name": request.motion.name,
                  "description": request.motion.description,
                  "duration_seconds": request.motion.duration_seconds,
                  "difficulty": request.motion.difficulty,
                  "keyframes": request.motion.keyframes} if request.motion else {}
        return motion_keyframes_storyboard_prompt(motion)

    elif storyboard_type == StoryboardType.MOTION_DESIGN:
        motion = {"name": request.motion.name,
                  "description": request.motion.description,
                  "duration_seconds": request.motion.duration_seconds,
                  "keyframes": request.motion.keyframes} if request.motion else {}
        scene = {"name": request.scene.name, "description": request.scene.description,
                 "layout": request.scene.layout} if request.scene else None
        chars = [{"name": c.name, "description": c.description} for c in request.characters] if request.characters else None
        return motion_design_storyboard_prompt(motion, scene, chars)

    elif storyboard_type == StoryboardType.SCENE_OVERVIEW:
        scene = {"name": request.scene.name,
                 "description": request.scene.description,
                 "time_period": request.scene.time_period,
                 "mood": request.scene.mood,
                 "layout": request.scene.layout,
                 "props": request.scene.props} if request.scene else {}
        chars = [{"name": c.name, "description": c.description} for c in request.characters] if request.characters else None
        return scene_overview_storyboard_prompt(scene, chars)

    else:
        raise ValueError(f"Unknown storyboard type: {storyboard_type}")