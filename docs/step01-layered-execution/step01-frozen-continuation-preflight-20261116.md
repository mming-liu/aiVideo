# Step01 冻结稿续验预检表（2026-11-16）

状态：**零调用预检完成**，等待人工确认后发起“1 次修复 + 1 次最终审计”。
本轮未发起任何真实模型调用，未改正式产物。

## 1. 冻结身份

| 项 | 值 |
|---|---|
| run_id | `20261116T003059-8920` |
| 隔离根 | `.dsh/step01_real_20261116/锦衣夜行/part_01_杀青州/chapter_001` |
| 恢复清单 | `snapshots/step01_resume_20261116_003848.json` |
| 恢复清单 SHA-256 | `01c87de3d9b679ed5f6d8240ccbdb370d6e6b8826ca4fa2a2b886757158efba7`（2,425 B） |
| 冻结候选 | `.dsh/step01_budget_frozen/20261116T003059-8920/frozen_script.json` |
| 冻结候选 SHA-256 | `932af1afd8652213e208c1c02d9f6a8f561c3c73b13c54d53efb2c5110a00767`（137,413 B） |
| 冻结候选 | **13 镜**，总时长 **87.13s** |
| 计划缓存 | `chapter_outline.cache.json`，SHA-256 `5c1bc5ef5b37da698d091c1477f3fc3fac0efc6da90cc4240af05ba7c0888471` |
| 原文 SHA-256 | `aba1adc22826c2834754af529b6235e3bd4a5428b59a45c10353f5483ce854c3` |
| 规则指纹 | `8050857f18c1877f` |
| 通道 | `agent_plan` / `deepseek-v4.1-flash` |
| 时长带 | 60–90s（目标 70–80s） |
| 审计复用 | `resume --dry-run`：`ok=true`、`audit_reusable=true`、`audit_issues=11` |

> 复用判定的真实原因：首次预检曾因 `PROMPT_SOURCE_FINGERPRINT` 变化被拒（该指纹哈希整个
> `main.py`，而 `source_identity` 又包含它）。处置：`HOOK_MAX_ATTEMPTS` 放
> `shared/config.py`、`main.py` 保持审计产物的字节状态；dry-run 已恢复
> `audit_reusable=true`。详细对账见
> [step01-real-run-report-fix-20261116.md](step01-real-run-report-fix-20261116.md) §3.1。

## 2. 首轮 blocking 摘要（13 条，与顺序契约无关）

| code | 条数 | 镜头 |
|---|---|---|
| `duplicate_injection` | 4 | 2,3,4,5 |
| `kf_segment_missing` | 3 | 1 |
| `kf_temporal_consistency` | 2 | 11,13 |
| `model_audit_FRAME_LAYOUT_MISSING_ENTITIES` | 1 | 1 |
| `model_audit_MISSING_REQUIRED_ACTOR` | 1 | 7 |
| `model_audit_CAUSAL_TRIGGER_MISSING` | 1 | 8 |
| `kf_event_window_mismatch` | 1 | 11 |

审计 `step01_audit.blocked.json` 自身 issues **11 条**；提交点 `blocking_count=13`
是合并确定性质量门后的集合。顺序契约读数：13/13 镜声明 `plan_refs`、台词首次出现序
**D1→D2→D3**、日志无 `lines_order_violation`。

## 3. 续验入口与上限

### 严格上限（推荐，精确 2 次 HTTP 尝试）

```powershell
$env:STEP01_TOTAL_BUDGET_SEC="1800"
$env:HOOK_ENABLED="0"
$env:HOOK_MAX_ATTEMPTS="0"
$env:TEXT_TRANSPORT_RETRIES="0"
$env:TEXT_TRANSPORT_CONNECT_RETRIES="0"
$env:AGENT_PLAN_TEXT_STREAM="1"
python -m step01_script_gen.resume `
  --manifest ".dsh\step01_real_20261116\锦衣夜行\part_01_杀青州\chapter_001\snapshots\step01_resume_20261116_003848.json" `
  --max-sends 2 --hook-max-attempts 0 --audit-repair-rounds 1 --no-hook
```

- **逻辑调用上限 2**：1 次定向修复 + 1 次最终审计（首轮审计复用，不重复）。
- **HTTP 尝试上限 2**：`TEXT_TRANSPORT_RETRIES=0` + `TEXT_TRANSPORT_CONNECT_RETRIES=0`
  ⇒ 每次调用 1 次尝试；发送边界 `--max-sends 2` 兜底。
- 时间预算 `1800s`（受控上限；617+180+600+60≈1457s，1800s 只是余量，不保证提交通过）。
- `HOOK_ENABLED=0` / `HOOK_MAX_ATTEMPTS=0`：跳过钩子，不重新生成正文/钩子。

### 容忍 1 次传输重试（上限 4 次尝试）

```powershell
$env:TEXT_TRANSPORT_RETRIES="1"
$env:TEXT_TRANSPORT_CONNECT_RETRIES="0"
python -m step01_script_gen.resume ... --max-sends 4 --hook-max-attempts 0 `
  --audit-repair-rounds 1 --no-hook
```

- 每次调用最多 2 次尝试（初次 + 1 次重试），2 次逻辑调用 ⇒ **最多 4 次 HTTP 尝试**。

## 4. 门禁

- 本轮已完成：运行器计数/台账/BLOCK 读取修正、钩子两层预算、离线单测、文档对账、
  冻结身份核验、恢复入口 dry-run。
- **未执行**：真实修复/审计调用。等待人工确认 §3 的命令与上限后，再发起那 1 轮
  “修复 + 最终审计”。
