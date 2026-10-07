import { groupedBars, line, bars, hbars } from './charts.js';

// ---------- helpers ----------
const $ = sel => document.querySelector(sel);
function h(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(n.dataset, v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
  return n;
}
const pad = n => String(n).padStart(2, '0');
const clock = ts => { const d = new Date(ts); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const dayLabel = ts => new Date(ts).toLocaleDateString(undefined, { weekday: 'short' });
const dateTime = ts => new Date(ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
function dur(ms) {
  if (ms == null) return '—';
  const m = Math.round(ms / 60000);
  if (m < 1) return '<1m';
  if (m < 60) return `${m}m`;
  const hh = Math.floor(m / 60);
  return hh >= 24 ? `${Math.floor(hh / 24)}d ${hh % 24}h` : `${hh}h ${pad(m % 60)}m`;
}
async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts, headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
  return j;
}
const plateEl = (p, onclick) => h(onclick ? 'button' : 'span', { class: 'plate', onclick, title: onclick ? 'View history' : null }, p);
const width = el => Math.max(280, Math.floor(el.clientWidth || 600));

const OUTCOME = {
  entry: ['In', 'info'], exit: ['Out', 'good'], area: ['Moved', ''], entry_inferred: ['In (inferred)', 'warn'],
  duplicate: ['Repeat', ''], orphan_exit: ['Exit, no entry', 'warn'], unassigned: ['Camera unassigned', 'warn'],
};
const badge = outcome => { const [t, c] = OUTCOME[outcome] || [outcome, '']; return h('span', { class: `chip ${c}` }, t); };

// ---------- state / tabs ----------
let stats = null, reads = [], tab = 'overview';
const renderers = { overview: renderOverview, turnaround: renderTurnaround, cameras: renderCameras, vehicles: renderVehicles };

function showTab(name) {
  tab = name;
  document.querySelectorAll('#tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
  document.querySelectorAll('.tab').forEach(s => (s.hidden = s.id !== `tab-${name}`));
  history.replaceState(null, '', `#${name}`);
  render();
}
$('#tabs').addEventListener('click', e => { const b = e.target.closest('button[data-tab]'); if (b) showTab(b.dataset.tab); });

function render() {
  if (!stats) return;
  const root = $(`#tab-${tab}`);
  // don't rebuild a form the user is typing in
  if ((tab === 'cameras' || tab === 'vehicles') && root.contains(document.activeElement) && root.childElementCount) {
    if (tab === 'cameras') updateCameraStatus();
    return;
  }
  renderers[tab](root);
}

// ---------- shared pieces ----------
function kpi(label, value, unit, note, cls = '') {
  return h('div', { class: `card kpi ${cls}` }, h('h2', {}, label),
    h('div', { class: 'val' }, value, unit ? h('small', {}, unit) : null), h('div', { class: 'note' }, note || ' '));
}
function chartCard(title, sub, body, legend) {
  return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, title), sub ? h('div', { class: 'sub' }, sub) : null), legend || null), body);
}
const emptyMsg = t => h('div', { class: 'empty' }, t);

