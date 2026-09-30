/* AIQYN Portal — операторлық карта және өтінімдер кезегі. */

const SHYMKENT = [42.3417, 69.5901];

const STATUS_LABEL = {
  new: 'Тексеруді күтуде',
  candidate: 'Тексеруді күтуде',
  confirmed: 'Оператор тексерді',
  verified: 'Оператор тексерді',
  sent: 'ЕКЦ 109-ға жіберілді',
  submitted: 'ЕКЦ 109-ға жіберілді',
  registered: '109 жүйесінде тіркелді',
  assigned: 'Орындаушы тағайындалды',
  in_progress: 'Жұмыс жүргізілуде',
  repaired: 'Қайта тексеруді күтуде',
  resolved: 'Жойылды деп белгіленді',
  verification_pending: 'Қайта тексеруді күтуде',
  closed: 'Тексерілді және жабылды',
  reopened: 'Қайта ашылды',
  rejected: 'Жалған анықтау',
  returned: 'Қайтарылды',
  duplicate: 'Дубликатпен біріктірілді',
};

const ZONE_KIND = {
  repair: { label: 'Жол жұмысы жүріп жатыр', color: '#ffd60a', className: 'roadwork' },
  closed: { label: 'Жол жабық', color: '#ff453a', className: 'closed' },
  ignore: { label: 'Ішкі бақылау аймағы', color: '#8e8e96', className: 'ignore' },
};

const SEVERITY_COLOR = { high: '#ff453a', medium: '#ffd60a', low: '#0a84ff' };
const SEVERITY_ICON = { high: 'fa-triangle-exclamation', medium: 'fa-circle-exclamation', low: 'fa-circle-info' };
const TYPE_ICON = {
  pothole: 'fa-road',
  crack_alligator: 'fa-burst',
  crack_longitudinal: 'fa-wave-square',
  crack_transverse: 'fa-grip-lines',
  flood: 'fa-water',
  streetlight_out: 'fa-lightbulb',
  obstruction: 'fa-road-barrier',
  manhole_open: 'fa-circle-exclamation',
};

const state = {
  map: null,
  documents: [],
  visibleDocuments: [],
  selectedId: null,
  markers: {},
  knownIds: new Set(),
  // Шешім қабылданған оқиғалар. Сервер жауабы кешігіп келсе, карточка
  // тізімге қайта шығып кетпеуі үшін — оператор оны «қаралды» деп
  // санап қойған, ол қайта пайда болса шатасады.
  dismissed: new Set(),
  reviewedCount: 0,
  firstLoad: true,
  mapFitted: false,
  liveTimer: null,
  zones: [],
  zoneTints: [],
  cdnLayer: null,
  boundaryLayer: null,
  boundaryGeo: null,          // масштаб өзгергенде қалыңдығы қайта есептелетін жолақтар
  drawing: false,
  drawingPoints: [],
  drawingLine: null,
  drawingNodes: null,
  pendingZonePoints: null,
  currentDoc: null,
  foundPoint: null,
  foundMarker: null,
  manualPoint: null,
  pickingForManual: false,
  correctionId: null,
  correctionMarker: null,
  pendingRejectId: null,
  layers: {},
  layerVisibility: { incidents: true, roadworks: true, closures: true },
  lastStats: null,
  freshIds: new Set(),
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]));
}

function attr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

function toast(message, kind = '') {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast show ${kind}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = `toast ${kind}`; }, 3400);
}

function auth() { return localStorage.getItem('aiqyn_auth'); }
function authHeaders() { return auth() ? { Authorization: `Basic ${auth()}` } : {}; }
/* Орыс тілінде сервер берген орысша өрістерді қолданамыз: ақау түрі,
   өтінім мәтіні, жауапты ұйым. Интерфейстің қалған мәтінін i18n.js аударады. */
const RU = window.AIQYN_LANG === 'ru';
function localizeDoc(doc) {
  if (!RU || !doc) return doc;
  return {
    ...doc,
    defect_type_official: doc.defect_type_official_ru || doc.defect_type_official,
    description_text: doc.description_text_ru || doc.description_text,
    responsible_org: doc.responsible_org_ru || doc.responsible_org,
  };
}

function isReviewedStatus(status) {
  return !['new', 'candidate', 'rejected', 'duplicate'].includes(status);
}
function isDraft(doc) {
  if (doc.is_draft != null) return Boolean(doc.is_draft);
  return !isReviewedStatus(doc.status) || !doc.external_ticket_id;
}

function shortDate(value) {
  if (!value) return '—';
  return String(value).replace('T', ' ').slice(0, 16);
}

function relativeTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'дәл қазір';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин бұрын`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} сағ бұрын`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} күн бұрын`;
  return shortDate(value);
}

function copyText(text, message) {
  const done = () => toast(message, 'ok');
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => toast('Көшірілмеді', 'error'));
    return;
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  try { document.execCommand('copy'); done(); } catch { toast('Көшірілмеді', 'error'); }
  area.remove();
}

let audioContext = null;
function playPing() {
  try {
    audioContext ||= new (window.AudioContext || window.webkitAudioContext)();
    if (audioContext.state === 'suspended') audioContext.resume();
    const now = audioContext.currentTime;
    [880, 1318.5].forEach((frequency, index) => {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, now + index * 0.09);
      gain.gain.exponentialRampToValueAtTime(0.045, now + index * 0.09 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + index * 0.09 + 0.35);
      oscillator.connect(gain).connect(audioContext.destination);
      oscillator.start(now + index * 0.09);
      oscillator.stop(now + index * 0.09 + 0.4);
    });
  } catch { /* дыбыс қолжетімсіз — ештеңе етпейміз */ }
}

function accuracyLabel(doc) {
  const value = Number(doc.gps_accuracy_m ?? doc.location_accuracy_m);
  if (!Number.isFinite(value) || value <= 0) return 'Белгісіз';
  return `±${Math.round(value)} м`;
}

function currentCoordinates(doc) {
  return {
    lat: Number(doc.display_lat ?? doc.lat),
    lon: Number(doc.display_lon ?? doc.lon),
  };
}

function modal(id, open) {
  const element = $(id);
  if (!element) return;
  element.classList.toggle('open', open);
  if (open) {
    const focusable = element.querySelector('input, select, textarea, button');
    setTimeout(() => focusable?.focus(), 20);
  }
}

function requireAuth() {
  if (auth()) return true;
  modal('login-modal', true);
  toast('Оператор ретінде кіріңіз', 'error');
  return false;
}

/* ---------------- Карта ---------------- */

/* Базалық карта үш деңгейлі: CDN → жергілікті кэш → тор.
   Демо офлайн өтуі мүмкін, сондықтан тақтайша жүктелмесе де портал
   жұмысын жалғастыруы керек: маркерлер координата бойынша дұрыс орында
   тұрады, тек фон ғана өзгереді. */
/* CARTO 2026 жылдан бастап кілт сұрайды (тақтайшаның орнында «API KEY
   REQUIRED» жазуы шығады), сондықтан Esri Canvas қолданылады: кілтсіз,
   Шымкент көшелерінің атаулары қазақша жазылған. */
const ESRI_CANVAS = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas';
const TILE_CDN_DARK = `${ESRI_CANVAS}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`;
const TILE_CDN_LIGHT = `${ESRI_CANVAS}/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`;
const TILE_CDN = TILE_CDN_DARK;
// Көше атаулары бөлек қабатта: негізгі тақтайша оларды тек ірі масштабта көрсетеді
const TILE_LABELS_DARK = `${ESRI_CANVAS}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`;
const TILE_LABELS_LIGHT = `${ESRI_CANVAS}/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`;
const TILE_ATTRIBUTION = 'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap';
const TILE_LOCAL = '/static/tiles/{z}/{x}/{y}.png';

function setBasemapMode(mode, note) {
  const badge = $('basemap-note');
  document.body.classList.toggle('basemap-offline', mode !== 'online');
  if (!badge) return;
  if (mode === 'online') {
    badge.hidden = true;
    return;
  }
  badge.hidden = false;
  badge.querySelector('span').textContent = note;
}

/* ============================================================
   Тема: қара / ақ
   ------------------------------------------------------------
   Күндіз, әсіресе проекторда, қара интерфейс оқылмайды. Таңдау
   есте сақталады. Карта тақтасы да бірге ауысады — әйтпесе ақ
   беттің ортасында қара карта жалғыз қалып, көзге ұрып тұрар еді.
   ============================================================ */
function applyTheme(mode) {
  const light = mode === 'light';
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
  localStorage.setItem('aiqyn_theme', light ? 'light' : 'dark');

  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = light ? '#f4f4f2' : '#050506';

  const button = document.getElementById('theme-toggle');
  if (button) {
    button.querySelector('i').className = light ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
    button.title = light ? 'Қара тема' : 'Ақ тема';
  }

  // Карта тақтасын ауыстыру
  if (state.cdnLayer) {
    state.cdnLayer.setUrl(light ? TILE_CDN_LIGHT : TILE_CDN_DARK);
  }
  if (state.labelLayer) {
    state.labelLayer.setUrl(light ? TILE_LABELS_LIGHT : TILE_LABELS_DARK);
  }
}

function initBasemap() {
  const light = document.documentElement.dataset.theme === 'light';
  const cdn = state.cdnLayer = L.tileLayer(light ? TILE_CDN_LIGHT : TILE_CDN, {
    attribution: TILE_ATTRIBUTION,
    maxZoom: 20,
    maxNativeZoom: 16,
  });
  const labels = state.labelLayer = L.tileLayer(light ? TILE_LABELS_LIGHT : TILE_LABELS_DARK, {
    maxZoom: 20,
    maxNativeZoom: 16,
    zIndex: 2,
  });

  let loaded = 0;
  let failed = 0;
  let switched = false;

  const useLocal = () => {
    if (switched) return;
    switched = true;
    state.map.removeLayer(cdn);
    state.map.removeLayer(labels);

    // Жергілікті кэш бар ма? (scripts/cache_tiles.py оны алдын ала жасайды)
    fetch('/static/tiles/manifest.json', { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : Promise.reject()))
      .then((manifest) => {
        L.tileLayer(TILE_LOCAL, {
          attribution: `${TILE_ATTRIBUTION} · жергілікті көшірме`,
          minZoom: manifest.min_zoom ?? 11,
          maxZoom: manifest.max_zoom ?? 15,
          maxNativeZoom: manifest.max_zoom ?? 15,
          bounds: manifest.bounds,
          errorTileUrl:
            'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7',
        }).addTo(state.map);
        setBasemapMode('cache', 'Байланыс жоқ — карта жергілікті көшірмеден');
      })
      .catch(() => {
        setBasemapMode('grid', 'Байланыс жоқ — карта фоны жүктелмеді, координаталар дұрыс');
      });
  };

  cdn.on('tileload', () => { loaded += 1; });
  cdn.on('tileerror', () => {
    failed += 1;
    // Бірен-саран қате — қалыпты жағдай. Бірде-бір тақтайша келмесе ғана ауысамыз.
    if (loaded === 0 && failed >= 4) useLocal();
  });

  cdn.addTo(state.map);
  labels.addTo(state.map);

  // CDN мүлдем жауап бермей, `tileerror` де шықпай қалатын жағдай
  setTimeout(() => { if (loaded === 0) useLocal(); }, 6000);
}

function initMap() {
  state.map = L.map('map', {
    zoomControl: true,
    preferCanvas: true,
    minZoom: 10,
  }).setView(SHYMKENT, 13);

  initBasemap();

  state.layers.incidents = L.layerGroup().addTo(state.map);
  state.layers.roadworks = L.layerGroup().addTo(state.map);
  state.layers.closures = L.layerGroup().addTo(state.map);
  state.layers.ignore = L.layerGroup().addTo(state.map);
  state.drawingNodes = L.layerGroup().addTo(state.map);

  // Масштаб өзгергенде боялған жол жолағының ені қайта есептеледі —
  // ол әрқашан асфальттың нақты енімен беттесіп тұрады
  // Учаске атаулары маркерлердің АСТЫНДА тұрады, ал қала масштабында
  // (14-тен кіші) мүлдем жасырылады: ондаған атау бір-бірін, маркерлерді
  // және карта тақтасын жауып тастайтын.
  state.map.createPane('zoneLabels').style.zIndex = 450;
  state.map.getPane('zoneLabels').style.pointerEvents = 'none';
  const syncLabelZoom = () => document.querySelector('.map-wrap')
    .classList.toggle('labels-far', state.map.getZoom() < 14);
  state.map.on('zoomend', syncLabelZoom);
  syncLabelZoom();
  state.map.on('zoomend', refreshZoneWeights);

  state.map.on('click', async (event) => {
    if (state.drawing) {
      addDrawPoint(event.latlng);
      return;
    }
    if (state.pickingForManual) {
      state.pickingForManual = false;
      document.querySelector('.map-wrap').classList.remove('location-mode');
      $('location-hint').classList.remove('show');
      setManualPoint({ lat: event.latlng.lat, lon: event.latlng.lng, label: '', detail: '' });
      $('man-addr').value = '';
      modal('manual-modal', true);
      return;
    }
    if (state.correctionId) await saveCorrectedLocation(event.latlng);
  });
}

function setLayerVisibility(name, visible) {
  state.layerVisibility[name] = visible;
  const layer = state.layers[name];
  if (!layer) return;
  if (visible && !state.map.hasLayer(layer)) layer.addTo(state.map);
  if (!visible && state.map.hasLayer(layer)) state.map.removeLayer(layer);

  const button = $(`layer-${name}`);
  if (button) {
    button.classList.toggle('active', visible);
    button.setAttribute('aria-pressed', String(visible));
  }
}

function pinIcon(group, fresh = false) {
  const lead = group[0];
  const status = lead.status;
  const severity = group.some((item) => item.severity === 'high') ? 'high'
    : group.some((item) => item.severity === 'medium') ? 'medium' : 'low';
  const color = ['repaired', 'resolved', 'closed'].includes(status) ? '#30d158'
    : status === 'rejected' ? '#8e8e96'
      : (SEVERITY_COLOR[severity] || '#ff453a');
  const approximate = group.some((item) => !item.gps_trusted && !item.location_corrected);
  const html = `
    <div class="pin-wrap ${approximate ? 'approximate' : ''} ${fresh ? 'fresh' : ''}"
         style="--pin:${color}" title="${approximate ? 'Орны нақтыланбаған' : 'Координатасы нақтыланған'}">
      <i class="fa-solid fa-location-dot" aria-hidden="true"></i>
      <span class="pin-status"></span>
      ${group.length > 1 ? `<span class="pin-count">${group.length}</span>` : ''}
    </div>`;

  return L.divIcon({
    className: 'incident-pin',
    html,
    iconSize: [38, 44],
    iconAnchor: [19, 44],
    popupAnchor: [0, -42],
  });
}

function groupedDocuments(documents) {
  const groups = new Map();
  documents.forEach((doc) => {
    const { lat, lon } = currentCoordinates(doc);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const key = `${lat.toFixed(6)}:${lon.toFixed(6)}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(doc);
  });
  return [...groups.values()];
}

