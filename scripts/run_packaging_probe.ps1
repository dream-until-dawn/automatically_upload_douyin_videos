# 探针 B：验证单文件可执行产物是否会被 Windows 安全中心拦截。
#
# 这是 M0 阶段优先级最高的验证项。若产物注定被隔离，交付形态必须在动工前重新设计，
# 而不是等到最后一步才发现。
#
# 验证链条（任一环节失败即判定探针不通过）：
#   1. 能否成功打包出单文件可执行体
#   2. 产物落盘后是否依然存在（实时保护会直接隔离可疑文件）
#   3. Defender 按需扫描是否报出威胁
#   4. 产物能否被正常执行并输出预期结果
#
# 注意：本文件必须以 UTF-8 **带 BOM** 保存。Windows PowerShell 5.1 读取无 BOM 的
# UTF-8 脚本时会按系统 ANSI 代码页解码，导致中文乱码并引发语法错误。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/run_packaging_probe.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 原生命令（python / MpCmdRun / 产物本身）会把普通信息写入 stderr。
# 在 PowerShell 5.1 中用 2>&1 捕获会被包装成 NativeCommandError 并误判为失败，
# 因此统一改为 *> 重定向到日志文件后再读取。
$logDir = Join-Path $root "build"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Invoke-Native {
    <#
        .SYNOPSIS
        调用原生命令并安全捕获全部输出，返回 @{ ExitCode; Output }。
    #>
    param(
        [Parameter(Mandatory = $true)][scriptblock] $Action,
        [Parameter(Mandatory = $true)][string] $LogFile
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Action *> $LogFile
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    $output = if (Test-Path $LogFile) { Get-Content $LogFile } else { @() }
    return @{ ExitCode = $code; Output = $output }
}

Write-Host "=== 探针 B：打包与安全中心拦截验证 ===" -ForegroundColor Cyan
Write-Host ""

# ---------- 步骤 0：确认 Defender 实时保护处于开启状态 ----------
# 若实时保护是关闭的，本次探针的结论没有参考价值，必须如实说明。
$realtimeOn = $null
try {
    $status = Get-MpComputerStatus
    $realtimeOn = $status.RealTimeProtectionEnabled
    Write-Host "[环境] Defender 实时保护: $realtimeOn"
    Write-Host "[环境] 防病毒引擎版本  : $($status.AMEngineVersion)"
    Write-Host "[环境] 特征库版本      : $($status.AntivirusSignatureVersion)"
} catch {
    Write-Host "[环境] 无法读取 Defender 状态（可能非管理员或使用第三方杀软）: $_" -ForegroundColor Yellow
}
Write-Host ""

# ---------- 步骤 1：打包 ----------
$probeDist = Join-Path $root "build\probe"
$exeName = "packaging_probe"
$exePath = Join-Path $probeDist "$exeName.exe"

if (Test-Path $exePath) { Remove-Item $exePath -Force }

Write-Host "[打包] 正在使用 PyInstaller 生成单文件可执行体（首次较慢）..." -ForegroundColor Cyan
$buildStart = Get-Date

$build = Invoke-Native -LogFile (Join-Path $logDir "probe-build.log") -Action {
    python -m uv run pyinstaller `
        --onefile `
        --clean `
        --noconfirm `
        --collect-all playwright `
        --distpath (Join-Path $root "build\probe") `
        --workpath (Join-Path $root "build\probe-work") `
        --specpath (Join-Path $root "build\probe-spec") `
        -n $exeName `
        scripts/probe_packaging.py
}

$buildSeconds = ((Get-Date) - $buildStart).TotalSeconds
$build.Output | Select-Object -Last 8 | ForEach-Object { Write-Host "       $_" }
Write-Host "[打包] 退出码 $($build.ExitCode)，耗时 $([math]::Round($buildSeconds,1)) 秒"
Write-Host ""

# ---------- 步骤 2：产物是否幸存（实时保护会直接隔离） ----------
if (-not (Test-Path $exePath)) {
    Write-Host "[结论] 探针 B 不通过：产物未生成或已被实时保护隔离" -ForegroundColor Red
    Write-Host "       完整构建日志见 build\probe-build.log"
    exit 1
}
$sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host "[产物] 已生成: $exePath （$sizeMB MB）" -ForegroundColor Green
Write-Host ""

# ---------- 步骤 3：Defender 按需扫描 ----------
$mpCmd = "$env:ProgramFiles\Windows Defender\MpCmdRun.exe"
$platformDir = "$env:ProgramData\Microsoft\Windows Defender\Platform"
if (Test-Path $platformDir) {
    # 优先使用 Platform 目录下的最新版本，它比 Program Files 下的更新
    $latest = Get-ChildItem $platformDir -Directory -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending | Select-Object -First 1
    if ($latest) {
        $candidate = Join-Path $latest.FullName "MpCmdRun.exe"
        if (Test-Path $candidate) { $mpCmd = $candidate }
    }
}

$scanClean = $null
if (Test-Path $mpCmd) {
    Write-Host "[扫描] 调用 Defender 按需扫描产物..." -ForegroundColor Cyan
    $scan = Invoke-Native -LogFile (Join-Path $logDir "probe-scan.log") -Action {
        & $mpCmd -Scan -ScanType 3 -File $exePath -DisableRemediation
    }
    $scan.Output | ForEach-Object { Write-Host "       $_" }
    # MpCmdRun 以退出码 0 表示未发现威胁，2 表示发现威胁
    $scanClean = ($scan.ExitCode -eq 0)
    $verdict = if ($scanClean) { "未发现威胁" } else { "报出威胁或扫描异常" }
    Write-Host "[扫描] 退出码 $($scan.ExitCode) -> $verdict"
} else {
    Write-Host "[扫描] 未找到 MpCmdRun.exe，跳过按需扫描" -ForegroundColor Yellow
}
Write-Host ""

# ---------- 步骤 4：产物能否正常执行 ----------
Write-Host "[执行] 正在运行产物..." -ForegroundColor Cyan
$run = Invoke-Native -LogFile (Join-Path $logDir "probe-run.log") -Action {
    & $exePath
}
$run.Output | ForEach-Object { Write-Host "       $_" }
$runOk = ($run.ExitCode -eq 0)
Write-Host "[执行] 退出码 $($run.ExitCode)"
Write-Host ""

# ---------- 步骤 5：产物是否在执行后仍然幸存 ----------
$survived = Test-Path $exePath
Write-Host "[复查] 执行后产物仍存在: $survived"
Write-Host ""

# ---------- 结论 ----------
Write-Host "=== 探针 B 结论 ===" -ForegroundColor Cyan
Write-Host "  实时保护开启   : $realtimeOn"
Write-Host "  打包成功       : $($build.ExitCode -eq 0) （$sizeMB MB，$([math]::Round($buildSeconds,1)) 秒）"
Write-Host "  扫描未报威胁   : $scanClean"
Write-Host "  产物可正常执行 : $runOk"
Write-Host "  产物未被隔离   : $survived"

$passed = $runOk -and $survived -and ($scanClean -ne $false)
if ($passed) {
    Write-Host "`n[结论] 探针 B 通过：PyInstaller 单文件方案在本机未被安全中心拦截" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n[结论] 探针 B 不通过：需要改变打包方案（候选：Nuitka / 目录模式 / 代码签名）" -ForegroundColor Red
    exit 1
}
