// charts.js — Canvas 2D renderers for omarchy-data-visualization.
// Shared by the QML panel (`import "charts.js" as Charts`) and the browser dev page (<script src>).
// Every draw function: (ctx, w, h, snap, key, P) → hits[] for tooltips ({x,y,w,h,text}).
// P (palette): { bg, fg, accent, you, themes: [..], kinds: {name: color}, font }.
// Keep to the Context2D subset QtQuick supports (no setLineDash, no roundRect, plain CSS fonts).

var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
var ACT_ALPHA = [0.88, 0.66, 0.48, 0.33, 0.21, 0.11, 0.06];

function hex2rgb(hex) {
  var h = String(hex).replace("#", "");
  if (h.length === 8) h = h.slice(2);          // QML colour strings can be #AARRGGBB
  var n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgba(hex, a) { var c = hex2rgb(hex); return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")"; }
function mix(h1, h2, t) {
  var a = hex2rgb(h1), b = hex2rgb(h2);
  return "rgb(" + Math.round(a[0] * (1 - t) + b[0] * t) + "," + Math.round(a[1] * (1 - t) + b[1] * t) + "," + Math.round(a[2] * (1 - t) + b[2] * t) + ")";
}
function font(P, px, bold) { return (bold ? "bold " : "") + (px * (P.scale || 1)).toFixed(1) + "px \"" + P.font + "\""; }
function K(P, v) { return v * (P.scale || 1); }

function fmtH(sec) {
  var h = sec / 3600;
  if (h >= 99.5) return Math.round(h) + "h";
  if (h >= 1) return h.toFixed(1) + "h";
  var m = sec / 60;
  return (m >= 1 ? Math.round(m) : m.toFixed(1)) + "m";
}
function fmtN(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(Math.round(n));
}
function fmtCost(c) {
  if (c < 100) return "$" + c.toFixed(2);
  return "$" + String(Math.round(c)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
function pct(a, b) { return b > 0 ? Math.round((a - b) / b * 100) : null; }
function sum(a) { var s = 0; for (var i = 0; i < a.length; i++) s += a[i]; return s; }
function tsLabel(ts, key) {
  var d = new Date(ts * 1000);
  if (key === "15m" || key === "1h" || key === "6h") return (d.getHours() < 10 ? "0" : "") + d.getHours() + ":" + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
  if (key === "24h") return (d.getHours() < 10 ? "0" : "") + d.getHours();
  if (key === "7d") return (d.getMonth() + 1) + "/" + d.getDate();
  if (key === "1mo") return (d.getMonth() + 1) + "/" + d.getDate();
  return MONTHS[d.getMonth()];
}
function themeColor(P, snap, name) {
  var i = snap.themes.indexOf(name);
  return i < 0 ? P.fg : P.themes[i % P.themes.length];
}
function halo(ctx, P, t, x, y, col) {
  ctx.save(); ctx.lineJoin = "round"; ctx.lineWidth = 3; ctx.strokeStyle = rgba(P.bg, 0.85);
  ctx.strokeText(t, x, y); ctx.fillStyle = col; ctx.fillText(t, x, y); ctx.restore();
}
function empty(ctx, w, h, P, msg) {
  ctx.font = font(P, 11); ctx.fillStyle = rgba(P.fg, 0.45); ctx.textAlign = "center";
  ctx.fillText(msg || "no agent activity in this period", w / 2, h / 2); ctx.textAlign = "left";
  return [];
}
function ellipsize(ctx, s, maxW) {
  if (ctx.measureText(s).width <= maxW) return s;
  while (s.length > 2 && ctx.measureText(s + "…").width > maxW) s = s.slice(0, -1);
  return s + "…";
}

// ───────────────────────── heat map (bivariate: agents vs you) ─────────────────────────
function heat(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], H = R.heat, hits = [];
  var rows = H.agent.length, cols = H.agent[0] ? H.agent[0].length : 0;
  var max = 0, i, j;
  for (i = 0; i < rows; i++) for (j = 0; j < cols; j++) max = Math.max(max, H.agent[i][j]);
  var legendH = K(P, 34), padL = K(P, H.mode === "theme-hour" || H.mode === "theme-time" ? 58 : 40), padT = 2, labelH = K(P, 13);
  var cw = (w - padL) / cols, ch = (h - legendH - padT - labelH) / rows;
  ctx.font = font(P, 10);
  for (i = 0; i < rows; i++) {
    var rl = H.rows[i];
    var showRow = H.mode === "week-hour" ? i % 6 === 0 : H.mode === "day-hour" && rows > 10 ? (i % 5 === 0 || i === rows - 1) : true;
    if (showRow) {
      ctx.fillStyle = rgba(P.fg, 0.6);
      ctx.fillText(ellipsize(ctx, H.mode === "week-hour" ? rl + ":00" : rl, padL - 6), 0, padT + i * ch + ch / 2 + K(P, 3.5));
    }
    for (j = 0; j < cols; j++) {
      var a = H.agent[i][j], y = H.you[i][j], px = padL + j * cw, py = padT + i * ch;
      if (a > 1) {
        var f = y / (a + y);                           // share of the time that was you, not the agents
        var s = Math.max(0, Math.min(1, (f - 0.05) / 0.5));
        ctx.globalAlpha = 0.1 + 0.9 * Math.pow(a / max, 0.6);
        ctx.fillStyle = mix(P.accent, P.you, s);
      } else { ctx.globalAlpha = 1; ctx.fillStyle = rgba(P.fg, 0.045); }
      ctx.fillRect(px + 0.5, py + 0.5, Math.max(1, cw - 1.5), Math.max(1, ch - 1.5));
      ctx.globalAlpha = 1;
      var lab = H.mode === "week-hour" ? "week of " + H.cols[j] + " · " + rl + ":00" : H.mode === "theme-hour" ? rl + " · " + H.cols[j] + ":00" : H.mode === "theme-time" ? rl + " · " + H.cols[j] : rl + " · " + H.cols[j] + ":00";
      hits.push({ x: px, y: py, w: cw, h: ch, text: lab + "\nagents " + fmtH(a) + " · you " + fmtH(y) });
    }
  }
  // column labels
  ctx.fillStyle = rgba(P.fg, 0.55);
  var ly = padT + rows * ch + K(P, 11), lastM = "";
  for (j = 0; j < cols; j++) {
    var c = H.cols[j];
    if (H.mode === "week-hour") { if (c !== lastM) { if (lastM !== "" && j < cols - 2) ctx.fillText(c, padL + j * cw, ly); lastM = c; } }
    else if (H.mode === "theme-time") { if (j % Math.max(1, Math.round(cols / 4)) === 0) ctx.fillText(c, padL + j * cw, ly); }
    else if (Number(c) % 6 === 0) ctx.fillText(c, padL + j * cw, ly);
  }
  // legend: 5 intensity × 3 balance
  var lx = padL, lgy = h - K(P, 24), sw = K(P, 11), sh = K(P, 7), xx, yy;
  for (yy = 0; yy < 3; yy++) for (xx = 0; xx < 5; xx++) {
    ctx.globalAlpha = 0.1 + 0.9 * Math.pow((xx + 1) / 5, 0.6); ctx.fillStyle = mix(P.accent, P.you, yy / 2);
    ctx.fillRect(lx + xx * sw, lgy + (2 - yy) * sh, sw - 1, sh - 1);
  }
  ctx.globalAlpha = 1; ctx.fillStyle = rgba(P.fg, 0.7);
  ctx.fillText("more busy →", lx + 5 * sw + K(P, 8), lgy + K(P, 20));
  ctx.fillStyle = P.you; ctx.fillText("● you in the loop", lx + 5 * sw + K(P, 100), lgy + K(P, 6));
  ctx.fillStyle = P.accent; ctx.fillText("● agents on their own", lx + 5 * sw + K(P, 100), lgy + K(P, 20));
  if (max === 0) empty(ctx, w, h - legendH, P);
  return hits;
}

// ───────────────────────── sankey: tokens → agent → theme → activity ─────────────────────────
function sankey(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], S = R.sankey, hits = [], i, j;
  var kindV = S.kt.map(function (r) { return sum(r); });
  var total = sum(kindV);
  if (total <= 0) return empty(ctx, w, h, P);
  var themeV = snap.themes.map(function (_, ti) { var s = 0; for (var k = 0; k < S.kt.length; k++) s += S.kt[k][ti]; return s; });
  var actV = snap.activities.map(function (_, ai) { var s = 0; for (var t = 0; t < S.ta.length; t++) s += S.ta[t][ai]; return s; });
  function col(nodes) { return nodes.filter(function (n) { return n.v > total * 0.002; }); }
  var cols = [
    [{ name: "tokens", v: total, col: P.fg }],
    col(snap.kinds.map(function (k, i) { return { name: k, v: kindV[i], col: P.kinds[k] || P.fg, idx: i }; })),
    col(snap.themes.map(function (t, i) { return { name: t, v: themeV[i], col: themeColor(P, snap, t), idx: i }; })),
    col(snap.activities.map(function (a, i) { return { name: a, v: actV[i], col: rgba(P.fg, ACT_ALPHA[i] * 0.9 + 0.1), idx: i }; })),
  ];
  var padL = K(P, 58), padR = K(P, 104), nodeW = K(P, 7), gap = K(P, 7), top = 4, bot = h - 4;
  var colX = cols.map(function (_, i) { return padL + i * (w - padL - padR - nodeW) / (cols.length - 1); });
  // absolute scale: the busiest window of this length (S.peak) fills the height; quieter windows are drawn smaller
  var ref = Math.max(total, S.peak || 0), scale = Infinity;
  cols.forEach(function (c) { scale = Math.min(scale, (bot - top - gap * (c.length - 1)) / Math.max(ref, sum(c.map(function (n) { return n.v; })))); });
  cols.forEach(function (c, ci) {
    var H2 = sum(c.map(function (n) { return n.v * scale; })) + gap * (c.length - 1), y = top + (bot - top - H2) / 2;
    c.forEach(function (n) { n.x = colX[ci]; n.y = y; n.h = n.v * scale; n.so = 0; n.to = 0; y += n.h + gap; });
  });
  function find(ci, idx) { for (var k = 0; k < cols[ci].length; k++) if (cols[ci][k].idx === idx) return cols[ci][k]; return null; }
  var links = [];
  cols[1].forEach(function (k) { links.push({ s: cols[0][0], t: k, v: k.v, col: k.col, solid: true }); });
  for (i = 0; i < S.kt.length; i++) for (j = 0; j < snap.themes.length; j++) {
    var s1 = find(1, i), t1 = find(2, j); if (s1 && t1 && S.kt[i][j] > 0) links.push({ s: s1, t: t1, v: S.kt[i][j], col: t1.col, solid: true });
  }
  for (i = 0; i < snap.themes.length; i++) for (j = 0; j < snap.activities.length; j++) {
    var s2 = find(2, i), t2 = find(3, j); if (s2 && t2 && S.ta[i][j] > 0) links.push({ s: s2, t: t2, v: S.ta[i][j], col: s2.col, solid: true });
  }
  links.forEach(function (L) {
    var lh = L.v * scale, x0 = L.s.x + nodeW, x1 = L.t.x, y0 = L.s.y + L.s.so, y1 = L.t.y + L.t.to;
    L.s.so += lh; L.t.to += lh;
    var mx = (x0 + x1) / 2;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.bezierCurveTo(mx, y0, mx, y1, x1, y1); ctx.lineTo(x1, y1 + lh);
    ctx.bezierCurveTo(mx, y1 + lh, mx, y0 + lh, x0, y0 + lh); ctx.closePath();
    ctx.fillStyle = L.col.charAt(0) === "#" ? rgba(L.col, 0.28) : rgba(P.fg, 0.14); ctx.fill();
  });
  cols.forEach(function (c, ci) {
    c.forEach(function (n) {
      ctx.fillStyle = n.col; ctx.fillRect(n.x, n.y, nodeW, Math.max(1.5, n.h));
      var val = fmtN(n.v) + (ci === 0 ? "" : " · " + Math.round(n.v / total * 100) + "%");
      var lx = ci === 0 ? n.x - 6 : n.x + nodeW + 5, cy = n.y + n.h / 2;
      ctx.textAlign = ci === 0 ? "right" : "left";
      ctx.font = font(P, 10.5, true);
      if (n.h > K(P, 16) || ci === 0) { halo(ctx, P, n.name, lx, cy - 1, P.fg); ctx.font = font(P, 10); halo(ctx, P, val, lx, cy + K(P, 11), rgba(P.fg, 0.6)); }
      else if (n.h > 4) halo(ctx, P, n.name, lx, cy + K(P, 3.5), P.fg);
      ctx.textAlign = "left";
      hits.push({ x: n.x - 3, y: n.y, w: nodeW + 6, h: Math.max(5, n.h), text: n.name + "\n" + fmtN(n.v) + " tokens · " + (n.v / total * 100).toFixed(1) + "%" + (ci === 0 && S.peak ? "\n" + Math.round(total / S.peak * 100) + "% of the busiest window (" + fmtN(S.peak) + ")" : "") });
    });
  });
  return hits;
}

// ───────────────────────── stream graph: busy time per theme ─────────────────────────
function smoothPath(ctx, pts, move) {
  for (var i = 0; i < pts.length; i++) {
    var p = pts[i];
    if (i === 0) { if (move) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); continue; }
    var q = pts[i - 1], mx = (q[0] + p[0]) / 2;
    ctx.bezierCurveTo(mx, q[1], mx, p[1], p[0], p[1]);
  }
}
function stream(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], hits = [], nb = R.buckets.length, legendH = K(P, 20);
  var sm = R.stream.map(function (L) { return L.map(function (v, i) { return (L[Math.max(0, i - 1)] + 2 * v + L[Math.min(L.length - 1, i + 1)]) / 4; }); });
  var tot = R.buckets.map(function (_, b) { var s = 0; sm.forEach(function (L) { s += L[b]; }); return s; });
  var maxT = Math.max.apply(null, tot);
  var chartH = h - legendH, padB = K(P, 14), mid = (chartH - padB) / 2, sy = maxT > 0 ? (chartH - padB - 8) / maxT : 0;
  function x(b) { return 1 + b / (nb - 1) * (w - 2); }
  ctx.strokeStyle = rgba(P.fg, 0.12); ctx.lineWidth = 1; ctx.font = font(P, 10);
  var step = Math.ceil(nb / 7);
  for (var b = 0; b < nb; b += step) {
    ctx.beginPath(); ctx.moveTo(x(b), 0); ctx.lineTo(x(b), chartH - padB); ctx.stroke();
    ctx.fillStyle = rgba(P.fg, 0.55); ctx.fillText(tsLabel(R.buckets[b], key), x(b) + 3, chartH - 3);
  }
  if (maxT <= 0) empty(ctx, w, chartH, P);
  var base = tot.map(function (t) { return mid - t * sy / 2; });
  var placed = [];
  function free(x0, y0, x1, y1) {
    for (var q = 0; q < placed.length; q++) { var r = placed[q]; if (x0 < r[2] && x1 > r[0] && y0 < r[3] && y1 > r[1]) return false; }
    placed.push([x0, y0, x1, y1]); return true;
  }
  sm.forEach(function (L, li) {
    var top = base, bot = base.map(function (y, b) { return y + L[b] * sy; });
    var colr = P.themes[li % P.themes.length];
    if (sum(L) > 0) {
      ctx.beginPath(); smoothPath(ctx, top.map(function (y, b) { return [x(b), y]; }), true);
      smoothPath(ctx, bot.map(function (y, b) { return [x(b), y]; }).reverse(), false); ctx.closePath();
      ctx.fillStyle = rgba(colr, 0.55); ctx.fill(); ctx.strokeStyle = P.bg; ctx.lineWidth = 1; ctx.stroke();
      var bi = -1, lo = Math.floor(nb * 0.1), hi = Math.ceil(nb * 0.9);
      for (var k = lo; k < hi; k++) if (bi < 0 || L[k] > L[bi]) bi = k;
      if (bi >= 0 && L[bi] * sy > K(P, 12)) {
        ctx.font = font(P, 10.5, true);
        var name = snap.themes[li], tw = ctx.measureText(name).width;
        var lx0 = Math.min(Math.max(x(bi) - tw / 2, 2), w - tw - 2), ly0 = (top[bi] + bot[bi]) / 2 + K(P, 4);
        if (free(lx0 - 2, ly0 - K(P, 11), lx0 + tw + 2, ly0 + 3)) halo(ctx, P, name, lx0, ly0, P.fg);
        ctx.font = font(P, 10);
      }
      for (var b2 = 0; b2 < nb; b2++) if (R.stream[li][b2] > 0)
        hits.push({ x: x(b2) - w / nb / 2, y: top[b2], w: w / nb, h: Math.max(3, bot[b2] - top[b2]), text: snap.themes[li] + " · " + tsLabel(R.buckets[b2], key) + "\n" + fmtH(R.stream[li][b2]) + " busy" });
    }
    base = bot;
  });
  // legend
  var lx = 0, lyy = h - 5;
  ctx.font = font(P, 10.5);
  snap.themes.forEach(function (t, i) {
    var v = sum(R.stream[i]); if (v <= 0) return;
    var label = t + " " + fmtH(v), tw = ctx.measureText(label).width;
    if (lx + tw + K(P, 20) > w) return;
    ctx.fillStyle = P.themes[i % P.themes.length]; ctx.fillRect(lx, lyy - K(P, 4), K(P, 12), 3);
    ctx.fillStyle = rgba(P.fg, 0.8); ctx.fillText(label, lx + K(P, 17), lyy);
    lx += tw + K(P, 30);
  });
  return hits;
}

