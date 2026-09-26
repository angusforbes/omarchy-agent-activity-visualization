# Agent Activity — an Omarchy bar widget

An [Omarchy](https://omarchy.org) top-bar widget (plugin id `agf.data-visualization`, glyph 󰄧) that opens a
large dashboard of what your coding agents worked on: [Pi](https://github.com/badlogic/pi-mono), Claude Code and Codex.
Everything is computed locally from the agents' session logs; nothing is sent anywhere.

**Charts:** when (heat map, agent time vs your time) · where the tokens went (Sankey: tokens → agent → theme →
work kind) · themes over time (stream) · topic map (circle pack) · rising/fading terms · agents at once ·
tools by work kind · subagents by type and outcome · subagent runs.

**Time:** ranges 15m · 1h · 6h · 24h · 7d · 1mo · 1y, and a time slider (drag, ←/→, or ▶ play) that replays
any range back through your history. The Sankey can be scaled to the window or to the busiest window ever.

## Install

`git clone https://github.com/angusforbes/omarchy-agent-activity-visualization && cd omarchy-agent-activity-visualization && ./install.sh`

Needs Omarchy's shell (quickshell) and Python 3 (stdlib only). Then `omarchy-restart-shell` if the panel
shows an old version.

## Pieces

| Path | What |
|---|---|
| `collector/odv.py` | Collector (stdlib Python). Parses session logs → SQLite, tags turns, writes `snapshot.json`. |
| `plugin/Panel.qml` | Bar button + panel (KeyboardPanel like Bluetooth). Watches the snapshot; samples app focus for app time (tile hidden for now, data still collected). |
| `plugin/charts.js` | All chart drawing (Canvas 2D), shared by the panel and the dev page. |
| `dev/index.html` | Browser preview of the charts: `chromium --allow-file-access-from-files "dev/index.html?snap=file://$HOME/.local/share/omarchy-data-visualization/snapshot.json"` |
| `install.sh` | Installs the plugin and `~/.local/bin/odv`. No timer: data refreshes only when you ask. |

Data lives in `~/.local/share/omarchy-data-visualization/`: `odv.db`, `snapshot.json`,
`frames-*.json` (time-slider windows), `apptime.jsonl` (focus samples) and `config.json`:
themes and their keyword rules (`themes`, `themeRules`: theme → regex), `prices`, `tagLevel`. The defaults are
generic; put your own projects and keywords in `config.json`.

## Commands

- `odv run`: ingest + rule tags + build. What ↻ / R / right-click in the panel runs. Never calls Jev.
- `odv frames`: builds `frames-<range>.json` for the time slider (the panel runs it in the background after each refresh, ~20 s).
- `odv check`: sanity numbers per source, tag counts, app samples.
- `odv tag --jev`: optional AI tagging with [Jev](https://typesafe.ai) (TypeSafe), only when asked. `--limit N` to try a few first.
- `odv build --level session`: build the snapshot from session-level tags instead of turn-level.
- `odv reset`: delete the database (it is rebuilt from the logs on the next run).

Panel keys: `1`–`7` or ↑/↓ switch range, ←/→ or `[` `]` move the time slider, `P`/space plays, `S` switches the
Sankey scale, `R` or the ↻ button refreshes, Esc closes. Right-click the bar icon to refresh. Nothing runs on a schedule; the footer shows when the data is from. IPC: `omarchy-shell agf.data-visualization {toggle|refresh|setRange 1y}`.

## How numbers are defined

- **Turn**: a prompt and everything the agent did until the next prompt.
- **Agent busy**: time inside turns, with gaps over 10 min cut out. Parallel agents add up.
- **Your time**: for each prompt you typed, the time since the agent's last reply (at most 3 min),
  unioned across sessions. Prompts relayed from other agents (`[BEGIN | from: …`, room/talk deliveries)
  and subagent sessions are not counted as yours.
- **Tokens / cost**: from each log's usage fields. Pi records cost; Claude Code and Codex costs are
  estimated from `prices` in `config.json` (shown as "COST (EST.)").
- **Duplicates**: forked/twin Pi sessions copy their parent's history; copied entries are counted once.
- **App time**: focused window sampled every 30 s and on focus change; idle (2 min without input,
  respecting inhibitors such as video) excluded. Terminals are split into Pi / Claude Code / Codex / shell
  by window class and title.

## Subagents

- **Pi `Agent` / `SubagentWorkflow` children** have their own session files (`parentSession` in the header,
  `session_info` name like `Explore#79de4122`), which give their type, busy time, tokens and cost. Each
  run is also recorded in the parent (`subagents:record`: type, description, status, start/end).
- **Pane subagents** (`subagent` tool): `subagent_result` messages give name, status and duration.
- **Claude Code**: `Agent`/`Task` tool calls and `subagents/*.jsonl` sessions (type from `.meta.json`).
- A child session whose first prompt is copied from its parent is a fork/twin, not a subagent.

Tiles: **Subagents** (runs per type, stacked by outcome: done / steered / error, with median run time and
the model mix by tokens) and **Subagent runs** (each run as a dot: start time ×
duration, log scale, coloured by specific model in its family colour, lighter shades for further models of a family; red rings = errors,
accent rings = steered). A run's model comes from its child session (linked by the `#id` in the session
name); pane and Claude Code runs show as "unknown". Codex logs contain no subagents (every turn is in
"default" collaboration mode). The Sankey shows subagent tokens as their own "Subagents" agent.

## Tagging

Each turn gets a **theme** (from `config.json`), a **work kind** (from its tool calls: editing, running
commands, reading, web, delegating, talking) and a **topic** (from the folders and files it touched).

- **Rules** (always): keyword and path rules; short follow-ups ("yes") inherit the session's theme.
- **Jev** (optional, only via `odv tag --jev`; the widget itself never uses it): one request per unit with three Choice questions. Jev cannot write free
  text, so the topic is *selected* from candidates the code extracts: project folders, plugin ids, config
  apps from the files touched, words used in at least 3 prompts, and the topics already in use. Jev
  results are cached and override rules.

To enable Jev, save your key to `~/.pi/agent/secrets/typesafe_api_key` (or export `TYPESAFE_API_KEY`),
then back-fill with `odv tag --jev`. It makes one request per turn plus one per session.

**Terms** are not Jev: they are words from your prompts, weighted by rarity (tf-idf), compared with the
previous period of the same length.
