# O6 Step03 音色分级与多章防撞（2026-11-28）

> 承接 O5（`ebda39f`）。`RULE_SOURCE_FINGERPRINT` 不变；零 provider 调用、正式产物零写入。

## 1. 现状核实（先查再改）

`VOICE_TIER_POLICY` / 分级 / 共享桶**骨架已存在**：

- `tts/voice_registry.py`：`primary` / `recurring` / `minor` / `extra` 四级与别名、
  `tier_policy()`（默认 `strict`）、`is_shared_tier()`、`character_voice_pool()`、
  `profile_for()`（独占）/ `shared_profile_for()`（新模型共享）/
  `get_shared_voice()`（Sambert 旧池共享）；
- `step03_tts_audio/shot_tts.py`：`derive_voice_tiers()` 在 strict 下把所有人压成
  `primary`，`resolve_voice()` 按 `is_shared_tier()` 分流到共享路径。

所以本包不改合同默认值，只修**真实缺陷**并补验收口径。

## 2. 修掉的两个真实缺陷（tiered 模式实际不可用）

`VOICE_TIER_POLICY=tiered` 下，同一 `(gender, age_range)` 桶的**第二个**龙套必然报
`VoicePoolExhaustedError`：

1. `shared_profile_for()`（新模型路径）用 `_used_speakers()` 判「桶内能否复用」，
   把**别的龙套**也算成了占用 → 桶音色永远「被占」；
2. `get_shared_voice()`（Sambert 旧池路径）用 `used_voice_ids` 当 blocked，而共享音色
   恰恰被记进 `used_voice_ids` → 同样自锁。

修法：新增 `_unique_used_speakers()`（只收独占角色/旁白），共享路径的 blocked 改为
「独占集合 − 共享集合 ∪ avoid」。语义不变的部分：独占角色仍不能占共享音色，旁白仍
对两者都独占。

## 3. 验收（17 用例，匿名 12 角色 / 两章规模）

`tests/step03_tts_audio/test_voice_tier_expansion_offline.py`：

| 场景 | 断言 |
| --- | --- |
| strict 默认 | `tier_policy()=="strict"`；非法值抛 `ValueError` |
| strict 撞池 | 7 条合法 speaker：旁白 + 6 独占占满，第 7 个角色 fail-closed |
| strict 下龙套 | 也走独占分配，池满同样拒收（不存在隐形共享） |
| tiered 共享 | 旁白 + 4 主角独占 + 8 龙套共享：**独占集合与共享集合交集为空**、旁白不被占、同桶复用（共享 speaker ≤ 2） |
| tiered 仍 fail-closed | 独占占满后，主角报错；此时共享桶也确实无可用（不降级、不静默撞人） |
| 跨章稳定 | 重新装载注册表后同一角色/龙套拿到同一 speaker 与 profile id |
| 音色池 | 角色池不含旁白音色 |

## 4. 工具口径

- `tools/voice_tier_audit.py`：报告新增 `voice_tier_policy` / `shared_tiers_enabled`，
  `union.role_pools` 逐角色标 `unique|shared`（按**当前合同**，strict 下历史 tier 不生效）。
- `tools/voice_unit_report.py`：报告新增 `voice_tier_policy` / `shared_tiers_enabled`，
  同一份 unit 表在两种合同下不再无法区分。

## 5. 未完成项（如实记账）

**扩容 provider 抽象未做。** 计划要求「规范化 `tts/voice_registry.py` 对 `qwen3_local` /
`voice_design` 扩容提供方的抽象接口（带离线校验清单，默认关闭，开启需显式配置）」。
当前 `character_voice_pool()` 明确写着「扩容通道未交付：不再从环境变量并入未核实的
音色」。做成抽象接口需要先定「哪些 speaker 可被扩容、如何离线校验合法性与性别/年龄
元数据」，以及 `VOICE_POOL_EXPANSION` 类配置与校验清单——属独立设计，本包未做。
在它完成之前，7 条内置音的池容量就是硬上限（tiered 只能让龙套共享，不能扩容）。

## 6. 门禁

`tests/step03_tts_audio` + `tests/tts` + `tests/tools/test_voice_unit_report_plan.py`
共 116 项：115 passed / 1 failed，失败项是 O0 基线里既有的
`test_load_report_plan_rejects_wrong_chapter_and_digest`（陈旧断言），0 新增失败。
