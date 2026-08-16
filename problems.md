# 问题记录（Problems Log）

> 记录流水线开发中遇到过的真实问题（含已解决与开放中），避免重复踩坑、重复调查。
> 每个问题记录：现象 → 根因 → 处理/状态。与 `todolist.md`（仅当前待办）互补。
> 最近更新：2026-08-16

---

## 🔴 开放中（未解决/未收敛）

### P3. 图片模型不忠实渲染 ff_desc 锚点（08-14）
- **现象**：hook 首帧/尾帧。最终 prompt 明确写了「画右水下，一团玄色黑影轮廓，一截刀尖微露水面，正对杨旭心口方向」「【首帧禁血】刺客尚未出水、未刺中任何人」，但渲染出的图是**刺客上半身出水举匕首**、匕首尾部**发蓝光**（prompt 无任何「蓝光/发光」描述，为模型幻觉）；尾帧 prompt 写「神秘人已彻底没入水中」，图却是**刺客站水面上**（违反物理）；听香未跪坐、杨旭侧躺、钓杆画成细长撑杆。
- **根因**：`_build_final_frame_prompt` 重建证明**锚点已忠实到达图片模型**——是 wan2.7-image 对复杂单帧（3角色+威胁+道具+前一刻定格）渲染保真不足；「一截刀尖微露水面」诱导模型脑补持刀者（刀不浮空→补手→补手臂→刺客出水）；发光武器为模型常见幻觉。
- **影响**：Plan A 锚点系统在**文本层正确**（ff_desc/校验/最终 prompt 全达标），但**超出现行模型能力边界**——文本对、图不对。
- **待办方向**（用户定夺）：①简化 hook 首帧构图降载（纯水下黑影不要刀尖/删一件道具）；②hook 帧换 wan2.7-image-pro（官方标注复杂指令遵循更强）；③修 QC 让它当场拦住（见 P4）。
- **教训**：ff_desc 锚点写得再全，也要在「模型画得出」的复杂度内——锚点系统是上限放大器，不是模型能力替代品。

### P4. 图片 QC 的 prompt_semantics 失效（08-14 → 08-15 修复）
- **现象**：hook 4 帧 sidecar 全部 `has_hallucination:false, issues:[]`，但图明显不符 prompt（刺客出水/站水面/蓝光/姿势错）。QC 宣称做了「画面 vs 最终 prompt 语义核对」却全放过。
- **根因**：①传递链终点 `qc_image` 只认 `prompt is None`——空串/纯空白不触发 `build_qc_prompt` 兜底，视觉模型拿到空的「画面描述」无物可比 → 必然 clean（sidecar clean 实为「没查」）；②判定规则只给 warning、无关键锚点升级通道。
- **解决（08-15）**：`shared/image_qc.py` `qc_image` 改 `not (prompt and prompt.strip())` 才走兜底；`build_qc_prompt` 增关键锚点（服装色/人物数量/威胁源状态）严重度规则，`QC_SEMANTICS_CRITICAL=1` 升级 critical（删帧重生成，当场拦住），默认 warning。
- **教训**：QC 说 clean ≠ 图对。sidecar 的 `issues:[]` 在 QC prompt 无效时是「没查」而非「没问题」。

### P5. 关键帧数量 2→1（08-14 → 08-15 修复）
- **现象**：hook 的 script 有 keyframes×2（ratio 0.4/0.8），但 `images/` 只产出 1 张 `shot_0_kf.png`（`vimax_working/shots/0/` 有 keyframe_0/1）。
- **根因**：provider `_download` 的 fname_prefix 对**所有**关键帧统一取 `"kf"`（只有首帧/尾帧区分 ff/lf）→ 多个中间帧写同名 `shot_N_kf.png` 互相覆盖，只留最后一张。
- **解决（08-15）**：bridge 传 `frame_desc="关键帧{i}(t=...)"`，两个 provider（bailian/agnes）解析序号 → `shot_N_kf_0.png` / `shot_N_kf_1.png`。
- **教训**：provider 落盘命名与 bridge 帧类型脱钩时，同名覆盖是最隐蔽的数据丢失——命名必须携带唯一标识（帧类型+序号）。

