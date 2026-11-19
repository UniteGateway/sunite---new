"""AI engineering assistant (Stage 19).

* The model can only see data returned by tools; tools run the deterministic engine on the caller's
  tenant-scoped project (projects._access enforces isolation).
* Tool arguments are validated against each tool's JSON schema before execution.
* Grounding check: every number in the final answer must appear in some tool output (tolerance for
  rounding); otherwise the answer is flagged 'ungrounded' and the numbers are listed.
* Design changes are never applied by the AI: propose_change stores a pending proposal with before/after
  KPIs; a human with project.update approves it, which creates a normal project revision."""
from __future__ import annotations
import copy, json, os, re
from backend.db.database import transaction, utcnow
from backend.services import projects as P, equipment as EQ
from backend.services.context import Principal
from backend.services.errors import ValidationFailed, Conflict, NotFound
from backend.services._common import new_id, get_scoped, j, unj
from backend.services import audit
from backend.engine.pipeline import run_design, legacy_to_request

SYSTEM = ("You are the SUNITE solar engineering assistant for Unite Solar. Answer ONLY from tool results. "
          "Never invent equipment specifications, irradiance, shading or prices. If data is missing, say what is missing. "
          "To change a design, call propose_change; you cannot apply changes. Keep answers concise and cite the figures you used.")
MAX_STEPS = 6
CHANGEABLE = {"tilt_deg": (0, 40), "perimeter_m": (0, 5), "walkway_m": (0, 5), "row_gap_m": (0, 5), "roof_rotation_deg": (-180, 180),
              "module_watts": (100, 1000)}