// ---------- overview ----------
function renderOverview(root) {
  const S = stats, ex = S.exceptions;
  const hourly = S.hourly;
  const flowTotal = hourly.reduce((a, x) => a + x.in + x.out, 0);
  const tipOf = x => `${clock(x.hour)}–${clock(x.hour + 3600000)}\nIn ${x.in} · Out ${x.out} · On site at end ${x.onSite}`;
  const wrap = h('div');
  const wrap2 = h('div');
  root.replaceChildren(
    h('div', { class: 'grid kpis' },
      kpi('On site now', S.onSite.length, 'vehicles', S.peakOnSite ? `Peak last 24h: ${S.peakOnSite}` : ''),
      kpi('Entries today', S.entriesToday, '', 'Since midnight'),
      kpi('Exits today', S.exitsToday, '', 'Seen leaving by an exit camera'),
      kpi('Avg turnaround today', dur(S.turnaroundToday.avg), '', S.turnaroundToday.count ? `Median ${dur(S.turnaroundToday.median)} · P90 ${dur(S.turnaroundToday.p90)}` : 'No completed visits yet'),
      kpi('Needs attention', ex.overstay + ex.orphanExits + ex.missedExits, '',
        `${ex.overstay} overstay (>${S.overstayHours}h) · ${ex.orphanExits} exit w/o entry · ${ex.missedExits} missed exit`,
        ex.overstay + ex.orphanExits + ex.missedExits ? 'alert' : '')),
    h('div', { class: 'grid two' },
      chartCard('Vehicles in & out', 'Per hour, last 24 hours', flowTotal ? wrap : emptyMsg('No traffic recorded in the last 24 hours.'),
        h('div', { class: 'legend' }, h('span', { class: 'in' }, h('i'), 'In'), h('span', { class: 'out' }, h('i'), 'Out'))),
      chartCard('Vehicles on site', 'At the end of each hour, last 24 hours', wrap2)),
    zonesCard(S),
    h('div', { class: 'grid two' }, onSiteCard(S), feedCard()));
  if (flowTotal) wrap.append(groupedBars(hourly.map(x => ({ label: clock(x.hour), tip: tipOf(x), a: x.in, b: x.out })), width(wrap) || 560));
  wrap2.append(line(hourly.map(x => ({ label: clock(x.hour), tip: `${clock(x.hour + 3600000)}\n${x.onSite} on site`, y: x.onSite })), width(wrap2) || 560, { labelEvery: 3 }));
  // charts need their real width: re-draw once laid out
  requestAnimationFrame(() => {
    if (flowTotal) wrap.replaceChildren(groupedBars(hourly.map(x => ({ label: clock(x.hour), tip: tipOf(x), a: x.in, b: x.out })), width(wrap)));
    wrap2.replaceChildren(line(hourly.map(x => ({ label: clock(x.hour), tip: `${clock(x.hour + 3600000)}\n${x.onSite} on site`, y: x.onSite })), width(wrap2), { labelEvery: 3 }));
  });
}

function zonesCard(S) {
  const body = S.zones.length
    ? h('div', { class: 'zones' }, S.zones.map(z => h('div', { class: 'zone' },
      h('div', { class: 'nm' }, z.zone), h('div', { class: 'n' }, z.occupancy),
      h('div', { class: 'd' }, z.avgDwellMs != null ? `avg dwell ${dur(z.avgDwellMs)} · ${z.visits} stays (7d)` : 'no stays yet'))))
    : emptyMsg('No area cameras yet. Assign a camera the “Area” role on the Cameras tab to track dwell time in zones.');
  return chartCard('Areas', 'Vehicles currently in each zone', body);
}

function onSiteCard(S) {
  const rows = S.onSite;
  const tbl = rows.length
    ? h('div', { class: 'tablewrap' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Plate', 'Arrived', 'On site', 'Where', ''].map(t => h('th', {}, t)))),
      h('tbody', {}, [...rows].sort((a, b) => a.entryTs - b.entryTs).map(v => h('tr', {},
        h('td', {}, plateEl(v.plate, () => openVehicle(v.plate))),
        h('td', {}, clock(v.entryTs), v.entryInferred ? h('span', { class: 'muted', title: 'Entry camera did not see this vehicle; first seen in an area' }, ' ~') : null),
        h('td', {}, dur(v.durationMs), ' ', v.overstay ? h('span', { class: 'chip warn' }, '⚠ Overstay') : null),
        h('td', {}, v.zone ? `${v.zone} · ${dur(Date.now() - v.zoneSince)}` : h('span', { class: 'muted' }, '—')),
        h('td', {}, h('button', { class: 'btn', title: 'Mark as left without an exit read', onclick: () => markLeft(v) }, 'Mark left')))))))
    : emptyMsg('Yard is empty.');
  return chartCard(`On site now (${rows.length})`, 'Oldest first', tbl);
}
async function markLeft(v) {
  if (!confirm(`Mark ${v.plate} as left? This closes the visit without an exit read, so it is excluded from turnaround stats.`)) return;
  await api(`/api/visits/${v.id}/close`, { method: 'POST', body: {} });
  refresh();
}

