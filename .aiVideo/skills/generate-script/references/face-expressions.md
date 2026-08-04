# 动漫角色面部表情提示词库

> 可动态扩展。来源：抖音图文作品整理（2026-08-04）。
> 用途：AI 绘画 / 漫剧创作中为角色 prompt 补充**面部神态细节**，替代抽象情绪词。
> 与 step01 的情绪可视化规则（SYSTEM_PROMPT 17.2「抽象情绪词→具象动作」、
> validation.py `_EMOTION_ACTION_MAP`）同源互补：抽象情绪词禁止直接进 prompt，
> 必须换成下列「表情模板」级别的具象面部描述。
>
> 用法：从对应情绪类别挑 1 条模板，把描述嵌进角色的神态位置，如
> 「剑眉星目身穿白袍的杨旭，嘴角单侧上扬，眉峰微挑，眼神轻蔑，下颌微抬」。
> 每条均附英文关键词，供英文提示词/负面提示词对照。

## 一、快乐 / 笑意类

| 表情 | 中文描述 | English |
|------|---------|---------|
| 元气微笑 | 嘴角上扬至颧骨，苹果肌饱满，露6-8颗牙齿，眼尾弯成月牙，瞳孔明亮，日系动漫风，柔和光 | energetic smile, high cheekbones, 6-8 teeth, crescent eyes, bright pupils, soft light |
| 大笑 | 嘴角咧至耳根，露整排牙齿，眼角笑出细纹，鼻翼微张，头微仰，元气满满，明亮色调 | laughing out loud, grin to ears, crows-feet at eyes, flared nostrils, head tilted up, bright tones |
| 偷笑 | 嘴角抿起上扬，脸颊鼓起，眼神狡黠，目光躲闪，手掌轻捂嘴，低饱和度色调 | sly grin, puffed cheeks, mischievous eyes, averted glance, hand over mouth, muted tones |
| 害羞笑 | 嘴角轻扬，脸颊泛红，耳朵发红，头微低，眼神低垂，睫毛轻颤，柔和柔光 | shy smile, blushing cheeks, red ears, lowered head, downcast eyes, trembling lashes, soft glow |
| 傲娇冷笑 | 嘴角单侧上扬，眉峰微挑，眼神轻蔑，下颌微抬，侧脸角度，冷色调光影 | tsundere sneer, one-sided smirk, raised brow, contemptuous look, lifted chin, side profile, cool tones |
| 宠溺笑 | 嘴角轻弯，眼尾下垂带暖意，瞳孔柔和，目光平视，周身有柔和光晕，暖色调 | indulgent smile, warm drooping eye-corners, soft pupils, gentle gaze, warm halo, warm tones |
| 苦笑 | 嘴角扯出僵硬弧度，眉头微蹙，眼神无奈，眼底带疲惫，面部肌肉紧绷，低饱和色调 | wry smile, stiff lips, furrowed brows, helpless eyes, tired under-eyes, tight face, desaturated |
| 假笑 | 嘴角上扬无笑意，眼神空洞，苹果肌僵硬，眉峰平直，面部表情不自然，冷光 | fake smile, no warmth in eyes, hollow look, stiff cheeks, flat brows, unnatural face, cold light |
| 温柔笑 | 嘴角弯成弧形，眼尾带笑意，瞳孔温柔，目光注视前方，暖黄色光影 | gentle smile, curved lips, smiling eyes, warm pupils, forward gaze, warm yellow light |
| 得意笑 | 嘴角上扬，眼神明亮，眉梢微挑，头微仰，自信姿态，明亮暖光 | smug smile, bright eyes, raised brow-tips, head up, confident pose, bright warm light |

## 二、惊讶 / 震惊类

