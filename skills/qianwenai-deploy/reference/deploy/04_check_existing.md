# 存量部署检测（步骤 4）

Agent 直接执行 CLI 命令检测当前 region 下是否存在本工具创建的 ROS 栈，然后根据结果做路由决策。

---

## 查询命令

```bash
aliyun ros ListStacks --RegionId "$REGION" \
  --Tag.1.Key from --Tag.1.Value qianwenai
```

---

## 解析结果

从返回的 JSON 中提取 `Stacks` 数组，**过滤掉** `Status == "DELETE_COMPLETE"` 的栈。

对每个存活栈，读取 Tags 中 `qianwenai-appName` 的值，与当前项目的 `APP_NAME` 比对：

| 情况 | 含义 |
|------|------|
| 存在 `qianwenai-appName == APP_NAME` 的栈 | **同项目已有部署** |
| 存在栈但 appName 不匹配 | **其他项目的部署**（不影响当前操作） |
| 无存活栈（或 Stacks 为空） | **无存量部署** |

---

## 路由决策

| 检测结果 | 动作 |
|----------|------|
| 同项目已有部署 | AskUserQuestion：① 热更新（推荐，IP 不变）② 删除旧栈重新部署 |
| 无存量部署 | 继续全栈部署（步骤 5） |

> 用户选择热更新 → 跳转到 **热更新流程**（U1–U3）。
> 用户选择删除重建 → 先执行 `bash scripts/delete_stack.sh --project-root . --yes`，等待完成后继续全栈部署。

---

## 示例输出解读

```json
{
  "Stacks": [
    {
      "StackName": "qianwenai-myapp-202607151030",
      "StackId": "abc-123-def",
      "Status": "CREATE_COMPLETE",
      "CreateTime": "2026-07-15T10:30:00",
      "Tags": [
        {"Key": "from", "Value": "qianwenai"},
        {"Key": "qianwenai-appName", "Value": "myapp"}
      ]
    }
  ]
}
```

上例中 `qianwenai-appName` = `myapp`，若当前 `APP_NAME` 也是 `myapp`，则判定为同项目已有部署。
