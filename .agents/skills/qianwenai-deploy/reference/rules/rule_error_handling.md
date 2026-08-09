# 错误处理与约束

## 错误速查

| 现象                         | 原因                         | 处理                                                           |
|------------------------------|------------------------------|----------------------------------------------------------------|
| 环境检查：CLI 未装 / 版本过低 | `aliyun version` 失败        | 告知用户参考官方文档安装: https://help.aliyun.com/zh/cli/install-update-alibaba-cloud-cli |
| 环境检查：凭证无效            | `configure list` 无 Valid    | 按 `reference/deploy/01_env_check.md` 认证流程重新登录                   |
| 环境检查：身份探测失败        | `GetCallerIdentity` 报错     | 授权可能过期，回到认证流程重新登录                             |
| `InvalidTemplate`            | YAML 语法错                  | 看 Message 修模板                                              |
| `InsufficientStock`          | 库存不足                     | 给 2-3 个替代方案（更大规格 / 换地域）                         |
| `InvalidParameter`           | 密码不合格                   | 重新生成强密码                                                 |
| 栈回滚 `ROLLBACK_COMPLETE`   | 资源创建失败                 | `ListStackResources` 定位出错资源                              |
| Nginx 探活失败但栈成功       | UserData 未跑完 / Nginx 异常 | 查 `/var/log/qianwenai-bootstrap.log`                          |
| Nginx 通但应用未起（`app: "manual"` 待核验） | 应用崩了 / 尚未启动 | 用云助手查 `/var/log/qianwenai-app.log`                        |
| `DELETE_FAILED`              | 资源被外部占用               | ROS 控制台手动清理                                             |
| 密码丢失                     | `.local` 文件误删            | ECS/RDS 控制台重置密码                                         |
| RunCommand 超时              | 云助手未响应                 | 检查 ECS 状态和 `DescribeCloudAssistantStatus`                 |
| RunCommand 权限不足          | 缺 `ecs:RunCommand` 权限     | 添加 `AliyunECSFullAccess` 或精确授权                          |
| 热更新后应用未启动           | 新版本产物问题               | 检查远端日志 `/var/log/qianwenai-update.log`，修复后重新热更新 |
| 云助手不可用                 | 未安装或未启动               | `systemctl start aliyun.service`                               |
| 安全组未开放 80              | 规则缺失                     | ECS 控制台添加入方向 TCP 80                                    |
| RDS `InvalidDBInstanceClass` | 规格不可用                   | 检查 RDS 控制台可用规格                                        |
| RDS 可用区不支持             | ECS 有货但 RDS 没有          | 重新执行库存检查（见 `reference/deploy/08_check_stock.md`），带上 `DB_INSTANCE_CLASS` 校验 RDS 可用区 |
| `QuotaExceed.Instance`       | 配额已满                     | 清理闲置实例或提额                                             |

## 约束

**模板与 API**：

- ROS 必须用 `--TemplateURL`（`--TemplateBody` 被 WAF 拦截）
- 可用区必须来自库存检查（见 `reference/deploy/08_check_stock.md`，Agent 直接调用 `DescribeAvailableResource`）
- `DisableRollback=false` 和 `from=qianwenai` tag 必带
- 禁止跳过 `ValidateTemplate`

**产物与 OSS**：

- 临时桶记录在 `.qianwenai-deploy` 中，`delete_stack.sh` 依赖它清理

**密码**：

- 特殊字符仅 `!@%^*+=_-`（`& # $ | ;` 会破坏 `db.env` source）
- ECS 与 RDS 密码分别生成、分别记录，不入聊天

**探活**：

- `/healthz` 只证明 Nginx 活着；应用存活由云助手读应用日志单独核验（不做 HTTP 探测）

**RDS**：

- 仅 MySQL 8.0，不支持 PG/Redis/MongoDB
- 单 AZ，密码与 ECS 不复用
- `Fn::Sub` 内主脚本做 base64 编码注入，运行时解码 + source，避免 shell 变量与 Fn::Sub 冲突

## 当前限制

- 全栈按量付费，不支持包年包月
- 不支持 HTTPS（需自备域名+证书）
- 单 region
## 登服务器排查

核验应用是否启动时，通过 Cloud Assistant 在 ECS 上读应用日志判断。

## 要看的日志

| 文件 | 内容 |
|------|------|
| `/var/log/qianwenai-bootstrap.log` | UserData 引导过程 |
| `/var/log/qianwenai-app.log` | 应用 stdout/stderr |

## Cloud Assistant RunCommand

ECS 自带云助手，直接在实例上执行 shell 命令：

```bash
# 1. 下发命令（PlainText — 不要 base64 编码 CommandContent）
CID=$(PAGER=cat aliyun ecs RunCommand \
  --RegionId "$REGION" --InstanceId.1 "$INSTANCE_ID" --Type RunShellScript \
  --Timeout 60 --ContentEncoding PlainText \
  --CommandContent 'systemctl status qianwenai-app --no-pager; echo ---; tail -n 100 /var/log/qianwenai-app.log' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["InvokeId"])')

# 2. 取结果（异步，先等待）
sleep 8
PAGER=cat aliyun ecs DescribeInvocations --RegionId "$REGION" --InvokeId "$CID" --IncludeOutput true \
  | python3 -c 'import sys,json,base64; d=json.load(sys.stdin); r=d["Invocations"]["Invocation"][0]["InvokeInstances"]["InvokeInstance"][0]; print(base64.b64decode(r["Output"]).decode())'
```

> ⚠️ **不要 base64 编码 `--CommandContent`**。Cloud Assistant 默认 `--ContentEncoding PlainText`，
> 传入 base64 编码的内容会被当作乱码命令直接执行。

## 常用排查命令

```bash
# 查看服务状态
systemctl status qianwenai-app --no-pager

# 查看最近日志
tail -n 100 /var/log/qianwenai-app.log

# 查看引导日志
tail -n 50 /var/log/qianwenai-bootstrap.log

# 查看端口监听
ss -tlnp | grep 8080

# 重启服务
systemctl restart qianwenai-app
```

## 注意事项

- `INSTANCE_ID` 取自 `ListStackResources`（`ResourceType=ALIYUN::ECS::Instance` → `PhysicalResourceId`）
- `aliyun` CLI **没有 `--no-pager` 参数**，非交互环境用 `PAGER=cat aliyun ...`
- 排查→修复→`systemctl restart`→重新读应用日志确认启动，全程可走 RunCommand
