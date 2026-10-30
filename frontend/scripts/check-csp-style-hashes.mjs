#!/usr/bin/env node
// CSP style-src 검사 — `npm run build` 앞(prebuild)에서 돈다: 로컬 빌드, Dockerfile(세 판 모두 npm run build), CI 이미지 검사.
//
// 배경(2026-10-30 확인, react-aria 3.52.1): usePress는 첫 pressable 요소가 마운트될 때 한 번 `<style id="react-aria-pressable-style">`
// (touch-action 규칙)을, usePreventScroll은 iOS(모바일 WebKit)에서 모달이 열릴 때 `<style>`(overscroll-behavior 규칙)을 <head>에 넣는다.
// 둘 다 `<meta property="csp-nonce">`가 없으면 nonce 없이 넣으므로 `style-src 'self'`에 막혀 콘솔 경고가 나고 규칙은 적용되지 않았다.
// 그래서 두 nginx 설정의 style-src에 그 두 내용의 해시만 허용했다 (unsafe-inline·와일드카드·외부 출처가 아님).
//
// 이 검사가 보는 것 (주석은 설정으로 세지 않는다):
//   1) 라이브러리가 지금 넣는 내용의 해시가 아래 APPROVED(승인 목록)와 같다 — 라이브러리 판이 바뀌어 내용이 달라지면 여기서 멈춘다.
//   2) 두 설정(nginx.conf·nginx.render.conf)의 server·`location /assets/`·`location /` 세 자리마다 Content-Security-Policy 헤더가
//      정확히 하나씩, `always`와 함께 있다 (add_header가 있는 location은 server의 헤더를 물려받지 않으므로 세 곳 모두 필요).
//      다른 자리의 CSP 헤더, Report-Only 헤더, 세 자리의 값이 서로 다른 것도 멈춘다 — 자리가 바뀌면 REQUIRED_CONTEXTS를 실제 응답
//      확인과 함께 고친다.
//   3) 그 헤더의 style-src 토큰이 정확히 `'self'` + 승인 해시들이다 — 외부 출처·와일드카드·'unsafe-inline' 같은 추가 허용은 해시가
//      맞아도 멈춘다. style-src-elem·style-src-attr이 있으면 style-src의 해시 허용을 덮으므로 멈춘다.
//   일반적인 nginx·CSP 파서가 아니다: include·변수·따옴표 안 이스케이프는 다루지 않는다 (이 저장소의 두 설정 구조에 맞춘 최소 검사).
//
// 라이브러리가 넣는 내용이 바뀌었을 때 — 새 해시를 그대로 복사해 승인하지 않는다:
//   (1) 넣는 CSS 내용, (2) 넣는 경로(어느 훅이 언제·어떤 조건에서), (3) 범위(선택자가 어느 요소에 닿는지)를 검토한다.
//   넓은 규칙(모든 요소에 닿는 선택자·여러 속성)이면 nonce 방식 등 다른 대응을 먼저 검토한다.
//   승인하면 APPROVED의 hash·what·reviewed와 두 nginx 설정의 모든 style-src를 함께 고치고, 바뀐 이유(라이브러리 판)를 커밋 메시지에 적는다.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

/** 승인한 인라인 <style> 내용 — 해시가 곧 승인값이다. 라이브러리 내용이 이와 다르면 멈춘다 (자동으로 덮지 않는다) */
export const APPROVED = [
  {
    file: 'node_modules/react-aria/dist/private/interactions/usePress.mjs',
    hash: 'sha256-38RhXrc7EdReTKsOm23ZPOCUgniTUUcjky8QOOrQx6o=',
    what: 'usePress — 첫 pressable 요소 마운트 때 한 번: [data-react-aria-pressable] { touch-action: pan-x pan-y pinch-zoom } (터치 기기의 두 번 탭 확대·클릭 지연 보완, pressable 요소에만)',
    reviewed: '2026-10-30 · react-aria 3.52.1',
  },
  {
    file: 'node_modules/react-aria/dist/private/overlays/usePreventScroll.mjs',
    hash: 'sha256-gYiS/BvZvRcK27JIXTuwhZ3hs2+VJ1X+2gUlE+farlg=',
    what: 'usePreventScroll — iOS(모바일 WebKit)에서 모달이 열린 동안: * { overscroll-behavior: contain } (중첩 스크롤 영역의 스크롤 연쇄 완화, 모달이 닫히면 제거)',
    reviewed: '2026-10-30 · react-aria 3.52.1',
  },
]
export const CONFS = ['nginx.conf', 'nginx.render.conf']
/** CSP 헤더가 있어야 하는 자리(블록 이름) — 현재 두 설정의 구조. 구조가 바뀌면 실제 응답(/, 앱 경로, /assets/)을 확인하고 함께 고친다 */
export const REQUIRED_CONTEXTS = ['server', 'location /assets/', 'location /']
const CSP_HEADER = /^add_header\s+(Content-Security-Policy(?:-Report-Only)?)\s+(?:"([^"]*)"|'([^']*)'|(\S+))(\s+always)?\s*$/i