// ───────────────────────── topic map: two-level circle pack ─────────────────────────
function relax(cs, pad, gx, gy, iters) {
  var golden = Math.PI * (3 - Math.sqrt(5)), i, j, it;
  cs.forEach(function (c, i) { var r = 6 * Math.sqrt(i + 1); c.x = Math.cos(i * golden) * r * (1 + c.r / 10); c.y = Math.sin(i * golden) * r * (1 + c.r / 10); });
  for (it = 0; it < iters + 80; it++) {
    if (it < iters) cs.forEach(function (c) { c.x *= 1 - gx; c.y *= 1 - gy; });
    for (i = 0; i < cs.length; i++) for (j = i + 1; j < cs.length; j++) {
      var a = cs[i], b = cs[j], dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01, o = a.r + b.r + pad - d;
      if (o > 0) { dx /= d; dy /= d; var wa = b.r / (a.r + b.r), wb = a.r / (a.r + b.r); a.x -= dx * o * wa; a.y -= dy * o * wa; b.x += dx * o * wb; b.y += dy * o * wb; }
    }
  }
  var W = 0, mx = 0, my = 0;
  cs.forEach(function (c) { var m = c.r * c.r; W += m; mx += c.x * m; my += c.y * m; });
  mx /= W; my /= W;
  var R = 0;
  cs.forEach(function (c) { c.x -= mx; c.y -= my; R = Math.max(R, Math.sqrt(c.x * c.x + c.y * c.y) + c.r); });
  return R;
}
function topics(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key];
  if (R.topics.length && R.topics[0].projects) return topics3(ctx, w, h, snap, key, P);
  return topics2(ctx, w, h, snap, key, P);
}

