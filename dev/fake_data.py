#!/usr/bin/env python3
"""Generate a FAKE data set for screenshots and demos (no real sessions involved).

Two made-up months of agents working on generic scientific foundation-model projects, with Claude and GPT models
and a few million tokens in total. It fills a throwaway SQLite database with synthetic turns, tags and subagent
runs, then runs the real collector's build steps on it, so the snapshot has exactly the widget's format.

    python3 dev/fake_data.py [OUT_DIR]      # default: dev/fake-data/ (git-ignored)

Writes OUT_DIR/snapshot.json and OUT_DIR/frames-<range>.json (time-slider windows).
"""
import hashlib, json, os, random, sqlite3, sys, time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import odv  # noqa: E402

SEED = 7
DAYS = 60
HOME_FAKE = "/home/user"

THEMES = {
    "Foundation models": "Pretraining, architecture and fine-tuning of scientific foundation models",
    "Data & pipelines": "Datasets, loaders, sharding, quality control and preprocessing",
    "Evaluation": "Benchmarks, probing, ablations and zero-shot tests",
    "Visualization": "Embedding maps, attention plots, training dashboards",
    "Writing": "Papers, figures, proposals and notes",
    "Tooling": "Dev environment, cluster jobs, shell and editor setup",
}
# theme -> project -> subtopics, with relative weight and a vocabulary for prompts
PROJECTS = {
    "Foundation models": {
        "cellmodel": (["pretrain", "tokenizer", "masking"], 3.0, ["gene expression", "masked pretraining", "tokenizer", "cell embeddings", "learning rate warmup"]),
        "protmodel": (["finetune", "embeddings"], 2.0, ["protein sequences", "fine-tuning", "contact maps", "LoRA adapters", "embedding pooling"]),
        "seqmodel": (["attention", "scaling"], 1.6, ["long-context attention", "scaling curves", "rotary embeddings", "checkpoint averaging"]),
    },
    "Data & pipelines": {
        "atlas": (["loader", "qc"], 1.6, ["cell atlas", "batch effects", "quality control", "sparse matrices", "normalisation"]),
        "shards": (["dedup", "streaming"], 1.0, ["dataset shards", "deduplication", "streaming loader", "parquet"]),
    },
    "Evaluation": {
        "bench": (["perturbation", "transfer", "probing"], 1.8, ["perturbation prediction", "cross-species transfer", "linear probing", "ablation", "held-out tissues"]),
    },
    "Visualization": {
        "embedviz": (["umap", "clusters"], 1.1, ["UMAP", "cluster labels", "embedding explorer", "colour maps"]),
        "trainviz": (["loss", "attention-maps"], 0.8, ["loss curves", "attention heads", "gradient norms", "dashboard"]),
    },
    "Writing": {
        "paper": (["methods", "figures"], 1.0, ["methods section", "figure captions", "related work", "reviewer response"]),
        "notes": (["reading", "ideas"], 0.5, ["reading notes", "research ideas", "summaries"]),
    },
    "Tooling": {
        "cluster": (["jobs", "env"], 0.9, ["job scripts", "python environment", "dependency pins", "scheduler queue"]),
        "dotfiles": (["shell"], 0.4, ["shell aliases", "editor config", "keybindings"]),
    },
}
ACTIVITIES = list(odv.DEFAULT_CONFIG["activities"].keys())
VERBS = {
    "build": ["Add", "Implement", "Write", "Create", "Extend"],
    "debug": ["Fix", "Debug", "Why does", "Track down", "Investigate"],
    "research": ["Compare", "Look up", "Explain", "Find papers on", "Summarise options for"],
    "config": ["Set up", "Configure", "Pin versions for", "Tidy"],
    "write": ["Draft", "Rewrite", "Tighten", "Outline"],
    "chat": ["Quick question about", "Thoughts on", "Sanity check"],
}
# source -> models (weights); Claude and GPT only
MODELS = {
    "pi": [("claude-sonnet-4-5", 5), ("claude-opus-4-5", 2), ("gpt-5", 2)],
    "claude": [("claude-sonnet-4-5", 4), ("claude-opus-4-5", 3), ("claude-haiku-4-5", 1)],
    "codex": [("gpt-5-codex", 4), ("gpt-5", 2), ("gpt-5-mini", 1)],
}
SUB_MODELS = [("claude-haiku-4-5", 4), ("claude-sonnet-4-5", 3), ("gpt-5-mini", 2)]
SUB_TYPES = [("Explore", 5), ("general-purpose", 3), ("Plan", 1)]
TOOLS_BY_ACT = {   # typical tool mix per activity
    "build": {"edit": (2, 9), "write": (0, 3), "bash": (2, 8), "read": (1, 5)},
    "debug": {"bash": (4, 14), "read": (2, 7), "edit": (1, 4)},
    "research": {"web": (2, 7), "read": (1, 5), "bash": (0, 2)},
    "config": {"bash": (3, 9), "edit": (1, 3), "read": (1, 3)},
    "write": {"write": (1, 3), "edit": (1, 4), "read": (1, 3)},
    "chat": {"read": (0, 1)},
}


