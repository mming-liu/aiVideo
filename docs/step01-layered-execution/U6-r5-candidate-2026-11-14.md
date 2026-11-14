# U6 R5 候选构建与 S07 防呆加固（2026-11-14，离线零调用）

> 状态：**候选已冻结、审计包已重建、`remaining=0` 待授权**。
> 本轮全程零外部调用（`tools.probe_isolation.block_external_transport` 包裹），
> 未触碰 `output/锦衣夜行/part_01_杀青州/chapter_001/` 任何正式产物，未发任何模型请求。

## 1. 身份与结论

| 项 | 值 |
| --- | --- |
| 源 | `r4/candidate.json`（`candidate_sha256=1d53a4f9…631688`，revision `eee8decc…c12e877`） |
| R5 候选 | `.dsh/u6_closeout_20261110/r5/candidate.json` |
| `script_revision` | `0afa80749139a03143f4b60c150b913cb9442d41c420e52c2370d966b7c03472` |
| `candidate_sha256` | `e6c0b9bc2006d4c537d285b24bd0a4fd6863f4b4b9f287dfd475bfa5e878e80a` |
| `quality_sha256` | `e9742a6c69fcc94e5222c4be28d65dd359164baed60f58838b21f823b24e6003` |
| 质量门 | **PASS** / hard 0 / visual_risk 0 / advisory 77 / video_advisory 11 |
| 规则指纹 | `db08353ea107b486`（`2026-09-23.4`），未变 |
| 镜头数 / 总时长 | 13 / **76.75 s**（全部时长字段逐字未动） |
| 相对 R4 变更镜 | 1, 5, 7, 8, 10, 11, 13 |
| shot 2 | **逐字不变**，FF 内容哈希仍 `06ab04ab252884f8`，LF `eadf511bb726762a` |
| 锁定字段 | 全 13 镜 `narration_segment` / `subtitle` 逐字不变 |
| 审计包 | `.dsh/u6_closeout_20261110/r5/u6_package/audit_package.json` |
| `binding_digest` | **`b3d99aece00678cad02d3300b89b26e2`** |

## 2. 修订清单（对应 S03 条目）

| # | 镜头 | S03 条目 | 改了什么 |
| --- | --- | --- | --- |
| A | 10 | **BLOCKING-1** `frame_layouts.kfs[1][0].pose` | 张十三 峰值帧（kfs[1]）从「收手怒视／收手」改为 **「反手扬掌扇击」／[扬手, 反手, 扇击]**；听香改 **「受击头甩向一侧」／[受击, 头甩一侧, 半边脸红肿]**；**补 杨旭 条目**（kfs[0]/kfs[2] 都有，唯独峰值帧漏人，出图会少一个在镜角色）。事后态「收手／怒视」仍保留在 kfs[2] 与 lf。 |
| B | 13 | **BLOCKING-2** + advisory-8（同源） | `keyframes[0].stage` `pre → approach`；4.5–6.5 s 段落从「硬切到别庄大门」**收束回同一场景**（湾堤岸边／堤岸官道），尾帧改「马车沿堤岸道路远去」，不切别庄大门；`location`/`scene` 不动；`prompt`/`lf_desc`/`motion_desc`/`context_bridge`/`core_keywords`/`camera_movement` 同步。 |
| C | 7 | advisory `KF_STAGE_MISMATCH` | `keyframes[0].stage` `pre → approach`（t=1.0 的 desc 已写「倒纵离船」）；按 lf_desc 把「酒壶」补进该镜杨旭的 `ff/lf/kfs[0]/kfs[1]` props（kfs[2] 原本已有）。 |
| D | 11 | advisory `STATE_CHAIN_CONFLICT` | `keyframes[2].desc` 的「双手垂落」→**「双手捂嘴」**；`kfs[2]` 与 `lf` 的听香 pose/states 一并对齐。 |
| E | 5 | advisory `PROMPT_OMISSION` + `PROP_CONTINUITY` | `prompt` 补听香跪坐惊惧 + 杨旭「钓杆／青瓷酒壶」；`frame_layouts` 杨旭 props 补 钓杆+青瓷酒壶（shot4 lf 有 → shot5 断链 → shot6 ff 又有，本处补断点）。 |
| F | 1 | advisory `AUDIO_CONTINUITY` + 运镜词泄漏 | `motion_desc`「笛声」→**「箫声」**（与 shot2 竹箫、本镜 `audio_cue`「箫音」一致）；`lf_desc`/`kf2`/`kf3` 删「推近」（image_prompt_quality 把「推近」判为 video-only 泄漏）。 |
| G | 1/5/8 | **S07 前置防呆**（R4 plan-only 已 `blocked`） | 4 处跨字段 **24 字重复注入**等义改写：shot5 `prompt`↔`lf_desc`、shot5 `ff_desc`↔`lf_desc`、shot5 `lf_desc`↔`motion_desc`、shot8 `lf_desc`↔`motion_desc`。 |