// three levels: theme → project → subtopic (circles in circles in circles)
// The circle layout depends only on the data, not the canvas size, and is the slow part (~35 ms), so it is cached
// per window; the panel also warms it for every time-slider frame so scrubbing only has to draw.
var _layoutCache = {}, _layoutCount = 0;
function topicsLayout(snap, key, R) {
  R = R || snap.ranges[key];
  var sig = 0; R.topics.forEach(function (g) { (g.projects || []).forEach(function (p) { sig += p.v + (p.subs ? p.subs.length : 0); }); });
  var ck = key + "|" + (snap.level || "") + "|" + R.t0 + "|" + R.t1 + "|" + sig;
  if (_layoutCache[ck]) return _layoutCache[ck];
  var groups = R.topics.map(function (g) {
    var projs = g.projects.filter(function (p) { return !p.more && p.v > 0; }).map(function (p) {
      var leaves = p.subs.filter(function (x) { return x.v > 0; }).map(function (x) { return { t: x, r: Math.sqrt(x.v) }; });
      return { p: p, leaves: leaves };
    }).filter(function (p) { return p.leaves.length; });
    return { theme: g.theme, projs: projs };
  }).filter(function (g) { return g.projs.length; });
  groups.forEach(function (g) {
    g.projs.forEach(function (p) { p.r = relax(p.leaves, 0.8, 0.03, 0.03, 120) + 1.5; });
    g.kids = g.projs;
    g.r = relax(g.projs, 1.6, 0.02, 0.02, 160) + 3;
  });
  if (groups.length) relax(groups, 5, 0.012, 0.045, 260);
  if (++_layoutCount > 1500) { _layoutCache = {}; _layoutCount = 1; }
  _layoutCache[ck] = groups;
  return groups;
}

