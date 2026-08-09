#!/bin/bash
# qianwenai · systemd 托管应用
# 占位符：
#   __APP_ARTIFACT_URL__   应用产物 tar.gz 的 OSS 签名 URL
#   __APP_RUNTIME__        none | java | node | python
#   __START_COMMAND__          完整启动命令（相对 /opt/qianwenai），如
#                              ./server / "python3 app.py" / "java -jar app.jar" /
#                              "node server.js" / "gunicorn -b :8080 app:app"
#   __APP_PORT__           应用监听端口
set -euxo pipefail

LOG=/var/log/qianwenai-bootstrap.log
exec >> "$LOG" 2>&1
echo "[$(date -u +%FT%TZ)] === qianwenai systemd bootstrap start ==="

APP_URL="__APP_ARTIFACT_URL__"
RUNTIME="__APP_RUNTIME__"
ENTRY="__START_COMMAND__"
PORT="__APP_PORT__"

# 1. 安装运行时
case "$RUNTIME" in
  none)
    : # 无需安装运行时（静态链接二进制、或运行时已存在）
    ;;
  java)
    if ! command -v java >/dev/null 2>&1; then
      if command -v dnf >/dev/null 2>&1; then dnf install -y java-17-openjdk-headless
      else yum install -y java-17-openjdk-headless; fi
    fi
    ;;
  node)
    if ! command -v node >/dev/null 2>&1; then
      # 走发行版自带的、带 GPG 签名校验的包源安装 Node（不再用 `curl … | bash` 下载即执行远程代码）。
      # 顺序：① 直接 dnf install（Alibaba Cloud Linux 3 的 nodejs 20 是 alinux3-updates 里的独立包）；
      #       ② 独立包不存在时，若有 nodejs 模块流则启用后再装；③ 老系统退回 yum。
      if command -v dnf >/dev/null 2>&1; then
        if ! dnf install -y nodejs npm; then
          if dnf -q module list nodejs >/dev/null 2>&1; then
            dnf -y module reset nodejs || true
            # 优先 20，没有则退回该镜像可用的默认流
            dnf -y module enable nodejs:20 || dnf -y module enable nodejs || true
            dnf install -y nodejs npm
          fi
        fi
      else
        yum install -y nodejs npm
      fi
    fi
    if ! command -v yarn >/dev/null 2>&1; then
      npm install -g yarn --registry=https://registry.npmmirror.com
    fi
    ;;
  python)
    if ! command -v python3 >/dev/null 2>&1; then
      yum install -y python3 python3-pip
    fi
    ;;
  *)
    echo "[warn] unknown runtime '$RUNTIME', skipping runtime install"
    ;;
esac

# 2. 拉产物
mkdir -p /opt/qianwenai
cd /opt/qianwenai
curl -fsSL "$APP_URL" -o app.tar.gz
tar -xzf app.tar.gz
rm -f app.tar.gz

# 2b. Java JAR 名兜底：Maven/Gradle 通常产出带版本号的 JAR 名，而 ENTRY 常硬编码
# 固定名（如 "java -jar app.jar"）。若 ENTRY 引用的 JAR 不存在，则把真正可运行的
# JAR 软链到期望名，保证无论产物实际文件名如何都能启动。
if [ "$RUNTIME" = "java" ]; then
  # ENTRY 里的 JAR token = "-jar" 后面那个参数（默认 app.jar）。
  WANT_JAR="$(printf '%s ' $ENTRY | awk '{for(i=1;i<NF;i++) if($i=="-jar"){print $(i+1); exit}}')"
  [ -n "$WANT_JAR" ] || WANT_JAR="app.jar"
  WANT_BASE="$(basename "$WANT_JAR")"
  if [ ! -f "/opt/qianwenai/$WANT_BASE" ]; then
    # 选最大的 *.jar（即可运行的 fat JAR），排除 sources/javadoc/plain jar。
    # 用 stat 保证可移植（最小化镜像可能没有 GNU find -printf）。
    REAL_JAR="$(find /opt/qianwenai -maxdepth 3 -type f -name '*.jar' \
      ! -name '*-sources.jar' ! -name '*-javadoc.jar' ! -name 'original-*.jar' 2>/dev/null \
      | while read -r f; do printf '%s\t%s\n' "$(stat -c%s "$f" 2>/dev/null || echo 0)" "$f"; done \
      | sort -rn | head -1 | cut -f2)"
    if [ -n "$REAL_JAR" ]; then
      echo "[info] 未找到 JAR '$WANT_BASE'，软链真实 JAR：$REAL_JAR -> /opt/qianwenai/$WANT_BASE"
      ln -sf "$REAL_JAR" "/opt/qianwenai/$WANT_BASE"
    else
      echo "[error] /opt/qianwenai 下未找到可运行的 JAR；应用将无法启动"
    fi
  fi
fi

# python: 安装依赖
if [ "$RUNTIME" = "python" ] && [ -f requirements.txt ]; then
  python3 -m pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt
fi
# node: 安装依赖（产物已包含 package.json 时）
if [ "$RUNTIME" = "node" ] && [ -f package.json ]; then
  yarn install --production --registry=https://registry.npmmirror.com
fi

# 3. 解析启动命令
# ENTRY 即「完整启动命令」，是命令的唯一来源；脚本不按 runtime 注入任何解释器，
# 只把首个 token（argv[0]）解析成绝对路径——systemd ExecStart 要求 argv[0] 为绝对路径。
# 这样无论 ./server / "python3 run.py" / "java -jar app.jar" / "gunicorn app:app"
# 都按用户给定的命令原样运行，不存在「自动前缀」与「用户前缀」相撞的问题。
set -- $ENTRY
ARGV0="$1"; shift || true
case "$ARGV0" in
  /*)
    : ;;                                    # 已是绝对路径，原样使用
  */*)
    # 含 / 的相对路径（./server、subdir/app）→ 拼绝对路径；不能走 command -v，它会原样返回相对路径
    ARGV0="/opt/qianwenai/${ARGV0#./}"
    chmod +x "$ARGV0" 2>/dev/null || true
    ;;
  *)
    if command -v "$ARGV0" >/dev/null 2>&1; then
      ARGV0="$(command -v "$ARGV0")"        # PATH 上的解释器/工具（python3 / node / java / gunicorn …）
    else
      ARGV0="/opt/qianwenai/$ARGV0"         # 产物里的可执行文件（裸文件名，如 server）
      chmod +x "$ARGV0" 2>/dev/null || true
    fi ;;
esac
EXEC="$ARGV0 $*"

# 4. 写 systemd unit
cat > /etc/systemd/system/qianwenai-app.service <<UNIT
[Unit]
Description=qianwenai app
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/qianwenai
Environment=PORT=${PORT}
EnvironmentFile=-/etc/qianwenai/db.env
ExecStart=${EXEC}
Restart=always
RestartSec=3
StandardOutput=append:/var/log/qianwenai-app.log
StandardError=append:/var/log/qianwenai-app.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable qianwenai-app
systemctl restart qianwenai-app

echo "[$(date -u +%FT%TZ)] systemd app up"
