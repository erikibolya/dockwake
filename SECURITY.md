# Security

DockWake starts and stops a Docker container and is usually placed behind a
reverse proxy with authentication. Please treat it as an admin surface.

- Do not expose the DockWake host without authentication.
- Keep the socket proxy on an internal Docker network. It must never be
  published.
- The socket proxy is configured for start/stop only; do not enable `EXEC`,
  `CONTAINERS` or unrestricted `POST`.
- The app runs read-only as a non-root user and does not mount the Docker
  socket.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting:

<https://github.com/erikibolya/dockwake/security/advisories/new>

Do not open a public issue for a vulnerability. Include the affected version,
a minimal reproduction and the impact.
