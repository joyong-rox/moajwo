<#
.SYNOPSIS
외부 DB(Neon PostgreSQL) 백업 — libpq 서비스 파일·비밀번호 파일로 pg_dump 를 실행한다 (docs/EXTERNAL_DB.md 7장).

.DESCRIPTION
비밀번호·연결 문자열은 명령 인자·출력·기록에 넣지 않는다. 접속 정보는 %APPDATA%\postgresql\.pg_service.conf(서비스 이름)와
pgpass.conf(비밀번호)에서만 읽는다(환경 변수 PGSERVICEFILE·PGPASSFILE 로 다른 위치 지정 가능).
결과: <OutRoot>\<yyyyMMdd-HHmmss>\neondb-<시각>.dump (custom, --no-owner --no-privileges) + .sha256 + BACKUP-NOTE.md(시각·리비전·users 수).
보존 규칙(승인 묶음 v2 10항): 최근 KeepWeeks(기본 8주) + 반영 직전 백업(-PreDeploy 로 만든 것, 폴더에 PRE-DEPLOY 표지).
지울 후보는 목록만 출력하고 **지우지 않는다**. 주기: 매주 1회 + 계정 발급·반영 직전(-PreDeploy). 복원 확인은 분기 1회(7장 절차).

.PARAMETER Service   libpq 서비스 이름 — 직접 연결·소유 계정(기본 moajwo-migrate)
.PARAMETER PgBin     pg_dump.exe·psql.exe 가 있는 폴더(생략하면 PATH)
.PARAMETER OutRoot   백업 뿌리 폴더(기본 <저장소>\backups\external-db — Git 밖)
.PARAMETER PreDeploy 반영 직전 백업 표시(보존 예외)
.PARAMETER KeepWeeks 보존 주 수(기본 8)

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_external_db.ps1 -PgBin "C:\pg17\bin"
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_external_db.ps1 -PreDeploy
#>
param(
    [string]$Service = "moajwo-migrate",
    [string]$PgBin = "",
    [string]$OutRoot = "",
    [switch]$PreDeploy,
    [int]$KeepWeeks = 8
)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $OutRoot) { $OutRoot = Join-Path $Root "backups\external-db" }
if (-not $env:PGSERVICEFILE) { $env:PGSERVICEFILE = Join-Path $env:APPDATA "postgresql\.pg_service.conf" }
if (-not $env:PGPASSFILE) { $env:PGPASSFILE = Join-Path $env:APPDATA "postgresql\pgpass.conf" }

function Hide-Secret([string]$Text) {
    # 오류 문구에 들어갈 수 있는 Neon 호스트·IP 를 가린다 (docs/EXTERNAL_DB.md 3-1)
    if (-not $Text) { return "" }
    $t = [regex]::Replace($Text, 'ep-[a-z0-9-]+\.[a-z0-9.-]*neon\.tech', '<host>')
    return [regex]::Replace($t, '(?<!\d)\d{1,3}(\.\d{1,3}){3}(?!\d)', '<ip>')
}

function Invoke-Native([string]$Exe, [string[]]$ArgList, [string]$WorkDir) {
    # 표준 출력·오류를 임시 파일로 받아 종료 코드와 함께 돌려준다 (PowerShell 5.1 의 NativeCommandError 회피).
    # 공백·따옴표가 든 인자는 따옴표로 감싼다 (Start-Process 는 인자를 공백으로 이어 붙이기 때문)
    $quoted = @()
    foreach ($a in $ArgList) { if ($a -match '[\s"]') { $quoted += ('"' + ($a -replace '"', '\"') + '"') } else { $quoted += $a } }
    $out = [IO.Path]::GetTempFileName(); $err = [IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $Exe -ArgumentList ($quoted -join ' ') -WorkingDirectory $WorkDir -NoNewWindow -Wait -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        return @{ Code = $p.ExitCode; Out = (Get-Content -Raw -ErrorAction SilentlyContinue $out); Err = (Hide-Secret (Get-Content -Raw -ErrorAction SilentlyContinue $err)) }
    } finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $out, $err
    }
}

