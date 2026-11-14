# Step C + Step D 执行记录（2026-11-14/15）

> 依据：《Post-R6 终版》§3 Step C/D。**零模型调用**（重签为离线重建）；
> 未改 `script_result.json` 字节，未发起任何生成/审计/QC 请求。
> Step D 的 G3（参考清单先行）**未合入**，见 §4。

## 一、Step C：Step 01 主流程通用化

### C1 C1–C7 预检并入主质量门（同一实现，不是两份规则）

- 判据实现从 `tools/script_preflight.py` 迁到 `shared/script_preflight.py`；
  `tools/script_preflight.py` 只保留 CLI 并 re-export 判据（既有测试/调用不受影响）。
- `step01_script_gen/quality_issues.collect_structured_issues` 直接调用
  `shared.script_preflight.run()`，把五类判据转成主门 `Issue`：
  `layout_presence_gap` / `duplicate_injection`（**hard**，确定性结构事实）与
  `location_drift` / `stage_progress_mismatch` / `image_prompt_leak`（**advisory**，
  先可见不阻断；升级为 blocking 需另做跨内容校准）。
- `RULE_SOURCE_FINGERPRINT` 覆盖新增判据源：
  `shared/script_preflight.py`、`shared/prompt_contradiction.py`、
  `shared/prompt_contradiction_lexicon.py`；`RULE_VERSION` 与
  `shared/quality_gate.QUALITY_RULE_VERSION` 同步升到 **2026-11-14.2**。
- 判据异常按 `CHECK_FAILED`（hard）拒收，不把「没检测」当「没问题」。

**指纹迁移（零调用重签）**：

| 项 | 重签前 | 重签后 |
|---|---|---|
| `rule_version` | 2026-11-14.1 | **2026-11-14.2** |
| `rule_source_fingerprint` | `d3ab095ffa4c501a` | **`fdbc4c5bf56cdefa`** |
| `script_result.json` sha256 | `3273672c5ce8fd1a…` | **同左（字节未变）** |
| quality | PASS / hard 0 / visual 0 / advisory 78 | PASS / hard 0 / visual 0 / **advisory 83** |
| audit | ok（旧指纹） | ok，`reverified_from_fingerprint=d3ab095ffa4c501a` |
| commit / adoption | COMMITTED / 旧指纹 | COMMITTED / 新指纹，`gate_refresh.model_sends=0` |

命令与结果：

```
python tools/production_context.py --receipt <chapter>/step01_adoption.json --refresh-gate
# exit 0；written = quality/audit/commit/adoption；model_sends = 0
# commit.gate_refresh.at = 2026-11-15T00:14:20+08:00
# verify_step01_commit_manifest(...) -> COMMITTED
```

重签前四份产物备份在 `.dsh/step_c/pre_refresh/`（哈希见运行日志）。

### C2 隔离审计的显式只读基线

- `step01_script_gen/main.py` 新增 `_baseline_chapter_dir()`：
  `STEP01_BASELINE_CHAPTER_DIR` 显式声明时只读该目录；未声明才用 `CHAPTER_DIR`。
- `_committed_baseline_script()` 从该目录读 `script_result.json` + `step01_commit.json`
  并走 `verify_step01_commit_manifest`；不可用返回 None（回退全量审计）。
- `tools/audit_frozen_candidate.py` 支持包内 `baseline_chapter_dir`（相对路径按仓库
  根解析，不存在则 `baseline_invalid` 零发送拒收）。
- 单测：显式声明后**不得**回落到 `CHAPTER_DIR`（`test_step01_commit.py`）。

### C3 合跑 flake：按原组合两序复跑，**0 失败**

```
python -m pytest tests/shared tests/step01_script_gen/test_repair_effects.py \
  tests/step01_script_gen/test_audit_adjudication.py \
  tests/step01_script_gen/test_audit_entry_subprocess.py
# 1616 passed / 1 skipped
# 反向顺序（3 个文件在前）同样 1616 passed / 1 skipped
```

