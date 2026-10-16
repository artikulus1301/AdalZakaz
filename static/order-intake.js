// Разбор ничего не резервирует. Заказ появляется только после проверки формы продавцом.
function openOrderIntake() {
  const speech = META.speech?.available;
  const localSpeech = META.speech?.provider === "local";
  const dr = openDrawer(`<div class="chip">Помощник продавца</div><h1>Заказ из сообщения</h1>
    <p class="muted">Вставьте сообщение покупателя или расшифруйте голосовое. Найдём товар и подготовим черновик для проверки.</p>
    <div class="card">
      <div class="field"><label for="intake-channel">Откуда пришёл заказ</label><select id="intake-channel">${Object.entries(META.channels).filter(([key]) => key !== "kaspi").map(([key, name]) => `<option value="${key}" ${key === "whatsapp" ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></div>
      <div class="field"><label for="intake-text">Сообщение покупателя</label><textarea id="intake-text" rows="5" maxlength="6000" placeholder="Нужны белые кроссовки 42 размера, доставка завтра"></textarea><small class="hint">Русский и қазақша. Имя, телефон и адрес подставим, если они указаны.</small></div>
      <div class="row"><button type="button" class="btn small" id="intake-example">Попробовать пример</button><button type="button" class="btn small" id="intake-record" ${speech ? "" : "disabled"}>${icon("mic", 16)} Записать голос</button></div>
      <div class="field" style="margin-top:16px"><label for="intake-file">Или загрузите голосовое · до 2 минут, 10 МБ</label><input type="file" id="intake-file" accept="audio/*,.ogg,.opus,.m4a,.wav,.mp3,.webm" ${speech ? "" : "disabled"}></div>
      <p class="hint">${speech ? localSpeech ? "Голос распознаётся локально на сервере. Расшифровку можно исправить перед подтверждением." : "Голос отправится в подключённую модель речи. Расшифровку можно исправить перед подтверждением." : "Для голоса нужно установить модуль распознавания на сервере. Текстовый разбор уже доступен."}</p>
      <div id="intake-status" role="status" aria-live="polite"></div>
      <div id="intake-error" class="note warn" role="alert" hidden></div>
      <button type="button" class="btn primary" id="intake-parse">Разобрать сообщение</button>
    </div><div id="intake-preview" aria-live="polite"></div>`);
  const text = $("#intake-text", dr), channel = $("#intake-channel", dr), preview = $("#intake-preview", dr);
  const status = $("#intake-status", dr), error = $("#intake-error", dr), recordButton = $("#intake-record", dr);
  const controller = new AbortController();
  let recorder = null, timer = null, busy = false, closed = false;
  drawerCleanup = () => { closed = true; controller.abort(); clearTimeout(timer); recorder?.cancel(); };
  const fail = (e) => { if (!closed && e.name !== "AbortError") { error.hidden = false; error.textContent = e.message; } };
  const lock = (value, label = "") => {
    busy = value; status.textContent = label;
    ["#intake-parse", "#intake-example", "#intake-file", "#intake-channel", "#intake-text"].forEach((s) => { $(s, dr).disabled = value || (s === "#intake-file" && !speech); });
    recordButton.disabled = value || !speech;
  };
  function render(result) {
    const source = result.source === "llm" ? "Разбор моделью" : result.source === "fallback" ? "Локальный разбор · модель недоступна или ответ не прошёл проверку" : "Локальный разбор · без подключения модели";
    preview.innerHTML = `<h2>Черновик готов</h2><p class="hint">${source}. Заказ ещё не создан, остаток не зарезервирован.</p>
      ${result.items.map((row) => `<div class="card intake-result"><div class="row between"><h3>${esc(row.title || "Нужно выбрать товар")}</h3><span class="chip ${row.available && row.stock_scope !== "common" ? "ok" : "warn"}">${row.available ? row.stock_scope === "common" ? "Наличие размера уточнить" : "Есть в наличии" : "Нужна проверка"}</span></div>
        <p class="intake-source muted">«${esc(row.request)}»</p><div class="row"><b>${row.qty} шт.</b>${row.variant ? `<span>Размер / цвет: ${esc(row.variant)}</span>` : ""}${row.price != null ? `<b>${money(row.price)}</b>` : ""}${row.free != null ? `<span class="muted">Доступно: ${row.free}</span>` : ""}</div>
        ${[...row.errors, ...row.warnings].map((x) => `<p class="hint">${esc(x)}</p>`).join("")}
        ${!row.sku && row.candidates.length ? `<p class="hint">Возможные товары: ${row.candidates.map((p) => `${esc(p.title)} (${esc(p.sku)})`).join(", ")}</p>` : ""}</div>`).join("")}
      <div class="card"><b>Получение заказа</b><p>${esc(result.delivery_window || "День / время нужно уточнить")} · ${esc(result.delivery_zone || "Город / зону нужно уточнить")}</p><p class="hint">Пожелание покупателя, срок ещё не согласован.</p>${result.buyer.name || result.buyer.phone || result.buyer.address ? `<p>${[result.buyer.name, result.buyer.phone, result.buyer.address].filter(Boolean).map(esc).join(" · ")}</p>` : ""}</div>
      ${result.missing.length ? `<div class="note warn"><div><b>Уточните у покупателя</b><p>${result.missing.map(esc).join(" · ")}</p><small>Имя обязательно. Остальные сведения можно дополнить позже.</small></div></div>` : ""}
      ${result.warnings.map((x) => `<p class="hint">${esc(x)}</p>`).join("")}
      <div class="form-actions"><button class="btn primary" id="intake-review">Проверить и дополнить</button></div>`;
    $("#intake-review", dr).onclick = () => newOrderForm({ ...result, source_text: text.value }).catch(fail);
  }
  function invalidate() { preview.innerHTML = ""; error.hidden = true; }
  text.oninput = invalidate; channel.onchange = invalidate;
  $("#intake-example", dr).onclick = () => { text.value = "Нужны белые кроссовки 42 размера, доставка завтра"; invalidate(); text.focus(); };
  $("#intake-parse", dr).onclick = async () => {
    if (busy) return;
    invalidate();
    if (!text.value.trim()) { fail(new Error("Вставьте сообщение покупателя")); text.focus(); return; }
    lock(true, "Ищем товары, размеры и проверяем остатки…");
    try { const result = await api("/api/ai/order-intake", { method: "POST", json: { text: text.value, channel: channel.value }, signal: controller.signal }); if (!closed) render(result); }
    catch (e) { fail(e); } finally { if (!closed) lock(false); }
  };
  async function recognize(blob) {
    invalidate(); lock(true, "Распознаём голос и разбираем заказ…");
    try {
      const wav = await VoiceRecorder.toWav(blob, 120);
      if (closed) return;
      const fd = new FormData(); fd.append("file", wav, "order.wav"); fd.append("channel", channel.value);
      const result = await api("/api/ai/order-intake/voice", { method: "POST", body: fd, signal: controller.signal });
      if (!closed) { text.value = result.transcript; render(result); }
    } catch (e) { fail(e); } finally { if (!closed) lock(false); }
  }
  $("#intake-file", dr).onchange = async (e) => {
    const file = e.target.files[0]; if (!file || busy) return;
    if (file.size > 10 * 1024 * 1024) { fail(new Error("Выберите файл до 10 МБ")); e.target.value = ""; return; }
    await recognize(file); if (!closed) e.target.value = "";
  };
  async function stopRecording() {
    if (!recorder) return;
    clearTimeout(timer); const active = recorder; recorder = null;
    recordButton.textContent = "Записать голос";
    lock(true, "Готовим запись…");
    try { const audio = await active.stop(); if (!closed) await recognize(audio); }
    catch (e) { fail(e); } finally { if (!closed) lock(false); }
  }
  recordButton.onclick = async () => {
    if (recorder) { await stopRecording(); return; }
    if (busy) return;
    invalidate(); lock(true, "Ожидаем доступ к микрофону…");
    try {
      const active = await VoiceRecorder.start();
      if (closed) { active.cancel(); return; }
      recorder = active; recordButton.disabled = false; recordButton.textContent = "Остановить и распознать";
      status.textContent = "Идёт запись · нажмите остановить, максимум 2 минуты";
      timer = setTimeout(stopRecording, 119000);
    } catch (e) { fail(e); if (!closed) lock(false); }
  };
  text.focus();
}