### P6. 主角后镜省略服装色（08-14 → 08-15 修复）
- **现象**：规则 13.7 提升最高优先级后，主分镜服装色告警 24→10；剩 10 条（镜头 14-23）全为**主角杨旭**后镜省略——「杨旭仰卧船头，心口暗红洇痕」没写「素白轻袍」，而张十三/听香都重述了。
- **根因**：LLM 把主角当「已出场/已建立」，后镜不再重述；13.7 对 shots 的约束力仍弱于 hook（hook 是独立模板突出条目）。
- **影响**：渲染期 `_inject_character_costume` 已注入服装色兜底（实际渲出有）；ff_desc 自足性待收敛。
- **解决（08-15）**：规则 13.7 补「主角/已出场角色后镜也必须重述——禁止以『前镜已述/主角已建立』为由省略，主角与配角一视同仁」；待下一轮 step01 regen 验证收敛。

### P18. 校验无闭环 + 开篇冲突告警被淹没（08-15 确认 → 当天修复）
- **现象**：step01.log 每轮 ~60 条 WARN 纯打印后落盘；「前1/3段未建立核心冲突」「未检测到第一个爆点」这类直接决定前 10 秒留存的告警也只是一行 WARN。
- **根因**：`[GAP] 差距分析通过（尝试 1/2）`每轮都一次通过 → 重生成反馈（top-5 类）**从不附加** → 所有 WARN 无人修；`hard_fail_checks` 不通过也只 print。
- **影响**：高价值告警（开篇冲突/威胁朝向/keyframes 质量/首帧时序）带伤进 step02 烧额度；WARN 噪音导致「狼来了」效应，没人看 WARN。
- **解决（08-15）**：①定向修复轮 `_repair_shot_warnings`（main.py）——GAP 通过后高价值告警触发单镜 LLM 修复，只重写告警镜头，最多 2 轮/轮 6 镜，失败即停不中断；开篇冲突/爆点密度并入 `_REPAIR_PRIORITY_CATEGORIES`（作为修复前言注入）；`REPAIR_ROUNDS_ENABLED` 可关。②step02b 失败计数修真——`gen_all_videos` 统一以 `submit_poll._failed_videos` 为准（覆盖提交/QC/轮询失败 + Exception），坏镜头不再带 `Failed: 0` 假象进 step04。③QC 失败自动重提交（`VIDEO_QC_RESUBMIT=2`）+ 视频 seed 固定（`shot_index|ff|lf|prompt` 哈希派生）。④视频 QC 首/中/尾三帧（原单帧中段，漏检开头/结尾黑场淡出）。
- **验证**：`py_compile` 全过；修复收集器对现有脚本识别 23 个待修镜头；seed 确定性冒烟通过。
- **教训**：校验广度和「告警→修复」闭环是两回事；warning-only 系统会随时间积累成无人阅读的噪音——高价值告警必须有触发修复的通道，且要全脚本重生成之外的「单镜手术」级修复手段。

---

## ✅ 已解决（存档）

### P1. hook 首帧手改+反复重渲死胡同（08-13 → 08-14 解决）
- **现象**：手改生成文件的 ff_desc + 反复重渲，越改越乱——杨旭素白轻袍漂色、听香视线变向、船画小两人挤满、刺客不看目标。
- **根因**：①图像模型每次重渲=全新随机，文字手改对像素细节无控制力；②手改删掉锚点（尺寸/服装色/威胁源朝向），只靠角色肖像软锚必漂。
- **解决**：改走「规则+校验」系统性修（Plan A）：`_HOOK` 加 `char_layout`+keyframes；ff_desc 全锚点校验（威胁朝向/视线方位/尺寸锚/服装色）；规则 13.7 服装色提升最高优先级。LLM 一次生成全锚点 ff_desc。
- **教训**：**手改生成文件 + 反复重渲是死胡同**。要改就让 LLM 按规则一次生成对，校验兜底。

