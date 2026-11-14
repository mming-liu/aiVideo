# dsh 下发交接区（项目内可见索引）

> 建立时间：2026-11-13
> 目的：任务书、回传结果、会话归属约定在项目内可见、可入库、可复核。

## 零、强制约定（用户早就明确过，不可绕过）

**下发给 dsh 的每一个会话，都必须创建/归属到「对应项目的工作区」。**
对本项目即 dsh 客户端里的 `C:\myFloders\git\aiVideo` 工作区分组，不允许落到
「未分组」或 `default-workspace` 等其它工作区。

标准下发命令（**不加 `--no-session`**）：

```bash
cd /c/myFloders/git/aiVideo && \
  "C:/Users/ee54/AppData/Local/Python/pythoncore-3.14-64/python.exe" \
  "C:/Users/ee54/.workbuddy/dsh_bridge/dsh_bridge.py" run \
    --file "C:/myFloders/git/aiVideo/docs/dsh-handoff/<任务书>.md" \
    --tag "<tag>" --permission-mode danger-full-access
```

- 默认路径 `resolve_default_session(cwd)` 会**复用 cwd 对应工作区内的可续跑会话**，
  会话天然就在正确工作区。这是标准做法。
- `--no-session` 的帮助文本明写「仅排障时使用」。2026-11-12/13 曾因固定会话被
  写句柄占用而连续 5 次改用它，导致 4 个会话落到「未分组」——**不得再作为常规下发方式**。
- `--no-register-workspace` / `--allow-unassigned` 同样仅排障用，默认都是关闭的。

### bridge 现在的三层保障（不靠调用方自觉）

`C:\Users\ee54\.workbuddy\dsh_bridge\dsh_bridge.py`：

1. **前置硬校验** `workspace_key_for(cwd)`：cwd 未登记为 dsh 工作区 → 直接拒绝下发
   （`[工作区校验失败]`），不再静默跑到「未分组」。
2. **运行期即时登记** `watch_and_register()`：会话事件一出现就登记进工作区，
   任务跑几十分钟期间客户端也能看到；中途崩溃/被 kill 也不会漏登记。
3. **收尾校验** `session_in_workspace()`：会话不在 cwd 工作区 → 补登记并复验，
   仍失败则 `[工作区校验失败]` 且非零退出，不满足约定就不算成功交付。

配套修正：
- `register_new_session()` 改为「写前重读最新快照并合并」，桌面端运行期间改过
  `workspace.json` 也不再放弃登记（这是 4 次漏登记的直接根因）。
- **禁止回退固定会话** `DEFAULT_SESSION_ID`：该 id 实测未登记在任何工作区，
  `resolve_default_session()` 找不到候选时改为返回 `None`（新建会话 + 登记）。
- 同名 tag 重跑先清旧 `.jsonl`，避免 watcher 读到上一次的 session 事件误登记。

## 一、为什么之前"找不到会话区"

| 内容 | 实际位置 | 是否在 aiVideo 工作区 |
|---|---|---|
| 下发任务书（原件） | `.pytest_tmp/dsh_tasks/`（隐藏 + `.gitignore` 忽略） | 在项目内，但不可见 |
| dsh 回传结果（原件） | 同上 | 在项目内，但不可见 |
| 运行日志 `.jsonl` / `.done` | `C:\Users\ee54\.workbuddy\dsh_bridge\runs\` | ❌ 项目外（用户主目录） |
| dsh 会话本体 / 工作区分组 | `C:\Users\ee54\.dsh\sessions\`、`storages\session_projcache\sessions\`、`storages\workspace.json` | ❌ 项目外（用户主目录） |
| WorkBuddy 客户端会话索引 | `C:\Users\ee54\.workbuddy\sessions\` | ❌ 项目外 |

两层原因叠加：① 任务书/日志都写在项目外或隐藏目录；② 会话**没有登记进
aiVideo 工作区分组**（cwd 虽是 aiVideo，客户端按工作区分组时看不到）。

## 二、已做的修复（2026-11-13）

1. 补登记 4 个漏登记会话进 aiVideo 工作区（`sessionIds` 38 → 42，原有全保留）：
   `a483d815…`(fix_known_20261112b)、`65f2a65e…`(seedance_improve_20261112)、
   `6a34644b…`(summary_20261112)、`ddade3a8…`(remaining_problems_20261113)。
2. 修 bridge 并发跳过登记缺陷 + 上述三层保障与配套修正。
3. 项目内可见交接区：本目录按项目「逐条白名单」约定在 `.gitignore` 放行；
   任务书、结果、决策汇总的副本都在这里。本地状态快照不入库。

> 备份（可回退）：
> `.pytest_tmp/dsh_tasks/workspace.json.bak_20261113_2058`（改动前）
> `.pytest_tmp/dsh_tasks/workspace.json.before_merge_20261113.json`（合并前）

## 三、以后在哪里看 / 以后怎么下发

- **本目录 md**：每次下发的任务书（副本）、dsh 回传结果、决策事项汇总。
- **决策事项总入口**：`下发任务决策事项汇总_20261113.md`（G1–G7、A/B/C 三类、
  停止条件、当前状态与代码证据）。
- **dsh 客户端会话列表**：打开 `C:\myFloders\git\aiVideo` 工作区分组即可看到会话；
  运行日志在 `C:\Users\ee54\.workbuddy\dsh_bridge\runs\<tag>.jsonl`。
- **后续下发约定**：任务书与结果同步放一份到本目录（不再只写 `.pytest_tmp/`），
  且按第零节的标准命令下发，由 bridge 自动保证工作区归属。

## 四、五份下发任务书

| 文件 | tag | 性质 |
|---|---|---|
| `fix_known_issues_20261112.md` | `fix_known_20261112b` | Step01 确定性检查器已知缺陷修复 |
| `seedance_prompt_improve_20261112.md` | `seedance_improve_20261112` | Seedance 官方提示词实践落地 |
| `summary_20261112.md` | `summary_20261112` | 只读总结（注：原任务书已被回传结果覆写） |
| `remaining_problems_20261113.md` | `remaining_problems_20261113` | 只读核查仍真实存在的问题 |
| `fix_confirmed_problems_20261113.md` | `fix_confirmed_20261113` | 修复已确认缺陷、排除决策项 |

## 五、三份 dsh 回传结果

- `fix_confirmed_problems_result_20261113.md` — §9 有 G1–G7「已停下待裁决」状态表
- `seedance_prompt_improve_result_20261112.md` — §5 有产品/费用决策建议
- `remaining_problems_result_20261113.md` — 有「文档自报 vs 代码实测」对照表

> 本目录只做交接索引与留档，不参与运行、不改变任何门禁语义。
