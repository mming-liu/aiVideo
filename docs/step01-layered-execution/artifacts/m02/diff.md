# U6 离线修订候选（M02，桩响应经实际入口）

- 原稿：`baselines/2026-11-12-pre-layered/u6_frozen/candidate_d1a/candidate.json`（sha256 `3f88a060082173ca36cdcb6f14f94002843d502fe09930812ea13dc3bf42321f`）
- 冻结原文：`output/锦衣夜行/part_01_杀青州/chapter_001/_resume/novel_aba1adc22826.txt`（sha256 `aba1adc22826c2834754af529b6235e3bd4a5428b59a45c10353f5483ce854c3`）
- 原稿 revision：`84ff4112e6f6649489d7571bc0a1df73b2800247c902b8bd8bf07780ee514dc7`
- 修订后 revision：`abfd217dae61e9f8604ab95e3654a91893c2d2a3b53ba68f54d310c460efc88a`
- 桩调用次数：1；block：无
- 入轮目标：['shot_1', 'shot_11', 'shot_6', 'shot_7']
- 接受：True（resolved=0，字段触达=3）

## 差异（只列发生变化的字段）

### 镜 7 / `ff_desc`

- 原：神秘人右手反握带血短刃、双腿屈弹半起身，杨旭素白色轻袍仰倒船板心口暗红
- 新：神秘人双手空空、双腿屈弹半起身，杨旭素白色轻袍仰倒船板心口暗红

### 镜 7 / `lf_desc`

- 原：水面波纹扩散，神秘人不见踪影，杨旭素白色轻袍仰倒船板、心口衣料暗红洇痕持续可见，左手钓杆垂落船舷，右手酒壶倾倒酒水洒在甲板，冷蓝月光摇碎一湾月亮
- 新：水面波纹扩散，神秘人不见踪影，杨旭素白色轻袍仰倒船板、心口衣料暗红洇痕持续可见，左手钓杆垂落船舷，青瓷酒壶滚落甲板，酒水洒在甲板，冷蓝月光摇碎一湾月亮

### 镜 7 / `frame_layouts.ff`

- 原：[{"name": "神秘人", "on_screen": true, "position": "画右船舷", "pose": "屈腿欲纵", "gaze_target": "画右", "props": ["短刃"], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船舷"}, "states": ["屈腿"]}, {"name": "杨旭", "on_screen": true, "position": "船头", "pose": "仰倒", "gaze_target": "无", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船头平坦处"}, "states": ["仰倒", "心口暗红"]}]
- 新：[{"name": "神秘人", "on_screen": true, "position": "画右船舷", "pose": "屈腿欲纵", "gaze_target": "画右", "props": [], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船舷"}, "states": ["屈腿"]}, {"name": "杨旭", "on_screen": true, "position": "船头", "pose": "仰倒", "gaze_target": "无", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船头平坦处"}, "states": ["仰倒", "心口暗红"]}]

### 镜 11 / `ff_desc`

- 原：张十三压低身形逼近听香耳畔，右手食指竖于唇前，目露警告；听香双手捂脸后缩，身子紧贴船板
- 新：张十三压低身形逼近听香耳畔，右手尚未抬起，目露警告；听香双手捂脸后缩，身子紧贴船板

### 镜 11 / `frame_layouts.ff`

- 原：[{"name": "张十三", "on_screen": true, "position": "画左", "pose": "俯身竖指", "gaze_target": "听香", "props": [], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船板"}, "states": ["俯身", "厉色"]}, {"name": "听香", "on_screen": true, "position": "画右", "pose": "捂脸后缩", "gaze_target": "张十三", "props": [], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船板"}, "states": ["捂脸", "半边脸红肿"]}, {"name": "杨旭", "on_screen": true, "position": "船头", "pose": "仰倒", "gaze_target": "画上", "props": [], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船头平坦处"}, "states": ["仰倒", "心口暗红", "素白轻袍"]}]
- 新：[{"name": "张十三", "on_screen": true, "position": "画左", "pose": "俯身未抬手", "gaze_target": "听香", "props": [], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船板"}, "states": ["俯身", "厉色"]}, {"name": "听香", "on_screen": true, "position": "画右", "pose": "捂脸后缩", "gaze_target": "张十三", "props": [], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船板"}, "states": ["捂脸", "半边脸红肿"]}, {"name": "杨旭", "on_screen": true, "position": "船头", "pose": "仰倒", "gaze_target": "画上", "props": [], "support": {"id": "boat_1", "kind": "平底敞口采莲小舟", "surface": "船头平坦处"}, "states": ["仰倒", "心口暗红", "素白轻袍"]}]

## 被后处理覆盖的授权改动

下列字段在**合并后已经改对**，但被本轮后处理改写回原值；接受判定只按整轮计，因此该轮仍然被接受。这一栏是本包新增的台账（`postprocess_reverted`），此前没有任何记录。

- `shot_1`：`prompt`
- `shot_6`：`prompt`

机制（已定位，`shot_1`）：`step01_script_gen/constraints/framing_jump.py` 在相邻镜景别跨度 > 2 时改写**前一镜**的 prompt。它用 `_shot_size_level` 取景别，而该函数按词表顺序做**子串匹配**——`大远景` 先命中 `远景`（词表里 `远景` 排在 `大远景` 之前），于是「大远景」被判成 `远景`，与下一镜 `中近景` 相差 4 档触发改写；随后 `prompt.replace('远景', '特写')` 作用在「大远景」上，**恰好还原成「大特写」**。即：审计要求的一致性与相邻镜景别跨度判据冲突，且子串匹配让冲突落在原值上。

`shot_6` 的 prompt 同样被改写回原值（同一类 prompt 强化约束，未逐一定位到具体函数）。按 M09 口径，本包**不改这两条判据**，只把冲突记账并放进影子评估清单。

