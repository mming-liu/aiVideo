# 冻结候选审计 —— 默认只做零发送校验；真实调用必须显式 -Execute（且须人类授权）。
param(
    [switch]$Execute,
    [string]$Package = (Join-Path $PSScriptRoot 'audit_package.json'),
    [string]$OutRoot = ''
)
$ErrorActionPreference = 'Stop'

# ---- 授权登记根：真实启动固定用仓库登记根（不接受外部重定向）----
# 生产解析不读环境变量；这里只清掉测试专用的注入变量，避免测试 shim 带进真实运行。
Remove-Item Env:STEP01_TEST_AUTH_ROOT -ErrorAction SilentlyContinue

# ---- 授权身份展示（只读；脚本不生成 ID、不创造额度）----
$pkgData = Get-Content -Path $Package -Raw -Encoding UTF8 | ConvertFrom-Json
$auth = $pkgData.budget.new_authorization
if (-not $auth.authorization_id) {
    Write-Host '[INFO] 未登记新授权：允许 dry-run（零发送）；-Execute 会被 budget_exhausted 拒收。'
} else {
    Write-Host ("[INFO] 一次性授权: {0}  max_sends={1}" -f $auth.authorization_id, $auth.max_sends)
    Write-Host ("[INFO] 绑定摘要: {0}" -f $auth.binding_digest)
    Write-Host '[INFO] 同一授权已被占用时，入口会以 authorization_already_claimed 拒收（换运行根无效）。'
}

# ---- 冻结配置（与审计包一致；不含授权标记）----
$env:HOOK_ENABLED = "1"
$env:DURATION_MIN_SEC = "60"
$env:DURATION_MAX_SEC = "120"
$env:DURATION_TARGET_MIN_SEC = "105"
$env:DURATION_TARGET_MAX_SEC = "115"
$env:VOICE_ADAPTATION_ENABLED = "1"
$env:TEXT_TRANSPORT_RETRIES = "0"
$env:TEXT_TRANSPORT_CONNECT_RETRIES = "0"
$env:AUDIT_PARSE_RETRIES = "0"
$env:REPAIR_ROUNDS_ENABLED = "0"
$env:STEP01_AUDIT_ONLY = "1"
$env:AUDIT_TEXT_PROVIDER = "agent_plan"
$env:AUDIT_TEXT_MODEL = "deepseek-v4.1-flash"
Remove-Item Env:STEP01_AUDIT_AUTHORIZED -ErrorAction SilentlyContinue

# ---- 第一段：零发送校验（无条件执行，失败即停）----
$dryArgs = @('tools\audit_frozen_candidate.py', '--package', $Package, '--dry-run')
if ($OutRoot) { $dryArgs += @('--out-root', (Join-Path $OutRoot 'dryrun')) }
& python @dryArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host '[STOP] 零发送校验未通过 —— 不进入真实调用'
    exit $LASTEXITCODE
}
if (-not $Execute) {
    Write-Host '[OK] 零发送校验通过。真实调用需显式 -Execute，且须先获得人类授权。'
    exit 0
}

# ---- 第二段：真实调用（仅 -Execute 分支；授权标记在此设置）----
$env:STEP01_AUDIT_AUTHORIZED = "1"
$realArgs = @('tools\audit_frozen_candidate.py', '--package', $Package)
if ($OutRoot) { $realArgs += @('--out-root', (Join-Path $OutRoot 'run')) }
& python @realArgs
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host ("[STOP] 真实运行未提交（退出码 {0}）。若 code 为 authorization_already_claimed / authorization_lost，该授权已消费，禁止换运行根重试。" -f $code)
}
exit $code
