<#
.SYNOPSIS
외부 DB(Neon PostgreSQL) 백업 — libpq 서비스 파일·비밀번호 파일로 pg_dump 를 실행한다 (docs/EXTERNAL_DB.md 7장).

.DESCRIPTION
비밀번호·연결 문자열은 명령 인자·출력·기록에 넣지 않는다. 접속 정보는 %APPDATA%\postgresql\.pg_service.conf(서비스 이름)와
pgpass.conf(비밀번호)에서만 읽는다(환경 변수 PGSERVICEFILE·PGPASSFILE 로 다른 위치 지정 가능).
실행 방식 두 가지:
  -UseDocker (표준, 2026-11-02 13차): 고정 판 이미지(postgres:17.11, digest 고정)에서 psql·pg_dump 를 돌린다 — scripts\pg_docker.ps1
    (접속 파일 읽기 전용 마운트 → 컨테이너 안 tmpfs 사본 0600 → 실행, 쓰기 마운트는 출력 폴더뿐). 사용자 PC에 PostgreSQL 클라이언트가 없어도 된다.
  (없으면) PATH 또는 -PgBin 의 pg_dump.exe·psql.exe 17.
결과: <OutRoot>\<yyyyMMdd-HHmmss>\neondb-<시각>.dump (custom, --no-owner --no-privileges) + .sha256 + BACKUP-NOTE.md(시각·리비전·users 수·실행 방식).
보존 규칙(승인 묶음 v2 10항): 최근 KeepWeeks(기본 8주) + 반영 직전 백업(-PreDeploy 로 만든 것, 폴더에 PRE-DEPLOY 표지).
지울 후보는 목록만 출력하고 **지우지 않는다**. 주기: 매주 1회(예약 작업 moajwo-weekly-backup — scripts\register_weekly_backup.ps1) + 계정 발급·반영 직전(-PreDeploy).
복원 확인은 분기 1회(7장 절차).

.PARAMETER Service     libpq 서비스 이름 — 직접 연결·소유 계정(기본 moajwo-migrate)
.PARAMETER UseDocker   Docker 이미지의 클라이언트로 실행(권장)
.PARAMETER DockerImage -UseDocker 때 이미지(비우면 pg_docker.ps1 의 고정 digest 기본값)
.PARAMETER PgBin       (Docker 를 안 쓸 때) pg_dump.exe·psql.exe 가 있는 폴더(생략하면 PATH)
.PARAMETER OutRoot     백업 뿌리 폴더(기본 <저장소>\backups\external-db — Git 밖)
.PARAMETER PreDeploy   반영 직전 백업 표시(보존 예외)
.PARAMETER KeepWeeks   보존 주 수(기본 8)
.PARAMETER Log         실행 기록을 <OutRoot>\logs\backup-<시각>.log 에 남긴다(예약 작업용, Start-Transcript)

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_external_db.ps1 -UseDocker
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_external_db.ps1 -UseDocker -PreDeploy
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_external_db.ps1 -PgBin "C:\pg17\bin"
#>
param(
    [string]$Service = "moajwo-migrate",
    [switch]$UseDocker,
    [string]$DockerImage = "",
    [string]$PgBin = "",
    [string]$OutRoot = "",
    [switch]$PreDeploy,
    [int]$KeepWeeks = 8,
    [switch]$Log
)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $OutRoot) { $OutRoot = Join-Path $Root "backups\external-db" }
if (-not $env:PGSERVICEFILE) { $env:PGSERVICEFILE = Join-Path $env:APPDATA "postgresql\.pg_service.conf" }
if (-not $env:PGPASSFILE) { $env:PGPASSFILE = Join-Path $env:APPDATA "postgresql\pgpass.conf" }
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
if ($Log) {
    $logDir = Join-Path $OutRoot "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Transcript -Path (Join-Path $logDir "backup-$ts.log") | Out-Null
}

function Stop-Run([int]$Code) {
    if ($Log) { try { Stop-Transcript | Out-Null } catch { } }
    exit $Code
}

function Hide-Secret([string]$Text) {
    # 오류 문구에 들어갈 수 있는 Neon 호스트·IP 를 가린다 (docs/EXTERNAL_DB.md 3-1)
    if (-not $Text) { return "" }
    $t = [regex]::Replace($Text, 'ep-[a-z0-9-]+\.[a-z0-9.-]*neon\.tech', '<host>')
    return [regex]::Replace($t, '(?<!\d)\d{1,3}(\.\d{1,3}){3}(?!\d)', '<ip>')
}