当前 HEAD + 本包改动下**未复现**计划所述 17 条时序/状态污染；因此本轮没有
"为修 flake 而改夹具"。先前 14 条 `CONFIRM_REQUIRED` 是本机删除审计门的环境
拦截（设 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR` 后全绿），不是用例失败。
若后续在别的机器/顺序复现，再按失败清单定位模块级全局状态。

## 二、Step D：Step 02a 锁图保护 + 预检哈希同源

### D1 受保护帧防删（`shared/vimax_bridge.py`）

- 新增 `_frame_is_protected(shot, path, frame_kind, kf_index)`：
  - 镜头目录标记：`.lock_frame`（整镜）、`.lock_frame_<kind>`、
    `.lock_frame_keyframe_<i>`；
  - `STEP02A_PROTECTED_FRAMES`：`<shot>:<kind>[:<kf>]` 或路径 glob。
- 新增 `_del_frame_unless_protected(...)`：受保护帧即使 QC critical 也**只记录
  sidecar 告警、不删原图/哈希**；未保护帧保持 B7 删帧重抽。
- 已替换全部 9 处直接 `_del_frame_and_hash` 调用（首帧/尾帧/关键帧的缓存命中
  分支 + 生成后 critical 分支）；`_maybe_qc_frame` 因 `qc_call_failed` 返回 False
  时本就保留原图与哈希，未改语义。
- 单测 `tests/step02a_frame_gen/test_frame_protection.py`：**8 passed**
  （标记文件、按帧类型、env 镜头/类型、glob、受保护不删、未保护删、dry_run 零写入、
  `_plan_frame_state` 不写参考表）。

### D2 `--plan-only` 与生成链同源（`shared/ref_compose.py` / `step02a_frame_gen/`）

- `fit_refs(..., dry_run=True)`：只预测**内容寻址**参考表路径，不建目录、不拼图、
  不写 `ref_compose_*.json`；非 dry-run 行为不变。
- `_plan_frame_state`：
  - 首帧 = `_collect_frame_refs_for_shot(return_buckets=True)` + `fit_refs(tag="ff", dry_run=True)`；
  - 尾帧 = 首帧整图 + 分类 buckets + `fit_refs(tag="shot<idx>_lf", dry_run=True)`；
  - 与生成链的 buckets/顺序/内容寻址路径一致。
- `execution_plan._frame_refs` 也改为 dry-run，不再在构建计划时写参考表/报告。
- `step02a --plan-only` 日志上下文钉到临时目录（`_bridge_log` 不再追加正式
  `step02.log`），结束恢复。

**实测（真实生产章，`--frames ff,lf --shots 1,2 --plan-only`）**：

```
exit=0
PRODUCTION_UNCHANGED=true   # 章目录全部文件 name/size/mtime 前后一致
```

**帧状态读数**（按当前生成链口径）：

| 帧 | 状态 | 说明 |
|---|---|---|
| shot 1 FF | **cached** | 哈希与内容一致 |
| shot 2 FF | **cached** | 保护哈希 `06ab04ab252884f8`，字节未变 |
| shot 1 LF | **generate** | 现有哈希与当前 `lf_desc`/参考图字节不一致（R6 改稿后未重生成） |
| shot 2 LF | **generate** | 现有参考表 tag 与当前生成链不同（`lf` vs `shot2_lf`），属旧产物 |

结论：U7 的"shot 1 LF 已 cached"假设**不成立**；U7 需把 shot 1 LF（以及 shot 2 LF，
若策略需要）纳入生成批次，预算按实际 `generate` 数量计。

### D3 G3（参考清单先行）：**未合入**

`step02_video_gen/submit_poll.build_frozen_for_shot` 仍是「先 `_build_prompt`、
再 `_make_request` 定参考」；隔离样本（`tools/build_s06_g3_isolated_diff.py`）
已验证「先定参考清单、再写绑定」的可行性，但正式合入需要改
`prompt_builder` 的正文构建契约（正文要逐项声明 `参考图k=身份/用途`），并同步
`prepare_request` 后的绑定保持校验与全量视频请求回归。该改造涉及 Step 02b
正文语义，本轮未在无完整回归的情况下硬接；保留为下一工作项。

## 三、回归读数（本机，零真实调用）

| 范围 | 读数 |
|---|---|
| `test_script_preflight.py`（含主门同源用例） | 28 passed |
| `test_step01_commit.py`（含基线显式声明） | 46 passed |
| `test_quality_report_determinism / test_production_context` | 40 passed |
| `test_frame_protection.py` | 8 passed |
| `test_step02a_offline_fixups / test_u3_reference_identity / test_plan_only_isolation` | 59 passed / 1 skipped |
| 上述 C/D 相关合并跑 | **153 passed / 1 skipped** |
| `tests/shared` + 3 个 step01 文件（两序） | **1616 passed / 1 skipped**（各） |

## 四、未完成 / 已知缺口

1. **G3 未合入**（见 D3）；Step 02b 正文仍无法按最终参考清单声明 `参考图k`。
2. **shot 1 LF / shot 2 LF 为 generate**（见 D2 表），U7 需按实际数量生成。
3. `tests/shared` 合跑 17 条 flake **未复现**，未做夹具修改；需在复现环境再定位。
4. Step E（G6 声音两态）未开始；Step F 需按批次单独确认费用。
5. 本轮未重跑全量 `tests/step01_script_gen`；C/D 按受影响入口分组验证。
