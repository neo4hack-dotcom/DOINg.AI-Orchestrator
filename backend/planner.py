"""Plan decomposition: from the orchestrator's raw JSON to a schedulable task graph.

The orchestrator proposes modules (homogeneous sub-parts with contracts) and tasks
with a size (S/M/L), a complexity (1-5), the files they own and their dependencies.
This module makes that plan safe and efficient to run in parallel:

- modules are ordered by their dependencies; a module's entry tasks wait for the
  exit tasks of the modules it depends on;
- dependencies may only point backwards: cycles are impossible by construction;
- tiers are checked against complexity (a complexity-5 task never goes to a light worker);
- chains of tiny tasks are merged (fewer worker start-ups = fewer tokens);
- tasks that would touch the same files and are not ordered get an edge, so two
  workers never edit the same file at the same time;
- each task gets a wave (depth in the DAG) and a priority (critical-path weight):
  the scheduler starts the longest chains first.
"""
from __future__ import annotations

import fnmatch
from typing import Optional

TIERS = ["light", "medium", "careful", "heavy", "fable"]
SIZE_WEIGHT = {"S": 1, "M": 2, "L": 4}
COMPLEXITY_TIER = {1: "light", 2: "medium", 3: "medium", 4: "careful", 5: "heavy"}
MAX_TASKS = 120


def norm_tier(t: str, default: str = "medium") -> str:
    t = (t or "").lower().replace("savvy-", "").strip()
    return t if t in TIERS else default


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def clean_task(raw, i: int) -> Optional[dict]:
    if isinstance(raw, str):
        raw = {"title": raw, "brief": raw}
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or f"Task {i + 1}").strip()[:80]
    size = str(raw.get("size") or "M").upper()[:1]
    size = size if size in SIZE_WEIGHT else "M"
    complexity = _int(raw.get("complexity"), 2 if size == "S" else 3, 1, 5)
    files = raw.get("files") or []
    if isinstance(files, str):
        files = [files]
    after = raw.get("after") or raw.get("dependsOn") or []
    if not isinstance(after, list):
        after = [after]
    floor = COMPLEXITY_TIER[complexity]
    tier = norm_tier(str(raw.get("tier", "")), floor)
    # The LLM may under-tier: never more than one level below the complexity floor.
    if TIERS.index(tier) < TIERS.index(floor) - 1:
        tier = TIERS[TIERS.index(floor) - 1]
    return {
        "title": title,
        "tier": tier,
        "size": size,
        "complexity": complexity,
        "files": [str(f).strip().lstrip("./") for f in files if str(f).strip()][:20],
        "after": after,
        "brief": str(raw.get("brief") or raw.get("description") or title).strip(),
        "verify": str(raw.get("verify") or "").strip(),
        "verifyCommand": str(raw.get("verifyCommand") or "").strip(),
        "module": str(raw.get("module") or "").strip(),
    }


def needs_split(t: dict) -> bool:
    return (t["size"] == "L" and t["complexity"] >= 4) or len(t["brief"]) > 3000 or len(t["files"]) > 10


def _module_order(modules: list[dict]) -> list[dict]:
    ids = {m["id"] for m in modules}
    deps = {m["id"]: [d for d in m.get("dependsOn", []) if d in ids and d != m["id"]] for m in modules}
    ordered, seen, visiting = [], set(), set()

    def visit(mid: str) -> None:
        if mid in seen or mid in visiting:
            return  # a cycle: keep the given order
        visiting.add(mid)
        for d in deps[mid]:
            visit(d)
        visiting.discard(mid)
        seen.add(mid)
        ordered.append(next(m for m in modules if m["id"] == mid))

    for m in modules:
        visit(m["id"])
    return ordered


