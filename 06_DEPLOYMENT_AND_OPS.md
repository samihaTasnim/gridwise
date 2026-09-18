# 06 · Deployment and Operations

Target: your own always-on Linux VPS with Docker + Nginx (no cold starts, full control of timeouts). Do steps 1–3 in the first 20 minutes with a `/health`-only app, so deployment is never the thing that fails at 10:50 PM.

## 1. DNS + TLS

1. Add an `A` record: `gridwise.<yourdomain>` → VPS IP (low TTL).
2. Issue a certificate: `sudo certbot --nginx -d gridwise.<yourdomain>`.

No spare domain? A plain `http://<ip>:<port>` base URL technically satisfies the rules, but HTTPS on a hostname is safer against harness quirks.

## 2. docker-compose.yml (on the VPS, `/opt/gridwise`)

```yaml
services:
  gridwise:
    image: ghcr.io/<user>/gridwise:1.0.0
    env_file: .env                 # lives only on the server
    ports:
      - "127.0.0.1:8000:8000"      # only Nginx can reach it
    restart: unless-stopped
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```

## 3. Nginx site

```nginx
server {
    server_name gridwise.<yourdomain>;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_connect_timeout 5s;
        proxy_read_timeout 35s;      # judge timeout is 30s; don't cut earlier
        proxy_send_timeout 35s;
    }
    # listen 443 ssl + certificate lines are added by certbot
}
```

If this VPS sits behind Cloudflare or has rate-limit / WAF rules for your other sites, make sure they don't throttle bursts of POSTs from a single IP — that is exactly what a judge harness looks like.

## 4. Build and publish the fallback image

```bash
echo $GHCR_PAT | docker login ghcr.io -u <user> --password-stdin
docker build -t ghcr.io/<user>/gridwise:1.0.0 .
docker push ghcr.io/<user>/gridwise:1.0.0
```

Then in GitHub → Packages → gridwise → Package settings → **change visibility to Public** (GHCR packages are private by default; a private image scores 0 of the 4 Docker points). Docker Hub public repos work just as well.

Verify like a judge would:

```bash
docker logout ghcr.io
docker pull ghcr.io/<user>/gridwise:1.0.0
docker run --rm -p 8000:8000 ghcr.io/<user>/gridwise:1.0.0          # no env vars
curl -s http://localhost:8000/health                                # {"status":"ok"}
```

And with a key:

```bash
docker run --rm -p 8000:8000 \
  -e LLM_BASE_URL=... -e LLM_MODEL=... -e LLM_API_KEY=... \
  ghcr.io/<user>/gridwise:1.0.0
```

Check nothing secret is baked in: `docker history --no-trunc <image> | grep -i key` and confirm `.env` is in `.dockerignore`.

If you build on an ARM machine (Apple Silicon), build for the judges' likely platform: `docker buildx build --platform linux/amd64 ...`.

## 5. Runtime configuration

| Variable | Required | Purpose |
|---|---|---|
| `LLM_BASE_URL` | yes* | OpenAI-compatible base URL of the primary provider |
| `LLM_MODEL` | yes* | Model ID |
| `LLM_API_KEY` | yes* | Secret |
| `BACKUP_LLM_BASE_URL` / `_MODEL` / `_API_KEY` | no | Second provider |
| `PORT` | no (8000) | Listen port inside the container |
| `LOG_LEVEL` | no (INFO) | |

\* The service starts and serves `/health` without them; `/optimize-energy` then uses the labelled fallback parser.

## 6. Operations checklist for the judging window

- [ ] `restart: unless-stopped` set; `docker ps` shows healthy.
- [ ] Provider dashboards: quota and spending cap are sufficient; both keys tested today.
- [ ] Disk not full (`df -h`); log rotation configured as above.
- [ ] No deploys to the other sites on that VPS during judging; no `docker system prune` that could remove the image.
- [ ] Uptime check every minute on `/health` (UptimeRobot or a cron `curl`) alerting your phone.
- [ ] `docker logs -f gridwise` shows only ids, latencies, types — no keys, no prompts.
- [ ] Don't rotate the keys or take the repo/image/video down until results are out.

## 7. Quick diagnostics

| Symptom | Look at |
|---|---|
| 502 from Nginx | container down or wrong port → `docker compose ps`, `docker logs` |
| 504 after ~60 s | missing `proxy_read_timeout`, or LLM timeout not applied |
| All interpretations say "fallback parser used" | key/base URL/model ID wrong, or provider 429 → test with a direct curl to the provider |
| Slow p95 | provider latency → swap primary/backup; confirm cache works; confirm single LLM call per request |
| 400 on valid samples | schema too strict (e.g. int vs float) → test all 10 inputs against `Scenario.model_validate` |

## 8. Backup plan if the VPS misbehaves

Any container host that accepts a public image works: point it at the published image, set the three env vars, expose port 8000. Because the image is already built and public, this is a 5-minute move. Avoid free tiers that sleep when idle (cold start can exceed the 60 s health window).
