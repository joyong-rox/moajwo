<#
.SYNOPSIS
로컬 운영 정기 백업 — 예약 작업용 감싸기. 조건이 맞을 때 scripts\backup_local.sh 를 실행한다 (docs/DEPLOY.md 14장).

.DESCRIPTION
- Docker 엔진이 응답할 때까지 기다린다(로그인 직후 실행 대비, 기본 300초). 그래도 없으면 실패(종료 1)로 기록한다.
- 시도마다 여유 메모리(작업 관리자의 '사용 가능')를 본다. 기준 미만이거나, 운영이 사용 중(backup_local.sh 종료 3 — 실행 중인 결과·
  최근 몇 분 안의 쓰기)이면 WaitMinutes 뒤 다시 본다(최대 Attempts 번). 끝까지 안 되면 이번 회차는 건너뛰고 기록한다(종료 3).
  디스크 여유 기준 미달(종료 4)은 기다려도 나아지지 않으므로 바로 건너뛰고 기록한다.
- 운영은 멈추지 않는다: 볼륨은 읽기 전용으로만 마운트한다(backup_local.sh). 아무것도 지우지 않는다(보존 후보는 목록만).
- 성공 뒤 -CopyTo 가 있으면 그 폴더 아래 moajwo-local-weekly\<시각>\ 로 복사하고 sha256 을 대조한다.
  폴더가 없으면(외장 디스크가 연결되지 않음) 복사를 건너뛰고 종료 6, 대조가 다르면 종료 5.
- 기록: backups\local-weekly\logs\run-<시각>.log(-Log, Start-Transcript) · backups\local-weekly\logs\runs.tsv(회차마다 한 줄 — 시작·끝·종료 코드·요약)
  · backups\local-weekly\logs\attempts.tsv(backup_local.sh 시도마다 한 줄).
종료 코드: 0 완료 · 3 미룸 끝에 건너뜀 · 4 디스크 기준 미달로 건너뜀 · 5 사본 sha256 불일치 · 6 백업 완료, 사본 위치 없음 · 1 실패

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_local_weekly.ps1 -Label manual
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_local_weekly.ps1 -Log -CopyTo "E:\moajwo-backup"
#>
param(
    [string]$CopyTo = "",
    [ValidateSet("weekly", "manual")][string]$Label = "weekly",
    [int]$MinAvailableMiB = 768,
    [int]$Attempts = 6,
    [int]$WaitMinutes = 10,
    [int]$QuietMinutes = 10,
    [int]$MinFreeDiskMiB = 5120,
    [int]$WaitDockerSeconds = 300,
    [switch]$Log,
    # 아래 셋은 시험용(합성 볼륨·컨테이너·다른 결과 폴더) — 예약 작업은 기본값(운영)을 쓴다
    [string]$OutRoot = "",
    [string]$Volume = "moajwo_moajwo-data",
    [string]$Container = "moajwo-backend-1"
)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:NoDefaultCurrentDirectoryInExePath = "1"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $OutRoot) { $OutRoot = Join-Path $Root "backups\local-weekly" }
$LogDir = Join-Path $OutRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$started = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
if ($Log) { Start-Transcript -Path (Join-Path $LogDir "run-$ts.log") | Out-Null }