TOOLS = [
    {"name": "get_design_summary", "description": "Run the engine on a project and return key results.",
     "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]}},
    {"name": "explain_shading", "description": "Most shaded modules and monthly beam-weighted shading for a project.",
     "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}, "top_n": {"type": "integer"}}, "required": ["project_id"]}},
    {"name": "missing_information", "description": "List survey/design inputs that are missing or unverified for a project.",
     "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]}},
    {"name": "compare_equipment", "description": "Compare catalogue equipment records by id (specs and verification status).",
     "parameters": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ids"]}},
    {"name": "propose_change", "description": "Evaluate a design change and store it as a proposal for human approval. "
                                              f"Allowed fields: {sorted(CHANGEABLE)}.",
     "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}, "changes": {"type": "object"},
                                                     "rationale": {"type": "string"}}, "required": ["project_id", "changes"]}},
]
_SCHEMA = {t["name"]: t["parameters"] for t in TOOLS}


def _validate(name, args):
    sch = _SCHEMA.get(name)
    if not sch:
        raise ValidationFailed(f"Unknown tool {name}")
    if not isinstance(args, dict):
        raise ValidationFailed("Tool arguments must be an object")
    for r in sch.get("required", []):
        if r not in args:
            raise ValidationFailed(f"{name}: missing '{r}'")
    tmap = {"string": str, "integer": int, "array": list, "object": dict}
    for k, v in args.items():
        spec = sch["properties"].get(k)
        if not spec:
            raise ValidationFailed(f"{name}: unexpected argument '{k}'")
        if not isinstance(v, tmap[spec["type"]]) or (spec["type"] == "integer" and isinstance(v, bool)):
            raise ValidationFailed(f"{name}: '{k}' must be {spec['type']}")


def _payload_to_req(payload):
    return payload if "site" in payload else legacy_to_request({**payload, "obstacles": payload.get("obstacles", [])})


def _kpis(r):
    e = r.get("electrical") or {}
    return {"panel_count": r["panel_count"], "dc_kwp": r["dc_kwp"],
            "beam_weighted_shading_pct": r["shading"].get("beam_weighted_shading_pct"),
            "row_pitch_m": r["layout"]["row_pitch_m"], "string_design_status": e.get("status", "no inverter"),
            "annual_ac_kwh": (r.get("energy") or {}).get("annual_ac_kwh")}


def _design(con, p, project_id):
    prj = P.get(con, p, project_id)                       # tenant-scoped; NotFound for others
    try:
        return prj, run_design(_payload_to_req(prj["payload"]))
    except (KeyError, ValueError) as e:
        raise ValidationFailed(f"Project cannot be designed: {e}")


def run_tool(con, p: Principal, name, args):
    _validate(name, args)
    if name == "get_design_summary":
        prj, r = _design(con, p, args["project_id"])
        return {"project": prj["name"], "revision": prj["revision"], "status": prj["status"], **_kpis(r),
                "boq_unpriced_items": r["boq_totals"]["unpriced_items"], "warnings": r["warnings"][:6]}
    if name == "explain_shading":
        prj, r = _design(con, p, args["project_id"])
        top = sorted(r["panels"], key=lambda x: -x["shading_pct"])[: max(1, min(20, args.get("top_n", 5)))]
        return {"beam_weighted_shading_pct": r["shading"]["beam_weighted_shading_pct"],
                "monthly_beam_shading_pct": r["shading"]["monthly_beam_shading_pct"],
                "most_shaded_modules": [{"id": t["id"], "row": t["row"], "col": t["col"], "shading_pct": t["shading_pct"]} for t in top],
                "method": r["shading"]["method"]}
    if name == "missing_information":
        prj, r = _design(con, p, args["project_id"])
        pl, gaps = prj["payload"], []
        if not pl.get("obstacles"):
            gaps.append("No obstacles recorded: confirm the roof is clear (tanks, stair rooms, vents, parapets).")
        if any(o.get("height_m") in (None, 0) for o in pl.get("obstacles", []) if o.get("kind") not in ("keepout",)):
            gaps.append("Some obstacles have no height: measure on site.")
        if not (r.get("electrical")):
            gaps.append("No inverter selected: string design, SLD and cable sizing are pending.")
        if (r.get("energy") or {}).get("status") != "ok":
            gaps.append("No weather data: annual energy has not been simulated.")
        if r["boq_totals"]["unpriced_items"]:
            gaps.append(f"Unpriced BOQ items: {', '.join(r['boq_totals']['unpriced_items'])}.")
        if not r.get("structural"):
            gaps.append("No structural inputs (section weights, wind factors, roof capacity).")
        if any("not datasheet-verified" in w for w in r["warnings"]):
            gaps.append("Module/inverter data is not datasheet-verified in the equipment library.")
        return {"project": prj["name"], "missing": gaps}
    if name == "compare_equipment":
        out = []
        for eid in args["ids"][:6]:
            rec = EQ.get(con, p, eid)
            out.append({"id": eid, "model": f"{rec['manufacturer']} {rec['model']}", "kind": rec["kind"], "status": rec["status"],
                        "specs": rec["specs"]})
        return {"equipment": out}
    if name == "propose_change":
        return propose_change(con, p, args["project_id"], args["changes"], args.get("rationale", ""))
    raise ValidationFailed(name)


def propose_change(con, p: Principal, project_id, changes: dict, rationale=""):
    p.require("project.read")
    for k, v in changes.items():
        if k not in CHANGEABLE:
            raise ValidationFailed(f"'{k}' cannot be changed by the assistant (allowed: {sorted(CHANGEABLE)})")
        lo, hi = CHANGEABLE[k]
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValidationFailed(f"'{k}' must be a number in {lo}..{hi}")
    prj, before = _design(con, p, project_id)
    if "site" in prj["payload"]:
        raise ValidationFailed("Proposals currently support v0.2-format projects; edit v3 requests directly")
    new_payload = {**copy.deepcopy(prj["payload"]), **changes}
    after = run_design(_payload_to_req(new_payload))
    pid = new_id()
    with transaction(con):
        con.execute("INSERT INTO ai_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, p.org_id, project_id, prj["revision"], j(changes), j(_kpis(before)), j(_kpis(after)), rationale[:1000],
                     "pending", p.user_id, None, None, utcnow()))
        audit.for_principal(con, p, "ai.proposal_created", entity_type="project", entity_id=project_id, detail={"changes": changes})
    return {"proposal_id": pid, "status": "pending human approval", "changes": changes,
            "kpi_before": _kpis(before), "kpi_after": _kpis(after)}


def decide_proposal(con, p: Principal, proposal_id, approve: bool):
    p.require("project.update")
    pr = get_scoped(con, "ai_proposals", proposal_id, p.org_id, "Proposal")
    if pr["status"] != "pending":
        raise Conflict(f"Proposal is {pr['status']}")
    prj = P.get(con, p, pr["project_id"])
    if prj["revision"] != pr["base_revision"]:
        with transaction(con):
            con.execute("UPDATE ai_proposals SET status='stale' WHERE id=?", (proposal_id,))
        raise Conflict("Project changed since the proposal was made; ask the assistant to re-evaluate")
    if approve:
        P.update(con, p, pr["project_id"], payload={**prj["payload"], **unj(pr["changes"])}, if_match=prj["revision"],
                 note=f"AI-proposed change {unj(pr['changes'])} approved by user")
    with transaction(con):
        con.execute("UPDATE ai_proposals SET status=?, decided_by=?, decided_utc=? WHERE id=?",
                    ("applied" if approve else "rejected", p.user_id, utcnow(), proposal_id))
        audit.for_principal(con, p, "ai.proposal_" + ("applied" if approve else "rejected"), entity_type="project", entity_id=pr["project_id"])
    return {"status": "applied" if approve else "rejected"}


_NUM = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def grounding_check(answer: str, tool_outputs: list) -> dict:
    blob = json.dumps(tool_outputs, default=str)
    known = set()
    for m in _NUM.findall(blob):
        try:
            known.add(float(m.replace(",", "")))
        except ValueError:
            pass
    bad = []
    for m in _NUM.findall(answer or ""):
        try:
            v = float(m.replace(",", ""))
        except ValueError:
            continue
        if abs(v) < 13 and float(v).is_integer():
            continue                                    # small counts / list numbering / months
        if not any(abs(v - k) <= max(0.051, abs(k) * 0.005) for k in known):
            bad.append(m)
    return {"grounded": not bad, "ungrounded_numbers": bad}


def ask(con, p: Principal, question: str, *, project_id=None, provider=None) -> dict:
    p.require("project.read")
    if not question.strip() or len(question) > 4000:
        raise ValidationFailed("Question must be 1-4000 characters")
    if provider is None:
        from .providers import default_provider, ProviderUnavailable
        try:
            provider = default_provider()
        except ProviderUnavailable as e:
            return {"status": "unavailable", "message": f"AI assistant not configured: {e}. Engineering tools still work without it."}
    ctx = f"\n(Current project id: {project_id})" if project_id else ""
    messages = [{"role": "user", "content": question + ctx}]
    calls_log, outputs, usage = [], [], {}
    answer = None
    limit = int(os.environ.get("AI_DAILY_REQUESTS_PER_ORG", "500"))
    used = con.execute("SELECT COUNT(*) FROM ai_conversations WHERE org_id=? AND at_utc>=?", (p.org_id, utcnow()[:10])).fetchone()[0]
    if used >= limit:
        return {"status": "limit", "message": f"Daily AI request limit ({limit}) reached for your organisation."}
    from .providers import ProviderError
    for _ in range(MAX_STEPS):
        try:
            out = provider.complete(SYSTEM, messages, TOOLS)
        except ProviderError as e:
            return {"status": "error", "message": str(e)}
        if not out.get("tool_calls"):
            for k in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount"):
                usage[k] = usage.get(k, 0) + (out.get("usage") or {}).get(k, 0)
            answer = out.get("text") or ""
            break
        messages.append({"role": "assistant", "tool_calls": out["tool_calls"], "content": out.get("text") or "",
                         "raw": out.get("raw")})               # replayed verbatim (keeps Gemini thought signatures)
        for k in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount"):
            usage[k] = usage.get(k, 0) + (out.get("usage") or {}).get(k, 0)
        for c in out["tool_calls"]:
            try:
                res = run_tool(con, p, c["name"], c.get("args", {}))
            except (ValidationFailed, NotFound, Conflict) as e:
                res = {"error": e.message}
            calls_log.append({"name": c["name"], "args": c.get("args", {}), "ok": "error" not in res})
            outputs.append(res)
            messages.append({"role": "tool", "name": c["name"], "call_id": c.get("id"), "content": json.dumps(res, default=str)})
    else:
        answer = "I could not finish within the allowed number of steps."
    g = grounding_check(answer, outputs)
    with transaction(con):
        con.execute("INSERT INTO ai_conversations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (new_id(), p.org_id, p.user_id, project_id, question[:4000], answer, j(calls_log), j({**g, "usage": usage}),
                     f"{getattr(provider, 'name', 'unknown')}:{getattr(provider, 'model', '')}", utcnow()))
    proposals = [o for o in outputs if isinstance(o, dict) and o.get("proposal_id")]
    return {"status": "ok", "answer": answer, "tool_calls": calls_log, "grounding": g, "proposals": proposals, "usage": usage,
            "notice": None if g["grounded"] else "Some figures in this answer were not found in project data: verify before use."}
