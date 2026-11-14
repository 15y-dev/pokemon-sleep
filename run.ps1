<#
.SYNOPSIS
    adb_panorama.py をこの環境の設定で実行する。

.EXAMPLE
    .\run.ps1                       # 1匹目から637匹を撮影(前面で実行)
    .\run.ps1 -Start 70             # 70匹目の画面を表示した状態から再開
    .\run.ps1 -Start 70 -Background # バックグラウンドで実行し、ログを capture_*.log に出す
    .\run.ps1 -Count 3 -OutDir panoramas_test   # 3匹だけ試す
    .\run.ps1 -DryRun               # 実行せず、コマンドだけ表示する
#>
param(
    [int]$Start = 1,                   # N匹目の画面を表示した状態で実行する
    [int]$Count = 637,                 # 撮影する総数(ポケモンの数)
    [string]$OutDir = 'panoramas',     # 保存先フォルダ(プロジェクト直下からの相対パスも可)
    [string]$Serial = '',              # 端末のシリアル。空なら USB を優先して自動選択
    [switch]$Background,               # バックグラウンドで実行する
    [switch]$DryRun                    # コマンドを表示するだけで実行しない
)

$ErrorActionPreference = 'Stop'

# ---- この環境の設定 ----
$Adb    = 'D:\android\platform-tools\adb.exe'
$Swipe  = '0.3'
$Wait   = '1.0'
$Thresh = '0.8'
$Margin = '16'
# ------------------------

$root   = $PSScriptRoot
$py     = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'adb_panorama.py'
if (-not [System.IO.Path]::IsPathRooted($OutDir)) { $OutDir = Join-Path $root $OutDir }

foreach ($p in @($py, $script, $Adb)) {
    if (-not (Test-Path -LiteralPath $p)) { throw "Not found: $p" }
}

$pyArgs = @(
    '-u', $script,
    '--adb', $Adb,
    '--swipe', $Swipe, '--wait', $Wait, '--thresh', $Thresh, '--margin', $Margin,
    '--count', $Count, '--start', $Start,
    '--out-dir', $OutDir, '--skip-existing'
)
if ($Serial) { $pyArgs += @('--serial', $Serial) }

$env:PYTHONIOENCODING = 'utf-8'

if ($DryRun) {
    Write-Host "$py $($pyArgs -join ' ')"
    return
}

# 多重起動の防止(同じ端末を2つのプロセスで操作すると画面が壊れる)
$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*adb_panorama.py*' }
if ($running) {
    $ids = ($running | ForEach-Object { $_.ProcessId }) -join ', '
    throw "adb_panorama.py is already running (PID: $ids). To stop: Stop-Process -Id <PID>"
}

if ($Background) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $log   = Join-Path $root "capture_$stamp.log"
    $err   = Join-Path $root "capture_$stamp.err.log"
    $quoted = $pyArgs | ForEach-Object { if ("$_" -match '\s') { "`"$_`"" } else { "$_" } }
    $proc = Start-Process -FilePath $py -ArgumentList $quoted `
        -RedirectStandardOutput $log -RedirectStandardError $err `
        -WindowStyle Hidden -PassThru
    Write-Host "Started in background (PID: $($proc.Id))"
    Write-Host "Log: $log"
    Write-Host "Err: $err"
    Write-Host "To stop: Stop-Process -Id $($proc.Id)"
}
else {
    & $py @pyArgs
}
