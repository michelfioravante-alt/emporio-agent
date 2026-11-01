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

const history = [];
const reviewMode = new URLSearchParams(window.location.search).get("review") === "1";

function setPresence(state, text) {
  presenceLabel.classList.remove("is-busy", "is-offline");
  if (state === "busy") presenceLabel.classList.add("is-busy");
  if (state === "offline") presenceLabel.classList.add("is-offline");
  presenceLabel.textContent = text;
}

function addBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.textContent = text;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement("div");
  div.className = "bubble typing";
  div.id = "typing";
  div.innerHTML = "<span></span><span></span><span></span>";
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function hideTyping() {
  document.getElementById("typing")?.remove();
}

function renderActivity(items) {
  if (!items || items.length === 0) {
    activityEl.hidden = true;
    activityList.innerHTML = "";
    return;
  }
  activityEl.hidden = false;
  activityList.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${escapeHtml(item.label)}</strong> — ${escapeHtml(item.detail)}`;
    activityList.appendChild(li);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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
  const message = text.trim();
  if (!message) return;

  addBubble("user", message);
  history.push({ role: "user", content: message });
  inputEl.value = "";
  sendBtn.disabled = true;
  setPresence("busy", "Digitando…");
  showTyping();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: history.slice(0, -1),
        include_debug: reviewMode,
      }),
    });
    const data = await res.json();
    hideTyping();

    if (!res.ok) {
      const detail = data.detail || "Não foi possível concluir o atendimento agora.";
      addBubble("system", detail);
      history.pop();
      renderActivity([]);
      setPresence("offline", "Tente novamente");
      return;
    }

    addBubble("assistant", data.reply);
    history.push({ role: "assistant", content: data.reply });
    renderActivity(data.activity || []);

    if (reviewMode && data.debug) {
      reviewPanel.hidden = false;
      reviewDebug.textContent = JSON.stringify(data.debug, null, 2);
    }

    setPresence("online", "Online agora");
  } catch (_) {
    hideTyping();
    addBubble("system", "Não consegui conectar ao atendimento.");
    history.pop();
    setPresence("offline", "Sem conexão");
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(inputEl.value);
});

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(inputEl.value);
  }
});

document.querySelectorAll("#scenarios button").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.prompt));
});

clearBtn.addEventListener("click", () => {
  history.length = 0;
  chatEl.innerHTML = "";
  renderActivity([]);
  if (reviewMode) {
    reviewPanel.hidden = true;
    reviewDebug.textContent = "";
  }
  addBubble("system", "Nova conversa iniciada. Como podemos ajudar?");
  setPresence("online", "Online agora");
});

addBubble(
  "system",
  "Olá! Bem-vindo à Empório da Música. Posso ajudar com instrumentos, pedidos e políticas da loja."
);
if (reviewMode) {
  reviewPanel.hidden = false;
  reviewDebug.textContent = "Ative uma pergunta para ver o resumo técnico redigido.";
}
refreshHealth();