改后 step02a 画面计划预检：**单镜 `--shots 1` / `--shots 2` 与整章均为 `status=planned`、`prompt_preflight=ok`、`preflight problems=[]`**（R4 时 1/5/8 三镜 `blocked`，会在采纳后硬阻断 S07）。

## 3. 13 镜确定性规则普查（R4 / R5 对照）

单镜规则函数（`step01_script_gen/quality_issues.py`）逐镜跑 + 跨字段 24 字重复注入 + 图片提示词质量，共 11 类判据。

| 镜 | R4 命中 | R5 命中 | R5 明细 |
| --- | --- | --- | --- |
| 1 | 4（重复注入 1 + img-QA 3×「推近」） | **0** | — |
| 2 | 0 | 0 | — |
| 3 | 0 | 0 | — |
| 4 | 0 | 0 | — |
| 5 | 1（重复注入 1） | **0** | — |
| 6 | 0 | 0 | — |
| 7 | 0 | 0 | — |
| 8 | 4（重复注入 1 + img-QA 3） | 3 | `offscreen_entity_in_prompt`×3（**R4 既有，非本轮引入**） |
| 9 | 0 | 0 | — |
| 10 | 0 | 0 | — |
| 11 | 0 | 0 | — |
| 12 | 1（img-QA 1） | 1 | `offscreen_entity_in_prompt`×1（R4 既有） |
| 13 | 1（img-QA 1） | 1 | `ff_desc_full` repeated 4-char「车厢入口」（**R4 既有**） |
| 合计 | 11 | **5** | |

- **9 个硬规则族（action_time / state_order / pose_conflict / surface_conflict / onscreen_prop / prop_release_owner / kf_segment_missing / kf_timeline / kf_person_visibility）13 镜全 0 命中**。
- **跨字段重复注入全 13 镜 0 命中**（R4 有 3 镜）。
- 图片提示词告警 8 → 5，且剩余 5 条**全部是 R4 既有的非阻断项**（`offscreen_entity_in_prompt` 描述的是「画面外实体在提示词里被提到」，属提示词冗余提示，不进质量门）。
- 明细落盘：`r5/census.json`（含 R4/R5 两列）。

## 4. Step 02a `--plan-only` 读数（零发送）

夹具：独立 `OUTPUT_BASE=.dsh/…/r5/plan_only_base`（R5 candidate + quality + 合成 audit 占位 + COMMITTED manifest），`VIMAX_WORKING_DIR` **显式指向真实** `output/…/chapter_001/vimax_working`（只读），资产库注册表复制自真实 `output/锦衣夜行/global/*_bank`。

| 目标 | `shot_frames` | FF / LF 状态 | 场景缺口 | 道具缺口 | 真实图片总量 | `model_sends` | preflight | visual_plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `--shots 1 --frames` 默认 | **2** | `generate` / `generate` | 0（wide/near/side 全有） | 0 | **2** | 0 | ok | planned |
| `--shots 2` | **0** | `cached` / `cached` | 0 | 0 | **0** | 0 | ok | planned |

