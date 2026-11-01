<#
.SYNOPSIS
Render 시연 환경 깨우기·점검 — 프런트 / → API /health(직접) → 프런트 /api/health(프록시) → API /auth/status (쓰기 요청 없음).

.DESCRIPTION
Render Free 는 15분 무요청이면 잠들고(깨는 데 1분 안팎), Neon Free 컴퓨트는 5분 무활동이면 멈춘다. 시연·발표처럼 정해진 시각에 쓰기 전
T-30분·T-10분·T-3분에 이 스크립트를 돌려 미리 깨우고 응답 시간을 기록한다(계획 9단계 — backups/followup-20261030/round8/NEON-B-RENDER-PLAN.md).
재시도·중단 규칙: 프런트 / 는 실패하면 60초 뒤 1회 재시도 · /health·/api/health 는 30초 간격 최대 3회(API 가 잠든 동안 프런트 경유는 429 가 나올 수 있어 직접 /health 를 먼저) · /auth/status 는 10초 간격 최대 3회.
한 단계가 끝내 실패하면 **거기서 멈추고** 결과만 남긴다(설정 변경·재시도 폭주 없음). 로그인·쿠키·비밀값은 쓰지 않는다.
기록: <OutDir>\wake-<yyyyMMdd-HHmmss>.md (기본 <저장소>\backups\external-db\wake — Git 밖).

.PARAMETER Front  프런트 주소(기본 https://moajwo-test.onrender.com)
.PARAMETER Api    API 주소(기본 https://moajwo-test-api.onrender.com)
.PARAMETER OutDir 기록 폴더
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\wake_render.ps1
#>
param(
    [string]$Front = "https://moajwo-test.onrender.com",
    [string]$Api = "https://moajwo-test-api.onrender.com",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $OutDir) { $OutDir = Join-Path $Root "backups\external-db\wake" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$rows = @()
$stopped = $false

function Get-Once([string]$Url) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        $r = Invoke-WebRequest -Uri $Url -Method Get -UseBasicParsing -TimeoutSec 90 -Headers @{ "User-Agent" = "moajwo-wake-check" }
        $sw.Stop()
        return @{ Status = [int]$r.StatusCode; Body = [string]$r.Content; Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) }
    } catch {
        $sw.Stop()
        $code = 0
        if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        return @{ Status = $code; Body = ""; Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1); Error = $_.Exception.GetType().Name }
    }
}

function Invoke-Step([string]$Name, [string]$Url, [scriptblock]$Ok, [int]$Retries, [int]$WaitSec) {
    $attempt = 0
    while ($true) {
        $attempt++
        $r = Get-Once $Url
        $pass = $false
        if ($r.Status -eq 200) { $pass = & $Ok $r.Body }
        $note = ""
        if ($r.Error) { $note = $r.Error }
        $script:rows += [pscustomobject]@{ Step = $Name; Try = $attempt; Status = $r.Status; Seconds = $r.Seconds; Result = $(if ($pass) { "정상" } else { "실패" }); Note = $note }
        Write-Host ("{0} 시도 {1}: {2} · {3}초 · {4} {5}" -f $Name, $attempt, $r.Status, $r.Seconds, $(if ($pass) { "정상" } else { "실패" }), $note)  # 함수 반환값에 섞이지 않게 Write-Host
        if ($pass) { return $true }
        if ($attempt -gt $Retries) { return $false }
        if ($r.Status -eq 429) { Write-Host ("  429(요청 제한) — {0}초 뒤 재시도" -f $WaitSec) }
        Start-Sleep -Seconds $WaitSec
    }
}

# 순서: 프런트 / → API /health(직접 — API 를 먼저 깨움) → 프런트 /api/health(프록시 확인) → API /auth/status(DB 왕복).
# 2026-11-01 1회 실행에서 API 가 잠든 동안 프런트 경유 /api/health 는 429 로 즉시 거절됐고, 직접 /health 가 33.5초 만에 깨운 뒤에야 200 — 그래서 직접 호출을 앞에 둔다.
$steps = @(
    @{ Name = "1 프런트 /"; Url = "$Front/"; Ok = { param($b) $b -match '<html' }; Retries = 1; Wait = 60 },
    @{ Name = "2 API /health"; Url = "$Api/health"; Ok = { param($b) $b -match '"database"\s*:\s*"ok"' }; Retries = 3; Wait = 30 },
    @{ Name = "3 프런트 /api/health"; Url = "$Front/api/health"; Ok = { param($b) $b -match '"database"\s*:\s*"ok"' }; Retries = 3; Wait = 30 },
    @{ Name = "4 API /auth/status"; Url = "$Api/auth/status"; Ok = { param($b) $b -match '"setup_required"\s*:\s*(true|false)' }; Retries = 3; Wait = 10 }
)
$setupNote = ""
foreach ($s in $steps) {
    $ok = Invoke-Step $s.Name $s.Url $s.Ok $s.Retries $s.Wait
    if (-not $ok) { $stopped = $true; Write-Output ("멈춤: {0} 가 끝내 정상이 아님 — 설정을 바꾸지 말고 보고 (계획 9단계)" -f $s.Name); break }
    if ($s.Name -like "4 *") {
        $last = Get-Once $s.Url
        if ($last.Body -match '"setup_required"\s*:\s*true') { $setupNote = "주의: setup_required true (관리자 생성 전 상태)" }
    }
}

$md = @()
$md += "# Render 깨우기·점검 — $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') KST · $(if ($stopped) { '멈춤' } else { '모두 정상' }) $setupNote"
$md += ""
$md += "| 단계 | 시도 | 상태 | 초 | 결과 | 비고 |"
$md += "|---|---|---|---|---|---|"
foreach ($r in $rows) { $md += ("| {0} | {1} | {2} | {3} | {4} | {5} |" -f $r.Step, $r.Try, $r.Status, $r.Seconds, $r.Result, $r.Note) }
$md += ""
$md += "- 규칙: 프런트 / 실패 시 60초 뒤 1회 재시도 · API /health(직접) → 프런트 /api/health 30초×3 · /auth/status 10초×3 · 실패하면 멈춤. 쓰기 요청 없음. 첫 응답이 느리면(수십 초) 휴면에서 깬 것 — 콜드 스타트 시간. API 가 잠든 동안 프런트 경유 /api/health 는 429 로 거절될 수 있다."
$path = Join-Path $OutDir "wake-$ts.md"
[IO.File]::WriteAllText($path, ($md -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))
Write-Output ("기록: {0}" -f $path)
if ($setupNote) { Write-Output $setupNote }
if ($stopped) { exit 1 } else { exit 0 }
