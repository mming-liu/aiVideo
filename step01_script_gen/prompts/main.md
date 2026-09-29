<!-- pro_main.md — 分镜生成主模板 (PROMPT_TEMPLATE) v6.43 精简版 -->
<!-- 精简前 159 行 → 60 行 -->
<!-- 占位符：{shot_count_min}, {shot_count_max}, {character_anchors}, {scene_object_anchors}, {audio_instruction}, {novel_text} -->

你是顶级的古风真人分镜导演和编剧。请将给定小说文本转化为适合视频生成的专业分镜脚本。

## 硬约束：见 system.md §1（A-K）

## 字段职责（最高优先级）
- prompt：仅图片构图与画面事实：主体、角色外观、姿态、位置、道具、光位。禁止写运镜、转场、时长节奏、视频防崩句、模型参数或 negative prompt。
- camera_movement：只在这里写运镜。
- clip_transition：只在这里写转场与转场时长。
- motion_desc：写视频动作与时间分段。
- audio_cue：写声音、对白和音效。
- Step02b 会把 ff_desc + motion_desc + lf_desc + camera_movement 组装成最终视频 prompt；clip_transition / fx_effect 不进入视频 prompt（clip_transition 在 Step04 合并时转成 xfade 转场）。

## 业务规则（5 条）
1. 写镜原则：每镜只承载一个核心动作/情绪转折；动作链必须**拆多镜**用 clip_transition=硬切0.2s 拼接，禁拉长单镜
2. 时长分配：{{DUR_RANGES}} / 末镜 ≥ 4s（非末镜不得低于 3.5s，会被质量门判 blocking）
2b. 台词容量：单镜 narration_segment 总字数 ÷ 4（字/秒）= 台词最低需求秒数，**必须 ≤ {{SHOT_CAP}}s**（安全值 ≤ {{NARR_MAX_CHARS}} 字）。超了就按原文连续切分成多镜，禁改写台词、禁拉长单镜
2c. ★ **一条台词也能切开**：台词原句约束的是**文字不得改写/增删**，不是「一句引号只能给一个镜头」。**一条台词 > {{SHOT_CAP}}s 时，把这一条按原文连续切成 2-3 段分给多个镜头**，每段各带「说话人道：」前缀、段间首尾相接、逐字不增不减。**切点只许落在句末标点（。！？…；）之后**——逗号/顿号切点一律非法（句中碎片会被硬门判破损、整稿拒收）；连续标点串（如 `……`）只在串尾切。且**能不能切由「含前缀估时 ≤ {{SHOT_CAP}}s」+「存在句末切点」共同决定**：{{NARR_MAX_CHARS}} 字只是单镜安全字数，**不是「超过就一定切得开」**；没有句末切点的长台词**不可表示**，须回规划层另选，不要在生成层硬拆。例：句内有 `……` 停顿的 89 字（≈20.2s）→ 切 2 段、每段 ≈45 字；37 字（≈8.8s）一镜可容纳。这不是多余拆镜，是必须的——整条留一镜会因超容拒收整稿
3. 对话驱动：narration_segment 只放人物对话（"说话人道：台词原句"），禁改写；无对话填空；subtitle = narration_segment
4. 角色出现：main_subject 提到的人物必出现（prompt 开头写外貌）；旁白提及人物很可能出现；场景语义必出人物
5. kf 字段：用 `stage`（6 值 enum）+ `t_sec`（0 < t_sec < duration_sec），**禁 `time_ratio`**；**帧数按规则 3b（每个分时段至少一帧）**
6. 事件履约声明（v6.138）：prompt 末尾「生成执行契约」把镜头节拍编号为 B1..Bn、选用台词编号为 D1..Dm；每镜必须输出 `"plan_refs": {{"beats": [B…], "lines": [D…]}}`（本镜承载哪些节拍/台词，编号按契约块）；**每个节拍都必须被至少一镜承载**，声明顺序不得回退（后位节拍/台词不得在前位完成前呈现——供述未完不得提前出现尖叫/掌掴等后果画面）；纯过渡镜写空数组

