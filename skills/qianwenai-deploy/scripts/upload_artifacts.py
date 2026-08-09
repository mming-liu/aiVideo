#!/usr/bin/env python3
"""产物上传到 OSS 临时桶 + 生成签名 URL。详见 reference/deploy/10_upload_artifacts.md"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path

def _ts_key(name: str) -> str:
    """生成带时间戳的对象键，如 static-20260703-113500.tar.gz，确保每次上传不覆盖旧版本。"""
    base, ext = name.rsplit(".", 1) if "." in name else (name, "")
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"{base}-{ts}.tar.gz"

def sh(cmd, check=True, capture=False):
    print(f"[sh] {' '.join(cmd) if isinstance(cmd, list) else cmd}", file=sys.stderr)
    r = subprocess.run(cmd, shell=isinstance(cmd, str), check=check,
                       stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None,
                       text=True)
    if capture:
        return r.stdout.strip()
    return None

def aliyun(*args, capture=False):
    return sh(["aliyun", *args], capture=capture)

_OSS_NOT_ACTIVATED_MARKERS = (
    "not enabled", "not activated", "not been opened", "has not opened",
    "nosuchservice", "please activate", "service is not open",
    "未开通", "请先开通",
)

def _oss_service_not_activated(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _OSS_NOT_ACTIVATED_MARKERS)

def _activate_oss_service() -> bool:
    """尽力自动开通 OSS 服务（开通免费，仅按用量计费）。

    开通成功或服务本就已开通时返回 True。
    """
    print("[bucket] 检测到 OSS 服务未开通——正在自动开通"
          "（开通免费，仅按用量计费）……", file=sys.stderr)
    r = subprocess.run(["aliyun", "ossadmin", "OpenOssService"],
                       capture_output=True, text=True)
    out = (r.stderr + r.stdout).lower()
    if r.returncode == 0 or any(k in out for k in ("already", "opened", "order.process", "success")):
        print("[bucket] OSS 服务已开通。", file=sys.stderr)
        return True
    print(r.stderr, file=sys.stderr)
    print("[bucket] 自动开通 OSS 失败。请到 https://oss.console.aliyun.com/ "
          "手动开通后重试。", file=sys.stderr)
    return False

def ensure_bucket(region: str, bucket: str | None) -> str:
    created = False
    if not bucket:
        bucket = f"qianwenai-deploy-tmp-{uuid.uuid4().hex[:6]}"

    def _mb():
        return subprocess.run(
            ["aliyun", "oss", "mb", f"oss://{bucket}/", "--region", region],
            capture_output=True, text=True)

    r = _mb()
    combined = (r.stderr + r.stdout).lower()
    if r.returncode != 0 and _oss_service_not_activated(combined):
        # 第二道防线（环境检查流程通常已先处理）。
        if not _activate_oss_service():
            raise SystemExit(2)
        r = _mb()
        combined = (r.stderr + r.stdout).lower()
    if r.returncode == 0:
        created = True
    elif "already" in combined or "bucketalreadyexists" in combined:
        pass  # 桶已存在（复用），不重复初始化
    else:
        print(r.stderr, file=sys.stderr)
        raise SystemExit(2)
    print(f"[bucket] {bucket}", file=sys.stderr)

    if created:
        _set_bucket_tag(bucket)
        _set_bucket_lifecycle(bucket, region)

    return bucket

def _set_bucket_tag(bucket: str):
    subprocess.run(
        ["aliyun", "oss", "bucket-tagging", "--method", "put",
         f"oss://{bucket}/", "from#qianwenai"],
        capture_output=True, text=True)

def _set_bucket_lifecycle(bucket: str, region: str):
    lifecycle_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<LifecycleConfiguration>'
        '<Rule><ID>auto-expire-7d</ID>'
        '<Prefix></Prefix>'
        '<Status>Enabled</Status>'
        '<Expiration><Days>7</Days></Expiration>'
        '</Rule>'
        '</LifecycleConfiguration>'
    )
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False)
    try:
        tmp.write(lifecycle_xml)
        tmp.close()
        subprocess.run(
            ["aliyun", "oss", "lifecycle", "--method", "put",
             f"oss://{bucket}/", tmp.name],
            capture_output=True, text=True)
    finally:
        os.unlink(tmp.name)

def to_internal_url(url: str) -> str:
    """把 OSS 公网 URL 转为内网端点（VPC 内可达、流量免费）。"""
    return re.sub(r"oss-([a-z0-9-]+)\.aliyuncs\.com", r"oss-\1-internal.aliyuncs.com", url)

def upload(bucket: str, local: Path, key: str, internal: bool = True) -> str:
    sh(["aliyun", "oss", "cp", str(local), f"oss://{bucket}/{key}", "-f"])
    url = aliyun("oss", "sign", f"oss://{bucket}/{key}", "--timeout", "86400", capture=True)
    for tok in url.split():
        if tok.startswith("http"):
            return to_internal_url(tok) if internal else tok
    # 解析不出签名 URL 必须立刻失败：否则会把空 URL 写进 JSON，
    # 模板照样生成、栈照样创建成功，但 ECS 上 `curl -fsSL "" -o app.tar.gz`
    # 拉不到产物，最终表现为「有公网 IP 但应用永远起不来」，极难排查。
    raise SystemExit(
        f"无法从 `aliyun oss sign` 输出解析签名 URL（oss://{bucket}/{key}）。CLI 输出：\n{url}"
    )
# 打包时跳过这些目录/文件，避免 node_modules / .git 等大量冗余内容入包：
# 1) 拖慢上传与 ECS 拉取；2) macOS 上的 node_modules 含原生扩展（sharp/bcrypt 等），
# 上 Linux ECS 后无法运行，反正 UserData 会在 ECS 端重装依赖。
TAR_EXCLUDE_DIR_NAMES = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",

    ".idea", ".vscode",
}
# 仅匹配相对路径片段（精确等于这些路径才排除，避免误伤同名目录）
TAR_EXCLUDE_REL_PATHS = {
    ".next/cache",
    "target/test-classes",
    "build/test-results",
}
TAR_EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db"}

def _tar_filter(ti: "tarfile.TarInfo"):
    # ti.name 形如 "./node_modules/foo" 或 "node_modules/foo"
    parts = [p for p in ti.name.split("/") if p and p != "."]
    if any(p in TAR_EXCLUDE_DIR_NAMES for p in parts):
        return None
    rel = "/".join(parts)
    if any(rel == ex or rel.startswith(ex + "/") for ex in TAR_EXCLUDE_REL_PATHS):
        return None
    if parts and parts[-1] in TAR_EXCLUDE_FILE_NAMES:
        return None
    return ti

def tar_dir(src: Path, dest: Path, arcname: str = "."):
    with tarfile.open(dest, "w:gz") as t:
        t.add(str(src), arcname=arcname, filter=_tar_filter)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--static-dir", default=None, help="本地静态 build 产物目录")
    ap.add_argument("--app-mode", default=None,
                    choices=["docker-image", "docker-compose", "binary", "skip"],
                    help="如果不需要打应用产物可填 skip")
    ap.add_argument("--app-dir", default=None)
    ap.add_argument("--app-image-name", default=None,
                    help="docker-image 模式下要 docker save 的本地镜像")
    ap.add_argument("--template-file", default=None,
                    help="ROS 模板文件路径，上传到 OSS 后输出 template_url（用于 --TemplateURL 避免 WAF）")
    ap.add_argument("--no-internal", action="store_true",
                    help="不替换为内网端点（默认替换为 oss-*-internal.aliyuncs.com）")
    args = ap.parse_args()

    bucket = ensure_bucket(args.region, args.bucket)
    internal = not args.no_internal
    out = {"bucket": bucket, "static_url": None, "app_url": None, "template_url": None}

    with tempfile.TemporaryDirectory(prefix="qianwenai-pack-") as tmpdir:
        tmp = Path(tmpdir)

        # 静态文件
        if args.static_dir:
            fdir = Path(args.static_dir).resolve()
            if not fdir.is_dir():
                raise SystemExit(f"static-dir 不存在: {fdir}")
            fpack = tmp / "static.tar.gz"
            tar_dir(fdir, fpack, arcname=".")
            out["static_url"] = upload(bucket, fpack, _ts_key("static"), internal=internal)

        # 应用
        if args.app_mode and args.app_mode != "skip":
            bpack = tmp / "app.tar.gz"

            if args.app_mode == "docker-image":
                if not args.app_image_name:
                    raise SystemExit("docker-image 模式需要 --app-image-name")
                img_tar = tmp / "image.tar"
                sh(["docker", "save", "-o", str(img_tar), args.app_image_name])
                with tarfile.open(bpack, "w:gz") as t:
                    t.add(str(img_tar), arcname="image.tar")
            elif args.app_mode == "docker-compose":
                bdir = Path(args.app_dir or ".").resolve()
                tar_dir(bdir, bpack, arcname=".")
            elif args.app_mode == "binary":
                bdir = Path(args.app_dir or ".").resolve()
                tar_dir(bdir, bpack, arcname=".")

            out["app_url"] = upload(bucket, bpack, _ts_key("app"), internal=internal)

        # 模板上传（避免 --TemplateBody 被 WAF 拦截）
        if args.template_file:
            tpl = Path(args.template_file).resolve()
            if not tpl.is_file():
                raise SystemExit(f"template-file 不存在: {tpl}")
            out["template_url"] = upload(bucket, tpl, "template.yaml", internal=False)

    print(json.dumps(out, ensure_ascii=False))

if __name__ == "__main__":
    main()