function topics3(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], hits = [];
  function growthOf(o) { return o.pv > 0 ? Math.max(-1, Math.min(1, (o.v - o.pv) / o.pv)) : 1; }
  var groups = topicsLayout(snap, key, R);
  if (!groups.length) return empty(ctx, w, h, P);
  var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  groups.forEach(function (g) { minX = Math.min(minX, g.x - g.r); maxX = Math.max(maxX, g.x + g.r); minY = Math.min(minY, g.y - g.r); maxY = Math.max(maxY, g.y + g.r); });
  var k = Math.min((w - 8) / (maxX - minX), (h - 18) / (maxY - minY));
  var ox = w / 2 - (minX + maxX) / 2 * k, oy = 12 + (h - 16) / 2 - (minY + maxY) / 2 * k;
  var labels = [], projLabels = [];
  groups.forEach(function (g) {
    var col = themeColor(P, snap, g.theme), gx = ox + g.x * k, gy = oy + g.y * k, gr = g.r * k;
    ctx.beginPath(); ctx.arc(gx, gy, gr, 0, 2 * Math.PI); ctx.fillStyle = rgba(col, 0.04); ctx.fill();
    ctx.strokeStyle = rgba(col, 0.35); ctx.lineWidth = 1; ctx.stroke();
    g.projs.forEach(function (p) {
      var px = gx + p.x * k, py = gy + p.y * k, pr = p.r * k, pg = growthOf(p.p);
      ctx.beginPath(); ctx.arc(px, py, pr, 0, 2 * Math.PI); ctx.fillStyle = rgba(col, 0.07); ctx.fill();
      ctx.strokeStyle = rgba(col, 0.7); ctx.lineWidth = 1.2; ctx.stroke();
      p.leaves.forEach(function (c) {
        var cx = px + c.x * k, cy = py + c.y * k, r = c.r * k, t = c.t, gr2 = growthOf(t);
        ctx.beginPath(); ctx.arc(cx, cy, Math.max(0.8, r - 0.4), 0, 2 * Math.PI);
        ctx.fillStyle = t.more ? rgba(col, 0.08) : rgba(col, 0.16 + 0.34 * (gr2 + 1) / 2); ctx.fill();
        if (!t.more) { ctx.strokeStyle = rgba(col, 0.9); ctx.lineWidth = 0.8; ctx.stroke(); }
        if (r > K(P, 15) && p.leaves.length > 1) {
          ctx.font = font(P, 8.5); ctx.textAlign = "center"; ctx.fillStyle = rgba(P.fg, 0.85);
          ctx.fillText(ellipsize(ctx, t.name, r * 1.8), cx, cy + K(P, 3)); ctx.textAlign = "left";
        }
        hits.push({ x: cx - r, y: cy - r, w: 2 * r, h: 2 * r, text: g.theme + " › " + p.p.name + (t.name !== p.p.name ? " › " + t.name : "") + "\n" + fmtH(t.v) + " busy" +
          (t.pv > 0 ? " · " + (gr2 >= 0 ? "▲ " : "▼ ") + Math.round(Math.abs((t.v - t.pv) / t.pv) * 100) + "%" : " · new") });
      });
      projLabels.push({ t: p.p.name, x: px, y: p.leaves.length > 1 ? py - pr - K(P, 2) : py + K(P, 3.5), r: pr, col: col, single: p.leaves.length === 1, v: p.p.v, g: pg });
    });
    labels.push({ t: g.theme.toUpperCase(), x: gx, y: Math.max(9, gy - gr - 3), col: col, r: gr });
  });
  // project labels (biggest first), then theme labels, skipping collisions
  var boxes = [];
  function place(L, px, bold, colr) {
    ctx.font = font(P, px, bold); ctx.textAlign = "center";
    var tw = ctx.measureText(L.t).width, b = [L.x - tw / 2 - 2, L.y - K(P, px), L.x + tw / 2 + 2, L.y + 2];
    for (var q = 0; q < boxes.length; q++) { var o = boxes[q]; if (b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]) return; }
    boxes.push(b); halo(ctx, P, L.t, L.x, L.y, colr); ctx.textAlign = "left";
  }
  projLabels.sort(function (a, b) { return b.r - a.r; });
  projLabels.forEach(function (L) { if (L.r > K(P, 11)) { L.t = ellipsize(ctx, L.t, Math.max(L.r * 2.2, K(P, 40))); place(L, 10, true, P.fg); } });
  labels.sort(function (a, b) { return b.r - a.r; });
  labels.forEach(function (L) { place(L, 9.5, true, L.col); });
  return hits;
}

