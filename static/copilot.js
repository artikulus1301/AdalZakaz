// Adal Copilot — чат с ИИ-помощником продавца.
// Ответы строятся по данным ядра (RAG). Поручение превращается в план (to-do), который выполняется только после одобрения.
const CP_KEY = "adal_copilot_chat";
let cpChat = null, cpBusy = false;
try { cpChat = localStorage.getItem(CP_KEY); } catch {}

const CP_SUGGEST = ["Что сегодня важно?", "Какие заказы ждут оплаты?", "Какие претензии ждут решения?", "Что заканчивается на складе?"];
const CP_STEP = { ready: "к выполнению", blocked: "не пройдёт проверку — выполнен не будет", done: "выполнено", failed: "ошибка", skipped: "пропущен" };
const CP_PLAN_CHIP = { pending: "warn", running: "ai", done: "ok", partial: "warn", failed: "bad", rejected: "plain", expired: "plain", blocked: "bad" };

Object.assign(EVENT_LABELS, {
  copilot_plan_approved: "Продавец одобрил план Adal Copilot",
  copilot_step_done: "Шаг плана Adal Copilot выполнен",
});

function cpSaveChat(id) {
  cpChat = id;
  try { localStorage.setItem(CP_KEY, id); } catch {}
}

function cpNewChatId() {
  const b = new Uint8Array(8); crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

function cpPlan(p) {
  const until = new Date(p.expires_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return `<div class="cp-plan" data-plan="${p.id}">
    <div class="cp-plan-head"><b>План действий</b><span class="chip ${CP_PLAN_CHIP[p.status] || ""}">${esc(p.status_label)}</span></div>
    <ol class="cp-todo">${p.steps.map((s) => {
      const state = s.result ? s.result.status : s.status;
      const note = state === "skipped" ? s.result.message : [CP_STEP[state], s.result ? s.result.message : s.note].filter(Boolean).join(" · ");
      return `<li class="cp-step ${state}"><span class="cp-mark" aria-hidden="true"></span><div>
        <div class="cp-step-t"><span class="sr-only">Шаг ${s.n}: </span>${cpRich(s.title)}</div>
        <small>${esc(note)}</small></div></li>`;
    }).join("")}</ol>
    ${p.status === "pending" ? `<div class="row cp-plan-act">
      <button class="btn primary small cp-approve" type="button">${icon("check", 16)} Одобрить и выполнить${p.ready < p.steps.length ? ` (${p.ready} из ${p.steps.length})` : ""}</button>
      <button class="btn small ghost cp-reject" type="button">Отклонить</button>
      <small class="muted">План действует до ${until}</small></div>` : ""}
  </div>`;
}

function cpSources(list) {
  if (!list?.length) return "";
  const open = { order: 1, claim: 1, product: 1, metrics: 1 };
  return `<div class="cp-sources" aria-label="Источники">${list.map((s) => open[s.kind]
    ? `<button type="button" class="chip cp-src" data-kind="${esc(s.kind)}" data-id="${esc(s.id ?? "")}">${esc(s.label)}</button>`
    : `<span class="chip">${esc(s.label)}</span>`).join("")}</div>`;
}

// Оформление ответа: сначала экранирование (HTML от модели не проходит), затем списки, **жирный**
// и ссылки на заказы и претензии — номер AZ-000001 / CL-000001 совпадает с их id
function cpRich(text) {
  return esc(text)
    .replace(/^(\s*)[-*]\s+/gm, "$1• ")
    .replace(/^#{1,4}\s*(.+)$/gm, "<b>$1</b>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\b(AZ|CL)-(\d{6})\b/g, (m, kind, n) =>
      `<button type="button" class="cp-ref" data-kind="${kind === "AZ" ? "order" : "claim"}" data-id="${Number(n)}">${m}</button>`);
}

// Источники-чипы без дублей: номер, который уже кликабелен в тексте или в плане, второй раз не показываем
function cpExtraSources(m) {
  const shown = m.text + " " + (m.plan?.steps || []).map((s) => s.title).join(" ");
  return (m.meta?.sources || []).filter((s) => !(["order", "claim"].includes(s.kind) && shown.includes(s.label)));
}

function cpMessage(m) {
  if (m.role === "user") return `<div class="cp-msg user" data-id="${m.id}"><div class="cp-bubble">${esc(m.text)}</div></div>`;
  return `<div class="cp-msg bot" data-id="${m.id}"><div class="cp-avatar" aria-hidden="true">${icon("ai", 16)}</div><div class="cp-body">
    <div class="cp-bubble">${cpRich(m.text)}</div>${m.plan ? cpPlan(m.plan) : ""}${cpSources(cpExtraSources(m))}</div></div>`;
}

const CP_EMPTY = `<div class="cp-welcome"><div class="cp-avatar big" aria-hidden="true">${icon("ai", 24)}</div>
  <h2>Чем могу помочь?</h2>
  <div class="cp-suggest">${CP_SUGGEST.map((s) => `<button type="button" class="btn small">${esc(s)}</button>`).join("")}</div></div>`;

function cpHistory(list) {
  if (!list.length) return `<p class="cp-hist-empty">Здесь появятся ваши диалоги</p>`;
  return `<ul class="cp-hist-list">${list.map((ch) => `<li><button type="button" class="cp-hist-item" data-chat="${esc(ch.chat_id)}"
    ${ch.chat_id === cpChat ? 'aria-current="true"' : ""}><span class="cp-hist-t">${esc(ch.title)}</span><small>${dt(ch.updated_at)}</small></button></li>`).join("")}</ul>`;
}

function cpOpenChat(id) {
  if ($(".drawer")) closeDrawer();
  cpSaveChat(id);
  go("copilot", true);
}

// Список диалогов: колонка слева на компьютере и выезжающая панель на телефоне
function cpBindHistory(root) {
  root.addEventListener("click", (e) => {
    if (e.target.closest(".cp-new")) return cpOpenChat(cpNewChatId());
    const item = e.target.closest(".cp-hist-item");
    if (item) cpOpenChat(item.dataset.chat);
  });
}

async function viewCopilot() {
  if (!cpChat) cpSaveChat(cpNewChatId());
  const [data, history] = await Promise.all([api("/api/copilot/chat?chat_id=" + encodeURIComponent(cpChat)), api("/api/copilot/chats")]);
  cpSaveChat(data.chat_id);
  const newBtn = `<button class="btn small block cp-new" type="button">${icon("plus", 16)} Новый диалог</button>`;
  $("#view").innerHTML = `<div class="cp-view">
    <div class="topbar"><div><h1>Adal Copilot</h1></div>
      <div class="row">${data.ai.available ? "" : `<span class="chip warn">ИИ не подключён — упрощённые ответы</span>`}
        <button class="btn small cp-hist-open" type="button">${icon("clock", 16)} Диалоги</button></div></div>
    <div class="cp-wrap">
      <aside class="cp-hist" aria-label="Диалоги">${newBtn}<div class="cp-hist-box">${cpHistory(history)}</div></aside>
      <section class="cp" aria-label="Чат с Adal Copilot">
        <div class="cp-log" id="cp-log" role="log" aria-live="polite" aria-relevant="additions" tabindex="0">${data.messages.length ? data.messages.map(cpMessage).join("") : CP_EMPTY}</div>
        <form class="cp-form" id="cp-form">
          <label class="sr-only" for="cp-text">Сообщение для Adal Copilot</label>
          <textarea id="cp-text" rows="1" maxlength="2000" placeholder="Напишите сообщение…"></textarea>
          <button class="btn primary" id="cp-send" type="submit">${icon("arrowRight", 18)}<span class="cp-send-l">Отправить</span></button>
        </form>
      </section>
    </div></div>`;
  const log = $("#cp-log"), text = $("#cp-text");
  const scroll = () => { log.scrollTop = log.scrollHeight; };
  const grow = () => { text.style.height = "auto"; text.style.height = Math.min(text.scrollHeight, 160) + "px"; };
  const refreshHistory = async () => {
    const list = await api("/api/copilot/chats").catch(() => null);
    if (list && document.body.contains(log)) $(".cp-hist-box").innerHTML = cpHistory(list);
  };
  scroll();
  cpBindHistory($(".cp-hist"));
  $(".cp-hist-open").onclick = async () => {
    const list = await api("/api/copilot/chats");
    cpBindHistory(openDrawer(`<h1>Диалоги</h1>${newBtn}<div class="cp-hist-box">${cpHistory(list)}</div>`));
  };

  async function send(msg) {
    msg = msg.trim();
    if (!msg || cpBusy) return;
    cpBusy = true; $("#cp-send").disabled = true;
    const first = !!$(".cp-welcome", log);
    $(".cp-welcome", log)?.remove();
    log.insertAdjacentHTML("beforeend", `<div class="cp-msg user pending"><div class="cp-bubble">${esc(msg)}</div></div>
      <div class="cp-msg bot cp-wait"><div class="cp-avatar" aria-hidden="true">${icon("ai", 16)}</div><div class="cp-body"><div class="cp-bubble cp-typing"><span class="spin"></span> Думаю…</div></div></div>`);
    text.value = ""; grow(); scroll();
    try {
      const r = await api("/api/copilot/chat", { method: "POST", json: { text: msg, chat_id: cpChat } });
      cpSaveChat(r.chat_id);
      if (!document.body.contains(log)) return;
      $$(".cp-msg.pending, .cp-wait", log).forEach((el) => el.remove());
      log.insertAdjacentHTML("beforeend", r.messages.map(cpMessage).join(""));
      refreshHistory();
    } catch (e) {
      if (!document.body.contains(log)) return;
      $$(".cp-msg.pending, .cp-wait", log).forEach((el) => el.remove());
      if (first && !$(".cp-msg", log)) log.innerHTML = CP_EMPTY;
      text.value = msg; grow();
      toast(e.message, true);
    } finally {
      cpBusy = false;
      if (document.body.contains(log)) { $("#cp-send").disabled = false; scroll(); text.focus(); }
    }
  }

  $("#cp-form").onsubmit = (e) => { e.preventDefault(); send(text.value); };
  text.addEventListener("input", grow);
  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(text.value); }
  });

  log.addEventListener("click", async (e) => {
    const suggestion = e.target.closest(".cp-suggest button");
    if (suggestion) return send(suggestion.textContent);
    const src = e.target.closest(".cp-src, .cp-ref");
    if (src) {
      const id = Number(src.dataset.id);
      ({ order: () => openOrder(id), claim: () => openClaim(id), product: () => openProduct(id), metrics: () => go("analytics", true) })[src.dataset.kind]?.();
      return;
    }
    const btn = e.target.closest(".cp-approve, .cp-reject");
    if (!btn) return;
    const card = btn.closest(".cp-plan"), approveIt = btn.classList.contains("cp-approve");
    await act(btn, async () => {
      const r = await api(`/api/copilot/plans/${card.dataset.plan}/${approveIt ? "approve" : "reject"}`, { method: "POST" });
      card.outerHTML = cpPlan(r.plan);
      if (r.message) log.insertAdjacentHTML("beforeend", cpMessage(r.message));
      scroll();
      if (approveIt) {
        refreshClaimCount();
        toast(r.plan.status === "done" ? "План выполнен" : r.repeated ? "План уже выполнялся" : "План выполнен не полностью — см. шаги", r.plan.status === "failed");
      }
    });
  });
}

VIEWS.copilot = viewCopilot;