### ⚠️ v6.49 红果场景/人物硬约束（v6.53 起由代码层强制执行）
- **scene 字段合并为 2-3 个**：所有「同一物理地点」必须用同一个 scene 字符串（例：荷塘-小舟-岸边=「照月湾」；别庄大门-别庄内-庄仆=「杨府别庄」）。禁止把同地点细分（禁「小舟」「岸边」拆开）。
- **人物合并为 2-4 人**：所有非核心配角（老汉/庄仆/船夫/家丁）统一 alias 为「群演」（main_subject 与 frame_layouts.*.name 都用「群演」名）。主角 + 主要对手 + 1-2 配角，禁止超过 4 个独立角色。
- **威胁源统一写「神秘人」**：main_subject 与 frame_layouts 的 name 字段写「神秘人」（代码层会把「刺客/杀手/黑衣人/蒙面人」折叠为「神秘人」，与 ff_desc 合规形态一致）。
- **代码层会自动改写**：main_subject + frame_layouts.*.name 命中 alias 表（船夫→群演、刺客→神秘人）会被直接替换，ff_desc 不动。

### ⚠️ v6.51 时序三铁律（HARD 级，代码层校验）
1. **FF 前一刻**：ff_desc 定格在核心动作发生前的静止瞬间，禁「正在/已跃出/已刺入/渐渐/逐渐」等进行时/完成时词
2. **LF 留接口**：lf_desc 是动作结束态，必须能被下一镜 ff_desc 直接承接（邻镜角色姿态/位置不跳变）
3. **motion 分段写**：motion_desc 按 `前X秒：…；X-Y秒：…；Y秒后：…` 三段式写，每段含「动作动词+主体+方向」
3b. **★ 每段一帧（帧数铁律，v6.33；2026-09-27 写回生成面）**：`motion_desc` **显式分了时段**时，**每个正文非空的时段至少有一帧** `keyframes` 落在它自己的时间窗内（`t_sec` 取**该段中点**最稳），该帧 `desc` 描写**这一段自己的动作**，`frame_layouts.kfs` **按同序号**给出等长条目（**两表长度必须始终相等**）。**允许**为叙事额外加帧；**不允许**某个动作时段一帧都没有——那等于那段动作没有画面证据，下游按 blocking 处理。`motion_desc` **未分时段**的单动作镜不受此约束（0 帧或按需给帧都可以）。
4. **temporal_plan 与 motion_desc 同源**：每个 shot 还要输出 `temporal_plan.events`，把 motion_desc 的分段结构化成事件表 `[{{start_sec, end_sec, description, source_quote}}, ...]`。
   - 段数与起止秒数必须与 motion_desc 的 `前X秒/X-Y秒` **逐段一致**；`description` 即该段正文（不含时间标签）
   - `start_sec`/`end_sec` 落在 `(0, duration_sec]` 内，按时间升序、首尾相接、不重叠
   - `source_quote` 填该事件在**原文**中的出处短句；原文没有对应句时填空字符串（禁止编造）
   - `event_id` 由管线分配，**不要自己产出**

## 角色与场景物体库
### 角色库 (Character Bank)
{character_anchors}

### 场景物体尺寸锚定库 (Scene Object Bank)
{scene_object_anchors}

## 声音生成指令（按当前视频供应商注入）
{audio_instruction}

## frame_layouts 字段（v6.57+ per-frame spec 事实源，每镜必填）

**结构**：`{{ff:[...], lf:[...], kfs:[[...]]}}`，每项 `{{name, on_screen, position, pose, gaze_target, props}}` **6 字段全必填**。

**`on_screen` 决定 QC 期望人数（治 lf 互斥根因）**：
- ✅ 正确：lf「神秘人已没入水中不见踪影」→ lf 项 `{{"name": "神秘人", "on_screen": false, ...}}`
- ❌ 错误：把已退场角色 lf 项 `on_screen: true` → QC 期望 3 人，模型多画人头（互斥 bug）

**空镜 = 三桶全 `[]`**：
- ✅ 正确：`{{"ff": [], "lf": [], "kfs": []}}`
- ❌ 错误：哨兵行 `{{"name": "空镜", "on_screen": false, ...}}` → 污染 QC 期望人数

