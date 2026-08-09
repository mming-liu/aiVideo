# 项目分析（步骤 3）

Agent 直接浏览项目目录，采集关键信号后做出项目类型判断。

---

## 采集步骤

### 1. 文件树概览

```bash
find <项目根> -maxdepth 3 -not -path '*/node_modules/*' -not -path '*/.git/*' \
  -not -path '*/__pycache__/*' -not -path '*/.venv/*' -not -path '*/target/*' \
  -not -path '*/dist/*' -not -path '*/build/*' | head -100
```

目的：了解项目整体结构（静态/应用分离？单体？monorepo？）

### 2. 配置文件

读取项目根目录及一级子目录下的关键配置文件（存在则读）：

| 类别 | 文件 |
|------|------|
| Node.js | `package.json` |
| Go | `go.mod` |
| Python | `requirements.txt`, `pyproject.toml`, `Pipfile` |
| Java | `pom.xml`, `build.gradle` |
| Rust | `Cargo.toml` |
| Docker | `Dockerfile`, `docker-compose.yml` |
| 静态构建 | `vite.config.*`, `next.config.*`, `nuxt.config.*` |
| 运行时 | `Procfile`, `.env.example` |

读取方式：直接 `cat` 或 `head -50`（大文件取前 50 行即可）。

### 3. 入口源码采样

读取可能的入口文件的前 30 行，确认框架和端口：

常见入口：`app.py`, `main.py`, `server.py`, `main.go`, `cmd/main.go`,
`server.js`, `index.js`, `src/index.ts`, `src/main.rs`, `Program.cs`, `app.rb`

```bash
head -30 <入口文件>
```

### 4. README

```bash
head -80 README.md
```

获取构建/运行说明。

### 5. 数据库信号

在配置文件和源码中查找数据库相关依赖/连接串：
- MySQL：`pymysql`, `mysql2`, `go-sql-driver/mysql`, `jdbc:mysql://`, `MYSQL_` 环境变量
- PostgreSQL：`psycopg2`, `pg`, `gorm.io/driver/postgres`
- Redis：`redis`, `ioredis`, `go-redis`
- MongoDB：`pymongo`, `mongoose`, `mongo-driver`
- Docker Compose 中的 `image: mysql/postgres/redis/mongo`

### 6. 应用元数据

从 `package.json`(name/description)、`go.mod`(module)、`pyproject.toml`(name)、
`Cargo.toml`(name) 等提取 app_name 和 app_desc。
无法提取时，使用目录名作为 app_name。

---

## 判断产出

采集完成后，Agent 确定以下变量：

| 变量 | 说明 |
|------|------|
| `APP_NAME` | 应用名（小写、短横线连接） |
| `APP_DESC` | 一句话描述 |
| `app_type` | 见下方映射表 |
| `start_command` | 完整启动命令（相对部署目录） |
| `app_port` | 应用监听端口 |
| `static_dir` | 静态构建产物目录（如 `dist`） |
| `app_dir` | 应用目录 |
| `nginx_mode` | `static+app` / `proxy` / `static` |

---

## app_type 映射

| 信号 | app_type |
|------|----------|
| Dockerfile / docker-compose.yml | `docker` |
| go.mod / Cargo.toml（静态编译语言） | `systemd` + `runtime=none` |
| pom.xml / build.gradle | `systemd` + `runtime=java` |
| package.json + express/fastify/koa/nest | `systemd` + `runtime=node` |
| requirements.txt / pyproject.toml + Python 入口 | `systemd` + `runtime=python` |
| 纯静态（React/Vue/Vite，无应用） | `static-only` |

有 Dockerfile 时优先选 `docker`，除非用户明确不想用。

### 其他语言一律走 Docker

`runtime` 白名单只有 `none` / `java` / `node` / `python` 四个值，含义是「要不要在 ECS 上
安装解释器」。据此的判定规则：

| 项目类型 | 选择 |
|----------|------|
| Java / Python / Node | `systemd` + 对应 `runtime` |
| 能编译出自包含二进制（Go、Rust、C/C++、Zig 等） | `systemd` + `runtime=none` |
| **其他所有语言**（Ruby、PHP、Elixir、.NET、Deno、Bun、Perl 等） | **`docker`** |

#### 判断顺序

1. **有 `Dockerfile` / `docker-compose.yml`？** → `docker`（作者已表达部署意图，最可靠）
2. **是 Java / Python / Node？** → `systemd` + 对应 `runtime`
3. **构建产物是自包含的？** → 是则 `runtime=none`；否则需要 Docker（见下方无 Dockerfile 的处理）

