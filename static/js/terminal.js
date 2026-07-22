// Terminal view: embedded SSH-like console for the Raspberry Pi.
let terminalSocket = null;

function sanitizeOutput(text) {
  return text.replace(/\x1b\[[0-9;]*m/g, "");
}

export function initTerminal() {
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

  const output = document.getElementById("terminal-window");
  const input = document.getElementById("terminal-input");

  function append(text) {
    if (!output) return;
    const line = document.createElement("div");
    line.textContent = sanitizeOutput(text);
    output.appendChild(line);
    output.scrollTop = output.scrollHeight;
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/terminal`;
    terminalSocket = new WebSocket(url);

    terminalSocket.onopen = () => append("[Connected to terminal]");
    terminalSocket.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "terminal" && msg.output) {
          append(msg.output);
        }
      } catch (err) {
        append(`[Terminal parse error] ${err}`);
      }
    };
    terminalSocket.onclose = () => {
      append("[Terminal disconnected. Reconnecting in 2s...]");
      setTimeout(connect, 2000);
    };
    terminalSocket.onerror = () => terminalSocket.close();
  }

  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !terminalSocket || terminalSocket.readyState !== WebSocket.OPEN) return;
    const text = input.value + "\n";
    terminalSocket.send(text);
    input.value = "";
  });

  connect();
}
