#!/bin/bash
# qianwenai · Docker 应用 bootstrap
# 占位符：
#   __APP_ARTIFACT_URL__   应用镜像 tar.gz（docker save 的产物）或 docker-compose.yml + 构建上下文 tar.gz 的 OSS 签名 URL
#   __APP_MODE__           docker-image | docker-compose
#   __APP_PORT__           应用容器监听端口（被 Nginx 反代）
#   __APP_IMAGE_NAME__     docker-image 模式下 docker load 后的镜像名:tag（如 myapp:latest）
set -euxo pipefail

LOG=/var/log/qianwenai-bootstrap.log
exec >> "$LOG" 2>&1
echo "[$(date -u +%FT%TZ)] === qianwenai docker bootstrap start ==="

# 1. 安装 Docker
if ! command -v docker >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y docker
  elif command -v yum >/dev/null 2>&1; then
    yum install -y docker
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y docker.io
  fi
fi
systemctl enable docker
systemctl start docker

APP_URL="__APP_ARTIFACT_URL__"
APP_MODE="__APP_MODE__"
APP_PORT="__APP_PORT__"
IMAGE_NAME="__APP_IMAGE_NAME__"

mkdir -p /opt/qianwenai
cd /opt/qianwenai
curl -fsSL "$APP_URL" -o app.tar.gz

# 若 RDS bootstrap 写了 db.env，docker 启动时挂进容器
DB_ENV_OPT=""
[ -f /etc/qianwenai/db.env ] && DB_ENV_OPT="--env-file /etc/qianwenai/db.env"

if [ "$APP_MODE" = "docker-image" ]; then
  # 解压并 docker load
  tar -xzf app.tar.gz
  # docker load 后确保镜像 tag 与 IMAGE_NAME 一致：产物 tar 里的 tag 未必等于 IMAGE_NAME，
  # 若不一致，unit 的 ExecStart 会按 IMAGE_NAME 找不到/跑错镜像。这里按加载结果重打 tag。
  LOAD_OUT=$(docker load -i image.tar)
  echo "$LOAD_OUT"
  LOADED_REF=$(echo "$LOAD_OUT" | sed -n 's/^Loaded image: //p' | head -1)
  [ -z "$LOADED_REF" ] && LOADED_REF=$(echo "$LOAD_OUT" | sed -n 's/^Loaded image ID: //p' | head -1)
  if [ -n "$LOADED_REF" ] && [ "$LOADED_REF" != "${IMAGE_NAME}" ]; then
    docker tag "$LOADED_REF" "${IMAGE_NAME}"
  fi
  # 写 systemd unit 持久托管
  cat > /etc/systemd/system/qianwenai-app.service <<UNIT
[Unit]
Description=qianwenai app container
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker rm -f qianwenai-app
ExecStart=/usr/bin/docker run --rm --name qianwenai-app -p ${APP_PORT}:${APP_PORT} ${DB_ENV_OPT} ${IMAGE_NAME}
ExecStop=/usr/bin/docker stop qianwenai-app

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable qianwenai-app
  systemctl restart qianwenai-app

elif [ "$APP_MODE" = "docker-compose" ]; then
  # 解压（包含 docker-compose.yml 和构建上下文 或 已 build 的镜像 tar）
  tar -xzf app.tar.gz
  # 安装 docker compose plugin（若未自带）
  if ! docker compose version >/dev/null 2>&1; then
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
      -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  fi
  # compose 会自动加载同目录下的 .env；如有 RDS env 则导出到 .env。
  # db.env 权限为 0600（含数据库密码），必须用 install -m 600 保持权限，
  # 否则默认 cp 会落成 0644，使密钥对同机其他用户可读。
  if [ -f /etc/qianwenai/db.env ]; then
    install -m 600 /etc/qianwenai/db.env ./.env
  fi
  docker compose -f docker-compose.yml up -d
fi

echo "[$(date -u +%FT%TZ)] docker app up"
