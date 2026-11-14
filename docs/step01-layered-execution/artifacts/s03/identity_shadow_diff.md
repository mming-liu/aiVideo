# S03 身份影子差异（旧稿 vs 新稿）

> 生成：`python tools/build_s03_identity_shadow_diff.py`（离线、零模型调用）
> 检查器：`shared.execution_semantics.check_script_semantics`（真实函数）
> 本记录**不改变**默认门禁；旧稿的实际准入影响按既定裁决处理。

| 版本 | codes | relation_status | relation_note | 关系承载 | 过程证据 | needs_semantic_review |
|---|---|---|---|---|---|---|
| old_no_identity | causal_gap | unresolved | 该项未完成语义判定（因果链条目没有显式单位身份） | - | 无 | True |
| new_identity | causal_gap、time_ellipsis_lost | bound | - | shot:1 | 无 | False |
| new_identity_plus_bird | causal_gap、time_ellipsis_lost | bound | - | shot:1 | 无 | False |

## 准入差异（记录，不自动改变门禁）

- 旧稿（无身份）：`time_ellipsis_lost`=False；只出 `causal_gap` advisory，**不新增拒收**——该项未完成语义判定。
- 新稿（带身份）：`time_ellipsis_lost`=True——过程/时间证据缺失时按关系范围拒收。
- 鸟节拍（真实 B3 + 两关键帧）：改变裁决=False；鸟镜进入关系承载=False。

## 结论

身份生产把「无法定位关系」与「关系已定位但过程/时间缺失」分开：前者进入影子差异并标 `needs_semantic_review`，后者才触发 `time_ellipsis_lost`。旧稿在正式门禁上的最终影响仍需按既定决策在提交/影子评估中处理，本文件只提供可复核读数。