| 表情 | 中文描述 | English |
|------|---------|---------|
| 震惊 | 眉峰上扬，眼睛微睁，嘴角微张，眼神意外，柔和光影 | shocked, raised brows, slightly wide eyes, parted lips, surprised look, soft light |
| 茫然发呆 | 眼神涣散，嘴巴微张，头微低，瞳孔失焦，面部茫然 | blank stare, unfocused eyes, slack mouth, lowered head, dazed face |
| 惊讶 | 嘴巴大张，眼睛睁大，瞳孔收缩，眉毛上扬，面部肌肉微僵，强光 | startled, mouth wide open, eyes wide, constricted pupils, raised brows, stiff face, strong light |
| 难以置信 | 眉头微皱，眼睛半睁半疑，嘴巴微撇，头微偏，柔和侧光 | incredulous, furrowed brows, half-squinting eyes, pursed lips, tilted head, soft side light |
| 石化 | 表情完全僵住，眼睛瞪大，嘴巴半张，面部如雕塑般静止，冷硬光影 | petrified, frozen expression, wide eyes, half-open mouth, statue-like face, hard light |
| 惊喜 | 眼睛睁大，眼角带笑，眉毛上扬，脸颊泛红，暖光 | pleasantly surprised, wide eyes, smiling corners, raised brows, blushing cheeks, warm light |
| 惊恐 | 眼睛瞪大，瞳孔收缩，嘴巴张开，眉毛上扬，面部肌肉扭曲，冷光 | horrified, wide eyes, constricted pupils, open mouth, raised brows, contorted face, cold light |
| 错愕 | 嘴巴微张，眼神停滞，眉头微蹙，头微偏，柔和光 | taken aback, slightly open mouth, stalled gaze, furrowed brows, tilted head, soft light |

## 三、悲伤 / 低落类

| 表情 | 中文描述 | English |
|------|---------|---------|
| 悲伤 | 嘴角下撇，眼神黯淡，眉毛微蹙，头微低，冷色调 | sad, downturned lips, dim eyes, furrowed brows, lowered head, cool tones |
| 委屈 | 嘴角下撇，眼眶泛红，眼神湿润，头微低，暖光 | aggrieved, downturned lips, reddened eyes, moist eyes, lowered head, warm light |
| 哭泣 | 眼泪滑落，眼眶泛红，嘴角微撇，眉头紧锁，柔和光 | crying, tears falling, reddened eyes, slight frown, knitted brows, soft light |
| 失落 | 眼神低垂，嘴角平直，面部无表情，冷光 | dejected, downcast eyes, flat mouth, expressionless face, cold light |
| 绝望 | 眼神空洞，嘴角下撇，面部肌肉僵硬，冷色调 | despairing, hollow eyes, downturned lips, stiff face, cold tones |
| 委屈哭 | 嘴巴微嘟，眼泪滑落，眼神委屈，头微偏，暖光 | crying with grievance, pouting mouth, falling tears, hurt eyes, tilted head, warm light |
| 隐忍 | 眼眶泛红，嘴角紧闭，眼神坚定，头微仰，冷光 | holding back tears, reddened eyes, tightly closed lips, determined eyes, chin up, cold light |
| 心碎 | 眼神黯淡，嘴角下撇，面部肌肉松弛，冷色调 | heartbroken, dim eyes, downturned lips, slack face, cool tones |
| 难过 | 嘴角下撇，眼神湿润，眉毛微蹙，头微低，柔和光 | upset, downturned lips, moist eyes, furrowed brows, lowered head, soft light |
| 沮丧 | 眼神低垂，嘴角下撇，面部无精打采，冷光 | discouraged, downcast eyes, downturned lips, listless face, cold light |

## 四、愤怒 / 生气类

| 表情 | 中文描述 | English |
|------|---------|---------|
| 愤怒 | 眉头紧锁，眼睛瞪大，瞳孔收缩，嘴巴大张，面部肌肉紧绷，冷光 | angry, knitted brows, wide eyes, constricted pupils, mouth open, tense face, cold light |
| 生气 | 眉头微蹙，眼神锐利，嘴角下撇，头微抬，冷色调 | annoyed, slight frown, sharp eyes, downturned lips, chin up, cool tones |
| 暴怒 | 眼睛瞪大，瞳孔收缩，嘴巴大张，面部肌肉扭曲，强光 | enraged, wide eyes, constricted pupils, mouth wide open, distorted face, strong light |
| 不满 | 眉头微蹙，眼神不满，嘴角下撇，头微偏，冷光 | displeased, furrowed brows, dissatisfied eyes, downturned lips, tilted head, cold light |
| 厌恶 | 眉头微皱，眼神厌恶，嘴角下撇，头微偏，冷色调 | disgusted, wrinkled brows, disgusted eyes, downturned lips, tilted head, cool tones |
| 愤怒冷笑 | 嘴角单侧上扬，眼神愤怒，眉峰微挑，侧脸角度，冷光 | angry sneer, one-sided smirk, furious eyes, raised brow, side profile, cold light |
| 气呼呼 | 脸颊鼓起，眉头微蹙，眼神不满，嘴巴微嘟，暖光 | huffy, puffed cheeks, furrowed brows, dissatisfied eyes, pouting mouth, warm light |
| 愤怒瞪视 | 眼睛瞪大，瞳孔收缩，眼神锐利，眉头紧锁，冷光 | glaring angrily, wide eyes, constricted pupils, sharp gaze, knitted brows, cold light |
| 愤怒咬牙 | 嘴巴紧闭，牙齿咬紧，眉头紧锁，眼神愤怒，冷光 | grinding teeth in anger, closed mouth, clenched teeth, knitted brows, furious eyes, cold light |
| 愤怒嘶吼 | 嘴巴大张，眼睛瞪大，瞳孔收缩，面部肌肉扭曲，强光 | angry roar, mouth wide open, bulging eyes, constricted pupils, distorted face, strong light |