function topics2(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], hits = [];
  var groups = R.topics.map(function (g) {
    var tot = sum(g.topics.map(function (t) { return t.v; }));
    return { theme: g.theme, kids: g.topics.filter(function (t) { return t.v > 0 && t.name.charAt(0) !== "+"; }).map(function (t) { return { t: t, r: Math.sqrt(t.v) }; }), tot: tot };
  }).filter(function (g) { return g.kids.length; });
  if (!groups.length) return empty(ctx, w, h, P);
  groups.forEach(function (g) { g.r = relax(g.kids, 1.2, 0.02, 0.02, 160) + 3; });
  relax(groups, 5, 0.012, 0.045, 260);
  var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  groups.forEach(function (g) { minX = Math.min(minX, g.x - g.r); maxX = Math.max(maxX, g.x + g.r); minY = Math.min(minY, g.y - g.r); maxY = Math.max(maxY, g.y + g.r); });
  var k = Math.min((w - 8) / (maxX - minX), (h - 18) / (maxY - minY));
  var ox = w / 2 - (minX + maxX) / 2 * k, oy = 12 + (h - 16) / 2 - (minY + maxY) / 2 * k;
  var labels = [];
  groups.forEach(function (g) {
    var col = themeColor(P, snap, g.theme), gx = ox + g.x * k, gy = oy + g.y * k, gr = g.r * k;
    ctx.beginPath(); ctx.arc(gx, gy, gr, 0, 2 * Math.PI); ctx.fillStyle = rgba(col, 0.05); ctx.fill();
    ctx.strokeStyle = rgba(col, 0.45); ctx.lineWidth = 1; ctx.stroke();
    g.kids.forEach(function (c) {
      var cx = gx + c.x * k, cy = gy + c.y * k, r = c.r * k, t = c.t;
      var growth = t.pv > 0 ? Math.max(-1, Math.min(1, (t.v - t.pv) / t.pv)) : 1;
      ctx.beginPath(); ctx.arc(cx, cy, Math.max(1, r - 0.5), 0, 2 * Math.PI);
      ctx.fillStyle = rgba(col, 0.14 + 0.36 * (growth + 1) / 2); ctx.fill();
      ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.textAlign = "center";
      if (r > K(P, 16)) {
        ctx.font = font(P, r > 30 ? 11 : 9.5, true); ctx.fillStyle = P.fg;
        ctx.fillText(ellipsize(ctx, t.name, r * 1.8), cx, cy + (r > K(P, 26) ? -1 : K(P, 3)));
        if (r > K(P, 26)) { ctx.font = font(P, 9.5); ctx.fillStyle = rgba(P.fg, 0.6); ctx.fillText(fmtH(t.v) + (growth > 0.15 ? " ▲" : growth < -0.15 ? " ▼" : ""), cx, cy + K(P, 11)); }
      }
      ctx.textAlign = "left";
      hits.push({ x: cx - r, y: cy - r, w: 2 * r, h: 2 * r, text: g.theme + " › " + t.name + "\n" + fmtH(t.v) + " busy" + (t.pv > 0 ? " · " + (growth >= 0 ? "▲ " : "▼ ") + Math.round(Math.abs((t.v - t.pv) / t.pv) * 100) + "%" : " · new") });
    });
    labels.push({ t: g.theme.toUpperCase(), x: gx, y: Math.max(9, gy - gr - 3), col: col, r: gr });
  });
  // theme labels last (on top), biggest first, skipping any that would collide
  labels.sort(function (a, b) { return b.r - a.r; });
  var boxes = [];
  ctx.font = font(P, 9.5, true); ctx.textAlign = "center";
  labels.forEach(function (L) {
    var tw = ctx.measureText(L.t).width, b = [L.x - tw / 2 - 3, L.y - 10, L.x + tw / 2 + 3, L.y + 2];
    for (var q = 0; q < boxes.length; q++) { var o = boxes[q]; if (b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]) return; }
    boxes.push(b); halo(ctx, P, L.t, L.x, L.y, L.col);
  });
  ctx.textAlign = "left";
  groups.forEach(function () {
  });
  return hits;
}

// ───────────────────────── rising / fading terms ─────────────────────────
function terms(ctx, w, h, snap, key, P) {
  var T = snap.ranges[key].terms, hits = [], y = K(P, 10), rowH = K(P, 19);
  var all = T.rising.concat(T.fading);
  if (!all.length) return empty(ctx, w, h, P, "not enough prompts yet");
  var max = Math.max.apply(null, all.map(function (r) { return Math.max(r.cur, r.prv); }));
  function section(label, list, up) {
    ctx.font = font(P, 9.5, true); ctx.fillStyle = rgba(P.fg, 0.45); ctx.fillText(label, 0, y); y += K(P, 14);
    list.forEach(function (r) {
      ctx.font = font(P, 11); ctx.fillStyle = up ? P.accent : P.you; ctx.fillText(up ? "▲" : "▼", 0, y);
      ctx.fillStyle = P.fg; ctx.fillText(ellipsize(ctx, r.term, w * 0.42), K(P, 16), y);
      var bx = w * 0.52, bw = w * 0.28;
      ctx.fillStyle = rgba(P.fg, 0.08); ctx.fillRect(bx, y - 6, bw, 4);
      ctx.fillStyle = rgba(P.fg, 0.25); ctx.fillRect(bx, y - 6, bw * r.prv / max, 4);
      ctx.fillStyle = up ? P.accent : rgba(P.fg, 0.6); ctx.fillRect(bx, y - 5, bw * r.cur / max, 2);
      ctx.font = font(P, 10); ctx.fillStyle = rgba(P.fg, 0.55); ctx.textAlign = "right";
      ctx.fillText(r.prv === 0 ? "new" : (r.cur >= r.prv ? "+" : "") + Math.round((r.cur - r.prv) / r.prv * 100) + "%", w, y);
      ctx.textAlign = "left";
      hits.push({ x: 0, y: y - rowH + 4, w: w, h: rowH, text: r.term + "\n" + r.cur + " prompts now · " + r.prv + " before" });
      y += rowH;
    });
    y += 4;
  }
  if (T.rising.length) section("RISING", T.rising, true);
  if (T.fading.length) section("FADING", T.fading, false);
  return hits;
}

