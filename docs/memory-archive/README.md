# 记忆归档（历史留档，非现状依据）

> 建立时间：2026-11-14
> 触发：用户指出「aiVideo 项目的记忆文件绝大部分已过时、不对」，要求统一收进项目里，
> 且后续不再往 memory 写项目记忆。

## 这个目录是什么

把散落在**用户主目录**和**隐藏目录**里的 aiVideo 项目记忆，按原文全量复制到项目内，
作为可追溯的历史留档。复制，不改写内容、不删原件（原件清理需用户另行确认）。

| 归档子目录 | 原始位置 | 文件数 | 说明 |
|---|---|---:|---|
| `workbuddy-workspace/` | `C:\myFloders\git\aiVideo\.workbuddy\memory\` | 2 | 2026-11-12 / 11-13 两份工作区日志 |
| `claude-project-memory/` | `C:\Users\ee54\.claude\projects\C--myFloders-git-aiVideo\memory\` | 254 | 39 个活动记忆 + `MEMORY.md` 索引 + `_baseline-2026-10-23.md` + `.archive/` 215 个历史件 |

完整逐文件清单（路径、字节、原修改时间）见 [MANIFEST.md](MANIFEST.md)；
清理清单与备份校验见 [CLEANUP-CANDIDATES.md](CLEANUP-CANDIDATES.md)
（原件已于 2026-11-14 经用户确认后移入回收站，可恢复）。

## ⚠️ 时效声明（重要）

**本目录全部内容均为历史原文，逐条时效未重新核实，不得作为当前实现、生效配置、
授权边界或现状判断的依据。** 用户已明确：绝大部分内容过时或不正确。

已知典型失效例（仅举例，不代表其余有效）：

- `_baseline-2026-10-23.md` 记录 `TEXT_PROVIDER=agent_plan` 走 `/zen/go/v1` 端点；
  当前已迁移到 `https://opencode.ai/inference/go/openai/v1`（提交 `1237553`）。
- 同文件记录 `HEAD 201b212`、模型/额度取值，均属当时快照，须重新读 `.env.content`。
- `.archive/` 是 2026-10-23 那次清理判定的「已被代码推翻 / 已被仓库覆盖 / 一次性读数」，
  当时就标注为「只供历史检索，不用于判断现状」。
- 两份工作区日志里的 HEAD、提交号、测试读数、待决策状态都是 11-12/11-13 的现场值。

## ⚠️ 清理前必须核对的候选硬规则

用户判断「绝大部分过时/不正确」，因此**没有**把下列条目升格为现行规则，也未并入
`AGENTS.md`/`CLAUDE.md`。但它们是用户历史明确过的**操作/费用硬规则**，且经检索
当前 `AGENTS.md`/`CLAUDE.md` 未完整覆盖。删除原始记忆前请逐条裁决「仍有效 / 已失效」；
仍有效的应改写进项目文档，再删原文。

| 候选（归档内路径） | 历史主张 | 项目文档覆盖情况 |
|---|---|---|
| `claude-project-memory/step02-forbidden-force-flag.md` | step02a/b 禁用 `--force`（忽略缓存重跑烧额度） | `AGENTS.md` 有「不自行使用 `--force` 扩量」，口径近似但不完全同 |
| `claude-project-memory/image-gen-one-frame-at-a-time.md` | 生图逐帧确认，禁止批量 | `AGENTS.md` Step02a 有「批量生成前须确认」，措辞不同 |
| `claude-project-memory/git-remote-policy.md` | 只推 `github`，gitee 冻结 | **未覆盖** |
| `claude-project-memory/shot-num-cli-mapping.md` | 用户说 shotN 直接用 `--shots N` | **未覆盖** |
| `claude-project-memory/slash-commands-execute-directly.md` | 斜杠命令直接执行，禁止前置分析 | **未覆盖** |
| `claude-project-memory/gbk-safe-print-characters.md` | Windows GBK print / subprocess 编码防线 | **未覆盖**（`AGENTS.md` 仅提 GBK 一次） |
| `claude-project-memory/error-print-formatting.md` | 错误打印走 `shared/error_fmt.py` | **未覆盖** |
| `claude-project-memory/multi-voice-tts-support.md` | `voice_registry` 跨章音色一致性 | **未覆盖** |