- shot 1 两张都判 `generate`：R5 改了 prompt/lf_desc（大远景空镜），旧图内容哈希必然不匹配 ⇒ 这不是「缓存失效」，是**预期重生成**。
- shot 2 两张都判 `cached`，`writes` 里**没有** `shots/2/first_frame.png`、`last_frame.png` ⇒ 实跑不会重画 shot 2。
- 夹具只复制了注册表 JSON、没复制资产图片目录，所以报告里的 `scene_assets=3` 是**夹具虚高**；真实资产缺口由 `real_env_asset_check` 独立复核（上表「场景/道具缺口」列）。该 caveat 已写入 `r5/plan_only_reading.json`。
- 读数落盘：`r5/plan_only_reading.json`、`r5/plan_only_shot_1.json`、`r5/plan_only_shot_2.json`。

## 5. S07 三项防呆（本节即 S07 执行约束，运行脚本必须照此实现）

### 5.1 shot 2 FF：**严禁**走 step02a

**原因（代码事实）**：`shared/vimax_bridge.py` 在 `_gen_first_frame_async` 的三条缓存命中分支上都是同一个模式（约 3932/3941/3947 行）：

```python
if await _maybe_qc_frame(shot, str(ff_path), "first_frame", characters, empty_scene):
    _del_frame_and_hash(ff_path, ff_hash_path, "首帧", idx, logger)
    continue
```

`_del_frame_and_hash`（1184 行，`unlink` 图片 + 哈希文件）判定的是**是否通过 QC**：缓存命中且 QC 判 critical ⇒ 删图重画。也就是说——**只要跑 step02a 的 `--shots 2`，一旦 QC 判 critical，`first_frame.png` 与 `frame_hash.txt` 会被物理删除，`06ab04ab252884f8` 这把唯一基线就没有了。**

**锁死规则**：
- 对 shot 2 FF 的 QC 核验**只允许**调用独立只读入口：
  `shared/image_qc.py::maybe_qc_sync(frame_path, shot, "first_frame", characters, empty_scene, prompt=...)`
  + `shared/image_qc.py::write_sidecar(frame_path, result, pre_digest=<当前图 sha256>, frame_meta=..., status=...)`；
- **严禁** `python -m step02a_frame_gen.main --shots 2 …`、**严禁**直接调 `_gen_first_frame_async` / `_ensure_first_frame` / `_del_frame_and_hash`；
- 只读入口不改图片：`maybe_qc_sync` 只读图 + 出 `QcResult`，`write_sidecar` 只写 sidecar（`first_frame_qc.json` 等），**0 次生图 POST**；
- shot 2 目录里的 QC 侧文件仍是旧式的 `first_frame_qc_log.json` / `first_frame_hallucination.json`，**没有**新式的 `first_frame_qc.json` sidecar ⇒ `qc_state` 仍是 `qc_stale/legacy_unbound`，重检本身就是这次 S07 要补的缺口（重检后应落 `qc_bound` 且 `pre_digest=4089f234ff475f7a…`）。

### 5.2 shot 1：不需要 `--force`，也**不许**用 `--force`

plan-only 已经证明 shot 1 FF/LF 都判 `generate`（哈希不匹配），**不加 `--force` 就会重画**。加 `--force` 的唯一额外效果是让缓存判据彻底失效——这恰好会连带把 shot 2 也推上重画路径（若误把 2 纳入选择集）。因此 S07 命令固定为：

```
python -m step02a_frame_gen.main --shots 1        # 不带 -f / --force，不带 kf
```

### 5.3 QC 与 POST 的计数口径

- **`qc_image_layered` 这个符号在仓库里不存在**（已 grep 确认）。「最多 5 次视觉 QC」是**预算记账约定**，不是代码常量：4 张新图各 1 次 + shot 2 FF 重检 1 次 = 5。
- 计数单位是 **QC 判定次数**，不是内部子请求数。`QC_PROVIDER=opencode` 时一次 `maybe_qc_sync` 内部会走「opencode 整体判定 + 第 1 层 grounding + 第 2 层二值问答」，合计 **2–3 次视觉子请求**；这些**不得**计入 5 次上限，否则预算会被凭空吃掉 2/3。
- **新发现（本轮必改）**：`.env.content` 里 `QC_MAX_ATTEMPTS=3`。而 `shared/vimax_bridge.py:3893/4105` 是 `max_retries = QC_MAX_ATTEMPTS - 1` ⇒ **实际每个目标最多 3 次生图 POST**，不是预算文件假设的 2 次。要守住「每目标 ≤2 次 POST」，S07 必须在进程环境里**显式 pin `QC_MAX_ATTEMPTS=2`**（`load_dotenv` 默认不覆盖已有 env，所以外层导出即可生效）。否则 shot1 两帧最坏可到 6 次 POST，40 张/172 张的口径全部作废。