### P2. LLM 原始输出丢失，无法回滚（08-13 记录）
- **现象**：想回滚 hook 6 字段到 08-13 22:50 的 LLM 原始输出，但只有 todolist 抄录的 ff_desc 一句话，其余 5 字段（lf/motion/prompt/keyframes）原文丢失。
- **根因**：`output/` 整个目录被 `.gitignore` 忽略（`.gitignore:14`），`script_result.json` 从未进 git；无 .bak、无 raw 响应缓存。
- **解决**：无法 git 还原，用户决策跳过回滚、全量重跑 step01。
- **教训**：生成产物不进 git，**重要基线需主动备份**（或关键中间产物入库）。

### P7. 校验误报刷屏（08-11 → 08-12 解决）
- **现象**：step01.log 同一类校验误报十几条重复，淹没真实缺陷。
- **根因**：自己打的检查自己误报（P0 1-5：电影级污染/fps对齐/空镜误判/多地点堆叠/渲染参数缺失）+ 规则过严（P1 6-7：keyframes/视线刷屏）。
- **解决**：P0 修根因（remove 词表/按 fps=24 对齐/动作镜排除/去纯转场词/提前注入渲染参数）+ P1 聚合提示；真实缺陷 8/9/11/12 单独修。
- **教训**：校验也要单测防自误报；刷屏用「每类最多 1 条 + 命中数」聚合。

### P8. qwen-image 800 字 prompt 限制遗留（08-08 定 → 08-14 确认并放宽）
- **现象**：ff_desc 80/120 字符限制与锚点栈（服装色/尺寸/威胁朝向）打架，hook ff_desc 塞不下尺寸锚。
- **根因**：80/120 是 08-08 换 `qwen-image-2.0-pro` 时定（官方硬限制：t2i 800 字符/2.0 系列 1300 token）；现行 `wan2.7-image` 官方文档「最大输入长度 = ---（未设定）」。
- **解决**：A+B 放宽 **普通镜头 ≤80→≤120，钩子 ≤120→≤160**；尺寸锚职责交给 bank 渲染期注入（`_inject_object_size_anchor`，bank 未覆盖才要求 ff_desc 自足）。
- **教训**：模型换代的遗留限制要跟着清；先查现行模型官方限制再决定约束值。

### P9. prompt.py 精简 token 预估偏高（08-12 记录）
- **现象**：预估省 1.6万-2.2万 token，两轮做完实测只省 ~5.5k token（14%）。
- **根因**：把 SYSTEM_PROMPT 源码块字符（含引号/换行转义）误当正文字符，高估可删示例量。
- **解决**：无（记录事实，剩余可压缩空间已很小，继续删风险/收益比上升）。
- **教训**：预估 token 省量要用「LLM 实际看到的正文」算，别用源码字符。

### P10. hook 与 shot 校验「两套」（08-14 确认，未重构）
- **现象**：`validate_hook`（手写子集）+ `_run_prompt_checklist`（54 项）两套校验；hook 模板改动、阈值改动要手动同步两处。
- **根因**：hook 在主分镜生成之后单独 LLM 生成（要引用主分镜台词黑名单），主校验跑在 parse_response（hook 未挂载）；hook 挂载后只能 `validate_hook` 补查。
- **解决**：未重构。已把威胁耦合/keyframes/站位/ff锚点 手动补进 `validate_hook` 缓解。
- **待办方向**：hook 挂载后重跑 `_run_prompt_checklist`（此时 script 有 hook）统一，`validate_hook` 退化为薄封装。

### P11. 12 位「未知」问题（占位，待补充）

> 以下为已知但未单列的问题（记忆中有记录，先占位不展开）：
> - hook 武器 t=0 不可见 → 模型凭空变武器/刺向错（已修 `_fix_ff_weapon_visible`）
> - 尾帧武器残留（匕首尖停水面）→ 已修规则 609/validation
> - 空镜出现人物幻觉（拿蜡烛的人）→ 前缀撤烛火+负面撤禁亮光+旁白改画外音
> - 图片 429 限流白烧免费额度 → 读 retry-after + 60s 窗口兜底
> - 首尾帧动漫→真人漂移 → 全镜头首尾双关键帧+prompt 尾部收束