function groupPopup(group) {
  if (group.length === 1) {
    const doc = group[0];
    return `<div class="pin-popup">
      <b>${escapeHtml(doc.defect_type_official)}</b>
      <div class="addr">${escapeHtml(doc.display_address_text || doc.address_text)}</div>
      <div class="tags">
        <span class="badge ${attr(doc.severity)}">${escapeHtml(doc.severity_kk || doc.severity)}</span>
        <span class="badge st-${attr(doc.status)}">${escapeHtml(STATUS_LABEL[doc.status] || doc.status)}</span>
      </div>
      <div class="when">${escapeHtml(doc.timestamp_human || '')}</div>
    </div>`;
  }

  return `<div class="pin-popup">
    <b>Осы координатада ${group.length} оқиға</b>
    <div class="addr">GPS деректерін оператор нақтылай алады</div>
    <div class="tags"><span class="badge medium">Бір нүктеге топтастырылды</span></div>
  </div>`;
}

function renderMarkers({ fit = false } = {}) {
  state.layers.incidents.clearLayers();
  state.markers = {};
  const bounds = [];

  groupedDocuments(state.visibleDocuments).forEach((group) => {
    const lead = group[0];
    const { lat, lon } = currentCoordinates(lead);
    const fresh = !state.firstLoad && group.some((item) => !state.knownIds.has(item.event_id));
    const marker = L.marker([lat, lon], {
      icon: pinIcon(group, fresh),
      riseOnHover: true,
      keyboard: true,
      title: `${lead.defect_type_official} — ${lead.display_address_text || lead.address_text}`,
    });

    marker.bindPopup(groupPopup(group), { closeButton: true, maxWidth: 280 });
    marker.on('click', () => {
      if (group.length === 1) openDetail(lead.event_id);
    });
    marker.addTo(state.layers.incidents);
    group.forEach((doc) => { state.markers[doc.event_id] = marker; });
    bounds.push([lat, lon]);
  });

  if ((fit || !state.mapFitted) && bounds.length) {
    // maxZoom 15 болғанда бір көшедегі ақаулардың бәрі бір нүктеге
    // жиналып көрінетін: оператор нешеу екенін де ажырата алмайтын.
    // Бір көше = ~50-300 м. Esri фоны 16-шы деңгейден әрі тек созылады
    // (бұлдырап, бос сұр алаң болып көрінеді), сондықтан шек — 16.
    state.map.fitBounds(bounds, { padding: [80, 80], maxZoom: 16 });
    state.mapFitted = true;
  }
}

function zoneGeometry(zone) {
  const points = zone.points || zone.polygon;
  if (Array.isArray(points) && points.length >= 2) return points;
  return null;
}

function zonePopup(zone, meta, unverified = false) {
  return `<div class="zone-popup">
    <b>${escapeHtml(zone.name)}</b>
    <span class="kind">${escapeHtml(meta.label)}${zone.corridor_m || zone.radius_m ? ` · дәліз ${Math.round(zone.corridor_m || zone.radius_m)} м` : ''}</span>
    ${zone.valid_until ? `<div class="kind">Аяқталуы: ${escapeHtml(zone.valid_until)}</div>` : ''}
    ${zone.responsible_org ? `<div class="kind">Жауапты: ${escapeHtml(zone.responsible_org)}</div>` : ''}
    ${zone.external_ref ? `<div class="kind">Негіз: ${escapeHtml(zone.external_ref)}</div>` : ''}
    ${zone.source_url ? `<div class="kind"><a href="${attr(zone.source_url)}" target="_blank" rel="noopener">Ресми дереккөзді ашу</a></div>` : ''}
    <div class="zone-status">
      ${unverified ? '<span class="badge medium">Шекарасы нақтыланбаған</span>' : '<span class="badge st-confirmed">Жол бойымен сызылған</span>'}
    </div>
    <button onclick="window.deactivateZone(${Number(zone.id)})">Учаскені архивтеу</button>
  </div>`;
}

/* Аймақ сызығының қалыңдығын МЕТРМЕН есептеу.
 *
 * Leaflet-те weight пиксельмен беріледі, ал жол — метрмен өлшенетін
 * нақты нысан. Сондықтан ағымдағы масштабтағы «метр/пиксель» мәнін
 * есептеп, дәліз енін пиксельге аударамыз. Нәтижесінде боялған жолақ
 * картаны ұлғайтқанда асфальттың нақты енімен беттеседі.
 */
function roadWeight(zone, points) {
  const map = state.map;
  if (!map) return 6;

  const lat = points && points.length ? points[0][0] : 42.32;
  const metersPerPixel =
    156543.03392 * Math.cos((lat * Math.PI) / 180) / Math.pow(2, map.getZoom());

  const corridor = Number(zone.corridor_m || zone.radius_m || 20);
  const px = (corridor * 2) / metersPerPixel;

  // Тым жіңішке де, тым дөрекі де болмасын
  return Math.max(3, Math.min(46, px));
}

function refreshZoneWeights() {
  state.zoneTints.forEach(({ layer, zone, points }) => {
    layer.setStyle({ weight: roadWeight(zone, points) });
  });
}

function renderZones() {
  ['roadworks', 'closures', 'ignore'].forEach((name) => state.layers[name].clearLayers());
  state.zoneTints = [];

  state.zones.forEach((zone) => {
    if (zone.expired || zone.effective_active === false) return;
    const meta = ZONE_KIND[zone.kind] || ZONE_KIND.repair;
    const layer = zone.kind === 'closed' ? state.layers.closures
      : zone.kind === 'ignore' ? state.layers.ignore : state.layers.roadworks;
    const points = zoneGeometry(zone);

    if (zone.shape === 'polyline' && points) {
      const verified = Boolean(zone.boundary_verified);

      // Жол ЖОЛДЫҢ ӨЗІНДЕЙ боялады: жалпақ жартылай мөлдір қабат
      // масштабқа қарай өзгереді (метрмен есептеледі), сондықтан
      // ұлғайтқанда жүру бөлігін дәл жауып тұрады, кішірейткенде
      // жіңішке сызыққа айналады — «қолдан сызылған» болып көрінбейді.
      const tint = L.polyline(points, {
        color: meta.color,
        weight: roadWeight(zone, points),
        opacity: verified ? .34 : .22,
        lineCap: 'round', lineJoin: 'round', interactive: false,
        className: 'zone-tint',
      });
      tint.addTo(layer);
      state.zoneTints.push({ layer: tint, zone, points });

      // Үстіндегі жіңішке өзек — қай масштабта да анық көрінеді
      const line = L.polyline(points, {
        color: meta.color,
        weight: zone.kind === 'closed' ? 3 : 2.5,
        opacity: verified ? .95 : .7,
        dashArray: zone.kind === 'closed' ? '10,8' : (verified ? null : '4,7'),
        lineCap: 'round', lineJoin: 'round',
      });
      line.bindPopup(zonePopup(zone, meta, !verified)).addTo(layer);
      // Алыс масштабта атау жасырын — сызықтың үстіне апарғанда шығады
      line.bindTooltip(escapeHtml(zone.name), { sticky: true, direction: 'top', className: 'zone-tip' });

      const center = line.getCenter ? line.getCenter() : line.getBounds().getCenter();
      L.marker(center, {
        interactive: false,
        pane: 'zoneLabels',
        icon: L.divIcon({
          className: '',
          html: `<div class="zone-label ${meta.className}">${escapeHtml(zone.name)}</div>`,
          iconAnchor: [45, -8],
        }),
      }).addTo(layer);
      return;
    }

    if (zone.shape === 'polygon' && points) {
      L.polygon(points, {
        color: meta.color, weight: 3, fillColor: meta.color, fillOpacity: .13,
        dashArray: zone.kind === 'closed' ? '10,8' : null,
      }).bindPopup(zonePopup(zone, meta)).addTo(layer);
      return;
    }

    if (zone.lat != null && zone.lon != null) {
      L.circle([zone.lat, zone.lon], {
        radius: Number(zone.radius_m || 100),
        color: meta.color, weight: 2, opacity: .55,
        fillColor: meta.color, fillOpacity: .025, dashArray: '4,9',
      }).bindPopup(zonePopup(zone, meta, true)).addTo(layer);
    }
  });
}

/* ============================================================
   iKomek 109-ға жүгіну
   ------------------------------------------------------------
   Шымкент әкімдігінің «i-Shymkent» орталығы 24/7 жұмыс істейді.
   Ашық API жоқ, бірақ жария арналар бар. Жүйе өтініштің мәтінін
   дайындап береді — оператор бір басып жібереді.

   wa.me хабарламаға ФАЙЛ тіркей алмайды, сондықтан мәтінде ресми
   PDF-ке сілтеме тұрады: 109 диспетчері оны бірден ашады.
   ============================================================ */
