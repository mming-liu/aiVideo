# 产物上传（步骤 9/10）

调用 `scripts/upload_artifacts.py` 上传构建产物和模板到 OSS。本文档说明调用方式和关键行为。

---

## 调用方式

### 上传模板（步骤 9，获取 TemplateURL）

```bash
python scripts/upload_artifacts.py --region "$REGION" \
  --template-file /tmp/qianwenai-template.yaml
```

输出 JSON 含 `template_url`。

### 上传构建产物（步骤 10）

```bash
python scripts/upload_artifacts.py --region "$REGION" \
  [--bucket "$BUCKET"] \
  --static-dir dist \
  --app-mode binary --app-dir app \
  > /tmp/qianwenai-artifacts.json
```

---

## 输出格式

```json
{
  "bucket": "qianwenai-deploy-tmp-a1b2c3",
  "static_url": "https://oss-...-internal.aliyuncs.com/static-20260729-143000.tar.gz?...",
  "app_url": "https://oss-...-internal.aliyuncs.com/app-20260729-143000.tar.gz?...",
  "template_url": "https://oss-...aliyuncs.com/template.yaml?..."
}
```

---

## 关键行为

| 行为 | 说明 |
|------|------|
| 桶名 | `qianwenai-deploy-tmp-<6位随机>`，首次新建，后续复用（传 `--bucket`） |
| 自动开通 OSS | 若 OSS 服务未开通，自动调 `OpenOssService` |
| Tag | 新建桶自动打 `from=qianwenai` tag |
| Lifecycle | 新建桶设 7 天过期（防遗忘） |
| 签名 URL | 24 小时有效 |
| 内网端点 | 默认转为 `oss-*-internal.aliyuncs.com`（VPC 内免费流量） |
| 打包排除 | `node_modules`、`.git`、`__pycache__`、`.venv`、`dist`、`build` 等 |

---

## app-mode 选项

| mode | 行为 |
|------|------|
| `binary` | tar 打包 app-dir |
| `docker-image` | `docker save` 镜像再 tar.gz |
| `docker-compose` | tar 打包整个目录 |
| `skip` | 不上传应用 |

---

## 注意事项

- 签名 URL 不要手动复制粘贴，用 `--artifacts-json` 管道传给 `generate_template.py`
- 模板上传用公网端点（ROS 需要公网可达的 URL）
- 产物上传用内网端点（ECS UserData 从 VPC 内拉取）

---

## 打包排除规则（tar filter）

脚本打包时自动排除以下目录和文件，避免冗余内容入包：

**排除目录名**（出现在路径任意层级即排除）：
`node_modules`、`.git`、`__pycache__`、`.venv`、`venv`、`.pytest_cache`、`.mypy_cache`、`.tox`、`.idea`、`.vscode`

**排除相对路径**：
`.next/cache`、`target/test-classes`、`build/test-results`

**排除文件名**：
`.DS_Store`、`Thumbs.db`

> 设计原因：macOS 上的 `node_modules` 含原生扩展（sharp/bcrypt 等），上 Linux ECS 后无法运行；UserData 会在 ECS 端重装依赖。

---

## OSS 服务自动开通

如果 OSS 未开通，脚本自动调用 `aliyun ossadmin OpenOssService`（开通免费，仅按用量计费）。
失败时提示用户到 https://oss.console.aliyun.com/ 手动开通。

---

## 内网端点转换

默认将 `oss-<region>.aliyuncs.com` 替换为 `oss-<region>-internal.aliyuncs.com`：
- 产物 URL（ECS UserData 从 VPC 内拉取）→ 用内网，流量免费
- 模板 URL（ROS 需公网可达）→ 用公网（`--template-file` 模式自动处理）
