#!/usr/bin/env bash
# 热更新：Cloud Assistant → ECS 原子替换。详见 reference/hotfix/update_app.md
# 环境变量：APP_URL / STATIC_URL（至少一个非空），PROJECT_ROOT（默认 .）
# stdout：JSON  退出码：0=成功 1=无更新 2=状态异常 3=执行失败
set -uo pipefail
ROOT="${PROJECT_ROOT:-.}"
STATE="$ROOT/.qianwenai-deploy"

[ -f "$STATE" ] || { echo "找不到 ${STATE}，请先完成首次部署" >&2; exit 2; }
EVAL=$(python3 - "$STATE" <<'PY'
import json, shlex, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
except Exception as e:
    sys.stderr.write(f"状态文件解析失败：{e}\n"); sys.exit(2)

def need(k):
    v = d.get(k)
    if not v:
        sys.stderr.write(f"状态文件缺少字段 '{k}'\n"); sys.exit(2)
    return v

region = need("region_id")
outputs = d.get("outputs") or {}
ecs_raw = outputs.get("ecs_instance_ids") or []
if isinstance(ecs_raw, str):
    ecs_raw = [x.strip() for x in ecs_raw.split(",") if x.strip()]
if not ecs_raw:
    sys.stderr.write("状态文件缺少 outputs.ecs_instance_ids\n"); sys.exit(2)

app_type = d.get("app_type") or ""
runtime = d.get("runtime") or "none"
nginx_mode = d.get("nginx_mode") or ""
bucket = d.get("artifact_bucket") or ""
public_ip = outputs.get("public_ip") or ""
app_mode = d.get("app_mode") or "docker-image"
image_name = d.get("app_image_name") or "qianwenai-app:latest"
app_port = d.get("app_port") or ""

vals = {
    "REGION": region,
    "ECS_IDS": " ".join(ecs_raw),
    "APP_TYPE": app_type,
    "RUNTIME": runtime,
    "NGINX_MODE": nginx_mode,
    "BUCKET": bucket,
    "PUBLIC_IP": public_ip,
    "APP_MODE": app_mode,
    "IMAGE_NAME": image_name,
    "APP_PORT": str(app_port),
}
for k, v in vals.items():
    print(f"{k}={shlex.quote(str(v))}")
PY
) || { echo "[update] 读取状态文件失败" >&2; exit 2; }
eval "$EVAL"

APP_URL="${APP_URL:-}"
STATIC_URL="${STATIC_URL:-}"

if [ -z "$APP_URL" ] && [ -z "$STATIC_URL" ]; then
  echo "[update] APP_URL 和 STATIC_URL 均为空，无内容可更新" >&2
  exit 1
fi

