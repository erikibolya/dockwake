(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const SESSION_KEY = "dockwake-session";
  const HEARTBEAT_MS = 10000;
  const POLL_FAST_MS = 1000;
  const POLL_SLOW_MS = 5000;

  const I18N = {
    en: {
      subtitle: "This app is stopped to save resources.",
      start: "Start app",
      retry: "Try again",
      force_stop: "Stop app",
      stop: "Stop",
      cancel: "Cancel and stop",
      hint_start: "Starting usually takes 15–20 seconds.",
      hint_stop: "Closing the tab stops the app after about {seconds} seconds.",
      loading_start: "Starting {app}…",
      loading_api: "Waiting for the app API.",
      loading_ui: "API is up, waiting for the web UI…",
      loading_stop: "Stopping {app}…",
      loading_stop_detail: "Waiting for the container to stop.",
      loading_hint: "Please keep this tab open.",
      error_start: "Could not start the app: ",
      error_stop: "Could not stop the app: ",
      unknown_error: "Something went wrong.",
      connection_failed: "Connection to DockWake failed. Try reloading the page.",
      ui_load_failed: "The app UI did not load. Try reloading the page.",
      launcher_unreachable: "DockWake is not responding. Try reloading the page.",
      confirm_stop: "Stop the app? Everyone using it will be disconnected.",
    },
    cs: {
      subtitle: "Aplikace je vypnutá, aby server zbytečně nezatěžovala.",
      start: "Spustit aplikaci",
      retry: "Zkusit znovu",
      force_stop: "Vypnout aplikaci",
      stop: "Vypnout",
      cancel: "Zrušit a vypnout",
      hint_start: "Start obvykle trvá 15–20 sekund.",
      hint_stop: "Po zavření tabu se aplikace vypne přibližně za {seconds} s.",
      loading_start: "Spouštím {app}…",
      loading_api: "Čekám na odpověď API aplikace.",
      loading_ui: "API běží, čekám na webové UI…",
      loading_stop: "Zastavuji {app}…",
      loading_stop_detail: "Čekám na ukončení kontejneru.",
      loading_hint: "Nech tab prosím otevřený.",
      error_start: "Nepodařilo se spustit aplikaci: ",
      error_stop: "Nepodařilo se vypnout aplikaci: ",
      unknown_error: "Nastala chyba.",
      connection_failed: "Spojení s DockWake selhalo. Zkus obnovit stránku.",
      ui_load_failed: "UI aplikace se nenačetlo. Zkus obnovit stránku.",
      launcher_unreachable: "DockWake neodpovídá. Zkus obnovit stránku.",
      confirm_stop: "Vypnout aplikaci? Všem se přeruší připojení.",
    },
  };

  const session = getSession();
  const frame = $("app-frame");

  let status = null;
  let statusReceivedAt = 0;
  let language = "en";
  let appName = "App";
  let pollTimer = null;
  let heartbeatTimer = null;
  let elapsedTimer = null;
  let toastTimer = null;
  let frameTimer = null;
  let viewerLoaded = false;
  let startBusy = false;
  let stopBusy = false;
  let heartbeatFailures = 0;
  let pollFailures = 0;

  // --------------------------------------------------------------- session

  function getSession() {
    let value = sessionStorage.getItem(SESSION_KEY);
    if (!value || !/^[A-Za-z0-9_-]{8,64}$/.test(value)) {
      value =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `s-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
      sessionStorage.setItem(SESSION_KEY, value);
    }
    return value;
  }

  // --------------------------------------------------------------------- i18n

  function t(key, vars = {}) {
    const table = I18N[language] || I18N.en;
    let text = table[key] || I18N.en[key] || key;
    for (const [name, value] of Object.entries(vars)) {
      text = text.split(`{${name}}`).join(String(value));
    }
    return text;
  }

  function applyI18n(nextLanguage) {
    language = I18N[nextLanguage] ? nextLanguage : "en";
    document.documentElement.lang = language;
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      const key = element.dataset.i18n;
      const text = t(key);
      if (text !== key) {
        element.textContent = text;
      }
    });
  }

  // ------------------------------------------------------------------- api

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-DockWake-Client": "1",
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      let detail = "";
      try {
        const data = await response.json();
        detail = data.error || "";
      } catch (_) {
        // ignore
      }
      throw new Error(detail || `HTTP ${response.status}`);
    }
    if (response.status === 204) {
      return null;
    }
    return response.json();
  }

  async function getStatus() {
    const response = await fetch("/api/status", {
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return response.json();
  }

  // --------------------------------------------------------------- screens

  function setScreen(name) {
    $("start-screen").hidden = name !== "start";
    $("loading-screen").hidden = name !== "loading";
    $("viewer").hidden = name !== "viewer";
    document.body.classList.toggle("in-viewer", name === "viewer");
  }

  function showError(message) {
    const element = $("start-error");
    element.textContent = message;
    element.hidden = false;
  }

  function hideError() {
    const element = $("start-error");
    element.hidden = true;
    element.textContent = "";
  }

  function showToast(message) {
    const element = $("toast");
    element.textContent = message;
    element.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      element.hidden = true;
    }, 6000);
  }

  function setLoadingText(title, detail) {
    $("loading-title").textContent = title;
    $("loading-detail").textContent = detail;
  }

  // -------------------------------------------------------------- heartbeat

  function startHeartbeat() {
    if (heartbeatTimer) {
      return;
    }
    sendHeartbeat();
    heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_MS);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  async function sendHeartbeat() {
    try {
      const next = await api("/api/heartbeat", {
        method: "POST",
        body: JSON.stringify({ session }),
      });
      heartbeatFailures = 0;
      if (next) {
        status = next;
        statusReceivedAt = Date.now();
      }
    } catch (error) {
      heartbeatFailures += 1;
      if (heartbeatFailures === 3) {
        showToast(t("connection_failed"));
      }
    }
  }

  function sendLeave() {
    const payload = JSON.stringify({ session });
    try {
      fetch("/api/leave", {
        method: "POST",
        keepalive: true,
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-DockWake-Client": "1",
        },
        body: payload,
      }).catch(() => {});
    } catch (_) {
      // ignore
    }
    try {
      const blob = new Blob([payload], { type: "application/json" });
      navigator.sendBeacon("/api/leave", blob);
    } catch (_) {
      // ignore
    }
  }

  // ---------------------------------------------------------------- elapsed

  function startElapsed() {
    if (elapsedTimer) {
      return;
    }
    updateElapsed();
    elapsedTimer = setInterval(updateElapsed, 1000);
  }

  function stopElapsed() {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    $("loading-elapsed").textContent = "";
  }

  function updateElapsed() {
    if (!status || status.elapsed == null) {
      $("loading-elapsed").textContent = "";
      return;
    }
    const seconds = Math.max(
      0,
      Math.floor(status.elapsed + (Date.now() - statusReceivedAt) / 1000)
    );
    $("loading-elapsed").textContent = `(${seconds} s)`;
  }

  // ----------------------------------------------------------------- viewer

  function ensureFrame(url) {
    if (!url) {
      return;
    }
    if (frame.dataset.url === url) {
      return;
    }
    frame.dataset.url = url;
    viewerLoaded = false;
    clearTimeout(frameTimer);
    frameTimer = setTimeout(() => {
      if (!viewerLoaded) {
        showToast(t("ui_load_failed"));
      }
    }, 20000);
    const separator = url.includes("?") ? "&" : "?";
    frame.src = `${url}${separator}_=${Date.now()}`;
  }

  // ----------------------------------------------------------------- update

  function update(next) {
    const previous = status;
    status = next;
    statusReceivedAt = Date.now();

    if (next.language && next.language !== language) {
      applyI18n(next.language);
    }
    appName = next.app_name || appName;
    $("app-name").textContent = appName;
    $("app-icon").src = next.app_icon_url || "/static/favicon.svg";
    $("app-frame").title = appName;
    $("viewer").setAttribute("aria-label", appName);
    $("stop-button").title = t("stop");
    document.title = appName;
    $("idle-hint").textContent = t("hint_stop", {
      seconds: next.idle_stop_seconds,
    });

    if (previous && previous.state === "ready" && next.state !== "ready") {
      frame.dataset.url = "";
    }

    if (next.state === "ready") {
      setScreen("viewer");
      hideError();
      $("force-stop-button").hidden = true;
      stopElapsed();
      ensureFrame(next.app_url || frame.dataset.url);
      startHeartbeat();
      return;
    }

    stopHeartbeat();
    stopElapsed();

    if (next.state === "starting") {
      setScreen("loading");
      setLoadingText(
        t("loading_start", { app: appName }),
        next.ui_ready ? t("loading_ui") : t("loading_api")
      );
      $("cancel-button").hidden = false;
      $("force-stop-button").hidden = true;
      hideError();
      startHeartbeat();
      startElapsed();
      return;
    }

    if (next.state === "stopping") {
      setScreen("loading");
      setLoadingText(
        t("loading_stop", { app: appName }),
        t("loading_stop_detail")
      );
      $("cancel-button").hidden = true;
      $("force-stop-button").hidden = true;
      hideError();
      return;
    }

    // stopped or error
    setScreen("start");
    const isError = next.state === "error";
    $("start-label").textContent = isError ? t("retry") : t("start");
    $("force-stop-button").hidden = !(isError && next.managed);
    if (isError) {
      showError(next.error || t("unknown_error"));
    } else {
      hideError();
    }
  }

  // ------------------------------------------------------------------- poll

  async function poll() {
    clearTimeout(pollTimer);
    try {
      const next = await getStatus();
      pollFailures = 0;
      update(next);
    } catch (error) {
      pollFailures += 1;
      if (pollFailures === 3) {
        showToast(t("launcher_unreachable"));
      }
    } finally {
      const fast =
        status &&
        (status.state === "starting" || status.state === "stopping");
      pollTimer = setTimeout(poll, fast ? POLL_FAST_MS : POLL_SLOW_MS);
    }
  }

  // ---------------------------------------------------------------- actions

  async function onStart() {
    if (startBusy) {
      return;
    }
    startBusy = true;
    const button = $("start-button");
    button.disabled = true;
    hideError();
    try {
      const next = await api("/api/start", {
        method: "POST",
        body: JSON.stringify({ session }),
      });
      update(next);
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, 250);
    } catch (error) {
      showError(t("error_start") + error.message);
    } finally {
      startBusy = false;
      button.disabled = false;
    }
  }

  async function onStop(confirmFirst) {
    if (stopBusy) {
      return;
    }
    if (confirmFirst && !window.confirm(t("confirm_stop"))) {
      return;
    }
    stopBusy = true;
    stopHeartbeat();
    try {
      const next = await api("/api/stop", {
        method: "POST",
        body: JSON.stringify({ session }),
      });
      update(next);
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, 250);
    } catch (error) {
      showToast(t("error_stop") + error.message);
    } finally {
      stopBusy = false;
    }
  }

  // ----------------------------------------------------------------- events

  $("start-button").addEventListener("click", onStart);
  $("force-stop-button").addEventListener("click", () => onStop(true));
  $("stop-button").addEventListener("click", () => onStop(true));
  $("cancel-button").addEventListener("click", () => onStop(false));

  frame.addEventListener("load", () => {
    viewerLoaded = true;
    clearTimeout(frameTimer);
  });

  window.addEventListener("pagehide", () => {
    if (status && status.managed) {
      sendLeave();
    }
  });

  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      poll();
      if (status && (status.state === "starting" || status.state === "ready")) {
        startHeartbeat();
      }
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      poll();
      if (status && (status.state === "starting" || status.state === "ready")) {
        sendHeartbeat();
      }
    }
  });

  applyI18n("en");
  poll();
})();
