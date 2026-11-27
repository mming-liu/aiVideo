# 清理清单（M07）— 2026-11-27

范围仅限 `downstream-safe-delivery` 任务创建/使用的对象。**不属于清理范围**：
主仓库 `output/`、`baselines/`、用户未跟踪文档、`.tmp/s14a`、`aiVideo-s14base`、
主仓库 `.tmp/phase2b_*` 及其他历史试验目录。

| # | 绝对路径 | 用途 | 可再生 | 删除前保留证据 | 删除条件 |
|---|---|---|---|---|---|
| 1 | `<worktree>/.tmp/fresh_pytest` | 早期 pytest 临时根 | 是 | 无唯一证据 | 相关进程结束 |
| 2 | `<worktree>/.tmp/s07-clean` | 旧干净副本 | 是（`git archive`） | 读数已记入账本 | 提交固定后可重建 |
| 3 | `<worktree>/.tmp/s07-debug` | S07 手工调试章节 | 是 | 调试结论已记入账本/证据 | 无未归档失败证据 |
| 4 | `<worktree>/.tmp/s04-real` | 真实第 1+2 章只读副本与审计脚本 | 复制件（来源：主仓库 `output/锦衣夜行/...`） | 审计读数已记入账本 | 确认原始来源仍在主仓库 `output/` |
| 5 | `<worktree>/.tmp/head.zip` / `verify.zip` / `m06_untracked.json` | 源码快照与基线记录 | 是 | `baseline.json` 已归档 | 提交固定后可重建 |
| 6 | `<worktree>/.pytest_tmp`、`.pytest_cache`、`.ruff_cache`、`**/__pycache__` | 测试/静态检查缓存 | 是 | 无需 | 进程结束 |
| 7 | `<worktree>/output` | 1 个文件：`锦衣夜行/part_01_杀青州/chapter_001/step02_events.json`（早期测试泄漏到默认 OUTPUT_BASE 的产物，非正式数据） | 是 | 已记录路径/大小；非正式产物 | 确认无未归档唯一证据 |
| 8 | `<worktree/>`（整个 worktree） | 本任务开发 worktree | 否（提交在 master 后不需要） | 源码已在 master；证据在 `docs/downstream-safe-delivery-evidence/2026-11-27/` | M05 合并完成、无未提交修改/进程 |
| 9 | 分支 `codex/downstream-safe-delivery` | 本任务分支 | 否 | 最终提交已在 master | `git branch -d` 成功 |
| 10 | `C:/myFloders/git/aiVideo/.tmp/downstream-safe-delivery-verify-482676b` | 固定提交干净副本 | 是（`git archive`） | 85 passed 读数已归档 | M05 合并完成 |

删除执行方式：PowerShell 原生命令 + `-LiteralPath`，删除前解析绝对路径并确认位于白名单；
递归前检查重解析点/符号链接；不跟随范围外链接；不使用 `cmd /c`；不使用 `--force`。

**保留**：证据目录（随 master 保留）、主仓库用户文件与正式产物、其他工作区。

## 执行结果（2026-11-27，M07）

| 对象 | 结果 |
|---|---|
| `.tmp/fresh_pytest`、`.tmp/s07-clean`、`.tmp/s07-debug`、`.tmp/s04-real`、`.tmp/head.zip`、`.tmp/verify.zip` | 已删除（s04-real 的原始章节仍在主仓库 `output/锦衣夜行/...`，已核对存在） |
| `.pytest_tmp`、`.pytest_cache`、`.ruff_cache`、各级 `__pycache__` | 已删除 |
| worktree `output/`（1 个测试泄漏文件 `step02_events.json`） | 已删除（非正式产物） |
| 本任务 worktree `C:/myFloders/git/aiVideo/.tmp/worktrees/downstream-safe-delivery` | `git worktree remove` 成功（未用 `--force`）；`.tmp/worktrees/` 空目录一并移除 |
| 分支 `codex/downstream-safe-delivery` | `git branch -d` 成功（marker: 已合并） |
| `C:/myFloders/git/aiVideo/.tmp/downstream-safe-delivery-verify-482676b` | 已删除 |
| `C:/myFloders/git/aiVideo/.tmp/downstream-safe-delivery-verify-631e269`（第三轮修复后的干净副本） | 已删除（62 passed 已归档） |
| 保留 | 主仓库 `output/`（3191 文件 / 989,104,390 字节，未变）、8 项用户未跟踪文件（SHA 未变）、`.tmp/s14a`、`aiVideo-s14base`、本证据目录、主仓库其他历史 `.tmp/phase2b_*` |

删除前逐项校验了绝对路径位于本任务 worktree 内、非符号链接/junction；未使用 `cmd /c`、
未递归删除主仓库 `.tmp` 整体、未对含用户工作的仓库执行 `reset`。
