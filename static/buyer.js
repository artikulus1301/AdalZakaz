// Общие функции страниц покупателя: язык kk/ru, словарь, безопасный вывод.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const money = (n) => (n ?? 0).toLocaleString("ru-RU") + " ₸";

const T = {
  ru: {
    order: "Заказ", status: "Статус", total: "Итого", bought: "Что вы купили", version: "версия описания",
    returns: "Возврат и обмен", returnsText: (d) => `Исправный товар можно вернуть или обменять в течение ${d} календарных дней, если:`,
    defects: "Если товар с браком или не соответствует описанию, вы вправе потребовать: устранить недостаток, уменьшить цену, заменить товар или вернуть деньги.",
    excluded: "Этот товар относится к категории, которую по закону нельзя вернуть, если он надлежащего качества. При браке или несоответствии описанию ваши права сохраняются.",
    answerIn: (d) => `Продавец обязан ответить на письменную претензию в течение ${d} календарных дней.`,
    escalate: "Если не согласны с ответом:", claims: "Ваши претензии", claimNo: "Претензия", answerUntil: "ответ до",
    claimStatus: { open: "рассматривается", decided: "решение принято", closed: "закрыта" },
    newClaim: "Подать претензию", whatHappened: "Что случилось?", describe: "Опишите проблему",
    describePh: "Например: размер меньше, чем в таблице; цвет отличается от фото…", photos: "Фото или видео товара (до 5)",
    voice: "Или расскажите голосом", rec: "🎤 Записать", stop: "■ Остановить", recorded: "Голос записан", del: "Удалить",
    send: "Отправить претензию", sent: (n, d) => `Претензия ${n} принята. Продавец ответит письменно до ${d}.`,
    notYet: "Претензию можно подать после передачи товара в доставку.",
    ask: "Задать вопрос продавцу", askPh: "Ваш вопрос", askSend: "Отправить", asked: "Вопрос отправлен продавцу",
    chat: "Переписка", seller: "Продавец", you: "Вы", response: "Ответ продавца",
    facts: { brand: "Бренд", country: "Страна производства", composition: "Состав", dimensions: "Размеры", weight: "Вес", color: "Цвет",
      size_grid: "Размерная сетка", variants: "Варианты", model: "Модель", warranty_months: "Гарантия, мес.", package_contents: "Комплектация", price: "Цена" },
    variant: "Вариант", notFound: "Заказ не найден", inStock: "В наличии", outStock: "Нет в наличии",
    passport: "Паспорт товара", updated: "Обновлено", sellerLbl: "Продавец",
  },
  kk: {
    order: "Тапсырыс", status: "Мәртебе", total: "Барлығы", bought: "Сіз не сатып алдыңыз", version: "сипаттама нұсқасы",
    returns: "Қайтару және айырбастау", returnsText: (d) => `Ақаусыз тауарды ${d} күнтізбелік күн ішінде қайтаруға немесе айырбастауға болады, егер:`,
    defects: "Тауар ақаулы болса немесе сипаттамаға сәйкес келмесе, сіз талап ете аласыз: ақауды жою, бағасын төмендету, тауарды ауыстыру немесе ақшаны қайтару.",
    excluded: "Бұл тауар заң бойынша сапасы тиісті болса қайтарылмайтын санатқа жатады. Ақау немесе сипаттамаға сәйкессіздік болса, құқықтарыңыз сақталады.",
    answerIn: (d) => `Сатушы жазбаша шағымға ${d} күнтізбелік күн ішінде жауап беруге міндетті.`,
    escalate: "Жауаппен келіспесеңіз:", claims: "Сіздің шағымдарыңыз", claimNo: "Шағым", answerUntil: "жауап мерзімі",
    claimStatus: { open: "қаралуда", decided: "шешім қабылданды", closed: "жабылды" },
    newClaim: "Шағым беру", whatHappened: "Не болды?", describe: "Мәселені сипаттаңыз",
    describePh: "Мысалы: өлшемі кестедегіден кіші; түсі суреттегіден басқа…", photos: "Тауардың фото не бейнесі (5-ке дейін)",
    voice: "Немесе дауыспен айтыңыз", rec: "🎤 Жазу", stop: "■ Тоқтату", recorded: "Дауыс жазылды", del: "Жою",
    send: "Шағымды жіберу", sent: (n, d) => `${n} шағымы қабылданды. Сатушы ${d} дейін жазбаша жауап береді.`,
    notYet: "Шағымды тауар жеткізуге берілгеннен кейін беруге болады.",
    ask: "Сатушыға сұрақ қою", askPh: "Сұрағыңыз", askSend: "Жіберу", asked: "Сұрақ сатушыға жіберілді",
    chat: "Хат алмасу", seller: "Сатушы", you: "Сіз", response: "Сатушының жауабы",
    facts: { brand: "Бренд", country: "Өндіруші ел", composition: "Құрамы", dimensions: "Өлшемдері", weight: "Салмағы", color: "Түсі",
      size_grid: "Өлшем торы", variants: "Нұсқалар", model: "Моделі", warranty_months: "Кепілдік, ай", package_contents: "Жиынтығы", price: "Бағасы" },
    variant: "Нұсқа", notFound: "Тапсырыс табылмады", inStock: "Қоймада бар", outStock: "Қоймада жоқ",
    passport: "Тауар паспорты", updated: "Жаңартылды", sellerLbl: "Сатушы",
  },
};

let LANG = "ru";
function initLang(def, onChange) {
  try { LANG = localStorage.getItem("adal_lang") || def || "ru"; } catch { LANG = def || "ru"; }
  const box = $("#langs");
  box.innerHTML = `<button data-l="kk">Қаз</button><button data-l="ru">Рус</button>`;
  const paint = () => $$("button", box).forEach((b) => b.classList.toggle("on", b.dataset.l === LANG));
  paint();
  box.onclick = (e) => { const l = e.target.dataset?.l; if (!l) return; LANG = l; try { localStorage.setItem("adal_lang", l); } catch {} paint(); document.documentElement.lang = l; onChange(); };
  document.documentElement.lang = LANG;
}
const t = () => T[LANG];

function factsTable(p) {
  const rows = Object.entries(t().facts).filter(([k]) => p[k] != null && p[k] !== "")
    .map(([k, label]) => `<tr><td>${label}</td><td>${k === "price" ? money(p[k]) : esc(p[k])}</td></tr>`).join("");
  return `<table class="facts">${rows}</table>`;
}
function returnsBlock(rules, excluded) {
  return `<h2>${t().returns}</h2>
    ${excluded ? `<div class="note warn">${t().excluded}</div>` : `<p>${t().returnsText(rules.return_days)}</p><ul>${rules.conditions[LANG].map((c) => `<li>${esc(c)}</li>`).join("")}</ul>`}
    <p>${t().defects}</p><p>${t().answerIn(rules.claim_response_days)}</p>
    <p class="muted">${t().escalate} ${esc(rules.escalation[LANG])}</p>`;
}
