<p align="center">
  <img src="dockwake/static/favicon.svg" width="96" height="96" alt="DockWake logo">
</p>

<h1 align="center">DockWake</h1>

<p align="center">
  Wake your self-hosted apps only when you open them.<br>
  Start a Docker container on demand, show its web UI, and stop it automatically
  when the last tab closes.
</p>

<p align="center">
  <a href="https://github.com/erikibolya/dockwake/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/erikibolya/dockwake/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/erikibolya/dockwake/blob/main/LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB.svg">
  <a href="https://github.com/erikibolya/dockwake/pkgs/container/dockwake"><img alt="GHCR" src="https://img.shields.io/badge/image-ghcr.io%2Ferikibolya%2Fdockwake-2496ED.svg"></a>
</p>

---

DockWake is a tiny web launcher for a **single Docker container with a web UI**.
Instead of keeping an app running 24/7 just in case you open it, DockWake shows
a **Start** button. It starts the container, waits until the UI is really
available, and embeds it in a full-screen iframe. When you close the tab, a
heartbeat stops and DockWake shuts the container down again.

It was built for [Frigate](https://frigate.video/) on a home server: with object
detection disabled, Frigate still decoded 34 camera streams 24/7 and used about
18 % of the host CPU while nobody was watching. DockWake brought idle usage to
practically zero. The same pattern works for any container behind Traefik:
Uptime Kuma, Immich, Paperless, a dashboard, an admin panel, ...

## Features

- **One button, one URL.** You open the app's normal host; DockWake decides
  whether to show Start or the running UI.
- **Waits for the real UI.** It checks both the internal readiness endpoint and
  the public UI URL (two consecutive `200`s) before setting the iframe. No
  Traefik 404 page from a half-started container.
- **Auto-stop on tab close.** A heartbeat keeps the app alive while a tab is
  open. Closing the last tab stops the container after a configurable grace
  period.
- **Multiple tabs.** The app stays up until the last DockWake tab is gone.
- **Crash fallback.** If the browser disappears without firing `pagehide`, a
  stale-session timeout stops the app.
- **Adopts running containers.** If the app is already running (a reboot, a
  manual start), DockWake adopts it and stops it later if nobody is watching.
- **Restricted Docker access.** The app never mounts the Docker socket. A
  dedicated socket proxy only allows start/stop.
- **Secure by default.** CSRF header, origin check, path whitelist, CSP,
  `X-Frame-Options: DENY`, read-only container, non-root user.
- **English and Czech UI** (`DOCKWAKE_LANGUAGE=en|cs`).
- **No runtime dependencies.** Python standard library only.

## How it works

```text
Browser ──> Traefik ──> DockWake (Start screen / iframe wrapper)
                  └──> Traefik ──> target app UI (iframe source)

DockWake ──> restricted socket proxy ──> docker start/stop target
        └──> target readiness URL (HTTP 2xx)
        └──> target public UI URL (HTTP 2xx/3xx/401)
```

1. You open the DockWake host in a browser.
2. If the target is stopped, DockWake shows **Start app**.
3. Start calls `docker start <container>` through the socket proxy.
4. DockWake polls the internal readiness URL and the public UI URL. Only after
   two consecutive UI `200`s does it mark the target ready.
5. The UI is loaded in a full-screen iframe.
6. Every open tab sends a heartbeat. The last tab sends `pagehide`/`leave`.
7. After `DOCKWAKE_IDLE_STOP_SECONDS` without heartbeats, DockWake calls
   `docker stop`.

The target UI must be reachable by the browser and must not block framing with
`X-Frame-Options: DENY` or `Content-Security-Policy: frame-ancestors`. Most
self-hosted apps work, but some need a setting. Uptime Kuma, for example, needs
`UPTIME_KUMA_DISABLE_FRAME_SAMEORIGIN=true` (the example sets it). If the app has
no such setting, remove the header in your reverse proxy or use a UI that allows
framing.

## Quick start

A complete Traefik example with Uptime Kuma is in
[`compose.example.yaml`](compose.example.yaml). Replace the example domains and
the basic-auth hash, then:

```bash
docker compose -f compose.example.yaml up -d
```

The important part is the DockWake service:

```yaml
dockwake:
  image: ghcr.io/erikibolya/dockwake:latest
  restart: unless-stopped
  read_only: true
  tmpfs: ["/tmp"]
  environment:
    DOCKWAKE_TARGET_CONTAINER: "uptime-kuma"
    DOCKWAKE_APP_NAME: "Uptime Kuma"
    DOCKWAKE_APP_URL: "https://status.example.com/"
    DOCKWAKE_TARGET_API_URL: "http://uptime-kuma:3001/"
    DOCKWAKE_TARGET_UI_URL: "https://status.example.com/"
    DOCKWAKE_LAUNCHER_ORIGIN: "https://wake.example.com"
  networks:
    - appnet
    - traefik_network
    - dockwake_internal
```

The target app gets a normal Traefik router on `status.example.com`; DockWake
gets the user-facing router on `wake.example.com`. DockWake's iframe loads the
first, so the address bar stays on `wake.example.com`.

The readiness URL is called **from inside the DockWake container**. The target
and DockWake must share a Docker network (`appnet` above). The target UI URL is
called from DockWake too, so it must be reachable from the container; use the
public URL or an internal URL that reaches the same reverse-proxy route.

### Frigate example

See [`examples/frigate/compose.yaml`](examples/frigate/compose.yaml). It keeps
the normal Frigate container and config, moves the Frigate UI to
`frigate-ui.example.com`, and puts DockWake on `wake.example.com`. Frigate is
stopped and started by DockWake; recordings and config are untouched.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DOCKWAKE_TARGET_CONTAINER` | required | Docker container name to start/stop. |
| `DOCKWAKE_APP_NAME` | `App` | Display name and browser title. |
| `DOCKWAKE_APP_URL` | required | Public URL loaded in the iframe. |
| `DOCKWAKE_APP_ICON_URL` | DockWake icon | Optional icon URL. |
| `DOCKWAKE_TARGET_API_URL` | required | Internal readiness URL (HTTP 2xx = ready). |
| `DOCKWAKE_TARGET_UI_URL` | `DOCKWAKE_APP_URL` | Public UI URL checked before showing the iframe. |
| `DOCKWAKE_LAUNCHER_ORIGIN` | empty | Public origin of DockWake, e.g. `https://wake.example.com`. Required for the `sendBeacon` leave path. |
| `DOCKWAKE_LANGUAGE` | `en` | `en` or `cs`. |
| `DOCKWAKE_PORT` | `8080` | Internal HTTP port. |
| `DOCKWAKE_IDLE_STOP_SECONDS` | `60` | Grace period after the last tab closes. |
| `DOCKWAKE_SESSION_STALE_SECONDS` | `180` | Fallback when `pagehide` never arrives. |
| `DOCKWAKE_START_TIMEOUT_SECONDS` | `120` | Maximum time to wait for readiness. |
| `DOCKWAKE_STOP_GRACE_SECONDS` | `30` | `docker stop -t` value; match the target's stop grace period. |
| `DOCKER_PROXY_URL` | required | URL of the restricted socket proxy, e.g. `http://dockwake-socket-proxy:2375`. |
| `DOCKWAKE_STATIC_DIR` | bundled `static/` | Override for development. |

## Security model

- **No Docker socket in the app.** DockWake talks HTTP to
  [`linuxserver/socket-proxy`](https://docs.linuxserver.io/images/docker-socket-proxy/),
  configured with `ALLOW_START=1`, `ALLOW_STOP=1`, `CONTAINERS=0`, `POST=0`.
  It cannot create, delete, exec into, or inspect containers.
- **The proxy is on an internal network** with DockWake only. It is never
  published.
- **Put the DockWake host behind authentication** (Traefik basic auth,
  Authelia, Authentik, ...). The example uses Traefik basic auth.
- **CSRF protection**: state-changing requests need `X-DockWake-Client: 1`;
  the leave beacon additionally checks `Origin`/`Sec-Fetch-Site`.
- **Hardening**: the app runs read-only as a non-root user, serves only
  whitelisted static files, and sends a restrictive CSP.
- The socket proxy technically allows start/stop of any container by name. The
  app hardcodes the configured target; a compromised DockWake can still
  start/stop other containers. Use a dedicated proxy instance.

## Reverse proxy notes

- Give the target app its **own host** (e.g. `status.example.com`) and give
  DockWake the user-facing host (e.g. `wake.example.com`). The target UI is
  iframed; the address bar stays on the DockWake host.
- The target's Traefik router must be active while the target runs. When the
  container is stopped, Traefik may remove the router (404). DockWake only
  loads the iframe after the target UI returns `200` twice.
- If the target sends `X-Frame-Options: DENY`, remove it for the target host or
  the iframe will be blocked.
- The target's own login page is shown inside the iframe. DockWake does not
  proxy credentials.

## Development

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"

# Unit and HTTP integration tests (stdlib, fast)
python -m unittest discover -s tests -v

# Browser end-to-end test (Playwright + Chromium)
python -m playwright install --with-deps chromium
python -m pytest e2e -q

# Lint
ruff check .
```

Local UI without Docker:

```bash
# terminal 1
python dev/fake_stack.py

# terminal 2
DOCKWAKE_PORT=8080 \
DOCKWAKE_TARGET_CONTAINER=app \
DOCKER_PROXY_URL=http://127.0.0.1:2375 \
DOCKWAKE_TARGET_API_URL=http://127.0.0.1:5001/api/ready \
DOCKWAKE_TARGET_UI_URL=http://127.0.0.1:5001/ \
DOCKWAKE_APP_URL=http://127.0.0.1:5001/ \
DOCKWAKE_APP_NAME="Test App" \
DOCKWAKE_LAUNCHER_ORIGIN=http://127.0.0.1:8080 \
DOCKWAKE_IDLE_STOP_SECONDS=5 \
DOCKWAKE_START_TIMEOUT_SECONDS=30 \
python -m dockwake.main
```

Open <http://127.0.0.1:8080/>. The fake app becomes ready after 3 s; closing
the tab stops it after 5 s.

See [`TESTING.md`](TESTING.md) for the feature/test inventory.

## CI/CD

- PRs run `test` (ruff + unit/integration), `e2e` (Playwright) and `build`
  (Docker build).
- Pushes to `dev` publish `ghcr.io/erikibolya/dockwake:dev`.
- Pushes to `main` publish `:latest`.
- Tags `v*` publish the version tag + `latest` and create a GitHub release.
- Workflows are in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

The first run creates the GHCR package. Make it public in the package settings
if you want anonymous pulls.

## Limitations

- One target container per DockWake instance. Run multiple instances for
  multiple apps.
- The target needs an HTTP readiness endpoint. A TCP-only app needs a small
  health endpoint or a different launcher.
- Auto-stop depends on the browser firing `pagehide` or on the stale-session
  fallback. If you open the target UI **directly** (not through DockWake), the
  heartbeat is absent and DockWake may stop it.
- The iframe keeps the DockWake host in the address bar. Deep links inside the
  target UI are not reflected in the top-level URL.
- If DockWake itself is down, the target stays running until it is stopped
  manually or DockWake restarts and adopts it.

## Related projects

- [Sablier](https://github.com/acouvreur/sablier) starts containers on demand
  through a reverse-proxy middleware and stops them after inactivity.
- [docker-snooze](https://github.com/decodeswapnil/docker-snooze) schedules and
  wakes Compose projects from a dashboard.
- [docknap](https://github.com/EkmalRey/docknap) is a lazy-loading reverse proxy
  for Docker containers.

DockWake is intentionally small and browser-tab-based: no scheduler, no extra
database, no Traefik plugin, just a start page, an iframe and a heartbeat.

## License

MIT © 2026 Erik Ibolya
