# tools/_write_file.ps1 - write a text file as UTF-8 without BOM
# Usage:
#   powershell -File tools/_write_file.ps1 -Dst "C:\path\file.py" -Content "<utf-8 string>"
# Note: -Content is a single-line string. For multi-line content, see tools\_write_multiline.ps1.
param(
    [Parameter(Mandatory=$true)] [string]$Dst,
    [Parameter(Mandatory=$true)] [AllowEmptyString()] [string]$Content
)
$enc = [System.Text.UTF8Encoding]::new($false)
$bytes = $enc.GetBytes($Content)
$dir = Split-Path -Parent $Dst
if ($dir -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}
[System.IO.File]::WriteAllBytes($Dst, $bytes)
Write-Host ("wrote {0} ({1} bytes)" -f $Dst, $bytes.Length)