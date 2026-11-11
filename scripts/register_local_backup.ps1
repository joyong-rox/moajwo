<#
.SYNOPSIS
로컬 운영 정기 백업 예약 작업(Windows 작업 스케줄러) 등록·해제·상태 — 작업 이름 moajwo-local-weekly-backup, 매주 1회(기본 일요일 21:00, PC 시간대) (docs/DEPLOY.md 14장).

.DESCRIPTION
- 실행 계정: 등록하는 현재 사용자, 로그온 세션에서만(Interactive — 비밀번호 저장 없음), 관리자 권한 없음(RunLevel Limited). 관리자 권한 없이 등록된다.
- 놓친 실행(PC가 꺼져 있었음)은 이 사용자가 로그인하면 바로 실행된다(StartWhenAvailable). Docker Desktop 도 사용자 세션에서 뜨므로 감싸기가 엔진을 기다린다.
- 동작: powershell.exe -File scripts\backup_local_weekly.ps1 -Log [-CopyTo <폴더>] — 사용 중·메모리 부족이면 10분 간격으로 최대 6번 다시 보고,
  그래도 안 되면 그 회차를 건너뛰고 기록한다(재시도는 스크립트 안에서 하므로 작업 스케줄러의 재시도는 쓰지 않음). 2시간 제한, 겹치면 새 실행 무시.
- 지우는 것 없음: 보존 후보는 backups\local-weekly\retention-candidates.txt 목록만.

.PARAMETER Remove  작업을 해제한다
.PARAMETER Status  등록 내용과 마지막·다음 실행, 최근 회차 기록만 보여 준다
.PARAMETER RunNow  (등록 뒤) 지금 한 번 실행하고 끝날 때까지 기다린다
.PARAMETER At      실행 시각(기본 21:00)
.PARAMETER Day     요일(기본 Sunday)
.PARAMETER CopyTo  PC 밖 사본 폴더(외장 디스크 등). 비우면 사본을 만들지 않는다

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_local_backup.ps1 -RunNow
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_local_backup.ps1 -CopyTo "E:\moajwo-backup"
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_local_backup.ps1 -Status
#>
param(
    [switch]$Remove,
    [switch]$Status,
    [switch]$RunNow,
    [string]$At = "21:00",
    [string]$Day = "Sunday",
    [string]$CopyTo = ""
)
$ErrorActionPreference = "Stop"
$TaskName = "moajwo-local-weekly-backup"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Script = Join-Path $Root "scripts\backup_local_weekly.ps1"
$PwshExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$LogDir = Join-Path $Root "backups\local-weekly\logs"
$Codes = "0 완료 · 3 미룸 끝에 건너뜀 · 4 디스크 부족 · 5 사본 불일치 · 6 사본 위치 없음 · 1 실패 · 267009 실행 중 · 267011 아직 실행 안 함"

function Show-Status {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $t) { Write-Host "작업 '$TaskName' 없음"; return }
    $i = $t | Get-ScheduledTaskInfo
    Write-Host ("작업 {0} · 상태 {1} · 계정 {2}({3}, {4}) · 트리거 {5}" -f $t.TaskName, $t.State, $t.Principal.UserId, $t.Principal.LogonType, $t.Principal.RunLevel, (($t.Triggers | ForEach-Object { "$($_.DaysOfWeek) $($_.StartBoundary)" }) -join '; '))
    Write-Host ("동작 {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
    Write-Host ("설정 StartWhenAvailable={0} · 제한 {1} · 재시도 {2}회 · 겹침 {3} · 배터리 허용 {4}" -f $t.Settings.StartWhenAvailable, $t.Settings.ExecutionTimeLimit, $t.Settings.RestartCount, $t.Settings.MultipleInstances, (-not $t.Settings.DisallowStartIfOnBatteries))
    Write-Host ("마지막 실행 {0} 결과 {1} · 다음 실행 {2}" -f $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime)
    Write-Host ("결과 코드: {0}" -f $Codes)
    $runs = Join-Path $LogDir "runs.tsv"
    if (Test-Path -LiteralPath $runs) { Write-Host "최근 회차(시작·끝·구분·종료·요약):"; Get-Content -LiteralPath $runs -Encoding UTF8 -Tail 3 | ForEach-Object { Write-Host "  $_" } }
}

if ($Status) { Show-Status; exit 0 }
if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "작업 '$TaskName' 해제됨"
    } else { Write-Host "작업 '$TaskName' 없음 — 할 일 없음" }
    exit 0
}

if (-not (Test-Path -LiteralPath $Script)) { throw "백업 스크립트가 없습니다: $Script" }
$arg = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Script + '" -Log'
if ($CopyTo) { $arg += ' -CopyTo "' + $CopyTo + '"' }
$action = New-ScheduledTaskAction -Execute $PwshExe -Argument $arg -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + "\" + $env:USERNAME) -LogonType Interactive -RunLevel Limited
$desc = "모아줘 로컬 운영 정기 백업 — scripts\backup_local_weekly.ps1 -Log (docs/DEPLOY.md 14장). 운영을 멈추지 않는 읽기 전용 백업, 자동 삭제 없음."
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $desc -Force | Out-Null
Write-Host "작업 '$TaskName' 등록됨 ($Day $At, 현재 사용자·로그온 세션·관리자 권한 없음)"
Show-Status

if ($RunNow) {
    $before = (Get-ScheduledTaskInfo -TaskName $TaskName).LastRunTime
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "지금 실행 시작 — 끝날 때까지 기다림(최대 130분)"
    $deadline = (Get-Date).AddMinutes(130)
    do {
        Start-Sleep -Seconds 5
        $i = Get-ScheduledTaskInfo -TaskName $TaskName
    } while (($i.LastTaskResult -eq 267009 -or $i.LastRunTime -eq $before) -and (Get-Date) -lt $deadline)
    Write-Host ("실행 끝 — 마지막 실행 {0} 결과 {1}" -f $i.LastRunTime, $i.LastTaskResult)
    $runs = Join-Path $LogDir "runs.tsv"
    if (Test-Path -LiteralPath $runs) { Write-Host ("최근 회차: " + (Get-Content -LiteralPath $runs -Encoding UTF8 -Tail 1)) }
    if ($i.LastTaskResult -ne 0) { exit 1 }
}
exit 0
