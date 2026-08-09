# 询价确认（步骤 9）

Agent 直接执行 CLI 命令进行模板验证和费用估算。

> **`$TEMPLATE_URL` 从哪来**：先执行步骤 7 的模板上传，即
> `python3 scripts/upload_artifacts.py --template-file <生成的模板.yaml> ...`，
> 该命令会把模板上传到 OSS 并输出签名 URL；将其导出为 `TEMPLATE_URL` 后再执行下面的命令。
> （ROS 必须用 `--TemplateURL`，`--TemplateBody` 会被 WAF 拦截。）

---

## 模板验证

```bash
aliyun ros ValidateTemplate --RegionId "$REGION" --TemplateURL "$TEMPLATE_URL"
```

非 0 退出码 → 读 `Code` + `Message`，修模板后重试。

---

## 费用估算

```bash
aliyun ros GetTemplateEstimateCost \
  --RegionId "$REGION" \
  --TemplateURL "$TEMPLATE_URL" \
  --Parameters.1.ParameterKey AppName        --Parameters.1.ParameterValue "$APP_NAME" \
  --Parameters.2.ParameterKey InstanceType   --Parameters.2.ParameterValue "$INSTANCE_TYPE" \
  --Parameters.3.ParameterKey Password       --Parameters.3.ParameterValue 'Tmp_Pwd_For_Pricing!1' \
  --Parameters.4.ParameterKey SystemDiskSize --Parameters.4.ParameterValue "40" \
  --Parameters.5.ParameterKey AppPort    --Parameters.5.ParameterValue "8080" \
  --Parameters.6.ParameterKey ZoneId         --Parameters.6.ParameterValue "$ZONE_ID" \
  --Parameters.7.ParameterKey UserDataScript --Parameters.7.ParameterValue "#!/bin/bash"
```

> 含 RDS 时不传 UserDataScript，改传 RDS 参数：
> `DbInstanceClass`, `DbInstanceStorage`, `DbName`, `DbAccount`, `DbPassword`

---

## 解析结果

返回 `Resources.<LogicalId>.Result.Order.OriginalAmount`（每个资源的**每小时**金额）。
求和得到总小时单价。币种始终为**人民币（¥）**。

---

## 确认展示

AskUserQuestion 汇总确认时展示：
- 小时单价（¥）
- 本次将创建的全部计费资源清单
- 不含公网流量、快照、OSS 存储等动态费用的提示
