#!/usr/bin/env python3
"""写入 .qianwenai-deploy 状态文件。详见 reference/deploy/13_record_state.md"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _ensure_gitignore(root: Path, *entries: str) -> None:
    """确保 .gitignore 含指定条目（幂等）。"""
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    lines = existing.splitlines()
    changed = False
    for entry in entries:
        if entry not in lines:
            lines.append(entry)
            changed = True
    if changed:
        content = "\n".join(lines)
        if not content.endswith("\n"):
            content += "\n"
        gi.write_text(content, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack-id", required=True,
                    help="ROS Stack ID")
    ap.add_argument("--stack-name", required=True,
                    help="ROS Stack Name")
    ap.add_argument("--region", required=True)
    ap.add_argument("--topology", default="single", choices=["single"])
    ap.add_argument("--app-type", required=True)
    ap.add_argument("--runtime", default=None,
                    help="运行时类型（none/java/node/python），用于热更新时判断依赖安装方式")
    ap.add_argument("--app-mode", default=None, choices=["docker-image", "docker-compose"],
                    help="Docker 部署模式（docker-image/docker-compose），热更新时据此决定 docker load 还是 compose up")
    ap.add_argument("--app-image-name", default=None,
                    help="docker-image 模式下 docker load 后的镜像名:tag，热更新重建容器时使用")
    ap.add_argument("--app-port", type=int, default=None,
                    help="应用监听端口（被 Nginx 反代），热更新健康检查与 docker run 端口映射使用")
    ap.add_argument("--outputs-json", required=True,
                    help='ROS GetStack 的 Outputs 序列化为 {"Key": "Value"} 的 JSON')
    ap.add_argument("--artifact-bucket", default=None)
    ap.add_argument("--static-dir", default=None)
    ap.add_argument("--app-dir", default=None)
    ap.add_argument("--nginx-mode", default=None, choices=["static+app", "proxy", "static"])
    ap.add_argument("--with-rds", action="store_true")
    ap.add_argument("--db-engine", default=None, choices=["mysql"])
    ap.add_argument("--artifact-urls-json", default=None,
                    help='产物签名 URL（upload_artifacts.py 的输出 JSON），存入 current_artifact_urls')
    ap.add_argument("--notes", default="")
    ap.add_argument("--project-root", default=".")
    args = ap.parse_args()

    deploy_mode = "full-stack"

    # 密码从环境变量读取，不经命令行（避免 ps 泄露）
    ecs_password = os.environ.get("PASSWORD") or None
    db_password = os.environ.get("DB_PASSWORD") or None

    outputs = json.loads(args.outputs_json)
    public_ip = outputs.get("PublicIp") or outputs.get("public_ip")
    ecs_ids_raw = outputs.get("EcsInstanceIds") or outputs.get("ecs_instance_ids") or ""
    if isinstance(ecs_ids_raw, list):
        ecs_ids = [str(x) for x in ecs_ids_raw]
    else:
        ecs_ids = [x.strip() for x in str(ecs_ids_raw).split(",") if x.strip()]

    db_instance_id = outputs.get("DbInstanceId") or outputs.get("db_instance_id")
    db_conn = outputs.get("DbConnectionAddress") or outputs.get("db_connection_address")
    db_port_raw = outputs.get("DbPort") or outputs.get("db_port")
    db_port = int(db_port_raw) if db_port_raw not in (None, "") else None
    db_account = outputs.get("DbAccount") or outputs.get("db_account")

    state = {
        "version": 1,
        "deploy_mode": deploy_mode,
        "region_id": args.region,
        "topology": args.topology,
        "app_type": args.app_type,
        "runtime": args.runtime,
        "static_dir": args.static_dir,
        "app_dir": args.app_dir,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tags": [{"Key": "from", "Value": "qianwenai"}],
        "outputs": {
            "public_ip": public_ip,
            "ecs_instance_ids": ecs_ids,
            "db_instance_id": db_instance_id,
            "db_connection_address": db_conn,
            "db_port": db_port,
            "db_account": db_account,
        },
        "nginx_mode": args.nginx_mode,
        "artifact_bucket": args.artifact_bucket,
        "notes": args.notes,
    }
    # Docker 热更新所需字段：仅在 docker 部署时有意义，避免污染其它 app_type 的状态文件
    if args.app_type == "docker":
        state["app_mode"] = args.app_mode or "docker-image"
        state["app_image_name"] = args.app_image_name or "qianwenai-app:latest"
    if args.app_port is not None:
        state["app_port"] = args.app_port
    if args.stack_id:
        state["stack_id"] = args.stack_id
    if args.stack_name:
        state["stack_name"] = args.stack_name
    if args.with_rds or db_instance_id:
        state["db_engine"] = args.db_engine or "mysql"

    if args.artifact_urls_json:
        urls = json.loads(args.artifact_urls_json)
        current = {k: v for k, v in urls.items() if v and k.endswith("_url") and k not in ("template_url",)}
        if current:
            state["current_artifact_urls"] = current

    root = Path(args.project_root).resolve()
    state_path = root / ".qianwenai-deploy"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    # 状态文件含 current_artifact_urls（OSS 签名 URL，等同下载凭证），按 0600 落盘。
    os.chmod(state_path, 0o600)
    # 无条件把主状态文件加入 .gitignore（即便本次没有密码文件）：签名 URL 不应入库。
    _ensure_gitignore(root, ".qianwenai-deploy")

    if ecs_password or db_password:
        local_path = root / ".qianwenai-deploy.local"
        local_data = {"stack_id": args.stack_id,
                      "warning": "本文件含密码，请勿提交版本库"}
        if ecs_password:
            local_data["ecs_password"] = ecs_password
        if db_password:
            local_data["db_password"] = db_password
        local_path.write_text(json.dumps(local_data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        os.chmod(local_path, 0o600)

        # 追加到 .gitignore
        _ensure_gitignore(root, ".qianwenai-deploy.local")

    print(str(state_path))


if __name__ == "__main__":
    main()
