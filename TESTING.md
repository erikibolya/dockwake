# Test inventory

| Feature / user path | Automated test | Layer |
|---|---|---|
| Start button starts the target | `e2e/test_ui.py`, `tests/test_launcher.py::test_start_flow` | browser + state machine |
| Readiness polling | `test_start_flow` | unit |
| Internal API must be up | `test_start_timeout` | unit |
| Public UI must be up before iframe | `test_start_waits_for_web_ui`, `test_start_timeout_when_web_ui_never_ready` | unit + e2e |
| Real HTTP start → ready → stop | `tests/test_integration.py::test_start_ready_and_auto_stop` | integration |
| Auto-stop after last tab closes | `test_auto_stop_after_last_tab_leaves`, `e2e/test_ui.py` | unit + browser |
| Multiple tabs keep it alive | `test_second_tab_keeps_container_alive` | unit |
| Crash fallback | `test_crashed_tab_is_stopped_after_stale_timeout` | unit |
| Adopt already-running target | `test_probe_adopts_already_running_app` | unit |
| Target API disappears | `test_running_app_returns_to_stopped_when_api_disappears` | unit |
| Docker proxy failure | `test_docker_start_failure` | unit |
| Manual stop | `test_stop_calls_docker`, integration | unit + integration |
| Invalid session rejected | `test_invalid_session_is_ignored`, `test_invalid_session_is_rejected` | unit + HTTP |
| CSRF protection | `test_start_requires_client_header`, `test_leave_from_foreign_origin_is_rejected` | HTTP |
| Security headers / CSP | `test_index_is_served_with_security_headers` | HTTP |
| Path traversal rejected | `test_path_traversal_is_rejected`, `test_static_whitelist_rejects_other_paths` | HTTP |
| HEAD requests | `test_head_index` | HTTP |
| Stop button top-right | `e2e/test_ui.py` (bounding box) | browser |
| Czech backend messages | `test_czech_messages` | unit |
| Iframe content | `e2e/test_ui.py` (`Fake app` text inside frame) | browser |

Run:

```bash
python -m unittest discover -s tests -v   # unit + HTTP integration
python -m pytest e2e -q                   # Playwright browser test
ruff check .
```

Manual smoke test of a deployed instance:

1. Open the DockWake host → Start screen.
2. Start → spinner → target UI in the iframe, with no Traefik 404.
3. Confirm the Stop button is top-right.
4. Close the tab → target container stops after the configured grace period.
5. Open two tabs → close one → target stays running; close the second → stops.
6. Start the target manually (`docker start <target>`) without DockWake open →
   DockWake adopts it and stops it after the idle timeout.