> 这些只是**待核对线索**，不是现行约束。核对方式：查当前代码/配置/对应专题文档，
> 确认仍成立的写入 `AGENTS.md` 或 `CLAUDE.md` 的唯一责任章节，再清理原文。

## 现在以什么为准

唯一事实源，按优先级：

1. **当前代码**（`step01_script_gen/`、`step02*_*/`、`shared/`、`_text_gen/` 等）
2. **最终生效配置**：`.env.content` 经 `shared/config.py` 叠加后的实际值
3. **项目约定**：[AGENTS.md](../../AGENTS.md)、[CLAUDE.md](../../CLAUDE.md)
4. **专题文档**：`docs/step01-layered-execution/`、`docs/dsh-handoff/` 等
5. **问题史**：`problems.md`、`todolist.md`
6. 本目录与 `docs/archive/`：**只供历史检索**

## 后续约定（用户 2026-11-14 明确）

**不再往任何 memory 目录写 aiVideo 项目记忆。**
项目记忆、现场状态、决策与工作记录统一写项目内可见的 `docs/` 专题文档。
`AGENTS.md` / `CLAUDE.md` 中「实施前先读项目记忆索引」的指引已移除。

## 本轮执行记录（2026-11-14）

> 按新约定，本条记录写在项目内 `docs/`，不再写入 `.workbuddy/memory/` 或
> `~/.claude/projects/.../memory/`。

1. **全量归档备份**：`.workbuddy/memory/` 2 份 + `~/.claude/projects/C--myFloders-git-aiVideo/memory/`
   254 份，共 256 个文件 / 960249 字节，逐文件字节比对一致（见 CLEANUP-CANDIDATES.md）。
2. **移除活文档旧引用**：`AGENTS.md`（「按任务查阅」引言 + 表格行）、`CLAUDE.md`（查阅入口节）
   删掉「实施前必读 `~/.claude/.../memory/MEMORY.md`」指引，改为以代码/生效配置/`AGENTS.md`/`CLAUDE.md`
   为唯一事实源；`problems.md`、`todolist.md`、`references/decision-tree-qc-repair.md` 加时效提示
   （不改历史叙述本身）。
3. **固化规则**：`AGENTS.md`/`CLAUDE.md` 写明项目记忆统一写项目内 `docs/`；
   用户级长期记忆清掉 aiVideo 专属条目，只保留跨项目元规则。
4. **`.gitignore`**：按逐条白名单放行归档索引三件（README / MANIFEST / CLEANUP-CANDIDATES）；
   256 份历史原文在项目内本地可见但不入库，避免把已判过时的内容塞进版本库。
5. **清理已完成**：用户确认「全部清理」后，256 个原件分 26 批（每批 ≤10）
   移入 Windows 回收站，逐批校验「文件移走 + 归档副本完好」，无异常；
   剩余原件 0，回收站可恢复条目 256，目录结构保留。
   详见 [CLEANUP-CANDIDATES.md](CLEANUP-CANDIDATES.md) 与 [CLEANUP-RESULT.json](CLEANUP-RESULT.json)。
   两个原 `memory/` 目录现为空，**按新约定不再写入**。

## 恢复方法

归档是原文副本，可随时从子目录复制回原位置（例如需要查阅某条旧记忆的上下文）：

```bash
cp "docs/memory-archive/claude-project-memory/<文件名>" \
   "C:/Users/ee54/.claude/projects/C--myFloders-git-aiVideo/memory/<文件名>"
```

> 如需恢复生效，必须先对照当前代码与配置核实，不得直接照做。
