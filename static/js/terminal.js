// Terminal view: embedded SSH-like console for the Raspberry Pi.
let terminalSocket = null;
let terminalAuthenticated = false;
const TERMINAL_TOKEN_COOKIE = "terminal_token";
const TERMINAL_TOKEN_TTL = 3600;
const terminalHistory = [];
let terminalHistoryIndex = -1;
let terminalDraft = "";

function sanitizeOutput(text) {
  return text.replace(/\x1b\[[0-9;]*m/g, "");
}

function setTerminalCookie(token) {
  document.cookie = `${TERMINAL_TOKEN_COOKIE}=${token};path=/;max-age=${TERMINAL_TOKEN_TTL};samesite=lax`;
}

function clearTerminalCookie() {
  document.cookie = `${TERMINAL_TOKEN_COOKIE}=;path=/;max-age=0;samesite=lax`;
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || res.statusText || res.status);
  }
  return res.json();
}

function addTerminalLine(output, className) {
  const terminalWindow = document.getElementById("terminal-window");
  if (!terminalWindow) return;
  const line = document.createElement("div");
  if (className) line.className = className;
  line.textContent = sanitizeOutput(output);
  terminalWindow.appendChild(line);
  terminalWindow.scrollTop = terminalWindow.scrollHeight;
}

function createCommandHistoryHandlers(input) {
  input.addEventListener("keydown", async (event) => {
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      if (terminalHistory.length === 0) return;
      if (terminalHistoryIndex === -1) {
        terminalDraft = input.value;
      }
      if (event.key === "ArrowUp") {
        terminalHistoryIndex = Math.max(0, terminalHistoryIndex === -1 ? terminalHistory.length - 1 : terminalHistoryIndex - 1);
      } else {
        terminalHistoryIndex = Math.min(terminalHistory.length - 1, terminalHistoryIndex + 1);
      }
      if (terminalHistoryIndex === -1) {
        input.value = terminalDraft;
      } else {
        input.value = terminalHistory[terminalHistoryIndex];
      }
      return;
    }

    if (event.key === "Tab") {
      event.preventDefault();
      const value = input.value;
      const lastTokenMatch = value.match(/(?:^|\s)([^\s]*)$/);
      const prefix = lastTokenMatch ? lastTokenMatch[1] : "";
      if (!prefix) return;

      try {
        const data = await fetchJson("/api/terminal/complete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prefix }),
        });
        const hints = Array.isArray(data.hints) ? data.hints : [];
        if (hints.length === 0) return;

        if (hints.length === 1) {
          const replacement = hints[0];
          input.value = value.replace(/(?:^|\s)([^\s]*)$/, `${value.endsWith(" ") ? "" : ""}${replacement}`);
        } else {
          const common = hints.reduce((commonPrefix, item) => {
            let i = 0;
            while (i < commonPrefix.length && item[i] === commonPrefix[i]) i += 1;
            return commonPrefix.slice(0, i);
          }, hints[0]);

          if (common.length > prefix.length) {
            input.value = value.replace(/(?:^|\s)([^\s]*)$/, `${value.endsWith(" ") ? "" : ""}${common}`);
          }
          addTerminalLine(`Suggestions: ${hints.join(", ")}`, "terminal-hints");
        }
      } catch (err) {
        addTerminalLine(`[Completion error] ${err.message}`, "terminal-error");
      }
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      const command = input.value.trim();
      if (!command || !terminalSocket || terminalSocket.readyState !== WebSocket.OPEN) return;
      terminalHistory.push(command);
      terminalHistoryIndex = -1;
      terminalDraft = "";
      terminalSocket.send(command + "\n");
      input.value = "";
    }
  });
}

async function createTerminalView() {
  const view = document.getElementById("view-terminal");
  if (!view) return;

  view.innerHTML = `
    <h1 class="page-title">Terminal</h1>
    <p class="page-sub">Execute shell commands on the Raspberry Pi from the dashboard.</p>
    <div class="terminal-view">
      <div class="terminal-window" id="terminal-window" aria-live="polite"></div>
      <div class="terminal-input-row">
        <span class="terminal-prompt">$</span>
        <input id="terminal-input" class="terminal-input" type="text" autocomplete="off" spellcheck="false" placeholder="Type a command and press Enter" />
      </div>
    </div>
  `;

  const input = document.getElementById("terminal-input");
  if (!input) return;
  createCommandHistoryHandlers(input);
  return { input };
}

async function connectTerminal() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws/terminal`;
  terminalSocket = new WebSocket(url);

  terminalSocket.onopen = () => addTerminalLine("[Connected to terminal]");
  terminalSocket.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type === "terminal" && msg.output) {
        addTerminalLine(msg.output);
      }
    } catch (err) {
      addTerminalLine(`[Terminal parse error] ${err}`, "terminal-error");
    }
  };
  terminalSocket.onclose = () => {
    addTerminalLine("[Terminal disconnected. Reconnecting in 2s...]", "terminal-error");
    setTimeout(() => {
      if (terminalAuthenticated) connectTerminal();
    }, 2000);
  };
  terminalSocket.onerror = () => terminalSocket.close();
}

async function renderLoginView() {
  const view = document.getElementById("view-terminal");
  if (!view) return;

  view.innerHTML = `
    <h1 class="page-title">Terminal Login</h1>
    <p class="page-sub">Please sign in before accessing the Raspberry Pi console.</p>
    <form class="card terminal-login" id="terminal-login-form">
      <label>
        Password
        <input id="terminal-password" type="password" autocomplete="current-password" />
      </label>
      <div class="save-row">
        <button type="submit" class="btn-save">Sign in</button>
        <span class="save-note" id="terminal-login-note"></span>
      </div>
    </form>
  `;

  const form = document.getElementById("terminal-login-form");
  const note = document.getElementById("terminal-login-note");
  const passwordInput = document.getElementById("terminal-password");
  if (!form || !passwordInput || !note) return;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    note.textContent = "";
    try {
      const result = await fetchJson("/api/terminal/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: passwordInput.value }),
      });
      setTerminalCookie(result.token);
      terminalAuthenticated = true;
      await initTerminal();
    } catch (err) {
      note.textContent = "Invalid password";
      note.className = "save-note show terminal-error";
      setTimeout(() => (note.className = "save-note"), 2000);
    }
  });
}

async function checkTerminalAuth() {
  try {
    const res = await fetchJson("/api/terminal/status");
    return Boolean(res.authorized);
  } catch (_err) {
    return false;
  }
}

export async function initTerminal() {
  const view = document.getElementById("view-terminal");
  if (!view) return;

  terminalAuthenticated = await checkTerminalAuth();

  if (!terminalAuthenticated) {
    await renderLoginView();
    return;
  }

  await createTerminalView();
  await connectTerminal();
}
