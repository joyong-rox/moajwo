# Render test frontend

This image (`frontend/Dockerfile.render` with `nginx.render.conf`) routes
`/api/` requests to `https://moajwo-test-api.onrender.com/`. The existing
Dockerfile and nginx.conf remain the local Docker Compose configuration.

## Services (read-only lookup, 2026-10-25)

Observed through the Render management API without deploying or changing
anything. These are the values at that time; read them again right before any
merge or switch (`docs/EXTERNAL_DB.md` 8-3).

| Service | Role | Branch | Auto-Deploy | Dockerfile / context | Health check path |
| --- | --- | --- | --- | --- | --- |
| `moajwo-test` | frontend at https://moajwo-test.onrender.com/ (the address users have been using) | test/render-20261021 | Off | frontend/Dockerfile.render-test / frontend | empty |
| `moajwo-test-web` | frontend (same API) | main | Off | frontend/Dockerfile.render / frontend | empty |
| `moajwo-test-api` | API (its SQLite holds the accounts) | main | Off | backend/Dockerfile / backend | empty |

All three are on the free plan. With Auto-Deploy Off, merging into main does
not by itself redeploy them; that is not a statement that no other trigger
exists, and Off is not an approval to deploy manually.

`frontend/Dockerfile.render-test` (what `moajwo-test` builds today) exists
only on `test/render-20261021`, not in this tree. Deploying this tree to
`moajwo-test` means changing its branch, Dockerfile path and context together;
never keep the old path and deploy a newer commit.

## Proposed settings for `moajwo-test` (apply only after approval)

Part of the switch approval in `docs/EXTERNAL_DB.md` 8-5; nothing here has been
saved on Render.

| Setting | Value |
| --- | --- |
| Branch | the approved release (main after the merge) |
| Root Directory | empty |
| Docker Build Context Directory | frontend |
| Dockerfile Path | frontend/Dockerfile.render |
| Docker Command | empty (remove the entire previous command) |
| Health Check Path | `/api/health` (nginx → API `/health`, which runs `SELECT 1`), or `/` — owner decision, see below |

Leave `moajwo-test-web` as it is: do not delete or stop it because it looks
like a duplicate. Whether it is still needed is a separate task. The API keeps
its branch and Dockerfile; its proposed health check path is `/health`.

The image runs nginx as the non-root `nginx` user (uid 101) and listens on
port 8080 inside the container (the current `moajwo-test` image listens on 80
as root). Render detects the open port; if it does not, add the environment
variable `PORT=8080` to the service. The public URL does not change.

Health check path trade-off: with `/api/health`, a new frontend deploy only
goes live once the API and its database answer, but the frontend's
availability is then tied to the API (Render stops routing to an instance after
15 seconds of failed checks and restarts it after 60), and every check while
the frontend is up is a real request to the API's public URL, so the API does
not spin down either. With `/`, only nginx itself is checked. Whether Render's
own checks count as inbound traffic for free spin-down is not documented; after
setting a path, confirm that idle services still spin down and watch the free
instance hours.

Save these settings, then deploy the approved commit. Do not use Restart
service to apply newly saved configuration. The image uses nginx's exec-form
CMD; no shell command or startup-time sed substitution is needed.

Verify that the deployment becomes live, then check the homepage, an API
request, and login separately. A successful frontend deploy does not establish
that the API or authentication is healthy. Check both services' runtime logs
if API requests fail. This file targets the existing test API hostname; update
nginx.render.conf before using it for another environment.

The proxy does not verify the API's certificate: `proxy_ssl_verify` is not set
(nginx defaults to off), the same as the current `moajwo-test` image. The
connection is encrypted, but nginx does not check that it reached the real API.
Turning it on needs `proxy_ssl_verify on` with a CA bundle
(`proxy_ssl_trusted_certificate`) and a test against the real Render
certificate chain; it is left as an owner decision.

Optional local container verification (requires Docker):

```sh
docker build -f frontend/Dockerfile.render -t moajwo-render-check frontend
docker run --rm moajwo-render-check nginx -t
```

`diagnostics/ci/image_check.sh` also runs the image in an isolated network in
front of an HTTPS stand-in API with the same host name (self-signed; it does
not check SNI or Host) to test login through the proxy, cookie attributes and
security headers. It never contacts Render.
