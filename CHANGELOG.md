# Changelog

All notable changes to this project are documented here.

## 0.1.0 - 2026-11-06

- Initial public release.
- Start/stop one Docker container through a restricted socket proxy.
- Start page, readiness checks, public UI check before iframe load.
- Heartbeat, multi-tab support, `pagehide` leave and stale-session fallback.
- Adopt already-running targets and stop them after the idle timeout.
- English and Czech UI (`DOCKWAKE_LANGUAGE`).
- Traefik examples for Uptime Kuma and Frigate.
- Unit, HTTP integration and Playwright end-to-end tests.
- GitHub Actions CI, GHCR image publishing and tag-based releases.
