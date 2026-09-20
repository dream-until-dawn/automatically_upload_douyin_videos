# 构建单文件可执行产物。
#
# 打包方案的可行性已由 M0 探针 B 验证（见 docs/probe-results.md）：
# 在 Defender 实时保护开启的情况下，产物未被隔离、按需扫描未报威胁。
#
# 注意：本文件必须以 UTF-8 **带 BOM** 保存。Windows PowerShell 5.1 读取无 BOM 的
# UTF-8 脚本时会按系统 ANSI 代码页解码，导致中文乱码并引发语法错误。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts/build.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -SkipVerify

param(
    # 跳过产物自检（仅在明确不需要时使用）
    [switch] $SkipVerify
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$exeName = "douyin_publisher"
$distDir = Join-Path $root "dist"
$exePath = Join-Path $distDir "$exeName.exe"
$logDir = Join-Path $root "build"

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Invoke-Native {
    <#
        .SYNOPSIS
        调用原生命令并安全捕获输出。

        .DESCRIPTION
        PyInstaller 等工具把普通信息写入 stderr。在 PowerShell 5.1 中用 2>&1
        捕获会被包装成 NativeCommandError 并误判为失败，因此改用 *> 重定向到
        日志文件后再读取。
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

Write-Host "=== 构建单文件可执行产物 ===" -ForegroundColor Cyan
Write-Host ""

# ---------- 步骤 1：确保依赖就绪 ----------
Write-Host "[准备] 同步依赖..." -ForegroundColor Cyan
$sync = Invoke-Native -LogFile (Join-Path $logDir "build-sync.log") -Action {
    python -m uv sync
}
if ($sync.ExitCode -ne 0) {
    $sync.Output | Select-Object -Last 10 | ForEach-Object { Write-Host "       $_" }
    Write-Host "[失败] 依赖同步失败" -ForegroundColor Red
    exit 1
}

# ---------- 步骤 2：打包 ----------
if (Test-Path $exePath) { Remove-Item $exePath -Force }

Write-Host "[打包] 正在生成 $exeName.exe ..." -ForegroundColor Cyan
$buildStart = Get-Date

$build = Invoke-Native -LogFile (Join-Path $logDir "build.log") -Action {
    python -m uv run pyinstaller `
        --onefile `
        --clean `
        --noconfirm `
        --collect-all playwright `
        --distpath (Join-Path $root "dist") `
        --workpath (Join-Path $root "build\work") `
        --specpath (Join-Path $root "build\spec") `
        -n $exeName `
        (Join-Path $root "src\douyin_publisher\__main__.py")
}

$buildSeconds = ((Get-Date) - $buildStart).TotalSeconds

if ($build.ExitCode -ne 0 -or -not (Test-Path $exePath)) {
    $build.Output | Select-Object -Last 20 | ForEach-Object { Write-Host "       $_" }
    Write-Host "[失败] 打包失败，完整日志见 build\build.log" -ForegroundColor Red
    exit 1
}

$sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host "[打包] 完成：$exePath （$sizeMB MB，$([math]::Round($buildSeconds,1)) 秒）" -ForegroundColor Green
Write-Host ""

if ($SkipVerify) {
    Write-Host "[跳过] 按要求跳过产物自检" -ForegroundColor Yellow
    exit 0
}

# ---------- 步骤 3：产物自检 ----------
# 打包产物最常见的故障是「能构建、不能跑」：依赖漏收、驱动路径解析失败等。
# 自检子命令会验证运行时依赖确实可用，比单纯看构建成功可靠得多。
Write-Host "[自检] 运行产物自检..." -ForegroundColor Cyan
$check = Invoke-Native -LogFile (Join-Path $logDir "build-selfcheck.log") -Action {
    & $exePath selfcheck
}
$check.Output | ForEach-Object { Write-Host "       $_" }

if ($check.ExitCode -ne 0) {
    Write-Host "[失败] 产物自检未通过（退出码 $($check.ExitCode)）" -ForegroundColor Red
    exit 1
}
Write-Host ""

# ---------- 步骤 4：命令行契约验证 ----------
# 自检只证明「跑得起来」，证明不了「退出码和 JSON 是一致的」。
# 上游同时依赖这两者做决策，不一致会让它收到两个互相矛盾的结论。
Write-Host "[契约] 校验退出码与 JSON 结果是否一致..." -ForegroundColor Cyan

$cases = @(
    @{ Args = @();                     Expect = 18; Name = "无参数" },
    @{ Args = @("未知命令");           Expect = 3;  Name = "未知子命令" },
    @{ Args = @("publish");            Expect = 18; Name = "publish 缺配置" },
    @{ Args = @("publish", "垃圾数据");Expect = 19; Name = "配置无法解析" }
)

$contractOk = $true
foreach ($case in $cases) {
    $out = & $exePath @($case.Args) 2>$null
    $code = $LASTEXITCODE
    $json = $null
    try { $json = $out | Select-Object -Last 1 | ConvertFrom-Json } catch { }

    # 除退出码与 code 一致外，还要确认结果结构完整：
    # type 用于与进度行区分，screenshot 字段必须存在（失败时为路径，成功时为 null）。
    # 少了任何一个，上游按文档写的解析代码都会出问题。
    $hasType = ($null -ne $json) -and ($json.type -eq "result")
    $hasShot = ($null -ne $json) -and ($json.PSObject.Properties.Name -contains "screenshot")

    $ok = ($null -ne $json) -and ($code -eq $case.Expect) -and ($json.code -eq $code) -and $hasType -and $hasShot
    if (-not $ok) { $contractOk = $false }
    $flag = if ($ok) { "OK " } else { "BAD" }
    $name = if ($null -ne $json) { $json.name } else { "<无法解析 JSON>" }
    $detail = ""
    if (-not $hasType) { $detail += " [缺 type=result]" }
    if (-not $hasShot) { $detail += " [缺 screenshot 字段]" }
    Write-Host "       [$flag] $($case.Name)：退出码=$code 期望=$($case.Expect) name=$name$detail"
}

# 进度回报默认关闭，因此 stdout 必须只有一行。
# 这条守的是向后兼容：上游若按「整段 json.loads」解析，多一行就会直接失败。
$lineOut = & $exePath publish "垃圾数据" 2>$null
$lineCount = ($lineOut | Where-Object { $_.Trim() -ne "" } | Measure-Object).Count
if ($lineCount -ne 1) {
    Write-Host "       [BAD] 默认配置下 stdout 有 $lineCount 行，应为 1 行" -ForegroundColor Red
    $contractOk = $false
} else {
    Write-Host "       [OK ] 默认配置下 stdout 仅一行（进度回报默认关闭）"
}

if (-not $contractOk) {
    Write-Host "[失败] 命令行契约校验未通过" -ForegroundColor Red
    exit 1
}
Write-Host ""

# ---------- 步骤 5：确认未被安全软件隔离 ----------
$survived = Test-Path $exePath
Write-Host "[复查] 执行后产物仍存在：$survived"

if (-not $survived) {
    Write-Host "[失败] 产物已被安全软件隔离" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[完成] 产物就绪：$exePath" -ForegroundColor Green
Write-Host "       用法：$exeName.exe publish `"<Base64 配置>`""
exit 0