function feedCard() {
  const items = reads.length
    ? h('ul', { class: 'feed' }, reads.map(r => h('li', {},
      h('span', { class: 'muted' }, clock(r.ts)), plateEl(r.plate, () => openVehicle(r.plate)),
      h('span', { class: 'cam', title: r.cameraName || r.cameraId }, r.cameraName || r.cameraId, r.zone && !(r.cameraName || '').includes(r.zone) ? ` · ${r.zone}` : ''),
      h('span', { class: 'badge-col' }, badge(r.outcome)))))
    : emptyMsg('Waiting for the first plate read…');
  return chartCard('Live reads', 'Most recent first', items);
}

// ---------- turnaround ----------
function renderTurnaround(root) {
  const S = stats, T = S.turnaroundToday, W = S.turnaroundWeek;
  const histWrap = h('div'), dailyWrap = h('div'), zoneWrap = h('div');
  const dailyPts = () => S.daily.map(d => ({ label: dayLabel(d.day), y: d.avg == null ? null : Math.round(d.avg / 60000), tip: `${dateTime(d.day).split(',')[0]}\n${d.count ? `Avg ${dur(d.avg)} over ${d.count} visits` : 'No completed visits'}` }));
  const zoneRows = S.zones.filter(z => z.avgDwellMs != null).map(z => ({ label: z.zone, value: z.avgDwellMs, text: dur(z.avgDwellMs), tip: `${z.zone}\nAvg ${dur(z.avgDwellMs)} · longest ${dur(z.maxDwellMs)}\n${z.visits} stays in 7 days` }));
  root.replaceChildren(
    h('div', { class: 'grid kpis' },
      kpi('Average today', dur(T.avg), '', `${T.count} completed visits`),
      kpi('Median today', dur(T.median), '', 'Half of vehicles are faster'),
      kpi('P90 today', dur(T.p90), '', '9 in 10 are faster than this'),
      kpi('Longest today', dur(T.max), '', T.min != null ? `Fastest ${dur(T.min)}` : ''),
      kpi('Average, 7 days', dur(W.avg), '', `${W.count} completed visits`)),
    h('div', { class: 'grid two' },
      chartCard('Turnaround distribution', 'Completed visits, last 7 days', W.count ? histWrap : emptyMsg('No completed visits yet.')),
      chartCard('Average turnaround by day', 'Minutes, last 7 days', dailyWrap)),
    chartCard('Average dwell by area', 'Time between arriving in a zone and moving on, last 7 days',
      zoneRows.length ? zoneWrap : emptyMsg('No area stays recorded yet.')),
    h('p', { class: 'muted' }, 'Turnaround = exit-camera read minus entry-camera read. Visits closed manually or by a repeat entry are excluded because their true exit time is unknown.'));
  const draw = () => {
    if (W.count) histWrap.replaceChildren(bars(S.histogram.map(b => ({ label: b.label, value: b.count, tip: `${b.label}\n${b.count} visits` })), width(histWrap)));
    dailyWrap.replaceChildren(line(dailyPts(), width(dailyWrap), { fmtY: v => `${Math.round(v)}m`, directLabel: p => `${p.y}m` }));
    if (zoneRows.length) zoneWrap.replaceChildren(hbars(zoneRows, width(zoneWrap)));
  };
  requestAnimationFrame(draw);
}

