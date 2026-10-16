// Панель продавца AdalZakaz. Ванильный JS, без сборки.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const money = (n) => (n ?? 0).toLocaleString("ru-RU") + " тг";
const dt = (iso) => (iso ? new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const d = (iso) => (iso ? new Date(iso).toLocaleDateString("ru-RU") : "—");
let META = null;

async function api(path, opts = {}) {
  const o = { ...opts, headers: { ...(opts.headers || {}) } };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; delete o.json; }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = "/login"; throw new Error("Нужен вход"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || data.detail?.[0]?.msg || `Ошибка ${r.status}`);
  return data;
}

function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.setAttribute("role", err ? "alert" : "status");
  t.innerHTML = `<span class="ico" aria-hidden="true">${icon(err ? "alert" : "check")}</span><span></span><button class="x" type="button" aria-label="Закрыть уведомление">${icon("x", 16)}</button>`;
  t.children[1].textContent = msg;
  $(".x", t).onclick = () => t.remove();
  $("#toasts").append(t);
  setTimeout(() => t.remove(), err ? 8000 : 3500);
}

// ---------------- тема: системная → светлая → тёмная ----------------
const THEMES = [null, "light", "dark"];
const THEME_ICON = { null: "monitor", light: "sun", dark: "moon" };
const THEME_NAME = { null: "системная", light: "светлая", dark: "тёмная" };
function paintTheme() {
  const cur = document.documentElement.dataset.theme || null;
  const b = $("#theme"); b.innerHTML = icon(THEME_ICON[cur], 20); b.setAttribute("aria-label", `Тема: ${THEME_NAME[cur]}. Сменить`);
}
$("#theme").onclick = () => {
  const cur = document.documentElement.dataset.theme || null;
  const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
  if (next) document.documentElement.dataset.theme = next; else delete document.documentElement.dataset.theme;
  try { next ? localStorage.setItem("adal_theme", next) : localStorage.removeItem("adal_theme"); } catch {}
  paintTheme(); toast(`Тема: ${THEME_NAME[next]}`);
};
paintTheme();

// Строки таблиц, открывающие карточку, доступны с клавиатуры (Tab + Enter/Пробел)
// + подписи ячеек: на мобильном строки таблиц показываются карточками «подпись — значение»
new MutationObserver(() => {
  $$("tr.click:not([tabindex])").forEach((r) => { r.tabIndex = 0; r.setAttribute("role", "button"); });
  $$("table:not(.rt)").forEach((t) => {
    const head = $("tr", t); const hs = head ? $$("th", head).map((th) => th.textContent.trim()) : [];
    if (!hs.length) return;
    t.classList.add("rt"); head.classList.add("hd");
    $$("tr", t).forEach((tr) => [...tr.children].forEach((td, i) => { if (td.tagName === "TD" && hs[i]) td.dataset.label = hs[i]; }));
  });
}).observe(document.body, { childList: true, subtree: true });
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches?.("tr.click")) { e.preventDefault(); e.target.click(); }
});

const SKELETON = `<div class="skel" aria-busy="true" aria-label="Загрузка"><i class="h"></i><div class="tiles"><i class="box"></i><i class="box"></i><i class="box"></i><i class="box"></i></div><i class="box"></i><i></i><i></i></div>`;
const emptyState = (title, hint = "", cta = "") => `<div class="empty"><div class="em-t">${title}</div>${hint ? `<div>${hint}</div>` : ""}${cta}</div>`;

