# tools/archive

已完成工作包的一次性脚本。只供查口径，**不保证还能运行**：多数脚本按
`Path(__file__).parent.parent` 定位仓库根，移到本目录后根目录会偏一层。
确需重跑时，用 `git show <提交>:tools/<文件名>` 取回原位置的版本。

历史文档里写的仍是原路径 `tools/<文件名>`。文档是当时的运行记录，保持原样，
路径对照见下表。

## 2026-11-21 精简包 S03

分类规则：只被历史文档引用、不被代码、测试或规则文件（AGENTS / CLAUDE / README）
引用的脚本移到这里；任何地方都没有引用的脚本直接删除（git 历史可取回）。
分类表见 [docs/tools-inventory-20261121.md](../../docs/tools-inventory-20261121.md)。

| 原路径 | 现路径 | 用途（来自引用文档） |
|---|---|---|
| `tools/build_m02_u6_candidate.py` | `tools/archive/build_m02_u6_candidate.py` | M02 U6 冻结候选构造 |
| `tools/build_m03_issue_status.py` | `tools/archive/build_m03_issue_status.py` | M03 问题台账状态 |
| `tools/build_s03_identity_shadow_diff.py` | `tools/archive/build_s03_identity_shadow_diff.py` | S03 身份影子比对 |
| `tools/build_s06_decision_samples.py` | `tools/archive/build_s06_decision_samples.py` | S06 G1/G2/G6 裁决样本 |
| `tools/build_s06_g3_isolated_diff.py` | `tools/archive/build_s06_g3_isolated_diff.py` | S06 G3 隔离请求差异 |
| `tools/build_s06_g7_samples.py` | `tools/archive/build_s06_g7_samples.py` | S06 G7 裁决样本 |
| `tools/diag_prop_anchor_generation.py` | `tools/archive/diag_prop_anchor_generation.py` | 09-25 道具锚点提示实验 |

更早归档的 `apply_738fa_audit_patch.py`、`apply_final_audit_patch.py` 不在本表。