// ───────────────────────── activity mix (share of busy time) ─────────────────────────
function mixChart(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], hits = [], nb = R.mix.length, legendH = K(P, 44), padB = K(P, 14);
  var chartH = h - legendH, gap = nb > 12 ? 2 : 3, bw = (w - gap * (nb - 1)) / nb;
  var totals = snap.activities.map(function () { return 0; });
  ctx.font = font(P, 10);
  R.mix.forEach(function (acts, i) {
    var tot = sum(acts), y = chartH - padB, x = i * (bw + gap);
    acts.forEach(function (v, a) { totals[a] += v; var hh = tot ? v / tot * (chartH - padB - 2) : 0; ctx.fillStyle = rgba(P.fg, ACT_ALPHA[a]); ctx.fillRect(x, y - hh, bw, hh); y -= hh; });
    if (!tot) { ctx.fillStyle = rgba(P.fg, 0.05); ctx.fillRect(x, 0, bw, chartH - padB); }
    hits.push({ x: x, y: 0, w: bw, h: chartH - padB, text: tsLabel(R.mixBuckets[i], key) + (tot ? "\n" + snap.activities.map(function (a, k) { return a + " " + Math.round(acts[k] / tot * 100) + "%"; }).join("\n") : "\nno activity") });
    if (i % Math.ceil(nb / 4) === 0) { ctx.fillStyle = rgba(P.fg, 0.55); ctx.fillText(tsLabel(R.mixBuckets[i], key), x, chartH - 2); }
  });
  var T = sum(totals), lx = 0, ly = chartH + K(P, 14), sq = K(P, 8);
  snap.activities.forEach(function (a, i) {
    var label = a + " " + (T ? Math.round(totals[i] / T * 100) : 0) + "%", tw = ctx.measureText(label).width + K(P, 26);
    if (lx + tw > w) { lx = 0; ly += K(P, 15); }
    ctx.fillStyle = rgba(P.fg, ACT_ALPHA[i]); ctx.fillRect(lx, ly - sq, sq, sq);
    ctx.fillStyle = rgba(P.fg, 0.8); ctx.fillText(label, lx + K(P, 12), ly);
    lx += tw;
  });
  return hits;
}

// ───────────────────────── agents at once ─────────────────────────
function conc(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], C = R.conc, hits = [], nb = C.peak.length, padB = K(P, 14), padL = K(P, 18);
  var maxY = Math.max(2, Math.max.apply(null, C.peak));
  function y(v) { return (h - padB) - v / maxY * (h - padB - 8); }
  function x(i) { return padL + i / (nb - 1) * (w - padL - 2); }
  ctx.strokeStyle = rgba(P.fg, 0.12); ctx.lineWidth = 1; ctx.font = font(P, 10);
  var step = Math.max(1, Math.ceil(maxY / 4));
  for (var g = 0; g <= maxY; g += step) { ctx.beginPath(); ctx.moveTo(padL, y(g)); ctx.lineTo(w, y(g)); ctx.stroke(); ctx.fillStyle = rgba(P.fg, 0.55); ctx.fillText(String(g), 0, y(g) + 3); }
  ctx.beginPath(); ctx.moveTo(x(0), y(0)); C.avg.forEach(function (v, i) { ctx.lineTo(x(i), y(v)); }); ctx.lineTo(x(nb - 1), y(0)); ctx.closePath();
  ctx.fillStyle = rgba(P.accent, 0.22); ctx.fill();
  ctx.beginPath();
  C.peak.forEach(function (v, i) { if (i === 0) ctx.moveTo(x(i), y(v)); else { ctx.lineTo(x(i), y(C.peak[i - 1])); ctx.lineTo(x(i), y(v)); } });
  ctx.strokeStyle = P.fg; ctx.lineWidth = 1.6; ctx.stroke();
  var mi = C.peak.indexOf(Math.max.apply(null, C.peak));
  if (C.peak[mi] > 0) {
    var px = Math.min(x(mi), w - 6);   // keep the marker inside the canvas when the peak is the last bucket
    ctx.beginPath(); ctx.arc(px, y(C.peak[mi]), 4.5, 0, 2 * Math.PI); ctx.fillStyle = P.bg; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = P.accent; ctx.stroke();
    ctx.font = font(P, 10, true); var lab = "peak " + C.peak[mi], lw = ctx.measureText(lab).width;
    var lx = px + 7 + lw > w ? px - 9 - lw : px + 7;
    halo(ctx, P, lab, lx, y(C.peak[mi]) + 3.5, P.accent); ctx.font = font(P, 10);
  }
  ctx.fillStyle = rgba(P.fg, 0.55);
  ctx.fillText(tsLabel(R.buckets[0], key), x(0), h - 2);
  ctx.fillText(tsLabel(R.buckets[Math.floor(nb / 2)], key), x(Math.floor(nb / 2)), h - 2);
  for (var i = 0; i < nb; i++) hits.push({ x: x(i) - w / nb / 2, y: 0, w: w / nb, h: h, text: tsLabel(R.buckets[i], key) + "\npeak " + C.peak[i] + " · avg " + C.avg[i].toFixed(1) + " while active" });
  return hits;
}

// ───────────────────────── tools × activity ─────────────────────────
function tools(ctx, w, h, snap, key, P) {
  var T = snap.ranges[key].tools, hits = [];
  if (!T.length) return empty(ctx, w, h, P);
  var rows = T.slice(0, 7), max = Math.max.apply(null, rows.map(function (r) { return sum(r.parts); }));
  var labelW = K(P, 66), numW = K(P, 44), bw = w - labelW - numW - 8, y = K(P, 12), bh = K(P, 8);
  rows.forEach(function (r) {
    var n = sum(r.parts), x = labelW;
    ctx.font = font(P, 11); ctx.fillStyle = P.fg; ctx.fillText(r.tool, 0, y);
    ctx.fillStyle = rgba(P.fg, 0.06); ctx.fillRect(labelW, y - bh, bw, bh);
    r.parts.forEach(function (p, a) { var pw = bw * p / max; ctx.fillStyle = rgba(P.fg, ACT_ALPHA[a]); ctx.fillRect(x, y - bh, pw, bh); x += pw; });
    ctx.font = font(P, 10); ctx.fillStyle = rgba(P.fg, 0.55); ctx.textAlign = "right"; ctx.fillText(fmtN(n), w, y); ctx.textAlign = "left";
    hits.push({ x: 0, y: y - 12, w: w, h: 18, text: r.tool + " · " + fmtN(n) + " calls\n" + snap.activities.map(function (a, k) { return a + " " + r.parts[k]; }).filter(function (s) { return !/ 0$/.test(s); }).join("\n") });
    y += K(P, 21);
  });
  return hits;
}