async function openIkomek(eventId) {
  let data;
  try {
    data = await fetch('/api/documents/' + encodeURIComponent(eventId) + '/ikomek')
      .then((r) => r.json());
  } catch (error) {
    return toast('109 сілтемесі жасалмады', 'error');
  }

  $('komek-body').innerHTML = `
    <div class="komek-head">
      <span class="komek-badge"><i class="fa-solid fa-headset"></i></span>
      <div>
        <p class="eyebrow">Ресми байланыс арнасы</p>
        <h2>${escapeHtml(data.target)}</h2>
        <p class="muted">Санат: Қалалық жол инфрақұрылымдары · 24/7</p>
      </div>
    </div>

    <p class="komek-note">Хабарламаның мәтіні дайын. Батырманы бассаңыз,
      WhatsApp ашылады да, тек «жіберу» түймесін басу қалады.
      Ресми PDF құжат хабарламадағы сілтемеде тұр.</p>

    <div class="komek-ways">
      <a class="btn btn-komek" href="${attr(data.whatsapp)}" target="_blank" rel="noopener">
        <i class="fa-solid fa-comment-dots"></i> WhatsApp арқылы жіберу
        <small>${escapeHtml(data.whatsapp_number)}</small></a>
      <a class="btn btn-ghost" href="${attr(data.telegram)}" target="_blank" rel="noopener">
        <i class="fa-solid fa-paper-plane"></i> Telegram-бот</a>
      <a class="btn btn-ghost" href="${attr(data.phone)}">
        <i class="fa-solid fa-phone"></i> 109 нөміріне қоңырау</a>
      <a class="btn btn-ghost" href="/api/documents/${encodeURIComponent(eventId)}/pdf"
         target="_blank" rel="noopener">
        <i class="fa-solid fa-file-pdf"></i> PDF-ті қарау</a>
    </div>

    <p class="section-title">Жіберілетін мәтін</p>
    <pre class="komek-text" id="komek-text">${escapeHtml(data.text)}</pre>
    <div class="modal-actions">
      <span class="kbd-hints"><kbd>Enter</kbd> WhatsApp <kbd>C</kbd> көшіру <kbd>Esc</kbd> жабу</span>
      <button class="btn btn-ghost" id="komek-copy"><i class="fa-solid fa-copy"></i> Мәтінді көшіру</button>
      <button class="btn btn-ghost" id="komek-cancel">Жабу</button>
    </div>`;

  modal('komek-modal', true);
  // Фокус WhatsApp батырмасында: Enter басу жеткілікті
  const whatsapp = $('komek-body').querySelector('.btn-komek');
  if (whatsapp) setTimeout(() => whatsapp.focus(), 50);
  state.komekText = data.text;
  $('komek-copy').onclick = () => copyText(data.text, 'Өтініш мәтіні көшірілді');
  $('komek-cancel').onclick = () => modal('komek-modal', false);
}

/* ============================================================
   Мекенжай / сілтеме бойынша іздеу
   ------------------------------------------------------------
   Дереккөз: 2ГИС (Шымкент көшелерін жақсы біледі), резервте
   OpenStreetMap. Картаның сілтемесін көшіріп қойса да түсінеді.
   Табылған нүктеден бірден оқиға тіркеуге болады.
   ============================================================ */
let geoTimer = null;

function geoResultHtml(items, hint) {
  if (!items.length) {
    return `<div class="geo-empty">${escapeHtml(hint || 'Ештеңе табылмады')}</div>`;
  }
  return items.map((item, index) => `<button class="geo-item" data-i="${index}">
    <i class="fa-solid ${item.source === 'coords' ? 'fa-crosshairs' : 'fa-location-dot'}"></i>
    <span><b>${escapeHtml(item.label)}</b><small>${escapeHtml(item.detail || '')}</small></span>
  </button>`).join('');
}

async function geoLookup(query, box, onPick) {
  if (query.trim().length < 3) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = '<div class="geo-empty">Ізделуде…</div>';
  let data;
  try {
    data = await fetch('/api/geocode?q=' + encodeURIComponent(query)).then((r) => r.json());
  } catch (error) {
    box.innerHTML = '<div class="geo-empty">Іздеу қызметіне қосылу мүмкін болмады</div>';
    return;
  }
  const items = data.results || [];
  box.innerHTML = geoResultHtml(items, data.error || 'Шымкент шегінен ештеңе табылмады');
  box.querySelectorAll('.geo-item').forEach((node) => {
    node.onclick = () => { box.hidden = true; onPick(items[Number(node.dataset.i)]); };
  });
}

/* Картадан табылған нүкте: маркер қойылады да, «осында оқиға қосу»
   деген ұсыныс шығады */
function showFoundPoint(item) {
  if (state.foundMarker) state.map.removeLayer(state.foundMarker);
  state.foundPoint = item;
  state.foundMarker = L.marker([item.lat, item.lon], {
    icon: L.divIcon({
      className: 'incident-pin',
      html: '<div class="pin-wrap" style="--pin:#1adfe3"><i class="fa-solid fa-location-crosshairs"></i></div>',
      iconSize: [38, 44], iconAnchor: [19, 44],
    }),
  }).addTo(state.map);
  state.foundMarker.bindPopup(
    `<div class="pop"><b>${escapeHtml(item.label)}</b>`
    + `<span>${escapeHtml(item.detail || '')}</span>`
    + `<span class="pop-coords">${item.lat.toFixed(6)}, ${item.lon.toFixed(6)}</span>`
    + '<button class="btn btn-sm btn-primary" id="pop-add">Осы жерге оқиға қосу</button></div>'
  ).openPopup();
  state.map.setView([item.lat, item.lon], 17);
  setTimeout(() => {
    const add = document.getElementById('pop-add');
    if (add) add.onclick = () => { state.map.closePopup(); openManual(item); };
  }, 60);
}

/* ============================================================
   Суреттен автоматты қосу
   ------------------------------------------------------------
   Қолмен енгізуден айырмашылығы: адам ЕШТЕҢЕ таңдамайды.
   Сурет жүктеледі — GPS суреттің өзінен, ақауды детектор табады,
   мекенжайды 2ГИС береді, қорытындыны ЖИ жазады.

   Ақау табылмаса, оқиға ЖАСАЛМАЙДЫ: «бірдеңе тапқан болып» жазба
   қосу жүйеге деген сенімді жояды.
   ============================================================ */
async function uploadPhoto(file) {
  if (!file || !requireAuth()) return;

  const body = new FormData();
  body.append('photo', file);
  toast('Сурет өңделуде — детектор, мекенжай, ЖИ…');

  const box = document.createElement('div');
  box.className = 'photo-progress';
  box.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>'
    + '<span><b>' + escapeHtml(file.name) + '</b><small>детектор қарап жатыр…</small></span>';
  document.body.appendChild(box);

  try {
    const response = await fetch('/api/documents/from-photo', {
      method: 'POST', headers: authHeaders(), body,
    });
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(data.detail || 'Өңделмеді');
    }
    if (!data.ok) {
      // Ақау табылмауы — қате емес, НӘТИЖЕ. Оны да ашық айтамыз.
      toast(data.detail || 'Ақау табылмады', 'warn');
      return;
    }

    state.dismissed.clear();
    await loadData();
    const verdict = data.ai_verified ? 'ЖИ растады' : 'ЖИ растамады — тексеру қажет';
    toast(`${data.defect_type} · ${data.detections} рамка · ${verdict}`, 'ok');
    openDetail(data.event_id);
  } catch (error) {
    toast(error.message || 'Сурет өңделмеді', 'error');
  } finally {
    box.remove();
  }
}

/* ============================================================
   Қолмен оқиға енгізу
   ============================================================ */
async function openManual(point) {
  if (!requireAuth()) return;
  const select = $('man-type');
  if (!select.options.length) {
    try {
      const data = await fetch('/api/categories').then((r) => r.json());
      select.innerHTML = (data.categories || [])
        .map((item) => `<option value="${attr(item.key)}" data-sev="${attr(item.severity)}">${escapeHtml(RU ? (item.ru || item.kk) : item.kk)}</option>`)
        .join('');
      select.onchange = () => {
        const sev = select.selectedOptions[0]?.dataset.sev;
        const radio = document.querySelector(`input[name="man-sev"][value="${sev}"]`);
        if (radio) radio.checked = true;
      };
      select.onchange();
    } catch (error) { toast('Ақау түрлері жүктелмеді', 'error'); }
  }
  setManualPoint(point || state.foundPoint || null);
  $('man-photo').value = '';
  $('man-photo-name').hidden = true;
  modal('manual-modal', true);
  setTimeout(() => $('man-addr').focus(), 80);
}

function setManualPoint(point) {
  state.manualPoint = point || null;
  const label = $('man-point');
  if (!point) {
    label.textContent = 'Нүкте әлі таңдалмаған';
    label.classList.remove('ok');
    return;
  }
  $('man-addr').value = point.label || '';
  label.textContent = `Таңдалды: ${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`;
  label.classList.add('ok');
}

