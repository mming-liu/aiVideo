# TODO 待办清单

> 记录尚未完成的修复、待验证项和待决策事项。
> 最近更新：2026-08-08 ff_desc「前一刻」改造（已实施完成）。

---

## 1. ~~ff_desc「前一刻」改造（v6.19）~~ ✅ 已实施 + 已重跑验证

**状态**: 已完成（2026-08-08），step01 重跑验证通过

**改动**:
- `style_prefix.py`: `FF_MOTION_TO_PREACTION` / `FF_PREACTION_CLAUSE_FIXES` / `FF_REACTION_TO_PREACTION` / `FF_PREACTION_STRIP_WORDS` 词库 + 8 个 `_preactionize_*` / `_detect_preaction_anchor` 辅助函数 + `detect_ff_multi_action` 放宽（pre_action 信号）+ `normalize_ff_desc` 重写
- `validation.py`: `_check_ff_multi_action` 过滤 pre_action + 文案；`_check_ff_motion_timeline` 新增「进行中动作定格」检查
- `prompts.py`: rule 13.5 / PROMPT_TEMPLATE / HOOK_PROMPT_TEMPLATE 统一「前一刻」语义
- `deepseek_schema.py`: `_SHOT`/`_HOOK` ff_desc description

**已验证**（单元级 + 真实重跑）:
- 单元级: 8 个动作镜头全部前一刻化 + 幂等 + 闸门清空；静态镜头原样
- **真实重跑（2026-08-08 10:07）**: 钩子 ff_desc 已是「水面微澜，一个黑衣刺客潜伏水下」——LLM 从源头生成前一刻（非归一化兜底）；21 镜 + 钩子全部前一刻化；`_check_ff_motion_timeline` **0 警告**（之前 6 处全消）
- step02b mock: 【视频起点】/【视频中段】/【视频终点】三段彻底错开
- 剩余: step02a/02b 真实生成验证（钩子视频动作只发生一次）→ 归入第 2 条

---

## 2. 钩子首尾帧质量修复 + 重生成验证

**状态**: 代码修复已完成，待重跑验证（2026-08-06 22:53 首帧生成但质量不达标）

**背景**: 
- r2v 收集器已改为优先用首帧 `first_frame.png` 作构图锚点（替代黄昏 wide.png 环境锚定）
- 首帧图生图已改为注入 场景near.png + 角色肖像 + 道具资产图（force_multi_ref）
- 钩子时长已改为 max(motion 分段, LLM duration, 对话估时)=6s（原 3s 截断）

**首帧质量排查发现 3 个根因（用户反馈 6 个现象）**:
1. **两帧几乎一样（只有杨旭胸口血迹不同）** → 尾帧参考图 = 首帧整图 + 资产图，模型直接抄首帧构图
2. **船太小、杨旭躺满整船** → 船尺寸锚定（6米×1.4米）在 `near_desc`/`scene_objects` 里，没进 ff_desc 图片 prompt
3. **首帧听香就尖叫溅血、提示词是一串动作** → ff_desc 把动作结果状态（血溅到脸上/尖叫）写进 t=0，校验层没拦住

**已加质量修复（代码层，无需重跑 step01）**:
- `shared/vimax_bridge.py`: 尾帧/关键帧参考图**不再含首帧整图**（`lf_refs = frame_refs`，去掉 `[ff_path_str] +`），靠 lf_desc 文字驱动动作演变
- `shared/vimax_bridge.py`: 新增 `_inject_object_size_anchor()`，首帧/尾帧 prompt 按 location 匹配 scene_objects 注入船尺寸（已验证：钩子 ff/lf 都注入「长6.0米、宽1.4米」）
- `step01_script_gen/validation.py`: `_check_ff_motion_timeline` 新增结果状态污染检查（ff 含「尖叫/溅血/血珠/瘫坐」等动作结果词且 motion 有时间分段 → 提示提前到 t=0；已验证拦截本次钩子「尖叫」）

**行动**:
- `python step02a_frame_gen/main.py --shots 0 --force` 重生成钩子首帧/尾帧，重点验证：
  - ① 尾帧不再复刻首帧（刺客位置应有变化，不再同位置举刀）
  - ② 船比例正确（不再躺满整船，6米×1.4米）
  - ③ 首帧听香不再提前尖叫溅血（惊恐未尖叫、脸上无血）