def clean_modules(raw: list) -> list[dict]:
    out, seen = [], set()
    for i, m in enumerate(raw or []):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or m.get("name") or f"M{i + 1}").strip()[:40] or f"M{i + 1}"
        if mid in seen:
            mid = f"{mid}-{i + 1}"
        seen.add(mid)
        deps = m.get("dependsOn") or m.get("after") or []
        out.append({
            "id": mid,
            "name": str(m.get("name") or mid).strip()[:80],
            "goal": str(m.get("goal") or m.get("description") or "").strip(),
            "files": [str(f).strip() for f in (m.get("files") or []) if str(f).strip()][:20],
            "contracts": str(m.get("contracts") or m.get("interfaces") or "").strip(),
            "dependsOn": [str(d).strip() for d in (deps if isinstance(deps, list) else [deps])],
            "verifyCommand": str(m.get("verifyCommand") or "").strip(),
            "size": str(m.get("size") or "M").upper()[:1],
        })
    return _module_order(out)


def _resolve_local(after: list, local_titles: dict[str, int], local_n: int) -> list[int]:
    """`after` entries are 1-based indexes within the module, or task titles."""
    out = []
    for a in after:
        idx = None
        if isinstance(a, (int, float)) or (isinstance(a, str) and a.strip().isdigit()):
            idx = int(a)
        elif isinstance(a, str):
            idx = local_titles.get(a.strip().lower())
        if idx is not None and 0 < idx < local_n and idx not in out:
            out.append(idx)
    return out


def assemble(module_tasks: list[tuple[Optional[dict], list[dict]]]) -> list[dict]:
    """Number tasks globally. ``module_tasks`` is [(module or None, cleaned tasks)] in module order."""
    tasks: list[dict] = []
    module_sinks: dict[str, list[int]] = {}
    for module, items in module_tasks:
        base = len(tasks)
        titles = {t["title"].lower(): i + 1 for i, t in enumerate(items)}
        local_ns = []
        for i, t in enumerate(items):
            n = base + i + 1
            local = _resolve_local(t["after"], titles, i + 1)
            after = [base + a for a in local]
            if module and not local:
                for dep in module.get("dependsOn", []):
                    after += [s for s in module_sinks.get(dep, []) if s not in after]
            tasks.append({**t, "n": n, "after": sorted(set(after)), "module": module["id"] if module else t.get("module", "")})
            local_ns.append(n)
        if module:
            dependents = {a for t in tasks[base:] for a in t["after"] if a > base}
            module_sinks[module["id"]] = [n for n in local_ns if n not in dependents] or local_ns[-1:]
        if len(tasks) >= MAX_TASKS:
            break
    return tasks[:MAX_TASKS]


def _dependents(tasks: list[dict]) -> dict[int, set[int]]:
    deps: dict[int, set[int]] = {t["n"]: set() for t in tasks}
    for t in tasks:
        for a in t["after"]:
            if a in deps:
                deps[a].add(t["n"])
    return deps


def renumber(tasks: list[dict]) -> list[dict]:
    mapping = {t["n"]: i + 1 for i, t in enumerate(tasks)}
    out = []
    for t in tasks:
        out.append({**t, "n": mapping[t["n"]], "after": sorted({mapping[a] for a in t["after"] if a in mapping})})
    return out