function Say([string]$Message) { Write-Host ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) }
function Stop-Run([int]$Code, [string]$Summary) {
    Say "끝 — 종료 $Code · $Summary"
    $line = "{0}`t{1}`t{2}`t{3}`t{4}" -f $started, (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Label, $Code, $Summary
    Add-Content -LiteralPath (Join-Path $LogDir "runs.tsv") -Value $line -Encoding UTF8
    if ($Log) { try { Stop-Transcript | Out-Null } catch { } }
    exit $Code
}

$Bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
if (-not (Test-Path -LiteralPath $Bash)) { Stop-Run 1 "Git Bash 없음 ($Bash)" }
$Script = (Join-Path $Root "scripts\backup_local.sh").Replace('\', '/')
if (-not (Test-Path -LiteralPath $Script)) { Stop-Run 1 "백업 스크립트 없음 ($Script)" }
Say "로컬 운영 정기 백업($Label) 시작 — 기준: 여유 메모리 ≥ $MinAvailableMiB MiB · 조용한 시간 $QuietMinutes 분 · 디스크 여유 ≥ $MinFreeDiskMiB MiB(또는 볼륨 3배) · 최대 $Attempts 번, $WaitMinutes 분 간격"

$deadline = (Get-Date).AddSeconds($WaitDockerSeconds)
while ($true) {
    & docker info --format "{{.ServerVersion}}" *> $null
    if ($LASTEXITCODE -eq 0) { break }
    if ((Get-Date) -ge $deadline) { Stop-Run 1 "Docker 엔진 응답 없음 ($WaitDockerSeconds 초 기다림)" }
    Start-Sleep -Seconds 10
}

$resultDir = $null
$why = ""
for ($n = 1; $n -le $Attempts; $n++) {
    $avail = [int](Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).AvailableMBytes
    if ($avail -lt $MinAvailableMiB) {
        $why = "여유 메모리 $avail MiB < 기준 $MinAvailableMiB MiB"
        Add-Content -LiteralPath (Join-Path $LogDir "attempts.tsv") -Value ("{0}`t{1}`t3`t미룸: {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Label, $why) -Encoding UTF8
    } else {
        Say "시도 $n/$Attempts — 여유 메모리 $avail MiB"
        $lines = @(& $Bash $Script --out-root $OutRoot.Replace('\', '/') --label $Label --quiet-minutes $QuietMinutes --min-free-mib $MinFreeDiskMiB --volume $Volume --container $Container |
            ForEach-Object { Write-Host $_; "$_" })
        $rc = $LASTEXITCODE
        if ($rc -eq 0) {
            $resultDir = ($lines | Where-Object { $_ -like "result_dir *" } | Select-Object -Last 1)
            if ($resultDir) { $resultDir = $resultDir.Substring(11) }
            break
        }
        if ($rc -eq 4) { Stop-Run 4 "디스크 여유 기준 미달 — 이번 회차 건너뜀" }
        if ($rc -ne 3) { Stop-Run 1 "백업 실패 (backup_local.sh 종료 $rc) — backups\local-weekly 의 backup.log·attempts.tsv 확인" }
        $why = ($lines | Where-Object { $_ -like "*[[]미룸[]]*" } | Select-Object -Last 1)
    }
    if ($n -lt $Attempts) {
        Say "미룸 — $why — $WaitMinutes 분 뒤 다시 ($n/$Attempts)"
        Start-Sleep -Seconds ($WaitMinutes * 60)
    }
}
if (-not $resultDir) { Stop-Run 3 "건너뜀 — $Attempts 번 모두 미룸: $why" }
$name = Split-Path -Leaf $resultDir

if ($CopyTo) {
    if (-not (Test-Path -LiteralPath $CopyTo -PathType Container)) { Stop-Run 6 "백업 완료 $name · 사본 위치 없음($CopyTo — 외장 디스크 연결 확인) — 사본 건너뜀" }
    $dest = Join-Path (Join-Path $CopyTo "moajwo-local-weekly") $name
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Path (Join-Path $resultDir "*") -Destination $dest -Force
    $tgz = Get-ChildItem -LiteralPath $resultDir -Filter "moajwo-local-*.tgz" | Select-Object -First 1
    $want = ((Get-Content -LiteralPath ($tgz.FullName + ".sha256") -TotalCount 1) -split '\s+')[0]
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $dest $tgz.Name)).Hash.ToLowerInvariant()
    if ($got -ne $want) { Stop-Run 5 "백업 완료 $name · 사본 sha256 불일치 ($dest)" }
    Say "사본 확인: $dest (sha256 같음 $want)"
    Stop-Run 0 "완료 $name · 사본 $dest sha256 같음"
}
Stop-Run 0 "완료 $name"
