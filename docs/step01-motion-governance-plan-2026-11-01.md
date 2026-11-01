# Step02a/b 请求一致性计划（修订版，2026-11-01）

## 1. 目标与成功判据

**目标**：不重跑 step01；只修**可证明的组装错误**（完全相同的系统运镜指令重复注入、冻结记录与实际下发不一致、首帧组装误用中段状态），并把字段冲突变成**结构化报告**，交给内容修订，而不是在下游自动删词。

成功判据：
1. 同一份请求在“预检”和“实发”中内容一致（provider prepare 后的 prompt 与冻结记录逐字一致）；
2. 视频 prompt 中“运镜：”只出现一次，且来自单一注入点；
3. 首帧 prompt 按帧时刻组装（ff 用 ff_desc、lf 用 lf_desc），不把中段动作注入首帧；
4. 冲突报告区分 definite / suspected / insufficient，附字段与证据，不自动改写原稿；
5. step02a --plan-only 能输出最终图片 prompt（当前不能）；
6. 发送内容变化时，同步更新 request_key / prompt_hash / 缓存关系，不冒充同一身份。

## 2. 基线与现状（已核代码）

- 相机参数不是硬控制：_video_gen/volcengine_provider.py:406 把 camera_movement 拼进文本（仅当 text[:20] 不含“运镜：”时前置），因此不能断言“模型必按固定机位执行”，也不能断言“固定机位下老农不会出现”。
- 重复注入：step02_video_gen/prompt_builder.py:1165 在首行之后插入“运镜：X”；provider 只检查前 20 字，未命中又前置一次 → 实际 prompt 出现两次。
- 冻结记录：step02_video_gen/request_plan.build_frozen_request 支持 prepared 参数（以 provider prepare 结果为准），但 step02_video_gen/submit_poll.build_frozen_for_shot 未传 prepared → 冻结记录可能是 pre-provider 版本，与实际下发不一致。
- 首帧状态来源：shared/style_prefix.py:1365 add_image_strict_constraints 接收调用方传入的 prompt；当前帧组装把镜头 prompt（含“吹奏”）带进首帧，而 ff_desc 写“未吹”。
- 预检缺口：step02a --plan-only 只输出数量/缓存/写入清单，不输出最终图片 prompt；step02b 预检不能替代 provider prepare 后的核验。
- 观察与假设：两条视频“船原地”是观察；“固定机位导致”是假设，已撤回。相似度 ≥0.85、动作段数阈值都只是**待校准信号**，不能作为“位移不可见”的证明或 strict 硬门依据。

## 3. 施工单（保留编号，收缩范围）

| 模块 | 修订后的目标 | 验收 |
|---|---|---|
| S01 | 结构化报告冲突与证据：definite / suspected / insufficient；取消通用删词 | 单测覆盖三类判定；报告含字段、原文、证据 |
| S02 | 修复**完全相同的系统运镜指令重复注入**；验证 provider prepare 后内容与冻结记录一致 | prepared prompt 与 frozen prompt 逐字相等；运镜计数=1 |
| S03 | 找到首帧中段状态的实际注入来源，按帧时刻组装；原始字段冲突只报告、不自动改写 | ff prompt 来自 ff_desc；冲突进入报告；不删原字段 |
| S04 | 预检与实发共用组装逻辑；展示最终请求、来源与变更记录；step01 原稿只读 | plan-only 输出最终 prompt；与实际 prepare 结果一致 |
| S05 | 覆盖否定、不同主体、固定相机+主体移动、入画出画、重新编号；核实际入口与缓存失效 | 用例通过；request_key/prompt_hash 变化可追踪 |

## 4. 改动细则

### S01 冲突报告（零调用）

shared/shot_motion_checks.py 改为返回证据记录，不提供 sanitize：

- definite：同一字段内可直接判定的矛盾（如 camera_movement=固定机位，motion_desc 明确写“镜头掠过/横移”）；
- suspected：跨字段但可能分属不同时刻（如 prompt 含“吹奏”、ff_desc 含“未吹”）；需人工或按帧时刻判定；
- insufficient：仅靠相似度/段数得出的信号（位移、密度），标注“待校准”，不阻断。

