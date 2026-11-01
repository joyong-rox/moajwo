<#
.SYNOPSIS
주간 백업 예약 작업(Windows 작업 스케줄러) 등록·해제·상태 — 작업 이름 moajwo-weekly-backup, 매주 일요일 21:00(PC 시간대, KST) (docs/EXTERNAL_DB.md 7장).

.DESCRIPTION
- 실행 계정: 등록하는 현재 사용자, 로그온 세션에서만(Interactive — 비밀번호 저장 없음), 관리자 권한 없음(RunLevel Limited). 관리자 권한 없이 등록된다.
- 놓친 실행(꺼져 있었음)은 PC 가 켜지고 이 사용자가 로그인하면 바로 실행(StartWhenAvailable). Docker Desktop 은 사용자 세션에서 뜨므로 이 조건이 자연스럽다.
- 동작: powershell.exe -File scripts\backup_external_db.ps1 -UseDocker -Log → backups\external-db\<시각>\ 에 백업, backups\external-db\logs\backup-<시각>.log 에 기록.
  보존 후보는 목록만(자동 삭제 없음). 실패(종료 코드 0 아님)하면 10분 간격으로 최대 3번 다시 시도, 1시간 제한, 겹치면 새 실행 무시, 배터리에서도 실행.
- 비밀값 없음: 작업 정의에는 스크립트 경로와 스위치만 들어간다.

.PARAMETER Remove  작업을 해제한다
.PARAMETER Status  등록 내용과 마지막·다음 실행만 보여 준다
.PARAMETER RunNow  (등록 뒤) 지금 한 번 실행하고 끝날 때까지 기다린다 — 시험용
.PARAMETER At      실행 시각(기본 21:00)
.PARAMETER Day     요일(기본 Sunday)

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_weekly_backup.ps1
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_weekly_backup.ps1 -RunNow
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_weekly_backup.ps1 -Remove
#>
param(
    [switch]$Remove,
    [switch]$Status,
    [switch]$RunNow,
    [string]$At = "21:00",
    [string]$Day = "Sunday"
)
$ErrorActionPreference = "Stop"
$TaskName = "moajwo-weekly-backup"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Script = Join-Path $Root "scripts\backup_external_db.ps1"
$PwshExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Show-Status {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $t) { Write-Host "작업 '$TaskName' 없음"; return }
    $i = $t | Get-ScheduledTaskInfo
    Write-Host ("작업 {0} · 상태 {1} · 계정 {2}({3}, {4}) · 트리거 {5}" -f $t.TaskName, $t.State, $t.Principal.UserId, $t.Principal.LogonType, $t.Principal.RunLevel, (($t.Triggers | ForEach-Object { "$($_.DaysOfWeek) $($_.StartBoundary)" }) -join '; '))
    Write-Host ("동작 {0} {1}" -f $t.Actions[0].Execute, $t.Actions[0].Arguments)
    Write-Host ("설정 StartWhenAvailable={0} · 제한 {1} · 재시도 {2}회/{3} · 겹침 {4} · 배터리 허용 {5}" -f $t.Settings.StartWhenAvailable, $t.Settings.ExecutionTimeLimit, $t.Settings.RestartCount, $t.Settings.RestartInterval, $t.Settings.MultipleInstances, (-not $t.Settings.DisallowStartIfOnBatteries))
    Write-Host ("마지막 실행 {0} 결과 {1} (0 = 성공, 267009 = 실행 중) · 다음 실행 {2}" -f $i.LastRunTime, $i.LastTaskResult, $i.NextRunTime)
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
$action = New-ScheduledTaskAction -Execute $PwshExe -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $Script + '" -UseDocker -Log') -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ($env:USERDOMAIN + "\" + $env:USERNAME) -LogonType Interactive -RunLevel Limited
$desc = "모아줘 외부 DB(Neon) 주간 백업 — scripts\backup_external_db.ps1 -UseDocker -Log (docs/EXTERNAL_DB.md 7장). 보존 후보는 목록만, 자동 삭제 없음."
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $desc -Force | Out-Null
Write-Host "작업 '$TaskName' 등록됨 ($Day $At, 현재 사용자·로그온 세션·관리자 권한 없음)"
Show-Status

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "지금 실행 시작 — 끝날 때까지 기다림(최대 30분)"
    $deadline = (Get-Date).AddMinutes(30)
    do {
        Start-Sleep -Seconds 5
        $i = Get-ScheduledTaskInfo -TaskName $TaskName
    } while ($i.LastTaskResult -eq 267009 -and (Get-Date) -lt $deadline)
    Write-Host ("실행 끝 — 마지막 실행 {0} 결과 {1}" -f $i.LastRunTime, $i.LastTaskResult)
    $logs = Get-ChildItem -Path (Join-Path $Root "backups\external-db\logs") -Filter "backup-*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($logs) { Write-Host "최근 기록: $($logs.FullName)" }
    if ($i.LastTaskResult -ne 0) { exit 1 }
}
exit 0
