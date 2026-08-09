# 等待栈终态 + 探活（步骤 12）

一条命令完成：轮询 GetStack 至终态 → 提取 Outputs → nginx 探活 → 输出结构化 JSON。（应用存活不走 HTTP 探测，改由云助手单独核验。）

---

## 调用方式

```bash
python3 scripts/wait_and_probe.py \
  --region "$REGION" \
  --stack-id "$STACK_ID" \
  --has-app
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--region` | 地域 ID | （必填） |
| `--stack-id` | 栈 ID | （必填） |
| `--has-app` | 标记 `app: "manual"`，由 Agent 用云助手核验应用 | 不加 = 跳过 |
| `--max-wait` | 最长等待秒数 | 1200（20 分钟），含 RDS 时传 2700（45 分钟） |
| `--probe-retries` | 探活重试次数 | 15 |
| `--probe-interval` | 探活重试基础间隔秒数（按次递增，上限 12s） | 4 |

> 纯静态项目（`app_type = static-only`）不加 `--has-app`。

### 关于等待时长

- 无 RDS：ECS+EIP 通常 2-5 分钟就绪，默认 1200s 已足够宽裕，不必手动加大。
- 含 RDS：RDS 创建约 10-30 分钟，传 `--max-wait 2700`。
- `--max-wait` 必须 **大于** 步骤 11 的 `TIMEOUT_MIN`（ROS 侧超时，默认 15min / 含 RDS 40min）；
  ROS 判失败后本脚本会立刻读到 `CREATE_FAILED`/`ROLLBACK_*` 终态并返回，不会空等到 `--max-wait`。
- 栈到达终态后**立即**开始 nginx `/healthz` 探活（不再固定 sleep 30s）：nginx 已就绪时 1 次即通、零等待；
  失败才按 4/8/12s（上限 12s）递增退避重试，15 次的重试窗口约 2.5 分钟，足够覆盖
  yum 装 Nginx 的慢场景。
- 单次 `aliyun` CLI 调用超过 30s 会被当作临时错误自动重试，不会中断整个等待。

---

## 输出格式（stdout JSON）

### 成功

```json
{
  "status": "ok",
  "public_ip": "47.xx.xx.xx",
  "instance_id": "i-xxx",
  "outputs": {"PublicIp": "...", "EcsInstanceIds": "i-xxx"},
  "health": {"nginx": "pass", "app": "manual"},
  "elapsed_seconds": 180
}
```

> `app: "manual"` 表示 nginx 已起，但应用存活**不**做 HTTP 探测（外部探测会假阴性，如
> Spring Boot 对未映射路径返回 500）。应用用云助手核验 —— 见下文。

### 失败

```json
{
  "status": "failed",
  "stage": "health_check",
  "error": "Nginx 探活失败 (15 次重试后)",
  "public_ip": "47.xx.xx.xx",
  "instance_id": "i-xxx",
  "outputs": {...},
  "health": {"nginx": "fail", "app": "skip"},
  "elapsed_seconds": 210
}
```

`stage` 可能值：`stack_create`（栈创建失败/超时）、`extract_outputs`（无法提取 IP）、`health_check`（nginx 探活失败）。

---

## Agent 决策逻辑

| 输出 status | 动作 |
|-------------|------|
| `ok` | 取 `public_ip`、`instance_id`、`outputs`，继续步骤 13（记录状态） |
| `failed` + stage=`stack_create` | 执行 `aliyun ros ListStackResources` 定位出错资源，参考 `reference/rules/rule_error_handling.md` |
| `failed` + stage=`health_check` | Nginx 没起来 —— 用云助手查 `/var/log/qianwenai-bootstrap.log` |

`ok` 且 `app: "manual"` 时，记成功前先核验应用：用云助手读应用日志判断是否起来（有干净启动行 / 端口在监听 = 起来了）。`INSTANCE_ID` = `ListStackResources` 里的 ECS 实例。

```bash
CID=$(PAGER=cat aliyun ecs RunCommand --RegionId "$REGION" --InstanceId.1 "$INSTANCE_ID" \
  --Type RunShellScript --Timeout 60 --ContentEncoding PlainText \
  --CommandContent 'systemctl status qianwenai-app; echo ---; tail -n 100 /var/log/qianwenai-app.log' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["InvokeId"])')
sleep 8
PAGER=cat aliyun ecs DescribeInvocations --RegionId "$REGION" --InvokeId "$CID" --IncludeOutput true \
  | python3 -c 'import sys,json,base64;d=json.load(sys.stdin);r=d["Invocations"]["Invocation"][0]["InvokeInstances"]["InvokeInstance"][0];print(base64.b64decode(r["Output"]).decode())'
```

---

## 心跳

脚本通过 stderr 输出心跳播报，Agent 可据此向用户提供等待反馈：
- `[heartbeat] 已等待 60s，当前状态: CREATE_IN_PROGRESS`
- `[heartbeat] 栈创建成功! IP: 47.xx.xx.xx, 开始探活...`
- `[heartbeat] Nginx 探活通过 (第 3 次)`

---

## 探活失败排查

用 Cloud Assistant 查日志（见 `reference/rules/rule_error_handling.md`）：
- `/var/log/qianwenai-bootstrap.log` — UserData 引导过程
- `/var/log/qianwenai-app.log` — 应用 stdout/stderr
