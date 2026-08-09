# Git Clone（步骤 2）

仅当用户输入为 Git URL 时执行此步骤。本地项目跳过。

---

## 调用方式

```bash
git clone [--branch <ref>] --depth 1 <url> /tmp/qianwenai-clone-$(date +%s)
```

---

## URL 格式

支持 `url#branch` 后缀指定分支/tag：

| 输入 | 解析结果 |
|------|----------|
| `https://github.com/user/repo` | clone 默认分支 |
| `https://github.com/user/repo#develop` | `--branch develop` |
| `https://github.com/user/repo#v1.2.0` | `--branch v1.2.0` |

---

## 错误处理

| 错误 | 判断依据 | 处理 |
|------|----------|------|
| 网络不可达 | `Could not resolve host` | 提示检查网络 |
| 仓库不存在 | `Repository not found` / 404 | 提示检查 URL 是否正确 |
| 需要认证 | `Authentication failed` / 401 / 403 | 提示用户配置 Git 凭证（SSH key 或 token），**不在聊天中收集 token** |
| 分支/tag 不存在 | `Remote branch <ref> not found` | 提示可用分支，让用户确认 |

---

## 产出

- 本地项目目录路径（传递给步骤 3）

---

## 注意事项

- 使用 `--depth 1` 浅克隆减少下载量
- 私有仓库：只提示配置凭证，绝不在聊天中收集 token/密码
- clone 成功后自动进入步骤 3（项目分析）