### P12. is_happyhorse NameError 回归（08-15 引入 → 当天修复）
- **现象**：`_video_gen/bailian_provider.py` r2v 提交必崩 NameError（`is_happyhorse` 未定义），而 `.env.content` 默认 `VIDEO_REFERENCE_MODE=r2v` → **所有镜头视频生成失败**。
- **根因**：08-15 改 ref_compose 时把 `_create_r2v_task` 里的 `is_happyhorse = "happyhorse" in model.lower()` 连同旧 `[:max_refs]` 裁剪逻辑一起删了，但 445/459/467 行仍引用。
- **解决**：补回定义；`py_compile` + rg 验证。
- **教训**：**重构删代码时，用 rg 全局确认所有引用点**（子代理审计才发现的回归）；改 provider 模型分支后至少 py_compile + 一次冒烟调用。

### P13. 中间关键帧白生成（08-15 确认 → 修复）
- **现象**：step02a 花额度生成 keyframe_0/1，但 bailian wan2.7-i2v 的 media 组合固定（首帧+尾帧+音频），`extra_keyframe_paths` 全程未读；image 模式下 prompt 还虚假声明「有关键帧参考图」→ 模型被告知存在实际没传的图。
- **根因**：bailian provider 只编码 `image_path`/`last_frame_image_path`；keyframe 落盘逻辑（P5 修过）与视频请求脱钩。
- **解决**：prompt_builder 按 provider 区分——bailian image 模式不收集 kf（不虚假声明），agnes image 模式保留（真传），r2v/text 不收集。
- **教训**：帧图生成（step02a）与视频生成（step02b）是两条链路，**产物是否真被下游消费必须逐 provider 验证**——「生成了」≠「用上了」。

### P14. 双层降采样 720P 瓶颈（08-15 确认 → 配置化）
- **现象**：1080P 首帧图 → wan2.7 按 `BAILIAN_VIDEO_RESOLUTION=720P` 出片（一次降采样）→ step04 硬编码 `scale=720:1280` 再压（第二次）；动作镜头细节发糊。
- **根因**：`prompt_builder.py` 的 `size="720x1280"` 是死参数（bailian 不读 size，读 resolution）；step04 merge 硬编码 scale 双重降采样。
- **解决**：`MERGE_RESOLUTION`/`MERGE_PRESET`/`MERGE_CRF`/`MERGE_AUDIO_MODE`/`MERGE_AUDIO_LOUDNORM` 全部配置化（shared/config.py）；调试期保持 720P 省钱，上线切 1080P（视频 0.6→1 元/秒）。
- **教训**：**「改了没反应」陷阱**——先确认参数是否真到达 provider（size vs resolution 双源）；分辨率要全链路（生成+合成）统一口径。

### P15. step04 合成层缺陷批（08-15 修复）
- **现象**：①字幕 PrimaryColour=OutlineColour=白色 → 亮场景不可读；②调色滤镜在字幕**之后**（vignette 压暗字幕区）；③无响度归一，只有固定 +5dB（放大底噪且不解决逐镜跳变）；④编码 ultrafast+CRF20 画质损失；⑤hook 704 宽 pad 出 8px 黑边烧进成片；⑥回退 happyhorse（无声）→ `[i:a]` 匹配不到 → **整章合并失败**。
- **根因**：合并层从未做过可读性/音质/鲁棒性审计；preset/CRF/分辨率全硬编码。
- **解决**：字幕黑边+微软雅黑+字号随分辨率缩放；filter 顺序改调色在前字幕最后；逐镜 loudnorm（EBU R128）替代固定增益；preset=medium/CRF=18 配置化；increase+crop 消黑边；无声片段 `anullsrc` 补轨。
- **教训**：合成层是成片的「最后一公里」，编码/字幕/音质/鲁棒性四个维度都要配置化+兜底。

