# R7 隔离小修与声音承载回读（2026-11-19）

**状态：R7 隔离候选可评阅；镜 3 声音窗口未通过。**
后续 R7.1 半秒跨镜预览见
[R7.1-cross-shot-and-general-fixes-20261119.md](R7.1-cross-shot-and-general-fixes-20261119.md)。
全程 0 外部模型 / 0 生图 / 0 视频 / 0 TTS 调用；正式章 `script_result.json`、
计划缓存、门禁、锁图与图片均未改动。

## 1. 基线与候选

| 项 | 读数 |
|---|---|
| 正式稿 revision | `0afa8074…` |
| 候选 | `.tmp/r7_isolated/锦衣夜行/part_01_杀青州/chapter_001/script_result.json` |
| 候选 `plan_id` | 重算绑定（旧 `51f333ff…` → 新计划身份） |
| 镜数 / 总时长 | 13 / **76.75s** |
| 镜 2 | 完整对象逐字段相等 |
| 镜 1 | 仅 `motion_desc` / `audio_cue` 变化 |
| 镜 1 FF/LF、镜 2 FF | SHA-256、锁记录、图片缓存身份逐项相等 |

保护哈希（前 16 位，base = candidate）：

```text
shot1_ff  23e1c4c31e480c1b
shot1_lf  64f99e07222ec4ab
shot2_ff  4089f234ff475f7a
shot1_ff_lock c43b7a45075a1f7a
shot1_lf_lock b71179645f93cf6b
shot2_ff_lock 04969acc8e80a9c1
```

## 2. 四处修订

| 镜 | 修订 | 结果 |
|---|---|---|
| 1→2 | 镜 1 motion 去“画外悠扬箫声”，audio_cue 去“箫音清越悠扬”；箫声改由镜 2 竹箫抵唇响起 | 镜 1 仅两字段变化 |
| 3 | N2 改为 L54 逐字原句（含“若非如此”），清掉“的，高价”断句；同步 B3 | 原文逐字成立 |
| 9 | 新增 N5“杨文轩一死，我们数年心血便尽皆化为乌有了”，`voice_role=张十三`，内心旁白；audio_cue 改“内心旁白（不张口）” | 7 单元之一 |
| 11 | 新增 D1“你若不想吃官司，便听我吩咐”，字幕/旁白段“张十三道：…”；原镜 12 对白顺延 D2 | 两句均归张十三 |
| 13 | prompt / motion_desc / lf_desc / keyframes[2].desc 统一改为“沿堤岸道路朝城里方向…驶离水湾没入夜色”；保留湾堤岸边、搀扶登车、跳上车辕扬鞭动作链 | 无“别庄”方向 |

计划侧同步：保留 B6/N4；新增 B7（N5）、B9（D1）；原 B7/B8 顺延为 B8/B10，
`plan_refs` 与 `dialogue_lines` 同步；`plan_id` 重算绑定。因 C6 会拒收跨字段
24 字整块搬运，镜 13 四处保留同一去向但措辞各异（未改语义）。

## 3. 确定性门禁

| 项 | 读数 |
|---|---|
| Step01 确定性质量门 | **hard=0 / visual_risk=0** |
| `script_preflight` C1–C7 | **hard=0**（advisory 仅既有 image_prompt_leak） |
| `check_event_contract` | **通过**；`lines_order_violation=0` |
| 播放顺序 | **N1→N2→N3→N4→N5→D1→D2** |
| 原文逐字 | N1/N2/N3/N4/N5/D1/D2 全部 `verbatim=true` |

说明：原文位置序为 N1→N3→N2→N4→N5→D1→D2，因镜 4 是回叙；**不以全章原文
位置递增作为验收条件**，播放序按实际时间轴单独核。

## 4. 声音承载回读（5 → 7）

| 单元 | 类型 | 文本 | 字符 | 承载镜 | 估时 |
|---|---|---|---|---|---|
| N1 | 旁白 | 杨家的主人… | 21 | 2 | 5.70s |
| N2 | 旁白 | 若非如此…买下 | 35 | 3 | **8.85s** |
| N3 | 旁白 | 杨文轩…秀才 | 23 | 4 | 5.30s |
| N4 | 旁白 | 那跃上船来…张十三 | 26 | 8 | 6.55s |
| N5 | 内心旁白（张十三） | 杨文轩一死…乌有了 | 23 | 9 | 5.05s |
| D1 | 对白（张十三） | 你若不想吃官司… | 14 | 11 | 3.30s |
| D2 | 对白（张十三） | 公子爷吃醉了酒… | 14 | 12 | 3.55s |

