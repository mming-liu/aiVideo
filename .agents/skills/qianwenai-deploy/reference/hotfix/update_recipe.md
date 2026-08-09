# 远程更新脚本模板（ECS 端执行）

`update_app.sh` 通过 Cloud Assistant RunCommand 在 ECS 上执行如下脚本。
Agent 无需关心具体脚本内容（由 `update_app.sh` 自动生成），本文档仅供调试参考。

---

## 应用更新流程

`update_app.sh` 按状态文件里的 `app_type` 分派，生成完全不同的更新脚本：

| app_type | 更新机制 |
|----------|----------|
| `systemd` | 两阶段原子替换目录 + `systemctl restart`（见下「systemd」） |
| `docker`  | `docker load` / `docker compose up`（见下「docker」） |

---

## systemd 更新流程（两阶段原子替换）

### 阶段 1：预备（服务继续运行）

```bash
STAGING_DIR=/opt/qianwenai.staging
rm -rf "$STAGING_DIR" && mkdir -p "$STAGING_DIR"

# 下载 + 完整性校验
curl -fsSL '<APP_URL>' -o "$STAGING_DIR/app.tar.gz"
tar -tzf "$STAGING_DIR/app.tar.gz" >/dev/null
tar -xzf "$STAGING_DIR/app.tar.gz" -C "$STAGING_DIR"
rm -f "$STAGING_DIR/app.tar.gz"
```

#### 按 app_type 预安装依赖

| app_type | 预安装命令（在 staging 目录执行） |
|----------|----------------------------------|
| systemd (runtime=python) | `pip download -d /tmp/qianwenai-pip-cache -r requirements.txt` |
| systemd (runtime=node) | `yarn install --production --registry=https://registry.npmmirror.com` |
| systemd (runtime=none) | 无需（静态编译） |
| systemd (runtime=java) | 无需（fat jar） |

### 阶段 2：原子切换（停机窗口）

```bash
systemctl stop qianwenai-app || true
# 保留旧版本用于回滚（上一轮的备份先清掉）
rm -rf /opt/qianwenai.prev
if [ -d /opt/qianwenai ]; then mv /opt/qianwenai /opt/qianwenai.prev; fi
mv "$STAGING_DIR" /opt/qianwenai

# 离线安装依赖（仅 Python，使用阶段 1 缓存）
pip install --no-index --find-links /tmp/qianwenai-pip-cache -r requirements.txt

systemctl restart qianwenai-app
```

### 健康检查（失败自动回滚）

```bash
APP_PORT=$(sed -n 's/^Environment=PORT=//p' /etc/systemd/system/qianwenai-app.service | head -1)
APP_PORT=${APP_PORT:-8080}
sleep 3
for i in $(seq 1 15); do
  curl -sf -o /dev/null --max-time 5 "http://localhost:${APP_PORT}/" && exit 0
  sleep 2
done

# 健康检查失败 → 回滚到 /opt/qianwenai.prev，
# 坏产物留在 /opt/qianwenai.failed 供排查
systemctl stop qianwenai-app || true
rm -rf /opt/qianwenai.failed
mv /opt/qianwenai /opt/qianwenai.failed || true
mv /opt/qianwenai.prev /opt/qianwenai
systemctl restart qianwenai-app
exit 1
```

---

## docker 更新流程

依赖状态文件字段：`app_mode`（`docker-image` / `docker-compose`）、
`app_image_name`（镜像名:tag）、`app_port`（健康检查 / 端口映射）。
镜像/上下文自包含，无预安装依赖阶段。

### docker-image 模式

```bash
STAGING_DIR=/opt/qianwenai.staging
rm -rf "$STAGING_DIR" && mkdir -p "$STAGING_DIR"
curl -fsSL '<APP_URL>' -o "$STAGING_DIR/app.tar.gz"
tar -tzf "$STAGING_DIR/app.tar.gz" >/dev/null
tar -xzf "$STAGING_DIR/app.tar.gz" -C "$STAGING_DIR"   # 解出 image.tar

# 记录旧镜像 ID（回滚用）→ load 新镜像
PREV_IMAGE_ID=$(docker images -q '<IMAGE_NAME>' | head -1)
echo "$PREV_IMAGE_ID" > /opt/qianwenai.prev-image
docker load -i "$STAGING_DIR/image.tar"

# 确保 systemd unit 存在（首次热更新时可能缺失）→ 目录原子替换 → 重启容器
# unit 内容与首装 templates/userdata/docker.sh 一致
rm -rf /opt/qianwenai.prev
if [ -d /opt/qianwenai ]; then mv /opt/qianwenai /opt/qianwenai.prev; fi
mv "$STAGING_DIR" /opt/qianwenai
systemctl restart qianwenai-app
```

### docker-compose 模式

```bash
# staging 下载/校验同上（产物含 docker-compose.yml + 构建上下文/镜像）
# 停旧 compose → 目录原子替换 → 起新 compose
(cd /opt/qianwenai && docker compose -f docker-compose.yml down) || true
rm -rf /opt/qianwenai.prev
if [ -d /opt/qianwenai ]; then mv /opt/qianwenai /opt/qianwenai.prev; fi
mv "$STAGING_DIR" /opt/qianwenai
cd /opt/qianwenai
[ -f /etc/qianwenai/db.env ] && cp /etc/qianwenai/db.env ./.env
docker compose -f docker-compose.yml up -d --build
```

### 健康检查（失败自动回滚）

```bash
APP_PORT=<app_port，默认 8080>
sleep 3
for i in $(seq 1 15); do
  curl -sf -o /dev/null --max-time 5 "http://localhost:${APP_PORT}/" && exit 0
  sleep 2
done

# 失败回滚：
#   docker-image  → docker tag 旧镜像 ID 回 <IMAGE_NAME>，恢复旧目录并 systemctl restart
#   docker-compose→ 恢复 /opt/qianwenai.prev 目录并 docker compose up -d --build
# 坏产物均留在 /opt/qianwenai.failed 供排查
exit 1
```

---

## 静态文件更新流程（零停机）

```bash
STATIC_STAGING=/var/www/static.staging
rm -rf "$STATIC_STAGING" && mkdir -p "$STATIC_STAGING"

curl -fsSL '<STATIC_URL>' -o /tmp/static.tar.gz
tar -tzf /tmp/static.tar.gz >/dev/null
tar -xzf /tmp/static.tar.gz -C "$STATIC_STAGING" --strip-components=0
rm -f /tmp/static.tar.gz

rm -rf /var/www/static
mv "$STATIC_STAGING" /var/www/static
nginx -t && systemctl reload nginx
```

---

## Cloud Assistant 调用方式

```bash
aliyun ecs RunCommand \
  --RegionId "$REGION" \
  --InstanceId.1 "$INSTANCE_ID" \
  --Type RunShellScript \
  --CommandContent "$SCRIPT" \
  --Timeout 300
```

轮询状态：`aliyun ecs DescribeInvocations --InvokeId "$INVOKE_ID"`
- `Finished` / `Success` → 成功
- `Failed` → 取 `DescribeInvocationResults` 查错误输出（base64 解码）
- 超时上限 5 分钟

---

## 状态文件更新（脚本自动处理）

成功后 `update_app.sh` 自动写入：
- `updated_at`：UTC 时间戳
- `current_artifact_urls`：新产物的签名 URL