async function submitManual() {
  const point = state.manualPoint;
  if (!point) return toast('Алдымен орнын таңдаңыз', 'error');

  const body = new FormData();
  body.append('class_key', $('man-type').value);
  body.append('lat', point.lat);
  body.append('lon', point.lon);
  body.append('address_text', $('man-addr').value.trim() || point.label || '');
  body.append('severity', document.querySelector('input[name="man-sev"]:checked').value);
  body.append('note', $('man-note').value.trim());
  body.append('reporter', $('man-reporter').value.trim());
  const file = $('man-photo').files[0];
  if (file) body.append('photo', file);

  const save = $('manual-save');
  save.disabled = true;
  save.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Тіркелуде…';
  try {
    const response = await fetch('/api/documents/manual', {
      method: 'POST', headers: authHeaders(), body,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Тіркелмеді');
    }
    const result = await response.json();
    modal('manual-modal', false);
    if (state.foundMarker) { state.map.removeLayer(state.foundMarker); state.foundMarker = null; }
    $('man-note').value = '';
    $('man-reporter').value = '';
    state.manualPoint = null;
    state.dismissed.clear();
    await loadData();
    const verdict = result.ai_verified === true ? ' · ЖИ ақауды растады'
      : (result.ai_verified === false ? ' · ЖИ растамады, тексеру қажет' : '');
    toast('Оқиға тіркелді' + verdict, 'ok');
    openDetail(result.event_id);
  } catch (error) {
    toast(error.message || 'Тіркелмеді', 'error');
  } finally {
    save.disabled = false;
    save.innerHTML = '<i class="fa-solid fa-square-plus"></i> Тіркеу және картаға қосу';
  }
}

/* ============================================================
   Қала шекарасы және учаскелер тізімі
   ------------------------------------------------------------
   Шекара — OpenStreetMap-тегі Шымкенттің ресми әкімшілік шегі
   (relation/3389772). Ол жүйенің жауапкершілік аймағын көрсетеді:
   бұл сызықтың сыртындағы ақау қала әкімдігінің құзырында емес.
   ============================================================ */
async function toggleBoundary() {
  if (state.boundaryLayer) {
    state.map.removeLayer(state.boundaryLayer);
    state.boundaryLayer = null;
    toast('Қала шекарасы жасырылды');
    return;
  }
  try {
    const geo = state.boundaryGeo
      || (state.boundaryGeo = await fetch('/static/shymkent.geojson').then((r) => r.json()));
    state.boundaryLayer = L.geoJSON(geo, {
      style: { color: '#0a84ff', weight: 2, opacity: .75, dashArray: '7 5', fill: false },
      interactive: false,
    }).addTo(state.map);
    state.map.fitBounds(state.boundaryLayer.getBounds(), { padding: [30, 30] });
    toast('Шымкенттің әкімшілік шекарасы (OSM)', 'ok');
  } catch {
    toast('Шекара жүктелмеді', 'error');
  }
}

/* Учаскелер тізімі: бұрын оларды тек картадан басып қана көруге
   болатын. Ондаған учаске болғанда керегін табу мүмкін емес. */
function openZonesPanel() {
  const zones = state.zones || [];
  const KIND = { repair: ['Жол жұмысы', '#ffd60a'], closed: ['Жабық жол', '#ff453a'],
                 ignore: ['Ішкі аймақ', '#98989f'] };
  const body = zones.length
    ? zones.map((zone) => {
        const [label, color] = KIND[zone.kind] || ['Учаске', '#98989f'];
        return `<div class="zone-row">
          <span class="zone-dot" style="background:${color}"></span>
          <span class="zone-copy">
            <b>${escapeHtml(zone.name || 'Атауы жоқ')}</b>
            <small>${escapeHtml(label)}${zone.responsible_org ? ' · ' + escapeHtml(zone.responsible_org) : ''}</small>
          </span>
          <button class="btn btn-sm btn-ghost" data-zoom="${attr(zone.id)}" title="Картадан көрсету">
            <i class="fa-solid fa-location-crosshairs"></i></button>
          <button class="btn btn-sm btn-danger" data-drop="${attr(zone.id)}" title="Учаскені өшіру">
            <i class="fa-solid fa-xmark"></i></button>
        </div>`;
      }).join('')
    : '<p class="muted">Әзірге белгіленген учаске жоқ.</p>';

  $('zones-list').innerHTML = body;
  $('zones-count').textContent = `${zones.length} учаске`;
  modal('zones-modal', true);

  $('zones-list').querySelectorAll('[data-zoom]').forEach((btn) => {
    btn.onclick = () => {
      const zone = zones.find((z) => String(z.id) === btn.dataset.zoom);
      const points = zoneGeometry(zone);
      modal('zones-modal', false);
      if (points && points.length) state.map.fitBounds(L.latLngBounds(points), { padding: [60, 60] });
      else if (zone.lat && zone.lon) state.map.setView([zone.lat, zone.lon], 16);
    };
  });
  $('zones-list').querySelectorAll('[data-drop]').forEach((btn) => {
    btn.onclick = async () => {
      if (!window.confirm('Учаске өшірілсін бе?')) return;
      await deactivateZone(btn.dataset.drop);
      modal('zones-modal', false);
    };
  });
}

async function loadZones() {
  try {
    const response = await fetch('/api/zones?active_only=true&include_expired=false');
    if (!response.ok) throw new Error('zone request');
    state.zones = (await response.json()).zones || [];
    renderZones();
  } catch {
    toast('Жол учаскелері жүктелмеді', 'error');
  }
}

/* ---------------- Жол учаскесін сызу ---------------- */

function setDrawing(on) {
  state.drawing = on;
  state.correctionId = null;
  $('location-hint').classList.remove('show');
  document.querySelector('.map-wrap').classList.toggle('drawing', on);
  $('draw-panel').classList.toggle('show', on);
  if (!on) {
    state.drawingPoints = [];
    state.drawingNodes.clearLayers();
    if (state.drawingLine) state.map.removeLayer(state.drawingLine);
    state.drawingLine = null;
  }
  updateDrawControls();
}

function addDrawPoint(latlng) {
  state.drawingPoints.push([latlng.lat, latlng.lng]);
  const node = L.marker(latlng, {
    interactive: false,
    icon: L.divIcon({ className: '', html: '<div class="draw-node"></div>', iconSize: [13, 13], iconAnchor: [6, 6] }),
  });
  node.addTo(state.drawingNodes);
  redrawDraftLine();
}

function redrawDraftLine() {
  if (state.drawingLine) state.map.removeLayer(state.drawingLine);
  if (state.drawingPoints.length >= 2) {
    state.drawingLine = L.polyline(state.drawingPoints, {
      color: '#ffd60a', weight: 6, opacity: .95, lineCap: 'round', lineJoin: 'round',
    }).addTo(state.map);
  }
  updateDrawControls();
}

function updateDrawControls() {
  const count = state.drawingPoints.length;
  $('draw-status').textContent = count < 2
    ? `Жол осінің бойымен нүктелер қойыңыз · ${count} нүкте`
    : `${count} нүкте қойылды · қисық жерлерде қосымша нүкте қосыңыз`;
  $('zone-undo').disabled = count === 0;
  $('zone-finish').disabled = count < 2;
}

function undoDrawPoint() {
  if (!state.drawingPoints.length) return;
  state.drawingPoints.pop();
  state.drawingNodes.clearLayers();
  state.drawingPoints.forEach(([lat, lon]) => {
    L.marker([lat, lon], {
      interactive: false,
      icon: L.divIcon({ className: '', html: '<div class="draw-node"></div>', iconSize: [13, 13], iconAnchor: [6, 6] }),
    }).addTo(state.drawingNodes);
  });
  redrawDraftLine();
}

function finishDrawing() {
  if (state.drawingPoints.length < 2) return;
  state.pendingZonePoints = state.drawingPoints.map((point) => [...point]);
  const length = polylineLength(state.pendingZonePoints);
  $('zone-coords').textContent = `${state.pendingZonePoints.length} нүкте · шамамен ${length >= 1000 ? `${(length / 1000).toFixed(1)} км` : `${Math.round(length)} м`}`;
  const today = new Date();
  $('zone-start').value ||= today.toISOString().slice(0, 10);
  const month = new Date(today); month.setDate(month.getDate() + 30);
  $('zone-until').value ||= month.toISOString().slice(0, 10);
  setDrawing(false);
  modal('zone-modal', true);
}

function polylineLength(points) {
  let total = 0;
  for (let index = 1; index < points.length; index += 1) {
    total += state.map.distance(points[index - 1], points[index]);
  }
  return total;
}

async function saveZone() {
  if (!state.pendingZonePoints || state.pendingZonePoints.length < 2) return;
  if (!requireAuth()) return;

  const name = $('zone-name').value.trim();
  if (!name) {
    $('zone-name').focus();
    return toast('Учаске атауын жазыңыз', 'error');
  }

  const kind = document.querySelector('input[name="zone-kind"]:checked')?.value || 'repair';
  const fromText = $('zone-from').value.trim();
  const toText = $('zone-to').value.trim();
  const verified = $('zone-verified').checked;
  const org = $('zone-org').value.trim();
  const externalRef = $('zone-ref').value.trim();
  const sourceUrl = $('zone-source').value.trim();
  if (sourceUrl) {
    try {
      const parsed = new URL(sourceUrl);
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('protocol');
    } catch {
      return toast('Ресми сілтемені https://... форматында жазыңыз', 'error');
    }
  }
  if (verified && (!fromText || !toText)) {
    return toast('Расталған учаске үшін «қай жерден — қай жерге дейін» шекарасын жазыңыз', 'error');
  }
  if (verified && !org) {
    return toast('Расталған учаске үшін жауапты ұйымды көрсетіңіз', 'error');
  }
  if (verified && !externalRef && !sourceUrl) {
    return toast('Расталған учаске үшін тапсырма нөмірін немесе ресми сілтемені көрсетіңіз', 'error');
  }
  const notes = [
    fromText || toText ? `Шекарасы: ${fromText || '—'} — ${toText || '—'}.` : '',
    verified ? 'Шекараны оператор картада нақтылады.' : 'Шекара нақтылануды қажет етеді.',
  ].filter(Boolean).join(' ');

  const body = {
    name,
    kind,
    shape: 'polyline',
    points: state.pendingZonePoints,
    polygon: state.pendingZonePoints,
    radius_m: Number($('zone-radius').value || 18),
    corridor_m: Number($('zone-radius').value || 18),
    valid_from: $('zone-start').value || null,
    valid_until: $('zone-until').value || null,
    responsible_org: org,
    external_ref: externalRef,
    source_url: sourceUrl,
    note: notes,
    boundary_verified: verified,
  };

  const response = await fetch('/api/zones', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });

  if (response.status === 401) {
    localStorage.removeItem('aiqyn_auth');
    updateOperatorLabel();
    modal('login-modal', true);
    return toast('Кіру деректері дұрыс емес', 'error');
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    return toast(error.detail || 'Учаске сақталмады', 'error');
  }

  state.pendingZonePoints = null;
  ['zone-name', 'zone-from', 'zone-to', 'zone-org', 'zone-ref', 'zone-source'].forEach((id) => { $(id).value = ''; });
  $('zone-verified').checked = false;
  modal('zone-modal', false);
  await Promise.all([loadZones(), loadData()]);
  toast(kind === 'closed' ? 'Жабық жол қызыл сызықпен белгіленді' : 'Жол жұмысы сары сызықпен белгіленді', 'ok');
}

async function deactivateZone(zoneId) {
  if (!requireAuth()) return;
  const response = await fetch(`/api/zones/${zoneId}`, { method: 'DELETE', headers: authHeaders() });
  if (!response.ok) return toast('Учаске архивтелмеді', 'error');
  await loadZones();
  toast('Учаске архивке көшірілді', 'ok');
}
window.deactivateZone = deactivateZone;

/* ---------------- Оқиғалар тізімі ---------------- */

function renderList() {
  const list = $('list');
  const query = $('filter-search').value.trim().toLocaleLowerCase('kk-KZ');
  const pool = state.documents.filter((doc) => !state.dismissed.has(doc.event_id));
  state.visibleDocuments = query ? pool.filter((doc) => [
    doc.event_id, doc.defect_type_official, doc.display_address_text, doc.address_text,
  ].some((value) => String(value || '').toLocaleLowerCase('kk-KZ').includes(query))) : [...pool];

  // Қауіпті ақау кезек күтпеуі керек: тексерілмеген «жоғары» деңгейлілер
  // тізімнің басына шығады. Қалғанының реті (жаңасы жоғарыда) сақталады.
  const urgent = (doc) => (doc.status === 'new' && doc.severity === 'high' ? 0 : 1);
  state.visibleDocuments.sort((a, b) => urgent(a) - urgent(b));

  $('list-count').textContent = `${state.visibleDocuments.length} оқиға`;

  // Осы отырыста қаншасы қаралғаны — жұмыстың көрінетін нәтижесі
  const done = $('queue-done');
  if (done) {
    const count = state.reviewedCount || 0;
    done.hidden = !count;
    done.textContent = `${count} қаралды`;
  }

  if (!state.visibleDocuments.length) {
    list.innerHTML = `<div class="empty"><i class="fa-solid fa-inbox" aria-hidden="true"></i>Бұл сүзгі бойынша оқиға табылмады.</div>`;
    renderMarkers();
    return;
  }

  list.innerHTML = state.visibleDocuments.map((doc) => {
    const severity = doc.severity || 'low';
    const approximate = !doc.gps_trusted && !doc.location_corrected;
    const fresh = state.freshIds.has(doc.event_id);
    // Кіші карточкада ДӘЛЕЛ бірден көрінеді: оператор ашпай тұрып-ақ
    // «бұл шынымен ақау ма» дегенді көзбен шеше алады
    const thumb = doc.photo_file
      ? `<img class="card-thumb" src="/media/${encodeURIComponent(doc.event_id)}/${attr(doc.photo_file)}"
              alt="" loading="lazy" decoding="async">`
      : `<span class="card-thumb card-thumb--empty"><i class="fa-solid ${TYPE_ICON[doc.class_key] || SEVERITY_ICON[severity] || 'fa-location-dot'}"></i></span>`;
    const pending = !isReviewedStatus(doc.status);

    return `<article class="card ${doc.event_id === state.selectedId ? 'active' : ''}" data-id="${attr(doc.event_id)}" style="--card-accent:${SEVERITY_COLOR[severity] || SEVERITY_COLOR.low}">
      <div class="card-main" data-open="${attr(doc.event_id)}" role="button" tabindex="0">
        ${thumb}
        <span class="card-copy">
          <span class="card-type">${escapeHtml(doc.defect_type_official || 'Инфрақұрылым ақауы')}</span>
          <span class="card-addr"><i class="fa-solid fa-location-dot" aria-hidden="true"></i> ${escapeHtml(doc.display_address_text || doc.address_text || 'Мекенжай нақтыланбаған')}</span>
          <span class="card-meta">
            <span class="badge ${attr(severity)}">${escapeHtml(doc.severity_kk || severity)}</span>
            <span class="badge">ЖИ ${Math.round((doc.confidence || 0) * 100)}%</span>
            ${approximate ? '<span class="badge medium"><i class="fa-solid fa-location-crosshairs"></i> Орны жуық</span>' : ''}
            ${doc.source_type === 'manual'
              ? '<span class="badge src-manual"><i class="fa-solid fa-pen"></i> Қолмен</span>'
              : '<span class="badge src-auto"><i class="fa-solid fa-robot"></i> Автоматты</span>'}
            ${doc.external_ticket_id ? `<span class="badge st-sent">№ ${escapeHtml(doc.external_ticket_id)}</span>` : ''}
            ${!pending ? `<span class="badge st-${attr(doc.status)}">${escapeHtml(STATUS_LABEL[doc.status] || doc.status)}</span>` : ''}
          </span>
        </span>
        <time class="card-time" title="${attr(doc.timestamp_human || shortDate(doc.timestamp))}">${escapeHtml(relativeTime(doc.timestamp) || (doc.timestamp_human || '').slice(11, 16))}${fresh ? '<span class="fresh-dot" title="Жаңа оқиға"></span>' : ''}</time>
      </div>
      ${pending ? `<div class="card-quick">
        <button class="card-act reject" data-quick-reject="${attr(doc.event_id)}" title="Жалған анықтау — қарамай-ақ қабылдамау">
          <i class="fa-solid fa-xmark"></i> Жалған</button>
        <button class="card-act open" data-open="${attr(doc.event_id)}" title="Толық ашып тексеру">
          Ашып тексеру <i class="fa-solid fa-arrow-right"></i></button>
      </div>` : ''}
    </article>`;
  }).join('');

  list.querySelectorAll('[data-open]').forEach((node) => {
    node.onclick = () => openDetail(node.dataset.open);
    node.onkeydown = (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openDetail(node.dataset.open); }
    };
  });
  // Кіші карточкадан бірден қабылдамау: анық қоқыс сигналды ашудың қажеті жоқ
  list.querySelectorAll('[data-quick-reject]').forEach((node) => {
    node.onclick = (event) => {
      event.stopPropagation();
      openReject(node.dataset.quickReject);
    };
  });
  const activeCard = list.querySelector('.card.active');
  if (activeCard) activeCard.scrollIntoView({ block: 'nearest' });
  renderMarkers();
}

