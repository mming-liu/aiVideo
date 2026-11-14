# S06（G3）隔离请求差异样本：先定稿参考、再构建正文

> 生成：`python tools/build_s06_g3_isolated_diff.py`（离线、零模型调用）
> **隔离样本，不接线生产**；默认门禁不变，真实收益未验证。

## 当前链路

1. build_frozen_for_shot 先构建正文与参考清单
2. prepare_request 冻结实际生效参考
3. 再用 prepared 重建冻结请求

## 逐字段差异

| 字段 | 当前 | 候选 |
|---|---|---|
| `request.prompt`（尾部） | …"官、发型、服装以参考图为准\n  图1；只取身份、五官、脸型、发型、服装与配饰，不取背景、姿势、构图、机位、原图光线与文字" | …"ng）：角色「甲」人物形象参考，图中人物五官、发型、服装以参考图为准\n  图1；只取身份、五官、脸型、发型、服装与配饰，不取背景、姿势、构图、机位、原图光线与文字\n【参考绑定】参考图1=甲（角色外观）" |
| 正文新增段 | （无） | "【参考绑定】参考图1=甲（角色外观）" |
| `reference_paths` | portrait_jia.png | portrait_jia.png |
| `references[*].role` | reference_image | reference_image |
| `frozen.references[*].role` | reference:portrait_jia.png | reference:portrait_jia.png |
| `frozen.media_mode` | reference | reference |
| 正文参考绑定 | 无 | 1 项 |

## 结论（供裁决）

- 参考清单本身不变（一致）；差异只在正文是否声明参考身份。
- 候选块为工具内追加，**未接线生产**；要证明收益，需在隔离分支实现「参考清单先行」后做真实模型对照。
- 本样本只回答「结构上能不能把参考绑定写进正文」，不回答模型是否因此改善。

## 限制

- 候选正文只在工具内追加，未接线生产；真实收益需实跑对照。
- 样本使用隔离角色肖像/首帧，不读真实资产库；不代表真实资产分布。