function Resolve-Tool([string]$Name) {
    if ($PgBin) { $p = Join-Path $PgBin "$Name.exe"; if (Test-Path $p) { return $p } ; Write-Error "$Name.exe 가 $PgBin 에 없습니다"; exit 2 }
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $c) { Write-Error "$Name 을 PATH 에서 찾지 못했습니다 — PostgreSQL 17 클라이언트를 설치하거나 -PgBin <폴더> 를 주세요"; exit 2 }
    return $c.Source
}

$pgdump = Resolve-Tool "pg_dump"
$psql = Resolve-Tool "psql"
foreach ($f in @($env:PGSERVICEFILE, $env:PGPASSFILE)) {
    if (-not (Test-Path $f)) { Write-Error "접속 파일이 없습니다: $f (docs/EXTERNAL_DB.md 3-1)"; exit 2 }
}

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$dir = Join-Path $OutRoot $ts
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$dump = Join-Path $dir "neondb-$ts.dump"

# 1) 건수·리비전 (비밀값 없음 — 값은 숫자·리비전 이름뿐)
function Query([string]$Sql) {
    $r = Invoke-Native $psql @("--no-password", "service=$Service", "-At", "-c", $Sql) $Root
    if ($r.Code -ne 0) { Write-Error ("psql 실패 (종료 {0}): {1}" -f $r.Code, $r.Err.Trim()); exit 3 }
    return $r.Out.Trim()
}
$rev = Query "select version_num from alembic_version"
$users = Query "select count(*) from users"
$temp = Query "select count(*) from users where must_change_password"
$ver = Query "show server_version"

# 2) pg_dump — PGSERVICE 로 서비스 지정(인자에 주소·비밀번호 없음)
$env:PGSERVICE = $Service
try {
    $r = Invoke-Native $pgdump @("--no-password", "--format=custom", "--no-owner", "--no-privileges", "--file=$dump") $Root
} finally {
    Remove-Item Env:PGSERVICE -ErrorAction SilentlyContinue
}
if ($r.Code -ne 0 -or -not (Test-Path $dump)) { Write-Error ("pg_dump 실패 (종료 {0}): {1}" -f $r.Code, $r.Err.Trim()); exit 4 }
$bytes = (Get-Item $dump).Length
$sha = (Get-FileHash -Algorithm SHA256 $dump).Hash.ToLower()
[IO.File]::WriteAllText((Join-Path $dir "neondb-$ts.dump.sha256"), "$sha *neondb-$ts.dump`n", (New-Object Text.UTF8Encoding $false))
if ($PreDeploy) { [IO.File]::WriteAllText((Join-Path $dir "PRE-DEPLOY"), "반영 직전 백업 — 보존 예외 (승인 묶음 v2 10항)`n", (New-Object Text.UTF8Encoding $false)) }
$kind = "정기"
if ($PreDeploy) { $kind = "반영 직전(보존 예외)" }
$note = @"
# 외부 DB 백업 — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') KST · $kind

- 원본: Neon ``neondb`` — 서비스 ``$Service``(직접 연결·소유 계정) · PostgreSQL $ver · 리비전 $rev · users $users(임시 상태 $temp)
- 파일: ``neondb-$ts.dump`` $bytes B · sha256 ``$sha`` (custom · --no-owner --no-privileges)
- 복원 확인(분기 1회): 빈 ``moajwo_restore_test`` 에 ``pg_restore --no-password --no-owner --no-privileges --exit-on-error --dbname="service=moajwo-restore-test"`` → 권한 재적용(5장) → 앱 계정 로그인 (docs/EXTERNAL_DB.md 7장)
- 보존: 최근 $KeepWeeks 주분 + 반영 직전 백업(PRE-DEPLOY). 이 도구는 지우지 않는다.
"@
[IO.File]::WriteAllText((Join-Path $dir "BACKUP-NOTE.md"), $note, (New-Object Text.UTF8Encoding $false))

Write-Output ("백업 완료: {0} · {1} B · sha256 {2} · 리비전 {3} · users {4}(임시 {5}) · PostgreSQL {6}" -f $dump, $bytes, $sha, $rev, $users, $temp, $ver)

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
exit 0
