# 删除/清理

调用 `scripts/delete_stack.sh` 执行整栈销毁。

---

## 调用方式

```bash
bash scripts/delete_stack.sh --project-root . --yes
```

`--yes` 跳过命令行交互确认（Agent 应已通过 AskUserQuestion 让用户二次确认）。

---

## 执行流程

1. 读取 `.qianwenai-deploy` 状态文件，提取 `stack_id`、`region_id`、`artifact_bucket`
2. `aliyun ros DeleteStack` 发起删除
3. 轮询 `GetStack` 至 404（DELETE_COMPLETE），超时 20 分钟（含 RDS 45 分钟）
4. 清理 OSS 临时桶（`oss rm -r -f` + `oss rm -b -f`）
5. 删除本地 `.qianwenai-deploy` 和 `.qianwenai-deploy.local`

> 第 3 步失败或超时（DELETE_FAILED / 超时 / DeleteStack 报错）时，脚本**仍会执行第 4 步清 OSS 桶**，
> 然后打印剩余资源与「稍后重跑本脚本」的提示并以退出码 2 结束；状态文件会保留以便重试。

---

## 前置确认（Agent 必须做）

> ⚠️ **不可逆操作**——Agent 必须在调用前通过 AskUserQuestion 二次确认：
> - 说清释放范围（哪些资源会被删除）
> - 含 RDS 时额外警告：数据库数据将随 RDS 一起销毁且无法恢复，建议先导出备份

---

## 错误处理

| 情况 | 脚本行为 |
|------|----------|
| Stack 已不存在（404） | 视为成功，继续清理 OSS |
| DELETE_FAILED | 退出码 2，提示查 `ListStackResources` 定位原因 |
| 超时 | 退出码 2，提示到控制台检查 |
| OSS 桶清理失败 | 警告但不阻断（桶有 7 天 lifecycle 自动过期） |

---

## 重要约束

> 🚫 **严禁手动逐个删除云资源**（ECS、VPC、安全组、EIP 等）。
> 全栈模式下所有资源由 ROS 栈管理，只需执行此脚本，ROS 自动按依赖顺序释放。
> 手动删会导致栈状态不一致、资源残留、删除失败。

---

## 状态文件必需字段

脚本从 `.qianwenai-deploy` 读取以下字段，缺失则中止（不删任何资源）：

| 字段 | 用途 |
|------|------|
| `region_id`（必需） | 定位栈所在地域 |
| `stack_id`（必需） | DeleteStack 的目标 |
| `stack_name` | 日志展示 |
| `artifact_bucket` | 清理 OSS 桶 |
| `db_engine` | 判断超时时长（含 RDS 延长到 45 分钟） |
| `outputs.public_ip` | 确认提示中展示 |