## 6. S07 启动锁键（与 S03 同一套思路）

| 键 | 值 | 作用 |
| --- | --- | --- |
| `IMAGE_QC_REPAIR_ENABLED` | `0` | 关掉 QC critical 触发的 i2i 修图/doodle 二修（否则每图多 1 次 POST） |
| `QC_MAX_ATTEMPTS` | **`2`** | 每目标最多 1 次初抽 + 1 次重抽（**必须显式 pin，见 5.3**） |
| `STEP02A_CONCURRENCY` | `1` | 串行，便于逐张确认 |
| `HOOK_ENABLED` / `VOICE_ADAPTATION_ENABLED` | `0` / `0` | 与 R5 投影同口径 |
| 允许的生成命令 | 仅 `--shots 1`（无 `-f`） | 见 5.2 |
| 允许的 shot 2 动作 | 仅 `maybe_qc_sync` + `write_sidecar` | 见 5.1 |
| 图片台账 | `31/172 → 35/172` | `step02a_image_budget.json` |
| 停止条件 | 任一目标第 3 次 POST / 累计 >35 / QC 达 5 仍有新图未审 / shot2 FF 哈希变化 / shot2 LF 出现生成请求 | 与 `u6_image_qc_budget.json` 一致 |

## 7. 审计包与防呆实测（零调用）

| 测试 | 命令 | 结果 |
| --- | --- | --- |
| 绑定摘要 | `--print-binding-digest` | `b3d99aece00678cad02d3300b89b26e2`（endpoint `https://opencode.ai/inference/go/openai/v1/chat/completions`，`max_sends=1`） |
| dry-run | `--dry-run --out-root …/dryrun` | **`VALIDATED`**，`would_send=0`，revision/candidate_sha256 复算一致，`authorization.registered=false` |
| 未授权 Execute | 无 `STEP01_AUDIT_AUTHORIZED` | **exit 2**，`REFUSED / authorization_required`，`send_state=not_sent`，`would_send=0`，未创建 out-root |
| 第二层额度门 | `STEP01_AUDIT_AUTHORIZED=1` + `remaining=0` | **exit 2**，`REFUSED / budget_exhausted`（`authorized_total=1, used=0, remaining=0`），未创建 out-root |

## 8. 通用性：把「逐镜补丁」升级成「判据」

R5 的修订本身是**逐镜内容补丁**：它只保证这一章这一版不犯这 6 个错，下一个候选、下一章还会重犯。
所以同批补上了 `tools/script_preflight.py`——把这几类缺陷做成**确定性判据**，对任意
`script_result.json` 都能跑（零调用、只读、不写 `output/`）。

### 8.1 覆盖率矩阵

| 缺陷类 | S03 出处 | U6 之前的确定性判据 | 现在 |
| --- | --- | --- | --- |
| 跨字段 24 字重复注入 | shot1/5/8（R4 plan-only `blocked`） | **有**，但只在 step02a 预检阶段 | `C6 duplicate_injection`（hard，前移到脚本级） |
| 峰值帧漏掉在镜角色 | shot10 **BLOCKING-1** | 无（`kf_layout_count_mismatch` 只比数量，不比身份） | `C1 layout_presence_gap`（hard） |
| 镜内硬切到别的地点 | shot13 **BLOCKING-2** | 无 | `C4 location_drift` |
| `stage` 与动作进度不符 | shot13、shot7 | 无 | `C5 stage_progress_mismatch` |
| 音效/乐器与画面不一致 | shot1（笛 vs 箫） | 无 | `C3 entity_class_conflict` |
| 道具跨镜断链 | shot5、shot7 | 部分（`_onscreen_prop_issues` 只看单帧） | `C2 prop_chain_break` |
| 人物状态链语义断裂 | shot11（垂落↔捂嘴） | 部分（`_state_order_issues` 只看时序） | **仍交给 LLM**（见 8.4） |

