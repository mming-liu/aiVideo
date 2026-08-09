# 环境检查（步骤 1）

Agent 逐步执行以下检查，根据每步结果即时判断和反馈。直接运行 CLI 命令。

---

## 1. CLI 是否安装

```bash
aliyun version 2>&1 | head -1
```

| 结果 | 处理 |
|------|------|
| 输出版本号（如 `3.x.x`） | 继续 |
| command not found | 告知用户安装（见下方） |
| 版本 < 3.x | 告知用户升级 |

**CLI 安装**：告知用户参考官方文档安装：
- https://help.aliyun.com/zh/cli/install-update-alibaba-cloud-cli

---

## 2. 凭证检查

```bash
aliyun configure list 2>&1
```

查看输出中带 `*` 标记的默认 profile 行：

| 结果 | 处理 |
|------|------|
| 有 `*` 行且含 `Valid` | 凭证有效，跳到步骤 3 |
| 有 `*` 行但不含 `Valid` | 凭证过期/无效，进入 **认证流程** |
| 无 `*` 行 / 命令报错（config.json 不存在） | 无凭证，进入 **认证流程** |

---

## 认证流程

**必须先通过对话询问用户选择认证方式**，推荐 OAuth（更安全，无需管理密钥）：

> 需要配置阿里云认证，请选择方式：
> 1. **OAuth 浏览器登录（推荐，更安全）** — 无需管理密钥，浏览器点一下即可
> 2. **AK/SK 手动配置** — 需要自行管理 AccessKey

### 用户选择 OAuth

依次执行两条命令：

```bash
# 自动预写配置（站点、region、语言）— 用户无需操作
aliyun configure set --mode OAuth --profile default \
  --oauth-site-type CN --region cn-hangzhou --language zh
```

```bash
# 弹出浏览器让用户授权
aliyun configure --mode OAuth --profile default
```

执行第二条后告知用户："已打开浏览器，请在浏览器中点击「授权」。"

授权完成后重新执行 `aliyun configure list` 验证凭证是否 Valid。

### 用户选择 AK/SK

告知用户在自己的终端中执行：

```
aliyun configure --profile default
```

提醒：
- region 填 `cn-hangzhou`
- **请勿将密钥粘贴到聊天中**

用户确认配置完成后，重新执行 `aliyun configure list` 验证。

---

## 3. Region 确认

```bash
aliyun configure get region 2>&1
```

| 结果 | 处理 |
|------|------|
| 有值（如 `cn-hangzhou`） | 记录为 REGION，继续 |
| 空 | 使用默认值 `cn-hangzhou`，继续 |

---

## 4. OSS 服务开通检查

```bash
aliyun oss ls 2>&1
```

| 结果 | 处理 |
|------|------|
| 正常输出（含 bucket 列表或空列表） | OSS 已开通，继续 |
| 含 `未开通` / `not activated` / `NoSuchService` 等 | 自动开通 ↓ |

自动开通：

```bash
aliyun ossadmin OpenOssService 2>&1
```

开通后重新验证 `aliyun oss ls`。若仍不可用，告知用户手动开通：https://oss.console.aliyun.com/

---

## 5. 身份探测

```bash
aliyun sts GetCallerIdentity 2>&1
```

| 结果 | 处理 |
|------|------|
| 返回 JSON 含 `AccountId` 和 `Arn` | 记录 ACCOUNT_ID 和 ARN，环境检查通过 ✓ |
| 报错 | 可能 OAuth 已过期，引导用户重新授权（回到认证流程） |

---

## 检查完成后的产出

环境检查通过后，Agent 持有以下变量，供后续步骤使用：

- `REGION` — 部署地域（默认 `cn-hangzhou`）
- `ACCOUNT_ID` — 阿里云账户 ID
- `IDENTITY_ARN` — 调用者 ARN