**`kfs` 与 `keyframes` 一一对应**：kfs[i] = keyframes[i] 的出镜表；数组长度必须相等。

**⚠️ 重要：`kfs` 数组长度必须严格等于 `keyframes` 数组长度**——有多少个关键帧就写多少个出镜表，**不能偷懒只写 1 个**！例如 keyframes 4 个 → kfs 必须也是 4 个二维数组项，每项是该关键帧的出镜表。Constraint 会硬校验（v6.57+ HARD 违例），不一致直接触发 regen。

**`gaze_target` 值域**：角色名 / 画左 / 画右 / 画上 / 画下 / 无。

**`pose`**：自由态词（≤15字：盘坐/潜伏/仰卧/半身探水/站立/倒纵入水/...）。

**`props` 语义铁律（治道具时序倒流）**：
- props = **该帧时刻该角色实际手中持有/身上佩戴的道具**，不是角色标配/全镜通用道具
- 该帧散文含「X已脱手/滑落/倾倒/坠落/掉/放下」→ 该帧 props **不得含 X**，pose **不得含「握X/提X/执X/拿X/举X/持X」**
- ✅ 正例：ff 散文"酒壶已脱手、钓杆垂于水面"→ ff props=`[]`、pose 不含"握酒壶"
- ❌ 反例：ff 散文"酒壶已脱手"但 ff props=`["酒壶"]`、pose="右手握酒壶"（时间倒流）
- ✅ 正例：kf2 散文"酒壶倾倒在船板上"→ props=`[]`（酒壶不在手中）

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
      "plan_refs": {{"beats": [1], "lines": [1]}},
      "narration_segment": "说话人道：台词（无对话填空）",
      "subtitle": "与 narration_segment 完全相同",
      "scene": "物理地点（与原文事件一致）",
      "location": "物理地点",
      "main_subject": "画面主体",
      "prompt": "【静态画面描述 + 角色外观/姿态/构图 + 光影色调】\n【禁写运镜/转场/时长/视频防崩句；这些分别写 camera_movement、clip_transition 和 motion_desc】",
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
      "frame_layouts": {{
        "ff":  [{{"name": "杨旭", "on_screen": true, "position": "画中", "pose": "盘坐", "gaze_target": "无", "props": ["酒壶"]}}],
        "lf":  [{{"name": "杨旭", "on_screen": true, "position": "画中", "pose": "仰卧", "gaze_target": "无", "props": []}}],
        "kfs": [[{{"name": "杨旭", "on_screen": true, "position": "画中", "pose": "盘坐", "gaze_target": "无", "props": []}}]]
      }},
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
    "frame_layouts": {{
      "ff":  [...],
      "lf":  [...],
      "kfs": [[...], ...]
    }},
    "keyframes": [...]
  }}
}}
```

**注意**：hook 不计入 shots 数组，shot 数组只放实际叙事镜头。

## 输入小说文本
{novel_text}
### ⚠️ v6.51.1 单镜内字段一致性（代码层校验）
- **motion 分段符号统一**：motion_desc 分段必须用全角中文「秒」「：」「；」，严禁出现半角「:」「;」「s」（如禁5-6秒;，必须5-6秒：）。段数 ≤ duration_sec 秒数（6秒分4段违反）。
- **motion 段内字数**：每段 ≤ 60 字（避免 LLM 在段内堆重复字凑长度）。整段 motion_desc 总长 ≤ 180 字。
- **FF/LF 动词唯一性**：FF 与 LF 不得用相同的开头动词（如都「端坐」「抬手」）。FF 的核心动词与 LF 的核心动词集合 Jaccard ≤ 0.5（去停用词后）。
- **FF/LF/Motion 去重**：同一镜内 FF 与 LF 不得包含完全相同的连续 4 字词组（避免「眼神拉，眼神拉」「头微歪，头微歪」类病句重复）。
- **scene 与 location 一致**：若 scene 与 location 完全相同（含空字符串），则视为 scene 字段未填写，代码层用 location 派生 scene。