### 8.2 判据口径（关键在「免报」）

每条判据都配了**证据白名单**，宁可漏报也不把正常叙事判成缺陷：

- **C1**：只在「在—不在—在」的中间帧且描述无离场证据时报；`kf0`/末帧缺席不报（叙事取舍正常）。
- **C2**：角色已出画 → 不报（道具记账无意义）；描述里仍提到该道具 → 不报（只是 layout 少写）；
  跨镜只在「上一镜有→本镜无→下一镜又有」的**断了又回来**形态报 advisory，
  单纯「本镜没再用」降为 `info` 且不计入告警数。
- **C3**：只在同一镜的**音效**与**画面字段**取到不同乐器时报警（同类多值本身不算错）。
- **C4**：必须同时命中「切换动词 + 地点后缀 + 与 `location` 无共同字」才报。
- **C5**：只对 `stage=pre` + 完成态词（已/完成/结束/之后）报。

### 8.3 验证（同一套判据，三个语料）

| 语料 | hard | advisory | info | 说明 |
| --- | --- | --- | --- | --- |
| **R4**（缺陷候选） | **4** | 13 | 3 | 3×重复注入 + shot10 漏人；advisory 精确覆盖 shot1 乐器、shot5 断链、shot13 地点与 stage |
| **R5**（本轮修复后） | **0** | 5 | 3 | 5 条全是既有 image-prompt 告警（shot8/12/13），无新增 |
| 历史快照 ×3 | 3–4 | 8–12 | 3 | hard 全部是重复注入（同一缺陷类，真实存在）；无新增类别误报 |

结论：**判据在「有缺陷的 R4」上抓得到、在「修好的 R5」上不误报、在历史语料上只抓同一类真问题**——
这是一条可复现的通用防线，而不是针对本镜的手写断言。

### 8.4 为什么 shot11 那类不硬做

「双手垂落 → 双手捂嘴」是**姿态语义互斥**，要做成确定性判据得先有一张姿态本体/互斥表
（手部位置的取值域、组合约束），否则「垂落→抬起」这类正常动作会被大量误判。
在拿到姿态词表之前，这类交给审计模型（它已经抓到过一次）比塞一条高误报规则更划算。
`stage` 与动作**进度**的语义匹配同理（C5 只覆盖「完成态词」这个可判的子集）。

### 8.5 不改变规则指纹（重要）

`tools/` 不在 `RULE_SOURCE_FINGERPRINT` 的纳入清单里（清单只含 `step01_script_gen/*`、
`constraints/`、`prompt_semantic/`、若干 `shared/*`）。实测指纹仍是 **`db08353ea107b486`**，
因此 **R5 的 `binding_digest`（`b3d99aec…`）不受影响，待授权状态不变**。

若要把这些判据变成**提交门禁**（而不是自查工具），就必须把判据落进 `quality_issues.py`
或 `constraints/`——那会改指纹、让所有已采纳报告与 R5 审计包失效，需要按 R6 重新冻结并重新授权。
这一步等用户裁决，本轮不动。

### 8.6 回归数据做成夹具（按 AGENTS.md「内容无关性与夹具」）

第一版把用例写成**测试内联的合成最小分镜**，并且直接用了作品里的人物名——那既违反
内容无关性（`FORBIDDEN_WORK_TERMS` 口径），也让「加一个案例」必须改测试代码。
2026-11-14 晚按项目既有夹具制（`fixtures/audit_narrative_families` + `narrative_family_builder.py`
那套）重构为三层：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 数据 | `tests/step01_script_gen/fixtures/script_preflight/packs/sample_packs.json` | 9 个 pack / 18 个 case：人物、地点、道具、镜头编号、`patches` 变异、每例预期判据 |
| 机制 | `tests/step01_script_gen/script_preflight_builder.py` | 展开 `frame_layouts`/`keyframes`、套用字段级变异、校验内容无关性与「只改声明字段」 |
| 消费 | `tests/step01_script_gen/test_script_preflight.py` | 参数化跑每个 `(pack, case)`、锁 pack 摘要、钉住「每族必须有 legal + defect 两侧」 |