/* Ақау ТҮРЛЕРІ бойынша есеп.
 *
 * Жалпы сан («барлығы 12») басшыға да, жөндеу бригадасына да аз
 * мағына береді. Ал «7 шұңқыр, 4 торлы жарық, 1 су басу» — бұл
 * жоспарлауға жарайтын ақпарат: қандай техника, қандай материал керек.
 */
const TYPE_LABEL = {
  pothole: 'Шұңқыр',
  crack_alligator: 'Торлы жарық',
  crack_longitudinal: 'Бойлық жарық',
  crack_transverse: 'Көлденең жарық',
  flood: 'Су басу',
  streetlight_out: 'Жарықтандыру',
  obstruction: 'Кедергі',
  manhole_open: 'Ашық люк',
};

function renderTypeStats(stats) {
  const box = $('type-stats');
  if (!box) return;

  const rows = (stats.by_type || []).filter((row) => Number(row.count) > 0);
  if (!rows.length) {
    box.innerHTML = '';
    return;
  }

  const max = Math.max(...rows.map((row) => Number(row.count)));
  const total = rows.reduce((sum, row) => sum + Number(row.count), 0);

  /* Жиналмалы: панельдің биіктігі шектеулі, ал оператордың негізгі
     жұмысы — тізім. Есеп керек кезде ашылады, әдепкіде жабық тұрады
     да, кезекке орын босатады. Күйі есте сақталады. */
  const open = localStorage.getItem('aiqyn_types_open') === '1';
  box.innerHTML = `
    <details class="type-stats-box"${open ? ' open' : ''}>
      <summary class="type-stats-head">
        <span>Ақау түрлері</span>
        <b>${total}</b>
        <i class="fa-solid fa-chevron-down" aria-hidden="true"></i>
      </summary>
    ${rows.map((row) => {
      const key = row.class_key || '';
      const count = Number(row.count);
      const sent = Number(row.sent || 0);
      const label = TYPE_LABEL[key] || row.name || 'Басқа';
      const width = Math.round((count / max) * 100);
      return `
        <button class="type-row" data-type="${escapeHtml(key)}"
                title="Сүзу: ${escapeHtml(label)} · 109-ға жіберілді: ${sent}">
          <span class="type-name">${escapeHtml(label)}</span>
          <span class="type-bar"><i class="t-${escapeHtml(key)}" style="width:${width}%"></i></span>
          <b class="type-count">${count}</b>
        </button>`;
    }).join('')}
    </details>
  `;

  const box_details = box.querySelector('details');
  if (box_details) {
    box_details.ontoggle = () => localStorage.setItem('aiqyn_types_open', box_details.open ? '1' : '0');
  }

  box.querySelectorAll('[data-type]').forEach((row) => {
    row.onclick = () => {
      $('filter-type').value = row.dataset.type;
      closeDetail();
      loadData();
    };
  });
}

/* Хидердегі сандар аналитика бетіне көшті: оператордың экраны кезекке
   арналған, ал сан — басшының есебіне. Мұнда тек панельдегі түр есебі
   қалды, ол сүзгі ретінде де жұмыс істейді. */
function renderStats(stats) {
  renderTypeStats(stats);
  state.lastStats = stats;
}


async function loadData() {
  const status = $('filter-status').value;
  const type = $('filter-type').value;
  const connection = $('connection-pill');

  try {
    const [documentsResponse, statsResponse] = await Promise.all([
      fetch(`/api/documents?status=${encodeURIComponent(status)}&class_key=${encodeURIComponent(type)}&limit=500`),
      fetch('/api/stats'),
    ]);
    if (!documentsResponse.ok || !statsResponse.ok) throw new Error('request');

    state.documents = ((await documentsResponse.json()).documents || []).map(localizeDoc);
    const stats = await statsResponse.json();

    const arrived = state.documents.filter((doc) => !state.knownIds.has(doc.event_id));
    state.freshIds = state.firstLoad ? new Set() : new Set(arrived.map((doc) => doc.event_id));

    renderStats(stats);
    renderList();

    if (!state.firstLoad && arrived.length) {
      toast(arrived.length === 1 ? `Жаңа оқиға: ${arrived[0].defect_type_official}` : `${arrived.length} жаңа оқиға келді`, 'ok');
      if ($('live-switch').checked) playPing();
    }
    state.documents.forEach((doc) => state.knownIds.add(doc.event_id));
    state.firstLoad = false;
    connection.classList.remove('offline');
    connection.innerHTML = '<i class="fa-solid fa-circle"></i> Желіде';
  } catch {
    connection.classList.add('offline');
    connection.innerHTML = '<i class="fa-solid fa-circle"></i> Байланыс жоқ';
    if (state.firstLoad) {
      $('list').innerHTML = '<div class="empty"><i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i>Portal API-мен байланыс жоқ.<br>Сервер іске қосылғанын тексеріңіз.</div>';
      $('list-count').textContent = '0 оқиға';
    }
    toast('Portal API-мен байланыс үзілді', 'error');
  }
}

/* ---------------- Оқиға карточкасы ---------------- */

/* Дәлелді толық өлшемде ашу.
   Оң жақ панельдегі кадр кішкентай — ақауды көзбен тексеру үшін
   операторға толық өлшем қажет. */
function bindLightbox(id) {
  const box = $(id || 'media-box');
  if (!box) return;
  const media = box.querySelector('img, video');
  if (!media) return;
  media.style.cursor = 'zoom-in';
  media.onclick = () => openLightbox(media);
}

