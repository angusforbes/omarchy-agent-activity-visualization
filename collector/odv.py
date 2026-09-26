#!/usr/bin/env python3
"""odv — collector for omarchy-data-visualization (agent activity dashboard).

    odv ingest            parse Pi / Claude Code / Codex logs + app-time samples into SQLite
    odv tag [--jev]       tag turns (rules; Jev when a TypeSafe key is configured)
    odv build             write snapshot.json for the bar panel
    odv run               ingest + tag + build   (what the systemd timer runs)
    odv check             print sanity numbers

Stdlib only. Data lives in ~/.local/share/omarchy-data-visualization/.
"""
import argparse, glob, hashlib, json, math, os, re, sqlite3, sys, time, urllib.request, urllib.error
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

HOME = os.path.expanduser("~")
DATA = os.path.join(HOME, ".local/share/omarchy-data-visualization")
DB_PATH = os.path.join(DATA, "odv.db")
SNAPSHOT = os.path.join(DATA, "snapshot.json")
APPTIME = os.path.join(DATA, "apptime.jsonl")
CONFIG = os.path.join(DATA, "config.json")

GAP_CAP = 600          # s: a gap longer than this inside a turn is "agent waiting", not busy
YOU_CAP = 180          # s: max reading/typing time credited to one prompt
APP_GAP = 75           # s: app-time samples further apart than this are a gap (shell down / asleep)

DEFAULT_CONFIG = {
    "tagLevel": "turn",   # "turn" or "session"
    "themes": {   # generic defaults; edit your own in config.json (with matching "themeRules")
        "Desktop & shell": "The desktop environment: window manager, top bar and widgets, themes, keybindings, launchers, notifications",
        "Agent tooling": "AI coding-agent tooling: agent harnesses, extensions, skills, subagents, prompts, Claude Code / Codex / Pi setup",
        "Science & viz": "Scientific computing and data visualisation: plots, charts, dashboards, simulations, GPU code",
        "AI models & APIs": "Using hosted AI models and APIs: image, 3D or protein generation, model access, API keys, rate limits",
        "Writing & notes": "Writing and notes: poems, notes, ideas, emails, letters, documents, reading texts",
        "System & hardware": "Linux system administration and hardware: drivers, packages, installs, audio, bluetooth, networking, displays, systemd, crashes",
        "Media & web": "Media and the web: videos, music, radio, podcasts, social media, browsing",
        "Fun & play": "Games, drawings, toys and other things made for fun",
    },
    "activities": {
        "build": "Creating or extending something: writing new code, features, files or tools",
        "debug": "Fixing something broken: errors, crashes, wrong behaviour, investigating why something fails",
        "research": "Finding things out: web research, reading docs, comparing options, explaining how something works",
        "config": "Configuring or setting up: settings, installs, keybindings, themes, permissions, environment",
        "write": "Writing prose: notes, poems, emails, documentation, summaries",
        "chat": "Conversation, quick questions or naming with little or no work done",
    },
    "prices": {   # $ per million tokens: input, output, cache read, cache write (estimates for sources without cost)
        "opus": [5, 25, 0.5, 6.25], "sonnet": [3, 15, 0.3, 3.75], "haiku": [1, 5, 0.1, 1.25],
        "gpt": [1.25, 10, 0.125, 0], "default": [3, 15, 0.3, 3.75],
    },
}

# ─────────────────────────── config / db ───────────────────────────

ACTIVE_THEME_RULES = {}

def load_config():
    global ACTIVE_THEME_RULES
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG):
        try:
            user = json.load(open(CONFIG))
            cfg.update(user)
        except Exception as e:
            print(f"odv: bad {CONFIG}: {e}", file=sys.stderr)
    else:
        os.makedirs(DATA, exist_ok=True)
        json.dump(DEFAULT_CONFIG, open(CONFIG, "w"), indent=2)
    ACTIVE_THEME_RULES = cfg.get("themeRules") or THEME_RULES
    return cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INT, mtime REAL, source TEXT);
CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, path TEXT);
CREATE TABLE IF NOT EXISTS turns(
  id TEXT PRIMARY KEY, path TEXT, source TEXT, session TEXT, cwd TEXT, model TEXT,
  start REAL, end REAL, busy REAL, segments TEXT,
  human INT, prompt TEXT, prompt_chars INT, reply TEXT, you_start REAL,
  tokens_in INT, tokens_out INT, tokens_cache INT, cost REAL, cost_est INT,
  tools TEXT, files TEXT, sub INT DEFAULT 0, subtype TEXT DEFAULT '', subid TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS turns_start ON turns(start);
CREATE INDEX IF NOT EXISTS turns_path ON turns(path);
CREATE TABLE IF NOT EXISTS tags(
  turn TEXT, method TEXT, level TEXT, theme TEXT, topic TEXT, activity TEXT, conf REAL, ts REAL,
  PRIMARY KEY(turn, method, level));
CREATE TABLE IF NOT EXISTS subruns(
  key TEXT PRIMARY KEY, path TEXT, source TEXT, kind TEXT, type TEXT, description TEXT, status TEXT,
  start REAL, end REAL, parent TEXT);
CREATE TABLE IF NOT EXISTS app(ts REAL PRIMARY KEY, dur REAL, app TEXT, title TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS jevprobs(turn TEXT, method TEXT, level TEXT, probs TEXT, PRIMARY KEY(turn, method, level));
CREATE TABLE IF NOT EXISTS agenttopic(turn TEXT, method TEXT, area TEXT, project TEXT, subject TEXT, conf REAL, probs TEXT, ts REAL,
  PRIMARY KEY(turn, method));
CREATE TABLE IF NOT EXISTS corrections(turn TEXT, field TEXT, value TEXT, ts REAL, PRIMARY KEY(turn, field));
"""

def db():
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(turns)")}
    for c, d in (("sub", "INT DEFAULT 0"), ("subtype", "TEXT DEFAULT ''"), ("subid", "TEXT DEFAULT ''"), ("final", "TEXT DEFAULT ''"), ("calls", "TEXT DEFAULT '[]'")):
        if c not in cols:
            con.execute(f"ALTER TABLE turns ADD COLUMN {c} {d}")
    return con

def iso(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def jsonl(path):
    with open(path, "rb") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

# ─────────────────────────── normalisation helpers ───────────────────────────

TOOL_MAP = {
    "bash": "bash", "shell": "bash", "commandexecution": "bash", "exec_command": "bash",
    "read": "read", "grep": "read", "glob": "read", "ls": "read", "find": "read", "notebookread": "read", "vcc_recall": "read",
    "edit": "edit", "multiedit": "edit", "filechange": "edit", "apply_patch": "edit", "notebookedit": "edit",
    "write": "write", "todowrite": "write", "jot_save": "write",
    "websearch": "web", "webfetch": "web", "web_search": "web", "fetch_content": "web", "source_check": "web",
    "get_search_content": "web", "web.search": "web",
    "task": "subagent", "agent": "subagent", "subagent": "subagent", "subagentworkflow": "subagent",
    "steer_subagent": "subagent", "get_subagent_result": "subagent",
}
TOOLS = ["bash", "read", "edit", "write", "web", "subagent", "rooms", "desktop", "models", "other"]

def norm_tool(name):
    n = (name or "").lower()
    if n in TOOL_MAP: return TOOL_MAP[n]
    if n.startswith("room_") or n in ("talk", "demand", "talk_reply", "sendmessage", "subagent_send", "subagent_stop", "listagents"): return "rooms"
    if n.startswith(("mcp__hyprcu", "mcp__hyprdesk", "hyprcu", "desktop", "whatsapp", "voice_switch", "mcp")): return "desktop"
    if n.startswith(("nim_", "flux", "trellis", "moflow", "openfold", "cosmos", "jev_", "image_gen")): return "models"
    return "other"

FILE_KEYS = ("path", "file_path", "filePath", "notebook_path")

def tool_files(args):
    out = []
    if isinstance(args, dict):
        for k in FILE_KEYS:
            v = args.get(k)
            if isinstance(v, str) and len(v) < 400:
                out.append(v)
        cmd = args.get("command")
        if isinstance(cmd, str):
            out += re.findall(r"(?:~|" + re.escape(HOME) + r")/[\w.\-/]+", cmd)[:6]
    return out

AGENT_PREFIX = re.compile(r"^\s*(\[BEGIN \||\[hyprpi|\[herdr|<system-reminder>|<task-notification>|\[subagent|Caveat:|<local-command|<command-name>/?(clear|compact|exit|model|resume)|\[room)", re.I)

def is_human_prompt(text):
    return not AGENT_PREFIX.match(text or "")

def price(model, cfg):
    m = (model or "").lower()
    for k in ("opus", "sonnet", "haiku", "gpt"):
        if k in m:
            return cfg["prices"][k]
    return cfg["prices"]["default"]

def call_of(name, args):
    """One tool call as (tool, file, command): what it touched, in order."""
    a = args if isinstance(args, dict) else {}
    path = next((a[k] for k in FILE_KEYS if isinstance(a.get(k), str)), "")
    cmd = a.get("command") if isinstance(a.get("command"), str) else " ".join(a["command"]) if isinstance(a.get("command"), list) else ""
    q = a.get("query") or (a.get("queries") or [""])[0] if isinstance(a.get("queries"), list) else a.get("query") or ""
    return [norm_tool(name), (path or "")[:200], (cmd or q or "")[:160]]

class Entry:
    __slots__ = ("ts", "kind", "text", "tools", "files", "tin", "tout", "tcache", "cost", "model", "key", "calls")
    def __init__(self, ts, kind, key, text="", tools=None, files=None, tin=0, tout=0, tcache=0, cost=None, model="", calls=None):
        self.ts, self.kind, self.key, self.text = ts, kind, key, text
        self.tools, self.files, self.calls = tools or [], files or [], calls or []
        self.tin, self.tout, self.tcache, self.cost, self.model = tin, tout, tcache, cost, model

# ─────────────────────────── source parsers ───────────────────────────
# Each returns (session_id, cwd, sub_agent: bool, [Entry]) — entries in time order.

DUR_RX = re.compile(r'^Sub-agent "([^"]+)" (\w+) \((?:(\d+)h ?)?(?:(\d+)m ?)?(?:(\d+)s)?\)')

def parse_pi(path):
    sid, cwd, sub, entries = os.path.basename(path), "", False, []
    meta = {"subtype": "", "subid": "", "runs": []}
    for o in jsonl(path):
        t = o.get("type")
        if t == "session":
            sid, cwd = o.get("id", sid), o.get("cwd", "")
            sub = bool(o.get("parentSession"))
            meta["parent"] = o.get("parentSession") or ""
            continue
        if t == "session_info" and not meta["subtype"]:
            name = o.get("name") or ""
            if "#" in name:                        # in-process Agent children: "Explore#79de4122"
                meta["subtype"], meta["subid"] = name.split("#", 1)
            continue
        if t == "custom" and o.get("customType") == "subagents:record":
            d = o.get("data") or {}
            try:
                st, en = float(d.get("startedAt") or 0) / 1000, float(d.get("completedAt") or 0) / 1000
            except ValueError:
                st = en = 0
            if d.get("id") and st:
                meta["runs"].append(dict(key="pi:" + d["id"], kind="agent", type=d.get("type") or "?", description=(d.get("description") or "")[:200],
                                         status=d.get("status") or "", start=st, end=en or None))
            continue
        if t == "custom_message" and o.get("customType") == "subagent_result":
            m = DUR_RX.match(o.get("content") or "")
            ts = iso(o.get("timestamp", ""))
            if m and ts:
                h, mi, se = (int(x or 0) for x in m.groups()[2:])
                dur = h * 3600 + mi * 60 + se
                meta["runs"].append(dict(key=f"pane:{m.group(1)}:{int(ts)}", kind="pane", type="pane", description=m.group(1),
                                         status=m.group(2), start=ts - dur, end=ts))
            continue
        if t != "message":
            continue
        m = o.get("message") or {}
        ts = iso(o.get("timestamp", "")) or (m.get("timestamp", 0) / 1000 if isinstance(m.get("timestamp"), (int, float)) else None)
        if ts is None:
            continue
        role = m.get("role")
        content = m.get("content")
        key = "pi:" + hashlib.md5(f"{o.get('timestamp')}|{role}|{json.dumps(content)[:200]}".encode()).hexdigest()
        if role == "user":
            text = content if isinstance(content, str) else " ".join(c.get("text", "") for c in (content or []) if isinstance(c, dict) and c.get("type") == "text")
            entries.append(Entry(ts, "user", key, text=text))
        elif role == "assistant":
            tools, files, text, calls = [], [], "", []
            for c in content or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "toolCall":
                    tools.append(c.get("name", ""))
                    files += tool_files(c.get("arguments"))
                    calls.append(call_of(c.get("name", ""), c.get("arguments")))
                elif c.get("type") == "text" and not text:
                    text = c.get("text", "")
            u = m.get("usage") or {}
            cost = (u.get("cost") or {}).get("total")
            entries.append(Entry(ts, "assistant", key, text=text, tools=tools, files=files, calls=calls,
                                 tin=(u.get("input") or 0), tout=(u.get("output") or 0),
                                 tcache=(u.get("cacheRead") or 0) + (u.get("cacheWrite") or 0),
                                 cost=cost, model=m.get("model") or ""))
        elif role == "toolResult":
            entries.append(Entry(ts, "tool", key))
    return sid, cwd, sub, entries, meta

def parse_claude(path):
    sid, cwd, entries, seen_msg = os.path.splitext(os.path.basename(path))[0], "", [], set()
    sub = "/subagents/" in path
    meta = {"subtype": "", "subid": "", "runs": []}
    if sub:
        try:
            meta["subtype"] = json.load(open(path[:-len(".jsonl")] + ".meta.json")).get("agentType") or "subagent"
        except Exception:
            meta["subtype"] = "subagent"
    for o in jsonl(path):
        t = o.get("type")
        if t not in ("user", "assistant"):
            continue
        ts = iso(o.get("timestamp", ""))
        if ts is None:
            continue
        cwd = cwd or o.get("cwd", "")
        if o.get("isSidechain"):
            sub = sub or False
        m = o.get("message") or {}
        c = m.get("content")
        if t == "user":
            if o.get("isMeta"):
                continue
            if isinstance(c, str):
                entries.append(Entry(ts, "user", "cc:" + (o.get("uuid") or str(ts)), text=c))
            elif isinstance(c, list):
                if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                    entries.append(Entry(ts, "tool", "cc:" + (o.get("uuid") or str(ts))))
                else:
                    text = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                    if text:
                        entries.append(Entry(ts, "user", "cc:" + (o.get("uuid") or str(ts)), text=text))
        else:
            mid = m.get("id") or o.get("uuid")
            tools, files, text, calls = [], [], "", []
            for x in c or []:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    tools.append(x.get("name", ""))
                    files += tool_files(x.get("input"))
                    calls.append(call_of(x.get("name", ""), x.get("input")))
                    if x.get("name") in ("Task", "Agent"):
                        inp = x.get("input") or {}
                        meta["runs"].append(dict(key="cc:" + (x.get("id") or str(ts)), kind="agent", type=inp.get("subagent_type") or "general-purpose",
                                                 description=(inp.get("description") or "")[:200], status="launched", start=ts, end=None))
                elif isinstance(x, dict) and x.get("type") == "text" and not text:
                    text = x.get("text", "")
            u = m.get("usage") or {}
            first = mid not in seen_msg   # one API message is split over several lines: count usage once
            seen_msg.add(mid)
            entries.append(Entry(ts, "assistant", "cc:" + (o.get("uuid") or str(ts)), text=text, tools=tools, files=files, calls=calls,
                                 tin=(u.get("input_tokens") or 0) if first else 0,
                                 tout=(u.get("output_tokens") or 0) if first else 0,
                                 tcache=((u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)) if first else 0,
                                 cost=None, model=m.get("model") or ""))
    return sid, cwd, sub, entries, meta

CODEX_NOISE = re.compile(r"^\s*(<environment_context>|<user_instructions>|# AGENTS\.md|<permissions)", re.I)

def parse_codex(path):
    sid, cwd, model, entries = os.path.basename(path), "", "", []
    for o in jsonl(path):
        t, p = o.get("type"), o.get("payload") or {}
        ts = iso(o.get("timestamp", ""))
        if t == "session_meta":
            sid, cwd = p.get("id", sid), p.get("cwd", "")
            continue
        if t == "turn_context":
            model = p.get("model") or model
            cwd = p.get("cwd") or cwd
            continue
        if ts is None or t != "event_msg":
            continue
        pt = p.get("type")
        if pt == "item_completed":
            it = p.get("item") or {}
            ity = it.get("type")
            key = "cx:" + (it.get("id") or str(ts))
            if ity == "UserMessage":
                text = " ".join(x.get("text", "") for x in (it.get("content") or []) if isinstance(x, dict))
                if text and not CODEX_NOISE.match(text):
                    entries.append(Entry(ts, "user", key, text=text))
            elif ity == "AgentMessage":
                text = " ".join(x.get("text", "") for x in (it.get("content") or []) if isinstance(x, dict)) if isinstance(it.get("content"), list) else str(it.get("content") or "")
                entries.append(Entry(ts, "assistant", key, text=text, model=model))
            elif ity in ("CommandExecution", "FileChange", "Extension", "ImageView", "McpToolCall", "WebSearch"):
                name = {"CommandExecution": "bash", "FileChange": "edit", "ImageView": "read"}.get(ity) or (it.get("kind") or ity)
                files = list((it.get("changes") or {}).keys()) if ity == "FileChange" else ([it.get("path")] if it.get("path") else [])
                if ity == "CommandExecution":
                    files += tool_files({"command": it.get("command") if isinstance(it.get("command"), str) else " ".join(it.get("command") or [])})
                cmdtxt = it.get("command") if isinstance(it.get("command"), str) else " ".join(it.get("command") or [])
                calls = [[norm_tool(name), f[:200], ""] for f in files[:5]] if ity == "FileChange" else [[norm_tool(name), (files[0] if files else "")[:200], (cmdtxt or "")[:160]]]
                entries.append(Entry(ts, "assistant", key, tools=[name], files=files, model=model, calls=calls))
        elif pt == "token_count":
            u = ((p.get("info") or {}).get("last_token_usage")) or {}
            if u:
                cached = u.get("cached_input_tokens") or 0
                entries.append(Entry(ts, "assistant", f"cx:tok:{ts}", tin=max(0, (u.get("input_tokens") or 0) - cached),
                                     tout=u.get("output_tokens") or 0, tcache=cached, model=model))
    return sid, cwd, False, entries, {"subtype": "", "subid": "", "runs": []}

SOURCES = {
    "pi": (lambda: glob.glob(os.path.join(HOME, ".pi/agent/sessions/**/*.jsonl"), recursive=True), parse_pi),
    "claude": (lambda: glob.glob(os.path.join(HOME, ".claude/projects/**/*.jsonl"), recursive=True), parse_claude),
    "codex": (lambda: glob.glob(os.path.join(HOME, ".codex/sessions/**/*.jsonl"), recursive=True), parse_codex),
}

# ─────────────────────────── turns ───────────────────────────

def build_turns(source, path, sid, cwd, sub, entries, cfg):
    """Group entries into turns: a turn starts at a prompt and runs until the next prompt."""
    entries.sort(key=lambda e: e.ts)
    turns, cur, prev_end = [], None, None
    def close(t):
        if not t or len(t["entries"]) < 2:
            return
        es = t["entries"]
        segs, s0, last = [], es[0].ts, es[0].ts
        for e in es[1:]:
            if e.ts - last > GAP_CAP:
                if last > s0: segs.append([s0, last])
                s0 = e.ts
            last = e.ts
        if last > s0: segs.append([s0, last])
        busy = sum(b - a for a, b in segs)
        if busy <= 0:
            return
        tools, files, calls = Counter(), Counter(), []
        tin = tout = tc = 0; cost = 0.0; est = 0; model = ""; reply = ""; final = ""
        for e in es:
            for n in e.tools: tools[norm_tool(n)] += 1
            for f in e.files: files[f] += 1
            calls += e.calls
            tin += e.tin; tout += e.tout; tc += e.tcache
            if e.model: model = e.model
            if e.kind == "assistant" and e.text and not reply: reply = e.text
            if e.kind == "assistant" and e.text: final = e.text
            if e.cost is not None: cost += e.cost
            elif e.tin or e.tout or e.tcache:
                p = price(e.model, cfg); est = 1
                cost += (e.tin * p[0] + e.tout * p[1] + e.tcache * p[2]) / 1e6
        prompt = es[0].text or ""
        human = (not sub) and is_human_prompt(prompt)
        start = es[0].ts
        you_start = None
        if human:
            d = min(YOU_CAP, start - t["prev_end"]) if t["prev_end"] else min(YOU_CAP, 15 + len(prompt) / 4)
            you_start = start - max(5, d)
        turns.append(dict(id=hashlib.md5(f"{path}|{es[0].key}".encode()).hexdigest(), path=path, source=source, session=sid,
                          cwd=cwd, model=model, start=start, end=es[-1].ts, busy=busy, segments=json.dumps(segs),
                          human=int(human), prompt=prompt[:4000], prompt_chars=len(prompt), reply=(reply or "")[:1500], final=(final or "")[:2500],
                          you_start=you_start, tokens_in=tin, tokens_out=tout, tokens_cache=tc, cost=cost, cost_est=est,
                          tools=json.dumps(dict(tools)), files=json.dumps([f for f, _ in files.most_common(12)]),
                          calls=json.dumps(calls[:80])))
    for e in entries:
        if e.kind == "user":
            close(cur)
            cur = {"entries": [e], "prev_end": prev_end}
        elif cur:
            cur["entries"].append(e)
            prev_end = e.ts
    close(cur)
    return turns

def ingest(con, cfg, verbose=False):
    known = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path,size,mtime FROM files")}
    changed = 0
    for source, (lister, parser) in SOURCES.items():
        # creation order, so an original session owns history that forks/twins later copy
        paths = sorted(lister(), key=lambda p: os.path.basename(p) if source == "pi" else os.path.getmtime(p))
        for path in paths:
            st = os.stat(path)
            if known.get(path) == (st.st_size, st.st_mtime):
                continue
            try:
                sid, cwd, sub, entries, meta = parser(path)
            except Exception as e:
                print(f"odv: skip {path}: {e}", file=sys.stderr)
                continue
            con.execute("DELETE FROM seen WHERE path=?", (path,))
            con.execute("DELETE FROM turns WHERE path=?", (path,))
            fresh, copied_first = [], False
            first_user = next((e for e in entries if e.kind == "user"), None)
            for e in entries:
                row = con.execute("SELECT path FROM seen WHERE key=?", (e.key,)).fetchone()
                if row and row[0] != path:
                    if e is first_user: copied_first = True
                    continue   # copied history (Pi twins / forks): already counted in the original
                fresh.append(e)
            # a child session is a subagent only if its first prompt is its own (a fork copies the parent's)
            sub = sub and not copied_first
            if sub and not meta["subtype"]:
                meta["subtype"] = "session"            # a child session without a type (e.g. an older subagent)
            con.executemany("INSERT OR REPLACE INTO seen VALUES(?,?)", [(e.key, path) for e in fresh])
            turns = build_turns(source, path, sid, cwd, sub, fresh, cfg)
            for t in turns:
                t["sub"], t["subtype"], t["subid"] = int(sub), meta["subtype"] if sub else "", meta["subid"] if sub else ""
            con.execute("DELETE FROM subruns WHERE path=?", (path,))
            for r in meta["runs"]:   # records repeat as status changes (and in copied history): keep the latest
                old = con.execute("SELECT path FROM subruns WHERE key=?", (r["key"],)).fetchone()
                if old and old[0] != path and r["status"] in ("", "running"):
                    continue
                con.execute("INSERT OR REPLACE INTO subruns VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (r["key"], path, source, r["kind"], r["type"], r["description"], r["status"], r["start"], r["end"], sid))
            cols = list(turns[0].keys()) if turns else []
            if turns:
                con.executemany(f"INSERT OR REPLACE INTO turns({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                [tuple(t[c] for c in cols) for t in turns])
            con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?)", (path, st.st_size, st.st_mtime, source))
            changed += 1
            if verbose:
                print(f"  {source:6} {len(turns):4} turns  {path}")
    ingest_apps(con)
    con.commit()
    return changed

# ─────────────────────────── app time ───────────────────────────

TERMINALS = {"foot", "kitty", "alacritty", "ghostty", "com.mitchellh.ghostty", "hyprpi.agent", "org.wezfurlong.wezterm"}

def term_kind(app, title):
    t = (title or "").lower()
    if app == "hyprpi.agent" or t.startswith("π") or t.startswith("pi ") or t == "pi":
        return "Pi"
    if "claude" in t or t.startswith("✳"):
        return "Claude Code"
    if "codex" in t:
        return "Codex"
    return "shell"

def ingest_apps(con):
    if not os.path.exists(APPTIME):
        return
    last = con.execute("SELECT MAX(ts) FROM app").fetchone()[0] or 0
    rows = [o for o in jsonl(APPTIME) if isinstance(o.get("ts"), (int, float))]
    rows.sort(key=lambda o: o["ts"])
    out = []
    for a, b in zip(rows, rows[1:] + [None]):
        ts = a["ts"] / 1000 if a["ts"] > 1e11 else a["ts"]
        if ts <= last or a.get("idle") or not a.get("app"):
            continue
        nxt = (b["ts"] / 1000 if b and b["ts"] > 1e11 else b["ts"]) if b else None
        if nxt is None:
            continue    # the latest sample's span is not finished yet
        dur = min(nxt - ts, APP_GAP) if nxt - ts <= APP_GAP else 30
        app = a["app"]
        kind = term_kind(app.lower(), a.get("title", "")) if app.lower() in TERMINALS else ""
        out.append((ts, dur, app, (a.get("title") or "")[:200], kind))
    con.executemany("INSERT OR REPLACE INTO app VALUES(?,?,?,?,?)", out)

# ─────────────────────────── tagging ───────────────────────────

THEME_RULES = {   # generic defaults; config.json "themeRules" (theme -> regex) replaces them
    "Desktop & shell": r"omarchy|hypr(land|ctl|idle|lock)|waybar|quickshell|\bbar\b|top bar|plugins?\b|widget|\btheme|keybind|wallpaper|\.config/hypr|walker|mako|workspace|window",
    "Agent tooling": r"\bpi\b|\.pi/|\bagents?\b|subagent|extension|\bskill|claude|codex|\bprompt|session|\bmcp\b",
    "Science & viz": r"genome|matlab|rapids|\bcuda|\bgpu|visuali[sz]|\bplot|\bchart|\bd3\b|webgl|shader|simulat|dashboard|data.?vis|notebook|numpy|pandas",
    "AI models & APIs": r"\bnim\b|nvidia|openai|anthropic|\bllm|\bflux\b|diffusion|protein|molecul|image gen|api key|rate limit|hugging ?face",
    "Writing & notes": r"\bpoems?\b|obsidian|\bemail|\bmail\b|\bessay|\bletter\b|\bwrite (a|me|an)|markdown",
    "System & hardware": r"webcam|driver|kernel|pacman|\byay\b|\bapt\b|bluetooth|wi-?fi|network|pipewire|\baudio|battery|\bdisk|systemd|tailscale|\bmonitor\b|display|coredump|crash|\binstall",
    "Media & web": r"youtube|radio|music|spotify|twitter|\bvideo|podcast|\bbrowser|firefox|chromium",
    "Fun & play": r"\bgames?\b|cartoon|\btoys?\b|drawing|puzzle",
}
ACT_RULES = {
    "debug": r"\bfix|\bbug|error|broken|crash|doesn.?t work|not working|\bfail|wrong|issue|why (is|does|did|isn|doesn)|stuck|debug|glitch|weird",
    "research": r"research|search|find out|what is|what are|how does|how do|compare|look up|investigate|explain|\bdocs?\b|which .* best|\?$",
    "config": r"config|setting|keybind|\binstall|set ?up|enable|disable|\btheme|colou?r|alias|permission|rename|\bname\b",
    "write": r"\bwrite (a|me|an|up)|\bpoem|\bemail|/note|/idea|/poem|draft|summari[sz]e|\bletter|readme|essay|\bnotes?\b",
    "build": r"\bbuild|create|\bmake|\badd\b|implement|\bnew\b|let.?s (do|build|make)|widget|feature|prototype",
}

STOP = set("""a about above after again against all also am an and any are aren as at be because been before being below between both but by can
cannot could couldn did didn do does doesn doing don down during each few for from further had has have having he her here hers herself him himself his how i if in
into is isn it its itself just let lets like make me more most much must my myself need no nor not now of off on once only or other our ours ourselves out over own
please same she should so some such than that the their theirs them themselves then there these they this those through to too under until up us very was we
were what when where which while who whom why will with would you your yours yourself yourselves ok okay yes yeah sure thanks thank maybe want wanted think
know see get got use using used also still really one two three new way thing things something anything everything able going gonna wanna kind look looks
lets try trying again first last next back well good great nice fine right left bit lot lots little much many every sure give gave take took make made
http https www com org html file files folder dir home work tmp null true false none each into onto via per etc can't don't it's i'm you're that's
should.ve would.ve could.ve dont doesnt cant isnt wont didnt youre thats whats heres theres ill ive id hmm uh um also just now then
already either short count ideas fitting instead actually probably basically currently exactly""".split()) | {os.path.basename(HOME).lower()}

# words too generic to be interesting as a trend in this corpus
TERM_STOP = set("""agent agents name readme message messages file please repo code session sessions prompt window windows
command commands text line lines test tests update updated change changes working work done okay error errors issue
help show open close start stop run running create created other same different before after instead should""".split())

TERM_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{2,30}")

def terms_of(prompt, files):
    out = set()
    for w in TERM_TOKEN.findall(prompt or ""):
        w = w.strip(".-_").lower()
        if len(w) >= 4 and w not in STOP and not w.isdigit():
            out.add(w)
    for f in files:
        b = os.path.basename(f.rstrip("/")).lower()
        b = re.sub(r"\.(py|js|ts|mjs|qml|md|json|jsonl|lua|toml|sh|html|css|txt)$", "", b)
        if 3 <= len(b) <= 30 and b not in STOP:
            out.add(b)
    return out

def topic_candidates(t):
    """Code-extracted topic candidates: project dirs, plugin ids, config apps, from files touched and cwd."""
    c = Counter()
    for f in json.loads(t["files"] or "[]") + [t["cwd"] or ""]:
        f = f.replace("~", HOME)
        for pat in (r"/Work/([^/]+)", r"/omarchy/plugins/([^/]+)", r"/\.config/([^/]+)", r"/\.pi/agent/extensions/([^/.]+)",
                    r"/\.pi/agent/(skills|notes)/([^/]+)", r"/Obsidian/([^/]+)"):
            m = re.search(pat, f)
            if m:
                name = m.groups()[-1]
                if name and name not in ("Work",) and not name.startswith("."):
                    c[re.sub(r"\.(md|json|py|js|html)$", "", name)] += 1
                break
    return c

def heuristic_tag(t, themes):
    text = ((t["prompt"] or "")[:1500] + " " + " ".join(json.loads(t["files"] or "[]")) + " " + (t["cwd"] or "")).lower()
    scores = {th: len(re.findall(rx, text)) for th, rx in ACTIVE_THEME_RULES.items() if th in themes}
    theme = max(scores, key=scores.get) if scores and max(scores.values()) > 0 else "Other"
    tools = json.loads(t["tools"] or "{}")
    p = (t["prompt"] or "").lower()[:1500]
    a = {k: len(re.findall(rx, p)) for k, rx in ACT_RULES.items()}
    a["build"] += 1 if tools.get("edit", 0) + tools.get("write", 0) > 0 else 0
    a["research"] += 1 if tools.get("web", 0) > 1 else 0
    a["chat"] = 2 if sum(tools.values()) == 0 and len(p) < 300 else 0
    act = max(a, key=a.get) if max(a.values()) > 0 else ("build" if sum(tools.values()) else "chat")
    cands = topic_candidates(t)
    if cands:
        topic = cands.most_common(1)[0][0]
    else:
        words = [w for w in terms_of(t["prompt"], []) if len(w) > 4]
        topic = sorted(words, key=len, reverse=True)[0] if words else "misc"
    return theme, topic, act

def jev_key():
    k = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if k:
        return k
    p = os.path.join(HOME, ".pi/agent/secrets/typesafe_api_key")
    return open(p).read().strip() if os.path.exists(p) else None

def jev_model(cfg):
    return cfg.get("jevModel") or "jev-latest"

def jev_method(cfg):
    """Tags are cached per model: method 'jev' for jev-latest, 'jev:<model>' for any other."""
    m = jev_model(cfg)
    return "jev" if m == "jev-latest" else "jev:" + m

def jev_call(key, state, questions, retries=4, model="jev-latest"):
    body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
    for i in range(retries):
        req = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["answers"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 529, 500, 502, 503) and i < retries - 1:
                time.sleep(2 ** i + 1)
                continue
            raise
    return None

def jev_tag(key, t, cfg, topic_vocab, known=None):
    """One Jev request per unit: theme (choice), activity (choice), topic (choice over code-extracted candidates)."""
    theme_h, topic_h, _ = heuristic_tag(t, cfg["themes"])
    cands = [c for c, _ in topic_candidates(t).most_common(8)]
    words = [w for w in terms_of(t["prompt"], []) if len(w) > 4 and w not in TERM_STOP and (known is None or w in known)]
    cands += sorted(words, key=lambda w: (known or {}).get(w, 0), reverse=True)[:8]
    cands += [v for v in topic_vocab[:20]]
    seen, opts = set(), {}
    for c in cands:
        if c.lower() not in seen:
            seen.add(c.lower()); opts[c] = None
    opts["(none of these)"] = "No listed name describes what this work is about"
    state = {"prompt": (t["prompt"] or "")[:2500], "previous_prompt_in_session": (t.get("prev_prompt") or "")[:600], "agent_first_reply": (t["reply"] or "")[:800],
             "files_touched": json.loads(t["files"] or "[]")[:10], "tools_used": json.loads(t["tools"] or "{}"),
             "working_directory": t["cwd"]}
    themes = dict(cfg["themes"]); themes["Other"] = "None of the other themes fits"
    q = {
        "theme": {"type": "choice", "instructions": "Which theme best describes what the user and the coding agent were working on in this exchange?", "criteria": themes},
        "activity": {"type": "choice", "instructions": "What kind of work did the agent mainly do in this exchange?", "criteria": cfg["activities"]},
        "topic": {"type": "choice", "instructions": "Which short name best labels the specific project or subject this exchange is about? Prefer a project, tool or subject name over a generic word.", "criteria": opts},
    }
    a = jev_call(key, state, q, model=jev_model(cfg))
    topic = a["topic"]["choice"]
    if topic == "(none of these)":
        topic = topic_h
    probs = {k: {"choice": v.get("choice"), "confidence": v.get("confidence"), "probabilities": v.get("probabilities")} for k, v in a.items()}
    return a["theme"]["choice"], topic, a["activity"]["choice"], min(a["theme"].get("confidence", 0), a["activity"].get("confidence", 0)), probs

def session_units(con):
    """Session-level units: concatenated prompts of each session, attached to every turn of that session."""
    units = defaultdict(list)
    for r in con.execute("SELECT * FROM turns ORDER BY start"):
        units[r["session"]].append(dict(r))
    out = {}
    for sid, ts in units.items():
        merged = dict(ts[0])
        merged["prompt"] = "\n---\n".join((t["prompt"] or "")[:400] for t in ts)[:4000]
        files = Counter()
        tools = Counter()
        for t in ts:
            for f in json.loads(t["files"] or "[]"): files[f] += 1
            for k, v in json.loads(t["tools"] or "{}").items(): tools[k] += v
        merged["files"] = json.dumps([f for f, _ in files.most_common(12)])
        merged["tools"] = json.dumps(dict(tools))
        out[sid] = (merged, [t["id"] for t in ts])
    return out

# ───────── the agent's side of a turn: measured (free) + topic hierarchy (Jev) ─────────

def footprint(calls):
    """What the agent touched, measured from its tool calls."""
    changed, read, cmds, web = [], [], [], 0
    for tool, path, cmd in calls:
        if tool in ("edit", "write") and path and path not in changed: changed.append(path)
        elif tool == "read" and path and path not in read: read.append(path)
        if tool == "bash" and cmd: cmds.append(cmd)
        if tool == "web": web += 1
    text = "\n".join(cmds)
    return {"changed": changed, "read": read, "commands": len(cmds), "web": web,
            "commit": bool(re.search(r"\bgit\b[^\n;|&]*\bcommit\b", text)), "push": bool(re.search(r"\bgit\b[^\n;|&]*\bpush\b", text)),
            "install": bool(re.search(r"\b(pacman|yay|paru)\s+-S|\bpip3? install|\bnpm (i|install)\b|\buv (add|pip install)", text)),
            "services": bool(re.search(r"\b(systemctl|journalctl|coredumpctl)\b", text))}

TOOL_PATTERNS = ["exploring", "edit–test loop", "editing", "shell ops", "web research", "delegating", "driving the desktop", "coordinating", "talking only"]

def tool_pattern(calls):
    """How the agent worked, from the order of its tool calls."""
    seq = [c[0] for c in calls]
    n = len(seq)
    if not n: return "talking only"
    k = Counter(seq)
    loops = sum(1 for a, b in zip(seq, seq[1:]) if a in ("edit", "write") and b == "bash")
    if k["subagent"] and k["subagent"] >= 0.25 * n: return "delegating"
    if k["desktop"] >= 0.3 * n: return "driving the desktop"
    if k["web"] >= 0.4 * n: return "web research"
    if k["rooms"] >= 0.4 * n: return "coordinating"
    if loops >= 2: return "edit–test loop"
    if k["edit"] + k["write"]: return "editing"
    if k["read"] >= 0.5 * n: return "exploring"
    if k["bash"] >= 0.5 * n: return "shell ops"
    return "exploring"

SYSTEM_PARTS = [   # (part, pattern over lower-cased paths + commands, weight)
    ("window manager", r"\.config/hypr|hyprctl|hyprland|hyprwrlds|hl\.dsp", 1.0),
    ("desktop shell", r"omarchy|quickshell|\bqs -p|waybar|/usr/share/omarchy|mako|walker", 1.0),
    ("agent tooling", r"\.pi/|\.claude|\.codex|hyprpi|herdr|/pi-[a-z]|/skills/|pi-jot|message board", 1.0),
    ("hardware & drivers", r"pacman|\byay\b|modprobe|lsusb|lspci|v4l2|webcam|camera|bluetoothctl|pactl|wpctl|pipewire|alsa|/dev/|firmware|dkms|nvidia-smi", 1.0),
    ("system services", r"systemctl|journalctl|coredumpctl|/etc/|udev|\bsudo\b", 1.0),
    ("apps & web", r"\.config/(?!hypr|omarchy|systemd)|brave|chromium|firefox|spotify|slack|whatsapp|xdg-open|flatpak|https?://", 0.8),
    ("notes", r"obsidian", 1.0),
    ("your projects", r"~/work/|" + re.escape(HOME.lower()) + "/work/", 0.5),
]

def system_part(calls):
    if not calls: return "none"
    score = Counter()
    for tool, path, cmd in calls:
        t = (path + " " + cmd).lower()
        for part, rx, w in SYSTEM_PARTS:
            if re.search(rx, t): score[part] += w
    return score.most_common(1)[0][0] if score else "other"

REPO_RX = [(r"/Work/([^/]+)", "{}"), (r"/\.config/omarchy/plugins/([^/]+)", "plugin {}"), (r"/\.pi/agent/extensions/([^/.]+)", "pi ext {}"),
            (r"/\.pi/agent/skills/([^/]+)", "skill {}"), (r"/\.pi/agent/(notes|npm|sessions)", "pi {}"), (r"/\.config/([^/]+)", "~/.config/{}"),
            (r"/Obsidian/([^/]+)", "Obsidian {}"), (r"^/usr/share/omarchy", "omarchy (system)"), (r"^/tmp/", "/tmp"), (r"/Downloads/", "~/Downloads")]

def repos_of(calls, cwd=""):
    """Repos and folders a turn touched: ~/Work/<repo>, Omarchy plugins, Pi extensions/skills, ~/.config/<app>, …"""
    out = []
    paths = [c[1] for c in calls if c[1]] + re.findall(r"(?:~|" + re.escape(HOME) + r")/[\w.\-/]+|/usr/share/omarchy[\w.\-/]*|/tmp/[\w.\-/]+", "\n".join(c[2] for c in calls))
    for p in paths:
        p = p.replace("~", HOME, 1) if p.startswith("~") else p
        for rx, fmt in REPO_RX:
            m = re.search(rx, p)
            if m:
                name = fmt.format(*m.groups()) if m.groups() else fmt
                if name not in out and not name.endswith(".md"): out.append(name)
                break
    return out[:6]

# everyday shell plumbing: not interesting as "what the agent ran"
GENERIC_CMDS = set("""cd echo true false sleep printf set export source for do done if then else fi local while read test
grep rg head tail sed awk cat less more ls ll find wc sort uniq cut tr tee xargs jq yq cp mv rm mkdir rmdir touch ln chmod chown
stat file diff cmp date basename dirname realpath readlink which type command env timeout seq printf nl column fold paste
kill pkill pgrep ps sh bash zsh exit return eval exec wait trap""".split())
CODE_WORDS = set("function const let var import from def class print console await async try catch clients new else elif".split())

def command_names(calls):
    """The programs an agent ran (first real word of each shell command): git, hyprctl, pacman, …"""
    out = []
    for tool, _, cmd in calls:
        if tool != "bash" or not cmd: continue
        for part in re.split(r"&&|\|\||;|\|", cmd.split("\n", 1)[0]):   # first line only: skip heredoc bodies
            w = part.strip().split()
            while w and (w[0] in ("sudo", "env", "time", "nohup", "setsid", "exec") or "=" in w[0]): w = w[1:]
            if not w: continue
            x = os.path.basename(re.sub(r"^[$(`'\"{]+", "", w[0]))
            if re.fullmatch(r"[a-z][\w.+-]{1,30}", x) and x not in GENERIC_CMDS and x not in CODE_WORDS:
                if x not in out: out.append(x)
    return out[:8]

AGENT_AREAS = {   # generic defaults; config.json "agentAreas" replaces them
    "Desktop shell": "The desktop shell: the top bar and its widgets and plugins, themes, menus, notifications, launcher",
    "Window manager": "The window manager: windows, workspaces, keybindings, dispatch, monitors",
    "Agent tooling": "Tools for AI coding agents: agent harnesses and their extensions and skills, subagents, prompts, Claude Code, Codex and Pi setup",
    "Dashboards & observability": "Visualising and analysing activity: dashboards, data collectors, charts",
    "AI models & APIs": "Hosted AI models and APIs: image, 3D and protein generation, model access, API keys, rate limits",
    "Science projects": "Scientific research code and figures: analysis, simulations, GPU code",
    "Linux system & hardware": "The Linux system itself: packages, drivers, audio, bluetooth, networking, services, crashes, performance",
    "Apps & web": "Desktop apps and web services: browsers, music, chat apps, email, video, social media",
    "Writing & notes": "Writing and notes: notes, poems, emails, documents, reading texts",
    "Fun & play": "Things made for fun: games, drawings, stories",
}
SUBJECT_SKIP = set("cd ls cat grep rg sed awk echo head tail find python3 python node bash sh true printf sleep wc sort uniq jq xargs test mkdir rm cp mv chmod ln tee env set export source pwd which file stat curl".split())

def agent_candidates(t, known=None):
    """Code-extracted candidates for the project and the subject of an agent turn."""
    calls = json.loads(t.get("calls") or "[]")
    files = [c[1] for c in calls if c[1]] + json.loads(t.get("files") or "[]")
    projects = [c for c, _ in topic_candidates({"files": json.dumps(files), "cwd": t.get("cwd") or ""}).most_common(10)]
    subj = Counter()
    for f in files:
        b = re.sub(r"\.(py|js|ts|mjs|qml|md|json|jsonl|lua|toml|sh|html|css|txt|conf|ini|yaml|yml)$", "", os.path.basename(f.rstrip("/")))
        if 3 <= len(b) <= 40: subj[b] += 2
    for _, _, cmd in calls:
        w = re.split(r"[\s;|&]+", cmd.strip())
        for x in w[:6]:
            x = os.path.basename(x)
            if re.fullmatch(r"[a-z][\w.-]{2,24}", x) and x not in SUBJECT_SKIP: subj[x] += 1; break
    for wd in terms_of((t.get("final") or t.get("reply") or "")[:2000], []):
        if len(wd) > 4 and (known is None or wd in known) and wd not in TERM_STOP: subj[wd] += 0.5
    return projects, [x for x, _ in subj.most_common(14)]

def agent_vocab(con, cfg):
    """Project names already in use (from prompt-level Jev topics, grouped to their parent project)."""
    M = jev_method(cfg)
    tops = Counter()
    for (tp,) in con.execute("SELECT topic FROM tags WHERE method=? AND level='turn'", (M,)):
        tops[clean_topic(tp)] += 1
    parents = topic_parents(set(tops))
    grouped = Counter()
    for tp, n in tops.items(): grouped[parents.get(tp, tp)] += n
    return [x for x, _ in grouped.most_common(30) if x not in ("misc", "clipboard")]

def jev_agent_topic(key, t, cfg, known=None, vocab=()):
    """The agent's side of a turn, as a topic hierarchy: area → project → subject (one request, three choices)."""
    projects, subjects = agent_candidates(t, known)
    if t.get("prompt_topic"): projects = [t["prompt_topic"]] + projects
    def opts(xs):
        seen, o = set(), {}
        for x in xs:
            if x and x.lower() not in seen: seen.add(x.lower()); o[x] = None
        o["(none of these)"] = "None of the listed names fits"
        return o
    areas = dict(cfg.get("agentAreas") or AGENT_AREAS); areas["Other"] = "None of the other areas fits"
    state = {"user_prompt": (t.get("prompt") or "")[:600], "agent_final_message": (t.get("final") or t.get("reply") or "")[:2000],
             "tool_calls": [f"{c[0]} {c[1] or c[2]}"[:140] for c in json.loads(t.get("calls") or "[]")[:25]],
             "working_directory": t.get("cwd")}
    q = {
        "area": {"type": "choice", "instructions": "Judging from what the agent did and wrote (its tool calls and messages): which area of this laptop's work was the agent working on?", "criteria": areas},
        "project": {"type": "choice", "instructions": "Which project, folder or component was the agent working on? Prefer a specific project name.", "criteria": opts(projects + list(vocab)[:12])},
        "subject": {"type": "choice", "instructions": "Which short name best labels the specific thing the agent worked on in this exchange (a file, feature, command or concept)?", "criteria": opts(subjects)},
    }
    a = jev_call(key, state, q, model=jev_model(cfg))
    probs = {k: {"choice": v.get("choice"), "confidence": v.get("confidence"), "probabilities": v.get("probabilities")} for k, v in a.items()}
    pj, sj = a["project"]["choice"], a["subject"]["choice"]
    return (a["area"]["choice"], pj if pj != "(none of these)" else (projects[0] if projects else "misc"),
            sj if sj != "(none of these)" else (subjects[0] if subjects else "misc"), a["area"].get("confidence", 0), probs)

def jev_units(con, cfg, lv):
    """Units that need a Jev request at this level for the configured model: [(unit, ids)].
    A session is (re)asked only when untagged, or when it has grown by half since it was tagged;
    smaller growth just copies the session's labels to its new turns (no request)."""
    M = jev_method(cfg)
    if lv == "turn":
        todo = [dict(r) for r in con.execute(f"""SELECT t.*, (SELECT p.prompt FROM turns p WHERE p.session=t.session AND p.start<t.start ORDER BY p.start DESC LIMIT 1) AS prev_prompt
                    FROM turns t LEFT JOIN tags g ON g.turn=t.id AND g.method=? AND g.level='turn' WHERE g.turn IS NULL ORDER BY t.start DESC""", (M,))]
        return [(t, [t["id"]]) for t in todo], []
    tagged = {r[0]: r for r in con.execute("SELECT turn, theme, topic, activity, conf FROM tags WHERE method=? AND level='session'", (M,))}
    ask, copy = [], []
    for sid, (u, ids) in session_units(con).items():
        have = [i for i in ids if i in tagged]
        new = [i for i in ids if i not in tagged]
        if not new: continue
        if not have or len(new) >= max(2, len(have) / 2): ask.append((u, ids))
        else:
            src = tagged[have[-1]]
            copy += [(i, src[1], src[2], src[3], src[4]) for i in new]
    return ask, copy

def jev_pending(con, cfg):
    con.row_factory = sqlite3.Row
    return {lv: len(jev_units(con, cfg, lv)[0]) for lv in ("turn", "session")}

def tag(con, cfg, use_jev=None, limit=None, level=None, verbose=False, progress=None):
    con.row_factory = sqlite3.Row
    now = time.time()
    levels = [level] if level else ["turn", "session"]
    # rules: always, for everything missing
    for lv in levels:
        if lv == "turn":
            # retag all turns (cheap); a follow-up that matches no theme ("yes", "ok go ahead") inherits the session's last one
            rows = con.execute("SELECT * FROM turns ORDER BY session, start").fetchall()
            last, out = {}, []
            for r in rows:
                th, tp, ac = heuristic_tag(dict(r), cfg["themes"])
                if th == "Other" and r["session"] in last:
                    th, tp = last[r["session"]]
                elif th != "Other":
                    last[r["session"]] = (th, tp)
                out.append((r["id"], "rules", "turn", th, tp, ac, 0.0, now))
            con.executemany("INSERT OR REPLACE INTO tags VALUES(?,?,?,?,?,?,?,?)", out)
        else:
            for sid, (unit, ids) in session_units(con).items():
                th, tp, ac = heuristic_tag(unit, cfg["themes"])
                con.executemany("INSERT OR REPLACE INTO tags VALUES(?,?,?,?,?,?,?,?)", [(i, "rules", "session", th, tp, ac, 0.0, now) for i in ids])
    con.commit()
    key = jev_key()
    if not use_jev or not key:          # Jev only when explicitly asked (odv tag --jev / the Jev app), never in `odv run`
        if use_jev and not key:
            print("odv: no TypeSafe key (TYPESAFE_API_KEY or ~/.pi/agent/secrets/typesafe_api_key); rules only", file=sys.stderr)
        return 0
    # one Jev tagger at a time (the timer and a manual back-fill must not double up)
    import fcntl
    lockf = open(os.path.join(DATA, "jev.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if verbose: print("odv: another Jev tagging run is active; skipping")
        return 0
    done = 0
    M = jev_method(cfg)
    vocab = [r[0] for r in con.execute("SELECT topic, COUNT(*) n FROM tags WHERE method=? GROUP BY topic ORDER BY n DESC LIMIT 30", (M,))]
    df = Counter()   # words seen in >= 3 prompts: real vocabulary, not one-off typos
    for (p,) in con.execute("SELECT prompt FROM turns WHERE human=1"):
        for w in terms_of(p, []): df[w] += 1
    known = {w: n for w, n in df.items() if n >= 3}
    for lv in levels:
        n_lv = 0
        units, copy = jev_units(con, cfg, lv)
        con.executemany("INSERT OR REPLACE INTO tags VALUES(?,?,?,?,?,?,?,?)", [(i, M, lv, th, tp, ac, cf, now) for i, th, tp, ac, cf in copy])
        if limit:
            units = units[:limit]
        def work(u):
            try:
                return u, jev_tag(key, u[0], cfg, vocab, known)
            except Exception as e:
                print(f"odv: jev failed: {e}", file=sys.stderr)
                return u, None
        with ThreadPoolExecutor(6) as ex:
            for (unit, ids), res in ex.map(work, units):
                if not res:
                    continue
                th, tp, ac, conf, probs = res
                con.executemany("INSERT OR REPLACE INTO tags VALUES(?,?,?,?,?,?,?,?)", [(i, M, lv, th, tp, ac, conf, now) for i in ids])
                con.execute("INSERT OR REPLACE INTO jevprobs VALUES(?,?,?,?)", (ids[0], M, lv, json.dumps(probs)))
                if progress: progress(lv, n_lv + 1, len(units))
                done += 1; n_lv += 1
                if n_lv % 25 == 0:
                    con.commit()
                    if verbose: print(f"  jev {lv}: {n_lv}/{len(units)}")
    con.commit()
    return done

# ─────────────────────────── build snapshot ───────────────────────────

RANGES = {  # hours, stream/conc buckets, mix buckets
    "15m": (0.25, 15, 5, "last 15 minutes"),
    "1h": (1, 12, 6, "last hour"),
    "6h": (6, 24, 12, "last 6 hours"),
    "24h": (24, 24, 12, "last 24 hours"),
    "7d": (168, 42, 7, "last 7 days"),
    "1mo": (720, 30, 15, "last 30 days"),
    "1y": (8760, 52, 12, "last 12 months"),
}
FAMILIES = ["haiku", "sonnet", "opus", "fable", "gpt", "kimi", "other"]

def family(model):
    m = (model or "").lower()
    for f in FAMILIES[:-1]:
        if f in m: return f
    return "other" if m else "unknown"

def short_model(model):
    """claude-haiku-4-5-20251001 → haiku-4-5, moonshotai/kimi-k2.6 → kimi-k2.6"""
    m = (model or "").split("/")[-1]
    m = re.sub(r"^claude-", "", m)
    m = re.sub(r"-\d{8}$", "", m)
    return m or "unknown"

KINDS = {"pi": "Pi", "claude": "Claude Code", "codex": "Codex", "sub": "Subagents"}

def clip_segs(segs, a, b):
    out = []
    for s, e in segs:
        s2, e2 = max(s, a), min(e, b)
        if e2 > s2: out.append((s2, e2))
    return out

def spread(segs, t0, blen, n, arr, w=1.0):
    """Add the seconds of each segment into equal buckets of length blen starting at t0."""
    for s, e in segs:
        while s < e:
            i = int((s - t0) // blen)
            nb = t0 + (i + 1) * blen
            chunk = min(e, nb) - s
            if 0 <= i < n: arr[i] += chunk * w
            s += chunk

def union_len(iv):
    tot, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(iv):
        if cur_e is None or s > cur_e:
            if cur_e is not None: tot += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None: tot += cur_e - cur_s
    return tot

def peak_overlap(iv):
    ev = sorted([(s, 1) for s, e in iv] + [(e, -1) for s, e in iv], key=lambda x: (x[0], x[1]))
    c = m = 0
    for _, d in ev:
        c += d; m = max(m, c)
    return m

# What the agent did in a turn, measured from its tool calls (no model involved).
WORK = ["editing", "running", "reading", "web", "delegating", "talking"]
WORK_OF = {"edit": "editing", "write": "editing", "bash": "running", "desktop": "running", "read": "reading",
           "web": "web", "subagent": "delegating", "rooms": "talking", "models": "running"}
WORK_WEIGHT = {"editing": 3, "delegating": 3, "web": 2, "running": 1, "reading": 1, "talking": 1}

def work_kind(tools):
    score = Counter()
    for name, n in tools.items():
        wk = WORK_OF.get(name)
        if wk: score[wk] += n * WORK_WEIGHT[wk]
    return score.most_common(1)[0][0] if score else "talking"

def load_units(con, cfg, level, methods=("rules",)):
    """Units for charts. The Data vis panel uses rules only (methods=("rules",)); Jev tags are for the Jev app."""
    con.row_factory = sqlite3.Row
    tags = {}
    for method in methods:   # later methods override earlier ones
        for r in con.execute("SELECT turn, theme, topic, activity FROM tags WHERE method=? AND level=?", (method, level)):
            tags[r[0]] = (r[1], r[2], r[3])
    spell = defaultdict(Counter)
    for th, tp, ac in tags.values():
        c = clean_topic(tp); spell[c.lower()][c] += 1
    canon = {k: v.most_common(1)[0][0] for k, v in spell.items()}
    tags = {k: (th, canon[clean_topic(tp).lower()], ac) for k, (th, tp, ac) in tags.items()}
    units = []
    for r in con.execute("SELECT * FROM turns"):
        th, tp, ac = tags.get(r["id"], ("Other", "misc", "chat"))
        if th not in cfg["themes"]: th = "Other"
        if ac not in cfg["activities"]: ac = "chat"
        segs = [tuple(s) for s in json.loads(r["segments"])]
        tokens = (r["tokens_in"] or 0) + (r["tokens_out"] or 0) + (r["tokens_cache"] or 0)
        units.append(dict(id=r["id"], source=r["source"], kind="sub" if r["sub"] else r["source"], sub=r["sub"], subtype=r["subtype"] or "",
                          subid=r["subid"] or "", fam=family(r["model"]), model=short_model(r["model"]) if r["model"] else "unknown",
                          session=r["session"], start=r["start"], end=r["end"], segs=segs,
                          busy=r["busy"], human=r["human"], you=(r["you_start"], r["start"]) if r["human"] and r["you_start"] else None,
                          tokens=tokens, cost=r["cost"] or 0, est=r["cost_est"], theme=th, topic=tp, tagact=ac,
                          act=work_kind(json.loads(r["tools"] or "{}")),
                          tools=json.loads(r["tools"] or "{}"), terms=terms_of(r["prompt"], json.loads(r["files"] or "[]")) if r["human"] else set()))
    return units

TOPIC_SPLIT = re.compile(r"[-_.]")

def clean_topic(t):
    """Tidy topic names: clipboard-<hash> → clipboard, long note titles → their first word."""
    t = (t or "").strip() or "misc"
    if re.search(r"(?i)(^|[-_])clipboard([-_]|$)", t): return "clipboard"
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", t): t = re.sub(r"[-_]?[0-9a-f]{8}-[0-9a-f-]+", "", t) or "misc"
    if len(t.split()) >= 3 or "—" in t: t = t.split()[0]
    return t

def topic_parents(names):
    """Group topics under a parent project: tool-rooms → tool, app-twin → app, fooAI → foo.
    A parent is the first word of a hyphenated name when that word is itself a topic or starts 2+ topics,
    otherwise the longest other topic (4+ chars) the name starts with."""
    low = {n.lower(): n for n in names}
    first = Counter(TOPIC_SPLIT.split(n.lower())[0] for n in names if TOPIC_SPLIT.search(n))
    out = {}
    for n in names:
        l = n.lower(); head = TOPIC_SPLIT.split(l)[0]
        if head != l and len(head) >= 2 and (head in low or first[head] >= 2):
            out[n] = low.get(head, head)
            continue
        # fooAI → foo (a capital starts the rest), but "hyprcu" is not "hypr" + "cu"
        pref = [o for o in low if o != l and len(o) >= 4 and l.startswith(o) and n[len(o):len(o) + 1].isupper()]
        out[n] = low[max(pref, key=len)] if pref else n
    return out

def leaf_name(topic, parent):
    if topic == parent: return parent
    rest = topic[len(parent):] if topic.lower().startswith(parent.lower()) else topic
    return rest.strip("-_ /.") or topic

def build(con, cfg, now=None, out=SNAPSHOT):
    """Build both tag levels; top-level fields mirror the configured one, `levels` holds both for the panel's switch."""
    snap = build_level(con, cfg, "turn", now)     # rules only: the Data vis panel never uses Jev
    tmp = out + ".tmp"
    json.dump(snap, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, out)
    return snap

# Time-slider frames: the same range, ending `step` earlier each frame (frame 0 = now). 1y has none.
FRAMES = {"15m": (300, 12 * 24), "1h": (600, 6 * 48), "6h": (1800, 2 * 24 * 7), "24h": (3600, 7 * 24), "7d": (6 * 3600, 120), "1mo": (86400, 90)}
SHORT_HEAT = {"15m": (15, 60), "1h": (12, 300), "6h": (24, 900)}   # heat columns, seconds per column

def build_frames(con, cfg, now=None):
    """Write frames-<range>.json for the panel's time slider: frame i ends i*step before now, back to the first data."""
    now = now or time.time()
    units = load_units(con, cfg, "turn")
    first = min((u["start"] for u in units), default=now)
    for key, (step, cap) in FRAMES.items():
        out, i = [], 0
        while i <= cap and (i == 0 or now - i * step > first + step):
            out.append(build_level(con, cfg, "turn", now - i * step, units=units, only=key)["ranges"][key]); i += 1
        path = os.path.join(DATA, f"frames-{key}.json")
        json.dump({"generated": now, "step": step, "frames": out}, open(path + ".tmp", "w"), separators=(",", ":"))
        os.replace(path + ".tmp", path)

_TP_CACHE = {}
_SETUP_CACHE = {}
_PEAK_CACHE = {}

def build_level(con, cfg, level, now=None, units=None, only=None):
    units = units if units is not None else load_units(con, cfg, level)
    now = now or time.time()
    now_h = (int(now) // 3600 + 1) * 3600          # end of the current hour
    themes_all = list(cfg["themes"].keys()) + ["Other"]
    acts = WORK
    kinds = ["pi", "claude", "codex", "sub"]
    # theme order: by all-time busy, so colours are stable across ranges
    # range-independent setup, cached across the many build_level calls of `odv frames`
    ck = (id(units), len(units), level, sum(u["tokens"] for u in units))
    if _SETUP_CACHE.get("key") != ck:
        tot_theme = Counter()
        for u in units: tot_theme[u["theme"]] += u["busy"]
        themes = [t for t in sorted(themes_all, key=lambda t: -tot_theme[t]) if tot_theme[t] > 0] or themes_all
        # term document frequency (to drop typos / one-offs)
        df = Counter()
        for u in units:
            for w in u["terms"]: df[w] += 1
        apps = con.execute("SELECT ts, dur, app, title, kind FROM app").fetchall()
        # model of each subagent child session (most tokens), to colour runs by model family
        fam_by_subid = {}
        for sid_, mod_, tok_ in sorted(((u["subid"], u["model"], u["tokens"]) for u in units if u["sub"] and u["subid"]), key=lambda x: x[2]):
            fam_by_subid[sid_] = mod_
        runs = []
        for r in con.execute("SELECT key, kind, type, description, status, start, end FROM subruns ORDER BY start"):
            d = dict(zip(("key", "kind", "type", "description", "status", "start", "end"), r))
            rid = d["key"].split(":", 1)[1] if d["key"].startswith("pi:") else ""
            d["fam"] = fam_by_subid.get(rid[:8], "unknown")
            runs.append(d)
        n_human = max(10, sum(1 for u in units if u["human"]))
        _SETUP_CACHE.clear()
        _SETUP_CACHE.update(key=ck, v=(tot_theme, themes, df, apps, fam_by_subid, runs, n_human))
    tot_theme, themes, df, apps, fam_by_subid, runs, n_human = _SETUP_CACHE["v"]
    names = frozenset(u["topic"] for u in units)
    if _TP_CACHE.get("names") != names: _TP_CACHE.update(names=names, parents=topic_parents(names))   # slow; reused by slider frames
    parent_of = _TP_CACHE["parents"]

    snap = {"generated": now, "level": level, "themes": themes, "activities": acts, "kinds": [KINDS[k] for k in kinds],
            "jev": False, "tagging": "rules",
            "counts": {k: con.execute("SELECT COUNT(DISTINCT session) FROM turns WHERE source=? AND sub=0", (k,)).fetchone()[0] for k in ("pi", "claude", "codex")},
            "first": min((u["start"] for u in units), default=now), "ranges": {}}

    for key, (hours, nb, nmix, label) in RANGES.items():
        if only and key != only: continue
        span = hours * 3600
        if hours < 24:
            cell = SHORT_HEAT[key][1]
            t1 = (int(now) // cell + 1) * cell         # end of the current heat cell (1/5/15 min)
        else:
            t1 = now_h
        t0 = t1 - span
        p0 = t0 - span

        def in_range(u, a, b):
            return u["end"] > a and u["start"] < b

        cur = [u for u in units if in_range(u, t0, t1)]
        prev = [u for u in units if in_range(u, p0, t0)]

        def kpis(us, a, b):
            busy = sum(sum(e - s for s, e in clip_segs(u["segs"], a, b)) for u in us)
            you_iv = [(max(u["you"][0], a), min(u["you"][1], b)) for u in us if u["you"] and u["you"][1] > a and u["you"][0] < b]
            ivs = [iv for u in us for iv in clip_segs(u["segs"], a, b)]
            # per-session intervals for concurrency: overlap only between different sessions
            by_s = defaultdict(list)
            for u in us:
                by_s[u["session"]] += clip_segs(u["segs"], a, b)
            merged = []
            for s, iv in by_s.items():
                iv.sort(); m = []
                for x in iv:
                    if m and x[0] <= m[-1][1]: m[-1] = (m[-1][0], max(m[-1][1], x[1]))
                    else: m.append(x)
                merged += m
            return {"agent": busy, "you": union_len(you_iv), "prompts": sum(1 for u in us if u["human"] and a <= u["start"] < b),
                    "tokens": sum(u["tokens"] for u in us if a <= u["start"] < b), "cost": sum(u["cost"] for u in us if a <= u["start"] < b),
                    "costEst": any(u["est"] for u in us if a <= u["start"] < b),
                    "sessions": len({u["session"] for u in us}), "peak": peak_overlap(merged), "_merged": merged}

        kc, kp = kpis(cur, t0, t1), kpis(prev, p0, t0)
        merged_cur = kc.pop("_merged"); kp.pop("_merged")
        R = {"label": label, "t0": t0, "t1": t1, "kpis": kc, "prev": kp if any(u["start"] < t0 for u in units) else None}

        # ── heat map ──
        lt = lambda ts: time.localtime(ts)
        if key in SHORT_HEAT:
            ncol, cell = SHORT_HEAT[key]
            rows = themes; cols = [time.strftime("%H:%M", lt(t0 + i * cell)) for i in range(ncol)]
            ag = [[0.0] * ncol for _ in rows]; yo = [[0.0] * ncol for _ in rows]
            for u in cur:
                ri = rows.index(u["theme"]) if u["theme"] in rows else None
                if ri is None: continue
                spread(clip_segs(u["segs"], t0, t1), t0, cell, ncol, ag[ri])
                if u["you"]: spread(clip_segs([u["you"]], t0, t1), t0, cell, ncol, yo[ri])
            R["heat"] = {"mode": "theme-time", "rows": rows, "cols": cols, "agent": ag, "you": yo}
        elif key == "24h":
            rows = themes; cols = [time.strftime("%H", lt(t0 + i * 3600)) for i in range(24)]
            ag = [[0.0] * 24 for _ in rows]; yo = [[0.0] * 24 for _ in rows]
            for u in cur:
                ri = rows.index(u["theme"]) if u["theme"] in rows else None
                if ri is None: continue
                spread(clip_segs(u["segs"], t0, t1), t0, 3600, 24, ag[ri])
                if u["you"]: spread(clip_segs([u["you"]], t0, t1), t0, 3600, 24, yo[ri])
            R["heat"] = {"mode": "theme-hour", "rows": rows, "cols": cols, "agent": ag, "you": yo}
        elif key in ("7d", "1mo"):
            nd = 7 if key == "7d" else 30
            midnight = time.mktime(lt(now)[:3] + (0, 0, 0, 0, 0, -1))
            d0 = midnight - (nd - 1) * 86400
            rows = [time.strftime("%-m/%-d", lt(d0 + i * 86400 + 43200)) for i in range(nd)]
            ag = [[0.0] * 24 for _ in range(nd)]; yo = [[0.0] * 24 for _ in range(nd)]
            flat_a = [0.0] * (nd * 24); flat_y = [0.0] * (nd * 24)
            for u in cur:
                spread(clip_segs(u["segs"], d0, d0 + nd * 86400), d0, 3600, nd * 24, flat_a)
                if u["you"]: spread(clip_segs([u["you"]], d0, d0 + nd * 86400), d0, 3600, nd * 24, flat_y)
            for i in range(nd * 24):
                ag[i // 24][i % 24] = flat_a[i]; yo[i // 24][i % 24] = flat_y[i]
            R["heat"] = {"mode": "day-hour", "rows": rows, "cols": [f"{h:02d}" for h in range(24)], "agent": ag, "you": yo}
        else:
            weeks = 52; w0 = t1 - weeks * 7 * 86400
            ag = [[0.0] * weeks for _ in range(24)]; yo = [[0.0] * weeks for _ in range(24)]
            flat_a = [0.0] * (weeks * 168); flat_y = [0.0] * (weeks * 168)
            for u in cur:
                spread(clip_segs(u["segs"], w0, t1), w0, 3600, weeks * 168, flat_a)
                if u["you"]: spread(clip_segs([u["you"]], w0, t1), w0, 3600, weeks * 168, flat_y)
            for i in range(weeks * 168):
                h = lt(w0 + i * 3600 + 1800).tm_hour
                ag[h][i // 168] += flat_a[i]; yo[h][i // 168] += flat_y[i]
            R["heat"] = {"mode": "week-hour", "rows": [f"{h:02d}" for h in range(24)],
                         "cols": [time.strftime("%b", lt(w0 + i * 7 * 86400)) for i in range(weeks)], "agent": ag, "you": yo}

        # ── sankey: tokens → kind → theme → activity (tokens of turns starting in range) ──
        kt = [[0.0] * len(themes) for _ in kinds]; ta = [[0.0] * len(acts) for _ in themes]
        for u in cur:
            if not (t0 <= u["start"] < t1) or u["theme"] not in themes: continue
            ti = themes.index(u["theme"])
            kt[kinds.index(u["kind"])][ti] += u["tokens"]
            ta[ti][acts.index(u["act"])] += u["tokens"]
        # absolute scale: the most tokens any window of this length ever used (hour-aligned, over all data)
        pk = _PEAK_CACHE.get((key, len(units)))
        if pk is None:
            bin_ = 60 if hours < 24 else 3600
            nwin = int(round(hours * 3600 / bin_))
            by_h = Counter()
            for u in units:
                if u["theme"] in themes: by_h[int(u["start"]) // bin_] += u["tokens"]
            pk = (0.0, 0)
            if by_h:
                h0, h1 = min(by_h), max(by_h) + 1
                run = 0.0
                for hh in range(h0, h1 + nwin):        # window = nwin bins (hh-nwin, hh]
                    run += by_h.get(hh, 0) - by_h.get(hh - nwin, 0)
                    if run > pk[0]: pk = (run, (hh + 1) * bin_)
            _PEAK_CACHE[(key, len(units))] = pk
        R["sankey"] = {"kt": kt, "ta": ta, "peak": pk[0], "peakEnd": pk[1]}

        # ── stream: busy seconds per theme per bucket ──
        blen = span / nb
        layers = [[0.0] * nb for _ in themes]
        for u in cur:
            if u["theme"] in themes:
                spread(clip_segs(u["segs"], t0, t1), t0, blen, nb, layers[themes.index(u["theme"])])
        R["buckets"] = [t0 + i * blen for i in range(nb)]
        R["stream"] = layers

        # ── concurrency per bucket ──
        peak, avg = [], []
        for i in range(nb):
            a, b = t0 + i * blen, t0 + (i + 1) * blen
            iv = clip_segs(merged_cur, a, b)
            u_len = union_len(iv)
            peak.append(peak_overlap(iv)); avg.append(sum(e - s for s, e in iv) / u_len if u_len else 0)
        R["conc"] = {"peak": peak, "avg": avg}

        # ── activity mix ──
        mlen = span / nmix
        mix = [[0.0] * len(acts) for _ in range(nmix)]
        for u in cur:
            arr = [0.0] * nmix
            spread(clip_segs(u["segs"], t0, t1), t0, mlen, nmix, arr)
            for i, v in enumerate(arr): mix[i][acts.index(u["act"])] += v
        R["mixBuckets"] = [t0 + i * mlen for i in range(nmix)]
        R["mix"] = mix

        # ── topics: theme → project → subtopic (flat list kept for the 2-level map) ──
        tc, tpv = defaultdict(float), defaultdict(float)
        for u in cur: tc[(u["theme"], u["topic"])] += sum(e - s for s, e in clip_segs(u["segs"], t0, t1))
        for u in prev: tpv[(u["theme"], u["topic"])] += sum(e - s for s, e in clip_segs(u["segs"], p0, t0))
        groups = []
        for th in themes:
            items = sorted([(tp, v) for (t2, tp), v in tc.items() if t2 == th and v > 0], key=lambda x: -x[1])
            if not items: continue
            top, rest = items[:9], items[9:]
            topics = [{"name": tp, "v": v, "pv": tpv.get((th, tp), 0)} for tp, v in top]
            if rest: topics.append({"name": f"+{len(rest)} more", "v": sum(v for _, v in rest), "pv": sum(tpv.get((th, tp), 0) for tp, _ in rest)})
            projs = defaultdict(lambda: {"v": 0.0, "pv": 0.0, "subs": []})
            for tp, v in items:
                par = parent_of.get(tp, tp); pr = projs[par]
                pr["v"] += v; pr["pv"] += tpv.get((th, tp), 0)
                pr["subs"].append({"name": leaf_name(tp, par), "full": tp, "v": v, "pv": tpv.get((th, tp), 0)})
            plist = sorted(projs.items(), key=lambda kv: -kv[1]["v"])
            projects = []
            for par, pr in plist[:7]:
                subs = sorted(pr["subs"], key=lambda x: -x["v"])
                keep, more = subs[:6], subs[6:]
                if more: keep.append({"name": f"+{len(more)}", "full": "", "v": sum(x["v"] for x in more), "pv": sum(x["pv"] for x in more), "more": True})
                projects.append({"name": par, "v": pr["v"], "pv": pr["pv"], "subs": keep})
            if len(plist) > 7:
                projects.append({"name": f"+{len(plist) - 7} more", "v": sum(p["v"] for _, p in plist[7:]), "pv": sum(p["pv"] for _, p in plist[7:]), "subs": [], "more": True})
            groups.append({"theme": th, "topics": topics, "projects": projects})
        R["topics"] = groups

        # ── terms: rising / fading (document counts of human prompts) ──
        cc, pc = Counter(), Counter()
        for u in cur:
            if t0 <= u["start"] < t1:
                for w in u["terms"]: cc[w] += 1
        for u in prev:
            if p0 <= u["start"] < t0:
                for w in u["terms"]: pc[w] += 1
        mn = 2 if hours <= 24 else 3
        cand = [w for w in set(cc) | set(pc) if df[w] >= 3 and max(cc[w], pc[w]) >= mn and w not in TERM_STOP]
        idf = lambda w: math.log(n_human / (1 + df[w]))
        score = lambda w: (cc[w] - pc[w]) / math.sqrt(cc[w] + pc[w] + 1) * idf(w)
        rising = sorted([w for w in cand if cc[w] > pc[w]], key=score, reverse=True)[:5]
        fading = sorted([w for w in cand if pc[w] > cc[w]], key=score)[:3]
        R["terms"] = {"rising": [{"term": w, "cur": cc[w], "prv": pc[w]} for w in rising],
                      "fading": [{"term": w, "cur": cc[w], "prv": pc[w]} for w in fading]}

        # ── tools × activity ──
        tools = {t: [0] * len(acts) for t in TOOLS}
        for u in cur:
            if t0 <= u["start"] < t1:
                for name, n in u["tools"].items():
                    tools.setdefault(name, [0] * len(acts))[acts.index(u["act"])] += n
        R["tools"] = [{"tool": k, "parts": v} for k, v in sorted(tools.items(), key=lambda x: -sum(x[1])) if sum(v) > 0]

        # ── app time ──
        at, term = Counter(), Counter()
        for ts, dur, app, title, kind in apps:
            if t0 <= ts < t1:
                if kind: term[kind] += dur
                else: at[app] += dur
        R["apps"] = {"terminal": sum(term.values()), "split": [{"name": k, "s": term[k]} for k in ("Pi", "Claude Code", "Codex", "shell") if term[k]],
                     "apps": [{"name": a, "s": s} for a, s in at.most_common(6)], "other": sum(s for a, s in at.most_common()[6:])}
        # ── subagents: per type (runs + outcomes from run records, busy/tokens/cost from child sessions) ──
        st = {}
        def T(name):
            return st.setdefault(name, {"type": name, "runs": 0, "busy": 0.0, "tokens": 0, "cost": 0.0, "dur": [], "models": {},
                                        "status": {"completed": 0, "steered": 0, "error": 0, "aborted": 0, "other": 0}})
        rr = []
        for r in runs:
            if not (t0 <= r["start"] < t1):
                continue
            x = T(r["type"]); x["runs"] += 1
            sname = r["status"] if r["status"] in ("completed", "steered", "error", "aborted") else ("error" if r["status"] in ("failed",) else "other")
            x["status"][sname] += 1
            d = (r["end"] - r["start"]) if r["end"] else None
            if d is not None and d >= 0: x["dur"].append(d)
            rr.append({"t": r["start"], "d": d, "type": r["type"], "status": sname, "desc": r["description"], "m": r["fam"]})
        sub_busy = sub_tok = 0
        models = {}
        model_names = defaultdict(Counter)
        for u in cur:
            if not u["sub"]: continue
            b = sum(e - s for s, e in clip_segs(u["segs"], t0, t1))
            x = T(u["subtype"] or "session"); x["busy"] += b
            sub_busy += b
            fm = x["models"].setdefault(u["model"], {"busy": 0.0, "tokens": 0, "cost": 0.0})
            fm["busy"] += b
            mt = models.setdefault(u["model"], {"model": u["model"], "family": u["fam"], "busy": 0.0, "tokens": 0, "cost": 0.0, "sessions": set(), "names": Counter()})
            mt["busy"] += b; mt["sessions"].add(u["session"])
            if t0 <= u["start"] < t1:
                x["tokens"] += u["tokens"]; x["cost"] += u["cost"]; sub_tok += u["tokens"]
                fm["tokens"] += u["tokens"]; fm["cost"] += u["cost"]
                mt["tokens"] += u["tokens"]; mt["cost"] += u["cost"]
        types = sorted(st.values(), key=lambda x: -(x["runs"] * 1e6 + x["busy"]))
        for x in types:
            ds = sorted(x.pop("dur")); x["median"] = ds[len(ds) // 2] if ds else None
        mods = []
        for f, m in sorted(models.items(), key=lambda kv: -kv[1]["tokens"]):
            mods.append({"model": f, "family": m["family"], "busy": m["busy"], "tokens": m["tokens"], "cost": m["cost"], "sessions": len(m["sessions"])})
        R["subs"] = {"types": types, "models": mods, "modelFamily": {r["m"]: family(r["m"]) for r in rr} | {m["model"]: m["family"] for m in mods}, "runs": rr[-600:], "busy": sub_busy, "tokens": sub_tok,
                     "allBusy": kc["agent"], "allTokens": kc["tokens"]}
        snap["ranges"][key] = R

    return snap

# ─────────────────────────── CLI ───────────────────────────

def check(con, cfg):
    con.row_factory = sqlite3.Row
    print("sessions / turns by source:")
    for r in con.execute("SELECT source, COUNT(DISTINCT session) s, COUNT(*) n, SUM(busy)/3600.0 h, SUM(human) p, SUM(tokens_in+tokens_out+tokens_cache)/1e6 tok, SUM(cost) c FROM turns GROUP BY source"):
        print(f"  {r['source']:6} {r['s']:4} sessions {r['n']:5} turns  busy {r['h']:7.1f}h  prompts {r['p']:5}  tokens {r['tok']:8.1f}M  cost ${r['c']:.2f}")
    r = con.execute("SELECT MIN(start), MAX(end) FROM turns").fetchone()
    if r[0]: print(f"  span: {datetime.fromtimestamp(r[0])} → {datetime.fromtimestamp(r[1])}")
    for lv in ("turn", "session"):
        for m in ("rules", "jev"):
            n = con.execute("SELECT COUNT(*) FROM tags WHERE method=? AND level=?", (m, lv)).fetchone()[0]
            if n:
                print(f"tags {m}/{lv}: {n}")
                for row in con.execute("SELECT theme, COUNT(*) n FROM tags WHERE method=? AND level=? GROUP BY theme ORDER BY n DESC", (m, lv)):
                    print(f"    {row[0]:22} {row[1]}")
    print("app samples: %s, %.1fh" % tuple(con.execute('SELECT COUNT(*), COALESCE(SUM(dur),0)/3600.0 FROM app').fetchone()))
    print(f"jev key: {'yes' if jev_key() else 'no'}")

def main():
    ap = argparse.ArgumentParser(prog="odv")
    ap.add_argument("cmd", choices=["ingest", "tag", "build", "run", "frames", "check", "reset", "pending", "serve"])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--jev", action="store_true", help="also tag with Jev (only when asked; `odv run` never does)")
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--level", choices=["turn", "session"])
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if a.level and a.cmd == "build":
        cfg["tagLevel"] = a.level
    con = db()
    use_jev = bool(a.jev) and not a.no_jev
    t = time.time()
    if a.cmd == "reset":
        os.remove(DB_PATH); print("odv: database removed"); return
    if a.cmd in ("ingest", "run"):
        n = ingest(con, cfg, a.verbose); print(f"odv: ingested {n} changed files ({time.time() - t:.1f}s)")
    if a.cmd in ("tag", "run"):
        n = tag(con, cfg, use_jev, a.limit, a.level if a.cmd == "tag" else None, a.verbose)
        print(f"odv: jev-tagged {n} units" if use_jev else "odv: rule tags updated (no Jev)")
    if a.cmd in ("build", "run"):
        build(con, cfg); print(f"odv: wrote {SNAPSHOT}")
    if a.cmd == "frames":   # the panel runs this in the background after a refresh (~10 s)
        t = time.time(); build_frames(con, cfg); print(f"odv: wrote slider frames ({time.time() - t:.1f}s)")
    if a.cmd == "check":
        check(con, cfg)
    if a.cmd == "pending":
        print(json.dumps({"model": jev_model(cfg), "requests": jev_pending(con, cfg)}))
    if a.cmd == "serve":
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "observatory"))
        try:
            import server
        except ImportError:
            sys.exit("odv serve: the Jev Observatory app (observatory/) is not installed")
        server.serve(a.port)

if __name__ == "__main__":
    main()