要点：

- **基线先合法**：每个 case 的起点是 pack 基线，legal 变体必须零 finding；
  defect 变体只能改自己声明的字段，构造器逐镜比对顶层字段，多改一个字段即报错。
- **每族必须有对照侧**：只有 legal 没有 defect（或反之）的族等于没校准阈值，测试直接红。
- **换小说/换章节 = 换或加 pack**，构造器、消费测试、工具代码都不改。
- 夹具只读：`FROZEN` 里锁 pack 的规范化 sha256 前缀（`0d804ed69a02517c`）。

**夹具制当场抓到一个真 bug**：`pf.run()` 会经 `check_image_prompt` →
`collect_image_prompt_quality` → `attach_gate_metadata` → `ensure_script_revision`
给**调用方传入的 script 补写 `script_revision`**。生产质量门依赖这个「补写」，
所以不去改它；但预检是只读入口，现在改为传 `copy.deepcopy(script)`，
`test_run_does_not_mutate_script` 钉住这一条。没有这条用例，同一份 script 在
预检前后 `revision` 会漂移，而调用方完全看不到。

回归：`python -m pytest tests/step01_script_gen/test_script_preflight.py -q` → **27 passed**
（18 个 pack case + 9 条结构/不变量用例）。真实语料读数与重构前逐字一致
（R4 hard 4 / advisory 13 / info 3，R5 hard 0 / advisory 5 / info 3）。

### 8.7 内容无关性：判据、正向、修复、审核四侧一起落到夹具

8.6 只解决了「新判据没有夹具」。但真正要回答的问题是：**这些判据是不是只对《锦衣夜行》
成立**。按 AGENTS.md「内容无关性与夹具」，分三层查了一遍，并把缺的补齐。

**第一层：判据代码（生产侧）——已有门禁**
`test_content_agnostic_guard.py`（G05）扫 `step01_script_gen/**`、`shared/**` 的**代码常量**
（跳过 docstring）与提示模板/技能 md，禁 8 个作品名 + 12 个第一章专属剧情短语。
本轮核验通过，未改动。

**第二层：测试语料——原来没有门禁，本轮补上**
新增 `tests/_content_binding.py`（词表单点定义）+ `tests/test_content_binding_ratchet.py`
（棘轮）+ `tests/fixtures/content_binding_baseline.json`（快照）：

| 规则 | 含义 |
| --- | --- |
| 未在基线里的文件必须为 0 | 新夹具一律内容无关，想豁免必须显式申报 |
| 已列出的文件不得高于基线 | 只减不增 |
| 清零的条目必须从基线删掉 | 防止「降了但基线没动」的假账 |
| `historical` 段必须写理由（≥12 字） | 历史抓取件可以留作品词，但不许静默豁免 |
| `PARAMETERIZED_CORPORA` 六套语料必须全 0 | 受控语料是「换书可用」的前提 |
| 本模块词表必须覆盖 G05 的词 | 一边收紧、另一边漏放行就是洞 |

实测账：`tests/` 下 **236 个文件、9852 处**仍含作品词（其中 23 个是申报过的历史抓取件），
其余 213 个是散落在各处单元测试里的内联造数。棘轮把它们钉住：**只能往下走**。

**第三层：三个域的实际状态**

| 域 | 之前的回归数据 | 现在 |
| --- | --- | --- |
| **正向**（不报） | `script_preflight` 内联写死作品词 | 9 pack / 18 case，全匿名（甲/乙/丙） |
| **正向**（矛盾自检） | `test_prompt_contradiction.py` 全篇 08-26 现场原文（杨旭/听香/照月湾） | 5 pack / 17 case，holder 用 `甲某/乙某`（满足判据的 2–5 字约束），旧文件已删 |
| **修复** | 只有「缺陷存在」的断言 | 4 pack：**缺陷 → 授权修复 → 复查全绿**，并用生产的 `repair_fields.path_allowed` 判「修复没越权」；另加整份改名后裁决不变的通用性用例 |
| **审核** | — | **本来就是内容无关的**：`fixtures/step01_audit_min/` 是匿名包（甲乙丙），`test_audit_entry_subprocess.py` 已有拒收矩阵（未授权/预算耗尽/摘要不符/规则身份不符/运行根复用/正式目录…）。本轮核验确认，未改 |

