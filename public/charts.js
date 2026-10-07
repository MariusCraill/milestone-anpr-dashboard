// Tiny dependency-free SVG charts. Colours come from CSS classes so light/dark just works.
const NS = 'http://www.w3.org/2000/svg';

export function s(tag, attrs = {}, ...kids) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  for (const k of kids) if (k != null) n.append(k);
  return n;
}
const text = (x, y, str, attrs = {}) => { const t = s('text', { x, y, ...attrs }); t.textContent = str; return t; };

function niceMax(v) {
  if (v <= 4) return 4;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
  return 10 * p;
}

function frame(width, height, m, max, fmt = String) {
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${width} ${height}`, role: 'img' });
  const plotH = height - m.t - m.b;
  const y = v => m.t + plotH - (v / max) * plotH;
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    svg.append(s('line', { class: i === 0 ? 'axis-line' : 'grid-line', x1: m.l, x2: width - m.r, y1: y(v), y2: y(v) }));
    svg.append(text(m.l - 6, y(v) + 4, fmt(v), { 'text-anchor': 'end' }));
  }
  return { svg, y, plotW: width - m.l - m.r, plotH, base: m.t + plotH };
}

// bar with rounded top corners, flat on the baseline
function barPath(x, yTop, w, base, r = 3) {
  const h = base - yTop;
  if (h <= 0) return '';
  r = Math.min(r, h, w / 2);
  return `M${x},${base}V${yTop + r}Q${x},${yTop} ${x + r},${yTop}H${x + w - r}Q${x + w},${yTop} ${x + w},${yTop + r}V${base}Z`;
}

/** Grouped bars: rows = [{label, tip, a, b}] drawn with classes bar-in / bar-out. */
export function groupedBars(rows, width, { labelEvery = 3 } = {}) {
  const m = { l: 30, r: 6, t: 8, b: 22 }, height = 210;
  const max = niceMax(Math.max(1, ...rows.map(r => Math.max(r.a, r.b))));
  const { svg, y, plotW, base } = frame(width, height, m, max);
  const cw = plotW / rows.length, bw = Math.max(3, Math.min(14, cw * 0.38));
  rows.forEach((r, i) => {
    const cx = m.l + cw * i + cw / 2;
    svg.append(s('path', { class: 'bar-in', d: barPath(cx - bw - 1, y(r.a), bw, base) }));
    svg.append(s('path', { class: 'bar-out', d: barPath(cx + 1, y(r.b), bw, base) }));
    if (i % labelEvery === 0) svg.append(text(cx, height - 6, r.label, { 'text-anchor': 'middle' }));
    svg.append(s('rect', { class: 'hit', x: m.l + cw * i, y: m.t, width: cw, height: base - m.t, 'data-tip': r.tip }));
  });
  return svg;
}

/** Line (+ optional area) with hover dots. pts = [{label, tip, y|null}]. */
export function line(pts, width, { fmtY = String, labelEvery = 1, area = true, directLabel = null } = {}) {
  const m = { l: 36, r: 14, t: 12, b: 22 }, height = 190;
  const max = niceMax(Math.max(1, ...pts.map(p => p.y ?? 0)));
  const { svg, y, plotW, base } = frame(width, height, m, max, fmtY);
  const step = plotW / Math.max(1, pts.length - 1);
  const x = i => m.l + (pts.length === 1 ? plotW / 2 : step * i);
  const segs = [];
  let cur = [];
  pts.forEach((p, i) => { if (p.y == null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([x(i), y(p.y)]); });
  if (cur.length) segs.push(cur);
  for (const sg of segs) {
    const d = sg.map(([px, py], i) => `${i ? 'L' : 'M'}${px},${py}`).join('');
    if (area && sg.length > 1) svg.append(s('path', { class: 'area-s1', d: `${d}L${sg.at(-1)[0]},${base}L${sg[0][0]},${base}Z` }));
    svg.append(s('path', { class: 'line-s1', d }));
    if (sg.length === 1) svg.append(s('circle', { class: 'pt', cx: sg[0][0], cy: sg[0][1], r: 4 }));
  }
  pts.forEach((p, i) => {
    if (i % labelEvery === 0) svg.append(text(x(i), height - 6, p.label, { 'text-anchor': 'middle' }));
  });
  const hoverLine = s('line', { class: 'hover-line', y1: m.t, y2: base, visibility: 'hidden' });
  const hoverDot = s('circle', { class: 'pt', r: 4.5, visibility: 'hidden' });
  svg.append(hoverLine, hoverDot);
  pts.forEach((p, i) => {
    const hit = s('rect', { class: 'hit', x: x(i) - step / 2, y: m.t, width: Math.max(step, 12), height: base - m.t, 'data-tip': p.tip });
    hit.addEventListener('mouseenter', () => {
      hoverLine.setAttribute('x1', x(i)); hoverLine.setAttribute('x2', x(i)); hoverLine.setAttribute('visibility', 'visible');
      if (p.y != null) { hoverDot.setAttribute('cx', x(i)); hoverDot.setAttribute('cy', y(p.y)); hoverDot.setAttribute('visibility', 'visible'); }
    });
    hit.addEventListener('mouseleave', () => { hoverLine.setAttribute('visibility', 'hidden'); hoverDot.setAttribute('visibility', 'hidden'); });
    svg.append(hit);
  });
  if (directLabel) {
    const li = pts.map(p => p.y).findLastIndex(v => v != null);
    if (li >= 0) svg.append(text(x(li), y(pts[li].y) - 9, directLabel(pts[li]), { 'text-anchor': 'end', class: 'val-label' }));
  }
  return svg;
}

/** Single-series vertical bars with a count on top of each. rows = [{label, value, tip}] */
export function bars(rows, width) {
  const m = { l: 30, r: 6, t: 18, b: 22 }, height = 190;
  const max = niceMax(Math.max(1, ...rows.map(r => r.value)));
  const { svg, y, plotW, base } = frame(width, height, m, max);
  const cw = plotW / rows.length, bw = Math.min(40, cw * 0.62);
  rows.forEach((r, i) => {
    const cx = m.l + cw * i + cw / 2;
    svg.append(s('path', { class: 'bar-one', d: barPath(cx - bw / 2, y(r.value), bw, base, 4) }));
    if (r.value > 0) svg.append(text(cx, y(r.value) - 5, r.value, { 'text-anchor': 'middle', class: 'val-label' }));
    svg.append(text(cx, height - 6, r.label, { 'text-anchor': 'middle' }));
    svg.append(s('rect', { class: 'hit', x: m.l + cw * i, y: m.t, width: cw, height: base - m.t, 'data-tip': r.tip }));
  });
  return svg;
}

/** Horizontal bars: rows = [{label, value, text, tip}] */
export function hbars(rows, width) {
  const labelW = Math.min(130, width * 0.34), rowH = 30, valW = 70;
  const svg = s('svg', { class: 'chart', viewBox: `0 0 ${width} ${rows.length * rowH}`, role: 'img' });
  const max = Math.max(1, ...rows.map(r => r.value));
  const trackW = width - labelW - valW - 8;
  rows.forEach((r, i) => {
    const yy = i * rowH;
    svg.append(text(0, yy + 19, r.label, { class: 'val-label' }));
    svg.append(s('rect', { class: 'hbar-track', x: labelW, y: yy + 7, width: trackW, height: 14, rx: 7 }));
    if (r.value > 0) svg.append(s('rect', { class: 'bar-one', x: labelW, y: yy + 7, width: Math.max(8, (r.value / max) * trackW), height: 14, rx: 7 }));
    svg.append(text(width, yy + 19, r.text, { 'text-anchor': 'end', class: 'val-label' }));
    svg.append(s('rect', { class: 'hit', x: 0, y: yy, width, height: rowH, 'data-tip': r.tip }));
  });
  return svg;
}