// ───────────────────────── app time ─────────────────────────
function apps(ctx, w, h, snap, key, P) {
  var A = snap.ranges[key].apps, hits = [];
  var total = A.terminal + sum(A.apps.map(function (a) { return a.s; })) + A.other;
  if (total < 60) return empty(ctx, w, h, P, "collecting app time…");
  var labelW = K(P, 92), numW = K(P, 40), bw = w - labelW - numW - 8, y = K(P, 12);
  var max = Math.max(A.terminal, A.apps.length ? A.apps[0].s : 0, A.other);
  function row(name, s, parts, small) {
    ctx.font = font(P, small ? 10 : 11); ctx.fillStyle = small ? rgba(P.fg, 0.75) : P.fg;
    ctx.fillText(ellipsize(ctx, name, labelW - K(P, small ? 16 : 6)), small ? K(P, 10) : 0, y);
    var hh = K(P, small ? 4 : 8), x = labelW;
    ctx.fillStyle = rgba(P.fg, 0.06); ctx.fillRect(labelW, y - hh, bw * s / max, hh);
    parts.forEach(function (p) { var pw = bw * p[0] / max; ctx.fillStyle = p[1]; ctx.fillRect(x, y - hh, pw, hh); x += pw; });
    ctx.font = font(P, 10); ctx.fillStyle = rgba(P.fg, 0.55); ctx.textAlign = "right"; ctx.fillText(fmtH(s), w, y); ctx.textAlign = "left";
    hits.push({ x: 0, y: y - 12, w: w, h: 17, text: name + "\n" + fmtH(s) + " focused" });
    y += K(P, small ? 16 : 21);
  }
  var kc = { "Pi": P.kinds["Pi"], "Claude Code": P.kinds["Claude Code"], "Codex": P.kinds["Codex"], "shell": rgba(P.fg, 0.3) };
  if (A.terminal > 0) {
    row("terminals", A.terminal, A.split.map(function (s) { return [s.s, kc[s.name] || rgba(P.fg, 0.4)]; }), false);
    A.split.forEach(function (s) { row(s.name, s.s, [[s.s, rgba(kc[s.name].charAt(0) === "#" ? kc[s.name] : P.fg, 0.75)]], true); });
  }
  A.apps.forEach(function (a) { if (y < h) row(a.name, a.s, [[a.s, rgba(P.fg, 0.45)]], false); });
  if (A.other > 0 && y < h) row("other", A.other, [[A.other, rgba(P.fg, 0.25)]], false);
  return hits;
}

// ───────────────────────── KPI helpers (text, used by QML and the dev page) ─────────────────────────
function kpiItems(snap, key) {
  var R = snap.ranges[key], c = R.kpis, p = R.prev;
  function d(a, b) { if (!p) return ""; var v = pct(a, b); return v === null ? "new" : (v >= 0 ? "▲ " : "▼ ") + Math.abs(v) + "%"; }
  return [
    { label: "AGENT BUSY", value: fmtH(c.agent), delta: d(c.agent, p && p.agent), up: !p || c.agent >= p.agent },
    { label: "YOUR TIME", value: fmtH(c.you), delta: d(c.you, p && p.you), up: !p || c.you >= p.you },
    { label: "PROMPTS", value: fmtN(c.prompts), delta: d(c.prompts, p && p.prompts), up: !p || c.prompts >= p.prompts },
    { label: "TOKENS", value: fmtN(c.tokens), delta: d(c.tokens, p && p.tokens), up: !p || c.tokens >= p.tokens },
    { label: "COST" + (c.costEst ? " (EST.)" : ""), value: fmtCost(c.cost), delta: d(c.cost, p && p.cost), up: !p || c.cost >= p.cost },
    { label: "PEAK PARALLEL", value: c.peak + (c.peak === 1 ? " agent" : " agents"), delta: c.sessions + " sessions", up: true, plain: true },
  ];
}

// ───────────────────────── subagents: runs per type, by outcome ─────────────────────────
var STATUS = ["completed", "steered", "error", "aborted", "other"];
function statusColor(P, s) {
  return s === "completed" ? rgba(P.fg, 0.7) : s === "steered" ? P.accent : s === "error" ? P.you : rgba(P.fg, 0.25);
}
var FAM_IDX = { sonnet: 0, opus: 1, haiku: 2, fable: 3, kimi: 4, gpt: 5 };
function famColor(P, f) {
  return FAM_IDX[f] !== undefined ? P.themes[FAM_IDX[f] % P.themes.length] : rgba(P.fg, 0.3);
}
// a specific model: its family's colour, lighter for each further model of the same family (by tokens)
function modelColor(P, S, model) {
  var fam = (S.modelFamily && S.modelFamily[model]) || "unknown";
  var base = famColor(P, fam);
  if (base.charAt(0) !== "#") return base;
  var same = (S.models || []).filter(function (m) { return m.family === fam; }).map(function (m) { return m.model; });
  var i = Math.max(0, same.indexOf(model));
  return i === 0 ? base : mix(base, P.bg, Math.min(0.55, 0.28 * i));
}
function subTypeColor(P, S, type) {
  var i = 0;
  for (var k = 0; k < S.types.length; k++) if (S.types[k].type === type) { i = k; break; }
  return P.themes[(i + 1) % P.themes.length];
}
function subTypes(ctx, w, h, snap, key, P) {
  var S = snap.ranges[key].subs, hits = [];
  if (!S || !S.types.length) return empty(ctx, w, h, P, "no subagents in this period");
  var y = K(P, 10);
  ctx.font = font(P, 10); ctx.fillStyle = rgba(P.fg, 0.6);
  var share = S.allTokens ? Math.round(S.tokens / S.allTokens * 100) : 0;
  ctx.fillText(ellipsize(ctx, fmtH(S.busy) + " · " + fmtN(S.tokens) + " tok · " + share + "%", w), 0, y);
  y += K(P, 18);
  var rowH = K(P, 33), fit = Math.max(1, Math.floor((h - K(P, 32) - y) / rowH) + 1);
  var rows = S.types.slice(0, fit), max = Math.max.apply(null, rows.map(function (t) { return t.runs; })) || 1;
  var labelW = K(P, 84), numW = K(P, 28), bw = w - labelW - numW - 6, bh = K(P, 7);
  rows.forEach(function (t) {
    ctx.font = font(P, 11); ctx.fillStyle = P.fg; ctx.fillText(ellipsize(ctx, t.type === "general-purpose" ? "general" : t.type, labelW - K(P, 4)), 0, y);
    var x = labelW;
    ctx.fillStyle = rgba(P.fg, 0.06); ctx.fillRect(labelW, y - bh, bw, bh);
    STATUS.forEach(function (s) { var n = t.status[s] || 0; if (!n) return; var pw = bw * n / max; ctx.fillStyle = statusColor(P, s); ctx.fillRect(x, y - bh, pw, bh); x += pw; });
    ctx.font = font(P, 10); ctx.fillStyle = rgba(P.fg, 0.55); ctx.textAlign = "right"; ctx.fillText(String(t.runs), w, y); ctx.textAlign = "left";
    // model mix: families by tokens, in their colours
    var fams = Object.keys(t.models || {}).map(function (f) { return [f, t.models[f].tokens]; }).filter(function (m) { return m[1] > 0; })
      .sort(function (a, b) { return b[1] - a[1]; });
    var ft = sum(fams.map(function (m) { return m[1]; })), mx = 0, my = y + K(P, 13);
    ctx.font = font(P, 9.5);
    if (t.median != null) { ctx.fillStyle = rgba(P.fg, 0.5); var md = "~" + fmtH(t.median) + "  "; ctx.fillText(md, 0, my); mx = ctx.measureText(md).width; }
    fams.slice(0, 2).forEach(function (m) {
      var lab = m[0] + " " + Math.round(m[1] / ft * 100) + "%  ", lw = ctx.measureText(lab).width;
      if (mx + lw > w) return;
      ctx.fillStyle = modelColor(P, S, m[0]); ctx.fillText(lab, mx, my); mx += lw;
    });
    if (!fams.length) { ctx.fillStyle = rgba(P.fg, 0.4); ctx.fillText("model unknown", mx, my); }
    hits.push({ x: 0, y: y - K(P, 12), w: w, h: K(P, 30), text: t.type + " · " + t.runs + " runs\n" +
      STATUS.filter(function (s) { return t.status[s]; }).map(function (s) { return s + " " + t.status[s]; }).join(" · ") +
      "\n" + fmtH(t.busy) + " busy · " + fmtN(t.tokens) + " tokens · " + fmtCost(t.cost) + (t.median != null ? "\nmedian run " + fmtH(t.median) : "") +
      Object.keys(t.models || {}).map(function (f) { var m = t.models[f]; return "\n  " + f + ": " + fmtN(m.tokens) + " tok · " + fmtH(m.busy) + " · " + fmtCost(m.cost); }).join("") });
    y += rowH;
  });
  if (S.types.length > rows.length) {
    ctx.font = font(P, 9.5); ctx.fillStyle = rgba(P.fg, 0.45);
    ctx.fillText(ellipsize(ctx, "+" + (S.types.length - rows.length) + " more: " + S.types.slice(rows.length).map(function (t) { return t.type; }).join(", "), w), 0, y - rowH + K(P, 31));
  }
  // outcome legend
  var lx = 0, ly = h - K(P, 3);
  ctx.font = font(P, 9.5);
  [["completed", "done"], ["steered", "steered"], ["error", "error"]].forEach(function (s) {
    ctx.fillStyle = statusColor(P, s[0]); ctx.fillRect(lx, ly - K(P, 7), K(P, 7), K(P, 7));
    ctx.fillStyle = rgba(P.fg, 0.7); ctx.fillText(s[1], lx + K(P, 10), ly);
    lx += ctx.measureText(s[1]).width + K(P, 18);
  });
  return hits;
}