function ConvertTo-ArgString([string[]]$ArgList) {
    # Start-Process 는 인자를 공백으로 이어 붙이므로 Windows 규칙(따옴표 앞 역슬래시 2배, 안쪽 따옴표 \")으로 감싼다
    $quoted = @()
    foreach ($a in $ArgList) {
        if ($a -match '[\s"]' -or $a -eq "") {
            $e = [regex]::Replace($a, '(\\*)"', '$1$1\"')
            $e = [regex]::Replace($e, '(\\+)$', '$1$1')
            $quoted += ('"' + $e + '"')
        } else { $quoted += $a }
    }
    return ($quoted -join ' ')
}

function Invoke-Native([string]$Exe, [string[]]$ArgList, [string]$WorkDir) {
    # 표준 출력·오류를 임시 파일로 받아 종료 코드와 함께 돌려준다 (PowerShell 5.1 의 NativeCommandError 회피)
    $out = [IO.Path]::GetTempFileName(); $err = [IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $Exe -ArgumentList (ConvertTo-ArgString $ArgList) -WorkingDirectory $WorkDir -NoNewWindow -Wait -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        return @{ Code = $p.ExitCode; Out = [string](Get-Content -Raw -ErrorAction SilentlyContinue $out); Err = (Hide-Secret ([string](Get-Content -Raw -ErrorAction SilentlyContinue $err))) }
    } finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $out, $err
    }
}

function Resolve-Tool([string]$Name) {
    if ($PgBin) { $p = Join-Path $PgBin "$Name.exe"; if (Test-Path $p) { return $p } ; Write-Error "$Name.exe 가 $PgBin 에 없습니다"; Stop-Run 2 }
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $c) { Write-Error "$Name 을 PATH 에서 찾지 못했습니다 — -UseDocker 를 쓰거나 PostgreSQL 17 클라이언트를 설치해 -PgBin <폴더> 를 주세요"; Stop-Run 2 }
    return $c.Source
}

$Runner = Join-Path $PSScriptRoot "pg_docker.ps1"
function Invoke-Pg([string]$Tool, [string[]]$ToolArgs, [string]$OutDir) {
    # 한 도구 실행 → @{Code; Out; Err}. Docker 방식은 pg_docker.ps1(접속 파일 읽기 전용 마운트·tmpfs 사본), 아니면 PATH/-PgBin 의 실행 파일
    if ($UseDocker) {
        $p = @{ Tool = $Tool; ToolArgs = $ToolArgs; PassThru = $true }
        if ($OutDir) { $p.OutDir = $OutDir }
        if ($DockerImage) { $p.Image = $DockerImage }
        return (& $Runner @p)
    }
    return (Invoke-Native (Resolve-Tool $Tool) $ToolArgs $Root)
}

if ($UseDocker) {
    if (-not (Test-Path -LiteralPath $Runner)) { Write-Error "scripts\pg_docker.ps1 이 없습니다"; Stop-Run 2 }
    $how = "Docker"
} else {
    $null = Resolve-Tool "pg_dump"; $null = Resolve-Tool "psql"
    $how = "네이티브(" + (Resolve-Tool "pg_dump") + ")"
}
foreach ($f in @($env:PGSERVICEFILE, $env:PGPASSFILE)) {
    if (-not (Test-Path $f)) { Write-Error "접속 파일이 없습니다: $f (docs/EXTERNAL_DB.md 3-1)"; Stop-Run 2 }
}

$dir = Join-Path $OutRoot $ts
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$dumpName = "neondb-$ts.dump"
$dump = Join-Path $dir $dumpName

# 0) 클라이언트 판 (기록용)
$r = Invoke-Pg "pg_dump" @("--version") ""
if ($r.Code -ne 0) { Write-Error ("pg_dump 를 실행하지 못했습니다 (종료 {0}): {1}" -f $r.Code, $r.Err.Trim()); Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $dir; Stop-Run 2 }
$client = $r.Out.Trim()

