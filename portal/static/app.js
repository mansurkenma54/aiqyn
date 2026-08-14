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
  firstLoad: true,
  mapFitted: false,
  liveTimer: null,
  zones: [],
  zoneTints: [],          // масштаб өзгергенде қалыңдығы қайта есептелетін жолақтар
  drawing: false,
  drawingPoints: [],
  drawingLine: null,
  drawingNodes: null,
  pendingZonePoints: null,
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

function initMap() {
  state.map = L.map('map', {
    zoomControl: true,
    preferCanvas: true,
    minZoom: 10,
  }).setView(SHYMKENT, 13);

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap, &copy; CARTO',
    maxZoom: 20,
    subdomains: 'abcd',
  }).addTo(state.map);

  state.layers.incidents = L.layerGroup().addTo(state.map);
  state.layers.roadworks = L.layerGroup().addTo(state.map);
  state.layers.closures = L.layerGroup().addTo(state.map);
  state.layers.ignore = L.layerGroup().addTo(state.map);
  state.drawingNodes = L.layerGroup().addTo(state.map);

  // Масштаб өзгергенде боялған жол жолағының ені қайта есептеледі —
  // ол әрқашан асфальттың нақты енімен беттесіп тұрады
  state.map.on('zoomend', refreshZoneWeights);

  state.map.on('click', async (event) => {
    if (state.drawing) {
      addDrawPoint(event.latlng);
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
    state.map.fitBounds(bounds, { padding: [80, 80], maxZoom: 15 });
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

      const center = line.getCenter ? line.getCenter() : line.getBounds().getCenter();
      L.marker(center, {
        interactive: false,
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
  state.visibleDocuments = query ? state.documents.filter((doc) => [
    doc.event_id, doc.defect_type_official, doc.display_address_text, doc.address_text,
  ].some((value) => String(value || '').toLocaleLowerCase('kk-KZ').includes(query))) : [...state.documents];

  $('list-count').textContent = `${state.visibleDocuments.length} оқиға`;

  if (!state.visibleDocuments.length) {
    list.innerHTML = `<div class="empty"><i class="fa-solid fa-inbox" aria-hidden="true"></i>Бұл сүзгі бойынша оқиға табылмады.</div>`;
    renderMarkers();
    return;
  }

  list.innerHTML = state.visibleDocuments.map((doc) => {
    const severity = doc.severity || 'low';
    const approximate = !doc.gps_trusted && !doc.location_corrected;
    const fresh = state.freshIds.has(doc.event_id);
    return `<button class="card ${doc.event_id === state.selectedId ? 'active' : ''}" data-id="${attr(doc.event_id)}" style="--card-accent:${SEVERITY_COLOR[severity] || SEVERITY_COLOR.low}">
      <div class="card-head">
        <span class="card-icon ${attr(severity)}"><i class="fa-solid ${TYPE_ICON[doc.class_key] || SEVERITY_ICON[severity] || 'fa-location-dot'}" aria-hidden="true"></i></span>
        <span class="card-copy">
          <span class="card-type">${escapeHtml(doc.defect_type_official || 'Инфрақұрылым ақауы')}</span>
          <span class="card-addr"><i class="fa-solid fa-location-dot" aria-hidden="true"></i> ${escapeHtml(doc.display_address_text || doc.address_text || 'Мекенжай нақтыланбаған')}</span>
        </span>
        <time class="card-time" title="${attr(doc.timestamp_human || shortDate(doc.timestamp))}">${escapeHtml(relativeTime(doc.timestamp) || (doc.timestamp_human || '').slice(11, 16))}${fresh ? '<span class="fresh-dot" title="Жаңа оқиға"></span>' : ''}</time>
      </div>
      <span class="card-meta">
        <span class="badge ${attr(severity)}">${escapeHtml(doc.severity_kk || severity)}</span>
        <span class="badge st-${attr(doc.status)}">${escapeHtml(STATUS_LABEL[doc.status] || doc.status)}</span>
        <span class="badge">AI ${Math.round((doc.confidence || 0) * 100)}%</span>
        ${approximate ? '<span class="badge medium"><i class="fa-solid fa-location-crosshairs"></i> Орны жуық</span>' : ''}
        ${doc.external_ticket_id ? `<span class="badge st-sent">№ ${escapeHtml(doc.external_ticket_id)}</span>` : ''}
      </span>
    </button>`;
  }).join('');

  list.querySelectorAll('.card').forEach((card) => {
    card.onclick = () => openDetail(card.dataset.id);
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

  box.innerHTML = `
    <div class="type-stats-head">
      <span>Ақау түрлері</span>
      <b>${total}</b>
    </div>
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
  `;

  box.querySelectorAll('[data-type]').forEach((row) => {
    row.onclick = () => {
      $('filter-type').value = row.dataset.type;
      closeDetail();
      loadData();
    };
  });
}

function renderStats(stats) {
  const cards = [
    { icon: 'fa-layer-group', label: 'Барлығы', value: Number(stats.total || 0), status: 'all', cls: '' },
    { icon: 'fa-bell', label: 'Тексеруде', value: Number(stats.pending_review || 0), status: 'new', cls: 'alert' },
    { icon: 'fa-paper-plane', label: '109-ға жіберілді', value: Number(stats.by_status?.sent || stats.by_status?.submitted || 0), status: 'sent', cls: '' },
    { icon: 'fa-person-digging', label: 'Жұмыста', value: Number(stats.by_status?.in_progress || 0), status: 'in_progress', cls: 'work' },
    { icon: 'fa-road-barrier', label: 'Карта учаскесі', value: Number(stats.active_zones || 0), status: null, cls: '' },
  ];
  const previous = state.lastStats || {};
  $('stats').innerHTML = cards.map((card, index) => {
    const bump = previous[card.label] != null && previous[card.label] !== card.value;
    return `<button class="stat-card ${card.cls} ${bump ? 'bump' : ''}" data-stat-index="${index}" ${card.status ? `data-status="${card.status}" title="Сүзгі: ${card.label}"` : ''}>
      <i class="fa-solid ${card.icon}"></i><span>${card.label}</span><b>${card.value}</b>
    </button>`;
  }).join('');
  renderTypeStats(stats);
  state.lastStats = Object.fromEntries(cards.map((card) => [card.label, card.value]));
  $('stats').querySelectorAll('[data-status]').forEach((chip) => {
    chip.onclick = () => {
      $('filter-status').value = chip.dataset.status;
      closeDetail();
      loadData();
    };
  });
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

    state.documents = (await documentsResponse.json()).documents || [];
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

async function openDetail(eventId) {
  state.selectedId = eventId;
  renderList();

  const response = await fetch(`/api/documents/${encodeURIComponent(eventId)}`);
  if (!response.ok) return toast('Оқиға карточкасы жүктелмеді', 'error');
  const doc = await response.json();
  const detail = $('detail');
  const mediaBase = `/media/${encodeURIComponent(doc.event_id)}`;
  const draft = isDraft(doc);
  const approximate = !doc.gps_trusted && !doc.location_corrected;
  const reviewed = isReviewedStatus(doc.status);
  const { lat, lon } = currentCoordinates(doc);
  const mapLink = doc.map_link || `https://www.google.com/maps?q=${lat},${lon}`;
  const gisLink = Number.isFinite(lat) && Number.isFinite(lon) ? `https://2gis.kz/shymkent?m=${lon}%2C${lat}%2F16` : null;
  const officialTitle = draft ? 'Өтінім жобасының мәтіні' : 'Жөндеу өтінімінің мәтіні';

  detail.innerHTML = `
    <div class="detail-head">
      <div>
        <p class="eyebrow">Оқиға карточкасы</p>
        <h2 class="detail-title">${escapeHtml(doc.defect_type_official || 'Инфрақұрылым ақауы')}</h2>
        <div class="detail-id">${escapeHtml(doc.event_id)}</div>
      </div>
      <div class="detail-head-actions">
        <button class="close-x" id="btn-copy-link" title="Оқиғаға сілтемені көшіру" aria-label="Оқиғаға сілтемені көшіру"><i class="fa-solid fa-link"></i></button>
        <button class="close-x" id="detail-close" aria-label="Карточканы жабу"><i class="fa-solid fa-xmark"></i></button>
      </div>
    </div>

    ${draft ? `<div class="draft-banner"><i class="fa-solid fa-file-pen"></i> <b>ӨТІНІМ ЖОБАСЫ</b> · Ресми арнаға жіберілмеген. Оператор тексергеннен кейін ғана ЕКЦ 109-ға жолданады.</div>`
      : `<div class="registered-banner"><i class="fa-solid fa-circle-check"></i> <b>${doc.external_ticket_id ? `ЕКЦ 109 № ${escapeHtml(doc.external_ticket_id)}` : 'Оператор тексерген өтінім'}</b>${doc.external_ticket_created_at ? ` · ${escapeHtml(shortDate(doc.external_ticket_created_at))}` : ''}</div>`}

    ${approximate ? '<div class="warn"><i class="fa-solid fa-location-crosshairs"></i> GPS орны жуық көрсетілген. Өтінімді жібермес бұрын маркерді жолдың нақты нүктесіне бекітіңіз.</div>' : ''}

    ${doc.video_file ? `<div class="media-tabs">
      <button class="media-tab active" data-tab="photo">Фотофиксация</button>
      <button class="media-tab" data-tab="video">Видео · ${Math.round(doc.video_seconds || 0)} сек</button>
      ${doc.after_photo_file ? '<button class="media-tab" data-tab="after">Орындалғаннан кейін</button>' : ''}
    </div>` : ''}
    <div class="media" id="media-box">
      ${doc.photo_file ? `<img src="${mediaBase}/${attr(doc.photo_file)}" alt="Ақаудың фотофиксациясы">` : '<div class="empty"><i class="fa-solid fa-image"></i>Фото дәлел жоқ</div>'}
    </div>

    <div class="detail-section"><p class="section-title">Оқиға деректері</p></div>
    <div class="fields">
      <div class="field"><span class="field-key">Жұмыс күйі</span><span class="field-val"><span class="badge st-${attr(doc.status)}">${escapeHtml(STATUS_LABEL[doc.status] || doc.status)}</span></span></div>
      <div class="field"><span class="field-key">Мекенжай</span><span class="field-val">${escapeHtml(doc.display_address_text || doc.address_text || '—')}</span></div>
      <div class="field"><span class="field-key">Координата</span><span class="field-val">${Number.isFinite(lat) ? lat.toFixed(6) : '—'}, ${Number.isFinite(lon) ? lon.toFixed(6) : '—'} · <a href="${attr(mapLink)}" target="_blank" rel="noopener">Google</a>${gisLink ? ` · <a href="${attr(gisLink)}" target="_blank" rel="noopener">2GIS</a>` : ''} · <button class="text-btn" id="btn-copy-coords" type="button">көшіру</button></span></div>
      <div class="field"><span class="field-key">Геопозиция</span><span class="field-val">${doc.location_corrected ? '<span class="badge st-confirmed">Оператор нақтылады</span>' : escapeHtml(accuracyLabel(doc))} · ${escapeHtml(doc.gps_source || doc.geo_source || 'дереккөз белгісіз')}</span></div>
      <div class="field"><span class="field-key">Алдын ала қауіп</span><span class="field-val"><span class="badge ${attr(doc.severity || 'low')}">${escapeHtml(doc.severity_kk || doc.severity || 'Бағаланбаған')}</span></span></div>
      <div class="field"><span class="field-key">Модель сенімділігі</span><span class="field-val">${Math.round((doc.confidence || 0) * 100)}% · бұл ауырлық бағасы емес</span></div>
      ${doc.ai_verified ? `<div class="field"><span class="field-key">AI қосымша бағасы</span><span class="field-val"><span class="badge ai">${Math.round((doc.ai_confidence || 0) * 100)}%</span>${doc.ai_note ? `<br>${escapeHtml(doc.ai_note)}` : ''}</span></div>` : ''}
      ${doc.ai_size ? `<div class="field"><span class="field-key">Шамаланған өлшем</span><span class="field-val">${escapeHtml(doc.ai_size)}</span></div>` : ''}
      ${doc.ai_location ? `<div class="field"><span class="field-key">Жолдағы орны</span><span class="field-val">${escapeHtml(doc.ai_location)}</span></div>` : ''}
      ${doc.ai_action ? `<div class="field"><span class="field-key">Ұсынылатын шара</span><span class="field-val">${escapeHtml(doc.ai_action)}${doc.ai_urgency_days ? ` · ${Number(doc.ai_urgency_days)} күн` : ''}</span></div>` : ''}
      <div class="field"><span class="field-key">Анықталған уақыт</span><span class="field-val">${escapeHtml(doc.timestamp_human || shortDate(doc.timestamp))}</span></div>
      <div class="field"><span class="field-key">Жауапты бағыт</span><span class="field-val">${escapeHtml(doc.responsible_org || 'ЕКЦ 109 диспетчерлік кезегі')}</span></div>
      ${doc.external_ticket_id ? `<div class="field"><span class="field-key">109 өтінімі</span><span class="field-val"><b>${escapeHtml(doc.external_ticket_id)}</b>${doc.external_ticket_status ? ` · ${escapeHtml(doc.external_ticket_status)}` : ''}</span></div>` : ''}
    </div>

    <div class="detail-section"><p class="section-title">${officialTitle}</p></div>
    <div class="doc-text">${escapeHtml(doc.description_text || 'Мәтін қалыптастырылмаған.')}</div>

    <div class="detail-section"><p class="section-title">Әрекеттер тарихы</p></div>
    <div class="history">
      ${(doc.history || []).map((item) => `<div class="history-item">
        <span class="history-dot"></span>
        <span><b>${escapeHtml(STATUS_LABEL[item.status] || item.status)}</b>${item.note ? ` · ${escapeHtml(item.note)}` : ''}<br><small>${escapeHtml(shortDate(item.created_at))} · ${escapeHtml(item.actor || 'system')}</small></span>
      </div>`).join('') || '<div class="empty">Тарих жазбасы жоқ</div>'}
    </div>

    <div class="actions">
      ${!reviewed ? `<button class="btn btn-primary span-2" id="btn-confirm"><i class="fa-solid fa-paper-plane"></i> Тексеру және ЕКЦ 109-ға жіберу</button>
        <button class="btn btn-danger" id="btn-reject"><i class="fa-solid fa-xmark"></i> Жалған анықтау</button>` : ''}
      <button class="btn btn-ghost" id="btn-correct-location"><i class="fa-solid fa-location-crosshairs"></i> Орнын нақтылау</button>
      <button class="btn btn-ghost" id="btn-docx"><i class="fa-solid fa-file-word"></i> Word</button>
      <button class="btn btn-ghost" id="btn-print"><i class="fa-solid fa-print"></i> PDF</button>
      ${['sent', 'submitted', 'registered', 'assigned'].includes(doc.status) ? '<button class="btn btn-warning span-2" id="btn-progress"><i class="fa-solid fa-person-digging"></i> Жұмыс басталды деп белгілеу</button>' : ''}
      ${doc.status === 'in_progress' ? '<button class="btn btn-primary span-2" id="btn-after"><i class="fa-solid fa-camera"></i> Орындалғаннан кейінгі фотоны қосу</button><input type="file" id="after-file" accept="image/jpeg,image/png" hidden>' : ''}
      ${doc.status === 'repaired' ? '<button class="btn btn-primary" id="btn-close-work"><i class="fa-solid fa-circle-check"></i> Қайта тексерілді — жабу</button><button class="btn btn-danger" id="btn-reopen"><i class="fa-solid fa-rotate-left"></i> Ақау қалды — қайта ашу</button>' : ''}
      ${doc.status === 'reopened' ? '<button class="btn btn-warning span-2" id="btn-resume"><i class="fa-solid fa-person-digging"></i> Қайта жөндеуге беру</button>' : ''}
    </div>`;

  detail.classList.add('open');
  detail.setAttribute('aria-hidden', 'false');
  syncUrlEvent(eventId);
  $('detail-close').onclick = closeDetail;
  $('btn-copy-link').onclick = () => {
    const url = `${location.origin}/portal?event=${encodeURIComponent(doc.event_id)}`;
    copyText(url, 'Оқиға сілтемесі көшірілді');
  };
  if ($('btn-copy-coords')) $('btn-copy-coords').onclick = () => copyText(`${lat.toFixed(6)}, ${lon.toFixed(6)}`, 'Координата көшірілді');
  $('btn-docx').onclick = () => { location.href = `/api/documents/${encodeURIComponent(doc.event_id)}/docx`; };
  $('btn-print').onclick = () => { window.open(`/documents/${encodeURIComponent(doc.event_id)}/print`, '_blank', 'noopener'); };
  $('btn-correct-location').onclick = () => startLocationCorrection(doc.event_id);
  if ($('btn-confirm')) $('btn-confirm').onclick = () => confirmAndSend(doc.event_id);
  if ($('btn-reject')) $('btn-reject').onclick = () => openReject(doc.event_id);
  if ($('btn-progress')) $('btn-progress').onclick = () => setStatus(doc.event_id, 'in_progress', 'Жауапты орындаушы жұмысты бастады');
  if ($('btn-close-work')) $('btn-close-work').onclick = () => setStatus(doc.event_id, 'closed', 'Қайта тексеру нәтижесінде ақаудың жойылғаны расталды');
  if ($('btn-reopen')) $('btn-reopen').onclick = () => setStatus(doc.event_id, 'reopened', 'Қайта тексеру кезінде ақау толық жойылмағаны анықталды');
  if ($('btn-resume')) $('btn-resume').onclick = () => setStatus(doc.event_id, 'in_progress', 'Қайта жөндеу жұмысы басталды');
  if ($('btn-after')) {
    $('btn-after').onclick = () => $('after-file').click();
    $('after-file').onchange = (event) => uploadAfterPhoto(doc.event_id, event.target.files?.[0]);
  }

  detail.querySelectorAll('.media-tab').forEach((tab) => {
    tab.onclick = () => {
      detail.querySelectorAll('.media-tab').forEach((item) => item.classList.remove('active'));
      tab.classList.add('active');
      const box = $('media-box');
      if (tab.dataset.tab === 'video') box.innerHTML = `<video src="${mediaBase}/${attr(doc.video_file)}" controls autoplay muted loop></video>`;
      else if (tab.dataset.tab === 'after') box.innerHTML = `<img src="${mediaBase}/${attr(doc.after_photo_file)}" alt="Орындалғаннан кейінгі фото">`;
      else box.innerHTML = `<img src="${mediaBase}/${attr(doc.photo_file)}" alt="Ақаудың фотофиксациясы">`;
    };
  });

  const marker = state.markers[eventId];
  if (marker) state.map.panTo(marker.getLatLng(), { animate: true, duration: .35 });
}
window.openIncident = openDetail;

function syncUrlEvent(eventId) {
  const url = new URL(location.href);
  if (eventId) url.searchParams.set('event', eventId);
  else url.searchParams.delete('event');
  history.replaceState(null, '', url);
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

async function confirmAndSend(eventId) {
  if (!requireAuth()) return;
  if (!window.confirm('Оператор деректерді тексерді ме? Өтінім ЕКЦ 109 демо-арнасына жіберіледі.')) return;
  const response = await fetch(`/api/documents/${encodeURIComponent(eventId)}/confirm`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({}),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    return toast(error.detail || 'Өтінім жіберілмеді', 'error');
  }
  const result = await response.json();
  toast(result.delivery?.ticket_id ? `ЕКЦ 109 № ${result.delivery.ticket_id}` : 'Өтінім ЕКЦ 109-ға жіберілді', 'ok');
  await loadData();
  openDetail(eventId);
}

function openReject(eventId) {
  if (!requireAuth()) return;
  state.pendingRejectId = eventId;
  $('reject-note').value = '';
  modal('reject-modal', true);
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
  closeDetail();
  await loadData();
  toast('Оқиға жалған анықтау ретінде белгіленді', 'ok');
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
  $('filter-status').onchange = loadData;
  $('filter-type').onchange = loadData;
  $('filter-search').oninput = renderList;
  $('btn-refresh').onclick = async () => { await Promise.all([loadData(), loadZones()]); toast('Деректер жаңартылды', 'ok'); };
  $('btn-show-all').onclick = () => renderMarkers({ fit: true });
  $('btn-export').onclick = () => { location.href = `/api/export.csv?status=${encodeURIComponent($('filter-status').value)}`; };
  $('live-switch').onchange = (event) => { setLive(event.target.checked); toast(event.target.checked ? 'Тікелей жаңарту қосылды' : 'Тікелей жаңарту тоқтатылды'); };

  ['incidents', 'roadworks', 'closures'].forEach((name) => {
    $(`layer-${name}`).onclick = () => setLayerVisibility(name, !state.layerVisibility[name]);
  });

  $('btn-zone').onclick = () => { if (requireAuth()) { closeDetail(); setDrawing(true); } };
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

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      setDrawing(false);
      cancelLocationCorrection();
      closeDetail();
      document.querySelectorAll('.modal.open').forEach((item) => item.classList.remove('open'));
      return;
    }

    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName) || event.target.isContentEditable;
    if (typing || event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key === '/') {
      event.preventDefault();
      $('filter-search').focus();
      return;
    }

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const docs = state.visibleDocuments;
      if (!docs.length) return;
      event.preventDefault();
      const index = docs.findIndex((doc) => doc.event_id === state.selectedId);
      const next = event.key === 'ArrowDown'
        ? (index + 1) % docs.length
        : (index <= 0 ? docs.length - 1 : index - 1);
      openDetail(docs[next].event_id);
    }
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  initMap();
  bindEvents();
  updateOperatorLabel();
  await Promise.all([loadZones(), loadData()]);
  setLive(true);

  const linked = new URLSearchParams(location.search).get('event');
  if (linked) openDetail(linked);
});
