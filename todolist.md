# TODO 待办清单

> 记录**尚未完成**的修复、待验证项、待决策事项。
> 已解决问题与历史存档见 **[problems.md](problems.md)**；各版本演进细节原记于 `memory/`（v6.50→v6.54.1）。
> 最近更新：2026-10-01（补记 09-30 之后的现状与 plan carrier 复核；本清单正文的迭代条目仍是 2026-08-29 那次整版重写）

> ⚠️ **两条时效提醒（2026-10-01 核实；第 15 轮复核更正①）**：
> ① 本文引用的 `memory/*.md` 记忆留档**并未丢失**：它们在 **Claude 项目级记忆目录**
>    `~/.claude/projects/C--myFloders-git-aiVideo/memory/`（此前只查了仓库 / `git ls-files memory` / 用户级 `~/.claude/memory` 三处，
>    所以误判为缺失）。需要细节时按该路径找，或查 [docs/](docs) 与 [docs/archive/](docs/archive)。
> ② 08-29 之后 step01 的治理（BEST-GUARD、质量门、时序契约、**计划层契约**）不在本文，
>    见 [docs/step01-plan-carrier-contract-2026-09-30.md](docs/step01-plan-carrier-contract-2026-09-30.md) 等三份报告；
>    下面的「0.」条是 09-30 之后的实际卡点。

---

## 🔴 当前迭代必做

### 0. step01 计划层「过门」（所有下游读数的前置；2026-10-01 复核）
- **现状（一手证据）**：09-30 两章各跑一次，均停在 `plan_contract_invalid`（`plan_contract_version=4`，
  整跑 17.5s），**从未进入候选生成**；正式 `script_result.json` 仍是 09-24 08:11 那版，`output/` 下无 `final.mp4`。
- **真实失败形态**（已推翻 09-30 文档 §4 的旧描述）：**一条节拍承载多条声音条目**
  （ch1 `B2 载体：旁白1-5`、`B5 载体：台词1-2`；ch2 `B1 载体：旁白1` + 4 条事实全挂 B1）。
  探针穷举三种写法（区间/单值/多段）**全部失败**；但生成层本就支持「一镜多节拍」，故**判据可满足**，
  缺口在**提示没有多声示范** + **模型把「一镜多声」编码成「一节拍多声」**。
- **待裁决（三件，见 problems.md 同名节）**：
  - [ ] ① 提示层：给「N 条同类声音 ⇒ N 条节拍（可落同一镜）」的示例，并补 `shot_beats` 示例行漏掉的 `旁白k`
  - [ ] ② 规范化层（确定性兜底，幂等）：区间载体自动展开 + 事实侧「按键唯一」时重定位 + `hook_carrier` 越界按 `hook_spoken` 修正
  - [ ] ③ 契约版本与缓存：展开会改节拍编号 ⇒ `PLAN_CONTRACT_VERSION` 升 5、`plan_refs`/缓存同步
- **验收（离线可判）**：用 `chapter_001/002` 的两份 dump 做夹具，`plan_contract_problems` 由 9/6 条降到 **2/1** 条
  （剩下的 `画面`类错挂属语义，交 regen）；再跑一次应**零改动**（幂等）。
  探针：`python .tmp/_p1_carrier_probe.py` → `.tmp/_p1_carrier_probe_out.txt`（零模型调用）。
- **不做**：放宽「一节拍一载体」（承载关系唯一记录是有价值的不变量）。

### 1. 端到端完整基线（计划二步骤 7 欠账，所有 QC 改动的最终考场）
> ⚠️ **前置未满足（2026-10-01）**：step01 还停在计划层（见上条），所以这一条现在跑不起来。
> 另：`chapter_001` 现存 `clips/`（09-12）但**无 `vimax_working/`**，而 `CLIP_SOURCE=vimax` 指向
> `vimax_working/shots/<idx>/video.mp4` ⇒ step04 目前必然失败（成因待确认）。
- [ ] **step01 用新管线重跑**（验证 alias 折叠产线路径：char_layout/main_subject 入管线即折叠，而非依赖就地补数据）
- [ ] **step02a 全量**（当前只抽验过 hook/shot6；重点看前景动态镜 shot5/9/10 体型与人数）
- [ ] **step02b → step04 → final.mp4**（整链从未跑通，是 v6.50 以来的最大欠账）
- [ ] 跑完按各镜 `_qc_log.json` 抽验：QC 判定/修复尝试/重抽原因

### 2. 「刀已脱手」不可渲染描述（待决策）
- 现状：hook desc「刀已脱手尖朝下直指心口」**5/5 抽画成手持**——漂浮刀具超出模型能力，
  逐句对账上线后该句会永久 critical → 每次耗尽 QC_MAX_ATTEMPTS
