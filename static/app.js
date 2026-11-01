const chatEl = document.getElementById("chat");
const formEl = document.getElementById("composer");
const inputEl = document.getElementById("message");
const sendBtn = document.getElementById("sendBtn");
const clearBtn = document.getElementById("clearBtn");
const presenceLabel = document.getElementById("presenceLabel");
const activityEl = document.getElementById("activity");
const activityList = document.getElementById("activityList");
const reviewPanel = document.getElementById("reviewPanel");
const reviewDebug = document.getElementById("reviewDebug");
const scenarioButtons = document.querySelectorAll("#scenarios button");

const MAX_HISTORY_SENT = 12;
const REQUEST_TIMEOUT_MS = 45000;
// Instant, all-at-once replies feel robotic: show "digitando…" briefly, then reveal the
// reply word by word. Only the UI paces itself; the API answers as fast as it can.
const MIN_TYPING_MS = 700;
const WORD_DELAY_MS = 35;
const MAX_REVEAL_MS = 4000;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
// sessionStorage: the conversation survives a reload but is gone when the tab closes.
const STORAGE_KEY = "emporio-chat-v1";
const WELCOME =
  "Olá! Bem-vindo à Empório da Música. Posso ajudar com instrumentos, preços, pedidos, trocas e horários da loja.";

const reviewMode = new URLSearchParams(window.location.search).get("review") === "1";
let history = [];
let busy = false;

function setPresence(state, text) {
  presenceLabel.classList.remove("is-busy", "is-offline");
  if (state === "busy") presenceLabel.classList.add("is-busy");
  if (state === "offline") presenceLabel.classList.add("is-offline");
  presenceLabel.textContent = text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// Only **bold** is rendered; everything else stays escaped text.
function renderRichText(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function addBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  if (role === "assistant") {
    div.innerHTML = renderRichText(text);
  } else {
    div.textContent = text;
  }
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement("div");
  div.className = "bubble typing";
  div.id = "typing";
  div.setAttribute("aria-label", "Digitando");
  div.innerHTML = "<span></span><span></span><span></span>";
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function hideTyping() {
  document.getElementById("typing")?.remove();
}

function renderActivity(items) {
  activityList.innerHTML = "";
  activityEl.hidden = !items || items.length === 0;
  (items || []).forEach((item) => {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${escapeHtml(item.label)}</strong> — ${escapeHtml(item.detail)}`;
    activityList.appendChild(li);
  });
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  inputEl.readOnly = value;
  scenarioButtons.forEach((button) => {
    button.disabled = value;
  });
}

function saveConversation() {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  } catch (_) {
    // Storage full or blocked: the chat keeps working without persistence.
  }
}

function loadConversation() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
    if (!Array.isArray(saved)) return [];
    return saved.filter(
      (item) =>
        item && (item.role === "user" || item.role === "assistant") && typeof item.content === "string"
    );
  } catch (_) {
    return [];
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function revealReply(text) {
  if (reducedMotion) {
    addBubble("assistant", text);
    return;
  }
  const bubble = addBubble("assistant", "");
  chatEl.setAttribute("aria-busy", "true");
  // Split keeping whitespace so line breaks and list layout are preserved.
  const tokens = text.split(/(\s+)/);
  const words = tokens.filter((token) => token.trim()).length || 1;
  const delay = Math.min(WORD_DELAY_MS, MAX_REVEAL_MS / words);
  let shown = "";
  for (const token of tokens) {
    shown += token;
    if (!token.trim()) continue;
    // Close an unfinished **bold** so the partial text never shows raw asterisks.
    const unbalanced = (shown.match(/\*\*/g) || []).length % 2 === 1;
    bubble.innerHTML = renderRichText(unbalanced ? `${shown}**` : shown);
    chatEl.scrollTop = chatEl.scrollHeight;
    await sleep(delay);
  }
  bubble.innerHTML = renderRichText(text);
  chatEl.removeAttribute("aria-busy");
}

function errorMessage(data) {
  if (data && typeof data.detail === "string" && data.detail) return data.detail;
  return "Não foi possível concluir o atendimento agora. Tente novamente.";
}

async function refreshHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.status === "ok") {
      setPresence("online", "Online agora");
    } else {
      setPresence("offline", "Atendimento em manutenção");
    }
  } catch (_) {
    setPresence("offline", "Sem conexão");
  }
}

async function sendMessage(text) {
  const message = (text || "").trim();
  if (!message || busy) return;

  setBusy(true);
  addBubble("user", message);
  inputEl.value = "";
  setPresence("busy", "Digitando…");
  showTyping();

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const startedAt = performance.now();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: history.slice(-MAX_HISTORY_SENT),
        include_debug: reviewMode,
      }),
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      await sleep(Math.max(0, MIN_TYPING_MS - (performance.now() - startedAt)));
    }
    hideTyping();

    if (!res.ok) {
      addBubble("error", errorMessage(data));
      renderActivity([]);
      setPresence("offline", "Tente novamente");
      return;
    }

    history.push({ role: "user", content: message }, { role: "assistant", content: data.reply });
    saveConversation();
    await revealReply(data.reply);
    renderActivity(data.activity || []);

    if (reviewMode && data.debug) {
      reviewPanel.hidden = false;
      reviewDebug.textContent = JSON.stringify(data.debug, null, 2);
    }

    setPresence("online", "Online agora");
  } catch (error) {
    hideTyping();
    const timedOut = error && error.name === "AbortError";
    addBubble(
      "error",
      timedOut
        ? "A resposta demorou mais que o esperado. Tente de novo em instantes."
        : "Não consegui conectar ao atendimento. Verifique sua internet e tente de novo."
    );
    setPresence("offline", timedOut ? "Tente novamente" : "Sem conexão");
  } finally {
    clearTimeout(timeout);
    setBusy(false);
    inputEl.focus();
  }
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(inputEl.value);
});

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage(inputEl.value);
  }
});

scenarioButtons.forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.prompt));
});

clearBtn.addEventListener("click", () => {
  if (busy) return;
  history = [];
  sessionStorage.removeItem(STORAGE_KEY);
  chatEl.innerHTML = "";
  renderActivity([]);
  if (reviewMode) {
    reviewPanel.hidden = true;
    reviewDebug.textContent = "";
  }
  addBubble("system", "Nova conversa iniciada. Como podemos ajudar?");
  setPresence("online", "Online agora");
  inputEl.focus();
});

addBubble("system", WELCOME);
history = loadConversation();
history.forEach((item) => addBubble(item.role, item.content));

if (reviewMode) {
  reviewPanel.hidden = false;
  reviewDebug.textContent = "Envie uma pergunta para ver o resumo técnico (dados pessoais ocultados).";
}
refreshHealth();
