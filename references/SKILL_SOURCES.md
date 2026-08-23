# Skill 仓库来源索引

> 整包参考仓库位于仓库根 `reference_skills/`（**5 个独立 skill 仓库，未做内容同步**）。
> 本文件仅做发现/导航/对照，**零拷贝**——所有源内容以 `reference_skills/` 为唯一事实源。
> 标 ★ 的是与 aiVideo 流水线（step01-step04 + shared/）对应度最高、可直接借鉴的 skill。

---

## 1. spark-video ★★

**定位**：premise → screenplay → storyboard → render → review → mp4 完整生产 pipeline，自带 scripts/ 工具链（doctor / scaffold / render / stitch / providers），跨平台支持 Claude Code / Cursor / Qwen Code / Codex。
**入口**：`reference_skills/spark-video/README.md`（中文版 `README.zh.md`）

### 关键 sub-skill（6 个，全部在 `SKILL.md` 描述）
| Sub-skill | 路径 | 功能（1 句） |
|---|---|---|
| spark-video-producer | `references/spark-video-producer/SKILL.md` | 一键 producer：编排 5 个 sub-skill + 脚本（doctor.sh / scaffold.py / render_shot.py）端到端产出 mp4 |
| spark-video-screenwriter | `references/spark-video-screenwriter/SKILL.md` | premise → screenplay（多幕结构 + 角色 + 场景） |
| spark-video-director | `references/spark-video-director/SKILL.md` | screenplay → storyboard（镜号/运镜/对白/时长） |
| spark-video-vfx-review | `references/spark-video-vfx-review/SKILL.md` | 视觉特效走查清单（一致性/违和/重生成建议） |
| spark-video-clip-review | `references/spark-video-clip-review/SKILL.md` | 单镜/全片走查（音画同步/节奏/合规） |
| spark-video-cast | `references/spark-video-cast/SKILL.md` | 角色一致性管理（人物档案+跨集继承+换装） |

### 工具脚本
`scripts/doctor.sh --quick` 装前体检 / `scripts/scaffold.py` 项目脚手架 / `scripts/render_shot.py` 单镜渲染 / `scripts/stitch.py` 拼接 / `scripts/tts_qwen.py` 配音。

### 与 aiVideo 对照
- ✓ **可借鉴**：doctor 体检 + scaffold 项目脚手架（step01 缺）、producer 编排（aiVideo step01-step04 已拆，但缺统一编排）
- ✓ **已对齐**：r2v 模式、人物档案、storyboard 时间轴分镜
- ✗ **不需要**：自带的 render/stitch（aiVideo step02b/step04 已实现）

---

## 2. ShortVideoDirector ★

**定位**：5 个 AI 子代理协作（director / scriptwriter / storyboarder / creator / reviewer），Claude Code 主用 + Codex/OpenCode 兼容层，**44 个 SKILL.md** 是最大子集。
**入口**：`reference_skills/ShortVideoDirector/README.md`

### 核心 SKILL.md（按 aiVideo 阶段对照）
| 链路阶段 | ShortVideoDirector skill | 路径（取自 `skills/`，`.codex/skills/` 是 Codex 适配副本） |
|---|---|---|
| 短剧创作（编剧） | scriptwriter-script / scriptwriter-fix-script | `skills/scriptwriter-script/SKILL.md` |
| 短剧创作（导演） | director-outline / director-arc / director-plot-options | `skills/director-outline/SKILL.md` 等 |
| 短剧创作（分镜） | storyboarder-storyboard / storyboarder-fix-storyboard | `skills/storyboarder-storyboard/SKILL.md` |
| 短剧制作（分镜执行） | short-storyboard / short-fix-storyboard | `skills/short-storyboard/SKILL.md` |
| 短剧制作（视频） | short-video / generate-video / auto-video | `skills/short-video/SKILL.md` |
| 资产/图片 | creator-create-assets / creator-generate-images | `skills/creator-create-assets/SKILL.md` |
| 质量审阅 | director-review-novel / -script / -storyboard / -keyframes-visual / -keyframes-narrative | `skills/director-review-*/SKILL.md` |
| 系列续拍 | series-edit-story / series-repair-story / series-video | `skills/series-*/SKILL.md` |
| 兜底修复 | short-fix-outline / short-repair-story / short-input-confirm | `skills/short-fix-outline/SKILL.md` 等 |

### 与 aiVideo 对照
- ✓ **可借鉴**：director-review-* 系列的"多角色/多文件 review 流水线"思路（aiVideo 当前只有 step01 校验，没分多维审阅）
- ✓ **可借鉴**：creator-create-assets 资产创建→分镜的串行约束（aiVideo 已用 v7 场景/道具资产，但编排更简单）
- ✗ **不需要**：整套 Codex/OpenCode 兼容层（aiVideo 直接 Claude Code）

