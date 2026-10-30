#!/usr/bin/env node
// CSP style-src 해시 대조 — react-aria가 실행 중에 <head>에 넣는 인라인 <style> 두 개의 내용 해시가 nginx 설정(nginx.conf·nginx.render.conf)의
// 모든 style-src 해시와 같은지 확인한다. `npm run build` 앞(prebuild)에서 돈다 — 로컬 빌드, Dockerfile(세 판 모두 npm run build), CI 이미지 검사.
//
// 배경(2026-10-30 확인, react-aria 3.52.1): usePress는 첫 pressable 요소가 마운트될 때 한 번 `<style id="react-aria-pressable-style">`
// (touch-action 규칙)을, usePreventScroll은 iOS에서 모달이 열릴 때 `<style>`(overscroll-behavior 규칙)을 넣는다. 둘 다 `<meta property="csp-nonce">`가
// 없으면 nonce 없이 넣으므로 `style-src 'self'`에 막혀 콘솔 경고가 나고 규칙은 적용되지 않았다. 내용 해시 허용은 그 두 내용만 허용한다
// (unsafe-inline·와일드카드가 아님). 라이브러리 판이 바뀌어 넣는 내용이 달라지면 해시가 어긋나 여기서 멈춘다 — 그때 아래 출력의 새 해시로 두 설정을 고친다.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => readFileSync(join(root, p), 'utf8')

/** 라이브러리 파일에서 넣는 <style> 내용을 뽑는다: `style.textContent = \`...\`.trim()` 과 `${...}` 상수 치환 */
function injectedStyle(file) {
  const src = read(file)
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

const sources = [
  'node_modules/react-aria/dist/private/interactions/usePress.mjs',
  'node_modules/react-aria/dist/private/overlays/usePreventScroll.mjs',
]
const expected = sources.map((file) => ({ file, hash: `sha256-${createHash('sha256').update(injectedStyle(file)).digest('base64')}` }))
const want = new Set(expected.map((e) => `'${e.hash}'`))

let failed = false
for (const conf of ['nginx.conf', 'nginx.render.conf']) {
  const directives = [...read(conf).matchAll(/style-src ([^;]+);/g)].map((m) => m[1])
  if (directives.length === 0) {
    console.error(`${conf}: style-src 지시어가 없습니다`)
    failed = true
    continue
  }
  directives.forEach((d, i) => {
    const have = new Set(d.match(/'sha256-[A-Za-z0-9+/=]+'/g) ?? [])
    const missing = [...want].filter((h) => !have.has(h))
    const extra = [...have].filter((h) => !want.has(h))
    if (missing.length || extra.length) {
      failed = true
      console.error(`${conf} style-src #${i + 1}: 해시가 라이브러리 내용과 다릅니다\n  빠짐: ${missing.join(' ') || '-'}\n  출처 모름: ${extra.join(' ') || '-'}`)
    }
  })
}
for (const e of expected) console.log(`${e.file} → ${e.hash}`)
if (failed) {
  console.error('고치려면: 위 해시로 nginx.conf·nginx.render.conf의 모든 style-src를 맞추고, 바뀐 이유(라이브러리 판)를 커밋 메시지에 적으세요.')
  process.exit(1)
}
console.log('CSP style-src 해시 대조 통과 (nginx.conf · nginx.render.conf)')