function openLightbox(media) {
  const isVideo = media.tagName === 'VIDEO';
  const box = document.createElement('div');
  box.className = 'lightbox';
  box.innerHTML = `
    <button class="lightbox-close" aria-label="Жабу"><i class="fa-solid fa-xmark"></i></button>
    ${isVideo
      ? `<video src="${attr(media.getAttribute('src'))}" controls autoplay loop playsinline></video>`
      : `<img src="${attr(media.getAttribute('src'))}" alt="${attr(media.alt || 'Дәлел')}">`}
    <a class="lightbox-open" href="${attr(media.getAttribute('src'))}" target="_blank" rel="noopener">
      <i class="fa-solid fa-arrow-up-right-from-square"></i> Бөлек терезеде ашу</a>`;
  const close = () => {
    box.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  box.addEventListener('click', (e) => {
    if (e.target === box || e.target.closest('.lightbox-close')) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(box);
  box.querySelector('.lightbox-close').focus();
}

/* ЖИ-сарапшының қорытындысы — бөлек, толық блок.
   Бұрын оның бөліктері жалпы кестенің ішінде шашылып жататын да,
   оператор ЖИ не деп жазғанын түсінбейтін. */
function renderAiBlock(doc) {
  const has = doc.ai_verified || doc.ai_note || doc.ai_size ||
              doc.ai_location || doc.ai_action || doc.ai_danger;
  if (!has) {
    return `
    <div class="detail-section"><p class="section-title">ЖИ талдауы</p></div>
    <div class="ai-block ai-block--off">
      <p>Бұл оқиға ЖИ-сарапшыдан өтпеген — детекция тікелей операторға берілген.
      Қорытынды тек <code>ai_verify</code> қосулы болғанда жазылады.</p>
    </div>`;
  }

  const rows = [
    ['Тексеру нәтижесі', doc.ai_verified ? 'Ақау расталды' : 'Күмәнді — оператор шешеді'],
    ['ЖИ сенімділігі', doc.ai_confidence ? Math.round(doc.ai_confidence * 100) + '%' : null],
    ['Шамаланған өлшемі', doc.ai_size],
    ['Жолдағы орны', doc.ai_location],
    ['Қауіптілігі', doc.ai_danger],
    ['Ұсынылатын шара', doc.ai_action ?
      doc.ai_action + (doc.ai_urgency_days ? ` · ${Number(doc.ai_urgency_days)} күн ішінде` : '') : null],
  ].filter((r) => r[1]);

  return `
    <div class="detail-section"><p class="section-title">ЖИ талдауы</p></div>
    <div class="ai-block${doc.ai_verified ? ' ai-block--ok' : ' ai-block--warn'}">
      <div class="ai-head">
        <span class="ai-icon"><i class="fa-solid fa-microchip"></i></span>
        <div>
          <b>${doc.ai_verified ? 'ЖИ ақауды растады' : 'ЖИ күмән білдірді'}</b>
          <small>${escapeHtml(doc.ai_model || 'көру моделі')} · қорытынды операторға арналған кеңес,
            шешім емес</small>
        </div>
      </div>
      ${doc.ai_note ? `<p class="ai-note">${escapeHtml(doc.ai_note)}</p>` : ''}
      <dl class="ai-rows">
        ${rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd></div>`).join('')}
      </dl>
    </div>`;
}

/* ============================================================
   Оқиға карточкасы: ЕКІ ДЕҢГЕЙ
   ------------------------------------------------------------
   1) Кіші панель — жылдам шешім үшін. Дәлел, төрт негізгі дерек
      және екі батырма. Оператордың жұмысының көбі осында бітеді.
   2) «Толығырақ» — үлкен терезе. Тексеру керек болғанда ғана
      ашылады: ірі дәлел, барлық дерек, ЖИ қорытындысы, жеткізу
      квитанциясы, тарих және қалған әрекеттердің бәрі.

   Бұрын бәрі бір панельде тұрған: биіктігі экраннан асып,
   оператор растау батырмасына жету үшін ұзақ айналдыратын.
   ============================================================ */

function docContext(doc) {
  const { lat, lon } = currentCoordinates(doc);
  return {
    doc,
    lat,
    lon,
    mediaBase: '/media/' + encodeURIComponent(doc.event_id),
    draft: isDraft(doc),
    approximate: !doc.gps_trusted && !doc.location_corrected,
    reviewed: isReviewedStatus(doc.status),
    mapLink: doc.map_link || ('https://www.google.com/maps?q=' + lat + ',' + lon),
    gisLink: Number.isFinite(lat) && Number.isFinite(lon)
      ? 'https://2gis.kz/shymkent?m=' + lon + '%2C' + lat + '%2F16' : null,
  };
}

function statusBanner(c) {
  if (c.draft) {
    return '<div class="draft-banner"><i class="fa-solid fa-file-pen"></i> <b>ӨТІНІМ ЖОБАСЫ</b> · Ресми арнаға жіберілмеген.</div>';
  }
  const label = c.doc.external_ticket_id
    ? 'ЕКЦ 109 № ' + escapeHtml(c.doc.external_ticket_id)
    : 'Оператор тексерген өтінім';
  const when = c.doc.external_ticket_created_at
    ? ' · ' + escapeHtml(shortDate(c.doc.external_ticket_created_at)) : '';
  return '<div class="registered-banner"><i class="fa-solid fa-circle-check"></i> <b>' + label + '</b>' + when + '</div>';
}

/* Тексерілмеген оқиғадағы негізгі екі батырма — екі деңгейде де бірдей */
function decideActions(c, suffix) {
  // Тексерілмеген оқиғада: растау + қабылдамау. Жіберу батырмасы БІРЕУ —
  // растау мен 109-ға жүгіну бір әрекет, екі бөлек батырма шатастырады.
  if (!c.reviewed) {
    return '<button class="btn btn-primary span-2" data-act="confirm' + suffix + '">'
      + '<i class="fa-solid fa-paper-plane"></i> <span class="btn-label">Растау және 109-ға жіберу</span></button>'
      + '<p class="send-hint span-2"><i class="fa-solid fa-route"></i> Кетеді: '
      + escapeHtml(state.deliveryLabel || 'iKomek 109 · аудит журналы') + '</p>'
      + '<button class="btn btn-danger span-2" data-act="reject' + suffix + '">'
      + '<i class="fa-solid fa-xmark"></i> Жалған деп белгілеу</button>';
  }
  // Расталып қойған оқиға: 109-ға қайта жүгінуге болады
  return '<button class="btn btn-komek span-2" data-act="ikomek' + suffix + '">'
    + '<i class="fa-solid fa-comment-dots"></i> iKomek 109-ға жүгіну</button>';
}

/* Оператор шешім қабылдау үшін ең алдымен ЖИ не деп тапқанын көруі керек.
   Бұрын бұл тек «Толығырақ» терезесінде тұратын — әр оқиға үшін бір қосымша басу. */
function aiLine(doc) {
  const checked = Boolean(doc.ai_model || doc.ai_note);
  if (!checked) {
    return '<div class="ai-line none"><i class="fa-solid fa-robot"></i>'
      + '<span><b>ЖИ тексермеген</b> — фото мен видеоны өзіңіз қараңыз</span></div>';
  }
  const ok = Boolean(doc.ai_verified);
  const pct = doc.ai_confidence ? ` · ${Math.round(doc.ai_confidence * 100)}%` : '';
  const note = (doc.ai_note || '').replace(/\s+/g, ' ').trim();
  const extra = [doc.ai_size, doc.ai_urgency_days ? `жөндеу мерзімі ~${doc.ai_urgency_days} күн` : '']
    .filter(Boolean).join(' · ');
  return `<div class="ai-line ${ok ? 'ok' : 'doubt'}"><i class="fa-solid fa-robot"></i><span>`
    + `<b>${ok ? 'ЖИ растады' : 'ЖИ күмәнданды'}${pct}</b>`
    + (note ? ` — ${escapeHtml(note.length > 150 ? note.slice(0, 150) + '…' : note)}` : '')
    + (extra ? `<small>${escapeHtml(extra)}</small>` : '')
    + '</span></div>';
}

async function openDetail(eventId) {
  state.selectedId = eventId;
  renderList();

  const response = await fetch('/api/documents/' + encodeURIComponent(eventId));
  if (!response.ok) return toast('Оқиға карточкасы жүктелмеді', 'error');
  const doc = localizeDoc(await response.json());
  state.currentDoc = doc;

  const c = docContext(doc);
  const detail = $('detail');

  detail.innerHTML = `
    <div class="detail-head">
      <div>
        <p class="eyebrow">Оқиға</p>
        <h2 class="detail-title">${escapeHtml(doc.defect_type_official || 'Инфрақұрылым ақауы')}</h2>
        <div class="detail-id">${escapeHtml(doc.event_id)}</div>
      </div>
      <div class="detail-head-actions">
        <button class="close-x" id="btn-copy-link" title="Сілтемені көшіру" aria-label="Сілтемені көшіру"><i class="fa-solid fa-link"></i></button>
        <button class="close-x" id="detail-close" aria-label="Жабу"><i class="fa-solid fa-xmark"></i></button>
      </div>
    </div>

    ${statusBanner(c)}
    ${c.approximate ? '<div class="warn"><i class="fa-solid fa-location-crosshairs"></i> GPS орны жуық. Жібермес бұрын маркерді нақтылаған жөн.</div>' : ''}

    <div class="media media--sm" id="media-box">
      ${doc.photo_file
        ? `<img src="${c.mediaBase}/${attr(doc.photo_file)}" alt="Ақаудың фотофиксациясы">`
        : '<div class="empty"><i class="fa-solid fa-image"></i>Фото дәлел жоқ</div>'}
    </div>

    <div class="quick-facts">
      <div><span>Мекенжай</span><b>${escapeHtml(doc.display_address_text || doc.address_text || '—')}</b></div>
      <div><span>Қауіп</span><b><span class="badge ${attr(doc.severity || 'low')}">${escapeHtml(doc.severity_kk || doc.severity || '—')}</span></b></div>
      <div><span>Сенімділік</span><b>${Math.round((doc.confidence || 0) * 100)}%</b></div>
      <div><span>Уақыты</span><b>${escapeHtml(doc.timestamp_human || shortDate(doc.timestamp))}</b></div>
    </div>

    ${aiLine(doc)}

    <button class="btn btn-ghost more-btn" id="btn-more">
      <span><i class="fa-solid fa-up-right-and-down-left-from-center"></i> Толығырақ ашу</span>
      <small>видео, ЖИ талдауы, тарих, барлық әрекет</small>
    </button>

    <div class="actions">${decideActions(c, '')}</div>
    ${c.reviewed ? '' : `<div class="kbd-hints"><kbd>Enter</kbd> растау <kbd>X</kbd> жалған
      <kbd>J</kbd><kbd>K</kbd> келесі <kbd>Esc</kbd> жабу</div>`}`;

  detail.classList.add('open');
  detail.setAttribute('aria-hidden', 'false');
  syncUrlEvent(eventId);
  $('detail-close').onclick = closeDetail;
  $('btn-copy-link').onclick = () => copyText(
    location.origin + '/portal?event=' + encodeURIComponent(doc.event_id),
    'Оқиға сілтемесі көшірілді');
  $('btn-more').onclick = openFull;
  const thumb = detail.querySelector('#media-box img');
  if (thumb) { thumb.style.cursor = 'zoom-in'; thumb.onclick = openFull; }
  bindDocActions(detail, c, '');
}

/* ---------- Үлкен терезе ---------- */
function openFull() {
  const doc = state.currentDoc;
  if (!doc) return;
  const c = docContext(doc);
  const officialTitle = c.draft ? 'Өтінім жобасының мәтіні' : 'Жөндеу өтінімінің мәтіні';

  $('full-body').innerHTML = `
    <div class="full-head">
      <div>
        <p class="eyebrow">Оқиға карточкасы</p>
        <h2>${escapeHtml(doc.defect_type_official || 'Инфрақұрылым ақауы')}</h2>
        <div class="detail-id">${escapeHtml(doc.event_id)}</div>
      </div>
      ${statusBanner(c)}
    </div>

    <div class="full-grid">
      <div class="full-left">
        ${doc.video_file ? `<div class="media-tabs">
          <button class="media-tab active" data-tab="photo">Фотофиксация</button>
          <button class="media-tab" data-tab="video">Видео · ${Math.round(doc.video_seconds || 0)} сек</button>
          ${doc.after_photo_file ? '<button class="media-tab" data-tab="after">Орындалғаннан кейін</button>' : ''}
        </div>` : ''}
        <div class="media" id="full-media">
          ${doc.photo_file
            ? `<img src="${c.mediaBase}/${attr(doc.photo_file)}" alt="Ақаудың фотофиксациясы">`
            : '<div class="empty"><i class="fa-solid fa-image"></i>Фото дәлел жоқ</div>'}
        </div>
        ${renderAiBlock(doc)}
        <div class="detail-section"><p class="section-title">${officialTitle}</p></div>
        <div class="doc-text">${escapeHtml(doc.description_text || 'Мәтін қалыптастырылмаған.')}</div>
      </div>

      <div class="full-right">
        ${c.approximate ? '<div class="warn"><i class="fa-solid fa-location-crosshairs"></i> GPS орны жуық көрсетілген. Өтінімді жібермес бұрын маркерді жолдың нақты нүктесіне бекітіңіз.</div>' : ''}
        <div class="detail-section"><p class="section-title">Оқиға деректері</p></div>
        <div class="fields">
          <div class="field"><span class="field-key">Жұмыс күйі</span><span class="field-val"><span class="badge st-${attr(doc.status)}">${escapeHtml(STATUS_LABEL[doc.status] || doc.status)}</span></span></div>
          <div class="field"><span class="field-key">Мекенжай</span><span class="field-val">${escapeHtml(doc.display_address_text || doc.address_text || '—')}</span></div>
          <div class="field"><span class="field-key">Координата</span><span class="field-val">${Number.isFinite(c.lat) ? c.lat.toFixed(6) : '—'}, ${Number.isFinite(c.lon) ? c.lon.toFixed(6) : '—'} · <a href="${attr(c.mapLink)}" target="_blank" rel="noopener">Google</a>${c.gisLink ? ` · <a href="${attr(c.gisLink)}" target="_blank" rel="noopener">2GIS</a>` : ''} · <button class="text-btn" data-act="coords" type="button">көшіру</button></span></div>
          <div class="field"><span class="field-key">Геопозиция</span><span class="field-val">${doc.location_corrected ? '<span class="badge st-confirmed">Оператор нақтылады</span>' : escapeHtml(accuracyLabel(doc))} · ${escapeHtml(doc.gps_source || doc.geo_source || 'дереккөз белгісіз')}</span></div>
          <div class="field"><span class="field-key">Алдын ала қауіп</span><span class="field-val"><span class="badge ${attr(doc.severity || 'low')}">${escapeHtml(doc.severity_kk || doc.severity || 'Бағаланбаған')}</span></span></div>
          <div class="field"><span class="field-key">Модель сенімділігі</span><span class="field-val">${Math.round((doc.confidence || 0) * 100)}% · бұл ауырлық бағасы емес</span></div>
          <div class="field"><span class="field-key">Қалай тіркелді</span><span class="field-val">${doc.source_type === 'manual'
            ? '<span class="badge src-manual"><i class="fa-solid fa-pen"></i> Оператор қолмен енгізді</span>'
            : '<span class="badge src-auto"><i class="fa-solid fa-robot"></i> Жүйе автоматты тапты</span>'} · ${escapeHtml(doc.detector || '—')}</span></div>
          <div class="field"><span class="field-key">Анықталған уақыт</span><span class="field-val">${escapeHtml(doc.timestamp_human || shortDate(doc.timestamp))}</span></div>
          <div class="field"><span class="field-key">Жауапты бағыт</span><span class="field-val">${escapeHtml(doc.responsible_org || 'ЕКЦ 109 диспетчерлік кезегі')}</span></div>
          ${doc.external_ticket_id ? `<div class="field"><span class="field-key">109 өтінімі</span><span class="field-val"><b>${escapeHtml(doc.external_ticket_id)}</b>${doc.external_ticket_status ? ` · ${escapeHtml(doc.external_ticket_status)}` : ''}</span></div>` : ''}
        </div>
        ${renderDeliveryBlock(doc)}
        <div class="detail-section"><p class="section-title">Әрекеттер тарихы</p></div>
        <div class="history">
          ${(doc.history || []).map((item) => `<div class="history-item">
            <span class="history-dot"></span>
            <span><b>${escapeHtml(STATUS_LABEL[item.status] || item.status)}</b>${item.note ? ` · ${escapeHtml(item.note)}` : ''}<br><small>${escapeHtml(shortDate(item.created_at))} · ${escapeHtml(item.actor || 'system')}</small></span>
          </div>`).join('') || '<div class="empty">Тарих жазбасы жоқ</div>'}
        </div>
      </div>
    </div>

    <div class="actions full-actions">
      ${decideActions(c, '-full')}
      <button class="btn btn-ghost" data-act="location"><i class="fa-solid fa-location-crosshairs"></i> Орнын нақтылау</button>
      <button class="btn btn-ghost" data-act="docx"><i class="fa-solid fa-file-word"></i> Word</button>
      <button class="btn btn-ghost" data-act="whatsapp"><i class="fa-solid fa-comment-dots"></i> WhatsApp</button>
      <button class="btn btn-ghost" data-act="pdf"><i class="fa-solid fa-file-pdf"></i> PDF</button>

      ${['sent', 'submitted', 'registered', 'assigned'].includes(doc.status) ? '<button class="btn btn-warning span-2" data-act="progress"><i class="fa-solid fa-person-digging"></i> Жұмыс басталды деп белгілеу</button>' : ''}
      ${doc.status === 'in_progress' ? '<button class="btn btn-primary span-2" data-act="after"><i class="fa-solid fa-camera"></i> Орындалғаннан кейінгі фотоны қосу</button><input type="file" id="after-file" accept="image/jpeg,image/png" hidden>' : ''}
      ${doc.status === 'repaired' ? '<button class="btn btn-primary" data-act="close-work"><i class="fa-solid fa-circle-check"></i> Қайта тексерілді — жабу</button><button class="btn btn-danger" data-act="reopen"><i class="fa-solid fa-rotate-left"></i> Ақау қалды — қайта ашу</button>' : ''}
      ${doc.status === 'reopened' ? '<button class="btn btn-warning span-2" data-act="resume"><i class="fa-solid fa-person-digging"></i> Қайта жөндеуге беру</button>' : ''}
    </div>`;

  modal('full-modal', true);
  bindDocActions($('full-body'), c, '-full');
  bindMediaTabs($('full-body'), c);
  bindLightbox('full-media');
}

/* Екі деңгейдегі батырмалар бір жерде байланады — қайталанбауы үшін */
function bindDocActions(root, c, suffix) {
  const id = c.doc.event_id;
  const go = (name, fn) => {
    const node = root.querySelector('[data-act="' + name + '"]');
    if (node) node.onclick = fn;
  };
  go('confirm' + suffix, () => requestSend(id, root));
  go('ikomek' + suffix, () => { modal('full-modal', false); openIkomek(id); });
  go('reject' + suffix, () => { modal('full-modal', false); openReject(id); });
  go('coords', () => copyText(c.lat.toFixed(6) + ', ' + c.lon.toFixed(6), 'Координата көшірілді'));
  go('location', () => { modal('full-modal', false); startLocationCorrection(id); });
  go('docx', () => { location.href = '/api/documents/' + encodeURIComponent(id) + '/docx'; });
  go('print', () => window.open('/documents/' + encodeURIComponent(id) + '/print', '_blank', 'noopener'));
  go('pdf', () => window.open('/api/documents/' + encodeURIComponent(id) + '/pdf', '_blank', 'noopener'));
  go('ikomek', () => openIkomek(id));
  go('whatsapp', async () => {
    try {
      const data = await fetch('/api/documents/' + encodeURIComponent(id) + '/whatsapp').then((r) => r.json());
      window.open(data.link, '_blank', 'noopener');
    } catch (error) { toast('WhatsApp сілтемесі жасалмады', 'error'); }
  });
  go('progress', () => setStatus(id, 'in_progress', 'Жауапты орындаушы жұмысты бастады'));
  go('close-work', () => setStatus(id, 'closed', 'Қайта тексеру: ақау жойылған'));
  go('reopen', () => setStatus(id, 'reopened', 'Қайта тексеру: ақау сақталған'));
  go('resume', () => setStatus(id, 'in_progress', 'Қайта жөндеуге берілді'));
  const after = root.querySelector('[data-act="after"]');
  if (after) {
    const input = root.querySelector('#after-file');
    after.onclick = () => input && input.click();
    if (input) input.onchange = () => uploadAfterPhoto(id, input.files[0]);
  }
}

function bindMediaTabs(root, c) {
  const doc = c.doc;
  root.querySelectorAll('.media-tab').forEach((tab) => {
    tab.onclick = () => {
      root.querySelectorAll('.media-tab').forEach((item) => item.classList.remove('active'));
      tab.classList.add('active');
      const box = root.querySelector('#full-media');
      if (tab.dataset.tab === 'video') {
        box.innerHTML = '<video src="' + c.mediaBase + '/' + attr(doc.video_file) + '" controls playsinline></video>';
      } else if (tab.dataset.tab === 'after') {
        box.innerHTML = '<img src="' + c.mediaBase + '/' + attr(doc.after_photo_file) + '" alt="Орындалғаннан кейінгі фото">';
      } else {
        box.innerHTML = '<img src="' + c.mediaBase + '/' + attr(doc.photo_file) + '" alt="Ақаудың фотофиксациясы">';
      }
      bindLightbox('full-media');
    };
  });
}

function syncUrlEvent(eventId) {
  const url = new URL(location.href);
  if (eventId) url.searchParams.set('event', eventId);
  else url.searchParams.delete('event');
  history.replaceState(null, '', url);
}

/* ============================================================
   Кезек: шешім қабылданған оқиға БІРДЕН тізімнен кетеді
   ------------------------------------------------------------
   Бұрын шешім қабылдағаннан кейін бет қайта жүктеліп, сол оқиға
   қайтадан ашылатын. Оператор «мен мұны әлі қараған жоқпын ба?»
   деп шатасатын. Енді:
     1) карточка бірден өшеді (серверді күтпейміз);
     2) КЕЛЕСІ оқиға өзі ашылады — кезекпен жұмыс істеу үшін;
     3) кезек бітсе, панель жабылып, «бәрі қаралды» деп жазылады.
   ============================================================ */
function advanceQueue(eventId, message, kind = 'ok') {
  const order = state.visibleDocuments.map((doc) => doc.event_id);
  const index = order.indexOf(eventId);

  // Тізімнен алып тастаймыз — сервер жауабын күтпей
  state.dismissed.add(eventId);
  state.documents = state.documents.filter((doc) => doc.event_id !== eventId);
  state.visibleDocuments = state.visibleDocuments.filter((doc) => doc.event_id !== eventId);
  state.reviewedCount = (state.reviewedCount || 0) + 1;

  // Орнына келген оқиға, болмаса алдыңғысы
  const next = state.visibleDocuments[index] || state.visibleDocuments[index - 1] || null;

  state.selectedId = null;
  renderList();

  if (next) {
    openDetail(next.event_id);
    toast(`${message} · кезекте тағы ${state.visibleDocuments.length}`, kind);
  } else {
    closeDetail();
    toast(`${message} · кезек бос`, kind);
  }

  // Статистиканы фонда жаңартамыз — интерфейс күтіп тұрмайды
  loadData().catch(() => {});
}

function closeDetail() {
  if (!state.selectedId) return;
  state.selectedId = null;
  const detail = $('detail');
  detail.classList.remove('open');
  detail.setAttribute('aria-hidden', 'true');
  syncUrlEvent(null);
  renderList();
}

function startLocationCorrection(eventId) {
  if (!requireAuth()) return;
  closeDetail();
  setDrawing(false);
  state.correctionId = eventId;
  document.querySelector('.map-wrap').classList.add('location-mode');
  $('location-hint').classList.add('show');
  toast('Маркердің ұшы түсетін нақты жол нүктесін таңдаңыз');
}

function cancelLocationCorrection() {
  state.correctionId = null;
  document.querySelector('.map-wrap').classList.remove('location-mode');
  $('location-hint').classList.remove('show');
  if (state.correctionMarker) state.map.removeLayer(state.correctionMarker);
  state.correctionMarker = null;
}

async function saveCorrectedLocation(latlng) {
  const eventId = state.correctionId;
  if (!eventId) return;

  if (state.correctionMarker) state.map.removeLayer(state.correctionMarker);
  state.correctionMarker = L.marker(latlng, {
    icon: L.divIcon({
      className: 'incident-pin',
      html: '<div class="pin-wrap" style="--pin:#64d2ff"><i class="fa-solid fa-location-dot"></i><span class="pin-status"></span></div>',
      iconSize: [38, 44], iconAnchor: [19, 44],
    }),
  }).addTo(state.map);

  const response = await fetch(`/api/documents/${encodeURIComponent(eventId)}/location`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      lat: latlng.lat,
      lon: latlng.lng,
      note: 'Оператор маркерді картада жолдың нақты нүктесіне бекітті',
    }),
  });
  if (!response.ok) {
    cancelLocationCorrection();
    const error = await response.json().catch(() => ({}));
    return toast(error.detail || 'Координата сақталмады', 'error');
  }

  cancelLocationCorrection();
  await loadData();
  toast('Маркер жолдың нақты нүктесіне бекітілді', 'ok');
  openDetail(eventId);
}

const CHANNEL_LABEL = {
  ekc109: 'ЕКЦ 109',
  email: 'Email',
  whatsapp: 'WhatsApp',
  telegram: 'Telegram',
};

/* Өтінім қайда кететіні — бір рет сұралып, әр карточкада көрсетіледі.
   Оператор батырманы баспас БҰРЫН арнаны көруі керек. */
async function loadDeliveryLabel() {
  try {
    const health = await fetch('/api/delivery/health').then((r) => r.json());
    const ready = Object.entries(health)
      .filter(([, item]) => item && item.configured)
      .map(([key]) => CHANNEL_LABEL[key] || key);
    const ekc = health.ekc109 && health.ekc109.configured;
    state.deliveryLabel = [
      ...ready,
      ...(ekc ? [] : ['iKomek 109 (WhatsApp, бір басу)', 'аудит журналы']),
    ].join(' · ');
  } catch (error) {
    state.deliveryLabel = 'iKomek 109 · аудит журналы';
  }
}

/* ============================================================
   Жіберу — екі қадам, бірақ браузердің confirm() терезесінсіз
   ------------------------------------------------------------
   Бірінші басу (немесе Enter) батырманы «Иә, жіберу» күйіне
   ауыстырады, екіншісі жібереді. Кездейсоқ басудан қорғайды, ал
   пернетақтамен бір оқиға = Enter, Enter. 6 секундта екінші басу
   болмаса, батырма бастапқы күйіне оралады.
   ============================================================ */
function requestSend(eventId, root) {
  if (!requireAuth()) return;
  const button = (root || document).querySelector('[data-act^="confirm"]');
  if (state.armedId === eventId) {
    clearTimeout(state.armTimer);
    state.armedId = null;
    if (button) { button.disabled = true; button.classList.remove('armed'); }
    modal('full-modal', false);
    // Оператор серверді КҮТПЕЙДІ: келесі оқиға бірден ашылады, жіберу фонда
    // жүреді. Координатаның жол сегменті кэште болмаса, сервер OSM-ды
    // 12 секундқа дейін күтуі мүмкін — ол уақытта оператор келесіні қарайды.
    advanceQueue(eventId, 'Жіберілуде…', 'ok');
    doSend(eventId);
    return;
  }
  state.armedId = eventId;
  if (button) {
    button.classList.add('armed');
    const label = button.querySelector('.btn-label');
    if (label) label.textContent = 'Иә, жіберу — тағы бір рет басыңыз (Enter)';
  }
  clearTimeout(state.armTimer);
  state.armTimer = setTimeout(() => {
    state.armedId = null;
    if (button && button.isConnected) {
      button.classList.remove('armed');
      const label = button.querySelector('.btn-label');
      if (label) label.textContent = 'Растау және 109-ға жіберу';
    }
  }, 6000);
}

async function doSend(eventId) {
  const response = await fetch(`/api/documents/${encodeURIComponent(eventId)}/confirm`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({}),
  });
  if (!response.ok) {
    // Кезектен алдын ала алынған оқиғаны қайтарамыз — ол жоғалып кетпеуі керек
    const error = await response.json().catch(() => ({}));
    state.dismissed.delete(eventId);
    state.reviewedCount = Math.max(0, (state.reviewedCount || 1) - 1);
    loadData().catch(() => {});
    return toast(`${error.detail || 'Өтінім жіберілмеді'} — оқиға кезекке қайтарылды`, 'error');
  }
  const result = await response.json();
  const delivery = result.delivery || {};
  const channels = delivery.channels || {};
  const sent = (delivery.delivered || []).map((key) => CHANNEL_LABEL[key] || key);

  toast(
    sent.length ? `Жіберілді: ${sent.join(', ')}` : `Расталды · № ${delivery.ticket_id || ''}`,
    sent.length ? 'ok' : 'warn',
  );

  // Ресми 109 API өтінімді қабылдамаса — iKomek терезесі ӨЗІ ашылады:
  // мәтін дайын, WhatsApp бір басумен. Бұрын бұл тексеріс жоқ өріске
  // (delivery.ikomek) қарайтын, сондықтан терезе ешқашан ашылмайтын.
  if (!(channels.ekc109 && channels.ekc109.ok)) openIkomek(eventId);
}

/* Жеткізу квитанциясы — өтінім ҚАЙ арнамен кеткені.
   Бұл блок жоқ болса, «жіберілді» деген сөз тексерілмейді. */
function renderDeliveryBlock(doc) {
  const attempts = doc.delivery_attempts || [];
  if (!attempts.length) return '';
  const last = attempts[attempts.length - 1];
  const channels = last.channels;
  if (!channels) return '';

  const rows = Object.entries(channels).map(([key, state]) => {
    const badge = state.ok ? 'st-sent' : (state.configured ? 'st-rejected' : 'st-new');
    const mark = state.ok ? 'жетті' : (state.configured ? 'жетпеді' : 'бапталмаған');
    return `<div class="field">
      <span class="field-key">${escapeHtml(CHANNEL_LABEL[key] || key)}</span>
      <span class="field-val"><span class="badge ${badge}">${mark}</span>
        <small>${escapeHtml(state.detail || '')}</small></span>
    </div>`;
  }).join('');

  return `
    <div class="detail-section"><p class="section-title">Жеткізу квитанциясы</p></div>
    <div class="fields">${rows}
      <div class="field"><span class="field-key">Әрекет уақыты</span>
        <span class="field-val">${escapeHtml(shortDate(last.created_at))} · ${escapeHtml(last.actor || 'operator')}</span></div>
    </div>`;
}

function openReject(eventId) {
  if (!requireAuth()) return;
  state.pendingRejectId = eventId;
  $('reject-note').value = '';
  modal('reject-modal', true);
}

function quickReject(number) {
  const select = $('reject-reason');
  if (!select || number < 1 || number > select.options.length) return;
  select.selectedIndex = number - 1;
  submitReject();
}

async function submitReject() {
  const eventId = state.pendingRejectId;
  if (!eventId) return;
  const reasonLabel = $('reject-reason').selectedOptions[0].textContent;
  const note = $('reject-note').value.trim();
  const response = await fetch(`/api/documents/${encodeURIComponent(eventId)}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ note: note ? `${reasonLabel}: ${note}` : reasonLabel }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    return toast(error.detail || 'Шешім сақталмады', 'error');
  }
  modal('reject-modal', false);
  state.pendingRejectId = null;
  advanceQueue(eventId, 'Жалған анықтау деп белгіленді');
}

