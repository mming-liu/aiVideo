# O13 LF 行尾防护与磁盘水位巡检（2026-11-28）

> 承接 O12（`b0bd936`）+ O9 修复（`eb9e343`）。`RULE_SOURCE_FINGERPRINT` 不变
> （`4fba3d45a6851f41`）——顺带验证了 O2 的 AST 归一化：**改掉 564 个文件的行尾
> 之后指纹一个字节没动**。

## 1. `.gitattributes` 行尾规范

在既有文件**开头**加通用规则（Git 后规则覆盖前规则，所以原有 `-text` 钉住项仍生效）：

```
*.py text eol=lf      *.md text eol=lf      *.json text eol=lf
*.yml/*.yaml text eol=lf      .env.content/.env.keys text eol=lf
*.ps1/*.bat text eol=crlf
```

保留的例外（不转换）：`baselines/** -text`、`tests/shared/fixtures/execution_contract/**`、
`docs/step01-layered-execution/*` 的若干 `-text` 证据文件——它们的 sha256 按原始字节登记。

## 2. 行尾归一（一次性的机械变更）

| 步骤 | 结果 |
| --- | --- |
| `git add --renormalize .` | 54 个仍以 CRLF 入库的文本文件转为 LF（其余早在库里就是 LF） |
| 工作树重写（仅 `text` 已声明且非 `eol=crlf` 的文件） | 564 个文件 CRLF→LF；179 个 `-text` 钉子跳过；3 个 `.ps1` 保持 CRLF |
| 复核 | 跟踪的 855 个 `.py` 里 **只剩 1 个** CRLF：`baselines/.../audit_old.py`（`-text` 钉子，按设计保留） |

工作树重写脚本按 `git check-attr text/eol` 逐文件判定，**不碰** `-text` 与二进制；
重写后 `git add -u .` 无任何内容变更（`--ignore-cr-at-eol` 下 0 行改动）。

## 3. `tools/workspace_size_audit.py --check-thresholds`（只读巡检）

```
python tools/workspace_size_audit.py --check-thresholds [--stale-days 14] [--core-limit-mb 200]
```

四项检查（**绝不删除、绝不写文件**，报告里 `writes: []`）：

| 检查 | 判据 | 本机实测 |
| --- | --- | --- |
| `core_code_size` | 代码+测试目录 ≤ 200 MB | **59.8 MB**（通过） |
| `tmp_stale_dirs` | `.tmp/` 子目录最新内容 mtime 超过 N 天 | **51 个**过期项（提示可清理） |
| `duplicate_large_files` | ≥16 MB 且 size+sha256 相同 | **2 组**重复 |
| `snapshot_accumulation` | 单章 `snapshots/` > 30 份 | 0（通过） |

退出码：`0` 全部通过；`1` 有越界项（同时打印 `[建议]` 清理清单）。
过期判定用**目录内最新文件**的 mtime，而不是目录自身 mtime——刚被 touch 的旧目录
不会被误判为"新鲜"。

## 4. 测试

`tests/tools/test_workspace_hygiene.py`（7 用例）：

1. `.gitattributes` 必须给 `*.py/*.md/*.json/*.yml/*.yaml` 声明 `eol=lf`、给
   `*.ps1/*.bat` 声明 `eol=crlf`；
2. 跟踪的 `*.py` 无 CRLF（用 `git check-attr` 豁免显式 `-text` 钉子）；
3-5. 干净根通过、过期+重复根报出 `[建议]`、核心区红线；
6-7. CLI 退出码 0/1。

## 5. 残余与风险

1. **一次性大范围行尾改写**：564 个文件的字节变了（内容不变）。已用
   `git diff --ignore-cr-at-eol` 与全量用例复核；若你本地有未提交的同文件改动，
   合并时需以内容为准（不要按行尾冲突手工合并）。
2. `workspace_size_audit --check-thresholds` 当前**只建议、不清理**；`.tmp/` 过期目录
   的删除仍需人工确认（脚本不碰生产/临时资料）。
3. 磁盘水位只覆盖核心代码区、`.tmp/`、重复大文件与 `snapshots/`；`output/` 正式产物
   不在清理建议范围内（受只读红线保护）。

## 6. 行尾归一暴露的一个既有缺陷（已修）

行尾改写后，	ests/step01_script_gen 有 **55 个用例**开始报 candidate_identity 拒收：

- 	ests/step01_script_gen/fixtures/step01_audit_min/audit_package.json 里登记的
  candidate_sha256 / quality_sha256 是按 **CRLF 工作树字节**算的；
- 而仓库里 candidate.json 的 **blob 一直是 LF**（git show eb9e343:... 可复核，
  sha256 从未等于登记值）——也就是说这批用例**只在 CRLF 工作树里才绿**，
  干净检出本该是红的。这正是本包要根治的「行尾假一致」。

修法：按提交里的 LF 字节重登这两个摘要（42ddccde… / 36f82ab…）。
复核：五个相关测试文件 75 项全过；全仓扫描 	ests/**/*.json 里其余
candidate/novel/quality/script_sha256 登记项，**0 处不匹配**。
