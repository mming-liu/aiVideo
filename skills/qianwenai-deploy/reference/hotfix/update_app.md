# 热更新

调用 `scripts/update_app.sh` 通过 Cloud Assistant 在 ECS 上原子替换应用代码。

---

## 调用方式

```bash
APP_URL="$APP_URL" STATIC_URL="$STATIC_URL" \
  bash scripts/update_app.sh
```

环境变量 `PROJECT_ROOT`（默认 `.`）指向含 `.qianwenai-deploy` 的目录。

---

## 前置条件

- `.qianwenai-deploy` 状态文件存在
- 新产物已通过 `upload_artifacts.py` 上传到 OSS
- `APP_URL` 和/或 `STATIC_URL` 至少一个非空

---

## 执行流程（脚本在 ECS 上执行的逻辑）

脚本按状态文件里的 `app_type` 分派应用更新逻辑（`docker` / `systemd`）。
**docker 部署必须走 docker 分支**：早期版本无视 `app_type` 一律生成 systemd 脚本，
导致 docker 应用热更新「成功返回」但线上仍跑旧容器。

### 应用更新 · systemd（最小停机窗口）

1. **阶段 1：预备**（服务继续运行）
   - 下载新产物到 `/opt/qianwenai.staging`
   - 完整性校验（`tar -tzf`）
   - 预安装依赖（Python: pip download；Node: yarn install）

2. **阶段 2：原子切换**（停机窗口）
   - `systemctl stop qianwenai-app`
   - `rm -rf /opt/qianwenai && mv staging → /opt/qianwenai`
   - 离线安装依赖（使用阶段 1 预下载的缓存）
   - `systemctl restart qianwenai-app`
   - 本地健康检查（curl localhost 重试 15 次）

### 应用更新 · docker

依赖状态文件字段：`app_mode`（`docker-image` / `docker-compose`）、
`app_image_name`（镜像名:tag）、`app_port`（健康检查 / 端口映射）。

- **docker-image 模式**
  1. 下载 tar.gz 到 `/opt/qianwenai.staging` 并校验、解出 `image.tar`
  2. 记录旧镜像 ID（回滚用）→ `docker load -i image.tar`
  3. 确保 systemd unit `qianwenai-app.service` 存在 → 目录原子替换 → `systemctl restart qianwenai-app`
  4. 健康检查失败 → `docker tag` 回滚到旧镜像并重启（新产物留在 `/opt/qianwenai.failed`）
- **docker-compose 模式**
  1. 下载 tar.gz（含 `docker-compose.yml` + 上下文/镜像）到 staging
  2. `docker compose down` 旧服务 → 目录原子替换 → `docker compose up -d --build`
  3. 健康检查失败 → 恢复旧目录并 `docker compose up -d --build`

### 静态文件更新（零停机）

1. 下载到 `/var/www/static.staging`
2. 完整性校验
3. `rm -rf /var/www/static && mv staging`
4. `nginx -t && systemctl reload nginx`

---

## 输出

stdout JSON：
```json
{"status": "success", "updated_instances": ["i-xxx"], "invoke_ids": ["t-xxx"]}
```

---

## 关键机制

| 机制 | 说明 |
|------|------|
| Cloud Assistant | 通过 `RunCommand` 下发，免 SSH、免开 22 端口 |
| 轮询 | `DescribeInvocations` 最多等 5 分钟 |
| 状态文件更新 | 成功后自动写入 `updated_at` 和 `current_artifact_urls` |
| 依赖预安装 | Python/Node 在 staging 目录预装，减少停机时间 |

---

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 无内容可更新 |
| 2 | 状态文件异常 |
| 3 | RunCommand 执行失败 |
