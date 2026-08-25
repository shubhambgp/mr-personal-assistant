# Deployment — the CD half of the pipeline

CI already exists (`.github/workflows/ci.yml`): on every push to `main` and every
PR it runs ruff + backend tests, the guardrail evals against a loaded database,
the frontend gates, a dependency audit, a confidentiality scan, and it *builds*
both Docker images. What it does not do is put them anywhere.

This is the other half: **CI proves the images build, CD ships them.**

```
push to main ──► CI (existing) ──► CD ──► build + push to GHCR
                   green?           │      tag = commit SHA
                                    └────► ssh to the server
                                           compose pull && up -d
                                           health gate on /api/health
```

CD waits for CI rather than duplicating it, so a red test suite can never reach
the server.

---

## 1. Once, on the server

A single small VM is enough — this app runs as **one** backend process by
design (see §5).

```bash
# docker + compose, then:
sudo mkdir -p /srv/mr-assistant && cd /srv/mr-assistant
# copy docker-compose.yml and the four schema files from the repo
```

Create `/srv/mr-assistant/.env` — compose interpolates it, and it never leaves
the server:

```ini
POSTGRES_PASSWORD=<generated>
RO_PASSWORD=<generated>
JWT_SECRET=<python -c "import secrets;print(secrets.token_urlsafe(48))">
OPENAI_API_KEY=<yours>
ENVIRONMENT=production
COOKIE_SECURE=true
CORS_ORIGINS=https://assistant.example.com
GOOGLE_CLIENT_ID=<from the Google console>
GOOGLE_CLIENT_SECRET=<from the Google console>
GOOGLE_REDIRECT_URI=https://assistant.example.com/api/agenda/callback
AGENDA_ENCRYPTION_KEY=<python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())">
```

Apply the schemas once (all four are idempotent, so re-running is safe):

```bash
docker compose up -d db
for f in seed_app chat_history agent_schema agenda_schema; do
  docker compose exec -T db psql -U qorvexa -d qorvexa < backend/etl/$f.sql
done
```

Then put TLS in front — Caddy or nginx terminating HTTPS and proxying to the
`web` container on `:8080`. TLS is not optional: `ENVIRONMENT=production`
refuses to start unless `COOKIE_SECURE=true`, and a Secure cookie over plain
HTTP means nobody can log in.

## 2. One change to `docker-compose.yml`

`CORS_ORIGINS`, `ENVIRONMENT` and `COOKIE_SECURE` are already overridable from
the `.env` above — they used to be hardcoded to the local-Docker values, which
meant a production deploy could not turn on a Secure cookie or switch `/docs`
off. Nothing to do there.

What is still missing is where the images come from. Add an `image:` line beside
each existing `build:`, so compose builds locally *and* pulls in production from
the same file:

```yaml
  backend:
    build: ./backend
    image: ghcr.io/<owner>/<repo>/backend:${TAG:-latest}

  web:
    build: ./frontend
    image: ghcr.io/<owner>/<repo>/web:${TAG:-latest}
```

## 3. Three GitHub secrets

| Secret             | What it is                                                    |
| ------------------ | ------------------------------------------------------------- |
| `DEPLOY_HOST`    | the server's hostname or IP                                   |
| `DEPLOY_USER`    | the ssh user that owns`/srv/mr-assistant`                   |
| `DEPLOY_SSH_KEY` | that user's private key                                       |
| `DEPLOY_URL`     | `https://assistant.example.com` — the health gate polls it |

Application secrets stay in the server's `.env` and are **not** GitHub secrets.
Rotating a key is then one edit and a restart, not a deploy.

## 4. `.github/workflows/cd.yml`

```yaml
name: CD

on:
  workflow_run:
    workflows: [CI]
    types: [completed]
    branches: [main]

concurrency:
  group: cd
  cancel-in-progress: false        # never interrupt a half-applied deploy

jobs:
  deploy:
    # The whole point: ship only what CI proved.
    if: github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    env:
      REGISTRY: ghcr.io/${{ github.repository }}
      TAG: ${{ github.event.workflow_run.head_sha }}

    steps:
      - uses: actions/checkout@v5
        with:
          ref: ${{ github.event.workflow_run.head_sha }}

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      # Two tags per image: the SHA is what a rollback names, `latest` is what
      # the server's compose file resolves when TAG is unset.
      - name: Build and push
        run: |
          set -euo pipefail
          docker build -t "$REGISTRY/backend:$TAG" -t "$REGISTRY/backend:latest" ./backend
          docker build -t "$REGISTRY/web:$TAG"     -t "$REGISTRY/web:latest"     ./frontend
          docker push --all-tags "$REGISTRY/backend"
          docker push --all-tags "$REGISTRY/web"

      - name: Deploy
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          envs: TAG
          script: |
            set -euo pipefail
            cd /srv/mr-assistant
            echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            export TAG
            docker compose pull
            docker compose up -d --remove-orphans
            docker image prune -f

      - name: Health gate
        run: |
          for _ in $(seq 1 30); do
            if curl -fsS "${{ secrets.DEPLOY_URL }}/api/health" | grep -q '"status":"ok"'; then
              echo "healthy"; exit 0
            fi
            sleep 4
          done
          echo "::error::deployment never reported healthy — see §6 to roll back"
          exit 1
```

`/api/health` returns `degraded` rather than `ok` when either Postgres pool,
Qdrant, the manifest or the OpenAI key is unavailable — so the gate catches a
container that started but cannot serve.

## 5. Five deploy-time traps, all specific to this app

1. **`CORS_ORIGINS` is also the OAuth landing page.** The Gmail consent callback
   redirects to the first origin in that list. Set it wrong and a rep who
   connects Gmail successfully lands on a 404 while the credential is stored
   fine — a bug that looks exactly like a failed connection.
2. **`GOOGLE_REDIRECT_URI` must match the Google console entry byte for byte**,
   including the scheme and any trailing path.
3. **Never add `--workers`.** The rate limiters, the metrics, the three caches
   and the embedded Qdrant lock are all per-process; a second worker silently
   multiplies every rate limit. The reason is in `backend/Dockerfile`.
4. **Schema changes are manual.** There is no migration tool — apply the
   relevant `.sql` before the new image starts. All four files are idempotent
   and `agenda_schema.sql` is double-apply tested in CI.
5. **Beyond one process, Qdrant needs a server.** Local mode holds a file lock,
   so set `QDRANT_URL` before running anything alongside the backend.

## 6. Rollback

Every deploy is tagged with its commit SHA, so a rollback is a deploy of an
older one — no rebuild:

```bash
cd /srv/mr-assistant
TAG=<previous-sha> docker compose up -d
```

Keep the SHA of the last known-good deploy where the on-call person can find it.

## 7. What this pipeline deliberately does not do

* **No migration gate.** Trap 4 is a human step, and the pipeline will happily
  ship an image whose code expects a column nobody added.
* **No blue/green.** `compose up -d` restarts the backend in place, so a deploy
  is a few seconds of 502 and any turn in flight is dropped. Fine for a field
  force; not fine for continuous use.
* **No horizontal scale.** One process, by design (trap 3). Redis for the
  limiters and caches is what unlocks a second one.
* **Secrets live on the server.** Simpler than syncing a secrets manager, and
  the trade is that server access is production access.

Read `README.md` § "Before deploying anywhere real" alongside this — and treat
any secret that has ever been in a local `.env` as compromised before the first
real deploy.