# 1) 건수·리비전 (비밀값 없음 — 값은 숫자·리비전 이름뿐)
function Query([string]$Sql) {
    $r = Invoke-Pg "psql" @("--no-password", "service=$Service", "-At", "-c", $Sql) ""
    if ($r.Code -ne 0) { Write-Error ("psql 실패 (종료 {0}): {1}" -f $r.Code, $r.Err.Trim()); Stop-Run 3 }
    return $r.Out.Trim()
}
$rev = Query "select version_num from alembic_version"
$users = Query "select count(*) from users"
$temp = Query "select count(*) from users where must_change_password"
$ver = Query "show server_version"

# 2) pg_dump — 접속은 --dbname=service=<이름>(인자에 주소·비밀번호 없음)
$dumpArgs = @("--no-password", "--format=custom", "--no-owner", "--no-privileges", "--dbname=service=$Service")
if ($UseDocker) {
    $r = Invoke-Pg "pg_dump" ($dumpArgs + @("--file=/out/$dumpName")) $dir
} else {
    $r = Invoke-Pg "pg_dump" ($dumpArgs + @("--file=$dump")) ""
}
if ($r.Code -ne 0 -or -not (Test-Path $dump)) { Write-Error ("pg_dump 실패 (종료 {0}): {1}" -f $r.Code, $r.Err.Trim()); Stop-Run 4 }
$bytes = (Get-Item $dump).Length
$sha = (Get-FileHash -Algorithm SHA256 $dump).Hash.ToLower()
[IO.File]::WriteAllText((Join-Path $dir "$dumpName.sha256"), "$sha *$dumpName`n", (New-Object Text.UTF8Encoding $false))
if ($PreDeploy) { [IO.File]::WriteAllText((Join-Path $dir "PRE-DEPLOY"), "반영 직전 백업 — 보존 예외 (승인 묶음 v2 10항)`n", (New-Object Text.UTF8Encoding $false)) }
$kind = "정기"
if ($PreDeploy) { $kind = "반영 직전(보존 예외)" }
$note = @"
# 외부 DB 백업 — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') KST · $kind

- 원본: Neon ``neondb`` — 서비스 ``$Service``(직접 연결·소유 계정) · PostgreSQL $ver · 리비전 $rev · users $users(임시 상태 $temp)
- 파일: ``$dumpName`` $bytes B · sha256 ``$sha`` (custom · --no-owner --no-privileges)
- 실행 방식: $how · 클라이언트 $client
- 복원 확인(분기 1회): 빈 ``moajwo_restore_test`` 에 ``pg_restore --no-password --no-owner --no-privileges --exit-on-error --dbname=service=moajwo-restore-test`` → 권한 재적용(5장) → 앱 계정 확인 → 복원 DB 비우기 (docs/EXTERNAL_DB.md 7장)
- 보존: 최근 $KeepWeeks 주분 + 반영 직전 백업(PRE-DEPLOY). 이 도구는 지우지 않는다.
"@
[IO.File]::WriteAllText((Join-Path $dir "BACKUP-NOTE.md"), $note, (New-Object Text.UTF8Encoding $false))

Write-Output ("백업 완료: {0} · {1} B · sha256 {2} · 리비전 {3} · users {4}(임시 {5}) · PostgreSQL {6} · {7} · {8}" -f $dump, $bytes, $sha, $rev, $users, $temp, $ver, $how, $client)

# 3) 보존 후보 — 목록만 (지우지 않음)
$cut = (Get-Date).AddDays(-7 * $KeepWeeks)
$cands = @()
Get-ChildItem -Path $OutRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Name -match '^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$') {
        $hasDump = @(Get-ChildItem -Path $_.FullName -Filter 'neondb-2*.dump' -ErrorAction SilentlyContinue).Count -gt 0
        $pre = Test-Path (Join-Path $_.FullName 'PRE-DEPLOY')
        $when = Get-Date -Year $matches[1] -Month $matches[2] -Day $matches[3] -Hour $matches[4] -Minute $matches[5] -Second $matches[6]
        if ($hasDump -and -not $pre -and $when -lt $cut) { $cands += $_.Name }
    }
}
if ($cands.Count -gt 0) {
    Write-Output ("보존 기간({0}주) 지난 정기 백업 폴더 — 지울 후보(이 도구는 지우지 않음, 소유자 확인 뒤 수동 삭제): {1}" -f $KeepWeeks, ($cands -join ', '))
} else {
    Write-Output ("보존 기간({0}주) 지난 정기 백업 없음" -f $KeepWeeks)
}
Stop-Run 0
