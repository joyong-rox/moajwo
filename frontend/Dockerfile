FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.30-alpine
# 기반 이미지(떠도는 태그)에 아직 든 취약 판만 같은 Alpine 안정 저장소의 수정판으로 올린다 — brotli-libs 1.2.0-r1
# (CVE-2026-111497, 수정 1.2.1-r0). 다른 패키지는 올리지 않고, 기반 이미지가 수정판을 담으면 아무것도 바꾸지 않는다.
RUN apk upgrade --no-cache brotli-libs
# 비루트(이미지의 nginx 사용자, uid 101)로 실행한다: 1024 아래 포트를 쓰지 않고(8080), pid·임시 파일은 nginx가 쓸 수 있는
# 곳에 둔다. 'user' 지시어는 root로 시작할 때만 뜻이 있어 지운다. 기반 이미지의 시작 스크립트는 비루트에서도 그대로 돈다
# (설정 파일을 고치지 못하면 건너뜀).
RUN sed -i -e '/^user /d' -e 's,^pid .*;,pid /tmp/nginx.pid;,' /etc/nginx/nginx.conf     && chown -R nginx:nginx /var/cache/nginx
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