#### 通用原则：看产物形态，不看语言名

上表列举的语言不可能穷尽。遇到未列出的语言（Nim、Crystal、Haskell、OCaml 等）时，
按这一条判断：

> **构建完成后得到的，是「拷到一台裸 Linux 上就能直接执行的文件」，
> 还是「必须先装解释器/虚拟机才能跑的代码或中间产物」？**

- 前者 → `systemd` + `runtime=none`（产物自带一切，宿主机无需安装任何东西）
- 后者 → 白名单内（Java/Python/Node）用对应 `runtime`；白名单外一律 `docker`

判断依据是**默认构建产物**，不是「理论上能不能」。例如 .NET 虽然可以用
`dotnet publish --self-contained` 打出自包含可执行文件，但其默认产物是需要 .NET 运行时的
DLL，因此归入 `docker`——不要去猜用户的构建参数。

> ⚠️ 除上述静态编译语言外，**不要**给其他语言选 `runtime=none` 并在 `start_command` 里
> 指定解释器。ECS 基础镜像里没有 ruby / php / dotnet 等运行时，`systemd.sh` 解析
> `start_command` 时会因 `command -v` 找不到命令而回退成 `/opt/qianwenai/<命令名>`，
> 最终 systemd 启动失败——而此时 ECS/EIP 已经创建并开始计费。

走 Docker 时分两种情况：

1. **项目已有 Dockerfile / docker-compose.yml** → 直接用，正常推进。
2. **项目没有 Dockerfile** → 在步骤 3 就告知用户，不要继续往下走：
   > 💬 检测到 <语言/框架> 项目。当前内置运行时安装仅覆盖 Java / Python / Node（以及 Go、Rust
   > 等编译型语言），你的项目需要通过 Docker 部署——这是本技能的通用方案，不限语言。
   >
   > 项目中还没有 `Dockerfile`。我可以根据项目结构生成一份供你确认，或者你也可以自己提供。

   用 AskUserQuestion 确认：**帮我生成 Dockerfile** / **我自己提供**。
   生成后必须展示完整内容并获得用户确认，再进入步骤 4。

---

## nginx_mode 判定

| 条件 | nginx_mode |
|------|------------|
| 有静态产物 + 有应用 | `static+app`（默认） |
| 无静态，纯应用（Flask/Django/Streamlit 等） | `proxy` |
| 纯静态，无应用 | `static` |

> `proxy` 模式下所有请求反代到应用。Flask/Django/Streamlit/Gradio **必须**用 `proxy`，
> 误用 `static+app` 会导致 `try_files` 拦截路由。

---

## Python 框架启动命令参考

| 框架 | start_command | 默认端口 |
|------|---------------|----------|
| FastAPI | `uvicorn main:app --host 0.0.0.0 --port 8080` | 8080 |
| Flask | `gunicorn -b 0.0.0.0:8080 app:app` | 8080 |
| Django | `gunicorn -b 0.0.0.0:8080 <project>.wsgi:application` | 8080 |
| Streamlit | `streamlit run app.py --server.port 8080 --server.headless true` | 8080 |
| Gradio | `python3 app.py` | 7860 |

---

## `--start-command` 说明

`start_command` 是**完整启动命令**（相对部署目录 `/opt/qianwenai`），不是文件路径。

- Go 二进制：`./server`
- Python：`python3 app.py` 或 `gunicorn -b :8080 app:app`
- Java：`java -jar app.jar`
- Node：`node server.js`

---

## Git URL 源的构建命令

| 类型 | 构建命令 |
|------|----------|
| Node.js | `npm install && npm run build` |
| Go | `go build -o <binary> .` |
| Python | `pip install -r requirements.txt` |
| Java | `mvn package -DskipTests` 或 `gradle build -x test` |
| Rust | `cargo build --release` |
| Docker | `docker build -t <name>:latest .` |

---

## Agent 判断流程

1. 看 `find` 输出理解整体结构
2. 读配置文件中的依赖清单和构建配置
3. 读入口源码确认框架和端口
4. 读 README 获取构建/运行说明
5. 有把握 → 直接确定；不确定 → AskUserQuestion 询问

> ⚠️ 分析完成后，须检查是否存在硬编码的敏感信息（密钥、Token、密码等），若发现须警告用户。
