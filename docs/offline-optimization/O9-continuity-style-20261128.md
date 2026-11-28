# O9 跨章连续性记忆与 global 资产画风硬隔离（2026-11-28）

> 承接 O8（`014db15`）。`RULE_SOURCE_FINGERPRINT` 不变（`4fba3d45a6851f41`）；
> 零模型调用；正式 `output/` 只读（仅内存审计，不落盘）。

## 1. 资产库画风隔离（`shared/character_bank.py`）

### 落地的字段与判定

- `ASSET_CONTRACT_VERSION = 2`；
- 角色条目与每个肖像视图新增 `video_style` + `asset_contract_version`；
- 画风锚点（`StyleBank.lock`）同时写 `video_style` + `asset_contract_version`，
  并钉住**风格纪元** `style_stamp`；
- 解析按当前 `VIDEO_STYLE` 过滤，原因四态：
  `ok` / `style_mismatch` / `legacy_in_epoch` / `legacy_unstamped`（+ `style_unknown`）。

### 历史资产怎么处理（关键取舍）

生产 `global/` 里的 7 个角色与锚点**都没有画风标记**。若直接按「缺字段即拒收」，
当前书会被一次性判死（要重生成全部肖像，既费钱也不是本包意图）。因此用**风格纪元**：

1. 首次在某个画风下运行本代码，把该画风记进 bank 的 `meta.style_stamp`（随下次
   `save()` 落盘；`load()` 保持只读）；
2. 缺标记的旧资产在**纪元内**视为可用（`legacy_in_epoch`，原因如实记账）；
3. `VIDEO_STYLE` 一旦换成别的画风：缺标记资产变成 `legacy_unstamped` **拒收**，
   带标记但画风不符的变成 `style_mismatch` **拒收**；
4. 逃生开关 `ASSET_STYLE_STRICT=0` 把拒收降级为「可用但原因如实记录」。

### 真实数据只读审计（本机现网）

```
style=gufeng  epoch=gufeng  strict=True
杨旭/听香/张十三/冯西辉/安立桐/刘旭/叫化子  7 个角色 → ok, legacy_in_epoch, 各 5 个视图
style_anchor.png → ok, legacy_in_epoch
```

审计前后 `verify_baseline.py` 均 `ok:true`（656 文件 / 401,740,834 字节，零写入）。

### 消费方

`get_all_portraits_as_registry()` 与 `get_anchor_path()` 原地变为画风感知（签名不变）：

- `shared/vimax_bridge.py:531/919`（画风锚点）、`:2151`（肖像注册表）；
- `step02a_frame_gen/prompt_builder.py:265`（肖像注册表）。

`StyleBank.get_anchor_for_style()` / `CharacterBank.resolve_portraits()` 提供逐条原因，
`style_audit()` 供报告与测试使用。

## 2. 跨章连续性（`shared/chapter_continuity.py` + `tools/chapter_continuity.py`）

- `extract_end_state(script, chapter=)`：从**末镜**确定性提取
  `chapter_end_state.json`（schema `chapter_end_state/1`）：在场角色 + 外观锚点
  （解析 prompt 里的 `[角色: 外观]`）、随身道具、场景/地点、时刻桶（夜/晨/昏/正午…）、
  色温、终态画面、来源（镜头数）；
- 同一份剧本两次提取**逐字段相同**（无时间戳/随机）；
- `compare_continuity(prev, script)`：对照新章**开场镜头**输出 advisory 告警：
  `character_absent_at_start` / `appearance_changed` / `scene_hard_jump` /
  `time_of_day_jump` / `props_missing_at_start`；**一律不阻断**；
- `tools/chapter_continuity.py`：
  `--script ... --out ...`（提取）与 `--prev ... --script ...`（核对，退出码 0/1/2）。

## 3. 测试

- `tests/shared/test_asset_style_isolation.py`（12 用例）：判定表、混放库过滤、
  历史资产纪元采纳、换画风后拒收、strict=0 降级、锚点隔离、落盘持久化；
- `tests/shared/test_chapter_continuity.py`（9 用例）：提取确定性与字段完整、空剧本
  降级、读写校验、整洁承接零告警、换装/缺席/场景/时间/道具五类告警、CLI 三条路径。

回归：`tests/shared` + `tests/step02a_frame_gen` + `tests/enhancements` = 2378 passed /
15 failed / 5 skipped，15 项失败与 O0 基线**完全一致**（3 个 frame_spec/roster +
6 个 image_qc_retry + 6 个 enhancements），0 新增。

## 4. 残余

1. **Step01 规划入口未接 `--prev-chapter-state`**：`step01_script_gen/main.py` 没有
   argparse（配置/环境驱动），把终态注入提示词需要改提示模板并做真实 Step01 回归
   （本包零调用约束下做不了）。当前形态：`tools/chapter_continuity.py` 产出
   `chapter_end_state.json` + 告警，供规划前人工/脚本比对。
2. **告警是启发式的**：外观锚点按 `[角色: 描述]` 原样比较，同义改写会误报；
   所以只做 advisory，不做硬门。
3. `game`/`objects`/`scenes`/`props` 四个资产库目录仍是独立文件、未纳入 `video_style`
   标记（本包只覆盖角色肖像与画风锚点，计划点名的两处）。