def wpick(rng, items):
    tot = sum(w for _, w in items); x = rng.random() * tot
    for v, w in items:
        x -= w
        if x <= 0: return v
    return items[-1][0]


def make_db(path, now):
    rng = random.Random(SEED)
    if os.path.exists(path): os.remove(path)
    con = sqlite3.connect(path)
    con.executescript(odv.SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(turns)")}
    for c, d in (("final", "TEXT DEFAULT ''"), ("calls", "TEXT DEFAULT '[]'")):
        if c not in cols: con.execute(f"ALTER TABLE turns ADD COLUMN {c} {d}")
    cfg = json.loads(json.dumps(odv.DEFAULT_CONFIG)); cfg["themes"] = THEMES
    topics = [(th, pj, sub, w / len(subs), vocab) for th, ps in PROJECTS.items() for pj, (subs, w, vocab) in ps.items() for sub in subs]
    t_start = now - DAYS * 86400
    turns, tags, runs = [], [], []
    day = time.mktime(time.localtime(t_start)[:3] + (12, 0, 0, 0, 0, -1))   # noon of each calendar day
    today = time.localtime(now)[:3]
    while time.localtime(day)[:3] <= today:
        lt = time.localtime(day)
        weekend = lt.tm_wday >= 5
        # project focus drifts over the month so the stream graph rises and fades
        phase = (day - t_start) / (DAYS * 86400)
        last_day = time.localtime(day)[:3] == today
        n_sessions = rng.randint(6, 9) if last_day else (rng.randint(1, 3) if weekend else rng.randint(3, 7))
        for _ in range(n_sessions):
            def weight(t):
                th, pj, sub, w, _ = t
                if pj == "cellmodel": w *= 1.6 - phase
                if pj == "bench": w *= 0.4 + 1.4 * phase
                if pj == "paper": w *= 0.2 + 1.8 * phase ** 2
                if pj == "atlas": w *= 1.4 - phase
                return w
            th, pj, sub, _, vocab = wpick(rng, [(t, weight(t)) for t in topics])
            source = wpick(rng, [("pi", 5), ("claude", 3), ("codex", 2)])
            model = wpick(rng, MODELS[source])
            hour = rng.choice([9, 10, 10, 11, 13, 14, 14, 15, 16, 17, 20, 21, 22])
            t = time.mktime(time.localtime(day)[:3] + (hour, rng.randint(0, 59), 0, 0, 0, -1))
            if last_day: t = now - rng.choice([0.2, 0.4, 0.6, 1, 1.5, 2.5, 4, 7, 11]) * 3600 - rng.uniform(0, 900)
            if t > now - 300: continue
            sid = hashlib.md5(f"{t}{rng.random()}".encode()).hexdigest()
            cwd = f"{HOME_FAKE}/projects/{pj}"
            prev_end = None
            for k in range(rng.randint(2, 9)):
                act = wpick(rng, [("build", 4), ("debug", 3), ("research", 2), ("config", 1), ("write", 2 if th == "Writing" else 0.4), ("chat", 1)])
                busy = rng.uniform(40, 900) if act != "chat" else rng.uniform(10, 60)
                start = t if prev_end is None else prev_end + rng.uniform(20, 240)
                if start + busy > now: break
                end = start + busy
                tools = {tn: rng.randint(*r) for tn, r in TOOLS_BY_ACT[act].items()}
                tools = {tn: n for tn, n in tools.items() if n > 0}
                spawn = source == "pi" and act in ("research", "debug", "build") and rng.random() < 0.18
                if spawn: tools["subagent"] = rng.randint(1, 2)
                size = sum(tools.values()) + 1
                tin = int(rng.uniform(50, 250) * size); tout = int(rng.uniform(15, 70) * size); tc = int(rng.uniform(100, 420) * size)
                p = odv.price(model, cfg)
                cost = (tin * p[0] + tout * p[1] + tc * p[2]) / 1e6
                phrase = rng.choice(vocab)
                prompt = f"{rng.choice(VERBS[act])} the {phrase} in {pj} {sub.replace('-', ' ')}" + rng.choice(["", " and rerun the tests", " — keep it small", ", then summarise"])
                files = [f"{cwd}/{sub.replace('-', '_')}/{rng.choice(['model', 'train', 'data', 'eval', 'plot', 'utils'])}.py" for _ in range(rng.randint(1, 3))]
                calls = [[tn, files[0] if tn in ("edit", "write", "read") else "", "python -m pytest -q" if tn == "bash" else ""] for tn in tools]
                tid = hashlib.md5(f"{sid}{k}".encode()).hexdigest()
                you = start - min(odv.YOU_CAP, max(8, (start - prev_end) if prev_end else 20 + len(prompt) / 4))
                turns.append(dict(id=tid, path=f"fake/{sid}.jsonl", source=source, session=sid, cwd=cwd, model=model, start=start, end=end,
                                  busy=busy, segments=json.dumps([[start, end]]), human=1, prompt=prompt, prompt_chars=len(prompt), reply="Done.",
                                  final="Done.", you_start=you, tokens_in=tin, tokens_out=tout, tokens_cache=tc, cost=cost,
                                  cost_est=0 if source == "pi" else 1, tools=json.dumps(tools), files=json.dumps(files), calls=json.dumps(calls),
                                  sub=0, subtype="", subid=""))
                tags.append((tid, th, f"{pj}-{sub}", act))
                if spawn:
                    for _ in range(tools["subagent"]):
                        stype = wpick(rng, SUB_TYPES); smodel = wpick(rng, SUB_MODELS)
                        s0 = start + rng.uniform(5, busy * 0.6); dur = rng.uniform(30, 420)
                        subid = hashlib.md5(f"{tid}{s0}".encode()).hexdigest()
                        status = wpick(rng, [("completed", 12), ("steered", 2), ("error", 1)])
                        runs.append((f"pi:{subid}", f"fake/{sid}.jsonl", "pi", "agent", stype, f"{stype} {phrase}", status, s0, s0 + dur, sid))
                        stools = {"read": rng.randint(3, 12), "bash": rng.randint(1, 6)}
                        if stype == "general-purpose": stools["edit"] = rng.randint(1, 4)
                        sin = int(rng.uniform(700, 2000)); sout = int(rng.uniform(90, 400)); scc = int(rng.uniform(800, 3300))
                        sp = odv.price(smodel, cfg)
                        stid = hashlib.md5(f"{subid}turn".encode()).hexdigest()
                        turns.append(dict(id=stid, path=f"fake/{subid}.jsonl", source="pi", session=subid, cwd=cwd, model=smodel, start=s0, end=s0 + dur,
                                          busy=dur, segments=json.dumps([[s0, s0 + dur]]), human=0, prompt=f"{stype}: {phrase}", prompt_chars=20,
                                          reply="", final="", you_start=None, tokens_in=sin, tokens_out=sout, tokens_cache=scc,
                                          cost=(sin * sp[0] + sout * sp[1] + scc * sp[2]) / 1e6, cost_est=0, tools=json.dumps(stools),
                                          files=json.dumps(files[:1]), calls="[]", sub=1, subtype=stype, subid=subid[:8]))
                        tags.append((stid, th, f"{pj}-{sub}", "research"))
                prev_end = end
        day = time.mktime(time.localtime(day + 86400)[:3] + (12, 0, 0, 0, 0, -1))
    keys = list(turns[0].keys())
    con.executemany(f"INSERT INTO turns({','.join(keys)}) VALUES({','.join('?' * len(keys))})", [tuple(t[k] for k in keys) for t in turns])
    con.executemany("INSERT INTO tags(turn, method, level, theme, topic, activity, conf, ts) VALUES(?, 'rules', 'turn', ?, ?, ?, 1, 0)",
                    [(a, b, c, d) for a, b, c, d in tags])
    con.executemany("INSERT INTO subruns VALUES(?,?,?,?,?,?,?,?,?,?)", runs)
    con.commit()
    return con, cfg, turns


def main():
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fake-data"))
    os.makedirs(out, exist_ok=True)
    odv.DATA = out; odv.CONFIG = os.path.join(out, "config.json"); odv.SNAPSHOT = os.path.join(out, "snapshot.json")
    now = time.time()
    con, cfg, turns = make_db(os.path.join(out, "fake.db"), now)
    odv.build(con, cfg, now, out=odv.SNAPSHOT)
    odv.build_frames(con, cfg, now)
    tok = sum(t["tokens_in"] + t["tokens_out"] + t["tokens_cache"] for t in turns)
    print(f"fake data: {len(turns)} turns, {tok / 1e6:.1f}M tokens -> {out}")


if __name__ == "__main__":
    main()
