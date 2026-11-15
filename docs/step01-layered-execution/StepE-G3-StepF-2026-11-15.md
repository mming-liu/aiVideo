# Step E / Step D-G3 / Step F 收口记录（2026-11-15）

## 1. Step E：Step 03 `confirmed` 回写 + Step 04 准入门禁（零调用）

- 新增 `shared/voice_state.py`：章节声音状态两态（`prepared`/`confirmed`）。
  `confirmed` 需同时满足：绑定非空 `script_revision`、每条 required 音轨
  存在且 ≥ MIN_BYTES、ffprobe 时长 > 0、零 blocking；任何缺口一律
  `prepared` 并逐条落 `blocking` 原因。
- **Step 03**（`step03_tts_audio/main.py`）：真实 TTS 合成完成并通过校验后
  落盘 `voice_state.json`（含每条音轨 sha256/bytes/duration 与绑定
  revision）；`--plan-only` / candidate 分支保持 `prepared`（plan 报告
  附 `voice_state.json`，状态即 prepared，5 条音轨登记、未合成）。
- **Step 04**（`step04_merge/main.py`）：正式合成入口统一接线
  `_assert_voice_gate()`——`voice_required=True` 时要求 `confirmed` 且
  revision 与正式稿一致；`prepared`/缺失/漂移 fail-closed 拒收
  （`VoiceStateError`：`voice_state_missing` / `voice_state_not_confirmed`
  / `voice_state_revision_mismatch`）；`--dry-run` 只推演时间线，
  差异记入 `reasons` 不拒收。
- 单测：`tests/shared/test_voice_state.py`（构建/拒绝路径）、
  `tests/step04_merge/test_voice_state_gate.py`（prepared 拒收、confirmed
  放行、revision 失配拒收、dry_run 放行四路径）全绿。
- 两个既有 E2E（`test_merge_chain_wp5`、`test_step04_shots_subset_e2e`）
  按新门禁预置 confirmed 状态后放行——stub 片段无音轨 ⇒
  `voice_required=True` 是门禁**正确行为**（防静默成片）。

## 2. Step D-G3：参考清单先行合入（零调用）

- 新增 `step02_video_gen/ref_manifest.py`：在 `build_frozen_for_shot` /
  `prepare_request` 组装前先生成参考清单（`参考图k=身份` 绑定段），
  prepare 前后一致性可核对；消费 5967f7e 已校准的三态判据。
- `prompt_builder._make_request` 接线清单生成；`_video_gen/models.py`
  增加清单承载字段。
- 回归：`tests/step02_video_gen` 74 passed（exit=0）；
  `tests/step02b_video_gen` 全绿（13 条删除门配额误伤已分文件复跑排除）。

## 3. 本轮修复的两类接线问题（用户点名）

### C6 误判（`shared/script_preflight.py`）

- 旧口径把 `prompt/ff_desc/lf_desc/motion_desc` 四字段拼成一份文本跑
  重复块检测 → FF/LF 合法复用同一角色外观（首尾帧同人必须同貌）、
  ff 持械→lf 弃械等**合法时序推进**全部误判 hard。
- 根因：真实入口从不把四字段拼进同一份提交 prompt（两处合并点均为
  搜索探针）。修复：C6 改为**逐字段自检**（与 step02a 真实入口同一
  语义，字段内重复注入仍 hard）；跨字段整块搬运仍 hard 但豁免
  ff↔lf 合法复用。
- 正式稿回归 0 hard 不变；夹具包补 `legal_ff_lf_appearance_reuse`
  legal 对照用例并同步 FROZEN 摘要。C6 修复进入规则指纹 ⇒ 正式章
  门禁产物**零调用重签**（`--refresh-gate`）：PASS/0 hard/83 advisory
  与 issue 集合逐条一致，revision `0afa8074…` 不变，审计记录
  `reverified_from → to` 指纹链完整。

### dry-run 预检引用未生成参考拼表（`step02a_frame_gen/execution_plan.py`）

- 实锤：plan-only 报告把「真实生成时才由 `fit_refs` 拼出的预测拼表」
  标 `reference_unprepared` → 13 镜全 `blocked` → 02b
  `execution_requirements` 会以 `visual_plan_blocked` 拦真实请求。
- 修复：预测拼表标记 `materialized_at_execution`（执行期自物化），
  不计入 blocked；**真实源资产缺失仍 fail-closed**。
  `tests/step02a_frame_gen/test_plan_sheet_materialization.py` 2 passed；
  修复后正式章 plan-only：`reference_unprepared` 0 条、blocked 0 镜。

## 4. 锁帧保护补强（防覆写）

- `.lock_frame` 原先只拦「QC critical 删帧重抽」，不拦「哈希失配/
  `--force` 原位覆写」。补齐：FF/LF/KF 三条生成循环在重画前检查
  `_frame_is_protected`，锁保帧一律保留原图（sidecar 照记，交人工）。
- `_plan_frame_state` 同步锁口径：锁保帧报 `locked`（真实运行零 POST），
  plan-only 账单与真实行为一致。
- `tests/step02a_frame_gen/test_frame_protection.py` 12 passed。

## 5. Step F：锁保 + U7 全章 13 镜预检账单（零调用）

### 资产核验与锁保（零图片 POST）

