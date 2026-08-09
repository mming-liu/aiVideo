# 模板生成（步骤 7）

调用 `scripts/generate_template.py` 组装 ROS 模板和 UserData 脚本。

---

## 调用方式

### 无 RDS

```bash
python scripts/generate_template.py \
  --topology single \
  --app-type systemd \
  --runtime none \
  --app-port 8080 \
  --start-command "./server" \
  --nginx-mode static+app \
  --artifacts-json /tmp/qianwenai-artifacts.json \
  --output /tmp/qianwenai-template.yaml \
  --userdata-output /tmp/qianwenai-userdata.sh
```

### 含 RDS

```bash
DB_PASSWORD='<strong-pwd>' python scripts/generate_template.py \
  --topology single \
  --app-type systemd \
  --runtime none \
  --app-port 8080 \
  --start-command "./server" \
  --nginx-mode proxy \
  --with-rds --db-name appdb --db-account appuser \
  --artifacts-json /tmp/qianwenai-artifacts.json \
  --output /tmp/qianwenai-template.yaml \
  --userdata-output /tmp/qianwenai-userdata.sh
```

---

## 参数说明

| 参数 | 说明 |
|------|------|
| `--topology` | 固定 `single`（单机） |
| `--app-type` | `docker` / `systemd` / `static-only` |
| `--app-port` | 应用监听端口 |
| `--runtime` | 运行时安装（仅 systemd）：`none`（默认）/ `java` / `node` / `python`。<br>仅这三种语言 + 静态编译语言（`none`）走 systemd，其他语言用 `--app-type docker`，详见 `03_analyze_project.md` |
| `--start-command` | 完整启动命令（相对 `/opt/qianwenai`） |
| `--nginx-mode` | `static+app`/`proxy`/`static` |
| `--artifacts-json` | `upload_artifacts.py` 的输出 JSON 文件路径（或 `-` 表示 stdin） |
| `--with-rds` | 选用 `*_rds.yaml` 模板 |

---

## 关键行为

| 行为 | 说明 |
|------|------|
| 模板骨架 | 读取 `templates/ros_single.yaml` 或 `ros_single_rds.yaml` |
| UserData 注入 | 按 app_type 从 `templates/userdata/*.sh` 组装 |
| 无 RDS 路径 | 模板原样写出；UserData 写到独立文件，作为 ROS Parameter 传入 |
| 含 RDS 路径 | UserData base64 编码后 inline 到模板的 `__USERDATA_BODY__` 位置 |
| DB_PASSWORD | 必须通过环境变量传入（不走命令行，避免 `ps` 泄露） |

---

## artifacts-json 管道

推荐用法：`upload_artifacts.py` 输出 → 文件 → `generate_template.py` 读入：

```bash
python scripts/upload_artifacts.py ... > /tmp/qianwenai-artifacts.json
python scripts/generate_template.py ... --artifacts-json /tmp/qianwenai-artifacts.json
```

这样产物签名 URL 自动填入模板，无需手动粘贴。

---

## UserData 组装细节

模板从 `templates/userdata/*.sh` 读取片段，按 nginx_mode 和 app_type 组合拼接。

### nginx 片段选择

| nginx_mode | 片段文件 | 占位符 |
|------------|----------|--------|
| `static+app` | `nginx_static_proxy.sh` | `__STATIC_ARTIFACT_URL__`、`__APP_PORT__` |
| `proxy` | `nginx_proxy.sh` | `__APP_PORT__` |
| `static` | `nginx_static.sh` | `__STATIC_ARTIFACT_URL__` |

### 应用片段选择

| app_type | 片段文件 | 占位符 |
|----------|----------|--------|
| `docker` | `docker.sh` | `__APP_ARTIFACT_URL__`、`__APP_MODE__`、`__APP_PORT__`、`__APP_IMAGE_NAME__` |
| `systemd` | `systemd.sh` | `__APP_ARTIFACT_URL__`、`__APP_RUNTIME__`、`__START_COMMAND__`、`__APP_PORT__` |
| `static-only` | 无应用片段 | — |

### runtime 映射

| `--runtime` 参数 | `__APP_RUNTIME__` 值 | 说明 |
|------------------|----------------------|------|
| `none` (默认) | `none` | 不安装运行时（静态二进制、或运行时已存在） |
| `java` | `java` | 安装 JDK 17 |
| `node` | `node` | 安装 Node.js + npm + yarn |
| `python` | `python` | 安装 Python3 + pip |

---

## 含 RDS 时的 UserData inline 机制

1. 拼好的 UserData body 做 **base64 编码**
2. 注入模板 `__USERDATA_BODY__` 占位符位置
3. 运行时 ECS 先写 `db.env`（RDS 变量由 Fn::Sub 替换），再解码 + source 主脚本
4. Fn::Sub 完全不接触 shell 变量，避免 `${!VAR}` 等不可靠问题

`--userdata-output` 在 `--with-rds` 时仅写一个 placeholder 备查文件。
