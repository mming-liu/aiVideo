# TODO 待办清单

> 记录尚未完成的修复、待验证项和待决策事项。
> 最近更新：2026-08-10 全流水线审计（纯 LLM 依赖无代码兜底清单）

---

## 2026-08-10 全流水线审计：纯 LLM 依赖无代码兜底

> 排查发现 20+ 处「只靠 prompts.py 里的 LLM 规则文本约束、无代码校验/修正/兜底」的环节。
> 与已修好的关键帧质量案例（`_extract_char_layout` 姿态锚定）同类——LLM 不遵守就出错。
> 详见下方 A（高风险）/ B（中风险）/ C（低风险+死代码）三档。

### 🔴 高风险（LLM 违约直接进产物，下游无兜底）

- ~~**A1 脸型不注入**~~ ✅ **已修复（08-10）**：`_inject_character_costume` 现同时注入 `static_features`（脸型）+ `dynamic_features`（服装），格式「`{ident}：{static}，身着{动态}`」；威胁源别名（刺客→神秘人）也注入；三帧 + QC 补算全链路生效。
- ~~**A2 r2v 漏威胁源肖像**~~ ✅ **已修复（08-10）**：`prompt_builder.py` `_collect_r2v_references` L1029 用 `_char_aliases` 判定出镜角色，合规替换「刺客→神秘人」后刺客肖像也进视频参考图。
- ~~**A3 道具空间方位**~~ ✅ **已修复（08-10）**：新增 `_extract_prop_layout` 道具方位锚定块注入 ff/lf/kf 三帧 + 视频 prompt（`_build_prompt` 4.1d）；`validation._check_prop_spatial_anchor` 同框多道具无方位 WARN。提取规则：最近方位词、排除隐藏武器（未出水/未出鞘）、排除空间物体。
- **A4 致命伤跨镜持续**：规则8——第2镜伤口洇痕消失无告警，剧情连续性断裂。**兜底**：仿 `_check_pose_continuity` 做跨镜「伤处锚定词持续」检查。
- **A5 禁形容词/比喻**：规则5.1——「快速出剑」→ 视频模型画抽象动作，零校验零修正。**兜底**：加 `_check`（词表现成 SYSTEM_PROMPT L209-215）+ 替换修正。
- **A6 视频产物零 QC**：`submit_poll.py` `_poll_video`——成功判定=文件>20KB，时长/人物消失/字幕/幻觉无任何校验，坏视频直接进 step04。**兜底**：视频产物接 QC。
- **A7 首帧缺失无锚点**：`prompt_builder.py`——首帧缺失时 r2v 无构图锚点，`_find_fallback_reference` 已写好但从未接线。**兜底**：首帧缺失时调用。

### 🟡 中风险（有校验但无修正 / 校验面窄）

- ~~**B1 新角色脸型靠 LLM**~~ ✅ **已修复（08-10）**：`_ensure_face_shape` 加性别默认脸型兜底（male→国字脸/female→鹅蛋脸/无性别→方脸），`国字脸` 已进 `_FACE_SHAPE_HINTS` 防二次叠加；幂等已验证。
- **B2 角色名无规范化/去重**：`character_extractor.py` + `character_bank.py`——「杨旭」「杨旭。」注册成双角色。**兜底**：strip + 全半角归一 + 相似去重。
- ~~**B3 物体尺寸零校验**~~ ✅ **已修复（08-10）**：`object_extractor.py` 加 `_sanitize_object_dimensions` 按 category 分档钳制（船2-15m/车2-8m/轿1-3m/厅堂3-60m/院落5-200m），越界 WARN+钳制、非数值置 None。
- **B4 空镜判定漏「神秘人」**：`style_prefix.py` `is_empty_scene` L1338——合规替换产物不在 PERSON_HINTS，误判空镜、人物渗入。**兜底**：空镜判定补威胁源别名。
- **B5 尺寸锚子串漏配**：`vimax_bridge.py` `_inject_object_size_anchor` L1307——location 写「湖上」不写「小舟」就漏配。**兜底**：匹配增强 + 比例后验。
- **B6 视线汇聚零校验**：规则22.7/13.5——`_check_gaze_direction` 只查画左/画右，不查三角色视线汇聚同一焦点。**兜底**：加视线收敛判定。
- ~~**B7 QC critical 断链**~~ ✅ **已修复（08-10）**：`_maybe_qc_frame` 返回 critical 信号，首/尾/关键帧的新生成+缓存命中路径 QC critical → unlink 坏帧+删哈希重生成；`submit_poll` 补关键帧幻觉双保险。
- **B8 资产图无内容校验**：`asset_gen.py`——只校验>5KB，wide 图画进路人/道具图带手不发现；失败不 retry。**兜底**：接 `_maybe_qc_frame` + retry。
- **B9 对白超长句**：规则43——`_check_dialogue_length` warning-only，超长句仍进 TTS。
- **B10 敏感词表盲区**：`content_safety.py`——心口/胸口/刺/斩先验不换；`check_chain_safety()` 死代码无人调用。**兜底**：启动时链式自检 + 补漏网词。

### 🟢 低风险 / 死代码（不直接产生画面错误，但维护陷阱）

- **C1 gap_analysis 覆盖率死代码**：`gap_analysis.py` L74-93/L297-340——`validate_script_gap` 恒返回空 `low_coverage_shots`，声称的覆盖检查从未执行。
- **C2 大纲零校验**：`outline.py`——main_events/climax 非空零校验，空大纲静默注入。
- **C3 软性规则无校验**：规则13.6 ff≤80字 / 规则37 抽象词量化 / 规则46 出场设计 / 规则24 色彩≤20%。
- **C4 hook 台词去重盲区**：`gen_hook_script` 生成时拿不到主分镜文本，结构性盲区零校验。
- **C5 validate_fps_consistency 死代码**：`style_prefix.py`——定义后无调用点。

### ⭐ 建议优先修复（与已修关键帧问题同构）

✅ A1 已修 → ✅ A2 已修 → ✅ B1 已修 → ✅ B3 已修 → ✅ B7 已修 → ✅ A3 已修 → 剩余：A4（致命伤跨镜）/ A5（禁形容词）/ A6（视频零QC）/ A7（首帧缺锚点）/ B4-B6 / B8-B10