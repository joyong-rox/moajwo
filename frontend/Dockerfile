# 빌드 단계: node 24.22.1 (2026-10-21 보안 판 — 2026-10 eMudhra 인증서 사고를 반영한 뿌리 인증서 NSS 3.131.1). 떠도는 태그가
# 아니라 멀티아키 색인 digest로 고정한다 (2026-10-22 Docker Hub 조회 — 그날의 24-alpine과 같은 판). 빌드 기록에 실제 node·npm 판과
# 내장 뿌리 인증서 가운데 emSign·eMudhra 이름이 남았는지(0이어야 함)를 남긴다. 빌드 도구(npm)는 node 내장 CA로 레지스트리에 연결한다.
FROM node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS build
WORKDIR /app
RUN node --version && npm --version \
    && node -e "const {X509Certificate}=require('crypto');const r=require('tls').rootCertificates;const bad=r.filter(p=>/emsign|emudhra/i.test(new X509Certificate(p).subject));console.log('node root certificates', r.length, 'emSign/eMudhra', bad.length)"
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# 실행 기반도 digest로 고정한다 (docs/DEPLOY.md 11장): nginx:1.30-alpine 멀티아키 색인 — 2026-11-09부터 Render용
# frontend/Dockerfile.render와 같은 색인이다. 그 linux/amd64 매니페스트는
# sha256:bb8f722b4be005fcd7827816a136ab779fd40185d85b5171be915cc7fc0575c7 (nginx 1.30.6-r1 · Alpine 3.24.4, 2026-11-05T19:55:22Z 생성,
# 2026-11-09 Docker Hub 조회). 이전 고정은 sha256:47928bf…(Alpine 3.24.3, 2026-10-28~11-09).
FROM nginx:1.30-alpine@sha256:e9b2d7624fd7957f4ed44ae0a272d93fb73aeb8512ba8916f8118ae6143fd115
# 아래 apk 단계는 빌드할 때 저장소에서 받으므로 기반을 고정해도 빌드 시점에 따라 판이 달라질 수 있다 — 반영 전 완성 이미지 목록 대조가 잡는다.
# 기반 이미지에 아직 든 취약 판만 같은 Alpine 안정 저장소의 수정판으로 올린다 — brotli-libs 1.2.0-r1
# (CVE-2026-111497, 수정 1.2.1-r0), curl·libcurl 8.22.0-r0 (CVE-2026-92392, 수정 8.23.0-r0), libexpat 2.9.0-r0
# (CVE-2026-117610·CVE-2026-118370 외 2건, 수정 2.10.0-r0 — Alpine v3.24 secdb, 2026-11-04 확인: 그날의 nginx:1.30-alpine도 2.9.0-r0),
# zlib 1.3.2-r1 (CVE-2026-4738, 수정 1.4-r0 — Alpine v3.24 main, 2026-11-09 확인, docs/SECURITY-ZLIB-20261109.md). 다른 패키지는 올리지 않고,
# 기반 이미지가 수정판을 담으면 아무것도 바꾸지 않는다. OS 신뢰 저장소(ca-certificates·ca-certificates-bundle)도 최신으로 올린다
# (2026-10 eMudhra 인증서 사고 대응, docs/SECURITY-CA-20261026.md — 로컬 앞단은 backend에 http로 붙어 상류 검증은 쓰지 않음).
RUN apk upgrade --no-cache brotli-libs curl libcurl libexpat zlib ca-certificates ca-certificates-bundle
# 비루트(이미지의 nginx 사용자, uid 101)로 실행한다: 1024 아래 포트를 쓰지 않고(8080), pid·임시 파일은 nginx가 쓸 수 있는
# 곳에 둔다. 'user' 지시어는 root로 시작할 때만 뜻이 있어 지운다. 기반 이미지의 시작 스크립트는 비루트에서도 그대로 돈다
# (설정 파일을 고치지 못하면 건너뜀).
RUN sed -i -e '/^user /d' -e 's,^pid .*;,pid /tmp/nginx.pid;,' /etc/nginx/nginx.conf     && chown -R nginx:nginx /var/cache/nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
