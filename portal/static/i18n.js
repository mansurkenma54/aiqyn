/* ============================================================
   AIQYN — екі тіл: орысша (әдепкі) және қазақша
   ------------------------------------------------------------
   Бастапқы мәтін шаблондар мен app.js ішінде қазақша жазылған.
   Орысша нұсқа сөздік (i18n-ru.js) арқылы беттегі мәтін
   түйіндеріне және title/placeholder/aria-label атрибуттарына
   қолданылады. Кейін пайда болатын мәтінді (тізім, карточка,
   хабарлама) MutationObserver аударады — app.js-ті әр жолға
   бөлшектеп жазудың қажеті жоқ.

   Сөздікте жоқ мәтін қазақша қалады — бұл әдейі: аударылмаған
   жол бірден көзге түседі де, сөздікке қосылады.
   ============================================================ */
(function () {
  'use strict';

  var KEY = 'aiqyn_lang';
  var lang = 'ru';
  try { if (localStorage.getItem(KEY) === 'kk') lang = 'kk'; } catch (e) { /* жеке режим */ }

  window.AIQYN_LANG = lang;
  document.documentElement.lang = lang === 'ru' ? 'ru' : 'kk';

  window.setAiqynLang = function (next) {
    try { localStorage.setItem(KEY, next === 'kk' ? 'kk' : 'ru'); } catch (e) { /* ignore */ }
    location.reload();
  };

  /* Орыс тіліндегі көпше түр: 1 событие, 2 события, 5 событий */
  function plural(n, one, few, many) {
    var a = Math.abs(n) % 100;
    var b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }
  window.ruPlural = plural;

  /* ---------------- Ауыстырғыш ---------------- */
  var css = [
    '.lang-switch{display:inline-flex;align-items:center;gap:2px;padding:3px;border-radius:999px;',
    'border:1px solid rgba(127,127,127,.35);font:700 11px/1 Inter,system-ui,sans-serif;letter-spacing:.04em}',
    '.lang-switch button{min-width:30px;height:24px;padding:0 8px;border:0;border-radius:999px;cursor:pointer;',
    'background:transparent;color:inherit;opacity:.62;font:inherit;transition:background .15s,opacity .15s}',
    '.lang-switch button:hover{opacity:1}',
    '.lang-switch button.on{opacity:1;background:rgba(127,127,127,.26)}',
    '.lang-switch button:focus-visible{outline:2px solid currentColor;outline-offset:1px}',
  ].join('');

  function mountSwitches() {
    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
    document.querySelectorAll('[data-lang-switch]').forEach(function (slot) {
      slot.classList.add('lang-switch');
      slot.setAttribute('role', 'group');
      slot.setAttribute('aria-label', lang === 'ru' ? 'Язык интерфейса' : 'Интерфейс тілі');
      slot.innerHTML = '';
      [['ru', 'RU', 'Русский'], ['kk', 'KZ', 'Қазақша']].forEach(function (item) {
        var button = document.createElement('button');
        button.type = 'button';
        button.textContent = item[1];
        button.title = item[2];
        button.setAttribute('data-i18n-skip', '');
        button.setAttribute('aria-pressed', String(item[0] === lang));
        if (item[0] === lang) button.className = 'on';
        button.onclick = function () { if (item[0] !== lang) window.setAiqynLang(item[0]); };
        slot.appendChild(button);
      });
    });
  }

  if (lang !== 'ru') {
    document.addEventListener('DOMContentLoaded', mountSwitches);
    return;
  }

  /* ---------------- Аудару ---------------- */
  var DICT = window.AIQYN_RU || {};

  function exact(text) {
    return Object.prototype.hasOwnProperty.call(DICT, text) ? DICT[text] : null;
  }

  // Санмен келетін мәтін: «12 оқиға», «3 мин бұрын», «ЖИ 67%»…
  var RULES = [
    [/^(\d+) оқиға$/, function (m) { var n = +m[1]; return n + ' ' + plural(n, 'событие', 'события', 'событий'); }],
    [/^(\d+) қаралды$/, function (m) { return 'проверено: ' + m[1]; }],
    [/^(\d+) учаске$/, function (m) { var n = +m[1]; return n + ' ' + plural(n, 'участок', 'участка', 'участков'); }],
    [/^(\d+) күн$/, function (m) { var n = +m[1]; return n + ' ' + plural(n, 'день', 'дня', 'дней'); }],
    [/^ЖИ (\d+)%$/, function (m) { return 'ИИ ' + m[1] + '%'; }],
    [/^ЖИ растады · (\d+)%$/, function (m) { return 'ИИ подтвердил · ' + m[1] + '%'; }],
    [/^ЖИ күмәнданды · (\d+)%$/, function (m) { return 'ИИ сомневается · ' + m[1] + '%'; }],
    [/^дәл қазір$/, function () { return 'только что'; }],
    [/^(\d+) мин бұрын$/, function (m) { return m[1] + ' мин назад'; }],
    [/^(\d+) сағ бұрын$/, function (m) { return m[1] + ' ч назад'; }],
    [/^(\d+) күн бұрын$/, function (m) { var n = +m[1]; return n + ' ' + plural(n, 'день', 'дня', 'дней') + ' назад'; }],
    [/^Видео · (\d+) сек$/, function (m) { return 'Видео · ' + m[1] + ' с'; }],
    [/^Осы координатада (\d+) оқиға$/, function (m) { var n = +m[1]; return 'В этой точке ' + n + ' ' + plural(n, 'событие', 'события', 'событий'); }],
    [/^(\d+) өтінім 72 сағаттан асты$/, function (m) { var n = +m[1]; return n + ' ' + plural(n, 'заявка ждёт', 'заявки ждут', 'заявок ждут') + ' дольше 72 часов'; }],
    [/^(\d+) өтінім бойынша$/, function (m) { var n = +m[1]; return 'по ' + n + ' ' + plural(n, 'заявке', 'заявкам', 'заявкам'); }],
    [/^(\d+) нүкте · шамамен (.+)$/, function (m) { var n = +m[1]; return n + ' ' + plural(n, 'точка', 'точки', 'точек') + ' · около ' + m[2]; }],
    [/^(\d+) жаңа оқиға келді$/, function (m) { return 'Новых событий: ' + m[1]; }],
    [/^(\d+) рамка$/, function (m) { var n = +m[1]; return n + ' ' + plural(n, 'рамка', 'рамки', 'рамок'); }],
    [/^дәліз (\d+) м$/, function (m) { return 'коридор ' + m[1] + ' м'; }],
    [/^(\d+) күн ішінде$/, function (m) { var n = +m[1]; return 'в течение ' + n + ' ' + plural(n, 'дня', 'дней', 'дней'); }],
    // ЖИ өлшемі: «диаметрі ~40-50 см, тереңдігі ~5-7 см»
    [/^(?:(?:диаметрі|ені|ұзындығы|тереңдігі) ~[^,]+,? ?)+$/, function (m) {
      return m[0].replace(/диаметрі/g, 'диаметр').replace(/тереңдігі/g, 'глубина')
        .replace(/ұзындығы/g, 'длина').replace(/ені/g, 'ширина');
    }],
    [/^(\d+%) · бұл ауырлық бағасы емес$/, function (m) { return m[1] + ' · это не оценка опасности'; }],
    [/^(\d+%) · SDR \/ ЦС ГГ НИПД талабы$/, function (m) { return m[1] + ' · требование SDR / ЦС ГГ НИПД'; }],
    [/^жөндеу мерзімі ~(\d+) күн$/, function (m) { var n = +m[1]; return 'срок ремонта ~' + n + ' ' + plural(n, 'день', 'дня', 'дней'); }],
    [/^Дереккөз — жүйенің өз базасы, қолмен енгізілген сан жоқ\. Есеп (.+) жағдайына жасалды\.$/,
      function (m) { return 'Источник — собственная база системы, ручных цифр нет. Отчёт сформирован на ' + m[1] + '.'; }],
    [/^Жіберілді: (.+)$/, function (m) { return 'Отправлено: ' + tr(m[1], true); }],
    [/^Расталды · № (.+)$/, function (m) { return 'Подтверждено · № ' + m[1]; }],
    [/^Кетеді: (.+)$/, function (m) { return 'Уйдёт: ' + tr(m[1], true); }],
    [/^Таңдалды: (.+)$/, function (m) { return 'Выбрано: ' + m[1]; }],
    [/^Жаңа оқиға: (.+)$/, function (m) { return 'Новое событие: ' + tr(m[1], true); }],
    [/^Сүзу: (.+) · 109-ға жіберілді: (\d+)$/, function (m) { return 'Фильтр: ' + tr(m[1], true) + ' · отправлено в 109: ' + m[2]; }],
    [/^Аяқталуы: (.+)$/, function (m) { return 'Окончание: ' + m[1]; }],
    [/^Жауапты: (.+)$/, function (m) { return 'Ответственный: ' + m[1]; }],
    [/^Негіз: (.+)$/, function (m) { return 'Основание: ' + m[1]; }],
    [/^(.+) · кезекте тағы (\d+)$/, function (m) { return tr(m[1], true) + ' · в очереди ещё ' + m[2]; }],
    [/^(.+) · кезек бос$/, function (m) { return tr(m[1], true) + ' · очередь пуста'; }],
    [/^(.+) — оқиға кезекке қайтарылды$/, function (m) { return tr(m[1], true) + ' — событие возвращено в очередь'; }],
  ];

  /* Мекенжай (2ГИС/OSM деректері): «Шымкент, Мамен көшесі, 1» →
     «Шымкент, ул. Мамен, 1». Көше атауының өзі өзгермейді. */
  function address(text) {
    var single = /^[^,]{2,40} (көшесі|даңғылы|ауданы)$/.test(text);
    if (!single && (!/^Шымкент[, ]/.test(text) || !/(көшесі|ауданы|даңғылы|к-сі|алаңы|шағын ауданы)/.test(text))) return null;
    return text
      .replace(/([^,]+?) шағын ауданы/g, 'мкр. $1')
      .replace(/([^,]+?) көшесі/g, 'ул. $1')
      .replace(/([^,]+?) к-сі/g, 'ул. $1')
      .replace(/([^,]+?) даңғылы/g, 'пр. $1')
      .replace(/([^,]+?) алаңы/g, 'пл. $1')
      .replace(/([^,]+?) ауданы/g, '$1 район')
      .replace(/, (ул|пр|пл|мкр)\. /g, ', $1. ')
      .replace(/ ,/g, ',');
  }

  function rule(text) {
    var a = address(text);
    if (a !== null) return a;
    for (var i = 0; i < RULES.length; i++) {
      var m = text.match(RULES[i][0]);
      if (m) return RULES[i][1](m);
    }
    return null;
  }

  /* Сөздікте тұтас жоқ болса — « · » және « — » бойынша бөліп, бөліктерін
     аударамыз: «Шұңқыр · Шымкент, …» сияқты құрама жолдар үшін. */
  function segments(text) {
    var seps = [' · ', ' — '];
    for (var i = 0; i < seps.length; i++) {
      if (text.indexOf(seps[i]) < 0) continue;
      var parts = text.split(seps[i]);
      var changed = false;
      var out = parts.map(function (part) {
        var t = tr(part, false);
        if (t !== null) { changed = true; return t; }
        return part;
      });
      if (changed) return out.join(seps[i]);
    }
    return null;
  }

  // keepOriginal=true: табылмаса бастапқы мәтінді қайтарады (ереже ішінде)
  function tr(text, keepOriginal) {
    var t = text.replace(/\s+/g, ' ').trim();
    if (!t) return keepOriginal ? text : null;
    var out = exact(t);
    if (out === null) out = rule(t);
    if (out === null) out = segments(t);
    if (out === null) return keepOriginal ? text : null;
    return out;
  }
  window.aiqynTr = function (text) { return tr(text, true); };

  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1 };
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
  var CYR = /[А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]/;

  function skipped(el) {
    return !el || SKIP[el.tagName] || (el.closest && el.closest('[data-i18n-skip], .leaflet-tile-pane'));
  }

  function textNode(node) {
    var value = node.nodeValue;
    if (!value || !CYR.test(value) || skipped(node.parentElement)) return;
    var out = tr(value, false);
    if (out === null) return;
    // Бастапқы бос орындарды сақтаймыз — inline элементтердің арасы бұзылмауы үшін
    var lead = value.match(/^\s*/)[0];
    var tail = value.match(/\s*$/)[0];
    var next = lead + out + tail;
    if (next !== value) node.nodeValue = next;
  }

  function attrs(el) {
    // textarea-ның мәтінін аудармаймыз, бірақ placeholder-ін аударамыз
    if (!el || (el.closest && el.closest('[data-i18n-skip], .leaflet-tile-pane'))) return;
    for (var i = 0; i < ATTRS.length; i++) {
      var v = el.getAttribute(ATTRS[i]);
      if (!v || !CYR.test(v)) continue;
      var out = tr(v, false);
      if (out !== null && out !== v) el.setAttribute(ATTRS[i], out);
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 || skipped(root)) return;
    attrs(root);
    var tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    var node = tw.nextNode();
    while (node) {
      if (node.nodeType === 3) textNode(node); else attrs(node);
      node = tw.nextNode();
    }
  }

  function meta() {
    var title = tr(document.title, false);
    if (title) document.title = title;
    document.querySelectorAll('meta[name="description"], meta[property="og:title"], meta[property="og:description"], meta[property="og:image:alt"]')
      .forEach(function (m) {
        var out = tr(m.getAttribute('content') || '', false);
        if (out) m.setAttribute('content', out);
      });
  }

  function start() {
    mountSwitches();
    meta();
    walk(document.body);
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.type === 'characterData') textNode(r.target);
        else if (r.type === 'attributes') attrs(r.target);
        else for (var j = 0; j < r.addedNodes.length; j++) walk(r.addedNodes[j]);
      }
    }).observe(document.body, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ATTRS,
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
