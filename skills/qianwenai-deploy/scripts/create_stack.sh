#!/usr/bin/env bash
# 创建 ROS 栈（重试安全）。详见 reference/deploy/11_create_stack.md
# 用法：./create_stack.sh <region> <template-url> <stack-name> <params-file>
# 环境变量：APP_NAME APP_DESC [PROJECT_ROOT] [TIMEOUT_MIN]
# stdout：StackId  退出码：0 成功
set -uo pipefail

usage() {
  echo "Usage: $0 <region> <template-url> <stack-name> <params-file>" >&2
  exit 64
}
[ $# -eq 4 ] || usage
REGION="$1"; TPL_URL="$2"; NAME="$3"; PARAMS_FILE="$4"
: "${APP_NAME:?missing APP_NAME}"
: "${APP_DESC:?missing APP_DESC}"
[ -f "$PARAMS_FILE" ] || { echo "params-file not found: $PARAMS_FILE" >&2; exit 1; }
PROJECT_ROOT="${PROJECT_ROOT:-.}"
# ROS 侧超时：无 RDS 时 ECS+EIP 通常 2-5 分钟就绪，15 分钟已很宽裕；含 RDS 传 40。
# 注意与 wait_and_probe.py 的 --max-wait 保持「客户端 > ROS」的关系，
# 否则 ROS 已判失败、客户端还在傻等。
TIMEOUT="${TIMEOUT_MIN:-15}"

# 从 JSON 文件构建 --Parameters.N.ParameterKey / Value 参数。
# 用 NUL 分隔而非 tab/换行：参数值可能自带换行或 tab（如 APP_DESC、UserDataScript），
# 按行读会把一个值拆成多条、凭空多出参数。文件路径通过 argv 传入，避免路径里的
# 引号破坏内嵌 Python 代码。
PARAMS=()
while IFS= read -r -d '' key && IFS= read -r -d '' val; do
  n=$(( ${#PARAMS[@]} / 4 + 1 ))
  PARAMS+=("--Parameters.${n}.ParameterKey" "$key" "--Parameters.${n}.ParameterValue" "$val")
done < <(python3 - "$PARAMS_FILE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    params = json.load(f)
out = sys.stdout
for p in params:
    out.write(str(p["key"]) + "\0" + str(p["value"]) + "\0")
PY
)
[ ${#PARAMS[@]} -gt 0 ] || { echo "params-file 未解析出任何参数：$PARAMS_FILE" >&2; exit 1; }

# ─── 重试安全：先检查是否已存在同名栈 ───────────────────────────────
EXISTING_SID=""
echo "[create] 检查是否已存在同名栈：$NAME" >&2
EXISTING=$(aliyun ros ListStacks \
  --RegionId "$REGION" \
  --StackName.1 "$NAME" \
  --Status.1 CREATE_IN_PROGRESS \
  --Status.2 CREATE_COMPLETE \
  --Status.3 CREATE_FAILED \
  --PageSize 1 2>&1) || true

EXISTING_SID=$(echo "$EXISTING" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    stacks = d.get('Stacks', [])
    if stacks:
        s = stacks[0]
        if s.get('Status') in ('CREATE_IN_PROGRESS', 'CREATE_COMPLETE'):
            print(s.get('StackId', ''))
except Exception:
    pass
" 2>/dev/null)

if [ -n "$EXISTING_SID" ]; then
  echo "[create] 发现同名栈 ${EXISTING_SID}（${NAME}），复用" >&2
  STACK_ID="$EXISTING_SID"
else
  # 若存在同名的 CREATE_FAILED 栈，先删掉以释放名字
  FAILED_SID=$(echo "$EXISTING" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    stacks = d.get('Stacks', [])
    if stacks and stacks[0].get('Status') == 'CREATE_FAILED':
        print(stacks[0].get('StackId', ''))
except Exception:
    pass
" 2>/dev/null)
  if [ -n "$FAILED_SID" ]; then
    echo "[create] 删除此前失败的栈 $FAILED_SID" >&2
    aliyun ros DeleteStack --RegionId "$REGION" --StackId "$FAILED_SID" >/dev/null 2>&1 || true
    sleep 5
  fi

  # 创建新栈
  OUT=$(aliyun ros CreateStack \
    --RegionId "$REGION" \
    --StackName "$NAME" \
    --TemplateURL "$TPL_URL" \
    --DisableRollback false \
    --TimeoutInMinutes "$TIMEOUT" \
    --Tags.1.Key from                --Tags.1.Value qianwenai \
    --Tags.2.Key qianwenai-appName  --Tags.2.Value "$APP_NAME" \
    --Tags.3.Key qianwenai-appDesc  --Tags.3.Value "$APP_DESC" \
    "${PARAMS[@]}" 2>&1)
  CODE=$?
  if [ $CODE -ne 0 ]; then
    echo "[create] CreateStack CLI 报错（code=${CODE}），检查服务端……" >&2
    sleep 3
    FALLBACK=$(aliyun ros ListStacks \
      --RegionId "$REGION" --StackName.1 "$NAME" \
      --Status.1 CREATE_IN_PROGRESS --Status.2 CREATE_COMPLETE \
      --PageSize 1 2>&1) || true
    STACK_ID=$(echo "$FALLBACK" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    stacks = d.get('Stacks', [])
    if stacks: print(stacks[0].get('StackId', ''))
except Exception: pass
" 2>/dev/null)
    if [ -n "$STACK_ID" ]; then
      echo "[create] CLI 报错，但栈已在服务端创建：$STACK_ID" >&2
    else
      echo "$OUT" >&2; exit $CODE
    fi
  else
    STACK_ID=$(echo "$OUT" | python3 -c "import json,sys
try: print(json.load(sys.stdin)['StackId'])
except: pass")
    [ -z "$STACK_ID" ] && { echo "无法解析 StackId" >&2; echo "$OUT" >&2; exit 1; }
  fi
fi

# 临时状态文件：即便后续中断，delete_stack.sh 也能据此清理
python3 - "$PROJECT_ROOT" "$STACK_ID" "$NAME" "$REGION" <<'PY' || true
import datetime, json, os, sys
root, sid, name, region = sys.argv[1:5]
state = {
    "version": 1, "stack_id": sid, "stack_name": name, "region_id": region,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    "tags": [{"Key": "from", "Value": "qianwenai"}, {"Key": "qianwenai-appName", "Value": os.environ.get("APP_NAME", "")}],
    "provisional": True,
}
with open(os.path.join(root, ".qianwenai-deploy"), "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
PY

echo "$STACK_ID"
