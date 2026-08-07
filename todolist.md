# TODO 待办清单

> 记录尚未完成的修复、待验证项和待决策事项。
> 最近更新：2026-08-06 钩子首尾帧质量修复（已清理已完成项）。

---

## 1. 钩子首尾帧质量修复 + 重生成验证

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

## 2. 决策: ff_motion_timeline 是否升级为硬性检查

**状态**: 待决策

**背景**: `_check_ff_motion_timeline` 目前是 **warning 级**（质量问题不阻断）。如果希望它成为硬性拦截（ff_desc 含完成态动词 + motion 同动作推进 → 重新生成直到通过），需加进 `validation.py` 的 `hard_fail_checks` 列表。

**当前硬性检查**: `["prop_consistency", "shot_transition", "semantic_risk", "script_novel_alignment", "ff_lf_completeness", "body_purification"]`

---

## 3. 检查其余镜头（非钩子）的 ff/motion 时序

**状态**: 待确认

**背景**: 钩子排查时发现镜头10 也有「已冲出」vs「窜出跃上船头」的时序矛盾。`_check_ff_motion_timeline` 会对所有镜头扫描，重跑 step01 后可确认其他 28 个镜头是否也有类似问题。

---

## 4. 决策: 脸型库落地（ai-face-shapes-library）

**状态**: 待决策

**背景**: 已建 `references/ai-face-shapes-library.md`（5 种 AI 脸型：雀斑/天鹅/猫系/鹿系/狐系，含完整 Prompt + 核心特征 + 古风适配）。目前仅是素材库，未接入代码。

**可选落地方式**:
- A. `storyboard_refs.py` 加 `FACE_SHAPES` 词库 section，注入 step01 分镜 prompt（角色外貌描述可选脸型）
- B. 写入角色 `static_features`（CharacterBank），肖像/首帧跨章用同一脸型词
- C. 保持纯素材库，仅手动参考

---

## 5. 决策: 人脸去塑料库落地（ai-face-deplastic-library）

**状态**: 待决策

**背景**: 已建 `references/ai-face-deplastic-library.md`（5XL-AI 去塑料脸：基础核心 + 强化三组 + 完整模板 + 古风适配）。目前仅是素材库，未接入代码。

**可选落地方式**:
- A. `prompts.py` 5.3 古风人物公式的「皮肤必须强调真实纹理」处，升级为完整去塑料段落
- B. 肖像生成器（character_portraits_generator）面部段注入
- C. 保持纯素材库

**注意**: 脸型库和去塑料库互补——先选脸型定轮廓气质，再加去塑料段定皮肤真实感。两项可合并落地（同一次 code 改动）。

---

## 备注

- 已完成的项（2026-08-06 清理）: ① 重跑分镜让 ff_desc 时序修复生效（已重跑验证）；② 首帧参考图组合 5 张超限 → B+C 修复（超限拼图 + 角色重要性排序，重跑验证首帧生成成功）；③ combat-action 打斗动作吸收；④ 抖音视频 3 点吸收（克制感情绪戏/面部特写/切换拍摄对象）
- 2026-08-07 新增：5 条教程评估 + 3 点接入（v6.17）——① 打斗四段式节奏（combat-arc，反转长镜为规则7唯一例外+闪白+空镜升华）；② 台词标点情绪语法（voice-punctuation，只改标点不改文字）；③ 前后遮挡显式化（occlusion-layering，写清什么遮挡什么+部位）。未接入 2 条（手绘故事板/漫剧7模块）。skill 注入 prompts.py `_load_knowledge_skills`，规则30/31/32 进 SYSTEM_PROMPT，检查进 validation.py
- 相关文件: `shared/vimax_bridge.py`（尾帧参考图、_inject_object_size_anchor、_detect_action_subject）、`shared/vimax_adapters.py`（超限拼图）、`_image_gen/bailian_provider.py` + `agnes_provider.py`（IMAGE_MAX_REFS 兜底）、`step01_script_gen/validation.py`（_check_ff_motion_timeline 结果状态污染检查 + in_progress_exempt）、`step01_script_gen/prompts.py`（规则28/29 注入）
- 均未提交 git，工作区已改
