#!/usr/bin/env python3
"""等待 ROS 栈终态 + 探活。详见 reference/deploy/12_wait_stack.md

用法:
    python3 scripts/wait_and_probe.py \
        --region cn-hangzhou \
        --stack-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
        [--has-app] \
        [--max-wait 1200] \
        [--probe-retries 15] \
        [--probe-interval 4]

输出 (stdout): JSON 结构
    成功: {"status":"ok","public_ip":"...","instance_id":"...","outputs":{...},"health":{"nginx":"pass","app":"manual"},"elapsed_seconds":180}
    失败: {"status":"failed","stage":"...","error":"...","public_ip":"...","instance_id":"...","elapsed_seconds":...}

心跳播报输出到 stderr。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error


def run_cli(cmd: list[str]) -> tuple[int, str]:
    """执行 aliyun CLI 命令，返回 (returncode, stdout)"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        # 单次 CLI 调用卡住 ≠ 部署失败（此时资源已在创建、已开始计费）。
        # 当成临时错误交给调用方重试，绝不能让长轮询进程直接崩掉。
        return 1, "aliyun CLI 调用超时（30s），将重试"
    return result.returncode, result.stdout + result.stderr


def backoff(base: int, attempt: int, cap: int = 12) -> int:
    """探活重试间隔：前几次密集、逐步拉长，上限 cap 秒。"""
    return min(base * attempt, cap)


# 永久性错误关键字：一旦命中就没有重试价值，立即失败而不是空转到 TIMEOUT。
# 覆盖鉴权/权限/参数类错误（凭证过期、AK 无效、无权限、地域/参数非法等）。
FATAL_ERROR_MARKERS = (
    "InvalidAccessKeyId", "SignatureDoesNotMatch", "Forbidden",
    "NoPermission", "Unauthorized", "InvalidSecurityToken",
    "AccessDenied", "InvalidRegionId", "MissingParameter",
    "InvalidParameter", "SDK.InvalidCredential", "SDK.CanNotResolveEndpoint",
)


def get_stack(region: str, stack_id: str) -> tuple[str, dict]:
    """调用 GetStack。

    返回 (kind, data)：
      - ("ok", <解析后的 JSON>)   正常拿到栈
      - ("not_found", {})          明确的 StackNotFound / 404
      - ("fatal", {"error": ...})  永久性错误（鉴权/权限/参数），不应重试
      - ("transient", {})          网络抖动等临时错误，调用方应重试
    """
    rc, out = run_cli([
        "aliyun", "ros", "GetStack",
        "--RegionId", region,
        "--StackId", stack_id,
    ])
    if rc != 0:
        if any(marker in out for marker in FATAL_ERROR_MARKERS):
            return "fatal", {"error": out.strip()[:500]}
        if "StackNotFound" in out or "404" in out:
            return "not_found", {}
        # 网络抖动等临时错误，让调用方重试
        return "transient", {}
    try:
        return "ok", json.loads(out)
    except json.JSONDecodeError:
        return "transient", {}


def extract_outputs(stack_data: dict) -> dict:
    """从 GetStack 的 Outputs 提取 key-value"""
    outputs = {}
    for item in stack_data.get("Outputs", []):
        key = item.get("OutputKey", "")
        val = item.get("OutputValue", "")
        outputs[key] = val
    return outputs


def heartbeat(msg: str):
    """输出心跳到 stderr"""
    print(f"[heartbeat] {msg}", file=sys.stderr, flush=True)