- `python step02b_video_gen/main.py --shots 0 --force` 重生成钩子视频
- 检查视频: 时间应为深青夜色（非傍晚）、船 6米×1.4米、三人物（刺客/杨旭/听香）、动作只发生一次（破水→刺→没入）

---

## 3. ~~决策: ff_motion_timeline 是否升级为硬性检查~~ ✅ 已取消

**状态**: 已取消（2026-08-08）——前一刻改造后重跑 0 警告，无需升级

**背景**: `_check_ff_motion_timeline` 目前是 **warning 级**。原考虑升级为硬性拦截。但 v6.19 前一刻改造后，step01 重跑（10:07）该检查 **0 警告**（之前 6 处全消），LLM 从源头生成前一刻 ff_desc。升级需求已消失。

---

## 4. ~~检查其余镜头（非钩子）的 ff/motion 时序~~ ✅ 已验证解决

**状态**: 已验证解决（2026-08-08）——前一刻改造后重跑 0 警告

**背景**: 钩子排查时发现镜头10 也有「已冲出」vs「窜出跃上船头」的时序矛盾。v6.19 前一刻改造 + step01 重跑（10:07）后，`_check_ff_motion_timeline` 对全部 21 镜 + 钩子扫描 **0 警告**，时序问题全部解决。

---

## 5. ~~决策: 脸型库落地（ai-face-shapes-library）~~ ✅ 已实施

**状态**: 已完成（2026-08-08）

**落地**:
- 新建 `.aiVideo/skills/face-shapes/SKILL.md`（5 种脸型速查表 + 气质映射 + 古风 prompt）
- `prompts.py` `_load_knowledge_skills` 加 `read_skill("face-shapes")` 分支
- `character_extractor.py` 提取 prompt 加脸型选择规则（LLM 按气质选型，写入 static_features）
- `character_portraits_generator.py` `PROMPT_FACE_CLOSEUP` 加 `{physical_features}` 占位（脸型词进面部特写图）

**⚠️ 已有角色补脸型词（已完成）**: `character_extractor._ensure_face_shape` 按角色名/特征确定性映射给已知角色补脸型词（听香→鹿系、刺客→猫系、肖管事→方脸等），script 层 characters 带脸型词供肖像/视频使用；杨旭/张十三/老汉未匹配保持原样

---

## 6. ~~决策: 人脸去塑料库落地（ai-face-deplastic-library）~~ ✅ 已实施

**状态**: 已完成（2026-08-08）

**落地**:
- 新建 `.aiVideo/skills/face-deplastic/SKILL.md`（毛孔/红润/明暗/颗粒四要素 + 古风适配）
- `prompts.py` `_load_knowledge_skills` 加 `read_skill("face-deplastic")` 分支
- `character_portraits_generator.py` `PROMPT_FACE_CLOSEUP` Texture 行替换为完整去塑料段（仅面部特写）

**已验证**: skill 加载正常、`_load_knowledge_skills` 输出含两个新 skill、PROMPT_FACE_CLOSEUP format 完整


---

## 备注

- 已完成的项（2026-08-06 清理）: ① 重跑分镜让 ff_desc 时序修复生效（已重跑验证）；② 首帧参考图组合 5 张超限 → B+C 修复（超限拼图 + 角色重要性排序，重跑验证首帧生成成功）；③ combat-action 打斗动作吸收；④ 抖音视频 3 点吸收（克制感情绪戏/面部特写/切换拍摄对象）
- 2026-08-07 新增：5 条教程评估 + 3 点接入（v6.17）——① 打斗四段式节奏（combat-arc，反转长镜为规则7唯一例外+闪白+空镜升华）；② 台词标点情绪语法（voice-punctuation，只改标点不改文字）；③ 前后遮挡显式化（occlusion-layering，写清什么遮挡什么+部位）。未接入 2 条（手绘故事板/漫剧7模块）。skill 注入 prompts.py `_load_knowledge_skills`，规则30/31/32 进 SYSTEM_PROMPT，检查进 validation.py
- 相关文件: `shared/vimax_bridge.py`（尾帧参考图、_inject_object_size_anchor、_detect_action_subject）、`shared/vimax_adapters.py`（超限拼图）、`_image_gen/bailian_provider.py` + `agnes_provider.py`（IMAGE_MAX_REFS 兜底）、`step01_script_gen/validation.py`（_check_ff_motion_timeline 结果状态污染检查 + in_progress_exempt）、`step01_script_gen/prompts.py`（规则28/29 注入）
- 均未提交 git，工作区已改
