"""Stage 19 assistant: grounding, validation, approval workflow, isolation (scripted model, no network)."""
import json, os
from backend.ai.assistant import ask, decide_proposal, grounding_check, run_tool
from backend.ai.providers import ScriptedProvider
from backend.services import projects as P
from backend.services.errors import Conflict, Forbidden, ValidationFailed
from tests.test_identity_tenancy import _db, _company, _member, _raises

SITE = {"name": "AI roof", "latitude": 17.385, "longitude": 78.487,
        "roof": [{"x": 0, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 18}, {"x": 0, "y": 18}],
        "obstacles": [{"name": "Tank", "height_m": 2.5, "polygon": [{"x": 8, "y": 7}, {"x": 12, "y": 7}, {"x": 12, "y": 10}, {"x": 8, "y": 10}]}]}


def _setup():
    con = _db()
    admin, _ = _company(con, "Unite Solar", "ravi@unite.example")
    designer = _member(con, admin, "d@unite.example", "solar_designer")
    pid = P.create(con, designer, name="AI roof", payload=SITE)["id"]
    return con, admin, designer, pid


def _tool_then_answer(name, args, answer_fn):
    return ScriptedProvider([{"text": None, "tool_calls": [{"name": name, "args": args}]},
                             lambda msgs: {"text": answer_fn(json.loads(msgs[-1]["content"])), "tool_calls": []}])


def test_grounded_answer_uses_tool_data():
    con, admin, d, pid = _setup()
    prov = _tool_then_answer("get_design_summary", {"project_id": pid},
                             lambda r: f"The roof fits {r['panel_count']} modules ({r['dc_kwp']} kWp) with {r['beam_weighted_shading_pct']} % shading.")
    out = ask(con, d, "How big a system fits?", project_id=pid, provider=prov)
    assert out["status"] == "ok" and out["grounding"]["grounded"], out
    assert out["tool_calls"] == [{"name": "get_design_summary", "args": {"project_id": pid}, "ok": True}]
    assert con.execute("SELECT COUNT(*) FROM ai_conversations").fetchone()[0] == 1


def test_fabricated_numbers_are_flagged():
    con, admin, d, pid = _setup()
    prov = _tool_then_answer("get_design_summary", {"project_id": pid},
                             lambda r: f"{r['dc_kwp']} kWp will generate 1,450 kWh/kWp and save INR 812,000 a year.")
    out = ask(con, d, "Savings?", project_id=pid, provider=prov)
    assert not out["grounding"]["grounded"] and set(out["grounding"]["ungrounded_numbers"]) >= {"1,450", "812,000"}
    assert out["notice"]
    assert grounding_check("Use 12 strings in 3 rows", []) ["grounded"]          # small counts are not flagged


def test_invalid_tool_calls_return_errors_not_crashes():
    con, admin, d, pid = _setup()
    prov = ScriptedProvider([{"text": None, "tool_calls": [{"name": "get_design_summary", "args": {"project": pid}},
                                                           {"name": "drop_tables", "args": {}}]},
                             {"text": "I could not read the project.", "tool_calls": []}])
    out = ask(con, d, "Summary?", provider=prov)
    assert [c["ok"] for c in out["tool_calls"]] == [False, False]


def test_proposal_requires_human_approval():
    con, admin, d, pid = _setup()
    prov = _tool_then_answer("propose_change", {"project_id": pid, "changes": {"tilt_deg": 12}, "rationale": "higher yield"},
                             lambda r: f"Proposal {r['proposal_id']} is waiting for approval.")
    out = ask(con, d, "Tilt the modules for more energy", project_id=pid, provider=prov)
    prop = out["proposals"][0]
    assert prop["kpi_after"]["row_pitch_m"] > prop["kpi_before"]["row_pitch_m"]          # tilt -> wider rows
    assert P.get(con, d, pid)["revision"] == 1                                         # nothing applied yet
    cust = _member(con, admin, "c@home.example", "customer")
    assert _raises(Forbidden, decide_proposal, con, cust, prop["proposal_id"], True)
    decide_proposal(con, d, prop["proposal_id"], True)
    prj = P.get(con, d, pid)
    assert prj["revision"] == 2 and prj["payload"]["tilt_deg"] == 12
    assert "AI-proposed" in P.revisions(con, d, pid)[0]["note"]
    assert _raises(Conflict, decide_proposal, con, d, prop["proposal_id"], True)
    # stale: project edited after a proposal was made
    p2 = run_tool(con, d, "propose_change", {"project_id": pid, "changes": {"perimeter_m": 1.0}})
    P.update(con, d, pid, payload={**prj["payload"], "name": "renamed"}, if_match=2)
    assert _raises(Conflict, decide_proposal, con, d, p2["proposal_id"], True)


def test_change_whitelist_and_tenant_isolation():
    con, admin, d, pid = _setup()
    assert _raises(ValidationFailed, run_tool, con, d, "propose_change", {"project_id": pid, "changes": {"latitude": 10}})
    assert _raises(ValidationFailed, run_tool, con, d, "propose_change", {"project_id": pid, "changes": {"tilt_deg": 80}})
    other, _ = _company(con, "Other EPC", "boss@other.example")
    prov = _tool_then_answer("get_design_summary", {"project_id": pid}, lambda r: str(r))
    out = ask(con, other, "Tell me about this project", project_id=pid, provider=prov)
    assert out["tool_calls"][0]["ok"] is False and "not found" in out["answer"].lower()


def test_missing_information_and_unconfigured_provider():
    con, admin, d, pid = _setup()
    gaps = run_tool(con, d, "missing_information", {"project_id": pid})["missing"]
    assert any("inverter" in g for g in gaps) and any("weather" in g for g in gaps)
    os.environ.pop("GEMINI_API_KEY", None)
    assert ask(con, d, "hi")["status"] == "unavailable"
