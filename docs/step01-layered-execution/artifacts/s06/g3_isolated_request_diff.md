# S06（G3）隔离请求差异样本：三分支 + 真实 prepare_request

> 生成：`python tools/build_s06_g3_isolated_diff.py`（离线、零模型调用）
> **隔离样本，不接线生产**；默认门禁不变，真实收益未验证。

| 分支 | 正文路径 | 参考清单 | prepare | 绑定逐项对应 |
|---|---|---|---|---|
| seedance_concise | seedance_concise（从 shot 重建，调用方 prompt 不生效） | 2 项（first_frame.png、portrait_jia.png） | prepared | 是 |
| full_prompt | full_prompt（sanitize + ensure_prefix） | 2 项（first_frame.png、last_frame.png） | not_ready | 是 |
| first_last_image | first_last_image（build_motion_prompt，从 shot 重建） | 2 项（first_frame.png、last_frame.png） | prepared | 是 |

## 逐分支候选

### seedance_concise

- 正文路径：seedance_concise（从 shot 重建，调用方 prompt 不生效）
- 候选新增段：`【参考绑定】参考图1=本镜首帧（起始构图）；参考图2=甲（角色外观）`
- 候选正文尾部：`  图1；只取与本镜头明确匹配的主体特征，不得据此决定新人物、新场景或新动作
  图2；只取身份、五官、脸型、发型、服装与配饰，不取背景、姿势、构图、机位、原图光线与文字
【参考绑定】参考图1=本镜首帧（起始构图）；参考图2=甲（角色外观）`
- 参考清单不变：True；绑定逐项对应：True；绑定数=2 / 参考数=2
- 绑定经 prepare 后仍成立：是（verified）；冻结正文含绑定：True
- prepare：prepared

- 参考图1：first_frame.png（reference_image）→ `参考图1=本镜首帧（起始构图）` 在最终正文=True
- 参考图2：portrait_jia.png（reference_image）→ `参考图2=甲（角色外观）` 在最终正文=True

### full_prompt

- 正文路径：full_prompt（sanitize + ensure_prefix）
- 候选新增段：`【参考绑定】参考图1=本镜首帧（起始构图）；参考图2=本镜尾帧（终态）`
- 候选正文尾部：`所示内容生成，禁止与参考图冲突：参考图1：视频起点画面（首帧），本镜头画面从这里开始，人物位置与构图以它为基准；参考图2：视频终点画面（尾帧），动作结束后画面落在该构图
【参考绑定】参考图1=本镜首帧（起始构图）；参考图2=本镜尾帧（终态）`
- 参考清单不变：True；绑定逐项对应：True；绑定数=2 / 参考数=2
- 绑定经 prepare 后仍成立：未验证（unverified_provider_no_prepare）；冻结正文含绑定：True
- prepare：not_ready（BailianVideoService 未实现 prepare_request（prepared 入口不可用））

- 参考图1：first_frame.png（first_frame）→ `参考图1=本镜首帧（起始构图）` 在最终正文=True
- 参考图2：last_frame.png（last_frame）→ `参考图2=本镜尾帧（终态）` 在最终正文=True

### first_last_image

- 正文路径：first_last_image（build_motion_prompt，从 shot 重建）
- 候选新增段：`【参考绑定】参考图1=本镜首帧（起始构图）；参考图2=本镜尾帧（终态）`
- 候选正文尾部：`成使用以下参考图，严格按其所示内容生成，禁止与参考图冲突：参考图1：视频起点画面（首帧），本镜头画面从这里开始；参考图2：视频终点画面（尾帧），动作结束后画面落在该构图
【参考绑定】参考图1=本镜首帧（起始构图）；参考图2=本镜尾帧（终态）`
- 参考清单不变：True；绑定逐项对应：True；绑定数=2 / 参考数=2
- 绑定经 prepare 后仍成立：是（verified）；冻结正文含绑定：True
- prepare：prepared

- 参考图1：first_frame.png（first_frame）→ `参考图1=本镜首帧（起始构图）` 在最终正文=True
- 参考图2：last_frame.png（last_frame）→ `参考图2=本镜尾帧（终态）` 在最终正文=True

## 结论（供裁决）

- 三分支参考清单均不变：True；绑定逐项对应：True。
- 真实 `prepare_request` 可用分支：seedance_concise、first_last_image；不可用：full_prompt。
- 绑定经 prepare 验证：seedance_concise、first_last_image；未验证（provider 无 prepare）：full_prompt。
- 不可用分支按生产同一路径记 `pending_verification`，不冒充已准备。
- 本样本只回答「结构上参考绑定能否进正文并通过 prepare」；不回答模型是否因此改善。

## 限制

- 候选正文只在工具内追加，未接线生产；真实收益需实跑对照。
- bailian 未实现 prepare_request，full_prompt 分支的最终参考清单取自冻结请求（生产同路径记 pending_verification），不是 provider 实测。
- 样本使用隔离角色肖像/首帧，不读真实资产库；不代表真实资产分布。