> 更正一处先前的误判：上一轮按「同名测试文件」盘点时把 `tools/audit_frozen_candidate.py`
> 判成「零回归」，实际是 `test_audit_entry_subprocess.py`（623 行）在测它——名字不含
> 工具名而已。同理 `verify_*` 系列大多有回归。真正的零回归脚本是 4 个：
> `diagnose_video_gaps` / `p4_admission_check` / `reparse_final_audit` / `workspace_size_audit`。

**落地清单与实测**

| 文件 | 作用 |
| --- | --- |
| `tests/_content_binding.py` | 作品词单点定义 + 扫描器 + 声明文件豁免 |
| `tests/fixtures/content_binding_baseline.json` | 236 文件 / 9852 处的快照（23 条历史抓取件带理由） |
| `tests/test_content_binding_ratchet.py` | 6 条：新增即红、超基线即红、清零要删条目、受控语料全 0、历史件必须写理由、词表必须覆盖 G05 |
| `tests/shared/fixtures/prompt_contradiction/packs.json` + `test_prompt_contradiction_contract.py` | 5 pack / 17 case，旧内联用例删除 |
| `tests/step01_script_gen/fixtures/repair_loop/packs.json` + `test_repair_loop_contract.py` | 4 pack：基线全绿 → 缺陷复现 → 修复全绿 → 不越权 → 改名不变 |
| `tests/step01_script_gen/fixtures/script_preflight/*` + `test_script_preflight.py` | 9 pack / 18 case（8.6） |

全量：`python -m pytest tests/test_content_binding_ratchet.py tests/shared/test_prompt_contradiction_contract.py tests/step01_script_gen/test_{script_preflight,repair_loop_contract,content_agnostic_guard}.py tests/tools -q`
→ **180 passed / 0 skipped**。

**一处附带发现（未修，先记账）**：`tests/shared` 与 `test_repair_effects` +
`test_audit_adjudication` + `test_audit_entry_subprocess` 三个 step01 文件**同时**跑时，
有 17 条时序敏感用例（QC 并发、attempts exhausted、commit 路径）会红；拆成任意两组都全绿
（`tests/shared` 单独 1439 passed，加 `test_repair_effects` 1513 passed，
加 `test_audit_entry_subprocess` 1480 passed）。这是套件隔离/并发负载问题，与本次改动无关
（那一跑里没有本轮的任何一个新文件），但值得单独立项。

**一个必须说清的边界**：`shared/prompt_contradiction.py::_check_ff_lf_stage_conflict`
的 `injury_anchor` 词表（「伤处白袍」「一片暗红血迹洇开」…）是**从真实样本里提炼的
本书专用措辞**。它不在 G05 的 TERMS 里，所以门禁放行；但它确实是内容耦合——换一本
没有「白袍/伤处」描写的书，这条判据就不会触发。把它改成可配置词表（或从
`step01` 的伤情状态词表派生）会改 `RULE_SOURCE_FINGERPRINT`，**因此必须按 R6 重新冻结**，
本轮不动。这是目前唯一已知的「判据本身绑内容」的点。

## 9. 待授权（下一步唯一动作）

R5 包内 `budget.remaining=0`、`new_authorization` 为空。要发起**新的 1 次隔离审计**，需要在包内填入：

- `authorization_id`（不得复用 R1–R4 已用过的任何值）、`granted_by`、`granted_at`；
- `remaining = 1`；
- `binding_digest = b3d99aece00678cad02d3300b89b26e2`（逐字保留，改任何一个字节都会变）。

未收到该授权前**不发任何请求**。

送审前置（新增，已满足）：`python tools/script_preflight.py <candidate> --fail-on hard`
必须为 0 —— R5 实测 `hard 0 / advisory 5 / info 3`（5 条 advisory 全是既有 image-prompt 告警）。
后续任何候选（R6、下一章）在打包送审前都跑同一条命令，不再靠「人眼盯着这一版的几个镜头」。