旧稿 **5 单元 / 114 字符**；R7 **7 单元 / 152 字符**。

**镜 3 窗口失败**：N2 从 10.75s 起，镜 3 可用 8.38s，需要 8.85s
（超出 0.47s）；未加速、未截尾、未跨镜。按用户预案保留原句候选并标记失败。

## 5. 声音告警

`shared/audible_speech.py` 提供共享判据，`voice_unit_report` 与
`script_preflight` 消费同一实现：

- 旧稿镜 11：`audio_cue` + `motion_desc` 各 1 条
  `missing_audible_speech_unit`（共 2 条）；
- R7 修后稿：**0 条**；
- 纯环境音（箫声/蝉鸣/风声）不触发；`tts_mix` 才检查，原生声音按其合同。

## 6. 视频 plan-only（隔离，0 POST）

| 镜 | 策略 | 结果 |
|---|---|---|
| 1 | auto | ready / first_last / 2 refs |
| 2 | first_only | ready / first / 1 ref（保护对照） |
| 3 | auto | blocked / `frames_missing`（缺 lf） |
| 9 | auto | blocked / `frames_missing`（缺 ff/lf） |
| 11 | auto | blocked / `frames_missing`（缺 ff/lf） |
| 12 | auto | blocked / `frames_missing`（缺 ff/lf） |
| 13 | auto | blocked / `frames_missing`（缺 ff/lf） |

未伪造采纳回执；缺帧镜头明确列出。

## 7. 三问回审接入

`shared/revision_questions.py` 提供三项通用自检问题：

1. 摘句是否保留了决定句意的条件、否定及其所指？
2. 观众通过哪一句、哪位说话人或哪个既有声音载体，听懂角色改变行动的动机与威胁？
3. 末镜呈现的出发地、移动方向与目的地，分别有何原文依据？

接入：
- `outline._replan_by_ops` 与 `revise_plan_full` 的修订请求尾部；
- `feedback.build_regen_feedback` 的分镜重生成反馈；
- `content_verify.SYSTEM_PROMPT` 的内容核验系统提示。

离线渲染可见：`tests/shared/test_revision_questions.py`（3 条）。

## 8. 代码与测试

| 文件 | 改动 |
|---|---|
| `shared/audible_speech.py` | 新增共享声音承载判据 |
| `shared/script_preflight.py` | `include_audible_speech` 接入 |
| `tools/voice_unit_report.py` | 回读绑定、告警、`--audio-mode` |
| `shared/revision_questions.py` | 三问单一来源 |
| `outline.py` / `feedback.py` / `content_verify.py` | 三问接入 |
| `tests/shared/test_audible_speech.py` | 5 passed |
| `tests/shared/test_revision_questions.py` | 3 passed |

回归：`tests/step02_video_gen tests/step02b_video_gen tests/tools` **373 passed / 0 failed**；
声音/提示/预检目标 **48 passed**。

## 9. 交付物

隔离目录：`output/锦衣夜行/part_01_杀青州/chapter_001/_enhancement/r7_candidate/`

- `README.md`（观众信息表 + 5→7 + 门禁 + 备选）
- `script_before.json` / `script_r7.json`
- `r7_diff.json`
- `speech_units.json`
- `voice_unit_report.json/.md`（R7）
- `before_voice_unit_report.json/.md`（旧稿）
- `quality_gate.json`
- `r7_plan_only.json`
- `protection.json`
- `source_checks.json`

## 10. 待裁决：镜 3

| 方案 | 文本 | 字符 | 估时 | 8.38s 内 |
|---|---|---|---|---|
| 原句（当前候选） | 若非如此，前几日往泰州府去时，杨公子也不会花了两百贯宝钞的高价把她买下 | 35 | 8.85s | 否 |
| 短句 A | 若非如此，杨公子不会花两百贯宝钞买下她 | 19 | 4.80s | 是 |
| 短句 B | 若非如此，杨公子也不会花两百贯买下她 | 18 | 4.55s | 是 |

原句保留条件、人物、价款与买下事实；短句会改变“逐字原文”这一已指定条件，
需用户裁决后才能替换。未擅自启用改编、跨镜、加速或截尾。