def probe_url(url: str, timeout: int = 10) -> tuple[bool, int | None]:
    """HTTP GET 探活。

    返回 (reachable, status_code)：
      - reachable=True 表示拿到了 HTTP 响应（含 4xx/5xx），status_code 为状态码
      - reachable=False 表示连接层面失败（超时/拒绝/DNS），status_code 为 None
    仅用于探 nginx /healthz；是否"通过"由调用方判定（只有 2xx/3xx 才算 nginx 就绪）。
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.status
    except urllib.error.HTTPError as e:
        # 拿到了 HTTP 响应（如 404/500），reachable=True，交给调用方判定。
        return True, e.code
    except Exception:
        return False, None


def wait_for_terminal(region: str, stack_id: str, max_wait: int) -> tuple[str, dict]:
    """轮询 GetStack 至终态，返回 (terminal_status, stack_data)"""
    terminal_states = {
        "CREATE_COMPLETE", "UPDATE_COMPLETE",
        "CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED",
        "DELETE_COMPLETE", "DELETE_FAILED",
    }
    start = time.time()
    # 瞬时 StackNotFound：刚 CreateStack 后控制面可能短暂查不到（最终一致性），
    # 不能一见 not_found 就判 DELETE_COMPLETE。连续多次仍 not_found 才认为确已删除。
    not_found_streak = 0
    NOT_FOUND_TOLERANCE = 3

    while True:
        elapsed = int(time.time() - start)
        if elapsed > max_wait:
            return "TIMEOUT", {}
        # 前 2 分钟密集轮询（ECS+EIP 常在 2 分钟内就绪），之后放缓省 API 调用
        poll_interval = 5 if elapsed < 120 else 10

        kind, data = get_stack(region, stack_id)
        if kind == "fatal":
            # 永久性错误（凭证/权限/参数）：立即失败，不再空转到 TIMEOUT。
            return "FATAL_ERROR", data
        if kind == "not_found":
            not_found_streak += 1
            if not_found_streak >= NOT_FOUND_TOLERANCE:
                return "DELETE_COMPLETE", {}
            heartbeat(f"已等待 {elapsed}s，栈暂时查不到（第 {not_found_streak} 次），可能是控制面延迟，将重试...")
            time.sleep(poll_interval)
            continue
        not_found_streak = 0
        if kind == "transient":
            # 临时错误，继续轮询
            heartbeat(f"已等待 {elapsed}s，获取栈状态时出错，将重试...")
            time.sleep(poll_interval)
            continue

        status = data.get("Status", "")
        if status in terminal_states:
            return status, data

        heartbeat(f"已等待 {elapsed}s，当前状态: {status}")
        time.sleep(poll_interval)


def health_check(public_ip: str, has_app: bool, retries: int, interval: int) -> dict:
    """仅探 nginx，返回 {"nginx": "pass"/"fail", "app": "manual"/"skip"}。

    nginx 就绪用 HTTP /healthz 探测。应用存活由云助手在实例上读应用日志人工判断
    （HTTP 探测应用会假阴性，例如 Spring Boot 对未映射路径返回 500，而应用其实是 UP 的）
    —— 见 reference/rules/rule_error_handling.md。
    """
    result = {"nginx": "fail", "app": "skip"}

    # Nginx 就绪: /healthz —— 由 nginx 直接 `return 200 "ok"`。
    # 只有拿到 2xx/3xx 才算 nginx 就绪；4xx/5xx 都说明 nginx 配置未生效或异常，
    # 不能把 404（配置没加载）当成 pass。
    healthz_url = f"http://{public_ip}/healthz"
    for i in range(1, retries + 1):
        reachable, code = probe_url(healthz_url, timeout=10)
        if reachable and code is not None and code < 400:
            result["nginx"] = "pass"
            heartbeat(f"Nginx 探活通过 (第 {i} 次, status={code})")
            break
        heartbeat(f"Nginx 探活第 {i}/{retries} 次失败 (status={code})")
        if i < retries:
            time.sleep(backoff(interval, i))

    if result["nginx"] != "pass":
        return result

    # 应用存活：不走 HTTP 探测，标记为待人工核验（用云助手读实例上的应用日志判断）。
    if has_app:
        result["app"] = "manual"
    return result


def main():
    parser = argparse.ArgumentParser(description="等待 ROS 栈终态 + 探活")
    parser.add_argument("--region", required=True, help="地域 ID")
    parser.add_argument("--stack-id", required=True, help="栈 ID")
    parser.add_argument("--has-app", action="store_true",
                        help="标记 app:\"manual\"，由 Agent 用云助手核验应用")
    parser.add_argument("--max-wait", type=int, default=1200,
                        help="最长等待秒数 (默认 1200=20min；含 RDS 传 2700=45min)")
    parser.add_argument("--probe-retries", type=int, default=15, help="探活重试次数 (默认 15)")
    parser.add_argument("--probe-interval", type=int, default=4,
                        help="探活重试基础间隔秒数 (默认 4，按次数递增，上限 12s)")
    args = parser.parse_args()

    start_time = time.time()

    # 阶段 1: 等待栈终态
    heartbeat("开始轮询栈状态...")
    terminal_status, stack_data = wait_for_terminal(args.region, args.stack_id, args.max_wait)
    elapsed = int(time.time() - start_time)

    # 失败/删除/超时/永久错误
    if terminal_status not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        if terminal_status == "FATAL_ERROR":
            output = {
                "status": "failed",
                "stage": "stack_query",
                "error": "查询栈状态遇到永久性错误（鉴权/权限/参数），已提前终止，"
                         "请检查 aliyun CLI 凭证/权限/地域配置",
                "stack_status": terminal_status,
                "detail": stack_data.get("error", ""),
                "elapsed_seconds": elapsed,
            }
        else:
            output = {
                "status": "failed",
                "stage": "stack_create",
                "error": f"栈终态: {terminal_status}",
                "stack_status": terminal_status,
                "elapsed_seconds": elapsed,
            }
            # 尝试提取失败原因
            if stack_data:
                output["status_reason"] = stack_data.get("StatusReason", "")
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    # 栈成功 — 提取 Outputs
    outputs = extract_outputs(stack_data)
    public_ip = outputs.get("PublicIp", outputs.get("EipAddress", ""))
    # 模板 Output 是 EcsInstanceIds（逗号分隔的列表）；取第一个作为展示用实例 ID。
    # 兼容早期可能存在的 InstanceId / EcsInstanceId 单数键。
    ecs_ids_raw = outputs.get("EcsInstanceIds") or outputs.get("InstanceId") \
        or outputs.get("EcsInstanceId") or ""
    instance_id = str(ecs_ids_raw).split(",")[0].strip() if ecs_ids_raw else ""

    if not public_ip:
        output = {
            "status": "failed",
            "stage": "extract_outputs",
            "error": "栈成功但未找到 PublicIp/EipAddress",
            "outputs": outputs,
            "elapsed_seconds": int(time.time() - start_time),
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    heartbeat(f"栈创建成功! IP: {public_ip}, 开始探活...")

    # 阶段 2: 探活
    health = health_check(public_ip, args.has_app, args.probe_retries,
                          args.probe_interval)
    elapsed = int(time.time() - start_time)

    if health["nginx"] != "pass":
        output = {
            "status": "failed",
            "stage": "health_check",
            "error": f"Nginx 探活失败 ({args.probe_retries} 次重试后)",
            "public_ip": public_ip,
            "instance_id": instance_id,
            "outputs": outputs,
            "health": health,
            "elapsed_seconds": elapsed,
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(1)

    # Nginx 已通过。应用存活（health["app"] == "manual"）由云助手在实例上读日志人工核验
    # —— 见 reference/rules/rule_error_handling.md。
    output = {
        "status": "ok",
        "public_ip": public_ip,
        "instance_id": instance_id,
        "outputs": outputs,
        "health": health,
        "elapsed_seconds": elapsed,
    }
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