### P16. step03 TTS 孤儿步骤（08-15 确认 → 保留为备选）
- **现象**：voice_registry 8 音色精心生成的 mp3 **不进成片**——step04 native 模式只保留视频原声；音色一致性只靠 prompt 文字描述；`reference_audio` 是 TODO。
- **根因**：架构改 native（视频模型自带语音）后删了 TTS 混音路径，但 step03 没跟着删。
- **解决**：`MERGE_AUDIO_MODE=tts_mix` 恢复可选混音（TTS 权重 1.0、原声压 0.6），对视频原声不满意时切换；默认仍 native。TTS 300 字拦腰截断改为按句末标点安全截断。
- **待办方向**：实现 `reference_audio` 通道（step03 产物变音色参考，一石二鸟）。
- **教训**：删掉下游消费者时要同步评估上游是否变孤儿（跑 step03 = 白烧额度）。

### P17. step01 检查器误报 ~50%（08-15 修复）
- **现象**：最新轮 62 条 WARN，逐条对照 script_result.json 核实约一半是检查器自身缺陷（详见 P17 子项）。
- **根因**：检查器正则/判定逻辑与 LLM 实际输出格式脱节 + 规则互相矛盾 + 阈值不符合连续动作物理性。
- **解决**（validation.py + prompts.py，回归全过）：
  - 威胁源朝向句式扩充（刺向/刀尖朝/举过头顶/即将…）+ 规则48 水下黑影豁免（V1/V2）
  - 锚点标点归一化补全（！？…等）——「哎呀！」带感叹号匹配失败真因（V3）
  - 克制情绪触发词移除「压抑/微红」（氛围词/物理红印误报，V4）
  - 色调过渡混合色调兼容——「冷蓝+暖橙对比」≠突变，纯暖↔纯冷才报（V5）
  - shot_count 缺失静默补齐；narration 拼接仅字段存在时比对（V6/V7）
  - 内容密度档位放宽——连贯动作链「推臂→弹腿→倒纵」不是 3 个独立动作（V8）
  - hook 豁免结果状态污染 + motion 首时间段内状态词放行（V9）
  - 全检查器 hook 双编号统一 `_shot_tag`（V10）
  - prompts.py SYSTEM_PROMPT 注入 41.1/24.1/28.1/22.5a 四条规则（让 LLM 一次写对）
- **效果**：62 → 2 条（剩 2 条人工核实为真命中：hook 微表情不足、镜头7 动作密度偏满）。
- **教训**：**校验器自己也会出错**；改完必须对真实 script_result.json 回归；误报率 >30% 的系统没人信（狼来了效应），高价值检查（威胁源朝向）测不准比没有更糟——先修可信度再谈自动化。

### P19. lf_desc 锚点校验缺失（08-15 修复）
- **现象**：锚点检查（威胁朝向/视线方位矛盾/服装色重述）只查 `ff_desc`，尾帧同样喂图片模型却零锚点校验——实测现有脚本 lf_desc 有 **22 条服装色缺口**（尾帧漂色根因之一）。
- **根因**：08-14 的 Plan A 锚点系统只覆盖「首帧」维度（ff_desc 是动作起点），漏掉尾帧；规则 13.7 也只针对 ff。
- **解决**：`_check_ff_desc_anchors` 重构出 `_check_frame_desc_anchors(text, label, ...)`，A/B/D 三类锚点对称套到 lf_desc（ff==lf 时不重复查；尺寸锚 C 仍 ff 专属，bank 注入兜底 lf）。
- **验证**：对现有 script_result.json 重跑，新增 22 条 lf 告警（均为真实缺口，交由定向修复轮处理）。
- **教训**：**锚点校验要覆盖所有喂给图片模型的描述字段**（首帧/尾帧/关键帧）——只修一半等于留一半漂色口子。