- [ ] 三选一：改 desc 为可渲染形态（「持刀直指心口」）/ 建立可渲染性清单在 step01 源头避免 / 接受永久 attempts_exhausted（QC 留痕人工放行）

### 3. QC 长期命中率统计回填
- [ ] 跑量后回填命中率统计：edit 成功 / edit 失败回落重抽 / 直接重抽 三类计数
      （数据源：各镜 `*_qc_log.json`；原计划的 `memory` v6-53 留档**已缺失**，见本文件顶部时效提醒）
      —— 这是「收费模型先修后抽省钱」结论的实测依据

---

## 🟡 待决策 / 次优先

### 4. alias 体系收尾（v6.53 可选项 B.2）
- [ ] `character_bank.register_character` 接受 `aliases` 参数，同角色不同名（刺客/神秘人）bank 层合一
      （仿 `assets_bank._find_alias_conflict`；当前靠 identifier 别名链兜底，跨章换名会暴露）

### 5. KF 关键帧接入先修后抽（v6.54.2）
- [ ] 先修后抽当前只接 FF/LF；KF 生成路径仍是无脑重抽（`vimax_bridge` kf 循环）

### 6. QC 通用对账的已知盲区（记录在案，非紧急）
- 听香站位类偏差：位置锚（中舱边缘 vs 船尾）遵守率无机制覆盖——人数/动作能拦，站位不能
- 断言上限 10 条：复杂镜逐句断言会截断（关键锚优先，次要可能被挤掉）
- doodle 编辑副作用：刀具形态漂移（匕首→长剑）、构图软漂移——复检已兜底，漂移本身无抑制手段

### 7. prompt 瘦身重构（v6.50 P1 最后一项，未动）
- [ ] 发图 prompt 从「法条堆积」回到「画面描述」（目标 300-450 字，按 stage 动态选模板）
      —— 通用对账上线后 desc 的每句都会被 QC 核对，瘦身与对账天然互补

---

## 🟢 低优先 / 优化

- [ ] **skill 全量加载相关性筛选**：`_load_knowledge_skills()` 已合并 30→9 个，但仍全量加载
      ~7k token；按本章场景类型筛选或改目录式索引（prompts.py 审计遗留）
- [ ] 红果软护栏两项（判「暂不改」，记录在案）：镜数 13 偏多（红果建议 3-8，叙事密度合理）；
      人物名单 5>4（alias 折叠后 char_layout 已收敛，script characters 名单未收）
- [ ] 规则编号重排（prompts.py 3/3.5/3.6/3.7 混乱；08-21 决策暂不动，扩展时再考虑）
- [ ] hook 与 shot 校验「两套」是否统一（problems.md P10 遗留）

---

## 🗺 future roadmap

### C 方案 — 跨章按冲突重组分集（08-22 决策入档）
- 网文 1 章 ≠ 红果 1 集：密集章拆 2-3 集、水字数章合并；按冲突密度重组
- 三阶段：LLM 能力验证（extract_section_range + 冲突密度判断）→ 多集输出+路径改造 → 入口主键迁移
- 触发条件：A 方案跑通 2-3 章稳定 + 用户明确要求
- （详细设计与风险表见 git 历史版 TODOLIST.md；原 `memory redguo-60-90s-episode-standard.md` **已缺失**）

---

## 归档说明

- 08-22 前的已完成条目（v6.35 高潮优先 / Mode A unified / seedance 吸收 / hook 首帧验收 /
  QC prompt_semantics 失效 / kf 2→1 / 服装色收敛 / prompts.py 审计已完成项）已整体移除——
  均已解决或被 v6.50-v6.54.1 架构取代，历史见 problems.md 与 git 历史
- **v6.52-v6.54.1 三份计划（2026-08-29）全部实现**：参考图防泄漏/前景体型/船宽（v6.52）、
  QC 三层架构+alias（v6.53）、通用对账+先修后抽（v6.54/54.1）；
  演进细节原记于 `memory` 的 v6-52 / v6-53 / v6-54-1 三篇——**该目录已缺失**（见本文件顶部时效提醒）
---

## ✅ 2026-11-08：WP1–WP6 收口记录（详见 problems.md 同名节）

- **WP1 已完成**：全仓 `pytest tests` = `6334 passed / 281 skipped / 4 xfailed / 0 failed`；
  `.gitignore` 重复块导致的 allowlist 覆盖已修（`tests/shared`、`tests/step04_merge` 两处）。
- **WP2 已完成**：`58c1b6bd` 的 13 镜图片提示词告警 53 → 8；其中 45 条来自阈值重标定
  （4-gram ≥3→≥5、长度 800→1400），去重本身只省 56 字符、消除 0 条告警；剩 8 条为
  `prompt_facts` 判定的「正文提到、布局离屏」，保留评阅。