/** 라이브러리 소스에서 넣는 <style> 내용을 뽑는다: `style.textContent = \`...\`.trim()` 과 `${...}` 상수 치환 */
export function injectedStyle(src, file = '') {
  const found = [...src.matchAll(/\.textContent = `([\s\S]*?)`\.trim\(\)/g)]
  if (found.length !== 1) throw new Error(`${file}: 넣는 <style> 내용을 ${found.length}개 찾았습니다(1개여야 함) — 라이브러리 구현이 바뀌었는지 확인하세요`)
  return found[0][1]
    .replace(/\$\{([^}]+)\}/g, (_, name) => {
      const v = src.match(new RegExp(name.replace(/\$/g, '\\$') + ` = ['"]([^'"]+)['"]`))
      if (!v) throw new Error(`${file}: ${name} 값을 찾지 못했습니다`)
      return v[1]
    })
    .trim()
}

export const styleHash = (css) => `sha256-${createHash('sha256').update(css).digest('base64')}`

/** nginx 설정에서 CSP add_header 지시어를 블록 자리와 함께 모은다. 주석(`#` — 따옴표 밖, 토큰 머리)은 건너뛴다 */
export function cspHeaders(conf) {
  const out = []
  const stack = []
  let buf = ''
  let quote = null
  const directive = (text) => {
    const m = text.match(CSP_HEADER)
    if (m) out.push({ context: stack[stack.length - 1] ?? '(최상위)', name: m[1], value: m[2] ?? m[3] ?? m[4], always: Boolean(m[5]) })
  }
  for (let i = 0; i < conf.length; i++) {
    const ch = conf[i]
    if (quote) {
      buf += ch
      if (ch === quote) quote = null
      continue
    }
    if (ch === '#' && (buf === '' || /\s/.test(buf[buf.length - 1]))) {
      while (i < conf.length && conf[i] !== '\n') i++
      continue
    }
    if (ch === '"' || ch === "'") {
      quote = ch
      buf += ch
    } else if (ch === '{') {
      stack.push(buf.trim().replace(/\s+/g, ' '))
      buf = ''
    } else if (ch === '}') {
      stack.pop()
      buf = ''
    } else if (ch === ';') {
      directive(buf.trim())
      buf = ''
    } else {
      buf += ch
    }
  }
  return out
}

/** CSP 값 하나의 style-src 토큰이 정확히 want인지 — 빠짐·추가·중복 지시어·style-src-elem/attr을 오류로 */
export function checkPolicy(value, wantTokens) {
  const errors = []
  const directives = value.split(';').map((s) => s.trim()).filter(Boolean).map((s) => s.split(/\s+/))
  const names = directives.map((d) => d[0].toLowerCase())
  for (const n of ['style-src-elem', 'style-src-attr']) if (names.includes(n)) errors.push(`${n} 지시어가 있습니다 — style-src의 해시 허용을 덮어쓰므로 두지 않습니다`)
  const styles = directives.filter((d) => d[0].toLowerCase() === 'style-src')
  if (styles.length !== 1) {
    errors.push(`style-src 지시어가 ${styles.length}개입니다(1개여야 함)`)
    return errors
  }
  const have = new Set(styles[0].slice(1))
  const want = new Set(wantTokens)
  const missing = [...want].filter((t) => !have.has(t))
  const extra = [...have].filter((t) => !want.has(t))
  if (missing.length || extra.length) errors.push(`style-src 토큰이 승인값과 다릅니다\n    빠짐: ${missing.join(' ') || '-'}\n    승인 밖: ${extra.join(' ') || '-'}`)
  return errors
}