async function setStatus(eventId, status, note) {
  if (!requireAuth()) return;
  const response = await fetch(`/api/documents/${encodeURIComponent(eventId)}/status`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ status, note }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    return toast(error.detail || 'Статус өзгермеді', 'error');
  }
  await loadData();
  toast('Жұмыс күйі жаңартылды', 'ok');
  openDetail(eventId);
}

async function uploadAfterPhoto(eventId, file) {
  if (!file) return;
  const body = new FormData();
  body.append('after_photo', file);
  const response = await fetch(`/api/documents/${encodeURIComponent(eventId)}/after-photo`, {
    method: 'POST', headers: authHeaders(), body,
  });
  if (!response.ok) return toast('Фото жүктелмеді', 'error');
  await loadData();
  toast('Фото қосылды — қайта тексеру кезегіне жіберілді', 'ok');
  openDetail(eventId);
}

/* ---------------- Кіру және іске қосу ---------------- */

function updateOperatorLabel() {
  const token = auth();
  let user = null;
  if (token) {
    try { user = atob(token).split(':')[0]; } catch { localStorage.removeItem('aiqyn_auth'); }
  }
  $('operator-label').textContent = user ? `${user} · кірді` : 'Кірмеген';
  $('btn-login').classList.toggle('authed', Boolean(user));
}