- **WP3 已完成（2026-11-08 收口）**：参考图组装、`*_qc_log.json` 落盘、doodle 先修后抽
  状态机已由 `tests/step02a_frame_gen/test_wp3_58c1b6bd_doodle_and_qc.py` 钉在 `58c1b6bd`
  真入口：13 镜 × 首尾帧 26 条 prompt 过矛盾自检、同船多角色参考图；首抽直过 / 二修复检
  通过 / 二修失败回落重抽三条状态链；主图与 `_repair` 两份 QC 尝试日志分开落盘。
  计划稿两处过期命名已按源码更正：多角色参考图接口是
  `_select_all_character_refs(shot, bank, characters_data, limit=2)`（不存在
  `_select_multi_character_references`）；QC 侧车是 `<stem>_hallucination.json`，
  `.qc.json` 为笔误。
- **WP5 已完成**：`step04_merge.main` 在章节副本里真跑通，产出 `final.mp4`，13 段桩片 +
  对白/旁白桩音轨 + `.voice` 签名侧车 + 7 条字幕 cue；时间线重叠后总时长 **71.88s**。
- **C4 终局（2026-11-08）**：冻结态 `python -m pytest tests` =
  `6363 passed / 281 skipped / 4 xfailed / 0 failed`。前两次冻结跑各暴露一处
  用例间顺序依赖（`NOVEL_CHAPTER` 泄漏、`video_prompt_quality.chapter_dir` 的
  import 期绑定），均为测试隔离修复、无生产语义变化；根因与修法见 problems.md
  同名节。正式产物复核仍为 `58c1b6bd…` / `COMMITTED` / 13 镜 / 0 blocking。

### 仍未完成
- [ ] **WP4 路径契约**：`<chapter>/vimax_working/shots/<idx>/video.mp4` 已由合并实测确认，
      但还没有独立断言把「合并读取路径 = step02b 写出路径」钉死。
- [ ] **WP6 归档**：本文件与 problems.md 的条目已补，交付样片目录与成本/返工账待补。
- [ ] **两份本地用例未入库**：`tests/step02a_frame_gen/test_dump_safety.py`、
      `test_gen_all_frames_parallel.py` 仍被 `.gitignore` 的
      `tests/step02a_frame_gen/*` 覆盖，只在本地跑（全量套件会执行）；是否入库待裁决。
- [ ] **WP7–WP12**：未启动；Step02b 每次付费调用需人工确认（`CLAUDE.md` 暂停条件 3）。
### WP6 交付样片（2026-11-08）
- 位置：`.probe_payload/wp6_delivery/`（`final.mp4` / `subtitles.srt` / `wp6_report.json`，不入库）
- 读数：时长 **71.875s**（时间线预期 71.88s）、1440×2560、视频+音频；对白轨仅 **shot 12**，
  旁白 N1..N4（`wp6_report.json` 的 `narration_units=[1,2,3,4]`）
- `final_sha256 = 233720fc07fe29da76db45a46af440bd723c10fa689af1a3459effbf6cb002f8`
  （2,577,434,247 字节；2026-11-08 用当前用例整跑复核，成片字节与上一次一致；
  旧记录的 `a27c79…` 与 `[2,3,4,8,12]` 是修正对白轨分类前的读数）
- 素材性质：**桩素材（纯色+噪声）、零模型调用、真实 FFmpeg 合成**；不代表真实画面质量
- 复现命令：`$env:WP6_DELIVERY_DIR="<dir>"; python -m pytest tests/step04_merge/test_merge_chain_wp5.py -q -p no:cacheprovider`
### 更正：对白轨与旁白轨的分类（2026-11-08 复核）

上一轮报告把 2/3/4/8/12 笼统写成对白轨镜头，不准确。按 voice_units、逐镜声音分流
与侧车逐条核对后：shot 12 才是对白轨（shot_needs_dialogue_track=True，含 .voice 侧车）；
2/3/4/8 是纯旁白镜，声音由图外旁白 N1..N4 承担，合并侧会告警并忽略多余的对白轨文件；
其余 8 镜无发声。上一轮为 2/3/4/8 写的 shot_N.mp3 属于多余产物。

### 零模型调用的证据范围（限定表述）

成立范围：tests/step04_merge 的验收运行。用例开场把 requests.post/get 与
Session.request 替换为抛错实现，覆盖导入期、缓存缺失与审计路径；这些运行未发出请求。
不成立范围：2026-11-08 上午探测隔离章节 step01 提交链时，直接调用
_commit_step01_outputs，实际发出 2 次 agent_plan 审计请求（14112 / 14183 字节，
sha256=cb21c338… / 175f816d…），两次均 BLOCK、未替换正式产物。
因此零调用只适用于网络阻断后的验收运行，不覆盖上述 2 次探测。