---

## 3. capsule-cinema ★

**定位**：把"一次跑通的方法"打包成可迁移的**视频配方（recipe）**——栏目结构/分镜规则/生成渠道/质量门/返工经验主题可换、流程不变。
**入口**：`reference_skills/capsule-cinema/README.md`（中文 `README.zh.md`）

### 关键子模块
- `skills/` — 配方库（capsules）
- `install_as_skill.sh` — 一键安装为 Claude/Codex/OpenCode skill
- `references/` — 配方模板 + 渠道适配器（provider）
- `openclaw.plugin.json` — 插件描述

### 与 aiVideo 对照
- ✓ **可借鉴**：**"配方打包"思想**——aiVideo 当前 step01 规则塞在 `prompts.py` + `validation.py`，未来可抽出「古风权谋/红果悬疑/...」等配方切换
- ✗ **不需要**：nodejs 实现（aiVideo Python only）

---

## 4. Seedance2-Storyboard-Generator

**定位**：Claude Code + Seedance 2.0 短剧分镜生成器，专注 Seedance 2.0 时间轴格式（0-3s / 3-6s / 6-9s / 9-12s / 12-15s）+ 视频延长做多集。
**入口**：`reference_skills/Seedance2-Storyboard-Generator/CLAUDE.md`（无独立 README，CLAUDE.md 即总览）

### 关键文件
- `.claude/skills/seedance-storyboard-generator/SKILL.md` — 主 skill
- `docs/structured-prompt.md` — Seedance 2.0 prompt 手册
- `docs/🎬 即梦 Seedance 2.0 使用手册.docx` — 官方平台手册
- 案例项目：`林冲项目/` `聂风项目/` `项链项目/` 等

### 与 aiVideo 对照
- ✓ **可借鉴**：资产编号 C01-C99 / S01-S99 / P01-P99 体系（aiVideo scene/prop bank 用名字 key，缺编号语义）
- ✓ **可借鉴**：视频延长（@视频1 接力）思路——aiVideo step02b 缺"上一镜接续"机制
- ✗ **不需要**：四幕结构（aiVideo 是单章分镜，不是多幕连续剧）

---

## 5. Micro-Drama-Skills

**定位**：25 集 × 30 秒短剧全自动生产系统（剧本+角色+6 宫格分镜+故事板+Seedance 提交），3 个核心 skill。
**入口**：`reference_skills/Micro-Drama-Skills/README.md`

### 关键 skill
| Skill | 路径 | 功能 |
|---|---|---|
| produce-anime | `.claude/skills/produce-anime/SKILL.md` | 端到端：剧本+角色+6 宫格分镜+故事板 |
| generate-media | `.claude/skills/generate-media/SKILL.md` | Gemini API 出图/出视频 |
| submit-anime-project | `.claude/skills/submit-anime-project/SKILL.md` | 批量提交 Seedance 任务 |

### 与 aiVideo 对照
- △ **借鉴度低**：Gemini 链路（aiVideo 主用 DeepSeek + Seedance/Seedream）；6 宫格分镜与 aiVideo 镜头分镜不同范式
- ✓ **可借鉴**：批量提交任务的状态轮询框架（已有 spark-video 覆盖）

---

## 维护约定

1. **`reference_skills/` 是唯一事实源**——本文件只做导航，不复制任何 SKILL.md 全文。
2. **新增 skill 仓库** → 在 `reference_skills/` 下新建子目录 + 在本文件补 1 节（5 字段：定位/入口/关键文件/与 aiVideo 对照/借鉴度）。
3. **aiVideo 流水线真的采纳某 skill 的能力** → 才允许把对应 SKILL.md 提炼成 `references/<name>.md` 单文件（已存在的 4 个 ai-* 指南就是这个路径）。
4. **借鉴度评级**：★★ 高（已对齐 + 可直接搬能力）/ ★ 中（思路可借，落地需重写）/ △ 低（栈不同，仅供浏览）。

## 当前对照速查

| 阶段 | aiVideo | spark-video 借鉴度 | ShortVideoDirector 借鉴度 |
|---|---|---|---|
| 文本→分镜 | step01_script_gen | ★★ doctor + scaffold | ★ 多角色 review 流水线 |
| 帧图片 | step02a_frame_gen | ★ render_shot | ★ creator-create-assets 串行 |
| 视频生成 | step02b_video_gen | ★★ providers + render | ★ short-video + auto-video |
| 审阅/修复 | step01 validation（warning-only） | ★★ vfx-review + clip-review | ★★ director-review-* 多维 |
| 编排/收口 | 无（手跑 step01-step04） | ★★ producer 一键编排 | — |
