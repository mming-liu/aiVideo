# awesome-seedance 非 step01 吸收计划

> 日期：2026-09-23  
> 状态：计划稿；本轮只写文档，不修改代码、配置、测试和生产数据。  
> 后续实施顺序以[step01 收敛与 awesome-seedance 吸收总计划](./step01-and-awesome-seedance-master-plan-2026-09-23.md)为准；本文保留非 step01 工作包的详细设计。  
> 背景对照：[awesome-seedance 与 aiVideo 功能对照及吸收计划](./awesome-seedance-comparison-adoption-plan-2026-09-22.md)  
> 外部来源：[LearnPrompt/awesome-seedance](https://github.com/LearnPrompt/awesome-seedance)

## 1. 目标与冻结边界

本计划在 step01 排查周期不确定的前提下，先吸收不需要改动分镜生成链的内容。目标是提升参考图语义、人工审核效率、视频提示词可观测性和真实生成评测能力，同时保证正在排查的 step01 输出完全不受影响。

### 1.1 step01 冻结范围

以下目录和文件在本计划中全部禁止修改：

- `step01_script_gen/**`
- `_text_gen/**`
- `shared/motion_segments.py`
- `shared/kf_time.py`
- `shared/semantic_barrier.py`
- `_text_gen/deepseek_schema.py`
- 所有 step01 prompt、schema、constraint、validation、timecode、repair 和 quality gate 逻辑
- `tests/step01_script_gen/**`
- 当前章节的 `script_result.json` 内容

step01 在本计划中的唯一角色是提供只读输入。任何工具都只能读取已有的 `script_result.json`，不得回写、补字段、归一化、重排镜头或修改 revision。

### 1.2 允许规划的范围

后续实施仅限：

- `step02_video_gen/**`
- `step02b_video_gen/**`
- `tests/step02b_video_gen/**`
- 独立的 `tools/**` 离线工具
- `docs/**` 与 `references/**`
- 章节目录中新生成的审核报告、联系表和 A/B 结果；不得覆盖原始帧、视频和分镜 JSON

### 1.3 生产配置边界

当前配置为：

```text
VIDEO_PROVIDER=volcengine
VIDEO_REFERENCE_MODE=image
VOLCENGINE_T2V_MODEL=doubao-seedance-1-0-pro-250528
VOLCENGINE_I2V_MODEL=doubao-seedance-1-0-pro-250528
```

因此：

- 当前生产路径只使用首帧/尾帧 image 模式；
- Seedance 1.0 不支持 `reference_image`，r2v 功能只能做离线代码准备和单元测试，不能改变生产默认值；
- 不为验证计划切换付费模型；
- 不修改 `.env.content` 或 `.env.keys`；
- 不调用生成 API，除非以后单独决定进入真实 A/B 阶段。

## 2. 本轮吸收内容

排除 step01 后，外部仓库仍有四类内容值得吸收。

| 工作包 | 外部启发 | 当前缺口 | 当前模式是否立即受益 | 优先级 |
|---|---|---|---|---:|
| A. 审核联系表 | Storyboard grid | 缺少整章 FF/KF/LF 总览 | 是 | P0 |
| B. 离线 prompt 基线与差异报告 | 模板与跨模型复测 | 缺少固定镜头的提交 prompt 快照 | 是 | P0 |
| C. r2v 参考图继承/排除 | Reference identity lock | 已有“取什么”，缺少“不能取什么” | 否，等 r2v | P1 |
| D. 视频级 A/B 评测协议 | Cross-model retests | 代码测试不能证明成片更好 | 需要生成预算 | P1 |
| E. step02 质量报告增强 | pitfalls 与复测 verdict | 报告缺少实验版本和人工评分关联 | 是 | P2 |

不在本计划中实施：动作因果链、镜内状态交接、对白表演链。这三项需要改变 step01 生成内容，继续保留在冻结清单中。

### 2.1 来自 step01 收敛复核目录 12 的上游阻塞

[step01 convergence review 第 12 节](./step01-convergence-fixes-review-2026-09-22.md#十二step02a-启动实测绑定-revision-之后仍被-4-条-model-audit-blocking-拒绝2026-09-23) 已证明：当前正式脚本即使结构化质量报告为 PASS，最终 semantic audit 仍有 4 条真实 blocking，`require_semantic_gate()` 会阻止 step02a 启动。

| 位置 | 已确认矛盾 | 对本计划的影响 |
|---|---|---|
| shot 1 LF | 描述称老农已离场，layout 仍标记在场 | 不能把该镜作为已验收的联系表或 A/B 样本 |
| shot 2 FF | 描述称钓杆、酒壶已脱手，props 仍标记持有 | 不能用该镜评估道具参考策略，否则上游矛盾会污染结论 |
| shot 5 LF | LF 应为落船结果态，layout pose 仍为跃起过程态 | 不能用该镜评估视频终态稳定性 |
| shot 7 LF | LF 出现耳光动作，motion 没有对应过程 | 不能用该镜评估动作可用性 |

这四条不是 step02 应自动修复的内容。step02 如果擅自选择其中一个字段覆盖另一个字段，会把上游事实冲突隐藏成看似正常的模型输入。

### 2.2 目录 12 的 A–D 选项纳入计划

| 选项 | 原方案 | 是否属于当前非 step01 范围 | 本计划决定 |
|---|---|---|---|
| A | 给 step02a gate 增加显式 opt-in 调试绕过，只生成 shot 0 的 5 帧 | 技术上属于 step02a | **暂不实施**。绕过真实 blocking 会形成第二套准入语义；只有以后明确批准一次性诊断生成时再单独评审 |
| B | 为四类矛盾补通用确定性约束，重跑 step01 | 否 | 冻结；进入 step01 待办，不在本计划实施 |
| C | 把 semantic audit 移到 step01 唯一提交点，并让 step01 自己 BLOCK | 否 | 冻结；它能修复审计时序，但不能解决 blocking 的修复责任 |
| D | B + C 一起实施 | 否 | **step01 解冻后的推荐根治方向**；当前只记录依赖，不设时间 |

冻结期采用额外的执行策略：

> **不绕过 gate，不启动新的 step02a 生图；A/B 和工具开发只使用已有合格产物、独立 fixture 或纯离线数据。**

这使当前非 step01 工作可以继续，但交付物必须区分：

- `diagnostic`：可以用现有脚本构造 prompt 或报告，但不代表可进入生产；
- `upstream_blocked`：上游 audit 未通过，禁止生成新图片或视频；
- `eligible`：revision、fingerprint、audit scope 和 blocking 检查全部通过，才允许进入真实 A/B。

### 2.3 ref_sheet 额度口径

目录 12 已确认 `ref_sheet_*.png` 是本地 PIL 拼接，不调用图片模型，不消耗生成额度。后续所有预算报告采用以下口径：

- API 图片消耗只统计新生成的 FF、KF、LF、资产图和 QC 重抽；
- `ref_sheet`、联系表和缩略图计入本地派生产物数量，不计入 API 图片张数；
- 缓存命中的角色、场景和道具资产计为 0 次 API 调用；
- 报告同时列出 `api_generated_images` 与 `local_derived_images`，禁止再用“输出 PNG 总数”估算费用。

目录 12 给出的 shot 0 当前口径为：FF + LF + 3 个 KF，共 5 个模型生成帧；ref_sheet 不增加额度。由于 semantic gate 仍阻塞，本计划只记录成本，不据此启动生成。

## 3. 工作包 A：FF/KF/LF 审核联系表

### 3.1 目的

把各镜头独立生成的首帧、关键帧和尾帧排列成一张审核图，使角色漂移、空间方向翻转、动作阶段缺失、构图重复和缺图问题能在生成视频前被人工发现。

它借鉴外部仓库的 Storyboard Grid，但只作为审核产物，不作为视频模型参考图。

来源：[Storyboard grid to video](https://github.com/LearnPrompt/awesome-seedance/blob/main/docs/templates/en/storyboard-grid-to-video.md)

### 3.2 未来实施文件

计划新增：

- `tools/build_storyboard_contact_sheet.py`
- `tests/tools/test_build_storyboard_contact_sheet.py`

计划输出：

- `{chapter_dir}/storyboard_contact_sheet.jpg`
- `{chapter_dir}/storyboard_contact_sheet.json`

不修改 `shared/paths.py`。工具直接通过现有 `chapter_dir()`、`vimax_shots_dir()` 和文件名常量读取路径，避免为了单个离线工具扩大全局路径 API。

### 3.3 输入规则

只读取：

- `script_result.json` 中镜头顺序、镜号和关键帧数量；
- `vimax_working/shots/<idx>/first_frame.png`；
- `vimax_working/shots/<idx>/keyframe_*.png`；
- `vimax_working/shots/<idx>/last_frame.png`。

禁止：

- 修改或重新保存原始帧；
- 自动补图；
- 调用图片或视频模型；
- 根据联系表反向修改 `script_result.json`；
- 将联系表加入 `reference_paths`。
- 为补齐缺图而绕过 step02a semantic gate。

### 3.4 版式

- 每个镜头一行；列顺序固定为 FF → KF0…KFn → LF；
- 每格等比例缩放并 letterbox，不裁掉人物、道具或边缘信息；
- 镜号、帧类型和源文件名写在画面外边框；
- 缺失帧使用醒目的灰色占位，并在 JSON 中记录 `missing`；
- 图像宽度超过上限时自动分页，输出 `storyboard_contact_sheet_01.jpg` 等；
- JSON 保存 sheet 页码、格子位置、源路径、图片尺寸和缺失状态；
- 不在画面内容内部绘制时间码、字幕或水印。

### 3.5 验收标准

- 输入文件 SHA1 在工具运行前后完全一致；
- 缺图不会被静默跳过；
- upstream gate 未通过时，联系表只能汇总已有图片并在 JSON 标记 `upstream_blocked`；
- 镜头顺序与 `script_result.json` 一致；
- 关键帧按数字序号排序，`keyframe_10` 不得排在 `keyframe_2` 前；
- 横竖图都不变形；
- 同样输入重复运行得到相同 JSON 和视觉排列；
- 工具失败时不删除或覆盖已有审核图，采用临时文件完成后原子替换。

### 3.6 收益与风险

预期收益高，生产风险极低。它不改变模型输入，只提升人工发现问题的速度。

主要风险是大章节图片过宽或占用内存。通过分页、缩略图和逐图关闭资源解决，不引入数据库或新服务。

## 4. 工作包 B：离线 prompt 基线与差异报告

### 4.1 目的

固定 step02 实际提交给视频模型的 prompt，使以后修改参考图说明、压缩策略或 provider 路由时，可以看到准确的 before/after，而不是比较 step01 的聚合 prompt。

### 4.2 未来实施文件

计划新增：

- `tools/bench_video_prompt_patterns.py`
- `tests/fixtures/video_prompt_benchmark.json`
- `tests/tools/test_bench_video_prompt_patterns.py`

计划输出：

- `{chapter_dir}/video_prompt_benchmark.json`
- 可选 `{chapter_dir}/video_prompt_benchmark.md`

### 4.3 fixture 设计

fixture 不重新生成 step01 内容。第一版从已经稳定落盘的镜头中复制最小必要字段，形成脱离章节绝对路径的只读样本。

每条至少包含：

```json
{
  "case_id": "two_character_reference_01",
  "purpose": "检查多人参考图用途和编号",
  "mode": "r2v",
  "shot": {},
  "characters": [],
  "reference_manifest": [],
  "expected_fragments": [],
  "forbidden_fragments": []
}
```

建议固定 6 类样本：

1. image 首帧；
2. image 首尾帧；
3. r2v 单角色 + 场景；
4. r2v 双角色，检查编号与属性隔离；
5. r2v 道具 + 空间物体；
6. 参考图数量接近模型上限，检查拼表和压缩。

### 4.4 报告内容

每个 case 输出：

- provider、模型、模式；
- 最终 prompt，而非中间 prompt；
- prompt 字符数和模型上限；
- 实际参考图顺序、类型、文件摘要；
- 命中的预期片段和意外片段；
- provider 压缩前后差异；
- 是否影响 image/text 模式；
- verdict：`PASS`、`WARN`、`BLOCK`。
- upstream gate 状态、audit revision、blocking 数量；静态 prompt 通过不能覆盖 upstream blocking。

### 4.5 验收标准

- 不导入或运行 step01；
- 不读取正在变化的临时 step01 内部结构；
- fixture 输入不被函数修改；
- 报告明确区分 `_build_seedance_concise_prompt()`、追加参考图用途之后的 prompt，以及 provider 最终压缩结果；
- 同一输入重复运行结果一致；
- 工具不调用外部 API；
- image/text/r2v 三种模式均有快照级回归。
- shot 1/2/5/7 四类已知矛盾作为“上游阻塞样本”保留，报告必须标记 `upstream_blocked`，不得标成可生成。

## 5. 工作包 C：r2v 参考图继承/排除策略

### 5.1 目的

现有 r2v 已能按真实提交顺序标注每张图的用途，但主要表达“从图中取什么”。外部模板证明，明确写出“不得从图中取什么”有助于减少背景、姿势、构图和灯光污染。

来源：[Reference image identity lock](https://github.com/LearnPrompt/awesome-seedance/blob/main/docs/templates/en/character-reference-lock.md)

### 5.2 未来实施边界

只计划修改：

- `step02_video_gen/prompt_builder.py`
- `tests/step02b_video_gen/**`

绝不修改：

- step01 角色 schema；
- step01 生成的 `characters`、`char_layout`、`frame_layouts`；
- 参考图收集优先级；
- `MODEL_REF_LIMITS`；
- image 模式首帧/尾帧语义；
- 当前 `.env.content` 默认模式。

### 5.3 策略矩阵

| 参考类型 | 继承 | 禁止继承 |
|---|---|---|
| 角色肖像 | 身份、五官、脸型、发型、服装、配饰 | 背景、姿势、构图、机位、原灯光、文字 |
| 场景 wide/near | 建筑与空间布局、季节、时间氛围、光线、色调 | 偶然人物、文字、临时道具、参考图边框 |
| 手持道具 | 材质、颜色、结构、尺寸、磨损 | 白底、展示台、手势、构图、文字 |
| 空间物体 | 外形、结构、绝对/相对尺度、与人物的空间关系 | 白底、展示角度、无关环境、文字 |
| 角色参考表 | 每个角色各自的身份与服装 | 表格布局、标签文字、分格边界、角色间属性交换 |
| 道具参考表 | 每件道具各自外观 | 表格布局、标签文字、分格边界、道具融合 |
| 未识别参考 | 只继承与本镜头明确匹配的主体特征 | 不得决定新人物、新场景、新动作 |

### 5.4 文案与长度控制

采用紧凑的类型模板，不为每张图重复大段通用说明。建议结构：

```text
【参考图规则】图N严格对应上传顺序；各图只控制指定属性，禁止跨角色/跨类别混用。
- 图1（角色杨旭）：取身份、五官、发型、服装；不取背景、姿势、构图、原光线、文字。
- 图2（场景渡口）：取空间布局、季节、光线、色调；不取偶然人物、文字、临时物件。
```

约束：

- image 模式继续使用首帧/尾帧时间语义，不能套用“不取姿势/构图”；
- r2v 规则必须在 provider 最终压缩后仍保留；
- 多角色时只追加一次“禁止角色间交换五官、服饰、持物”；
- 不写模型无法执行的冗长法务式否定列表；
- 若接近 prompt 上限，保留顺序为：图号与用途 > 正向继承 > 跨角色隔离 > 排除项细节。

### 5.5 测试矩阵

| 用例 | 必须证明 |
|---|---|
| 单角色肖像 | 有 inherit/exclude，角色名正确 |
| 双角色肖像 | 图号正确，禁止属性互换 |
| 场景 wide/near | 不继承偶然人物和文字 |
| 普通道具 | 白底和展示构图不进入视频 |
| 船/马车等空间物体 | 仍保留尺度锚点，不退化成普通道具 |
| ref sheet | 不继承拼表布局和标签 |
| 首帧/尾帧 image | prompt 与修改前一致 |
| text 模式 | 不出现虚假的参考图说明 |
| 缺失路径 | 图号按实际提交媒体连续编号 |
| 超限组合 | 原收集/拼表逻辑和数量上限不变 |
| 缓存 | 参考图字节变化仍使 hash 变化 |

### 5.6 启用门槛

代码即使准备完成，也保持休眠，直到满足：

1. 生产或验收模型支持 `reference_image`；
2. 模型能力表已由官方资料确认；
3. 在测试环境显式设置 `VIDEO_REFERENCE_MODE=r2v`；
4. 工作包 B 的静态 prompt 报告通过；
5. 工作包 D 的真实生成 A/B 达标。
6. `require_semantic_gate()` 对用于实验的 script、quality 和 audit 组合真实通过，不能使用调试绕过。

## 6. 工作包 D：视频级 A/B 评测协议

### 6.1 目的

借鉴外部仓库公开 reproduced/degraded/failed 的方法，建立本项目自己的证据链。测试重点是古风权谋和现有资产体系，不照搬 UGC、广告、宠物等外部案例。

来源：[awesome-seedance Cross-model retests](https://github.com/LearnPrompt/awesome-seedance#-cross-model-retests)

### 6.2 版本对照

- A：当前生产 prompt；
- B：只加入本工作包要验证的一项变化；
- 每轮只验证一个变量，例如“参考图 exclude 规则”；
- 输入分镜、帧图、参考图、时长、分辨率、provider 和模型保持一致；
- 不使用仍在变化的 step01 新输出作为 A/B 输入；
- 固定保存输入文件摘要，避免资产图变化造成伪结论。
- shot 1/2/5/7 在四类上游矛盾修复并重新审计前，不进入 A/B 样本池。

### 6.3 样本

首轮 r2v 建议 6 镜：

1. 单角色转头或遮挡；
2. 双角色同框；
3. 双角色交互并持不同道具；
4. 场景资产 + 角色肖像；
5. 白底道具资产；
6. 船/马车等空间物体。

最低成本轮：6 镜 × A/B = 12 条视频。若 2 个版本接近，仅对争议镜头各追加一次，不进行整批重复。

### 6.4 评分

每项 0–2 分：

| 指标 | 0 | 1 | 2 |
|---|---|---|---|
| 角色身份 | 明显变脸/串脸 | 轻微漂移 | 稳定 |
| 服装配饰 | 交换/丢失/突变 | 细节漂移 | 稳定 |
| 场景一致性 | 布局错误或换景 | 局部漂移 | 稳定 |
| 道具一致性 | 丢失、复制、融合 | 细节偏差 | 稳定 |
| 参考污染 | 带入白底/姿势/拼表 | 轻微污染 | 无污染 |
| 动作可用性 | 动作失败 | 可剪辑补救 | 可直接使用 |
| 剧情忠实 | 新增/改写事实 | 轻微偏离 | 忠实 |

额外记录：生成耗时、失败码、重试次数、输出是否有音轨、人工评审备注。

成本字段分开记录：

- `api_generated_images`：实际请求图片模型的次数；
- `api_generated_videos`：实际请求视频模型的次数；
- `qc_regenerations`：QC 导致的额外模型请求；
- `cache_hits`：复用资产或帧的次数；
- `local_derived_images`：ref_sheet、联系表、缩略图等本地 PIL 产物。

### 6.5 通过门槛

- B 组平均总分至少比 A 组高 15%；
- 至少 4/6 镜在“参考污染”或目标一致性指标上提高；
- B 组不能新增剧情忠实 0 分案例；
- 动作可用性不能明显下降；
- 生成失败率不能高于 A 组；
- 单次结果矛盾时标记 `inconclusive`，不直接上线。

### 6.6 结果保存

计划保存：

- `docs/seedance-r2v-ab-YYYY-MM-DD.md`
- `{chapter_dir}/experiments/<experiment_id>/manifest.json`
- A/B prompt、参考图清单、输入 SHA1、模型参数、输出路径和评分

不覆盖 `vimax_working/shots/<idx>/video.mp4`。实验视频进入独立目录，避免污染生产缓存和 step04 合并输入。

## 7. 工作包 E：step02 质量报告增强

### 7.1 目的

让静态 prompt 检查、真实视频 QC 和人工 A/B 评分可以关联到同一实验，而不改动 step01 质量门。

### 7.2 未来实施范围

计划只涉及：

- `step02_video_gen/video_prompt_quality.py`
- `step02_video_gen/video_qc.py`
- 独立 A/B 工具和对应 tests

不改变现有 `VIDEO_QC_ENABLED=0` 默认值，不把新的主观评分直接变成生产阻断条件。

### 7.3 建议新增的报告字段

- `experiment_id`
- `prompt_variant`
- `video_provider`
- `video_model`
- `reference_mode`
- `prompt_sha1`
- `reference_manifest`
- `reference_policy_version`
- `manual_score_status`
- `upstream_gate_status`
- `upstream_audit_revision`
- `upstream_blocking_count`
- `api_generated_images`
- `local_derived_images`

这些字段只进入 step02 报告和实验 manifest，不写回 `script_result.json`。

### 7.4 验收标准

- 没有 `experiment_id` 时，现有生产报告保持兼容；
- 老报告读取不报错；
- 报告能够追踪到最终 prompt 和参考图版本；
- 主观评分缺失时不误判 BLOCK；
- step02 报告不修改 script revision；
- VIDEO_QC 软失败策略保持不变。
- 上游 semantic gate 阻塞时，报告只能是 `upstream_blocked`，不能被本地 prompt 检查的 PASS 覆盖。

## 8. 执行顺序

| 顺序 | 工作包 | 是否影响生产 | 是否依赖 r2v | 预计开发量 | 退出条件 |
|---:|---|---|---|---:|---|
| 0 | 上游 gate 状态登记 | 否 | 否 | 0.25 天 | 明确 `diagnostic/upstream_blocked/eligible`，不提供绕过 |
| 1 | A. 审核联系表 | 否 | 否 | 0.5–1 天 | 原图不变、分页、缺图与 gate 标记测试通过 |
| 2 | B. 离线 prompt 基线 | 否 | 否 | 1 天 | 三模式快照稳定、无 API 调用、不会把静态 PASS 当生产准入 |
| 3 | C. r2v inherit/exclude | 当前不影响 | 是 | 1 天 | image/text 零变化，r2v 测试通过 |
| 4 | E. step02 报告增强 | 低，字段增量 | 否 | 0.5–1 天 | 老报告兼容、上游 blocking 可见、无新绕过 |
| 5 | D. 真实生成 A/B | 实验目录内 | 是 | 0.5 天工具 + 生成时间 | 上游 gate 真实通过，且达到 15% 门槛或判定不采用 |

推荐先完成 gate 状态登记、A 和 B。它们对当前 image 模式立即有用，且不改变任何模型输入。C 可以提前写好但保持休眠。D 必须同时满足：r2v 模型可用、上游 semantic gate 真实通过、单独批准生成预算。

## 9. 测试与验证总表

### 9.1 静态检查

- `python -m py_compile` 只覆盖新增工具和实际修改的 step02 文件；
- `git diff --check`；
- 确认 `git diff --name-only` 不含任何 step01 冻结文件；
- 搜索是否误改 `.env.content`、模型能力表或 path contract。
- 确认没有新增 semantic gate bypass、ignore、force 或 skip 开关。

### 9.2 定向测试

- 联系表工具测试；
- prompt benchmark 测试；
- r2v purpose list 测试；
- frame contract 测试；
- video prompt quality 测试；
- 参考图 hash 失效测试。

### 9.3 回归测试边界

本计划不运行或改写 step01 产物作为验证步骤。若全量 pytest 因工作区中正在排查的 step01 改动失败，应分别报告：

- 本计划定向测试结果；
- 既有 step01 失败结果；
- 两者是否存在调用关系。

不能为了让全量测试变绿而修改 step01。

## 10. 回退方案

| 工作包 | 回退方式 |
|---|---|
| 联系表 | 删除新增工具和审核产物；原始帧未修改 |
| 离线 benchmark | 删除工具、fixture 和报告；生产无变化 |
| r2v 规则 | 回退 `prompt_builder.py` 的策略文案；默认 image 模式未变 |
| step02 报告字段 | 删除新增可选字段；保留旧报告结构 |
| A/B 实验 | 删除独立实验目录；生产视频和缓存不受影响 |

step02a gate 不在本计划中回退或绕过。若上游仍阻塞，真实生成阶段保持未启动状态。

任何时候只要 `git diff --name-only` 出现 step01 冻结范围文件，本计划的实现就应停止并拆分改动。

## 11. 交付物

计划全部完成后应具备：

1. 一键生成的整章 FF/KF/LF 审核联系表；
2. image/text/r2v 三种模式的最终 prompt 基线报告；
3. r2v 角色、场景、道具和参考表的 inherit/exclude 策略；
4. 与生产目录隔离的 A/B 实验 manifest 和评分报告；
5. 能追踪 prompt、参考图和模型版本的 step02 质量报告；
6. 一份明确的采用/不采用结论，而不是仅凭外部案例决定上线。
7. 对目录 12 四条 blocking 的清晰上游状态，保证它们不会被下游静态 PASS 掩盖。
8. 区分 API 生成与本地 PIL 派生的准确额度报告。

## 12. 暂缓项

以下内容等 step01 排查完成后重新评审，不设置预计日期：

- `motion_desc` 动作因果链；
- 镜内相邻时间段状态交接；
- 对白后的视觉反应链；
- step01 prompt 规则；
- step01 constraint 或 validation；
- 分镜 schema 新字段；
- 任何会改变 script revision 的处理。
- 目录 12 选项 B、C、D 及四类 blocking 的根因修复。
- 目录 12 选项 A 的 step02a gate 调试绕过；除非以后明确批准一次性诊断，否则不实施。

暂缓不影响本计划 A–E 工作包。两条工作流通过“step01 输出只读、step02 实验产物独立”保持隔离。