// Обёртка для кнопок: блокирует кнопку, показывает спиннер, ловит ошибку
async function act(btn, fn) {
  const html = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="spin"></span> ${html}`; }
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) { btn.disabled = false; btn.innerHTML = html; } }
}

const EVENT_LABELS = {
  order_created: "Заказ создан, остаток зарезервирован", payment_confirmed: "Оплата подтверждена из источника",
  status_paid: "Статус: оплачен", status_assembled: "Статус: собран", status_shipped: "Передан в доставку (списан со склада)",
  status_delivered: "Выдан покупателю", status_closed: "Заказ закрыт", status_cancelled: "Заказ отменён (резерв снят)",
  evidence_added: "Добавлено доказательство", address_confirmed: "Адрес и окно доставки подтверждены",
  clarification_recorded: "Зафиксирован ответ покупателя на уточнение", claim_received: "Претензия принята — запущен счётчик",
  claim_type_confirmed: "Тип претензии подтверждён человеком", claim_decided: "Решение по претензии (человек)",
  refund_registered: "Возврат денег зарегистрирован", returned_to_stock: "Товар возвращён на склад",
  claim_closed: "Претензия закрыта", ai_draft_sent: "Черновик ИИ отправлен (после проверки)",
  ai_draft_approved: "Черновик ИИ утверждён", ai_draft_rejected: "Черновик ИИ отклонён",
};
const TASK_LABELS = {
  card_draft: "Карточка kk/ru", card_check: "Проверка карточки", clarify: "Уточнение до отправки",
  reply_draft: "Ответ покупателю", claim_intake: "Приём претензии", claim_response: "Письменный ответ на претензию",
  reasons_analysis: "Разбор причин",
};

// ---------------- навигация ----------------
const VIEWS = { dash: viewDash, orders: viewOrders, products: viewProducts, claims: viewClaims, analytics: viewAnalytics, rules: viewRules };
function go(view, focus = false) {
  if (location.hash.slice(1) !== view) history.replaceState(null, "", "#" + view);
  $$("#nav button").forEach((b) => (b.dataset.view === view ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  if (!$("#view").children.length || focus) $("#view").innerHTML = SKELETON;
  VIEWS[view]()
    .then(() => focus && $("#view").focus({ preventScroll: true }))
    .catch((e) => ($("#view").innerHTML = `<div class="note bad"><div><b>Не удалось загрузить раздел.</b> ${esc(e.message)}<br><button class="btn small" onclick="go('${view}')">Повторить</button></div></div>`));
}
$("#nav").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { go(b.dataset.view, true); scrollTo(0, 0); } });
window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (VIEWS[v]) go(v, true); });

async function boot() {
  META = await api("/api/meta");
  $("#seller-name").textContent = META.seller_name || "Паспорт товара · журнал заказа · претензии";
  renderAiStatus();
  refreshClaimCount();
  go(VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : "dash");
}
function renderAiStatus() {
  const a = META.ai;
  $("#ai-status").innerHTML = `ИИ: ${a.available ? `<span class="chip ai">${esc(a.provider)}</span>` : `<span class="chip warn">демо без ключа</span>`}
    · режим <b>${a.mode === "shadow" ? "теневой" : "черновики"}</b><br>Kaspi: ${META.kaspi_mode === "api" ? "API" : "демо"}<br>`;
}
async function refreshClaimCount() {
  const cl = await api("/api/claims?status=active");
  const el = $("#claims-count");
  el.hidden = !cl.length; el.textContent = cl.length;
}

// ---------------- drawer ----------------
let drawerOpener = null;
function openDrawer(html) {
  const wasOpen = !!$(".drawer");
  const scroll = wasOpen ? $(".drawer").scrollTop : 0;
  if (!wasOpen) drawerOpener = document.activeElement;
  $("#drawer-root").innerHTML = `<div class="drawer-back"></div><div class="drawer" role="dialog" aria-modal="true" tabindex="-1">
    <div class="drawer-bar"><span class="muted" style="margin-right:auto;font-size:.875rem">Esc — закрыть</span><button class="btn small drawer-close" type="button">${icon("x", 16)} Закрыть</button></div>${html}</div>`;
  const dr = $(".drawer");
  const h = $("h1", dr); if (h) { h.id = "drawer-title"; dr.setAttribute("aria-labelledby", "drawer-title"); }
  $(".drawer-back").onclick = closeDrawer; $(".drawer-close").onclick = closeDrawer;
  document.body.style.overflow = "hidden";
  if (wasOpen) dr.scrollTop = scroll; else dr.focus();
  return dr;
}
function closeDrawer() {
  if (!$(".drawer")) return;
  $("#drawer-root").innerHTML = ""; document.body.style.overflow = "";
  if (drawerOpener?.isConnected) drawerOpener.focus();
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") return closeDrawer();
  if (e.key !== "Tab" || !$(".drawer")) return;
  // фокус не уходит из диалога
  const f = $$('.drawer :is(a[href],button,input,select,textarea,[tabindex="0"]):not([disabled])').filter((x) => x.offsetParent);
  if (!f.length) return;
  if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
  else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
});

// ---------------- общие компоненты ----------------
function chipStatus(s, label) {
  const cls = { delivered: "ok", closed: "ok", paid: "ok", cancelled: "bad", new: "warn" }[s] || "";
  return `<span class="chip ${cls}">${esc(label || s)}</span>`;
}
function timerChip(t, status) {
  if (status !== "open") return `<span class="chip ok">отвечено${t.answered_on_time === false ? " с опозданием" : ""}</span>`;
  if (t.overdue) return `<span class="chip bad timer">просрочено на ${-t.days_left} дн.</span>`;
  return `<span class="chip ${t.urgent ? "bad" : "warn"} timer">${t.days_left} дн. до ${d(t.deadline)}</span>`;
}
function evidenceThumb(e) {
  const src = `/files/${encodeURIComponent(e.filename)}`;
  let media = `<a href="${src}" target="_blank">файл</a>`;
  if (e.mime?.startsWith("image/")) media = `<a href="${src}" target="_blank"><img src="${src}" alt=""></a>`;
  else if (e.mime?.startsWith("video/")) media = `<video src="${src}" controls preload="metadata"></video>`;
  else if (e.mime?.startsWith("audio/")) media = `<audio src="${src}" controls></audio>`;
  return `<div class="ev">${media}<div><b>${esc(e.kind_label)}</b> · ${dt(e.created_at)}</div>
    <div class="muted" title="${esc(e.sha256)}">SHA-256 ${esc((e.sha256 || "").slice(0, 12))}…</div>${e.note ? `<div>${esc(e.note)}</div>` : ""}</div>`;
}

function micButton(targetSel) {
  const id = "mic" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => {
    const b = document.getElementById(id); if (!b) return;
    let rec = null;
    b.onclick = async () => {
      if (!rec) {
        try { rec = await VoiceRecorder.start(); b.classList.add("rec"); b.innerHTML = `${icon("stop", 16)} Стоп`; }
        catch (e) { toast(e.message, true); }
        return;
      }
      b.classList.remove("rec"); b.innerHTML = `<span class="spin"></span> Распознаю…`;
      const blob = await rec.stop(); rec = null;
      const fd = new FormData(); fd.append("file", blob, "voice.wav");
      try {
        const r = await api("/api/ai/transcribe", { method: "POST", body: fd });
        if (r.text) { const t = $(targetSel); t.value = (t.value ? t.value + " " : "") + r.text; }
        else toast(r.note || "Речь не распознана", true);
      } catch (e) { toast(e.message, true); }
      b.innerHTML = `${icon("mic", 16)} Голос`;
    };
  });
  return `<button type="button" class="btn small mic" id="${id}" title="Надиктовать (ru/kk)">${icon("mic", 16)} Голос</button>`;
}

function renderDraft(dr, opts = {}) {
  const src = dr.output?._source || dr.source;
  const srcLabel = src === "llm" ? `модель ${esc(dr.model)}` : src === "fallback" ? "модель недоступна → эвристика" : "демо-эвристика";
  const guard = (dr.guard || []).map((g) => `<div class="note ${g.severity === "block" ? "bad" : "warn"}">${esc(g.message)}</div>`).join("");
  const open = ["draft", "shadow"].includes(dr.status);
  const text = dr.text_final ?? dr.text_original ?? dr.text ?? "";
  const extra = [];
  const o = dr.output || {};
  if (o.missing?.length) extra.push(`<div class="note warn">Нет фактов: ${o.missing.map(esc).join(", ")}</div>`);
  if (o.notes?.length) extra.push(`<div class="muted">${o.notes.map(esc).join("<br>")}</div>`);
  if (o.used_fields?.length) extra.push(`<div class="muted">Опирается на поля: ${o.used_fields.map(esc).join(", ")}</div>`);
  if (o.needs_human?.length) extra.push(`<div class="note warn">Нужен человек: ${o.needs_human.map(esc).join("; ")}</div>`);
  if (o.photo_findings?.length) extra.push(`<div class="muted">По фото: ${o.photo_findings.map(esc).join("; ")}</div>`);
  const shadow = dr.mode === "shadow" || META.ai.mode === "shadow";
  const canSend = opts.sendable && !shadow;
  return `<div class="draft" data-draft="${dr.id ?? dr.draft_id}">
    <div class="draft-head"><span class="row" style="gap:6px">${icon("ai", 16)} Черновик ИИ · ${esc(TASK_LABELS[dr.task] || dr.task)} · ${srcLabel}</span>
      <span>${open ? (shadow ? `<span class="chip warn">теневой режим — не отправляется</span>` : `<span class="chip ai">ждёт проверки</span>`) : `<span class="chip">${esc(dr.status)}</span>`}</span></div>
    ${guard}${extra.join("")}
    ${text ? `<textarea rows="4" ${open ? "" : "readonly"}>${esc(text)}</textarea>` : ""}
    ${open ? `<div class="row" style="margin-top:6px">
      ${canSend ? `<button class="btn small primary" data-dact="send">Отправить покупателю</button>` : ""}
      ${opts.fill ? `<button class="btn small primary" data-dact="fill">Подставить в форму</button>` : ""}
      <button class="btn small" data-dact="approve">${shadow ? "Оценить: верно / исправлено" : "Утвердить"}</button>
      <button class="btn small danger" data-dact="reject">Отклонить</button></div>` : ""}
  </div>`;
}
function bindDrafts(root, onDone, onFill) {
  $$("[data-draft]", root).forEach((box) => {
    $$("[data-dact]", box).forEach((b) => (b.onclick = () => act(b, async () => {
      const id = box.dataset.draft, action = b.dataset.dact, ta = $("textarea", box);
      if (action === "fill") { onFill && onFill(id); return; }
      const r = await api(`/api/drafts/${id}/review`, { method: "POST", json: { action, text: ta ? ta.value : null } });
      toast(action === "send" ? "Отправлено и записано в журнал" : `Черновик: ${r.status}${r.edited ? " (с правками)" : ""}`);
      if (action === "send" && ta) navigator.clipboard?.writeText(ta.value).catch(() => {});
      onDone && onDone();
    })));
  });
}

// ---------------- Сводка ----------------
async function viewDash() {
  const [m, cl, inbox] = await Promise.all([api("/api/metrics"), api("/api/claims?status=active"), api("/api/inbox")]);
  const rejected = inbox.filter((i) => i.status.startsWith("rejected")).slice(0, 5);
  $("#view").innerHTML = `
    <div class="topbar"><div><h1>Сводка</h1><div class="sub">Что требует внимания сегодня</div></div>
      <div class="row"><button class="btn" id="kaspi">${icon("refresh")} Забрать заказы Kaspi</button><button class="btn primary" id="new-order">${icon("plus")} Новый заказ</button></div></div>
    ${m.claims_overdue_open > 0 ? `<div class="note bad"><div><b>Просрочено претензий: ${m.claims_overdue_open}.</b> Ответьте сегодня — срок ответа по закону 10 дней.</div></div>` : ""}
    <div class="grid g2">
      <div class="card"><div class="card-head"><h2>Претензии в работе</h2>${cl.length ? `<button class="btn ghost small" data-go="claims">Все ${icon("arrowRight", 16)}</button>` : ""}</div>
        ${cl.length ? `<div class="table-wrap"><table><tr><th>№</th><th>Заказ</th><th>Тип</th><th>Срок ответа</th></tr>
        ${cl.map((c) => `<tr class="click" data-claim="${c.id}"><td><b>${esc(c.number)}</b></td><td>${esc(c.order_number)}</td>
          <td>${c.type ? esc(c.type_label) : `<span class="chip ai" title="Предложение ИИ, ещё не подтверждено">ИИ: ${esc(c.ai_type_label)}</span>`}</td><td>${timerChip(c.timer, c.status)}</td></tr>`).join("")}</table></div>`
        : emptyState("Открытых претензий нет", "Новые появятся здесь автоматически.")}
      </div>
      <div class="card"><h2>Не принятые заказы</h2>
        <p class="muted">Заказ без свободного остатка не принимается — так один товар не продаётся дважды.</p>
        ${rejected.length ? rejected.map((r) => `<div class="note warn"><div>${esc(r.source)} · ${dt(r.created_at)}<br>${esc(r.error)}</div></div>`).join("") : emptyState("Все заказы приняты")}
      </div>
    </div>
    <h2 style="margin-top:8px">Показатели</h2>
    ${metricTiles(m)}`;
  $$("[data-go]").forEach((b) => (b.onclick = () => go(b.dataset.go, true)));
  $$("[data-claim]").forEach((r) => (r.onclick = () => openClaim(r.dataset.claim)));
  $("#kaspi").onclick = (e) => act(e.currentTarget, kaspiSync);
  $("#new-order").onclick = newOrderForm;
}
async function kaspiSync() {
  const r = await api("/api/connectors/kaspi/sync", { method: "POST" });
  if (r.error) return toast(r.error, true);
  const done = r.results.filter((x) => x.status === "done").length;
  toast(`Kaspi (${r.mode === "api" ? "API" : "демо"}): принято ${done}, отклонено ${r.results.length - done}`);
  go(location.hash.slice(1) || "dash");
}
function metricTiles(m) {
  const t = (v, k, warn) => `<div class="tile${warn ? " alert" : ""}"><div class="v">${warn ? icon("alert", 22) + " " : ""}${v ?? "—"}</div><div class="k">${k}</div></div>`;
  const p = (v) => (v == null ? "—" : v + "%");
  return `<div class="tiles">
    ${t(m.orders_total, "заказов")}
    ${t(p(m.no_stock_rate), `отмен из-за отсутствия товара (${m.no_stock_cancellations})`)}
    ${t(p(m.otif_approx), "OTIF (прибл.): вовремя и в полном объёме")}
    ${t(m.first_response_p90_min == null ? "—" : m.first_response_p90_min + " мин", "P90 первого ответа")}
    ${t(p(m.not_as_described_rate), "претензий «не соответствует описанию» от выданных")}
    ${t(p(m.claims_on_time_rate), "претензий отвечено в срок 10 дней")}
    ${t(m.claims_overdue_open, "просроченных претензий", m.claims_overdue_open > 0)}
    ${t(m.claim_resolution_avg_days == null ? "—" : m.claim_resolution_avg_days + " дн.", "средний срок решения")}
    ${t(p(m.ai_drafts_edited_rate), `черновиков ИИ исправлено человеком (из ${m.ai_drafts_reviewed})`)}
    ${t(money(m.orders_value), "сумма заказов (без отмен)")}
    ${t(money(m.payments_received), "подтверждено оплат, включая отменённые заказы")}
    ${t(money(m.unpaid_orders_value), "ожидается оплата")}
    ${t(money(m.revenue), "оплаты за вычетом зарегистрированных возвратов")}
    ${t(money(m.refunds), "зарегистрировано возвратов денег")}
  </div>`;
}

// ---------------- Заказы ----------------
async function viewOrders() {
  const st = sessionStorage.getItem("f_status") || "", ch = sessionStorage.getItem("f_channel") || "";
  const list = await api(`/api/orders?${new URLSearchParams({ ...(st && { status: st }), ...(ch && { channel: ch }) })}`);
  const opt = (obj, cur) => `<option value="">все</option>` + Object.entries(obj).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#view").innerHTML = `
    <div class="topbar"><div><h1>Заказы</h1><div class="sub">${list.length} ${st || ch ? "по фильтру" : "всего"}</div></div>
      <div class="row"><button class="btn" id="kaspi">${icon("refresh")} Kaspi</button><button class="btn primary" id="new-order">${icon("plus")} Новый заказ</button></div></div>
    <div class="row" style="margin-bottom:12px">
      <label class="sr-only" for="f-status">Статус</label><select id="f-status" style="width:auto">${opt(META.statuses, st).replace(">все<", ">Все статусы<")}</select>
      <label class="sr-only" for="f-channel">Канал</label><select id="f-channel" style="width:auto">${opt(META.channels, ch).replace(">все<", ">Все каналы<")}</select>
      ${st || ch ? `<button class="btn ghost small" id="f-reset">Сбросить фильтры</button>` : ""}</div>
    <div class="card flush">${list.length ? `<div class="table-wrap"><table>
      <tr><th>№</th><th>Канал</th><th class="hide-sm">Создан</th><th>Статус</th><th>Оплата</th><th class="num">Сумма</th><th></th></tr>
      ${list.map((o) => `<tr class="click" data-order="${o.id}"><td><b>${esc(o.number)}</b></td><td>${esc(o.channel_label)}</td>
        <td class="hide-sm">${dt(o.created_at)}</td><td>${chipStatus(o.status, o.status_label)}</td>
        <td>${o.payment_status === "paid" ? `<span class="chip ok">оплачен</span>` : `<span class="chip">нет</span>`}</td>
        <td class="num">${money(o.total)}</td><td>${o.open_claims ? `<span class="chip bad">претензия</span>` : ""}</td></tr>`).join("")}
    </table></div>` : (st || ch ? emptyState("По этому фильтру заказов нет", "Попробуйте сбросить фильтры.")
      : emptyState("Заказов пока нет", "Заберите заказы из Kaspi или создайте заказ из OLX, Instagram, WhatsApp.", `<button class="btn primary" id="empty-new">${icon("plus")} Новый заказ</button>`))}</div>`;
  $("#f-reset") && ($("#f-reset").onclick = () => { sessionStorage.removeItem("f_status"); sessionStorage.removeItem("f_channel"); go("orders"); });
  $("#empty-new") && ($("#empty-new").onclick = newOrderForm);
  $("#f-status").onchange = (e) => { sessionStorage.setItem("f_status", e.target.value); go("orders"); };
  $("#f-channel").onchange = (e) => { sessionStorage.setItem("f_channel", e.target.value); go("orders"); };
  $$("[data-order]").forEach((r) => (r.onclick = () => openOrder(r.dataset.order)));
  $("#kaspi").onclick = (e) => act(e.currentTarget, kaspiSync);
  $("#new-order").onclick = newOrderForm;
}

async function newOrderForm() {
  const products = await api("/api/products");
  const channels = Object.entries(META.channels).filter(([k]) => k !== "kaspi");
  const prodOpts = `<option value="" selected disabled>Выберите товар</option>` + products.map((p) => `<option value="${esc(p.sku)}" ${p.free <= 0 ? "disabled" : ""}>${esc(p.sku)} — ${esc(p.passport.data.title_ru)} (свободно ${p.free})</option>`).join("");
  const dr = openDrawer(`<h1>Новый заказ</h1>
    <p class="muted">Для OLX, Instagram, WhatsApp, Telegram. Заказы Kaspi приходят только через API. Заказ проходит через ту же очередь и резерв, что и Kaspi.</p>
    <form id="of" class="card">
      <div class="grid g2">
        <div class="field"><label for="o-ch">Канал</label><select id="o-ch" name="channel">${channels.map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
        <div class="field"><label for="o-lang">Язык покупателя</label><select id="o-lang" name="lang"><option value="ru">русский</option><option value="kk">қазақша</option></select></div>
        <div class="field"><label for="o-name">Имя <span class="req" aria-hidden="true">*</span></label><input id="o-name" name="name" autocomplete="name" required></div>
        <div class="field"><label for="o-phone">Телефон</label><input id="o-phone" name="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+7 7__ ___ __ __" pattern="[0-9+ ]{10,20}" title="Например: +7 701 123 45 67"></div>
        <div class="field"><label for="o-addr">Адрес</label><input id="o-addr" name="address" autocomplete="street-address"></div>
        <div class="field"><label for="o-zone">Зона доставки</label><select id="o-zone" name="zone"><option>Тараз</option><option>Жамбылский район</option><option>Байзакский район</option><option>Таласский район</option><option>Другой район области</option></select></div>
      </div>
      <h3>Товары</h3>${products.length ? "" : `<div class="note warn"><div>Нет товаров. Сначала создайте паспорт товара в разделе «Товары».</div></div>`}<div id="items"></div>
      <button type="button" class="btn small" id="add-item">${icon("plus", 16)} Добавить товар</button><hr>
      <div class="form-actions"><button class="btn primary" ${products.length ? "" : "disabled"}>Принять заказ</button><button type="button" class="btn ghost" id="o-cancel">Отмена</button></div>
      <span class="hint">Остаток резервируется сразу. Отменить заказ можно в его карточке.</span>
    </form>`);
  let n = 0;
  const addItem = () => { n++; $("#items", dr).insertAdjacentHTML("beforeend", `<div class="grid item" style="grid-template-columns:2.5fr .7fr 1.8fr auto;align-items:end;gap:8px">
      <div class="field"><label for="i-sku${n}">Товар</label><select id="i-sku${n}" name="sku" required>${prodOpts}</select></div>
      <div class="field"><label for="i-qty${n}">Кол-во</label><input id="i-qty${n}" name="qty" type="number" inputmode="numeric" min="1" value="1" required></div>
      <div class="field"><label for="i-var${n}">Размер / цвет</label><select id="i-var${n}" name="variant" disabled><option value="">Выберите товар</option></select><small class="hint variant-hint"></small></div>
      <div class="field"><button type="button" class="icon-btn rm" aria-label="Убрать товар">${icon("x")}</button></div></div>`);
    const row = $("#items", dr).lastElementChild;
    const select = $("[name=variant]", row), qty = $("[name=qty]", row);
    $("[name=sku]", row).onchange = () => {
      const p = products.find((p) => p.sku === $("[name=sku]", row).value);
      const opts = p.variant_options || [];
      select.disabled = !opts.length; select.required = !!opts.length;
      select.innerHTML = (opts.length > 1 ? `<option value="">Выберите вариант</option>` : "") + (opts.length ? opts.map((label) => {
        const v = p.inventory_variants.find((v) => v.label === label);
        return `<option value="${esc(label)}" ${v && v.free <= 0 ? "disabled" : ""}>${esc(label)}${v ? ` — доступно ${v.free}` : ""}</option>`;
      }).join("") : `<option value="">Без вариантов</option>`);
      $(".variant-hint", row).textContent = opts.length && !p.inventory_variants.length ? "Остаток общий. Настройте количество каждого размера в карточке товара." : "";
      select.onchange = () => { const v = p.inventory_variants.find((v) => v.label === select.value); qty.max = v ? v.free : p.free; };
      select.onchange();
    };
    $(".rm", row).onclick = () => { if ($$(".item", dr).length > 1) row.remove(); else toast("В заказе должен быть хотя бы один товар", true); };
  };
  addItem();
  $("#add-item", dr).onclick = addItem;
  $("#o-cancel", dr).onclick = closeDrawer;
  const key = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  $("#of", dr).onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => {
    const f = new FormData(e.target);
    const items = $$(".item", dr).map((row) => ({ sku: $("[name=sku]", row).value, qty: +$("[name=qty]", row).value, variant: $("[name=variant]", row).value }));
    const r = await api("/api/orders", { method: "POST", headers: { "Idempotency-Key": key }, json: {
      channel: f.get("channel"), delivery_zone: f.get("zone"), items,
      buyer: { name: f.get("name"), phone: f.get("phone"), address: f.get("address"), lang: f.get("lang") } } });
    toast("Заказ принят, остаток зарезервирован"); go(location.hash.slice(1) || "dash"); openOrder(r.order_id);
  }); };
}

const FLOW = ["new", "paid", "assembled", "shipped", "delivered", "closed"];
async function openOrder(id) {
  const o = await api(`/api/orders/${id}`);
  const idx = FLOW.indexOf(o.status);
  const hasPack = o.evidence.some((e) => ["packing_video", "packing_photo"].includes(e.kind));
  const statusBtns = o.next_statuses.filter((s) => s !== "cancelled" && s !== "paid")
    .map((s) => `<button class="btn primary small" data-status="${s}">${esc(META.statuses[s])} ${icon("arrowRight", 16)}</button>`).join("");
  const dr = openDrawer(`
    <div class="row between"><h1>${esc(o.number)} · ${esc(o.channel_label)}</h1>
      <div class="row">${chipStatus(o.status, o.status_label)} ${o.payment_status === "paid" ? `<span class="chip ok">оплата: ${esc(META.payment_sources[o.payment_source] || o.payment_source)}</span>` : `<span class="chip warn">не оплачен</span>`}</div></div>
    <div class="steps">${o.status === "cancelled" ? `<span class="step">Отменён: ${esc(o.cancel_reason)}</span>` :
      FLOW.map((s, i) => `<span class="step ${i < idx ? "done" : i === idx ? "now" : ""}">${esc(META.statuses[s])}</span>`).join("")}</div>
    <div class="grid g2">
      <div class="card"><h2>Состав заказа</h2>
        ${o.items.map((it) => `<div style="margin-bottom:8px"><b>${esc(it.title_ru)}</b> × ${it.qty} — ${money(it.price)}
          ${it.variant ? `<span class="chip">${esc(it.variant)}</span>` : ""}<br>
          <small>${esc(it.sku)} · продан по паспорту v${it.passport_version}${it.passport_outdated ? ` <span class="chip warn">паспорт обновлён после продажи</span>` : ""}</small></div>`).join("")}
        <div class="row between"><span class="muted">Итого</span><b>${money(o.total)}</b></div>
        <hr><div class="muted">Покупатель: ${esc(o.buyer_name || "—")} · ${esc(o.buyer_phone || "")} · язык ${o.buyer_lang}</div>
        <div class="muted">Адрес: ${esc(o.buyer_address || "—")} ${o.address_confirmed ? `<span class="chip ok">подтверждён ${esc(o.delivery_window || "")}</span>` : ""}</div>
        ${o.clarification ? `<div class="note ok">Уточнение: ${esc(o.clarification)}</div>` : ""}
      </div>
      <div class="card"><h2>Следующий шаг</h2>
        ${o.status === "new" && o.payment_status !== "paid" ? `
          <form id="pay"><p class="muted">Оплата подтверждается только из платёжного источника. <b>Скриншот от покупателя не принимается.</b></p>
          <div class="field"><label>Источник</label><select name="source">${Object.entries(META.payment_sources).filter(([k]) => o.channel === "kaspi" || k !== "kaspi_api").map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
          <div class="grid g2"><div class="field"><label>№ операции</label><input name="ref" required></div>
          <div class="field"><label>Сумма</label><input name="amount" type="number" value="${o.total}"></div></div>
          <button class="btn primary">Подтвердить оплату</button></form>` : ""}
        ${o.status === "paid" && !hasPack ? `<div class="note warn">Перед сборкой снимите видео упаковки: что положили, состояние, комплект.</div>` : ""}
        ${["paid", "assembled"].includes(o.status) && o.channel !== "kaspi" && !o.address_confirmed ? `
          <form id="addr"><h3>Подтвердить адрес до отправки</h3>
          <div class="field"><input name="address" value="${esc(o.buyer_address || "")}" placeholder="Адрес"></div>
          <div class="grid g2"><div class="field"><select name="zone">${["Тараз", "Жамбылский район", "Байзакский район", "Таласский район", "Другой район области"].map((z) => `<option ${z === o.delivery_zone ? "selected" : ""}>${z}</option>`).join("")}</select></div>
          <div class="field"><input name="window" placeholder="Окно: завтра 10:00–14:00" required></div></div>
          <button class="btn small">Адрес подтверждён покупателем</button></form>` : ""}
        <div class="row" style="margin-top:10px">${statusBtns}
          ${o.next_statuses.includes("cancelled") ? `<select id="cancel-reason" style="width:auto"><option value="buyer_request">по просьбе покупателя</option><option value="no_stock">нет товара</option><option value="no_payment">нет оплаты</option><option value="other">другое</option></select><button class="btn small danger" id="cancel">Отменить</button>` : ""}</div>
        ${o.buyer_url ? `<hr><h3>Страница заказа для покупателя (kk/ru)</h3>
          <div class="row"><div class="qr">${await api(`/api/orders/${o.id}/qr.svg`)}</div>
          <div><a href="${esc(o.buyer_url)}" target="_blank">${esc(o.buyer_url)}</a><br><button class="btn small" id="copy-link">Скопировать ссылку</button>
          <p class="muted">Что куплено, условия возврата простым языком и кнопка претензии. Отправьте ссылку в чат с покупателем.</p></div></div>`
          : `<hr><p class="muted">Заказ Kaspi: покупателя на внешние страницы не уводим, возврат — через процесс Kaspi.</p>`}
      </div>
    </div>

    <div class="card"><h2>Доказательства</h2>
      <div class="thumbs">${o.evidence.map(evidenceThumb).join("") || `<span class="muted">Пока нет</span>`}</div>
      <form id="ev" class="row" style="margin-top:10px">
        <select name="kind" style="width:auto" aria-label="Тип доказательства">${["packing_video", "packing_photo", "condition_photo", "return_video", "chat", "other"].map((k) => `<option value="${k}">${esc(META.evidence_kinds[k])}</option>`).join("")}</select>
        <input type="file" name="file" accept="video/*,image/*,audio/*" capture="environment" required style="width:auto" aria-label="Файл">
        <input name="note" placeholder="Заметка (необязательно)" aria-label="Заметка" style="width:220px"><button class="btn small">Загрузить</button></form>
      <small class="muted">Файл хранится под SHA-256 — видно, что его не подменили. Покупатель уведомлён о фиксации упаковки.</small>
    </div>

    <div class="grid g2">
      <div class="card"><h2>Уточнение до отправки</h2>
        <p class="muted">ИИ предложит вопрос, если карточка допускает разные варианты. Ответ фиксирует программа.</p>
        <button class="btn ai" id="ai-clarify">${icon("ai")} Предложить вопрос</button>
        <div id="clarify-box"></div>
        <form id="clar" style="margin-top:8px"><div class="field"><input name="question" placeholder="Вопрос"></div>
        <div class="field"><input name="answer" placeholder="Ответ покупателя (например: размер M, 42)"></div><button class="btn small">Зафиксировать ответ</button></form>
      </div>
      <div class="card"><h2>Переписка</h2>
        <div class="chat">${o.messages.map((m) => `<div class="msg ${m.direction}">${esc(m.text)}<small class="muted">${dt(m.created_at)}${m.ai_draft_id ? " · из черновика ИИ" : ""}</small></div>`).join("") || `<span class="muted">Сообщений нет</span>`}</div>
        <form id="inmsg" style="margin-top:8px"><div class="field"><textarea id="in-text" name="text" rows="2" placeholder="Вставьте сообщение покупателя (ru / kk / смешанное)"></textarea></div>
        <div class="row"><button class="btn ai">${icon("ai")} Черновик ответа</button>${micButton("#in-text")}<button type="button" class="btn small" id="own-reply" aria-expanded="false" aria-controls="own-form">Ответить самому</button></div></form>
        <form id="own-form" hidden style="margin-top:10px"><div class="field"><label for="own-text">Ваш ответ покупателю</label><textarea id="own-text" name="text" rows="3" required></textarea></div>
          <div class="form-actions"><button class="btn primary small">Отправить</button><button type="button" class="btn ghost small" id="own-cancel">Отмена</button></div></form>
      </div>
    </div>

    <div class="card"><h2>Черновики ИИ по заказу</h2>
      ${o.drafts.filter((x) => x.task !== "claim_intake" && x.task !== "claim_response").map((x) => renderDraft(x, { sendable: true })).join("") || `<span class="muted">Нет</span>`}</div>

    <div class="card"><h2>Претензии</h2>
      ${o.claims.map((c) => `<div class="row between" style="margin-bottom:6px"><a href="#" data-claim="${c.id}">${esc(c.number)}</a> ${timerChip(c.timer, c.status)}</div>`).join("") || `<span class="muted">Нет</span>`}
      ${["shipped", "delivered", "closed"].includes(o.status) ? `<form id="claimf" style="margin-top:8px"><p class="muted">Претензия по телефону, в кабинете Kaspi или в чате — зарегистрируйте её здесь, чтобы запустить счётчик 10 дней.</p>
        <div class="field"><textarea id="claim-text" name="text" rows="2" placeholder="Текст претензии"></textarea></div>
        <div class="row"><select name="lang" style="width:auto"><option value="ru">ru</option><option value="kk">kk</option></select>${micButton("#claim-text")}<button class="btn small">Зарегистрировать претензию</button></div></form>` : ""}
    </div>

    <div class="card"><details class="more"><summary>Журнал заказа (${o.events.length})</summary><ul class="timeline">
      ${o.events.map((e) => `<li>${dt(e.created_at)} — ${esc(EVENT_LABELS[e.type] || e.type)} <small class="muted">· ${esc(e.actor)}${e.payload?.kind ? " · " + esc(META.evidence_kinds[e.payload.kind]) : ""}${e.payload?.ref ? " · " + esc(e.payload.ref) : ""}${e.payload?.decision ? " · " + esc(META.decisions[e.payload.decision]) : ""}</small></li>`).join("")}
    </ul></details></div>`);

  const reload = () => { go(location.hash.slice(1) || "dash"); return openOrder(id); };
  const post = (url, json) => api(url, { method: "POST", json });
  $("#pay", dr) && ($("#pay", dr).onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target);
    act(e.submitter, async () => { await post(`/api/orders/${id}/payment`, { source: f.get("source"), ref: f.get("ref"), amount: +f.get("amount") }); toast("Оплата подтверждена"); reload(); }); });
  $("#addr", dr) && ($("#addr", dr).onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target);
    act(e.submitter, async () => { await post(`/api/orders/${id}/address`, Object.fromEntries(f)); reload(); }); });
  $$("[data-status]", dr).forEach((b) => (b.onclick = () => act(b, async () => { await post(`/api/orders/${id}/status`, { status: b.dataset.status }); toast("Статус обновлён"); reload(); })));
  $("#cancel", dr) && ($("#cancel", dr).onclick = (e) => confirm("Отменить заказ и снять резерв?") && act(e.currentTarget, async () => {
    await post(`/api/orders/${id}/status`, { status: "cancelled", reason: $("#cancel-reason", dr).value }); reload(); }));
  $("#copy-link", dr) && ($("#copy-link", dr).onclick = () => { navigator.clipboard.writeText(o.buyer_url); toast("Ссылка скопирована"); });
  $("#ev", dr).onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => { await api(`/api/orders/${id}/evidence`, { method: "POST", body: new FormData(e.target) }); toast("Доказательство сохранено"); reload(); }); };
  $("#ai-clarify", dr).onclick = (e) => act(e.currentTarget, async () => {
    const r = await post(`/api/orders/${id}/ai/clarify`);
    if (!r.output.needed) { $("#clarify-box", dr).innerHTML = `<div class="note ok">Уточнение не требуется — вариант однозначен.</div>`; return; }
    await reload();
    $("#clar [name=question]").value = r.text;
    $("#clar").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#clar", dr).onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target); act(e.submitter, async () => { await post(`/api/orders/${id}/clarification`, Object.fromEntries(f)); reload(); }); };
  $("#inmsg", dr).onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => { await post(`/api/orders/${id}/ai/reply`, { text: $("#in-text", dr).value }); reload(); }); };
  const ownToggle = (show) => { $("#own-form", dr).hidden = !show; $("#own-reply", dr).setAttribute("aria-expanded", show); if (show) $("#own-text", dr).focus(); };
  $("#own-reply", dr).onclick = () => ownToggle($("#own-form", dr).hidden);
  $("#own-cancel", dr).onclick = () => ownToggle(false);
  $("#own-form", dr).onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => { await post(`/api/orders/${id}/messages`, { text: $("#own-text", dr).value }); toast("Ответ отправлен и записан в журнал"); reload(); }); };
  $("#claimf", dr) && ($("#claimf", dr).onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target);
    act(e.submitter, async () => { const r = await post("/api/claims", { order_id: +id, text: f.get("text"), lang: f.get("lang") }); refreshClaimCount(); openClaim(r.id); }); });
  $$("[data-claim]", dr).forEach((a) => (a.onclick = (e) => { e.preventDefault(); openClaim(a.dataset.claim); }));
  bindDrafts(dr, reload);
}

// ---------------- Товары ----------------
async function viewProducts() {
  const list = await api("/api/products");
  $("#view").innerHTML = `
    <div class="topbar"><div><h1>Товары и паспорта</h1><div class="sub">Один остаток на все каналы</div></div><div class="row"><button class="btn primary" id="new-p">${icon("plus")} Новый товар</button></div></div>
    <div class="card flush">${list.length ? "" : emptyState("Товаров пока нет", "Паспорт товара — основа для карточек, заказов и ответов на претензии.")}<div class="table-wrap" ${list.length ? "" : "hidden"}><table>
      <tr><th>Артикул</th><th>Товар</th><th class="hide-sm">Категория</th><th class="num">Остаток</th><th class="num">Резерв</th><th class="num">Свободно</th><th>Паспорт</th></tr>
      ${list.map((p) => { const ch = p.passport.check_result || {}; const miss = (ch.missing || []).length, ai = (ch.ai_issues || []).length;
        return `<tr class="click" data-p="${p.id}"><td>${esc(p.sku)}</td><td>${esc(p.passport.data.title_ru)}</td><td class="hide-sm">${esc(p.category_label)}</td>
        <td class="num">${p.stock}</td><td class="num">${p.reserved}</td><td class="num" ${p.free <= 0 ? 'style="color:var(--bad)"' : ""}>${p.free}</td>
        <td>v${p.current_version} ${miss ? `<span class="chip bad">нет ${miss} полей</span>` : `<span class="chip ok">поля есть</span>`} ${ai ? `<span class="chip warn">${ai} замеч.</span>` : ""}
        ${p.variant_options.length && !p.inventory_variants.length ? `<span class="chip warn">остаток общий</span>` : ""}</td></tr>`; }).join("")}
    </table></div></div>`;
  $$("[data-p]").forEach((r) => (r.onclick = () => openProduct(r.dataset.p)));
  $("#new-p").onclick = () => openProduct(null);
}

const FIELD_ORDER = ["title_ru", "title_kk", "brand", "model", "country", "composition", "dimensions", "weight", "color", "size_grid", "variants", "package_contents", "warranty_months", "price", "desc_ru", "desc_kk", "supplier_text"];

function variantStockRow(label = "", stock = "", reserved = 0, locked = false) {
  return `<div class="variant-stock-row">
    <label>Размер / цвет<input name="variant-label" value="${esc(label)}" placeholder="Например: 42 / белый" maxlength="100" required ${locked ? "readonly" : ""}></label>
    <label>На складе<input name="variant-stock" type="number" inputmode="numeric" value="${stock}" min="${reserved}" step="1" required></label>
    <small class="muted">В резерве: ${reserved}</small>
    ${locked ? "" : `<button type="button" class="btn small remove-variant">Убрать</button>`}</div>`;
}

function stockEditor(p) {
  const tracked = p.inventory_variants.length > 0;
  return `<div class="card" id="variant-stock-card"><h2>Остатки по размерам и цветам</h2>
    ${tracked ? `<p class="muted">Общий остаток ${p.stock}, резерв ${p.reserved}. Укажите фактическое количество на складе, включая резерв.</p>` : `
      <form id="st" class="row"><label>Общий остаток<input name="stock" type="number" min="${p.reserved}" step="1" value="${p.stock}" required></label><button class="btn small">Сохранить общий остаток</button></form>
      <p class="muted">Чтобы проверять наличие конкретного размера и цвета, распределите фактический остаток по вариантам.</p>`}
    ${!tracked && p.reserved ? `<div class="note warn">Сначала завершите или отмените заказы с резервом (${p.reserved} шт.). После этого можно включить учёт по вариантам.</div>` : `
    <details ${tracked ? "open" : ""}><summary>${tracked ? "Изменить остатки вариантов" : "Настроить размеры и цвета"}</summary>
      <form id="variant-stock-form">
        <p class="hint">Одна строка — одна комбинация, например «42 / белый». ${tracked ? "Название существующего варианта сохраняется; для снятия с продажи укажите 0." : `Общий остаток сейчас ${p.stock}. Укажите количество каждого варианта — их сумма станет новым общим остатком.`}</p>
        <div id="variant-stock-rows">${tracked ? p.inventory_variants.map((v) => variantStockRow(v.label, v.stock, v.reserved, true)).join("") : (p.variant_options.length ? p.variant_options : [""]).map((v) => variantStockRow(v)).join("")}</div>
        <div class="row"><button type="button" class="btn small" id="add-stock-variant">Добавить размер / цвет</button><button class="btn primary small">Сохранить остатки вариантов</button></div>
      </form></details>`}
  </div>`;
}
async function openProduct(id) {
  const p = id ? await api(`/api/products/${id}`) : null;
  const data = p ? p.passport.data : {};
  const req = new Set(p ? p.required : []);
  const missing = new Set((p?.passport.check_result?.missing || []).map((m) => m.field));
  const area = (k) => ["desc_ru", "desc_kk", "supplier_text", "size_grid", "package_contents"].includes(k);
  const fields = FIELD_ORDER.map((k) => `<div class="field ${missing.has(k) ? "missing" : ""}" ${area(k) ? 'style="grid-column:1/-1"' : ""}>
      <label>${esc(META.fields[k])} ${req.has(k) ? `<span class="req">*</span>` : ""}</label>
      ${area(k) ? `<textarea name="${k}" rows="${k.startsWith("desc") ? 4 : 2}">${esc(data[k] ?? "")}</textarea>` : `<input name="${k}" value="${esc(data[k] ?? "")}" ${k === "variants" && p?.inventory_variants.length ? 'readonly aria-describedby="variant-edit-hint"' : ""}>`}
      ${k === "variants" && p?.inventory_variants.length ? `<small class="hint" id="variant-edit-hint">Добавляйте варианты в блоке «Остатки по размерам и цветам».</small>` : ""}</div>`).join("");
  const ch = p?.passport.check_result || {};
  const dr = openDrawer(`
    <h1>${p ? esc(data.title_ru) : "Новый товар"}</h1>
    ${p ? `<div class="row"><span class="chip">${esc(p.sku)}</span><span class="chip">${esc(p.category_label)}</span><span class="chip">паспорт v${p.current_version}</span>
      <span class="chip ${p.free > 0 ? "ok" : "bad"}">свободно ${p.free} из ${p.stock} (резерв ${p.reserved})</span></div>` : ""}
    <div class="grid g2" style="margin-top:12px;grid-template-columns:3fr 2fr">
      <form id="pf" class="card">
        ${p ? "" : `<div class="grid g3"><div class="field"><label>Артикул <span class="req">*</span></label><input name="sku" required></div>
          <div class="field"><label>Категория</label><select name="category">${Object.entries(META.categories).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select></div>
          <div class="field"><label>Остаток</label><input name="stock" type="number" value="0"></div></div>`}
        <div class="grid g2">${fields}</div>
        <div class="field"><label>Комментарий к версии</label><input name="note" placeholder="что изменилось"></div>
        <button class="btn primary">${p ? "Сохранить новую версию паспорта" : "Создать паспорт"}</button>
        <p class="muted">Версия неизменна после сохранения: заказ хранит ту версию, по которой товар продан.</p>
      </form>
      <div>${p ? `
        <div class="card"><h2>Проверка карточки</h2>
          ${(ch.missing || []).length ? `<div class="note bad">Не заполнено: ${ch.missing.map((m) => esc(m.label)).join(", ")}</div>` : `<div class="note ok">Обязательные поля заполнены</div>`}
          ${(ch.warnings || []).map((w) => `<div class="note warn">${esc(w)}</div>`).join("")}
          ${(ch.ai_issues || []).map((w) => `<div class="note ai">${esc(w.problem_ru)} <small>(${esc(w.field)})</small></div>`).join("")}
          ${ch.photo ? (ch.photo.observations || []).concat((ch.photo.conflicts || []).map((c) => `Фото: ${c.photo_shows} ≠ паспорт: ${c.passport_says} (${c.field})`)).map((w) => `<div class="note ai photo">${esc(w)}</div>`).join("") : ""}
          <div class="row"><button class="btn ai" id="ai-check">${icon("ai")} Проверить (ИИ + фото)</button><button class="btn ai" id="ai-draft">${icon("ai")} Черновик описания kk/ru</button></div>
          <div id="draft-box">${p.drafts.filter((x) => x.task === "card_draft" && ["draft", "shadow"].includes(x.status)).slice(0, 1).map((x) => renderDraft(x, { fill: true })).join("")}</div>
        </div>
        <div class="card"><h2>Фото реального товара</h2>
          <div class="thumbs">${(data.photos || []).map((f) => `<img src="/files/${encodeURIComponent(f)}" alt="">`).join("") || `<span class="muted">Нет фото</span>`}</div>
          <form id="ph" class="row" style="margin-top:8px"><input type="file" name="file" accept="image/*" capture="environment" required style="width:auto"><button class="btn small">Добавить</button></form>
          <small class="muted">ИИ (модель со зрением) сверит фото с полями паспорта: цвет, комплект, маркировку.</small></div>
        ${stockEditor(p)}
        <div class="card"><h2>Страница-паспорт для OLX и соцсетей</h2>
          <div class="row"><div class="qr">${await api(`/api/products/${p.id}/qr.svg`)}</div><div><a href="${esc(p.public_url)}" target="_blank">${esc(p.public_url)}</a>
          <p class="muted">Ссылку и QR — в объявление OLX или в профиль Instagram.</p></div></div></div>
        <div class="card"><h2>Версии</h2>${p.versions.map((v) => `<div><a href="#" data-v="${v.version}">v${v.version}</a> · ${dt(v.created_at)} · ${esc(v.note || "")} <small class="muted">${esc(v.author || "")}</small></div>`).join("")}<div id="diff"></div></div>
      ` : ""}</div>
    </div>`);

  $("#pf", dr).onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => {
    const f = Object.fromEntries(new FormData(e.target));
    const pdata = Object.fromEntries(FIELD_ORDER.map((k) => [k, f[k]]));
    if (p) { await api(`/api/products/${p.id}/passport`, { method: "PUT", json: { data: pdata, note: f.note || "Правка продавца" } }); toast("Сохранена новая версия паспорта"); openProduct(p.id); }
    else { const np = await api("/api/products", { method: "POST", json: { sku: f.sku, category: f.category, stock: +f.stock, data: pdata } }); toast("Паспорт создан"); openProduct(np.id); }
  }); };
  if (!p) return;
  const reload = () => { go(location.hash.slice(1) || "products"); return openProduct(p.id); };
  $("#ai-check", dr).onclick = (e) => act(e.currentTarget, async () => { await api(`/api/products/${p.id}/ai/check`, { method: "POST" }); reload(); });
  $("#ai-draft", dr).onclick = (e) => act(e.currentTarget, async () => { await api(`/api/products/${p.id}/ai/draft`, { method: "POST" }); reload(); });
  $("#ph", dr).onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => { await api(`/api/products/${p.id}/photos`, { method: "POST", body: new FormData(e.target) }); toast("Фото добавлено — новая версия паспорта"); reload(); }); };
  if ($("#st", dr)) $("#st", dr).onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => { await api(`/api/products/${p.id}/stock`, { method: "POST", json: { stock: +new FormData(e.target).get("stock") } }); toast("Остаток сохранён"); reload(); }); };
  const vf = $("#variant-stock-form", dr);
  if (vf) {
    const bindRemove = () => $$(".remove-variant", vf).forEach((b) => b.onclick = () => b.closest(".variant-stock-row").remove());
    bindRemove();
    $("#add-stock-variant", vf).onclick = () => { $("#variant-stock-rows", vf).insertAdjacentHTML("beforeend", variantStockRow()); bindRemove(); };
    vf.onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => {
      const variants = $$(".variant-stock-row", vf).map((row) => ({label: $("[name=variant-label]", row).value, stock: +$("[name=variant-stock]", row).value}));
      await api(`/api/products/${p.id}/variants`, {method: "PUT", json: {variants}});
      toast("Остатки вариантов сохранены"); reload();
    }); };
  }
  $$("[data-v]", dr).forEach((a) => (a.onclick = async (e) => { e.preventDefault();
    const v = await api(`/api/products/${p.id}/versions/${a.dataset.v}`);
    $("#diff", dr).innerHTML = `<hr><b>Изменения в v${v.version}</b>` + (v.diff.length ? v.diff.map((x) => `<div><small>${esc(x.label)}</small><br><span class="diff-old">${esc(x.old ?? "")}</span><br><span class="diff-new">${esc(x.new ?? "")}</span></div>`).join("") : `<div class="muted">Только фото</div>`); }));
  bindDrafts(dr, reload, (draftId) => {
    const dd = p.drafts.find((x) => String(x.id) === String(draftId)); const o = dd.output;
    if (o.desc_ru) $("[name=desc_ru]", dr).value = o.desc_ru;
    if (o.desc_kk) $("[name=desc_kk]", dr).value = o.desc_kk;
    if (o.title_kk && !$("[name=title_kk]", dr).value) $("[name=title_kk]", dr).value = o.title_kk;
    $("[name=note]", dr).value = "Описание kk/ru из черновика ИИ, сверено продавцом";
    api(`/api/drafts/${draftId}/review`, { method: "POST", json: { action: "approve" } }).catch(() => {});
    toast("Подставлено. Сверьте с реальным товаром и сохраните версию.");
  });
}

// ---------------- Претензии ----------------
async function viewClaims() {
  const list = await api("/api/claims");
  $("#view").innerHTML = `<div class="topbar"><div><h1>Претензии</h1><div class="sub">Одна очередь · срок ответа 10 календарных дней · решает человек</div></div></div>
    <div class="card flush">${list.length ? `<div class="table-wrap"><table><tr><th>№</th><th>Заказ</th><th class="hide-sm">Получена</th><th>Тип</th><th>Статус</th><th>Срок</th></tr>
    ${list.map((c) => `<tr class="click" data-claim="${c.id}"><td><b>${esc(c.number)}</b></td><td>${esc(c.order_number)} <small class="muted">${esc(META.channels[c.channel])}</small></td>
      <td class="hide-sm">${dt(c.received_at)}</td><td>${c.type ? esc(c.type_label) : `<span class="chip ai" title="предложение ИИ">${icon("ai", 14)} ${esc(c.ai_type_label)}</span>`}</td>
      <td>${{ open: "в работе", decided: "решение принято", closed: "закрыта" }[c.status]}</td><td>${timerChip(c.timer, c.status)}</td></tr>`).join("")}</table></div>`
      : emptyState("Претензий нет", "Претензии покупателей со страницы заказа, из Kaspi или по телефону попадут сюда.")}</div>`;
  $$("[data-claim]").forEach((r) => (r.onclick = () => openClaim(r.dataset.claim)));
}

async function openClaim(id) {
  const c = await api(`/api/claims/${id}`);
  const ev = c.evaluation, o = c.order;
  const claimEv = c.evidence.filter((e) => e.claim_id === c.id);
  const packEv = c.evidence.filter((e) => ["packing_video", "packing_photo"].includes(e.kind));
  const intake = c.drafts.find((x) => x.task === "claim_intake");
  const responses = c.drafts.filter((x) => x.task === "claim_response");
  const decisionOpts = [...ev.options, ...(ev.refusal_allowed ? ["refusal"] : [])].filter((v) => c.refund_limit > 0 || !["refund", "price_reduction"].includes(v));
  const selectedItem = o.items.find((i) => i.id === c.order_item_id);
  const needsReturnVariant = selectedItem && c.return_variants.length && !selectedItem.variant_id && !c.return_variants.some((v) => v.label.toLocaleLowerCase() === (selectedItem.variant || "").toLocaleLowerCase());
  const itemLabel = (it) => `${it.title_ru || it.sku}${it.variant ? " / " + it.variant : ""}`;
  const dr = openDrawer(`
    <div class="row between"><h1>Претензия ${esc(c.number)}</h1>${timerChip(c.timer, c.status)}</div>
    <p class="muted">Заказ <a href="#" id="to-order">${esc(o.number)}</a> · ${esc(o.channel_label)} · ${o.items.map((i) => esc(i.title_ru) + " (паспорт v" + i.passport_version + ")").join(", ")} · ${money(o.total)}
      · получена ${dt(c.received_at)} · ответить до <b>${d(c.deadline)}</b></p>
    <div class="grid g2">
      <div class="card"><h2>Что пишет покупатель</h2>
        ${c.buyer_type ? `<div class="muted">Покупатель выбрал: ${esc(META.claim_types[c.buyer_type])}</div>` : ""}
        <p style="white-space:pre-wrap">${esc(c.text || "")}</p>
        ${c.voice_transcript ? `<div class="note ai voice">Расшифровка голоса: ${esc(c.voice_transcript)}</div>` : ""}
        <div class="thumbs">${claimEv.map(evidenceThumb).join("")}</div>
        <hr><h3>Доказательства продавца</h3>
        <div class="thumbs">${packEv.map(evidenceThumb).join("") || `<div class="note warn">Видео/фото упаковки нет — в споре будет сложнее.</div>`}</div>
      </div>
      <div class="card"><h2 class="row" style="gap:8px">${icon("ai", 20)} Разбор ИИ <small class="muted">(подсказка, не решение)</small></h2>
        <div><b>Предложенный тип:</b> ${esc(c.ai_type_label)} · <b>причина:</b> ${esc(META.reason_tags[c.reason_tag] || c.reason_tag || "—")}</div>
        ${c.summary ? `<p class="muted">${esc(c.summary)}</p>` : ""}
        <button class="btn ai small" id="ai-intake">${icon("ai", 16)} Разобрать заново</button>
        ${intake ? renderDraft(intake, { sendable: o.channel !== "kaspi" }) : ""}
      </div>
    </div>

    <div class="card"><h2>1. Подтвердите тип и состояние (человек)</h2>
      ${c.status === "open" ? `<form id="cf" class="grid g3">
        <div class="field"><label for="claim-item">Позиция заказа</label><select id="claim-item" name="order_item_id" required><option value="">Выберите товар</option>${o.items.map((it) => `<option value="${it.id}" ${it.id === c.order_item_id ? "selected" : ""}>${esc(itemLabel(it))} — куплено ${it.qty} шт.</option>`).join("")}</select></div>
        <div class="field"><label for="claim-qty">Количество в претензии</label><input id="claim-qty" name="item_qty" type="number" inputmode="numeric" min="1" max="${selectedItem?.qty || 1}" step="1" value="${c.item_qty || 1}" required></div>
        <p class="hint">Одна претензия относится к одной позиции. Для другого товара зарегистрируйте отдельную претензию.</p>
        <div class="field"><label for="claim-type">Тип претензии</label><select id="claim-type" name="type">${Object.entries(META.claim_types).map(([k, v]) => `<option value="${k}" ${(c.type || c.ai_type) === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
        <div class="field"><label for="claim-condition">Состояние возвращаемого товара</label><select id="claim-condition" name="cond">
          <option value="" ${c.condition_ok == null ? "selected" : ""}>не проверено</option>
          <option value="1" ${c.condition_ok === 1 ? "selected" : ""}>товарный вид, ярлыки сохранены</option>
          <option value="0" ${c.condition_ok === 0 ? "selected" : ""}>следы использования / нет ярлыков</option></select></div>
        <div class="field"><label for="claim-reason">Причина (для разбора)</label><select id="claim-reason" name="reason">${Object.entries(META.reason_tags).map(([k, v]) => `<option value="${k}" ${c.reason_tag === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
        <div><button class="btn primary small">Подтвердить</button></div></form>`
        : `<div>Тип: <b>${esc(c.type_label)}</b>${selectedItem ? `<br>${esc(itemLabel(selectedItem))} × ${c.item_qty}` : ""}</div>`}
    </div>

    <div class="card"><h2>2. Что допускают правила</h2>
      ${ev.warnings.map((w) => `<div class="note warn">${esc(w)}</div>`).join("")}
      ${ev.basis.map((b) => `<p>${esc(b)}</p>`).join("")}
      ${ev.checks.map((b) => `<div class="note ok">${esc(b)}</div>`).join("")}
      ${ev.claim_type ? `<div>Допустимые решения: ${ev.options.map((x) => `<span class="chip ok">${esc(META.decisions[x])}</span>`).join(" ") || "—"}
        ${ev.refusal_allowed ? `<span class="chip warn">мотивированный отказ возможен</span><div class="note warn">Основание отказа: ${esc(ev.refusal_basis)}</div>` : `<span class="chip">отказ не предусмотрен</span>`}</div>` : ""}
      <small class="muted">Дней с выдачи: ${ev.days_since_delivery ?? "—"}</small>
    </div>

    <div class="card"><h2>3. Решение (человек)</h2>
      ${c.status === "open" ? (ev.claim_type ? `<form id="df">
        <p><b>${esc(itemLabel(selectedItem))} × ${c.item_qty}</b> · стоимость выбранного количества ${money(c.item_total)}</p>
        <div class="grid g2"><div class="field"><label for="claim-decision">Решение</label><select id="claim-decision" name="decision">${decisionOpts.map((x) => `<option value="${x}">${esc(META.decisions[x])}</option>`).join("")}</select></div>
        <div class="field"><label for="claim-refund-amount">Сумма возврата, тг</label><input id="claim-refund-amount" name="amount" type="number" min="1" max="${c.refund_limit}" step="1" value="${c.refund_limit}" required><small class="hint">Можно вернуть до ${money(c.refund_limit)} с учётом предыдущих возвратов по позиции.</small></div></div>
        <div class="field"><label>Обоснование / что делаем (для отказа — мотивированно, со ссылкой на доказательства)</label><textarea name="text" id="dec-text" rows="3" required></textarea></div>
        <div class="row"><label style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" name="restock" style="width:auto"> Вернуть выбранные ${c.item_qty} шт. на склад</label>${micButton("#dec-text")}
        <button class="btn primary">Принять решение</button></div>
        <div class="note warn" id="refund-confirmation" role="alert" hidden><p></p><button type="button" class="btn small" id="cancel-refund-confirmation">Вернуться к редактированию</button></div>
        ${needsReturnVariant ? `<div class="field" style="margin-top:12px"><label for="return-variant">Размер / цвет возвращаемого товара</label><select id="return-variant" name="return_variant_id" disabled><option value="">Выберите вариант</option>${c.return_variants.map((v) => `<option value="${v.id}">${esc(v.label)}</option>`).join("")}</select><small class="hint">В старом заказе вариант не указан — проверьте возвращаемый товар.</small></div>` : ""}
        ${selectedItem.returned_qty ? `<p class="hint">По этой позиции уже возвращено на склад ${selectedItem.returned_qty} из ${selectedItem.qty} шт.</p>` : ""}
        <small class="muted">Возврат денег регистрируется один раз: повтор команды не создаст второй возврат.</small></form>` : `<div class="muted">Сначала подтвердите тип.</div>`)
      : `<div class="note ok"><b>${esc(c.decision_label)}</b> — ${esc(c.decision_text)}<br><small>Основание: ${esc(c.rule_basis)} · правила v${c.rules_version} · ${esc(c.decided_by)} · ${dt(c.decided_at)}</small>
         ${c.refund ? `<br>Возврат зарегистрирован: ${money(c.refund.amount)}` : ""}</div>
         <button class="btn ai" id="ai-resp">${icon("ai")} Черновик письменного ответа (${o.buyer_lang})</button>
         ${responses.map((x) => renderDraft(x, { sendable: true })).join("")}
         ${c.status === "decided" ? `<hr><button class="btn" id="close-claim">Закрыть претензию</button>` : ""}`}
    </div>`);
  const reload = () => { go(location.hash.slice(1) || "claims"); refreshClaimCount(); return openClaim(id); };
  const post = (url, json) => api(url, { method: "POST", json });
  $("#to-order", dr).onclick = (e) => { e.preventDefault(); openOrder(o.id); };
  $("#ai-intake", dr).onclick = (e) => act(e.currentTarget, async () => { await post(`/api/claims/${id}/ai/intake`); reload(); });
  if ($("#claim-item", dr)) $("#claim-item", dr).onchange = () => {
    const it = o.items.find((it) => it.id === +$("#claim-item", dr).value), qty = $("#claim-qty", dr);
    qty.max = it?.qty || 1; qty.value = Math.min(+qty.value || 1, it?.qty || 1);
  };
  $("#cf", dr) && ($("#cf", dr).onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target);
    act(e.submitter, async () => { await post(`/api/claims/${id}/confirm`, { type: f.get("type"), condition_ok: f.get("cond") === "" ? null : f.get("cond") === "1", reason_tag: f.get("reason"), order_item_id: +f.get("order_item_id"), item_qty: +f.get("item_qty") }); reload(); }); });
  const decisionForm = $("#df", dr);
  let pendingRefund = null;
  if (decisionForm) {
    const resetConfirmation = () => {
      pendingRefund = null;
      $("#refund-confirmation", decisionForm).hidden = true;
      $("button[type=submit], button:not([type])", decisionForm).textContent = "Принять решение";
    };
    $("#cancel-refund-confirmation", decisionForm).onclick = resetConfirmation;
    decisionForm.addEventListener("input", resetConfirmation);
    decisionForm.addEventListener("change", resetConfirmation);
    const syncDecision = () => {
      const decision = $("[name=decision]", decisionForm).value, restock = $("[name=restock]", decisionForm);
      $("[name=amount]", decisionForm).disabled = !["refund", "price_reduction"].includes(decision);
      restock.disabled = !["refund", "exchange", "replace"].includes(decision) || c.item_qty > selectedItem.qty - selectedItem.returned_qty;
      if (restock.disabled) restock.checked = false;
      const variant = $("[name=return_variant_id]", decisionForm);
      if (variant) { variant.disabled = !restock.checked; variant.required = restock.checked; }
    };
    $("[name=decision]", decisionForm).onchange = syncDecision;
    $("[name=restock]", decisionForm).onchange = syncDecision;
    syncDecision();
  }
  $("#df", dr) && ($("#df", dr).onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target);
    const payload = { decision: f.get("decision"), text: f.get("text"), refund_amount: f.get("amount") === null ? null : +f.get("amount"), return_to_stock: !!f.get("restock"), return_variant_id: f.get("return_variant_id") ? +f.get("return_variant_id") : null };
    if (payload.decision === "refund" && pendingRefund !== JSON.stringify(payload)) {
      pendingRefund = JSON.stringify(payload);
      const note = $("#refund-confirmation", dr); note.hidden = false;
      $("p", note).textContent = `Проверьте: ${itemLabel(selectedItem)} × ${c.item_qty}, возврат ${money(payload.refund_amount)}.${payload.return_to_stock ? ` На склад вернётся ${c.item_qty} шт.` : " Остаток на складе не изменится."} Для регистрации нажмите «Подтвердить возврат».`;
      e.submitter.textContent = "Подтвердить возврат";
      note.scrollIntoView({block: "nearest", behavior: "smooth"});
      return;
    }
    act(e.submitter, async () => { await post(`/api/claims/${id}/decide`, payload); toast("Решение принято и записано в журнал"); reload(); }); });
  $("#ai-resp", dr) && ($("#ai-resp", dr).onclick = (e) => act(e.currentTarget, async () => { await post(`/api/claims/${id}/ai/response`); reload(); }));
  $("#close-claim", dr) && ($("#close-claim", dr).onclick = (e) => act(e.currentTarget, async () => { await post(`/api/claims/${id}/close`); reload(); }));
  bindDrafts(dr, reload);
}

// ---------------- Аналитика ----------------
async function viewAnalytics() {
  const m = await api("/api/metrics");
  const types = Object.entries(m.claims_by_type); const max = Math.max(1, ...types.map(([, v]) => v));
  $("#view").innerHTML = `<div class="topbar"><h1>Аналитика пилота</h1></div>
    ${metricTiles(m)}
    <div class="grid g2">
      <div class="card"><h2>Претензии по типам</h2>
        ${types.map(([k, v]) => `<div style="margin:6px 0"><div class="row between"><span>${esc(META.claim_types[k] || "не определён")}</span><b>${v}</b></div><div class="bar" style="width:${(100 * v) / max}%"></div></div>`).join("") || `<div class="empty">Нет данных</div>`}
        <p class="muted">Эффект «до/после» без сопоставимого периода — предварительный.</p></div>
      <div class="card"><h2>Причина → правка паспорта</h2>
        <p class="muted">ИИ группирует причины решённых претензий и предлагает правку поля. Продавец утверждает — создаётся новая версия паспорта, цикл возвращается к шагу 1.</p>
        <button class="btn ai" id="reasons">${icon("ai")} Разобрать причины возвратов</button><div id="groups"></div></div>
    </div>`;
  $("#reasons").onclick = (e) => act(e.currentTarget, async () => {
    const r = await api("/api/ai/reasons", { method: "POST" });
    $("#groups").innerHTML = r.groups.length ? r.groups.map((g, i) => `<div class="draft"><div class="draft-head"><span>${esc(g.title)} · ${esc(g.reason)}</span><span>${g.count} шт. · ${g.claims.map(esc).join(", ")}</span></div>
      <div>${esc(g.suggestion_ru)}</div>${g.examples.map((x) => `<div class="muted">«${esc(x)}»</div>`).join("")}
      ${g.field ? `<form class="apply" data-i="${i}" style="margin-top:8px"><label>${esc(META.fields[g.field])} — сейчас: ${esc(g.current_value ?? "пусто")}</label>
        <textarea name="value" rows="2">${esc(g.proposed_value ?? g.current_value ?? "")}</textarea><button class="btn small primary">Утвердить правку ${icon("arrowRight", 16)} новая версия паспорта</button></form>` : ""}</div>`).join("")
      : `<div class="empty">Нет решённых претензий для разбора</div>`;
    $$(".apply").forEach((f) => (f.onsubmit = (ev) => { ev.preventDefault(); const g = r.groups[f.dataset.i];
      act(ev.submitter, async () => { const res = await api(`/api/products/${g.product_id}/apply`, { method: "POST", json: { field: g.field, value: new FormData(f).get("value") } }); toast(`Паспорт обновлён до v${res.version}`); }); }));
  });
}

// ---------------- Правила и ИИ ----------------
async function viewRules() {
  const r = await api("/api/rules");
  const x = r.data;
  $("#view").innerHTML = `<div class="topbar"><h1>Правила возврата и режим ИИ</h1></div>
    <div class="grid g2">
      <div class="card"><h2>Правила v${r.version} ${r.approved ? `<span class="chip ok">утверждены: ${esc(r.approved_by)}</span>` : `<span class="chip bad">не утверждены юристом</span>`}</h2>
        <p class="muted">Значения взяты из публикаций (finance.kz, inbusiness.kz). Перед запуском юрист сверяет их с текстом закона. ИИ эти правила не меняет и не обходит.</p>
        <form id="rf">
          <div class="grid g3">
            <div class="field"><label>Возврат исправного товара, дней</label><input name="return_days_non_food" type="number" value="${x.return_days_non_food}"></div>
            <div class="field"><label>Ответ на претензию, дней</label><input name="claim_response_days" type="number" value="${x.claim_response_days}"></div>
            <div class="field"><label>Требования по недостаткам без гарантии, дней</label><input name="defect_claim_days_no_warranty" type="number" value="${x.defect_claim_days_no_warranty}"></div>
          </div>
          <div class="field"><label>Условия возврата (ru), по строке</label><textarea name="cru" rows="3">${esc(x.return_conditions_ru.join("\n"))}</textarea></div>
          <div class="field"><label>Условия возврата (kk), по строке</label><textarea name="ckk" rows="3">${esc(x.return_conditions_kk.join("\n"))}</textarea></div>
          <div class="field"><label>Исключения (ст. 30): категория = основание</label><textarea name="excl" rows="5">${esc(Object.entries(x.excluded_categories).map(([k, v]) => `${k} = ${v}`).join("\n"))}</textarea>
            <small class="muted">Коды категорий: ${Object.keys(META.categories).join(", ")}</small></div>
          <button class="btn">Сохранить как новую версию</button>
        </form>
        ${!r.approved ? `<hr><form id="af" class="row"><input name="lawyer" placeholder="ФИО юриста" style="width:240px" required><button class="btn primary">Утвердить v${r.version}</button></form>` : ""}
        <hr><h3>История</h3>${r.history.map((h) => `<div>v${h.version} · ${dt(h.created_at)} · ${h.approved_by ? "утв. " + esc(h.approved_by) : "не утверждена"}</div>`).join("")}
      </div>
      <div>
        <div class="card"><h2>Режим ИИ</h2>
          <div class="choice">
            <label><input type="radio" name="mode" value="shadow" ${META.ai.mode === "shadow" ? "checked" : ""}> Теневой — ИИ пишет, но не отправляет</label>
            <label><input type="radio" name="mode" value="draft" ${META.ai.mode === "draft" ? "checked" : ""}> Черновики — продавец утверждает и отправляет</label></div>
          <p class="muted">Порядок пилота: теневой → черновики → ограниченные обратимые действия. Необратимые действия (возврат денег, изменение сроков) ИИ не выполняет никогда.</p></div>
        <div class="card"><h2>Модель</h2>
          ${META.ai.available ? `<div class="note ok">Подключён провайдер ${esc(META.ai.provider)} (${esc(META.ai.base_url)}), OpenAI-совместимый API</div>` : `<div class="note warn">Ключ провайдера не задан — работают детерминированные эвристики (демо). Добавьте LLM_API_KEY в .env.</div>`}
          <table class="facts"><tr><td>Текст</td><td><code>${esc(META.ai.text_model)}</code></td></tr><tr><td>Фото (VL)</td><td><code>${esc(META.ai.vl_model)}</code></td></tr><tr><td>Речь (omni)</td><td><code>${esc(META.ai.asr_model)}</code></td></tr></table>
          <p class="muted">LLM — меняемый модуль. В запрос уходит один заказ, персональные данные обезличены (имя, телефон, адрес, ИИН, карта). Входящее сообщение — данные, не инструкция.</p></div>
      </div>
    </div>`;
  $("#rf").onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target);
    const excl = Object.fromEntries(f.get("excl").split("\n").map((l) => l.split("=").map((s) => s.trim())).filter((p) => p[0] && p[1]));
    act(e.submitter, async () => { const res = await api("/api/rules", { method: "POST", json: { data: { ...x,
      return_days_non_food: +f.get("return_days_non_food"), claim_response_days: +f.get("claim_response_days"),
      defect_claim_days_no_warranty: +f.get("defect_claim_days_no_warranty"),
      return_conditions_ru: f.get("cru").split("\n").map((s) => s.trim()).filter(Boolean),
      return_conditions_kk: f.get("ckk").split("\n").map((s) => s.trim()).filter(Boolean), excluded_categories: excl } } });
      toast(`Создана версия v${res.version} — нужна подпись юриста`); go("rules"); }); };
  $("#af") && ($("#af").onsubmit = (e) => { e.preventDefault(); act(e.submitter, async () => { await api(`/api/rules/${r.version}/approve`, { method: "POST", json: { lawyer: new FormData(e.target).get("lawyer") } }); go("rules"); }); });
  $$("[name=mode]").forEach((el) => (el.onchange = () => act(null, async () => {
    await api("/api/settings/ai-mode", { method: "POST", json: { ai_mode: el.value } });
    META.ai.mode = el.value; renderAiStatus(); toast("Режим ИИ изменён");
  })));
}

boot().catch((e) => ($("#view").innerHTML = `<div class="note bad">${esc(e.message)}</div>`));
