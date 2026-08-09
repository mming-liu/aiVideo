# 创建栈（步骤 11）

调用 `scripts/create_stack.sh` 创建 ROS 栈。本文档说明调用方式、参数规则和重试安全机制。

---

## 调用方式

```bash
APP_NAME="$APP_NAME" APP_DESC="$APP_DESC" \
  [TIMEOUT_MIN=40] \
  bash scripts/create_stack.sh "$REGION" "$TEMPLATE_URL" "$STACK_NAME" /tmp/qianwenai-params.json
```

---

## 参数文件格式

Agent 需生成 JSON 文件（如 `/tmp/qianwenai-params.json`），包含所有 ROS 模板参数：

```json
[
  {"key": "AppName", "value": "myapp"},
  {"key": "InstanceType", "value": "ecs.e-c1m2.large"},
  {"key": "Password", "value": "<Agent生成的强密码>"},
  {"key": "SystemDiskSize", "value": "40"},
  {"key": "AppPort", "value": "8080"},
  {"key": "ZoneId", "value": "cn-hangzhou-h"},
  {"key": "UserDataScript", "value": "<userdata脚本内容>"}
]
```

含 RDS 时不传 `UserDataScript`，改传：
- `DbInstanceClass`、`DbInstanceStorage`、`DbName`、`DbAccount`、`DbPassword`

---

## 栈名规则

格式：`qianwenai-${APP_NAME}-$(date +%Y%m%d%H%M)`

> ⚠️ **只生成一次**。重试时复用同一栈名——脚本会自动检查服务端是否已有同名栈。

---

## 密码规则

- ≥12 位
- 特殊字符仅限 `!@%^*+=_-`（`& # $ | ;` 会破坏 `db.env` 的 shell source）
- ECS 与 RDS 密码分别生成
- 密码**不输出到聊天**

---

## 重试安全机制（脚本内置）

1. 创建前先 `ListStacks` 查同名栈
2. 已有 CREATE_IN_PROGRESS/COMPLETE → 直接复用其 StackId
3. 已有 CREATE_FAILED → 先 DeleteStack 释放名字再创建
4. CLI 超时 → sleep 3s 再查服务端，已创建则复用
5. 创建成功立即写临时状态文件 `.qianwenai-deploy`（`provisional: true`）

---

## 超时设置

| 场景 | TIMEOUT_MIN |
|------|-------------|
| 无 RDS | 15（默认；ECS+EIP 一般 2-5 分钟就绪） |
| 含 RDS | 40（RDS 实例创建约 10-30 分钟） |

> 该值是 **ROS 侧**的 `TimeoutInMinutes`，超时后 ROS 直接把栈判为失败。
> 必须小于步骤 12 `wait_and_probe.py --max-wait`（默认 1200s=20min，含 RDS 传 2700s=45min），
> 否则 ROS 已判失败、客户端还在空等。

---

## Tags（脚本自动打）

- `from=qianwenai`
- `qianwenai-appName=$APP_NAME`
- `qianwenai-appDesc=$APP_DESC`

---

## 参数构建逻辑（脚本内部）

脚本从 JSON 文件读取参数，逐个转为 `--Parameters.N.ParameterKey / ParameterValue` CLI 参数。
Agent 只需保证 JSON 文件格式正确，无需关心 CLI 参数拼接。

### CreateStack CLI 参数（脚本自动拼接）

```
--RegionId $REGION
--StackName $STACK_NAME
--TemplateURL $TEMPLATE_URL
--DisableRollback false
--TimeoutInMinutes $TIMEOUT
--Tags.1.Key from         --Tags.1.Value qianwenai
--Tags.2.Key qianwenai-appName  --Tags.2.Value $APP_NAME
--Tags.3.Key qianwenai-appDesc  --Tags.3.Value $APP_DESC
--Parameters.1.ParameterKey ...  --Parameters.1.ParameterValue ...
```

> ⚠️ `DisableRollback` 必须为 `false`（创建失败时自动回滚）。

---

## 临时状态文件（防孤儿栈）

创建成功后立即写 `.qianwenai-deploy`（`provisional: true`）：
- 即便后续步骤中断，`delete_stack.sh` 也能据此定位和清理栈
- `record_state.py`（步骤 13）会覆盖此临时文件写入完整状态