/** 설정 파일 하나를 검사해 오류 문구 목록을 돌려준다 (비면 통과) */
export function checkConf(name, text, wantTokens, required = REQUIRED_CONTEXTS) {
  const errors = []
  const headers = cspHeaders(text)
  for (const h of headers.filter((h) => /report-only/i.test(h.name))) errors.push(`${name} ${h.context}: Content-Security-Policy-Report-Only 헤더가 있습니다 — 별도 정책을 두지 않습니다`)
  const csp = headers.filter((h) => h.name.toLowerCase() === 'content-security-policy')
  for (const h of csp.filter((h) => !required.includes(h.context))) errors.push(`${name} ${h.context}: 예상 밖 자리의 CSP 헤더 — 자리가 바뀌었으면 실제 응답을 확인하고 REQUIRED_CONTEXTS를 함께 고치세요`)
  const values = []
  for (const ctx of required) {
    const here = csp.filter((h) => h.context === ctx)
    if (here.length !== 1) {
      errors.push(`${name} ${ctx}: Content-Security-Policy 헤더가 ${here.length}개입니다(1개여야 함 — 주석 처리되거나 지워지지 않았는지)`)
      continue
    }
    const [h] = here
    if (!h.always) errors.push(`${name} ${ctx}: CSP 헤더에 always가 없습니다 (오류 응답에도 붙어야 함)`)
    for (const e of checkPolicy(h.value, wantTokens)) errors.push(`${name} ${ctx}: ${e}`)
    values.push(h.value)
  }
  if (new Set(values).size > 1) errors.push(`${name}: ${required.join(' · ')}의 CSP 값이 서로 다릅니다 — 세 자리는 같은 정책이어야 합니다`)
  return errors
}

/** 전체 검사 — 파일 읽기는 바꿔 끼울 수 있다(시험). 돌려주는 값: { ok, errors, lines } */
export function run({ readLib, readConf, approved = APPROVED, confs = CONFS, required = REQUIRED_CONTEXTS }) {
  const errors = []
  const lines = []
  for (const a of approved) {
    let css
    try {
      css = injectedStyle(readLib(a.file), a.file)
    } catch (e) {
      errors.push(e.message)
      continue
    }
    const hash = styleHash(css)
    lines.push(`${a.file} → ${hash}`)
    if (hash !== a.hash) {
      errors.push(`${a.file}: 라이브러리가 넣는 내용이 승인된 내용과 다릅니다\n    승인(${a.reviewed}): ${a.hash}\n    지금: ${hash}\n    지금 내용: ${JSON.stringify(css.slice(0, 200))}\n    → 해시를 복사하지 말고 내용·경로·범위를 검토한 뒤 APPROVED와 두 설정의 style-src를 함께 갱신하세요 (파일 머리말)`)
    }
  }
  const wantTokens = ["'self'", ...approved.map((a) => `'${a.hash}'`)]
  for (const conf of confs) errors.push(...checkConf(conf, readConf(conf), wantTokens, required))
  return { ok: errors.length === 0, errors, lines }
}

function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..')
  const read = (p) => readFileSync(join(root, p), 'utf8')
  const r = run({ readLib: read, readConf: read })
  for (const l of r.lines) console.log(l)
  if (!r.ok) {
    for (const e of r.errors) console.error(`[CSP 검사 실패] ${e}`)
    console.error('빌드를 멈춥니다. 라이브러리 내용이 바뀐 경우의 절차는 이 스크립트 머리말을 보세요 — 승인 없이 해시만 바꾸지 않습니다.')
    process.exit(1)
  }
  console.log(`CSP style-src 검사 통과 (${CONFS.join(' · ')} — ${REQUIRED_CONTEXTS.join(' · ')} 각 1개 · style-src = 'self' + 승인 해시 ${APPROVED.length}개)`)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main()