// ---------- cameras ----------
const ROLE_LABEL = { entry: 'Entry', exit: 'Exit', area: 'Area', unassigned: 'Unassigned', ignore: 'Ignore' };
function camStatus(c) {
  if (!c.enabled) return ['Disabled', ''];
  if (!c.lastReadAt) return ['No reads yet', 'warn'];
  const age = Date.now() - c.lastReadAt;
  return age < 3600_000 * 2 ? [`Active · ${dur(age)} ago`, 'good'] : [`Quiet · ${dur(age)} ago`, 'warn'];
}
function updateCameraStatus() {
  for (const c of stats.cameras) {
    const el = document.querySelector(`[data-status="${CSS.escape(c.id)}"]`);
    if (el) { const [t, cls] = camStatus(c); el.className = `chip ${cls}`; el.textContent = t; }
  }
}
function renderCameras(root) {
  const cams = stats.cameras;
  const by = role => cams.filter(c => c.role === role);
  const stage = (title, list, empty) => h('div', { class: 'stage' }, h('h3', {}, title),
    list.length ? h('ul', {}, list.map(c => h('li', {}, c.zone && c.role === 'area' ? `${c.zone} (${c.name})` : c.name))) : h('div', { class: 'muted' }, empty));
  const msg = h('span', { class: 'msg', role: 'status' });
  const syncBtn = h('button', { class: 'btn primary', onclick: async () => {
    syncBtn.disabled = true; msg.textContent = 'Syncing…';
    try { const r = await api('/api/cameras/sync', { method: 'POST', body: {} }); msg.textContent = `Synced ${r.synced} cameras from Milestone.`; await refresh(true); }
    catch (e) { msg.textContent = e.message; } finally { syncBtn.disabled = false; }
  } }, 'Sync cameras from Milestone');
  if (!stats.milestone.configured) { syncBtn.disabled = true; syncBtn.title = 'Set MILESTONE_BASE_URL, MILESTONE_USERNAME and MILESTONE_PASSWORD'; }

  const row = c => {
    const zone = h('input', { type: 'text', value: c.zone || '', placeholder: c.role === 'area' ? 'Zone name (required)' : 'Optional label', 'aria-label': `Zone for ${c.name}`, maxlength: 60 });
    const sel = h('select', { 'aria-label': `Role for ${c.name}` }, Object.entries(ROLE_LABEL).map(([v, l]) => h('option', { value: v, selected: v === c.role }, l)));
    const status = h('span', { class: 'chip', 'data-status': c.id });
    const save = async () => {
      try { await api(`/api/cameras/${encodeURIComponent(c.id)}`, { method: 'PUT', body: { role: sel.value, zone: zone.value } }); msg.textContent = `Saved “${c.name}”.`; await refresh(true); }
      catch (e) { msg.textContent = e.message; }
    };
    sel.addEventListener('change', () => { zone.placeholder = sel.value === 'area' ? 'Zone name (required)' : 'Optional label'; if (sel.value !== 'area' || zone.value.trim()) save(); else zone.focus(); });
    zone.addEventListener('change', save);
    const [t, cls] = camStatus(c); status.textContent = t; status.classList.add(...(cls ? [cls] : []));
    return h('tr', {}, h('td', {}, c.name, h('div', { class: 'muted' }, c.id.length > 24 ? c.id.slice(0, 8) + '…' : c.id)), h('td', {}, sel), h('td', {}, zone), h('td', {}, status));
  };

  root.replaceChildren(
    chartCard('Site layout', 'How vehicles move through the site, based on the roles below',
      h('div', { class: 'flow' }, stage('Entry', by('entry'), 'No entry camera'), h('span', { class: 'arrow' }, '→'),
        stage('Areas', by('area'), 'No area cameras'), h('span', { class: 'arrow' }, '→'), stage('Exit', by('exit'), 'No exit camera'))),
    h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, 'Allocate cameras'), h('div', { class: 'sub' }, 'New cameras appear here as “Unassigned” and are ignored until you give them a role.')),
        h('div', { class: 'toolbar' }, msg, syncBtn)),
      cams.length ? h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Camera', 'Role', 'Zone / label', 'Status'].map(t => h('th', {}, t)))), h('tbody', {}, cams.map(row))))
        : emptyMsg('No cameras yet. Sync from Milestone, or send a plate read to /api/ingest and the camera will be registered automatically.')),
    stats.milestone.lastError ? h('div', { class: 'card' }, h('span', { class: 'chip bad' }, '✕ Milestone error'), ' ', stats.milestone.lastError) : h('span'));
}