function setLive(on) {
  clearInterval(state.liveTimer);
  state.liveTimer = on ? setInterval(() => Promise.all([loadData(), loadZones()]), 5000) : null;
}

function bindEvents() {
  const resetQueue = () => {
    // Сүзгі ауысты — «қаралды» деген жасыру енді жарамсыз: басқа
    // сүзгіде сол оқиға көрінуі керек (мыс. «109-ға жіберілді»)
    state.dismissed.clear();
    state.reviewedCount = 0;
    loadData();
  };
  $('filter-status').onchange = resetQueue;
  $('filter-type').onchange = resetQueue;
  $('filter-search').oninput = renderList;
  $('btn-refresh').onclick = async () => { await Promise.all([loadData(), loadZones()]); toast('Деректер жаңартылды', 'ok'); };
  $('btn-show-all').onclick = () => renderMarkers({ fit: true });
  $('live-switch').onchange = (event) => { setLive(event.target.checked); toast(event.target.checked ? 'Тікелей жаңарту қосылды' : 'Тікелей жаңарту тоқтатылды'); };

  ['incidents', 'roadworks', 'closures'].forEach((name) => {
    $(`layer-${name}`).onclick = () => setLayerVisibility(name, !state.layerVisibility[name]);
  });

  /* ---------- «Картаға қосу» мәзірі ---------- */
  const menu = $('map-menu');
  const menuBtn = $('btn-map-menu');
  const closeMenu = () => { menu.hidden = true; menuBtn.setAttribute('aria-expanded', 'false'); };
  menuBtn.onclick = (event) => {
    event.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    menuBtn.setAttribute('aria-expanded', String(open));
  };
  /* Мәзірді жабу — CAPTURE фазасында.
     Кәдімгі click оқиғасы жеткіліксіз: Leaflet карта контейнеріндегі
     басылымды өзіне ұстап қалады да (stopPropagation), document-ке
     жетпейді. Сондықтан мәзір картаны басқанда жабылмай тұрған.
     pointerdown + capture — оны ешкім тоқтата алмайды. */
  document.addEventListener('pointerdown', (event) => {
    if (!$('map-menu-wrap').contains(event.target)) closeMenu();
  }, true);
  if (state.map) state.map.on('click movestart', closeMenu);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenu();
  });

  // Учаске түрі мәзірден таңдалады — модальда қайта іздеудің қажеті жоқ
  menu.querySelectorAll('[data-kind]').forEach((item) => {
    item.onclick = () => {
      closeMenu();
      if (!requireAuth()) return;
      const radio = document.querySelector(`input[name="zone-kind"][value="${item.dataset.kind}"]`);
      if (radio) radio.checked = true;
      closeDetail();
      setDrawing(true);
    };
  });

  $('menu-photo').onclick = () => { closeMenu(); $('photo-input').click(); };
  $('photo-input').onchange = (event) => {
    const file = event.target.files[0];
    event.target.value = '';
    uploadPhoto(file);
  };
  $('menu-manual').onclick = () => { closeMenu(); openManual(null); };
  $('menu-zones').onclick = () => { closeMenu(); openZonesPanel(); };

  /* ---------- Хидердегі іздеу ---------- */
  const geoBox = $('geo-results');
  $('geo-input').oninput = (event) => {
    const value = event.target.value;
    $('geo-clear').hidden = !value;
    clearTimeout(geoTimer);
    geoTimer = setTimeout(() => geoLookup(value, geoBox, showFoundPoint), 380);
  };
  $('geo-input').onkeydown = (event) => {
    if (event.key === 'Escape') { geoBox.hidden = true; event.target.blur(); }
  };
  $('geo-clear').onclick = () => {
    $('geo-input').value = '';
    $('geo-clear').hidden = true;
    geoBox.hidden = true;
    if (state.foundMarker) { state.map.removeLayer(state.foundMarker); state.foundMarker = null; }
  };
  document.addEventListener('pointerdown', (event) => {
    if (!$('geo-search').contains(event.target)) geoBox.hidden = true;
  }, true);

  /* ---------- Қолмен енгізу ---------- */
  $('komek-close').onclick = () => modal('komek-modal', false);
  $('manual-close').onclick = () => modal('manual-modal', false);
  $('manual-cancel').onclick = () => modal('manual-modal', false);
  $('manual-save').onclick = submitManual;
  $('man-photo-btn').onclick = () => $('man-photo').click();
  $('man-photo').onchange = () => {
    const file = $('man-photo').files[0];
    $('man-photo-name').hidden = !file;
    if (file) $('man-photo-name').textContent = 'Таңдалды: ' + file.name;
  };
  const manBox = $('man-results');
  let manTimer = null;
  $('man-addr').oninput = (event) => {
    clearTimeout(manTimer);
    manTimer = setTimeout(() => geoLookup(event.target.value, manBox, setManualPoint), 380);
  };
  $('man-pick').onclick = () => {
    modal('manual-modal', false);
    state.pickingForManual = true;
    document.querySelector('.map-wrap').classList.add('location-mode');
    $('location-hint').classList.add('show');
    toast('Картадан ақаудың нақты нүктесін басыңыз');
  };

  /* ---------- Панельді жию ---------- */
  $('theme-toggle').onclick = () => applyTheme(
    document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');

  const side = $('side-toggle');
  const applySide = (collapsed) => {
    document.body.classList.toggle('side-collapsed', collapsed);
    side.querySelector('i').className = collapsed
      ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-left';
    localStorage.setItem('aiqyn_side', collapsed ? '1' : '0');
    setTimeout(() => state.map && state.map.invalidateSize(), 320);
  };
  side.onclick = () => applySide(!document.body.classList.contains('side-collapsed'));
  applySide(localStorage.getItem('aiqyn_side') === '1');
  // Бет ашылғанда сақталған тема қолданылады (батырманың белгісі,
  // карта тақтасы, theme-color — бәрі бір жерден)
  applyTheme(localStorage.getItem('aiqyn_theme') || 'dark');
  $('menu-boundary').onclick = () => { closeMenu(); toggleBoundary(); };
  $('zone-undo').onclick = undoDrawPoint;
  $('zone-cancel').onclick = () => setDrawing(false);
  $('zone-finish').onclick = finishDrawing;
  $('zone-save').onclick = saveZone;
  $('zone-modal-cancel').onclick = () => { modal('zone-modal', false); state.pendingZonePoints = null; setDrawing(true); };
  $('zone-modal-close').onclick = () => { modal('zone-modal', false); state.pendingZonePoints = null; };

  $('location-cancel').onclick = cancelLocationCorrection;

  $('btn-login').onclick = () => modal('login-modal', true);
  $('login-close').onclick = () => modal('login-modal', false);
  $('login-cancel').onclick = () => modal('login-modal', false);
  $('login-submit').onclick = () => {
    const user = $('login-user').value.trim();
    const password = $('login-pass').value;
    if (!user || !password) return toast('Логин мен құпия сөзді толтырыңыз', 'error');
    localStorage.setItem('aiqyn_auth', btoa(`${user}:${password}`));
    updateOperatorLabel();
    modal('login-modal', false);
    toast(`Оператор: ${user}`, 'ok');
  };
  $('login-pass').onkeydown = (event) => { if (event.key === 'Enter') $('login-submit').click(); };

  $('full-close').onclick = () => modal('full-modal', false);
  $('zones-close').onclick = () => modal('zones-modal', false);

  $('reject-close').onclick = () => modal('reject-modal', false);
  $('reject-cancel').onclick = () => modal('reject-modal', false);
  $('reject-submit').onclick = submitReject;

  document.querySelectorAll('.modal').forEach((box) => {
    box.addEventListener('click', (event) => {
      if (event.target !== box) return;
      box.classList.remove('open');
      if (box.id === 'zone-modal') state.pendingZonePoints = null;
      if (box.id === 'reject-modal') state.pendingRejectId = null;
    });
  });

  document.querySelectorAll('#reject-quick [data-n]').forEach((button) => {
    button.onclick = () => quickReject(Number(button.dataset.n));
  });

  document.addEventListener('keydown', (event) => {
    // Терезе ашық болса, Esc тек соны жабады: астындағы оқиға карточкасы
    // ашық қалуы керек — оператор кезекпен жұмысын жалғастырады.
    if (event.key === 'Escape' && document.querySelector('.modal.open')) {
      document.querySelectorAll('.modal.open').forEach((item) => item.classList.remove('open'));
      state.pendingRejectId = null;
      return;
    }
    if (event.key === 'Escape') {
      setDrawing(false);
      cancelLocationCorrection();
      closeDetail();
      document.querySelectorAll('.modal.open').forEach((item) => item.classList.remove('open'));
      return;
    }

    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName) || event.target.isContentEditable;
    if (typing || event.ctrlKey || event.metaKey || event.altKey) return;

    // Ашық терезе өз пернелерін басқарады — кезек пернелері оның астындағы
    // келесі оқиғаны кездейсоқ жіберіп жібермеуі керек.
    const openModal = document.querySelector('.modal.open');
    if (openModal) {
      if (openModal.id === 'reject-modal' && /^[1-5]$/.test(event.key)) {
        event.preventDefault();
        quickReject(Number(event.key));
      } else if (openModal.id === 'komek-modal' && ['c', 'с'].includes(event.key.toLowerCase())) {
        event.preventDefault();
        if (state.komekText) copyText(state.komekText, 'Өтініш мәтіні көшірілді');
      }
      return;
    }

    if (event.key === '/') {
      event.preventDefault();
      $('filter-search').focus();
      return;
    }

    /* Кезекпен жұмыс — қолмен, тінтуірсіз.
       Оператор күніне ондаған оқиға қарайды: әр шешім үшін тінтуірді
       алып, батырманы іздеу уақыт алады. j/k — жүру, Enter — растау,
       x — жалған деп белгілеу. */
    const docs = state.visibleDocuments;

    if (['ArrowDown', 'ArrowUp', 'j', 'k', 'о', 'л'].includes(event.key)) {
      if (!docs.length) return;
      event.preventDefault();
      const down = ['ArrowDown', 'j', 'о'].includes(event.key);
      const index = docs.findIndex((doc) => doc.event_id === state.selectedId);
      const next = down
        ? (index + 1) % docs.length
        : (index <= 0 ? docs.length - 1 : index - 1);
      openDetail(docs[next].event_id);
      return;
    }

    if (!state.selectedId) return;
    const current = state.documents.find((doc) => doc.event_id === state.selectedId);
    if (!current || isReviewedStatus(current.status)) return;

    if (event.key === 'Enter') {
      event.preventDefault();
      requestSend(state.selectedId, $('detail'));
    } else if (event.key === 'x' || event.key === 'ч') {
      event.preventDefault();
      openReject(state.selectedId);
    }
  });
}

/* Портал сайттың ішінде <iframe> болып та көрсетіледі («тірі көрініс»).
   Ол жағдайда 5 секунд сайын сұрау жіберудің қажеті жоқ: көрініс статикалық
   дәлел ретінде тұрады, ал үздіксіз сұрау сайттың желісін бос жүктейді. */
const EMBEDDED = (() => {
  try { return window.self !== window.top; } catch (e) { return true; }
})();

document.addEventListener('DOMContentLoaded', async () => {
  if (EMBEDDED) document.body.classList.add('embedded');

  initMap();
  bindEvents();
  updateOperatorLabel();
  await Promise.all([loadZones(), loadData(), loadDeliveryLabel()]);

  if (EMBEDDED) {
    const liveSwitch = $('live-switch');
    if (liveSwitch) liveSwitch.checked = false;
  } else {
    setLive(true);
  }

  const linked = new URLSearchParams(location.search).get('event');
  if (linked) openDetail(linked);
});
