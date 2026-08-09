# 记录状态（步骤 13）

调用 `scripts/record_state.py` 将部署结果写入 `.qianwenai-deploy` 状态文件。

---

## 调用方式

```bash
PASSWORD="$ECS_PWD" [DB_PASSWORD="$DB_PWD"] \
  python scripts/record_state.py \
    --stack-id "$STACK_ID" \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --topology single \
    --app-type systemd --runtime none \
    --nginx-mode static+app \
    --outputs-json '{"PublicIp":"47.x.x.x","EcsInstanceIds":"i-xxx"}' \
    --artifact-bucket "$BUCKET" \
    --artifact-urls-json "$(cat /tmp/qianwenai-artifacts.json)" \
    [--with-rds --db-engine mysql] \
    [--static-dir dist] [--app-dir app]
```

> **docker 部署额外传** `--app-mode docker-image|docker-compose`、
> `--app-image-name <镜像名:tag>`（docker-image 模式）、`--app-port <端口>`。
> 这些字段写入状态文件后，`update_app.sh` 热更新才能正确 `docker load` / `docker compose up`；
> 缺失时默认 `docker-image` + `qianwenai-app:latest` + 端口 8080。

---

## 密码传递

- `PASSWORD` 环境变量 → 写入 `.qianwenai-deploy.local`（权限 0600）
- `DB_PASSWORD` 环境变量 → 同上（含 RDS 时）
- **绝不通过命令行参数传密码**（避免 `ps` 泄露）
- **绝不输出到聊天**

---

## outputs-json 格式

从 `GetStack` 的 Outputs 提取并序列化为扁平 JSON：

```json
{
  "PublicIp": "47.x.x.x",
  "EcsInstanceIds": "i-bp1xxx",
  "DbInstanceId": "rm-xxx",
  "DbConnectionAddress": "rm-xxx.mysql.rds.aliyuncs.com",
  "DbPort": "3306",
  "DbAccount": "appuser"
}
```

---

## 产出文件

| 文件 | 内容 | 安全 |
|------|------|------|
| `.qianwenai-deploy` | 完整部署状态（无密码，但含 `current_artifact_urls` OSS **签名 URL**） | 权限 0600，自动加入 `.gitignore`；签名 URL 在有效期内等同下载凭证，**不要提交 git / 外发** |
| `.qianwenai-deploy.local` | 含 ECS/RDS 密码 | 自动加入 `.gitignore`，权限 0600 |

> ⚠️ `current_artifact_urls` 里的 `static_url` / `app_url` 是带签名参数的 OSS 预签名下载链接，在过期前任何持有者都可下载产物。因此该状态文件按 0600 落盘并加入 `.gitignore`，不应提交版本库或分享。

---

## 状态文件关键字段

- `version`: 1
- `stack_id` / `stack_name` / `region_id`
- `app_type` / `nginx_mode` / `topology`
- `app_mode` / `app_image_name` / `app_port`（仅 docker 部署，热更新用）
- `outputs.public_ip` / `outputs.ecs_instance_ids`
- `artifact_bucket`
- `current_artifact_urls`
- `created_at` / `updated_at`

---

## 状态文件完整 Schema

```json
{
  "version": 1,
  "deploy_mode": "full-stack",
  "region_id": "cn-hangzhou",
  "topology": "single",
  "app_type": "systemd", "runtime": "none",
  "static_dir": "dist",
  "app_dir": "app",
  "nginx_mode": "static+app",
  "app_mode": null,
  "app_image_name": null,
  "app_port": null,
  "stack_id": "xxx",
  "stack_name": "qianwenai-myapp-202607291700",
  "created_at": "2026-07-29T09:00:00Z",
  "updated_at": null,
  "tags": [{"Key": "from", "Value": "qianwenai"}],
  "outputs": {
    "public_ip": "47.x.x.x",
    "ecs_instance_ids": ["i-bp1xxx"],
    "db_instance_id": null,
    "db_connection_address": null,
    "db_port": null,
    "db_account": null
  },
  "artifact_bucket": "qianwenai-deploy-tmp-abc123",
  "current_artifact_urls": {
    "static_url": "https://...",
    "app_url": "https://..."
  },
  "db_engine": null,
  "notes": ""
}
```

### .local 文件格式

```json
{
  "stack_id": "xxx",
  "warning": "本文件含密码，请勿提交版本库",
  "ecs_password": "...",
  "db_password": "..."
}
```

权限 0600，自动追加到 `.gitignore`。

---

## outputs-json 字段映射

脚本支持两种键名风格（兼容 ROS API 原始格式和小写格式）：

| ROS 原始键 | 小写键 | 用途 |
|-----------|--------|------|
| `PublicIp` | `public_ip` | 公网 IP |
| `EcsInstanceIds` | `ecs_instance_ids` | ECS 实例 ID（逗号分隔或数组） |
| `DbInstanceId` | `db_instance_id` | RDS 实例 ID |
| `DbConnectionAddress` | `db_connection_address` | RDS 内网地址 |
| `DbPort` | `db_port` | RDS 端口 |
| `DbAccount` | `db_account` | RDS 账号 |