// ---------- vehicles ----------
let searchPlate = '';
function renderVehicles(root) {
  const out = h('div');
  const input = h('input', { type: 'search', placeholder: 'Search plate, e.g. CA123456', value: searchPlate, 'aria-label': 'Plate number', maxlength: 12 });
  const go = () => { searchPlate = input.value.toUpperCase().replace(/[^A-Z0-9]/g, ''); if (searchPlate) loadVehicle(searchPlate, out); };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  const now = Date.now();
  root.replaceChildren(
    h('div', { class: 'card' }, h('div', { class: 'toolbar' }, input, h('button', { class: 'btn primary', onclick: go }, 'Look up'),
      h('a', { class: 'btn', href: `/api/export.csv?from=${now - 30 * 864e5}&to=${now}`, download: '' }, 'Export last 30 days (CSV)'))),
    stats.frequent.length ? chartCard('Repeat visitors', 'Most visits in the last 7 days',
      h('div', { class: 'toolbar' }, stats.frequent.map(f => h('span', {}, plateEl(f.plate, () => openVehicle(f.plate)), ` ×${f.visits}`)))) : h('span'),
    out);
  if (searchPlate) loadVehicle(searchPlate, out);
}
function openVehicle(plate) { searchPlate = plate; showTab('vehicles'); }
async function loadVehicle(plate, out) {
  out.replaceChildren(emptyMsg('Loading…'));
  try {
    const visits = await api(`/api/vehicles/${encodeURIComponent(plate)}`);
    out.replaceChildren(chartCard(`History for ${plate}`, `${visits.length} visits`,
      visits.length ? h('div', {}, visits.map(v => h('div', { class: 'visit' },
        h('div', {}, h('strong', {}, dateTime(v.entryTs)), ' → ', v.exitTs ? dateTime(v.exitTs) : h('span', { class: 'chip info' }, 'On site'), ' ',
          v.status === 'closed' ? h('span', { class: 'chip good' }, `✓ ${dur(v.exitTs - v.entryTs)}`) : v.status === 'missed_exit' ? h('span', { class: 'chip warn' }, '⚠ Exit not seen') : null,
          v.entryInferred ? h('span', { class: 'chip warn' }, 'entry inferred') : null),
        v.stays.length ? h('div', { class: 'muted' }, v.stays.map(s => `${s.zone} ${clock(s.startTs)}${s.endTs ? ' (' + dur(s.endTs - s.startTs) + ')' : ''}`).join('  →  ')) : null)))
        : emptyMsg('No visits found for that plate.')));
  } catch (e) { out.replaceChildren(emptyMsg(e.message)); }
}

// ---------- data loop ----------
let inflight = false, queued = false;
async function refresh(force = false) {
  if (inflight) { queued = true; return; }
  inflight = true;
  try {
    [stats, reads] = await Promise.all([api('/api/stats'), api('/api/reads?limit=30')]);
    $('#demo-badge').hidden = !stats.demo;
    $('#updated').textContent = new Date().toLocaleTimeString();
    if (force && tab === 'cameras') $('#tab-cameras').replaceChildren(); // allow rebuild after an edit
    render();
  } catch (e) { $('#updated').textContent = `failed (${e.message})`; }
  finally { inflight = false; if (queued) { queued = false; refresh(); } }
}

function connect() {
  const live = $('#live');
  const set = (cls, t) => { live.className = `chip ${cls}`; live.lastChild.textContent = t; };
  const es = new EventSource('/api/stream');
  let t = null;
  es.addEventListener('change', () => { clearTimeout(t); t = setTimeout(refresh, 300); });
  es.onopen = () => set('good', 'Live');
  es.onerror = () => set('bad', 'Reconnecting…');
}

// tooltip (event delegation over any [data-tip])
const tip = $('#tip');
document.addEventListener('mouseover', e => {
  const t = e.target.closest?.('[data-tip]');
  if (!t) return;
  tip.textContent = t.dataset.tip || t.getAttribute('data-tip'); tip.hidden = false;
});
document.addEventListener('mousemove', e => {
  if (tip.hidden) return;
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`; tip.style.top = `${e.clientY + 16 + tip.offsetHeight > innerHeight ? e.clientY - tip.offsetHeight - 10 : e.clientY + 16}px`;
});
document.addEventListener('mouseout', e => { if (e.target.closest?.('[data-tip]')) tip.hidden = true; });

// theme
const root = document.documentElement;
try { const t = localStorage.getItem('theme'); if (t) root.dataset.theme = t; } catch {}
$('#theme').addEventListener('click', () => {
  const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  root.dataset.theme = dark ? 'light' : 'dark';
  try { localStorage.setItem('theme', root.dataset.theme); } catch {}
});

let rz; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (tab === 'overview' || tab === 'turnaround') render(); }, 200); });

const initial = location.hash.slice(1);
if (renderers[initial]) showTab(initial);
refresh(); connect();
setInterval(refresh, 30_000);
