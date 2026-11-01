<#
.SYNOPSIS
PostgreSQL 17 클라이언트 도구(psql·pg_dump·pg_restore) 하나를 고정 판 Docker 이미지에서 실행한다 — 사용자 PC에 클라이언트를 두지 않는다 (docs/EXTERNAL_DB.md 7장).

.DESCRIPTION
- 이미지: postgres:17.11 (digest 고정, 기본값 -Image). Docker Desktop이 떠 있어야 한다 — 엔진이 없으면 -WaitDaemonSeconds(기본 180초) 동안 기다린 뒤 종료 2
  (로그인 직후의 예약 실행 때 Docker Desktop이 늦게 뜨는 경우 대비).
- 접속 정보는 3-1의 서비스 파일(PGSERVICEFILE)·비밀번호 파일(PGPASSFILE)·CA 파일(서비스 파일의 sslrootcert 값)에서만 읽는다. 세 파일은 **읽기 전용**으로 마운트하고
  (Docker Desktop의 바인드 마운트는 파일 권한이 0777로 보여 libpq가 비밀번호 파일을 무시하므로) 컨테이너 안 tmpfs(/run/pg, 0700)에 복사해 0600으로 맞춘 뒤 도구를 실행한다.
  컨테이너는 --rm 이라 끝나면 사본도 함께 사라진다(디스크에 남지 않음).
- 컨테이너용 서비스 파일 사본: sslrootcert 값만 컨테이너 경로(/run/pg/ca.pem)로 바꾼 사본을 서비스 파일과 같은 폴더의 pg_service.docker.conf 에 매번 다시 만들고
  본인만 읽게 제한한다(저장소 밖, icacls). 원본 서비스 파일은 바꾸지 않는다.
- 쓰기 마운트는 -OutDir(컨테이너 /out) 하나뿐이다. -InDir(컨테이너 /in)는 읽기 전용(복원할 백업 파일).
- 비밀번호·연결 문자열은 인자·출력·기록에 넣지 않는다(서비스 이름만). 오류 문구의 Neon 호스트·IP는 가린다.

.PARAMETER Tool              psql | pg_dump | pg_restore
.PARAMETER ToolArgs          도구 인자(배열). 접속은 "service=<이름>"(psql) 또는 "--dbname=service=<이름>"(pg_dump·pg_restore)으로 준다
.PARAMETER OutDir            컨테이너 /out 으로 쓰기 마운트할 폴더 (예: pg_dump --file=/out/…)
.PARAMETER InDir             컨테이너 /in 으로 읽기 전용 마운트할 폴더 (예: pg_restore /in/….dump)
.PARAMETER Image             고정 이미지 (태그@digest)
.PARAMETER WaitDaemonSeconds Docker 엔진을 기다리는 최대 초(기본 180)
.PARAMETER PassThru          출력 대신 @{Code; Out; Err} 를 돌려준다(다른 스크립트에서 호출할 때)

.EXAMPLE
.\scripts\pg_docker.ps1 -Tool psql -ToolArgs '--no-password','service=moajwo-migrate','-At','-c','select version_num from alembic_version'
.EXAMPLE
.\scripts\pg_docker.ps1 -Tool pg_restore -InDir backups\external-db\20261102-080000 -ToolArgs '--no-password','--no-owner','--no-privileges','--exit-on-error','--dbname=service=moajwo-restore-test','/in/neondb-20261102-080000.dump'
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("psql", "pg_dump", "pg_restore")][string]$Tool,
    [string[]]$ToolArgs = @(),
    [string]$OutDir = "",
    [string]$InDir = "",
    [string]$Image = "postgres:17.11@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1",
    [int]$WaitDaemonSeconds = 180,
    [switch]$PassThru
)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $env:PGSERVICEFILE) { $env:PGSERVICEFILE = Join-Path $env:APPDATA "postgresql\.pg_service.conf" }
if (-not $env:PGPASSFILE) { $env:PGPASSFILE = Join-Path $env:APPDATA "postgresql\pgpass.conf" }

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

