# tools\_write_multiline.ps1 - read UTF-8 content from a source file and write to dst as UTF-8 no BOM.
# Usage:
#   powershell -File tools\_write_multiline.ps1 -Src "C:\path\src.txt" -Dst "C:\path\dst.py"
param(
    [Parameter(Mandatory=$true)] [string]$Src,
    [Parameter(Mandatory=$true)] [string]$Dst
)
if (-not (Test-Path -LiteralPath $Src)) {
    Write-Error "Src not found: $Src"
    exit 1
}
$content = [System.IO.File]::ReadAllText($Src, [System.Text.UTF8Encoding]::new($false))
$enc = [System.Text.UTF8Encoding]::new($false)
$bytes = $enc.GetBytes($content)
$dir = Split-Path -Parent $Dst
if ($dir -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}
[System.IO.File]::WriteAllBytes($Dst, $bytes)
Write-Host ("wrote {0} from {1} ({2} bytes)" -f $Dst, $Src, $bytes.Length)