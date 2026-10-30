// check-csp-style-hashes.mjs 의 검사 범위 시험 — 합성 라이브러리 문자열·합성 nginx 설정만 쓴다 (node_modules·실제 설정에 기대지 않음).
// 실행: frontend 폴더에서 `npm test` (node --test). 정상 대조군·해시 불일치·헤더 누락/주석·추가 출처·unsafe-inline·style-src-elem·
// Report-Only·always 없음·라이브러리 내용 변경·주석 안의 헤더를 본다.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { checkConf, checkPolicy, cspHeaders, injectedStyle, run, styleHash } from './check-csp-style-hashes.mjs'

// 실제 라이브러리와 같은 모양(상수 치환·.trim())의 합성 소스 — 내용은 합성
const LIB_A = "const $x$var$ATTR = 'data-synthetic-a';\nfunction f() {\n  style.textContent = `\n@layer {\n  [${$x$var$ATTR}] {\n    touch-action: none;\n  }\n}\n    `.trim();\n}\n"
const LIB_B = "function g() {\n  style.textContent = `\n@layer {\n  * {\n    overscroll-behavior: contain;\n  }\n}`.trim();\n}\n"
const HASH_A = styleHash(injectedStyle(LIB_A))
const HASH_B = styleHash(injectedStyle(LIB_B))
const approved = [
  { file: 'lib-a.mjs', hash: HASH_A, what: '합성 a', reviewed: '시험' },
  { file: 'lib-b.mjs', hash: HASH_B, what: '합성 b', reviewed: '시험' },
]
const libs = { 'lib-a.mjs': LIB_A, 'lib-b.mjs': LIB_B }
const GOOD = `'self' '${HASH_A}' '${HASH_B}'`
const WRONG = GOOD.replace(HASH_B, 'sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
const policy = (styleSrc) => `default-src 'self'; script-src 'self'; style-src ${styleSrc}; img-src 'self'; object-src 'none'; frame-ancestors 'none'`
const header = (styleSrc, { always = true, name = 'Content-Security-Policy' } = {}) => `add_header ${name} "${policy(styleSrc)}"${always ? ' always' : ''};`
/** 실제 설정과 같은 구조: map·log_format(따옴표 안 '#')·server(/api/·/assets/·/) — 첫 줄 주석과 줄 끝 주석에 진짜 헤더 문장을 넣어 주석이 세어지지 않는지도 본다 */
const conf = (h1, h2, h3, extra = '') => `# 주석 — ${header(GOOD)}
map $request_uri $request_path {
    ~^(?<path>[^?]*) $path;
}
log_format moajwo '$remote_addr "#따옴표 안" [$time_local] "$request_method $request_path"';
server {
    listen 8080;
    server_tokens off; # 끝 주석 ${header(GOOD)}
    add_header X-Frame-Options "DENY" always;
    ${h1}
    location /api/ {
        proxy_pass http://backend:8000/;
    }
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        ${h2}
        try_files $uri =404;
    }
    location / {
        add_header Cache-Control "no-cache";
        ${h3}
        try_files $uri $uri/ /index.html;
    }
${extra}}
`
const good = conf(header(GOOD), header(GOOD), header(GOOD))
const check = (confText, confText2 = good) => run({ readLib: (f) => libs[f], readConf: (n) => (n === 'nginx.conf' ? confText : confText2), approved })

test('정상 대조군: 세 자리 모두 있고 style-src가 self + 승인 해시 2개면 통과', () => {
  const r = check(good)
  assert.deepEqual(r.errors, [])
  assert.equal(r.ok, true)
  assert.equal(r.lines.length, 2)
})

test('cspHeaders: 주석(줄 머리·줄 끝)과 따옴표 안의 #은 설정으로 세지 않고, 자리(블록 이름)를 맞게 단다', () => {
  const hs = cspHeaders(good)
  assert.deepEqual(hs.map((h) => h.context), ['server', 'location /assets/', 'location /'])
  assert.ok(hs.every((h) => h.always && h.name === 'Content-Security-Policy'))
})

test('허용 해시 하나가 다르면 빠짐·승인 밖으로 멈춘다', () => {
  const r = check(conf(header(WRONG), header(WRONG), header(WRONG)))
  assert.equal(r.ok, false)
  assert.equal(r.errors.length, 3)
  for (const e of r.errors) {
    assert.match(e, /^nginx\.conf (server|location \/assets\/|location \/): style-src 토큰이 승인값과 다릅니다/)
    assert.ok(e.includes(`빠짐: '${HASH_B}'`))
    assert.match(e, /승인 밖: 'sha256-AAAA/)
  }
  // 한 자리만 바뀌면 그 자리의 토큰 오류와 함께 세 자리가 서로 다르다는 오류도 난다
  const one = check(conf(header(WRONG), header(GOOD), header(GOOD)))
  assert.equal(one.ok, false)
  assert.ok(one.errors.some((e) => e.startsWith('nginx.conf server: style-src 토큰이 승인값과 다릅니다')))
  assert.ok(one.errors.some((e) => e.includes('CSP 값이 서로 다릅니다')))
})

test('location / 의 CSP 헤더 한 줄을 지우면 멈춘다 (다른 두 자리는 통과)', () => {
  const r = check(conf(header(GOOD), header(GOOD), ''))
  assert.equal(r.ok, false)
  assert.deepEqual(r.errors.map((e) => e.split(':')[0]), ['nginx.conf location /'])
  assert.match(r.errors[0], /헤더가 0개/)
})

test('세 줄을 모두 주석 처리하면 세 자리 모두 없음으로 멈춘다', () => {
  const r = check(conf(`# ${header(GOOD)}`, `# ${header(GOOD)}`, `# ${header(GOOD)}`))
  assert.equal(r.ok, false)
  assert.deepEqual(r.errors.map((e) => e.split(':')[0]), ['nginx.conf server', 'nginx.conf location /assets/', 'nginx.conf location /'])
})

test('해시가 맞아도 외부 출처·와일드카드·unsafe-inline이 더 있으면 멈춘다', () => {
  for (const extra of ['https://example.invalid', '*', "'unsafe-inline'", 'https:']) {
    const r = check(conf(header(`${GOOD} ${extra}`), header(`${GOOD} ${extra}`), header(`${GOOD} ${extra}`)))
    assert.equal(r.ok, false, extra)
    assert.equal(r.errors.length, 3, extra)
    assert.ok(r.errors.every((e) => e.includes(`승인 밖: ${extra}`)), extra)
  }
})

test('style-src-elem·style-src-attr 지시어는 해시 허용을 덮으므로 멈춘다', () => {
  const r = check(conf(header(GOOD), header(GOOD), header(`${GOOD}; style-src-elem 'self'`)))
  assert.equal(r.ok, false)
  assert.ok(r.errors.some((e) => e.includes('location /: style-src-elem 지시어가 있습니다')))
  assert.ok(r.errors.some((e) => e.includes('CSP 값이 서로 다릅니다')))
})

test('Report-Only 헤더·예상 밖 자리의 CSP·always 없음도 멈춘다', () => {
  const ro = check(conf(header(GOOD), header(GOOD), header(GOOD), `    location /x/ {\n        ${header(GOOD, { name: 'Content-Security-Policy-Report-Only' })}\n    }\n`))
  assert.ok(ro.errors.some((e) => e.includes('location /x/: Content-Security-Policy-Report-Only')))
  const extra = check(conf(header(GOOD), header(GOOD), header(GOOD), `    location /x/ {\n        ${header(GOOD)}\n    }\n`))
  assert.ok(extra.errors.some((e) => e.includes('location /x/: 예상 밖 자리의 CSP 헤더')))
  const noAlways = check(conf(header(GOOD), header(GOOD, { always: false }), header(GOOD)))
  assert.deepEqual(noAlways.errors, ['nginx.conf location /assets/: CSP 헤더에 always가 없습니다 (오류 응답에도 붙어야 함)'])
})

test('두 설정은 따로 검사한다 — 둘째 설정만 틀려도 멈춘다', () => {
  const r = check(good, conf(header(GOOD), header(GOOD), header(`'self' '${HASH_A}'`)))
  assert.equal(r.ok, false)
  assert.ok(r.errors.length >= 1 && r.errors.every((e) => e.startsWith('nginx.render.conf')))
  assert.ok(r.errors[0].startsWith('nginx.render.conf location /: style-src 토큰이 승인값과 다릅니다'))
})

test('라이브러리가 넣는 내용이 바뀌면 설정이 그대로여도 멈추고, 해시를 자동으로 바꾸지 않는다', () => {
  const changed = { ...libs, 'lib-a.mjs': LIB_A.replace('touch-action: none', 'touch-action: manipulation') }
  const r = run({ readLib: (f) => changed[f], readConf: () => good, approved })
  assert.equal(r.ok, false)
  assert.equal(r.errors.length, 1)
  assert.match(r.errors[0], /^lib-a\.mjs: 라이브러리가 넣는 내용이 승인된 내용과 다릅니다/)
  assert.match(r.errors[0], /해시를 복사하지 말고/)
  assert.equal(r.lines[0].endsWith(HASH_A), false)
})

test('라이브러리 소스에서 삽입 문장을 못 찾거나 둘 이상이면 멈춘다', () => {
  const r = run({ readLib: () => 'nothing here', readConf: () => good, approved })
  assert.ok(r.errors[0].includes('0개 찾았습니다'))
})

test('checkPolicy: style-src가 없거나 둘이면 멈춘다', () => {
  assert.deepEqual(checkPolicy("default-src 'self'", ["'self'"]), ['style-src 지시어가 0개입니다(1개여야 함)'])
  assert.deepEqual(checkPolicy("style-src 'self'; style-src 'self'", ["'self'"]), ['style-src 지시어가 2개입니다(1개여야 함)'])
  assert.deepEqual(checkPolicy("style-src 'self'", ["'self'"]), [])
})

test('checkConf: 자리 목록을 바꿔 부르면 그 목록으로 검사한다', () => {
  const msg = (ctx) => `x ${ctx}: 예상 밖 자리의 CSP 헤더 — 자리가 바뀌었으면 실제 응답을 확인하고 REQUIRED_CONTEXTS를 함께 고치세요`
  assert.deepEqual(checkConf('x', good, ["'self'", `'${HASH_A}'`, `'${HASH_B}'`], ['server']), [msg('location /assets/'), msg('location /')])
})

test('실제 파일: node_modules가 있으면 명령줄 실행이 통과한다 (없으면 건너뜀)', (t) => {
  const here = dirname(fileURLToPath(import.meta.url))
  if (!existsSync(join(here, '..', 'node_modules', 'react-aria'))) return t.skip('node_modules/react-aria 없음')
  const out = execFileSync(process.execPath, [join(here, 'check-csp-style-hashes.mjs')], { encoding: 'utf8' })
  assert.match(out, /CSP style-src 검사 통과/)
})