# 生成 docker 应用的热更新片段（在 ECS 上执行）。
# 依赖调用方已展开的变量：APP_MODE / IMAGE_NAME / APP_PORT / APP_URL。
# 与 templates/userdata/docker.sh 的首装逻辑保持一致：
#   docker-image  → 下载 tar.gz → docker load → 重启 systemd 托管的 qianwenai-app 容器
#   docker-compose→ 下载 tar.gz（含 compose.yml + 上下文/镜像）→ docker compose up -d --build
gen_app_update_docker() {
  local port="${APP_PORT:-8080}"
  local s="
# === 阶段 1：下载新产物到暂存区（旧容器继续运行） ===
echo '[update] 下载+验证新 docker 产物'
STAGING_DIR=/opt/qianwenai.staging
rm -rf \"\$STAGING_DIR\"
mkdir -p \"\$STAGING_DIR\"
curl -fsSL '$APP_URL' -o \"\$STAGING_DIR/app.tar.gz\"
tar -tzf \"\$STAGING_DIR/app.tar.gz\" >/dev/null
tar -xzf \"\$STAGING_DIR/app.tar.gz\" -C \"\$STAGING_DIR\"
rm -f \"\$STAGING_DIR/app.tar.gz\"

APP_PORT='$port'
DB_ENV_OPT=''
[ -f /etc/qianwenai/db.env ] && DB_ENV_OPT='--env-file /etc/qianwenai/db.env'
"

  if [ "$APP_MODE" = "docker-compose" ]; then
    s+="
# === docker-compose 模式：切换到新目录并重建 ===
if ! docker compose version >/dev/null 2>&1; then
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \\
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

# 保留旧目录用于回滚；先停旧 compose 再原子替换目录
echo '[update] 停止旧 compose 服务'
if [ -f /opt/qianwenai/docker-compose.yml ]; then
  (cd /opt/qianwenai && docker compose -f docker-compose.yml down) || true
fi
rm -rf /opt/qianwenai.prev
if [ -d /opt/qianwenai ]; then mv /opt/qianwenai /opt/qianwenai.prev; fi
mv \"\$STAGING_DIR\" /opt/qianwenai
cd /opt/qianwenai
# 保持 .env 权限与 db.env（0600）一致：db.env 含数据库密码，若用 cp 复制成默认 0644
# 会把密钥暴露成可被同机其他用户读取。install -m 600 一步到位地按 0600 落盘。
[ -f /etc/qianwenai/db.env ] && install -m 600 /etc/qianwenai/db.env ./.env
echo '[update] 启动新 compose 服务'
# set -e 下若 up 失败会直接退出，跳过后面的健康检查/回滚；兜住交给健康检查判定。
docker compose -f docker-compose.yml up -d --build || echo '[update] compose up 返回非零，转入健康检查/回滚'
"
  else
    s+="
# === docker-image 模式：docker load 新镜像并重启容器 ===
cd \"\$STAGING_DIR\"
if [ ! -f image.tar ]; then
  echo '[update] 产物中未找到 image.tar，无法 docker load' >&2
  exit 1
fi
# 记录旧镜像 ID 以便回滚
PREV_IMAGE_ID=\$(docker images -q '$IMAGE_NAME' 2>/dev/null | head -1)
echo \"\$PREV_IMAGE_ID\" > /opt/qianwenai.prev-image 2>/dev/null || true
echo '[update] docker load 新镜像'
# docker load 的输出形如 'Loaded image: repo:tag'，据此拿到新镜像的真实 tag/ID。
LOAD_OUT=\$(docker load -i image.tar)
echo \"\$LOAD_OUT\"
# 关键：新产物的镜像 tag 未必等于状态文件里固定的 IMAGE_NAME（'$IMAGE_NAME'）。
# 若不重打 tag，systemd unit 的 ExecStart 仍按旧 tag 跑，docker load 完却启动旧镜像，
# 表现为"健康检查通过、静默空转在旧版本"。这里强制把刚加载的镜像重打成 IMAGE_NAME。
LOADED_REF=\$(echo \"\$LOAD_OUT\" | sed -n 's/^Loaded image: //p' | head -1)
if [ -z \"\$LOADED_REF\" ]; then
  # 兼容 'Loaded image ID: sha256:...' 的输出形式
  LOADED_REF=\$(echo \"\$LOAD_OUT\" | sed -n 's/^Loaded image ID: //p' | head -1)
fi
if [ -n \"\$LOADED_REF\" ] && [ \"\$LOADED_REF\" != '$IMAGE_NAME' ]; then
  echo \"[update] 将新镜像 \$LOADED_REF 重打为 $IMAGE_NAME\"
  docker tag \"\$LOADED_REF\" '$IMAGE_NAME'
fi

# 与首装一致：用 systemd 托管的 qianwenai-app 容器，确保 unit 存在
if [ ! -f /etc/systemd/system/qianwenai-app.service ]; then
  cat > /etc/systemd/system/qianwenai-app.service <<UNIT
[Unit]
Description=qianwenai app container
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker rm -f qianwenai-app
ExecStart=/usr/bin/docker run --rm --name qianwenai-app -p \${APP_PORT}:\${APP_PORT} \${DB_ENV_OPT} $IMAGE_NAME
ExecStop=/usr/bin/docker stop qianwenai-app

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable qianwenai-app
fi

# 保留新产物目录（含 image.tar）供排查，替换旧的
rm -rf /opt/qianwenai.prev
if [ -d /opt/qianwenai ]; then mv /opt/qianwenai /opt/qianwenai.prev; fi
mv \"\$STAGING_DIR\" /opt/qianwenai
echo '[update] 重启容器'
# 注意：脚本头部是 set -euxo pipefail。systemctl restart 失败若不兜住，会立即退出，
# 下面的健康检查+回滚块将永远不可达；而 ExecStartPre 已用 docker rm -f 删掉旧容器，
# 结果是新旧都没跑。这里用 '|| true' 兜住，把成败判定交给随后的健康检查/回滚逻辑。
systemctl restart qianwenai-app || echo '[update] systemctl restart 返回非零，转入健康检查/回滚'
"
  fi

  # === 健康检查（docker 通用） ===
  s+="
echo '[update] 健康检查...'
sleep 3
HEALTHY=0
for _i in \$(seq 1 15); do
  if curl -sf -o /dev/null --max-time 5 \"http://localhost:\${APP_PORT}/\"; then
    HEALTHY=1
    break
  fi
  sleep 2
done
if [ \"\$HEALTHY\" -eq 0 ]; then
  echo '[update] 健康检查失败，回滚到上一版本'
"
  if [ "$APP_MODE" = "docker-compose" ]; then
    s+="
  if [ -d /opt/qianwenai.prev ]; then
    (cd /opt/qianwenai && docker compose -f docker-compose.yml down) || true
    rm -rf /opt/qianwenai.failed
    mv /opt/qianwenai /opt/qianwenai.failed || true
    mv /opt/qianwenai.prev /opt/qianwenai
    (cd /opt/qianwenai && docker compose -f docker-compose.yml up -d --build) || true
    for _i in \$(seq 1 15); do
      if curl -sf -o /dev/null --max-time 5 \"http://localhost:\${APP_PORT}/\"; then
        echo '[update] 回滚成功，已恢复上一版本（新产物留在 /opt/qianwenai.failed 供排查）'
        exit 1
      fi
      sleep 2
    done
    echo '[update] 回滚后健康检查仍失败，请人工介入'
  else
    echo '[update] 无可用备份，无法自动回滚'
  fi
"
  else
    s+="
  PREV_IMAGE_ID=\$(cat /opt/qianwenai.prev-image 2>/dev/null || true)
  if [ -n \"\$PREV_IMAGE_ID\" ]; then
    echo '[update] 回滚镜像标签到上一版本'
    docker tag \"\$PREV_IMAGE_ID\" '$IMAGE_NAME' || true
    rm -rf /opt/qianwenai.failed
    if [ -d /opt/qianwenai ]; then mv /opt/qianwenai /opt/qianwenai.failed || true; fi
    if [ -d /opt/qianwenai.prev ]; then mv /opt/qianwenai.prev /opt/qianwenai; fi
    systemctl restart qianwenai-app || echo '[update] 回滚重启返回非零，继续探活判定'
    for _i in \$(seq 1 15); do
      if curl -sf -o /dev/null --max-time 5 \"http://localhost:\${APP_PORT}/\"; then
        echo '[update] 回滚成功，已恢复上一版本（新产物留在 /opt/qianwenai.failed 供排查）'
        exit 1
      fi
      sleep 2
    done
    echo '[update] 回滚后健康检查仍失败，请人工介入'
  else
    echo '[update] 无可用旧镜像，无法自动回滚'
  fi
"
  fi
  s+="
  exit 1
fi
echo '[update] 健康检查通过'
"
  echo "$s"
}

gen_update_script() {
  local script="#!/bin/bash
set -euxo pipefail
exec >> /var/log/qianwenai-update.log 2>&1
echo \"[\$(date -u +%FT%TZ)] === qianwenai update start ===\"
"

  # 应用产物更新：按 APP_TYPE 分派。docker 与 systemd 的原子替换机制完全不同，
  # 早先版本无视 APP_TYPE 一律生成 systemd 脚本，导致 docker 部署热更新静默失败。
  if [ -n "$APP_URL" ] && [ "$APP_TYPE" = "docker" ]; then
    script+="$(gen_app_update_docker)"
  elif [ -n "$APP_URL" ]; then
    script+="
# === 阶段 1：下载新产物到暂存区（服务继续运行） ===
echo '[update] 下载+验证新产物'
STAGING_DIR=/opt/qianwenai.staging
rm -rf \"\$STAGING_DIR\"
mkdir -p \"\$STAGING_DIR\"
curl -fsSL '$APP_URL' -o \"\$STAGING_DIR/app.tar.gz\"
tar -tzf \"\$STAGING_DIR/app.tar.gz\" >/dev/null
tar -xzf \"\$STAGING_DIR/app.tar.gz\" -C \"\$STAGING_DIR\"
rm -f \"\$STAGING_DIR/app.tar.gz\"
"

    # 依赖预热：在暂存区提前拉好依赖，服务此时仍在跑，尽量缩短阶段 2 的停机窗口。
    case "$RUNTIME" in
      python)
        script+="
cd \"\$STAGING_DIR\"
if [ -f requirements.txt ]; then
  echo '[update] 预下载 Python 依赖包（服务不受影响）'
  mkdir -p /tmp/qianwenai-pip-cache
  python3 -m pip download -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -d /tmp/qianwenai-pip-cache -r requirements.txt
  echo '[update] Python 依赖预下载完成'
fi
"
        ;;
      node)
        script+="
cd \"\$STAGING_DIR\"
if [ -f package.json ]; then
  echo '[update] 预安装 Node 依赖（暂存区，服务不受影响）'
  rm -rf node_modules
  yarn install --production --registry=https://registry.npmmirror.com
  echo '[update] Node 依赖安装完成'
fi
"
        ;;
    esac

    script+="
# === 阶段 2：原子切换 ===
echo '[update] 停止服务'
systemctl stop qianwenai-app || true
echo '[update] 原子替换'
# 保留旧版本用于回滚，不直接删除；上一轮遗留的备份先清掉。
rm -rf /opt/qianwenai.prev
if [ -d /opt/qianwenai ]; then mv /opt/qianwenai /opt/qianwenai.prev; fi
mv \"\$STAGING_DIR\" /opt/qianwenai
"

    # Python 依赖必须在切换后、启动前装进新目录；用阶段 1 的缓存离线安装，不再走网络。
    case "$RUNTIME" in
      python)
        script+="
cd /opt/qianwenai
if [ -f requirements.txt ] && [ -d /tmp/qianwenai-pip-cache ]; then
  echo '[update] 离线安装 Python 依赖（使用预下载缓存）'
  python3 -m pip install --no-cache-dir --no-index --find-links /tmp/qianwenai-pip-cache -r requirements.txt
  rm -rf /tmp/qianwenai-pip-cache
fi
"
        ;;
    esac

    script+="
echo '[update] 启动服务'
# set -e 下 restart 失败会直接退出、跳过健康检查+回滚；兜住交给健康检查判定。
systemctl restart qianwenai-app || echo '[update] systemctl restart 返回非零，转入健康检查/回滚'

# === 健康检查 ===
echo '[update] 健康检查...'
APP_PORT=\$(sed -n 's/^Environment=PORT=//p' /etc/systemd/system/qianwenai-app.service 2>/dev/null | head -1)
APP_PORT=\${APP_PORT:-8080}
sleep 3
HEALTHY=0
for _i in \$(seq 1 15); do
  if curl -sf -o /dev/null --max-time 5 \"http://localhost:\${APP_PORT}/\"; then
    HEALTHY=1
    break
  fi
  sleep 2
done
if [ \"\$HEALTHY\" -eq 0 ]; then
  echo '[update] 健康检查失败，回滚到上一版本'
  if [ -d /opt/qianwenai.prev ]; then
    systemctl stop qianwenai-app || true
    rm -rf /opt/qianwenai.failed
    mv /opt/qianwenai /opt/qianwenai.failed || true
    mv /opt/qianwenai.prev /opt/qianwenai
    systemctl restart qianwenai-app || echo '[update] 回滚重启返回非零，继续探活判定'
    for _i in \$(seq 1 15); do
      if curl -sf -o /dev/null --max-time 5 \"http://localhost:\${APP_PORT}/\"; then
        echo '[update] 回滚成功，已恢复上一版本（新产物留在 /opt/qianwenai.failed 供排查）'
        exit 1
      fi
      sleep 2
    done
    echo '[update] 回滚后健康检查仍失败，请人工介入'
  else
    echo '[update] 无可用备份，无法自动回滚'
  fi
  exit 1
fi
echo '[update] 健康检查通过'
"
  fi

  if [ -n "$STATIC_URL" ]; then
    script+="
# === 静态文件更新（零停机） ===
echo '[update] 下载静态产物'
STATIC_STAGING=/var/www/static.staging
rm -rf \"\$STATIC_STAGING\"
mkdir -p \"\$STATIC_STAGING\"
curl -fsSL '$STATIC_URL' -o /tmp/static.tar.gz
tar -tzf /tmp/static.tar.gz >/dev/null
tar -xzf /tmp/static.tar.gz -C \"\$STATIC_STAGING\" --strip-components=0
rm -f /tmp/static.tar.gz
rm -rf /var/www/static
mv \"\$STATIC_STAGING\" /var/www/static
nginx -t && systemctl reload nginx
echo '[update] 静态文件更新完成'
"
  fi

  script+="
rm -rf /opt/qianwenai.staging /var/www/static.staging /tmp/qianwenai-pip-cache 2>/dev/null || true
echo \"[\$(date -u +%FT%TZ)] === qianwenai update complete ===\"
"
  echo "$script"
}

UPDATE_SCRIPT=$(gen_update_script)

run_on_instance() {
  local instance_id="$1"
  echo "[update] 下发更新命令到 $instance_id ..." >&2

  local out
  out=$(PAGER=cat aliyun ecs RunCommand \
    --RegionId "$REGION" \
    --InstanceId.1 "$instance_id" \
    --Type RunShellScript \
    --CommandContent "$UPDATE_SCRIPT" \
    --Timeout 300 2>&1)
  local code=$?
  if [ $code -ne 0 ]; then
    echo "[update] RunCommand 失败：$out" >&2
    return 3
  fi

  local invoke_id
  invoke_id=$(echo "$out" | python3 -c "import json,sys;print(json.load(sys.stdin).get('InvokeId',''))" 2>/dev/null)
  if [ -z "$invoke_id" ]; then
    echo "[update] 无法解析 InvokeId：$out" >&2
    return 3
  fi
  echo "[update] InvokeId=${invoke_id}，等待执行完成..." >&2

  local deadline=$(( $(date +%s) + 300 ))
  local status=""
  local wait=2

  while [ $(date +%s) -lt $deadline ]; do
    local inv_out
    inv_out=$(PAGER=cat aliyun ecs DescribeInvocations \
      --RegionId "$REGION" \
      --InvokeId "$invoke_id" 2>&1) || { sleep "$wait"; continue; }

    status=$(echo "$inv_out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    invs = d.get('Invocations', {}).get('Invocation', [])
    if invs:
        instances = invs[0].get('InvokeInstances', {}).get('InvokeInstance', [])
        if instances:
            print(instances[0].get('InvocationStatus', ''))
except: pass
" 2>/dev/null)

    case "$status" in
      Finished|Success)
        echo "[update] $instance_id 更新完成" >&2

        local result_out
        result_out=$(PAGER=cat aliyun ecs DescribeInvocationResults \
          --RegionId "$REGION" \
          --InvokeId "$invoke_id" 2>&1)
        local output_b64
        output_b64=$(echo "$result_out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    items = d.get('Invocation', {}).get('InvocationResults', {}).get('InvocationResult', [])
    if items: print(items[0].get('Output', ''))
except: pass
" 2>/dev/null)
        if [ -n "$output_b64" ]; then
          echo "[update] === 远程输出 ===" >&2
          echo "$output_b64" | base64 -d 2>/dev/null >&2 || true
          echo "[update] === 远程输出结束 ===" >&2
        fi
        echo "$invoke_id"
        return 0
        ;;
      Failed)
        echo "[update] $instance_id 执行失败" >&2

        local err_out
        err_out=$(PAGER=cat aliyun ecs DescribeInvocationResults \
          --RegionId "$REGION" \
          --InvokeId "$invoke_id" 2>&1)
        local err_b64
        err_b64=$(echo "$err_out" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    items = d.get('Invocation', {}).get('InvocationResults', {}).get('InvocationResult', [])
    if items: print(items[0].get('Output', ''))
except: pass
" 2>/dev/null)
        if [ -n "$err_b64" ]; then
          echo "[update] === 错误输出 ===" >&2
          echo "$err_b64" | base64 -d 2>/dev/null >&2 || true
        fi
        return 3
        ;;
      *)
        echo "[update] $(date -u +%H:%M:%S) $instance_id status=$status" >&2
        ;;
    esac
    sleep "$wait"; [ "$wait" -lt 10 ] && wait=$((wait + 2))
  done

  echo "[update] $instance_id 执行超时（5 分钟）" >&2
  return 3
}

UPDATED_INSTANCES=()
INVOKE_IDS=()
FAILED=0

IFS=' ' read -r -a ECS_ARRAY <<< "$ECS_IDS"
ecs="${ECS_ARRAY[0]}"
invoke_id=$(run_on_instance "$ecs") || FAILED=1
if [ $FAILED -eq 0 ]; then
  UPDATED_INSTANCES+=("$ecs")
  INVOKE_IDS+=("$invoke_id")
fi

if [ $FAILED -ne 0 ]; then
  echo "[update] 更新失败" >&2
  exit 3
fi

python3 - "$STATE" "$APP_URL" "$STATIC_URL" <<'PY'
import json, os, sys
from datetime import datetime, timezone

path, new_app, new_static = sys.argv[1:4]
with open(path, encoding="utf-8") as f:
    state = json.load(f)

# 记录被替换掉的产物 URL，便于人工回滚 / 追溯上一版本。
prev = state.get("current_artifact_urls") or {}

state["current_artifact_urls"] = {}
if new_app:
    state["current_artifact_urls"]["app_url"] = new_app
if new_static:
    state["current_artifact_urls"]["static_url"] = new_static

state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
if prev:
    state["previous_artifact_urls"] = prev
else:
    state.pop("previous_artifact_urls", None)

with open(path, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
# 状态文件含签名 URL（current_artifact_urls），保持 0600，避免同机其他用户读取。
os.chmod(path, 0o600)
sys.stderr.write(f"[update] 状态文件已更新 updated_at\n")
PY

python3 - "${UPDATED_INSTANCES[@]}" -- "${INVOKE_IDS[@]}" <<'PY'
import json, sys
args = sys.argv[1:]
sep = args.index("--")
instances = args[:sep]
invoke_ids = args[sep+1:]
print(json.dumps({
    "status": "success",
    "updated_instances": instances,
    "invoke_ids": invoke_ids,
}, ensure_ascii=False))
PY