## 五、其他情绪类

| 表情 | 中文描述 | English |
|------|---------|---------|
| 高冷 | 嘴角平直，眉头舒展，眼神淡漠，下颌线清晰，冷色调 | aloof, flat mouth, relaxed brows, indifferent eyes, sharp jawline, cool tones |
| 慵懒 | 眼皮半耷拉，眼神慵懒，嘴角平直，头微歪，周身散发慵懒气息，暖光 | languid, half-lidded eyes, lazy gaze, flat mouth, tilted head, lazy aura, warm light |
| 冰冷 | 眼神冰冷，嘴角平直，眉头微蹙，下颌微抬，侧光 | icy, cold eyes, flat mouth, slight frown, lifted chin, side light |
| 疏离 | 脸侧角度，冷硬光影，眼神疏离，嘴角平直 | distant, side profile, hard light, detached gaze, flat mouth |
| 困倦 | 嘴巴大张，眼皮耷拉，眼神惺忪，眼角泛红，头微仰，柔和暖光 | sleepy, mouth open, drooping lids, drowsy eyes, reddened corners, head tilted up, soft warm light |
| 疲惫 | 眼皮下垂，眼神疲惫，眼袋明显，嘴角下撇，头微低，低饱和色调 | weary, drooping lids, tired eyes, visible eye bags, downturned lips, lowered head, desaturated |
| 撒娇 | 嘴巴嘟起，脸颊鼓起，眼神委屈带期待，头微歪，暖色调光影 | acting cute, pouting mouth, puffed cheeks, pleading-wistful eyes, tilted head, warm tones |
| 傲娇扭头 | 头微扭，嘴角下撇，眼神傲娇，眉峰微挑，侧脸角度，冷光 | tsundere turn-away, tilted head, pouting lips, prideful eyes, raised brow, side profile, cold light |
| 好奇 | 头微歪，眼睛睁大，眼神好奇，嘴角微扬，暖光 | curious, tilted head, wide eyes, curious gaze, slight smile, warm light |
| 紧张 | 眼神轻微闪躲，眉头浅皱，嘴唇抿紧，鼻翼轻张，脸颊略僵，额头可有细微汗意，真实近景 | nervous, darting eyes, faint frown, pressed lips, slightly flared nostrils, stiff cheeks, faint sweat, realistic close-up |
| 冷漠 | 眼神平静无波，眉形平直，嘴角没有弧度，脸部肌肉放松但疏离，直视镜头，冷白色极简光线 | indifferent, flat calm eyes, straight brows, no mouth curve, relaxed-but-detached face, direct to camera, minimal cold-white light |
| 平静释然 | 眼神柔和稳定，眉头完全舒展，嘴角浮现极浅笑意，呼吸放松，脸部自然，温暖日落般柔光 | serene relief, soft steady eyes, fully relaxed brows, faint smile, easy breathing, natural face, warm sunset-like soft light |

---

## 使用建议

1. **情绪词→表情模板**：prompt 里要写「悲伤/愤怒/开心/惊讶…」等抽象情绪词时，先查上表，换成对应表情的具象面部描述（眉毛/眼睛/嘴巴/脸颊/头位/光影六要素）。
2. **光影要素别丢**：每条模板末尾的色调/光影词（冷光/暖光/柔和光/强光）可并入镜头的光影分层描述，与 `storyboard_refs.LIGHTING_MOODS` 配合。
3. **与情绪可视化规则对齐**：validation.py 检测到抽象情绪词时会自动替换为 `_EMOTION_ACTION_MAP` 的动作，表情模板是比动作更高一层的**面部**细化，两者可叠加：动作（低头垂眸，双手紧握）+ 表情（嘴角下撇，眼眶泛红）。
4. **负面提示词**：英文列可反向用于 negative prompt（如避免「fake smile」等），或屏蔽非目标情绪。
