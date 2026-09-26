import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Ui
import qs.Commons
import "charts.js" as Charts

// agf.data-visualization — "Agent Activity" bar button + large dashboard panel.
//
// Data: bin/odv.py (the collector, shipped next to this file) parses Pi / Claude Code / Codex
// session logs into ~/.local/share/omarchy-data-visualization/odv.db and writes snapshot.json,
// which this panel watches. A systemd user timer runs it every 10 min; opening the panel also
// runs it when the snapshot is older than 2 min.
//
// App time: this widget samples the focused window every 30 s (and on every focus change) with
// Quickshell's IdleMonitor, appending to apptime.jsonl; the collector turns samples into spans.
//
// Charts are drawn by charts.js (shared with dev/index.html) on Canvas items, in theme colours
// read from the current theme's colors.toml.
Panel {
  id: root
  moduleName: "agf.data-visualization"
  ipcTarget: "agf.data-visualization"
  manageIpc: false          // we own the IpcHandler below (adds refresh / setRange)

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property string glyph: "󰄧"   // nf-md-chart_areaspline
  readonly property string home: Quickshell.env("HOME")
  readonly property string dataDir: home + "/.local/share/omarchy-data-visualization"
  readonly property string cli: String(Qt.resolvedUrl("bin/odv.py")).replace(/^file:\/\//, "")

  property var snap: null
  property string range: "7d"
  // tag level shown: each turn tagged on its own, or every turn of a session sharing the session's tags
  property string level: "turn"
  readonly property var baseView: {
    if (!snap) return null
    if (!snap.levels || !snap.levels[level] || snap.level === level) return snap
    var v = Object.assign({}, snap)
    v.ranges = snap.levels[level].ranges; v.themes = snap.levels[level].themes; v.level = level
    return v
  }
  // time slider: `back` frames earlier than now (frames-<range>.json, built in the background by `odv frames`)
  readonly property var view: {
    if (!baseView) return null
    var f = back > 0 && frames && frames.frames ? frames.frames[Math.min(back, frames.frames.length - 1)] : baseView.ranges[range]
    if (!f || !f.sankey) return baseView
    // Sankey scale: "window" = this window's own tokens fill the height; "all" = the busiest window of this
    // length across all the data fills it (collector's sankey.peak), so quieter windows are drawn smaller
    var g = Object.assign({}, f); g.sankey = Object.assign({}, f.sankey, { peak: sankeyScale === "all" ? f.sankey.peak : 0 })
    return withRange(g)
  }
  function withRange(r) {
    var v = Object.assign({}, baseView)
    v.ranges = Object.assign({}, baseView.ranges); v.ranges[range] = r
    return v
  }
  // token total of every slider window (index 0 = now)
  readonly property var frameTotals: {
    if (!frames || !frames.frames || range === "1y") return []
    return frames.frames.map(function (f) { var t = 0; f.sankey.kt.forEach(function (r) { r.forEach(function (x) { t += x }) }); return t })
  }
  property string sankeyScale: "window"   // "window" | "all"
  onSankeyScaleChanged: paintTick++
  property int back: 0
  property var frames: null
  property bool playing: false
  readonly property int maxBack: range !== "1y" && frames && frames.frames ? frames.frames.length - 1 : 0
  // Keep the slider's place in time when the range changes: remember the shown window's end (0 = now) and, once the
  // new range's frames load, jump to the frame ending nearest to it.
  property real anchorEnd: 0
  property string placedRange: ""
  property bool placing: false        // a range switch placing the slider must not overwrite the remembered time
  onBackChanged: {
    if (!placing && frames && frames._range === range && placedRange === range && frames.frames[back]) anchorEnd = back > 0 ? frames.frames[back].t1 : 0
    if (back > 0 && live) live = false
    paintTick++
  }
  onFramesChanged: {
    if (frames && frames.frames && frames._range === range && placedRange !== range) {
      placedRange = range
      var best = 0
      if (anchorEnd > 0) { var d = Infinity; for (var i = 0; i < frames.frames.length; i++) { var e = Math.abs(frames.frames[i].t1 - anchorEnd); if (e < d) { d = e; best = i } } }
      placing = true; back = best; placing = false
    } else if (back > maxBack) back = maxBack
    warmIdx = 0; paintTick++
  }
  // ---- live: re-collect every 15 s while the panel is open, pinned to now (off when you move the slider back) ----
  property bool live: false
  readonly property bool liveEnabled: false   // LIVE mode is parked for now; set true to bring back the button and L key
  onLiveChanged: if (live) { playing = false; pendingBack = -1; back = 0; anchorEnd = 0; collect(true) }
  Timer { interval: 15000; repeat: true; running: root.liveEnabled && root.live && root.opened; onTriggered: root.collect(true) }
  // pre-compute the topic-map layout for every slider frame while the panel is open, a few per tick
  property int warmIdx: 0
  Timer {
    interval: 30; repeat: true
    running: root.opened && !!root.frames && !!root.frames.frames && root.warmIdx < root.frames.frames.length && root.range !== "1y"
    onTriggered: {
      var fs = root.frames.frames, lv = root.view ? root.view.level : ""
      for (var k = 0; k < 2 && root.warmIdx < fs.length; k++, root.warmIdx++) {
        var f = fs[root.warmIdx]
        if (f.topics && f.topics.length && f.topics[0].projects) Charts.topicsLayout({ level: lv }, root.range, f)
      }
    }
  }
  Timer {
    interval: 140; repeat: true; running: root.playing && root.opened
    onTriggered: { if (root.target() > 0) root.stepBack(-1); else root.playing = false }
  }
  // Frame skipping: slider/keys set a target; it is applied only once the charts have finished painting the
  // previous frame, so fast drags and held keys jump to the latest position instead of queueing every step.
  property int pendingBack: -1
  property int paintsPending: 0
  function target() { return pendingBack >= 0 ? pendingBack : back }
  function setBack(i) {
    i = Math.max(0, Math.min(maxBack, i))
    if (paintsPending <= 0) { pendingBack = -1; back = i } else pendingBack = i
  }
  function stepBack(d) { setBack(target() + d) }
  function chartPainted() {
    if (paintsPending > 0) paintsPending--
    if (paintsPending <= 0 && pendingBack >= 0) { var i = pendingBack; pendingBack = -1; if (i !== back) back = i }
  }
  Timer {   // watchdog: never let a missed painted() stall the slider
    interval: 120; running: root.pendingBack >= 0; onTriggered: { root.paintsPending = 0; root.chartPainted() }
  }
  function togglePlay() {
    if (root.playing) { root.playing = false; return }
    if (root.maxBack === 0) return
    if (root.back === 0) root.back = root.maxBack
    root.playing = true
  }
  FileView {
    id: framesFile
    path: root.dataDir + "/frames-" + root.range + ".json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onPathChanged: { root.frames = null; reload() }
    onLoaded: { try { var f = JSON.parse(text()); f._range = String(path).replace(/.*frames-(.*)\.json$/, "$1"); root.frames = f } catch (e) { } }
  }
  Process {
    id: framesProc
    command: ["python3", root.cli, "frames"]
    onExited: framesFile.reload()
  }
  function endLabel() {
    var R = root.view ? root.view.ranges[root.range] : null
    if (!R) return ""
    if (root.back === 0) return R.label.toUpperCase()
    var words = { "15m": "15 MIN", "1h": "1 HOUR", "6h": "6 HOURS", "24h": "24 HOURS", "7d": "7 DAYS", "1mo": "30 DAYS" }[root.range]
    var fmt = root.range === "1mo" ? "M/d" : (["15m", "1h", "6h"].indexOf(root.range) >= 0 ? "M/d h:mm AP" : "M/d h AP")
    return words + " TO " + Qt.formatDateTime(new Date(R.t1 * 1000), fmt).toUpperCase()
  }
  onLevelChanged: paintTick++
  readonly property var ranges: ["15m", "1h", "6h", "24h", "7d", "1mo", "1y"]
  property bool collecting: false
  property int paintTick: 0          // bump to repaint every chart

  // ---- theme palette -------------------------------------------------------------------
  property var themeKeys: ({})
  function tk(names, fallback) {
    for (var i = 0; i < names.length; i++) if (themeKeys[names[i]]) return themeKeys[names[i]]
    return String(fallback)
  }
  readonly property var pal: ({
    bg: String(Color.popups.background),
    fg: String(root.bar ? root.bar.foreground : Color.foreground),
    accent: String(Color.accent),
    you: tk(["red", "color1"], Color.urgent),
    themes: [String(Color.accent), tk(["magenta", "color5"], Color.accent), tk(["green", "color2"], Color.accent),
             tk(["yellow", "color3"], Color.accent), tk(["cyan", "color6"], Color.accent), tk(["orange", "bright_yellow", "color11"], Color.accent),
             tk(["red", "color1"], Color.urgent), tk(["dark_foreground", "color8"], Color.muted), tk(["brown", "bright_red", "color9"], Color.urgent)],
    kinds: { "Pi": String(root.bar ? root.bar.foreground : Color.foreground), "Claude Code": tk(["orange", "yellow", "color3"], Color.accent),
             "Codex": tk(["green", "color2"], Color.accent), "Subagents": tk(["magenta", "color5"], Color.accent) },
    font: root.bar ? root.bar.fontFamily : "monospace",
    scale: Style.space(100) / 100
  })
  onPalChanged: paintTick++

  FileView {
    id: themeFile
    path: root.home + "/.local/state/omarchy/current/theme/colors.toml"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var out = {}, lines = String(text()).split("\n")
      for (var i = 0; i < lines.length; i++) {
        var m = lines[i].match(/^\s*([A-Za-z0-9_-]+)\s*=\s*["']?(#[0-9A-Fa-f]{6})/)
        if (m) out[m[1]] = m[2]
      }
      root.themeKeys = out
    }
  }
  // theme switches swap the theme directory; re-read when the shell's colours change
  Connections {
    target: Color
    function onAccentChanged() { themeFile.reload() }
    function onBackgroundChanged() { themeFile.reload() }
  }

  // ---- snapshot ------------------------------------------------------------------------
  FileView {
    id: snapFile
    path: root.dataDir + "/snapshot.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { var first = !root.snap; root.snap = JSON.parse(text()); if (first) root.level = root.snap.level || "turn"; root.paintTick++ } catch (e) { /* keep last good snapshot */ }
    }
  }
  Process {
    id: runProc
    command: ["python3", root.cli, "run"]
    onExited: { root.collecting = false; snapFile.reload(); if (!root.skipFrames && !framesProc.running) framesProc.running = true }
  }
  property bool skipFrames: false     // live refreshes don't rebuild the slider frames (~20 s); ↻ does
  function collect(quick) {
    if (runProc.running) return
    root.skipFrames = !!quick
    root.collecting = true
    runProc.running = true
  }

  // ---- app-time sampling ---------------------------------------------------------------
  IdleMonitor {
    id: idleMon
    timeout: 120
    respectInhibitors: true          // a playing video keeps you "present"
    onIsIdleChanged: root.sample()
  }
  property var pendingLines: []
  Process {
    id: appendProc
    onExited: root.flushSamples()
  }
  function flushSamples() {
    if (appendProc.running || pendingLines.length === 0) return
    var payload = pendingLines.join("\n")
    pendingLines = []
    appendProc.command = ["sh", "-c", "mkdir -p \"$1\" && printf '%s\\n' \"$2\" >> \"$1/apptime.jsonl\"", "sh", root.dataDir, payload]
    appendProc.running = true
  }
  function sample() {
    var t = ToplevelManager.activeToplevel
    var line = JSON.stringify({ ts: Date.now() / 1000, app: t ? (t.appId || "") : "", title: t ? (t.title || "") : "", idle: idleMon.isIdle })
    var p = pendingLines.slice(); p.push(line); pendingLines = p
    flushSamples()
  }
  Timer { interval: 30000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.sample() }
  Connections {
    target: ToplevelManager
    function onActiveToplevelChanged() { root.sample() }
  }

  // ---- open / close --------------------------------------------------------------------
  Connections {
    target: root
    function onOpenedChanged() {
      if (root.opened) {
        root.paintTick++
        if (!root.snap) root.collect()     // only the very first time; afterwards refresh is by hand (↻ / R / right-click)
      }
    }
  }
  IpcHandler {
    target: "agf.data-visualization"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.collect() }
    // time each chart's JS against a no-op context, and the view rebuild, over n slider steps (no panel needed)
    function debugPerf(n: int): string {
      var noop = function () {}, ctx = { measureText: function () { return { width: 10 } } }
      ;["beginPath","moveTo","lineTo","bezierCurveTo","quadraticCurveTo","closePath","fill","stroke","fillRect","strokeRect","fillText","strokeText","arc","rect","save","restore","clip","setLineDash","translate","rotate","scale","reset","clearRect","createLinearGradient"].forEach(function (k) { ctx[k] = noop })
      ctx.createLinearGradient = function () { return { addColorStop: noop } }
      var names = ["heat","sankey","stream","topics","terms","conc","tools","subTypes","subRuns"], out = {}, t, b0 = root.back
      t = Date.now(); for (var i = 0; i < n; i++) { root.back = i % (root.maxBack + 1); var v = root.view } out.viewMs = (Date.now() - t) / n
      names.forEach(function (nm) { t = Date.now(); for (var i = 0; i < n; i++) { root.back = i % (root.maxBack + 1); root.drawChart(nm, ctx, 500, 250) } out[nm] = (Date.now() - t) / n })
      root.back = b0
      return JSON.stringify(out)
    }
    function debugState(): string { var R = root.view ? root.view.ranges[root.range] : null; return JSON.stringify({ range: root.range, back: root.back, maxBack: root.maxBack, anchorEnd: root.anchorEnd, placed: root.placedRange, t1: R ? new Date(R.t1 * 1000).toString() : null, live: root.live, label: root.endLabel() }) }
    function debugBack(i: int): void { root.setBack(i) }
    function debugSize(): string { return JSON.stringify({ col: column.implicitHeight, avail: root.contentAvail, fixed: root.fixedHeight, budget: root.chartBudget, w: panel.contentWidth, hdr: header.height, kpi: kpiRow.height, r1: row1.height, r2: row2.height, r3: row3.height, foot: footer.height, th: root.tileHead, t1: heatTile.height, h1: root.h1 }) }
    function setRange(r: string): void { if (root.ranges.indexOf(r) >= 0) root.range = r }
    function setLevel(l: string): void { if (l === "turn" || l === "session") root.level = l }
  }
  onRangeChanged: { root.playing = false; root.pendingBack = -1; if (root.placedRange !== root.range) { root.placing = true; root.back = 0; root.placing = false } root.warmIdx = 0; paintTick++ }

  function drawChart(name, ctx, w, h) {
    if (!root.view) return []
    switch (name) {
      case "heat": return Charts.heat(ctx, w, h, root.view, root.range, root.pal)
      case "sankey": return Charts.sankey(ctx, w, h, root.view, root.range, root.pal)
      case "stream": return Charts.stream(ctx, w, h, root.view, root.range, root.pal)
      case "topics": return Charts.topics(ctx, w, h, root.view, root.range, root.pal)
      case "terms": return Charts.terms(ctx, w, h, root.view, root.range, root.pal)
      case "mix": return Charts.mixChart(ctx, w, h, root.view, root.range, root.pal)
      case "conc": return Charts.conc(ctx, w, h, root.view, root.range, root.pal)
      case "tools": return Charts.tools(ctx, w, h, root.view, root.range, root.pal)
      case "apps": return Charts.apps(ctx, w, h, root.view, root.range, root.pal)
      case "subTypes": return Charts.subTypes(ctx, w, h, root.view, root.range, root.pal)
      case "subRuns": return Charts.subRuns(ctx, w, h, root.view, root.range, root.pal)
    }
    return []
  }
  readonly property var kpis: view ? Charts.kpiItems(view, range) : []
  function heatCaption() {
    if (!snap) return ""
    var m = view.ranges[range].heat.mode
    return (m === "theme-hour" ? "theme × hour" : m === "theme-time" ? "theme × minute" : m === "day-hour" ? "day × hour of day" : "week × hour of day") + " · agent time vs your time"
  }

  // ---- bar button ----------------------------------------------------------------------
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.glyph
    tooltipText: "Agent activity"
    onPressed: function(b) { if (b === Qt.RightButton) root.collect(); else root.toggle() }
  }

  // ---- reusable pieces -----------------------------------------------------------------
  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color dim: Qt.rgba(fg.r, fg.g, fg.b, 0.55)
  readonly property color rule: Qt.rgba(fg.r, fg.g, fg.b, 0.12)
  readonly property string ff: root.bar ? root.bar.fontFamily : "monospace"

  component ChartCanvas: Canvas {
    id: cv
    property string chart: ""
    property var hits: []
    renderStrategy: Canvas.Cooperative
    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      hits = root.drawChart(chart, ctx, width, height) || []
    }
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    Connections { target: root; function onPaintTickChanged() { if (cv.visible && cv.width > 0) root.paintsPending++; cv.requestPaint() } }
    onPainted: root.chartPainted()
    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
      onPositionChanged: function(m) {
        var hit = null
        for (var i = 0; i < cv.hits.length; i++) {
          var t = cv.hits[i]
          if (m.x >= t.x && m.x <= t.x + t.w && m.y >= t.y && m.y <= t.y + t.h) { hit = t; break }
        }
        if (!hit) { tip.visible = false; return }
        var p = cv.mapToItem(tipLayer, m.x, m.y)
        tip.text = hit.text
        tip.x = Math.min(p.x + 14, tipLayer.width - tip.width - 4)
        tip.y = Math.min(p.y + 14, tipLayer.height - tip.height - 4)
        tip.visible = true
      }
      onExited: tip.visible = false
    }
  }

  component Tile: Column {
    property string title: ""
    property string caption: ""
    property string captionRich: ""   // optional StyledText caption
    property string legend: ""        // "conc": drawn legend (stepped peak line + spiky average area) instead of the caption
    property string chart: ""
    property var options: []          // optional segmented choice at the right of the title, e.g. ["window", "all"]
    property string option: ""
    signal optionPicked(string o)
    property real chartHeight: 200
    readonly property real headHeight: tTitle.implicitHeight + tCap.implicitHeight + spacing * 2
    spacing: Style.space(3)
    Item {
      width: parent.width; height: tTitle.implicitHeight
      Text { id: tTitle; text: parent.parent.title; color: root.fg; font.family: root.ff; font.pixelSize: Style.font.body; font.bold: true; font.letterSpacing: 1.6; font.capitalization: Font.AllUppercase }
      Row {
        id: optRow
        readonly property var tile: parent.parent
        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(10)
        Text { visible: optRow.tile.options.length > 0; text: "scale"; color: root.dim; font.family: root.ff; font.pixelSize: Style.font.caption }
        Repeater {
          model: optRow.tile.options
          Text {
            required property string modelData
            text: modelData.toUpperCase()
            color: optRow.tile.option === modelData ? root.fg : (optMouse.containsMouse ? root.fg : root.dim)
            font.family: root.ff; font.pixelSize: Style.font.caption; font.bold: optRow.tile.option === modelData; font.letterSpacing: 1.2
            font.underline: optRow.tile.option === modelData
            MouseArea { id: optMouse; anchors.fill: parent; anchors.margins: -4; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: optRow.tile.optionPicked(modelData) }
          }
        }
      }
    }
    Row {
      visible: parent.legend === "conc"
      height: tCap.implicitHeight; spacing: Style.space(5)
      readonly property real sw: tCap.font.pixelSize * 2.2
      readonly property real sh: tCap.font.pixelSize * 0.9
      Canvas {   // peak: stepped line, same colour/weight as the chart
        width: parent.sw; height: parent.sh; anchors.top: parent.top; anchors.topMargin: 2
        onPaint: { var c = getContext("2d"); c.reset(); c.strokeStyle = root.fg; c.lineWidth = 1.6; c.beginPath()
          var ys = [0.85, 0.85, 0.2, 0.2, 0.55, 0.55, 0.85], w = width / 3
          c.moveTo(0, height * ys[0]); c.lineTo(w, height * 0.85); c.lineTo(w, height * 0.2); c.lineTo(2 * w, height * 0.2); c.lineTo(2 * w, height * 0.55); c.lineTo(width, height * 0.55); c.stroke() }
      }
      Text { text: "peak ·"; color: root.dim; font: tCap.font }
      Canvas {   // average: spiky filled area, same fill as the chart
        width: parent.sw; height: parent.sh; anchors.top: parent.top; anchors.topMargin: 2
        onPaint: { var c = getContext("2d"); c.reset(); c.fillStyle = Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.35); c.beginPath()
          var ys = [0.7, 0.35, 0.6, 0.15, 0.55, 0.3, 0.65, 0.4], n = ys.length
          c.moveTo(0, height); for (var i = 0; i < n; i++) c.lineTo(i / (n - 1) * width, height * ys[i]); c.lineTo(width, height); c.closePath(); c.fill() }
      }
      Text { text: "average"; color: root.dim; font: tCap.font }
    }
    Text { id: tCap; visible: parent.legend === ""; width: parent.width; text: parent.captionRich !== "" ? parent.captionRich : parent.caption; textFormat: parent.captionRich !== "" ? Text.StyledText : Text.PlainText; color: root.dim; font.family: root.ff; font.pixelSize: Style.font.caption + 1; elide: Text.ElideRight; bottomPadding: Style.space(5) }
    ChartCanvas { width: parent.width; height: parent.chartHeight; chart: parent.chart }
  }

  // ---- layout budget: charts share whatever height is left after the fixed parts ----
  readonly property real tileHead: heatTile.headHeight
  readonly property real contentAvail: panel.availableCardHeight - panel.verticalContentInset
  readonly property real fixedHeight: header.implicitHeight + kpiRow.implicitHeight + footer.height + 3 * tileHead + 4 + column.spacing * 9 + 2
  readonly property real chartBudget: Math.max(Style.space(420), contentAvail - fixedHeight)
  readonly property real h1: Math.floor(chartBudget * 0.37)
  readonly property real h2: Math.floor(chartBudget * 0.35)
  readonly property real h3: Math.floor(chartBudget * 0.28)

  component VRule: Rectangle { width: 1; color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.10) }
  component HRule: Rectangle { height: 1; color: root.rule }

  // ---- panel ---------------------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(1120))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        // ←/→ move the time slider (← = back in time); ↑/↓ switch range
        if (dx !== 0) { root.playing = false; root.stepBack(-dx); return }
        if (dy === 0) return
        var i = root.ranges.indexOf(root.range)
        root.range = root.ranges[Math.max(0, Math.min(root.ranges.length - 1, i + dy))]
      }
      onTextKey: function(t) {
        var i = "1234567".indexOf(t)
        if (i >= 0) root.range = root.ranges[i]
        else if (t === "r" || t === "R") root.collect()
        else if (t === "[") root.stepBack(1)
        else if (t === "]") root.stepBack(-1)
        else if (t === "p" || t === "P" || t === " ") root.togglePlay()
        else if ((t === "l" || t === "L") && root.liveEnabled) root.live = !root.live
        else if (t === "s" || t === "S") root.sankeyScale = root.sankeyScale === "all" ? "window" : "all"
        else if ((t === "t" || t === "T") && root.snap && root.snap.levels) root.level = root.level === "turn" ? "session" : "turn"
      }

      Flickable {
        id: scroller
        anchors.fill: parent
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height + 1

        Column {
          id: column
          width: scroller.width
          spacing: Style.space(9)

          // ---------- header ----------
          Item {
            id: header
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, seg.height)
            Text {
              id: heroIcon
              anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
              text: root.glyph; color: root.fg; font.family: root.ff; font.pixelSize: Style.font.display
            }
            Column {
              id: heroLabels
              anchors.left: heroIcon.right; anchors.leftMargin: Style.space(14); anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)
              Text { text: "Agent Activity"; color: root.fg; font.family: root.ff; font.pixelSize: Style.font.title + 6; font.bold: true }
              Row {
                spacing: Style.space(8)
                Text { text: "WHAT THE AGENTS WORKED ON"; color: root.fg; font.family: root.ff; font.pixelSize: Style.font.caption + 1; font.bold: true; font.letterSpacing: 2.5 }
                Text { text: root.snap ? "· " + root.endLabel() : ""; color: root.back > 0 ? Color.accent : root.dim; font.family: root.ff; font.pixelSize: Style.font.caption + 1; font.letterSpacing: 1.5 }
                Text { visible: root.collecting; text: "· UPDATING…"; color: Color.accent; font.family: root.ff; font.pixelSize: Style.font.caption + 1; font.letterSpacing: 1.5 }
              }
            }
            // ---- time slider: drag left to go back in time, ▶ plays forward to now ----
            Row {
              id: timeSlider
              visible: root.range !== "1y"
              anchors.right: levelSeg.visible ? levelSeg.left : seg.left; anchors.rightMargin: Style.space(16); anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(10)
              opacity: root.maxBack > 0 ? 1 : 0.4
              Text {
                anchors.verticalCenter: parent.verticalCenter
                text: framesProc.running && root.maxBack === 0 ? "…" : (root.playing ? "󰏤" : "󰐊")   // pause / play
                color: playMouse.containsMouse ? Color.accent : root.fg; font.family: root.ff; font.pixelSize: Style.font.title
                MouseArea { id: playMouse; anchors.fill: parent; anchors.margins: -4; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.togglePlay() }
              }
              Item {
                id: track
                width: Style.space(200); height: seg.height
                anchors.verticalCenter: parent.verticalCenter
                // x of a frame index (0 = now at the right edge, maxBack = oldest at the left)
                function xOf(i) { return root.maxBack > 0 ? (1 - i / root.maxBack) * width : width }
                function idxAt(mx) { return Math.round((1 - Math.max(0, Math.min(1, mx / width))) * root.maxBack) }
                // token sparkline of every window, so you can see where the big ones are
                Canvas {
                  id: spark
                  anchors.fill: parent
                  property var totals: root.frameTotals
                  onTotalsChanged: requestPaint()
                  onPaint: {
                    var c = getContext("2d"); c.reset()
                    var t = root.frameTotals, n = t.length; if (n < 2) return
                    var m = 0; for (var i = 0; i < n; i++) m = Math.max(m, t[i])
                    if (m <= 0) return
                    for (var j = 0; j < n; j++) {
                      var x = track.xOf(j), hh = t[j] / m * (height - 4)
                      c.fillStyle = Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.28)
                      c.fillRect(x - width / n / 2, height - hh, Math.max(1, width / n), hh)
                    }
                  }
                }
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3) }
                // current-window handle
                Rectangle {
                  x: track.xOf(root.back) - width / 2; anchors.bottom: parent.bottom
                  width: Style.space(8); height: parent.height
                  color: root.back > 0 ? Color.accent : root.fg; opacity: 0.9
                }
                Text { anchors.right: parent.right; anchors.top: parent.bottom; anchors.topMargin: Style.space(1); text: "now"; color: root.dim; font.family: root.ff; font.pixelSize: Style.font.caption }
                MouseArea {
                  anchors.fill: parent; anchors.topMargin: -Style.space(4); anchors.bottomMargin: -Style.space(4)
                  cursorShape: Qt.PointingHandCursor; enabled: root.maxBack > 0
                  function setFrom(mx) { root.playing = false; root.setBack(track.idxAt(mx)) }
                  onPressed: function(m) { setFrom(m.x) }
                  onPositionChanged: function(m) { if (pressed) setFrom(m.x) }
                  onWheel: function(w) { root.playing = false; root.stepBack(w.angleDelta.y > 0 ? 1 : -1) }
                }
              }
            }
            Rectangle {
              id: liveBtn
              visible: root.liveEnabled
              anchors.right: refreshBtn.left; anchors.rightMargin: visible ? Style.space(8) : 0; anchors.verticalCenter: parent.verticalCenter
              width: liveRow.implicitWidth + Style.space(18); height: seg.height
              color: liveMouse.containsMouse ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08) : "transparent"
              border.width: 1; border.color: root.live ? Color.urgent : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3)
              Row {
                id: liveRow
                anchors.centerIn: parent; spacing: Style.space(6)
                Rectangle {
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(7); height: width; radius: width / 2
                  color: root.live ? Color.urgent : root.dim
                  SequentialAnimation on opacity { running: root.live; loops: Animation.Infinite; NumberAnimation { to: 0.25; duration: 700 } NumberAnimation { to: 1; duration: 700 } }
                }
                Text { text: "LIVE"; color: root.live ? Color.urgent : root.fg; font.family: root.ff; font.pixelSize: Style.font.body; font.bold: true; font.letterSpacing: 1.5 }
              }
              MouseArea { id: liveMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.live = !root.live }
            }
            Rectangle {
              id: refreshBtn
              anchors.right: timeSlider.visible ? timeSlider.left : (levelSeg.visible ? levelSeg.left : seg.left); anchors.rightMargin: Style.space(16); anchors.verticalCenter: parent.verticalCenter
              width: seg.height; height: seg.height
              color: refreshMouse.containsMouse ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08) : "transparent"
              border.width: 1; border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3)
              Text {
                anchors.centerIn: parent
                text: root.collecting ? "…" : "󰑐"   // nf-md-refresh
                color: root.fg; font.family: root.ff; font.pixelSize: Style.font.title
              }
              MouseArea { id: refreshMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.collect() }
            }
            Rectangle {
              id: levelSeg
              visible: !!(root.snap && root.snap.levels)
              anchors.right: seg.left; anchors.rightMargin: Style.space(12); anchors.verticalCenter: parent.verticalCenter
              width: levelRow.width + 2; height: levelRow.height + 2
              color: "transparent"; border.width: 1; border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3)
              Row {
                id: levelRow
                x: 1; y: 1
                Repeater {
                  model: ["turn", "session"]
                  Rectangle {
                    required property string modelData
                    width: lvLabel.implicitWidth + Style.space(22); height: lvLabel.implicitHeight + Style.space(12)
                    color: root.level === modelData ? root.fg : (lvMouse.containsMouse ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08) : "transparent")
                    Text {
                      id: lvLabel
                      anchors.centerIn: parent
                      text: modelData.toUpperCase()
                      color: root.level === modelData ? Color.popups.background : root.fg
                      font.family: root.ff; font.pixelSize: Style.font.body; font.bold: true; font.letterSpacing: 1.5
                    }
                    MouseArea { id: lvMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.level = modelData }
                  }
                }
              }
            }
            Rectangle {
              id: seg
              anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
              width: segRow.width + 2; height: segRow.height + 2
              color: "transparent"; border.width: 1; border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.3)
              Row {
                id: segRow
                x: 1; y: 1
                Repeater {
                  model: root.ranges
                  Rectangle {
                    required property string modelData
                    required property int index
                    width: segLabel.implicitWidth + Style.space(26); height: segLabel.implicitHeight + Style.space(12)
                    color: root.range === modelData ? root.fg : (segMouse.containsMouse ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08) : "transparent")
                    Text {
                      id: segLabel
                      anchors.centerIn: parent
                      text: modelData.toUpperCase()
                      color: root.range === modelData ? Color.popups.background : root.fg
                      font.family: root.ff; font.pixelSize: Style.font.body; font.bold: true; font.letterSpacing: 1.5
                    }
                    MouseArea { id: segMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.range = modelData }
                  }
                }
              }
            }
          }

          HRule { width: parent.width }

          // ---------- empty state ----------
          Text {
            visible: !root.snap
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.collecting ? "Reading agent sessions for the first time…" : "No data yet. Right-click the bar icon (or press R) to collect."
            color: root.dim; font.family: root.ff; font.pixelSize: Style.font.body
          }

          // ---------- KPIs ----------
          Row {
            id: kpiRow
            visible: !!root.snap
            width: parent.width
            Repeater {
              model: root.kpis
              Item {
                required property var modelData
                required property int index
                width: column.width / 6
                height: kpiCol.implicitHeight
                VRule { visible: index > 0; height: parent.height; anchors.left: parent.left }
                Column {
                  id: kpiCol
                  x: index > 0 ? Style.space(14) : 0
                  spacing: Style.space(2)
                  Text { text: modelData.label; color: root.fg; font.family: root.ff; font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 1.8 }
                  Text { text: modelData.value; color: root.fg; font.family: root.ff; font.pixelSize: Style.font.title + 10 }
                  Row {
                    spacing: Style.space(4)
                    Text { text: modelData.delta; color: modelData.plain ? root.dim : (modelData.up ? Color.accent : root.pal.you); font.family: root.ff; font.pixelSize: Style.font.caption + 1; font.bold: !modelData.plain }
                    Text { visible: !modelData.plain && modelData.delta !== "" && modelData.delta !== "new"; text: "vs prev"; color: root.dim; font.family: root.ff; font.pixelSize: Style.font.caption + 1 }
                  }
                }
              }
            }
          }

          HRule { width: parent.width; visible: !!root.snap }

          // ---------- row 1: heat map | sankey ----------
          Row {
            id: row1
            visible: !!root.snap
            width: parent.width
            spacing: Style.space(16)
            Tile { id: heatTile; width: (column.width - Style.space(33)) * 5 / 12; title: "When"; caption: root.heatCaption(); chart: "heat"; chartHeight: root.h1 }
            VRule { height: root.tileHead + root.h1 }
            Tile { width: (column.width - Style.space(33)) * 7 / 12; title: "Where the tokens went"; options: ["window", "all"]; option: root.sankeyScale; onOptionPicked: function(o) { root.sankeyScale = o }
                   caption: root.sankeyScale === "all" ? "tokens → agent → theme → work kind · full height = busiest period ever" : "tokens → agent → theme → work kind · full height = this window"; chart: "sankey"; chartHeight: root.h1 }
          }

          HRule { width: parent.width; visible: !!root.snap }

          // ---------- row 2: stream | topic map ----------
          Row {
            id: row2
            visible: !!root.snap
            width: parent.width
            spacing: Style.space(16)
            Tile { width: (column.width - Style.space(33)) * 7 / 12; title: "Themes over time"; caption: "agent busy time per theme (keyword rules)"; chart: "stream"; chartHeight: root.h2 }
            VRule { height: root.tileHead + root.h2 }
            Tile { width: (column.width - Style.space(33)) * 5 / 12; title: "Topic map"; caption: "area = busy time · deeper fill = rising"; chart: "topics"; chartHeight: root.h2 }
          }

          HRule { width: parent.width; visible: !!root.snap }

          // ---------- row 3: small tiles ----------
          Row {
            id: row3
            visible: !!root.snap
            width: parent.width
            spacing: Style.space(14)
            // Work mix is hidden for now (set showMix: true to bring it back); the others widen to fill.
            property bool showMix: false
            readonly property int tiles: showMix ? 6 : 5
            readonly property real unit: (width - Style.space(14) * (2 * tiles - 2) - tiles) / tiles
            Tile { width: row3.unit; title: "Terms"; caption: "prompt words vs last period"; chart: "terms"; chartHeight: root.h3 }
            VRule { visible: row3.showMix; height: root.tileHead + root.h3 }
            Tile { visible: row3.showMix; width: row3.unit; title: "Work mix"; caption: "busy time by work kind · from tool calls"; chart: "mix"; chartHeight: root.h3 }
            VRule { height: root.tileHead + root.h3 }
            Tile { width: row3.unit; title: "Agents at once"; legend: "conc"; chart: "conc"; chartHeight: root.h3 }
            VRule { height: root.tileHead + root.h3 }
            Tile { width: row3.unit; title: "Tools"; caption: "calls, by work kind"; chart: "tools"; chartHeight: root.h3 }
            VRule { height: root.tileHead + root.h3 }
            Tile { width: row3.unit; title: "Subagents"; caption: "runs by type · outcome"; chart: "subTypes"; chartHeight: root.h3 }
            VRule { height: root.tileHead + root.h3 }
            Tile { width: row3.unit; title: "Subagent runs"; caption: "when × duration · model"; chart: "subRuns"; chartHeight: root.h3 }
            // App time (chart "apps") is hidden for now; the widget keeps sampling focus for it.
          }

          // ---------- footer ----------
          Item {
            id: footer
            visible: !!root.snap
            width: parent.width
            height: footL.implicitHeight
            Text {
              id: footL
              text: root.snap ? "no AI · keyword/path rules · work kind from tool calls · 1–7 or ↑↓ range · ←→ or slider: back in time · P play · S Sankey scale · ↻ or R refresh" : ""
              color: root.dim; font.family: root.ff; font.pixelSize: Style.font.caption
            }
            Text {
              anchors.right: parent.right
              text: root.snap ? "Pi " + root.snap.counts.pi + " · Claude Code " + root.snap.counts.claude + " · Codex " + root.snap.counts.codex + " sessions · data from " + Qt.formatDateTime(new Date(root.snap.generated * 1000), "ddd h:mm AP") : ""
              color: root.dim; font.family: root.ff; font.pixelSize: Style.font.caption
            }
          }
        }
      }

      // tooltip layer (above the charts, inside the card)
      Item {
        id: tipLayer
        anchors.fill: parent
        Rectangle {
          id: tip
          property alias text: tipText.text
          visible: false
          width: tipText.implicitWidth + Style.space(14)
          height: tipText.implicitHeight + Style.space(10)
          color: Color.popups.background
          border.width: 1
          border.color: Color.accent
          Text { id: tipText; anchors.centerIn: parent; color: root.fg; font.family: root.ff; font.pixelSize: Style.font.caption + 1 }
        }
      }
    }
  }
}