def merge_small(tasks: list[dict], max_brief: int = 1800) -> list[dict]:
    """Merge B into A when B only follows A, nothing else follows A, and both are tiny."""
    tasks = [dict(t) for t in tasks]
    changed = True
    while changed:
        changed = False
        by_n = {t["n"]: t for t in tasks}
        dependents = _dependents(tasks)
        for b in tasks:
            if len(b["after"]) != 1:
                continue
            a = by_n.get(b["after"][0])
            if not a or dependents[a["n"]] != {b["n"]} or a.get("module") != b.get("module"):
                continue
            small = lambda t: t["size"] == "S" and t["complexity"] <= 2 and t["tier"] in ("light", "medium")  # noqa: E731
            if not (small(a) and small(b)) or len(a["brief"]) + len(b["brief"]) > max_brief:
                continue
            a["title"] = f"{a['title']} + {b['title']}"[:80]
            a["brief"] = f"Part 1 - {a['brief']}\n\nPart 2 - {b['brief']}"
            a["verify"] = "; ".join(v for v in (a["verify"], b["verify"]) if v)
            a["verifyCommand"] = " && ".join(v for v in (a["verifyCommand"], b["verifyCommand"]) if v)
            a["files"] = list(dict.fromkeys(a["files"] + b["files"]))
            a["tier"] = TIERS[max(TIERS.index(a["tier"]), TIERS.index(b["tier"]))]
            a["size"] = "M"
            a["complexity"] = max(a["complexity"], b["complexity"])
            a["merged"] = a.get("merged", 1) + b.get("merged", 1)
            for t in tasks:
                if b["n"] in t["after"]:
                    t["after"] = sorted({a["n"] if x == b["n"] else x for x in t["after"]} - {t["n"]})
            tasks = [t for t in tasks if t["n"] != b["n"]]
            changed = True
            break
    return renumber(tasks)


def files_overlap(a: list[str], b: list[str]) -> bool:
    for x in a:
        for y in b:
            xs, ys = x.rstrip("/"), y.rstrip("/")
            if xs == ys or ys.startswith(xs + "/") or xs.startswith(ys + "/"):
                return True
            if fnmatch.fnmatch(xs, ys) or fnmatch.fnmatch(ys, xs):
                return True
    return False


def serialize_conflicts(tasks: list[dict]) -> int:
    """Order unordered tasks that share files. Returns the number of edges added."""
    anc: dict[int, set[int]] = {}
    added = 0
    by_n = {t["n"]: t for t in tasks}
    for t in sorted(tasks, key=lambda x: x["n"]):
        a: set[int] = set()
        for d in t["after"]:
            a |= anc.get(d, set()) | {d}
        if t["files"]:
            for other in sorted(tasks, key=lambda x: x["n"]):
                if other["n"] >= t["n"]:
                    break
                if other["n"] in a or not other["files"]:
                    continue
                if files_overlap(t["files"], other["files"]):
                    t["after"] = sorted(set(t["after"]) | {other["n"]})
                    a |= anc.get(other["n"], set()) | {other["n"]}
                    added += 1
        anc[t["n"]] = a
    del by_n
    return added


def schedule(tasks: list[dict]) -> dict:
    """Set wave and priority on each task. Returns stats."""
    by_n = {t["n"]: t for t in tasks}
    for t in sorted(tasks, key=lambda x: x["n"]):
        t["wave"] = 1 + max((by_n[a]["wave"] for a in t["after"] if a in by_n), default=0)
    dependents = _dependents(tasks)
    for t in sorted(tasks, key=lambda x: -x["n"]):
        own = SIZE_WEIGHT.get(t["size"], 2) + t["complexity"] / 2
        t["priority"] = round(own + max((by_n[d]["priority"] for d in dependents[t["n"]]), default=0), 2)
    waves: dict[int, int] = {}
    for t in tasks:
        waves[t["wave"]] = waves.get(t["wave"], 0) + 1
    critical = max((t["priority"] for t in tasks), default=0)
    return {"waves": len(waves), "maxWidth": max(waves.values(), default=0), "criticalPath": critical,
            "parallelism": round(sum(SIZE_WEIGHT.get(t["size"], 2) + t["complexity"] / 2 for t in tasks) / critical, 2) if critical else 1}


def finalize(tasks: list[dict], *, merge: bool = True) -> tuple[list[dict], dict]:
    if merge:
        tasks = merge_small(tasks)
    added = serialize_conflicts(tasks)
    stats = schedule(tasks)
    stats["conflictEdges"] = added
    stats["merged"] = sum(t.get("merged", 1) - 1 for t in tasks)
    for t in tasks:
        t.update(status="planned", round=0, runIds=[], defects=[], note="")
        t.setdefault("merged", 1)
    return tasks, stats
