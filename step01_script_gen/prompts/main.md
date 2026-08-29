<!-- pro_main.md — 分镜生成主模板 (PROMPT_TEMPLATE) v6.43 精简版 -->
<!-- 精简前 159 行 → 60 行 -->
<!-- 占位符：{shot_count_min}, {shot_count_max}, {character_anchors}, {scene_object_anchors}, {audio_instruction}, {novel_text} -->

你是顶级的古风真人分镜导演和编剧。请将给定小说文本转化为适合视频生成的专业分镜脚本。

## 硬约束：见 system.md §1（A-K）

## 业务规则（5 条）
1. 写镜原则：每镜只承载一个核心动作/情绪转折；动作链必须**拆多镜**用 clip_transition=硬切0.2s 拼接，禁拉长单镜
2. 时长分配：对话镜 6-8s / 动作/打斗/空镜 3-5s / 过渡 4-6s / 末镜 ≥ 4s
3. 对话驱动：narration_segment 只放人物对话（"说话人道：台词原句"），禁改写；无对话填空；subtitle = narration_segment
4. 角色出现：main_subject 提到的人物必出现（prompt 开头写外貌）；旁白提及人物很可能出现；场景语义必出人物
5. kf 字段：用 `stage`（6 值 enum）+ `t_sec`（0 < t_sec < duration_sec），**禁 `time_ratio`**

### ⚠️ v6.49 红果场景/人物硬约束（违反会被自动改写为「群演/合并」）
- **scene 字段合并为 2-3 个**：所有「同一物理地点」必须用同一个 scene 字符串（例：荷塘-小舟-岸边=「照月湾」；别庄大门-别庄内-庄仆=「杨府别庄」）。禁止把同地点细分（禁「小舟」「岸边」拆开）。
- **人物合并为 2-4 人**：所有非核心配角（老汉/庄仆/船夫/家丁）统一 alias 为「群演」（main_subject/char_layout 都用「群演」名）。主角 + 主要对手 + 1-2 配角，禁止超过 4 个独立角色。

## 角色与场景物体库
### 角色库 (Character Bank)
{character_anchors}

### 场景物体尺寸锚定库 (Scene Object Bank)
{scene_object_anchors}

## 声音生成指令（按当前视频供应商注入）
{audio_instruction}

## 输出 JSON 骨架
请严格输出一个 JSON 对象，不要包含任何 Markdown 标记（如 ```json），结构如下：

```json
{{
  "title": "本集标题",
  "characters": [
    {{
      "identifier": "角色名",
      "static_features": "外貌、衣着等静态特征",
      "dynamic_features": "性格、动作习惯等动态特征"
    }}
  ],
  "shots": [
    {{
      "index": 1,
      "narration_segment": "说话人道：台词（无对话填空）",
      "subtitle": "与 narration_segment 完全相同",
      "scene": "物理地点（与原文事件一致）",
      "location": "物理地点",
      "main_subject": "画面主体",
      "prompt": "【画面描述 + 镜头景别 + 光影色调 + 运镜】\n【必须以角色外貌描述开头（如'剑眉星目身穿白袍的杨旭...'）】\n【打斗镜铁律：每镜只写1个核心动作（2-3s），连贯招式靠短镜快切】",
      "motion_desc": "动作描述（按时间段或单动作）",
      "ff_desc": "动作前一刻静态定格（≤120 字符，禁多动作/时间推进）",
      "lf_desc": "动作结束后定格（必填、含 ff 全部角色、姿态显著不同）",
      "context_bridge": "从上一镜承接的视觉描述（镜头衔接）",
      "audio_cue": "三层：人声=内容+情绪+语调+语速+音色+口音；音效=音源材质+行为+环境音；背景音乐=配乐+风格",
      "fx_effect": "特效",
      "color_tone": "色调（与上镜衔接 ≤20% 跳变）",
      "cam_idx": 1,
      "clip_transition": "转场方式 + 时长",
      "duration_sec": 6.0,
      "variation_type": "small|medium|large",
      "char_layout": [
        {{"name": "角色名", "position": "画左/画右/画中/前景/中景/后景", "surface": "船尾/船头/甲板/船舷/岸边/亭内/楼上/楼下/车内/...", "facing": "面向画左/画右/镜头/背对/面向[角色]", "role": "主/次/背景"}}
      ],
      "keyframes": [
        {{"desc": "静态构图定格，**自描述本帧完整画面**（含本镜全部角色姿态 + 关键武器/道具位置 + 伤处状态；独立生图，模型看不到 ff/lf，禁写「不重复本镜 ff_desc」「前一刻画面定格」等占位措辞）", "stage": "pre|approach|climax|react|exit|after", "t_sec": 1.5}}
      ]
    }}
  ],
  "hook": {{
    "index": 0,
    "narration_segment": "说话人道：台词",
    "subtitle": "与 narration_segment 完全相同",
    "scene": "钩子场景",
    "location": "钩子物理地点",
    "main_subject": "钩子主体",
    "prompt": "钩子画面（pre-state only：水下黑影+一截刀尖微露 或 半身探水+单手持刀）",
    "motion_desc": "钩子动作",
    "ff_desc": "前一刻静态定格（≤120 字符）",
    "lf_desc": "动作结束后定格",
    "context_bridge": "开场钩子，倒叙展示，随后淡出黑场",
    "audio_cue": "钩子音效",
    "fx_effect": "钩子特效",
    "color_tone": "钩子色调",
    "cam_idx": 1,
    "clip_transition": "淡入转场，时长0.3s",
    "duration_sec": 4.0,
    "variation_type": "large",
    "char_layout": [...],
    "keyframes": [...]
  }}
}}
```

**注意**：hook 不计入 shots 数组，shot 数组只放实际叙事镜头。

## 输入小说文本
{novel_text}