### P20. 视频 QC 单帧 + seed 不传 + 失败假象（08-15 修复）
- **现象**：①视频视觉 QC 只抽**中段 1 帧**（`_MID_FRAME_RATIO=0.5`），开头黑场/结尾淡出吞画面漏检；②`req.seed` 恒 None → 每次提交纯抽卡，QC 判坏重提交拿全新随机结果，好坏反复横跳；③step02b `Failed: N` 只统计 gather 抛出的 Exception——`_poll_video` 正常 `return False`（终态错误/QC 失败/超时）与 `_submit_video` 失败（return None）都没计入，坏镜头带 `Failed: 0` 假象流进 step04。
- **根因**：QC 设计时只考虑了中段代表性；seed 从未接线（wan2.7 文档支持 seed，白白没用）；失败计数用 Exception 判定而非 `_failed_videos` 注册表。
- **解决**：①`video_qc.py` 抽首/中/尾三帧（0.12/0.5/0.88），任一帧 critical 即判坏；②`prompt_builder._make_request` 用 `shot_index|ff|lf|prompt` MD5 派生固定 seed；③`gen_all_videos` 失败数以 `submit_poll._failed_videos` 为准，日志输出失败镜头明细；④QC 失败自动重提交（`VIDEO_QC_RESUBMIT=2`，相同 req=相同 seed 可复现）。
- **验证**：seed 确定性冒烟通过（同输入同输出、改 prompt 变 seed）；三帧比例断言通过；`py_compile` 全过。
- **教训**：①视觉 QC 的抽帧策略要覆盖「可能出问题」的时段，单帧中段是最乐观抽样；②生成模型该用 seed 的地方一定要用（可复现性是 A/B 测试和 QC-重试循环的前提）；③失败统计要认「失败注册表」而非「异常类型」——正常返回 False 也是失败。

### P21. 刺客弯刀幻觉重复道具（08-16 确认 → 当天修复）
- **现象**：`props_bank` 同时存在「刺客弯刀」(P06) 与「刺客匕首」(P09)，钩子镜 r2v 参考图把两件道具一起注入（`image_submit: ref[6] 源路径=...刺客弯刀\prop.png`）。但刺客武器从头到尾是匕首（shot 6/7/hook 全为「右手紧握寒光匕首」）。
- **根因**：①第 1 章原文**从未命名刺客武器**——只有「手中一道寒光一闪，恰如天边那轮弦月一般」，道具提取 LLM 顺着「弦月」意象推断出「弯刀」；②后来钩子武器锁定为匕首（P1/Plan A 系列），提取器改名新建「刺客匕首」；③`register_prop` 只按 **identifier 精确匹配**去重（「刺客弯刀」≠「刺客匕首」），两件武器并存、都生成 prop.png；④运行时 `_collect_prop_asset_refs`/r2v 按「道具名+consistent_terms」子串匹配，弯刀别名含「匕首」→ 钩子镜写匕首时两件参考图一起命中。
- **解决（双加固）**：①数据：删 props_bank.json「刺客弯刀」条目 + 删目录（代码里其它「弯刀」都是通用武器词正则，与资产无关）；②`register_prop` 新增 `_find_alias_conflict` 别名去重（identifier∈别名 或 去泛化后别名交集非空 → 并入既有、不新建；吸收时保留 canonical 外观/别名），`_ALIAS_GENERIC_TERMS` 停用表防 牛车/马车 共享「车」误并入；③`_collect_prop_asset_refs` 与 r2v 道具段改为「命中词子串嵌套过滤」——hit 是另一命中的子串视为同一件丢弃（「匕首」⊂「寒光匕首」→ 只留匕首），命中词相同保留先出现者。
- **验证**：S1 寒光匕首镜头只留匕首 / S2 老酒胜酒壶 / S3 酒壶+老酒同镜两件都留（不误杀）/ T1 弯刀并入匕首 canonical 不污染 / T2 牛车马车不误并入 / r2v 路径无 NameError。`py_compile` 全过。
- **残留**：当前 script_result.json 的 prop_assets 仍含「刺客弯刀」（只影响【道具锚定】文字块冗余，不影响参考图——参考图走 bank）。prop_assets 从 bank 回写（main.py:719），下轮 step01 跑一次自动干净；**勿手动改 script_result.json**（mtime 变更触发帧/视频缓存失效，白烧额度）。已生成的钩子帧/视频（shot 6/7）含旧弯刀参考，需 `--force` 重生成该镜。
- **教训**：bank 去重只认 identifier 精确匹配时，LLM 给同一物件换名=必出重复条目，别名重叠才是「同一件」的真实信号；且「一致性问题」要在写入端（bank）拦一次、读取端（匹配）兜一次，单层防线必漏。