记录字段：code、class、field_paths、原文片段、reason、evidence。

### S02 运镜单源 + 冻结一致（零调用）

- 单一注入点：二选一——provider 不再前置，或 prompt_builder 不再插入；推荐保留 provider 的前置（它是实际发送前的最后一道），把 prompt_builder 的插入删掉，或把“运镜”放到 prompt 最前使 provider 的 text[:20] 命中。
- build_frozen_for_shot 传入 prepared（provider prepare 结果），让冻结记录以实际下发为准；若 prepared 与 frozen 不一致，报错而不是静默。
- 注意：修复会改变实际发送字符串 → request_key/prompt_hash 变化；这是**新的请求身份**，必须更新摘要并让缓存失效，不能声称与原视频同一身份。

### S03 首帧按帧时刻组装（零调用）

- 定位 add_image_strict_constraints 的调用方（帧 prompt 组装）；首帧使用 ff_desc 的起始态，尾帧使用 lf_desc 的终态；
- 镜头 prompt 里的中段动作（“吹奏”）不再注入首帧；这是**组装修正**，不是删原字段；
- 若 ff_desc 与 prompt 本身语义冲突，报告 suspected，交内容修订；不自动宣布某字段胜出。

### S04 预检=实发（零调用）

- step02a --plan-only 增加最终图片 prompt（首帧/尾帧各一条）与来源字段；
- step02b plan 使用与实发相同的 prepare 链路，输出 prepared prompt、request_key 与来源；
- 变更记录只允许“等价组装修正”（重复注入、帧时刻错用），并列出变更前后；
- step01 原稿只读；不重跑、不改 revision。

### S05 测试与入口（零调用）

- 否定：“未吹”不应被误判为“吹奏”；
- 不同主体：A 吹箫、B 不吹，不串主体；
- 固定相机+主体移动：船移动/人物入画，不因相机固定判为矛盾；
- 入画出画、重新编号：N 编号与镜头映射不丢；
- 缓存：prompt 变化后 request_key/prompt_hash 变化，旧缓存不命中；
- 实际入口：step02a/02b --plan-only 与 provider prepare 的最终 prompt 对照。

## 5. 边界与故障

- 不做通用删词、不做语义改写；只修可证明的组装错误；
- 原始字段冲突 → 报告，交内容修订（shot 1 的船/侧身/老农另立候选）；
- 相似度/密度只作提示，不作硬门；
- prepared 与 frozen 不一致 → fail-closed；
- 发送内容变化 → 更新请求摘要与缓存关系，不保留旧身份；
- 等价组装修正可保持 step01 revision 不变；非等价改动必须走修订/重采纳。

## 6. 验收

| 场景 | 入口 | 断言 |
|---|---|---|
| 运镜去重 | step02b --shots 1 --plan-only | prepared/frozen prompt 各只含一次“运镜” |
| 首帧状态 | step02a --shots 1 --plan-only | ff prompt 来自 ff_desc；不含中段“吹奏”；冲突入报告 |
| 冲突分类 | 单测 | definite/suspected/insufficient 各 1 例 |
| 缓存 | 单测/plan-only | prompt 变化 → request_key 变化，旧缓存不命中 |
| 入口一致 | plan-only vs prepare | 最终 prompt 逐字相等 |

## 7. 假设与未决

1. definite 的判定边界（哪些词算“明确运镜”）需要校准；
2. 运镜单源选 provider 还是 prompt_builder（推荐 provider，因其最接近发送）；
3. 去重后是否立即重生成受影响视频（请求身份已变，缓存会失效）；
4. shot 1 内容修订（船行进、侧身、老农）仍另立候选，不由本计划承诺解决。

## 8. 交付与证据

- 代码：shot_motion_checks（报告）、prompt_builder/provider（单源）、submit_poll/request_plan（prepared 冻结）、style_prefix（帧时刻）、step02a plan-only（最终 prompt）；
- 证据：单测、plan-only 输出、prepared/frozen 对照、request_key 变化记录；
- 本计划不产生付费调用。