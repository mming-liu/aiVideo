# 已确认问题状态台账

- 稿件身份：`84ff4112e6f6649489d7571bc0a1df73b2800247c902b8bd8bf07780ee514dc7`
- 本次观察输入：有
- 确认缺陷 7 条：仍被报出 4，本次未检出 3，需重验 0，有证据关闭 0，撤销 0

| id | 镜头 | 字段 | 处置 | 状态 | 本次报出的 code |
|---|---|---|---|---|---|
| u6-01 | 13 | `ff_desc` | legal_expression | not_detected_this_round | — |
| u6-02 | 7 | `lf_desc` | confirmed | still_reported | model_audit_STATE_CHAIN_CONFLICT |
| u6-03 | 5 | `frame_layouts.ff[2].props` | improvement | still_reported | model_audit_PROP_CONTINUITY |
| u6-04 | 11 | `ff_desc` | confirmed | still_reported | model_audit_FF_RESULT_LEAK |
| u6-05 | 1 | `prompt` | confirmed | still_reported | model_audit_VISUAL_SPEC_CONFLICT |
| u6-06 | 6 | `prompt` | confirmed | still_reported | model_audit_TEXT_DUPLICATION |
| u6-07 | 13 | `prompt` | undecided | not_detected_this_round | — |
| u6-08 | 10 | `ff_desc` | confirmed | not_detected_this_round | — |
| u6-09 | 4 | `ff_desc` | confirmed | not_detected_this_round | — |
| u6-10 | 7 | `ff_desc` | confirmed | not_detected_this_round | — |

> `not_detected_this_round` 只表示**本次审计没有报出**，不表示已修复；关闭需要修订证据或重新裁决。
