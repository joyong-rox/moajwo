# Render test frontend

This image is for `moajwo-test-web` and routes `/api/` requests to
`https://moajwo-test-api.onrender.com/`. The existing Dockerfile and nginx.conf
remain the local Docker Compose configuration.

After merging this change into main, set the existing Render service to:

| Setting | Value |
| --- | --- |
| Branch | main |
| Root Directory | empty |
| Docker Build Context Directory | frontend |
| Dockerfile Path | frontend/Dockerfile.render |
| Docker Command | empty (remove the entire previous command) |

Save these settings, then deploy the latest commit. Do not use Restart service
to apply newly saved configuration. The image uses nginx's exec-form CMD;
no shell command or startup-time sed substitution is needed.

Verify that the deployment becomes live, then check the homepage, an API
request, and login separately. A successful frontend deploy does not establish
that the API or authentication is healthy. Check both services' runtime logs
if API requests fail. This file targets the existing test API hostname; update
nginx.render.conf before using it for another environment.

Optional local container verification (requires Docker):

```sh
docker build -f frontend/Dockerfile.render -t moajwo-render-check frontend
docker run --rm moajwo-render-check nginx -t
```