// ───────────────────────── subagent runs: when × how long ─────────────────────────
function subRuns(ctx, w, h, snap, key, P) {
  var R = snap.ranges[key], S = R.subs, hits = [];
  if (!S || !S.runs.length) return empty(ctx, w, h, P, "no subagent runs in this period");
  var padL = K(P, 26), padB = K(P, 14), padT, t0 = R.t0, t1 = R.t1;
  // legend: model families present
  var present = {}; S.runs.forEach(function (r) { present[r.m] = (present[r.m] || 0) + 1; });
  var lx = 0, lrow = 0; ctx.font = font(P, 9.5);
  var byRuns = Object.keys(present).sort(function (a, b) { return present[b] - present[a]; });
  var shown = byRuns.filter(function (f) { return f !== "unknown"; }).slice(0, 5);
  var extra = byRuns.length - shown.length;
  shown.concat(extra > 0 ? ["+" + extra] : []).forEach(function (f) {
    if (f.charAt(0) === "+") {
      var lw0 = ctx.measureText(f).width + K(P, 6);
      if (lx + lw0 > w) { lx = 0; lrow++; }
      ctx.fillStyle = rgba(P.fg, 0.45); ctx.fillText(f, lx, K(P, 6) + lrow * K(P, 13) + K(P, 3.5));
      return;
    }
    var lw = ctx.measureText(f).width + K(P, 16);
    if (lx + lw > w) { lx = 0; lrow++; }
    var ly = K(P, 6) + lrow * K(P, 13);
    ctx.fillStyle = modelColor(P, S, f); ctx.beginPath(); ctx.arc(lx + K(P, 3.5), ly, K(P, 3.2), 0, 2 * Math.PI); ctx.fill();
    ctx.fillStyle = rgba(P.fg, 0.7); ctx.fillText(f, lx + K(P, 9), ly + K(P, 3.5));
    lx += lw;
  });
  padT = K(P, 16) + lrow * K(P, 13);
  var lo = Math.log(3), hi = Math.log(3 * 3600);
  function x(t) { return padL + (t - t0) / (t1 - t0) * (w - padL - 4); }
  function y(d) { var v = Math.log(Math.max(3, Math.min(3 * 3600, d))); return (h - padB) - (v - lo) / (hi - lo) * (h - padB - padT - 4); }
  ctx.strokeStyle = rgba(P.fg, 0.12); ctx.lineWidth = 1; ctx.font = font(P, 10);
  [[10, "10s"], [60, "1m"], [600, "10m"], [3600, "1h"]].forEach(function (g) {
    ctx.beginPath(); ctx.moveTo(padL, y(g[0])); ctx.lineTo(w, y(g[0])); ctx.stroke();
    ctx.fillStyle = rgba(P.fg, 0.55); ctx.fillText(g[1], 0, y(g[0]) + K(P, 3));
  });
  S.runs.forEach(function (r) {
    var cx = x(r.t), done = r.d != null, cy = done ? y(r.d) : padT + K(P, 2), rad = K(P, 3);
    var col = modelColor(P, S, r.m);
    ctx.beginPath(); ctx.arc(cx, cy, rad, 0, 2 * Math.PI);
    if (r.status === "error" || r.status === "aborted") { ctx.fillStyle = P.bg; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = P.you; ctx.stroke(); }
    else { ctx.fillStyle = col.charAt(0) === "#" ? rgba(col, done ? 0.75 : 0.3) : col; ctx.fill(); if (r.status === "steered") { ctx.lineWidth = 1.2; ctx.strokeStyle = P.accent; ctx.stroke(); } }
    hits.push({ x: cx - rad - 2, y: cy - rad - 2, w: 2 * rad + 4, h: 2 * rad + 4,
      text: r.type + " · " + r.m + " · " + r.status + (done ? " · " + fmtH(r.d) : "") + "\n" + (r.desc || "").slice(0, 60) });
  });
  ctx.fillStyle = rgba(P.fg, 0.55); ctx.font = font(P, 10);
  ctx.fillText(tsLabel(t0, key), padL, h - 2);
  var ml = tsLabel((t0 + t1) / 2, key); ctx.fillText(ml, padL + (w - padL) / 2 - ctx.measureText(ml).width / 2, h - 2);
  return hits;
}