| 帧 | 内容哈希（前16） | QC sidecar | 处置 |
|---|---|---|---|
| shots/1 FF | `23e1c4c31e480c1b` | legacy_unbound | **`.lock_frame_first_frame` 已写** |
| shots/1 LF | `64f99e07222ec4ab` | legacy_unbound（R4 期幻觉检测 `has_hallucination=false`） | **`.lock_frame_last_frame` 已写** |
| shots/2 FF | `06ab04ab252884f8` | **qc_passed**（绑定一致，R6 S06 采纳） | **`.lock_frame_first_frame` 已写** |

- 三帧哈希与生成链当前判据**完全一致**（shot2 FF 与 R6 采纳记录值
  相同）——Step D `fit_refs` 调整未造成真实哈希漂移，**无需改哈希文件**，
  也没有重复生成。
- 三帧 sidecar 为 legacy 格式（无 `image.sha256` 绑定）→ 五态为
  `qc_stale`。**零调用下不伪造 QC 绑定**：U7 真实运行会对这 3 帧自动
  补检（各 1 次 QC 调用、**零图片 POST**，锁保护栏保证不重画）。

### 02a plan-only 总表（`STEP02A_IMAGE_BUDGET_TOTAL=172`，账本 used=33）

- 运行：`python tools/production_context.py --receipt …/step01_adoption.json
  --run -- python step02a_frame_gen/main.py --frames ff,lf --plan-only`
  （不带 --force）→ exit 0，`production_unchanged=true`。

| shot | FF | LF | 说明 |
|---|---|---|---|
| 1 | **locked** | **locked** | 已验收+QC 通过，锁保零 POST |
| 2 | **locked** | generate | FF=R6 刚出图 QC passed；LF 待生成 |
| 3–13 | generate | generate | 全新 22 帧 |

- 待生成 `(shot, frame)`：`(2,LF)` + `(3..13,FF)` + `(3..13,LF)` = **23 帧
  图片 POST**。五态现状：shot2 FF `qc_passed`；shot1 FF/LF、shot2 LF
  `qc_stale`（legacy 未绑定）；shot3 FF `qc_not_run`（旧图无 sidecar）；
  其余 21 帧 `absent`。

### 批次拆分（供 U7 第 1 批授权审批）

| 批次 | 镜头 | 图片 POST | QC 次数上限 |
|---|---|---|---|
| 第 1 批 | shots 2–5 | 7（2LF + 3FF + 3LF） | 7 新帧 ×1 + rejected 重抽 ≤7；另 3 帧锁保补检 +3 |
| 第 2 批 | shots 6–9 | 8（4FF + 4LF） | 8 ×1 + 重抽 ≤8 |
| 第 3 批 | shots 10–13 | 8（4FF + 4LF） | 8 ×1 + 重抽 ≤8 |

- 全章合计：**23 POST**（账本 33→56，上限 172 余量充足）；
  QC 上限 23 + rejected 一轮 23 + 锁保补检 3 = **49 次上限**
  （QC 不占图片 POST 预算；`qc_rejected` 帧按护栏删帧重抽会再计 POST）。

### 02b plan-only（候选根 `_enhancement/stepF_precheck/video`）

- exit 0、`production_unchanged=true`、`network_calls=0`；
  ready=2（shot1/2 帧齐全，FF/LF sha256 已登记预检账本）、blocked=11
  （shots 3–13 帧未生成，登记前拦截）。进入 U8 视频生成前，请求组装链
  对已出图镜头完全闭合。

### 03 plan-only（候选根 `_enhancement/stepF_precheck/audio`）

- exit 0、零 provider 调用：5 个声音单元（对白 1 条 shot12、旁白 N1–N4）
  规划落 `speech_units.json`，`voice_preparation.json` status=blocked
  codes=`audio_units_missing`（plan-only 预期，非缺陷），章节声音状态
  `prepared`（5 条登记、未合成）。

## 6. 回归总账

- `tests/step02_video_gen`：74 passed（exit 0）。
- `tests/step02a_frame_gen` + `tests/step02b_video_gen`：全绿
  （13 条失败为本机删除审计门每进程 50 次配额误伤，分文件复跑全过；
  `test_media_plan_wp4` 另修一处写死 75.25 → 30–300s 兜底）。
- `tests/step03_tts_audio` + `tests/step04_merge`：全绿
  （修复 2 处 E2E 未预置 voice_state、1 处写死 75.25）。
- `tests/shared` + `tests/step01_script_gen`：5745+ 全绿
  （85 条中 84 条为删除门配额，分组复跑全过；1 条真实适配缺口——
  `test_audit_paired_progress` 的 mock issue 未声明 `entity_id`，
  G1 结构化关闭依据 (c) 无法成立。已按 G1 已批准语义补
  `entity_id="小厮"`：修复删掉布局幻影属独立于触达的确定性内容事实，
  4 passed）。

## 7. U7 前待办

1. **第 1 批真实生图授权**（shots 2–5，7 POST + QC ≤17 次）待人工审批。
2. 通道恢复后重跑 `.dsh/u6_closeout_20261110/r6_s07_shot2_qc.py`
   （只读 QC 补检，0 图片 POST）——429 限流仍未解封。
3. shot1 FF/LF 与 shot2 LF 的 legacy sidecar 将由 U7 运行期自动补检
   重绑（无需手工干预）。
