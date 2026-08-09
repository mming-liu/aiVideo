#!/usr/bin/env python3
"""组装 ROS 模板 + UserData 脚本。详见 reference/deploy/07_generate_template.md"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path


TPL_DIR = Path(__file__).resolve().parent.parent / "templates"


def load_skeleton(topology: str, with_rds: bool) -> str:
    if with_rds:
        fname = f"ros_{topology}_rds.yaml"
    else:
        fname = f"ros_{topology}.yaml"
    return (TPL_DIR / fname).read_text(encoding="utf-8")


def build_userdata(app_type: str, args) -> str:
    # 引导日志会记录带 OSS 签名 URL 的 curl 命令（set -x 展开），默认 umask 产生 644，
    # ECS 上任何本机用户可读 → 泄露 OSSAccessKeyId/Signature。与 *_rds.yaml 对称：先以
    # 600 建好日志再 exec，后续所有 userdata 子脚本都 append 到同一文件，权限得以保持。
    parts = [
        "#!/bin/bash",
        "set -euxo pipefail",
        "install -m 600 /dev/null /var/log/qianwenai-bootstrap.log 2>/dev/null || "
        "{ touch /var/log/qianwenai-bootstrap.log; chmod 600 /var/log/qianwenai-bootstrap.log; }",
        "exec >> /var/log/qianwenai-bootstrap.log 2>&1",
    ]

    nginx_mode = getattr(args, "nginx_mode", "static+app")

    if nginx_mode == "proxy":
        nginx = (TPL_DIR / "userdata" / "nginx_proxy.sh").read_text(encoding="utf-8")
        nginx = nginx.replace("__APP_PORT__", str(args.app_port))
        parts.append("# --- nginx: proxy (server-rendered) ---")
        parts.append(nginx)
    elif nginx_mode == "static":
        nginx = (TPL_DIR / "userdata" / "nginx_static.sh").read_text(encoding="utf-8")
        nginx = nginx.replace("__STATIC_ARTIFACT_URL__", args.static_artifact_url or "")
        parts.append("# --- nginx: static (no app) ---")
        parts.append(nginx)
    else:
        nginx = (TPL_DIR / "userdata" / "nginx_static_proxy.sh").read_text(encoding="utf-8")
        nginx = nginx.replace("__STATIC_ARTIFACT_URL__", args.static_artifact_url or "")
        nginx = nginx.replace("__APP_PORT__", str(args.app_port))
        parts.append("# --- nginx: static+app (static + api) ---")
        parts.append(nginx)


    if app_type == "static-only":
        pass
    elif app_type == "docker":
        app_script = (TPL_DIR / "userdata" / "docker.sh").read_text(encoding="utf-8")
        app_script = app_script.replace("__APP_ARTIFACT_URL__", args.app_artifact_url or "")
        app_script = app_script.replace("__APP_MODE__", args.app_mode or "docker-image")
        app_script = app_script.replace("__APP_PORT__", str(args.app_port))
        app_script = app_script.replace("__APP_IMAGE_NAME__", args.app_image_name or "qianwenai-app:latest")
        parts.append("# --- app: docker ---")
        parts.append(app_script)
    elif app_type == "systemd":
        runtime = getattr(args, "runtime", None) or "none"
        app_script = (TPL_DIR / "userdata" / "systemd.sh").read_text(encoding="utf-8")
        app_script = app_script.replace("__APP_ARTIFACT_URL__", args.app_artifact_url or "")
        app_script = app_script.replace("__APP_RUNTIME__", runtime)
        app_script = app_script.replace("__START_COMMAND__", args.start_command or "./server")
        app_script = app_script.replace("__APP_PORT__", str(args.app_port))
        parts.append(f"# --- app: systemd (runtime={runtime}) ---")
        parts.append(app_script)
    else:
        print(f"unknown app_type: {app_type}", file=sys.stderr)
        sys.exit(2)

    return "\n".join(parts) + "\n"


def inject_userdata_body(template_text: str, userdata_body: str) -> str:
    """把 userdata_body 做 base64 编码后注入模板的 __USERDATA_BODY__ 占位。

    不再尝试逐个转义 shell 变量（${!VAR} 不可靠，且 $VAR / ${VAR#pattern} 也会被
    ROS Fn::Sub 解析报错）。改用 base64 编码方案：Fn::Sub 完全看不到 shell 变量，
    运行时解码后 source 执行，继承 db.env 环境变量。
    """
    marker = "__USERDATA_BODY__"
    if marker not in template_text:
        print(f"模板中找不到 {marker} 占位符", file=sys.stderr)
        sys.exit(2)

    encoded = base64.b64encode(userdata_body.encode("utf-8")).decode("ascii")

    loader = (
        f"echo '{encoded}' | base64 -d > /tmp/qianwenai-main.sh\n"
        f"chmod +x /tmp/qianwenai-main.sh\n"
        f". /tmp/qianwenai-main.sh"
    )

    for line in template_text.splitlines():
        if marker in line:
            indent = line[: len(line) - len(line.lstrip())]
            break

    indented_lines = []
    for ln in loader.splitlines():
        if ln.strip():
            indented_lines.append(indent + ln)
        else:
            indented_lines.append("")
    indented_body = "\n".join(indented_lines)

    full_marker_line = indent + marker
    return template_text.replace(full_marker_line, indented_body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", choices=["single"], default="single")
    ap.add_argument("--app-type", required=True,
                    choices=["static-only", "docker", "systemd"])
    ap.add_argument("--app-port", type=int, default=8080)
    ap.add_argument("--runtime", default="none",
                    choices=["none", "java", "node", "python"],
                    help="运行时安装（仅 systemd 时有效）：none=不装（静态二进制）, java/node/python=自动安装对应运行时")
    ap.add_argument("--static-artifact-url", default="")
    ap.add_argument("--app-artifact-url", default="")
    ap.add_argument("--artifacts-json", default=None,
                    help="upload_artifacts.py 的 JSON 输出（文件路径，或 - 表示从 stdin 读）；"
                         "自动取其中的 static_url / app_url，免去手动粘贴长签名 URL。"
                         "显式的 --static-artifact-url / --app-artifact-url 优先。")
    ap.add_argument("--app-mode", default="docker-image", choices=["docker-image", "docker-compose"])
    ap.add_argument("--app-image-name", default="")
    ap.add_argument("--start-command", default="",
                    help="完整启动命令（相对 /opt/qianwenai），如 ./server / "
                         "\"python3 app.py\" / \"java -jar app.jar\" / \"node server.js\" / "
                         "\"gunicorn -b :8080 app:app\"。脚本不再自动补解释器前缀，"
                         "命令以此为唯一来源。")
    ap.add_argument("--nginx-mode", default="static+app", choices=["static+app", "proxy", "static"],
                    help="static+app: 静态文件 + /api/ 反代（默认）；proxy: 全量反代到应用（Flask/Django 等）；static: 纯静态托管")
    ap.add_argument("--output", required=True)
    ap.add_argument("--userdata-output", required=True,
                    help="无 RDS 时写出 UserData 到该文件；含 RDS 时该路径仅写一个 placeholder 注释")
    # RDS-related
    ap.add_argument("--with-rds", action="store_true",
                    help="选用 *_rds.yaml 模板，并把 UserData inline 进模板（Fn::Sub 嵌入 RDS 内网地址）")
    ap.add_argument("--db-name", default="appdb")
    ap.add_argument("--db-account", default="appuser")
    ap.add_argument("--db-instance-class", default="mysql.n2.medium.1")
    ap.add_argument("--db-instance-storage", type=int, default=20)
    args = ap.parse_args()

    # 校验 DB_PASSWORD
    if args.with_rds and not os.environ.get("DB_PASSWORD"):
        print("--with-rds 需要设置环境变量 DB_PASSWORD", file=sys.stderr)
        sys.exit(64)

    # 消费 artifacts-json
    if args.artifacts_json:
        raw = sys.stdin.read() if args.artifacts_json == "-" \
            else Path(args.artifacts_json).read_text(encoding="utf-8")
        try:
            art = json.loads(raw)
        except Exception as e:
            print(f"--artifacts-json 解析失败：{e}", file=sys.stderr)
            sys.exit(64)
        if not args.static_artifact_url:
            args.static_artifact_url = art.get("static_url") or ""
        if not args.app_artifact_url:
            args.app_artifact_url = art.get("app_url") or ""

    skeleton = load_skeleton(args.topology, args.with_rds)
    userdata = build_userdata(args.app_type, args)

    if args.with_rds:
        # UserData 内嵌到模板，--userdata-output 仅写一个备查文件（未转义、未缩进的原始版本）
        final_template = inject_userdata_body(skeleton, userdata)
        Path(args.output).write_text(final_template, encoding="utf-8")
        Path(args.userdata_output).write_text(
            "# NOTE: --with-rds 路径下 UserData 已 inline 到模板，无需作为 ROS Parameter 传入。\n"
            "# 以下为转义前的原始 body（仅供 diff 调试）：\n\n" + userdata,
            encoding="utf-8")
    else:
        # 原有路径：模板原样写出，UserData 走独立文件
        Path(args.output).write_text(skeleton, encoding="utf-8")
        Path(args.userdata_output).write_text(userdata, encoding="utf-8")

    print(json.dumps({"template": args.output, "userdata": args.userdata_output, "with_rds": args.with_rds},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