function Invoke-Main {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) { throw "docker 명령을 찾지 못했습니다 — Docker Desktop 설치와 PATH 를 확인하세요" }
    $deadline = (Get-Date).AddSeconds($WaitDaemonSeconds)
    $up = $false
    while ($true) {
        $v = Invoke-Native $docker.Source @("version", "--format", "{{.Server.Version}}") $Root
        if ($v.Code -eq 0) { $up = $true; break }
        if ((Get-Date) -ge $deadline) { break }
        Start-Sleep -Seconds 10
    }
    if (-not $up) { throw "Docker 엔진이 $WaitDaemonSeconds 초 안에 응답하지 않았습니다 — Docker Desktop 을 띄운 뒤 다시 실행하세요" }
    foreach ($f in @($env:PGSERVICEFILE, $env:PGPASSFILE)) {
        if (-not (Test-Path -LiteralPath $f)) { throw "접속 파일이 없습니다: $f (docs/EXTERNAL_DB.md 3-1)" }
    }
    $utf8 = New-Object Text.UTF8Encoding $false
    $text = [IO.File]::ReadAllText($env:PGSERVICEFILE, $utf8)
    $certs = @([regex]::Matches($text, '(?m)^[ \t]*sslrootcert[ \t]*=[ \t]*(.+?)[ \t]*$') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
    if ($certs.Count -ne 1) { throw "서비스 파일의 sslrootcert 값이 1개여야 합니다(지금 $($certs.Count)개) — 컨테이너에는 CA 파일 1개를 마운트합니다" }
    $ca = $certs[0]
    if (-not (Test-Path -LiteralPath $ca)) { throw "서비스 파일의 sslrootcert 파일이 없습니다: $ca" }
    # 컨테이너용 서비스 파일 사본 — 저장소 밖(서비스 파일과 같은 폴더), 본인만 읽기
    $copy = Join-Path (Split-Path -Parent $env:PGSERVICEFILE) "pg_service.docker.conf"
    [IO.File]::WriteAllText($copy, [regex]::Replace($text, '(?m)^([ \t]*sslrootcert[ \t]*=[ \t]*).+?[ \t]*$', '${1}/run/pg/ca.pem'), $utf8)
    $acl = Invoke-Native (Join-Path $env:SystemRoot "System32\icacls.exe") @($copy, "/inheritance:r", "/grant:r", ($env:USERNAME + ":(F)")) $Root
    if ($acl.Code -ne 0) { throw "서비스 파일 사본의 권한 제한에 실패했습니다(icacls 종료 $($acl.Code))" }
    # 컨테이너: 읽기 전용 마운트 3개 → tmpfs 사본(0600) → 도구 실행. 쓰기 마운트는 /out 뿐
    $name = "moajwo-pg-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + ([IO.Path]::GetRandomFileName() -replace '\.', '')
    $prelude = 'cp /mnt/pg/svc /run/pg/svc && cp /mnt/pg/pgpass /run/pg/pgpass && cp /mnt/pg/ca.pem /run/pg/ca.pem && chmod 600 /run/pg/svc /run/pg/pgpass /run/pg/ca.pem && exec "$@"'
    $a = @("run", "--rm", "--name", $name,
        "--mount", "type=tmpfs,destination=/run/pg,tmpfs-mode=0700,tmpfs-size=1m",
        "-v", ($copy + ":/mnt/pg/svc:ro"),
        "-v", ($env:PGPASSFILE + ":/mnt/pg/pgpass:ro"),
        "-v", ($ca + ":/mnt/pg/ca.pem:ro"),
        "-e", "PGSERVICEFILE=/run/pg/svc", "-e", "PGPASSFILE=/run/pg/pgpass", "-e", "HOME=/run/pg")
    if ($OutDir) { $a += @("-v", ((Resolve-Path -LiteralPath $OutDir).Path + ":/out")) }
    if ($InDir) { $a += @("-v", ((Resolve-Path -LiteralPath $InDir).Path + ":/in:ro")) }
    $a += @("--entrypoint", "/bin/sh", $Image, "-c", $prelude, "sh", $Tool) + $ToolArgs
    return (Invoke-Native $docker.Source $a $Root)
}

try {
    $result = Invoke-Main
} catch {
    $result = @{ Code = 2; Out = ""; Err = (Hide-Secret $_.Exception.Message) }
}
if ($PassThru) { return $result }
if ($result.Out) { Write-Output $result.Out.TrimEnd() }
if ($result.Err) { Write-Host ("[stderr] " + $result.Err.TrimEnd()) }
exit $result.Code
