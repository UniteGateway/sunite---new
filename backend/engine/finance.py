"""Deterministic project-finance arithmetic. Every tariff, rate, subsidy and tax is a
caller-supplied, source-dated input; nothing jurisdiction-specific is hard-coded."""
from __future__ import annotations
import math


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    r = annual_rate_pct / 1200
    if r == 0:
        return principal / months
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)


def npv(rate_pct: float, cashflows: list[float]) -> float:
    r = rate_pct / 100
    return sum(cf / (1 + r) ** t for t, cf in enumerate(cashflows))


def irr(cashflows: list[float], lo=-0.99, hi=10.0, tol=1e-10) -> float | None:
    f = lambda r: sum(cf / (1 + r) ** t for t, cf in enumerate(cashflows))
    flo, fhi = f(lo), f(hi)
    if flo * fhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) < tol:
            break
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return 100 * (lo + hi) / 2


def payback_years(cashflows: list[float]) -> float | None:
    cum = cashflows[0]
    for t in range(1, len(cashflows)):
        prev = cum
        cum += cashflows[t]
        if cum >= 0 > prev:
            return t - 1 + (-prev / cashflows[t])
    return None if cum < 0 else 0.0


def capex_model(capex: float, year1_kwh: float, tariff_per_kwh: float, *, years: int = 25,
                degradation_pct: float, tariff_escalation_pct: float, om_year1: float,
                om_escalation_pct: float, discount_rate_pct: float,
                inverter_replacement: tuple[int, float] | None = None,
                subsidy: float = 0.0) -> dict:
    rows, flows = [], [-(capex - subsidy)]
    disc_cost = capex - subsidy
    disc_energy = 0.0
    r = discount_rate_pct / 100
    for y in range(1, years + 1):
        kwh = year1_kwh * (1 - degradation_pct / 100) ** (y - 1)
        tariff = tariff_per_kwh * (1 + tariff_escalation_pct / 100) ** (y - 1)
        om = om_year1 * (1 + om_escalation_pct / 100) ** (y - 1)
        extra = inverter_replacement[1] if inverter_replacement and inverter_replacement[0] == y else 0.0
        savings = kwh * tariff
        net = savings - om - extra
        flows.append(net)
        disc_cost += (om + extra) / (1 + r) ** y
        disc_energy += kwh / (1 + r) ** y
        rows.append({"year": y, "energy_kwh": round(kwh, 1), "tariff": round(tariff, 4),
                     "savings": round(savings, 2), "om": round(om, 2), "replacement": extra,
                     "net_cashflow": round(net, 2)})
    return {"cashflows": flows, "table": rows,
            "npv": round(npv(discount_rate_pct, flows), 2),
            "irr_pct": None if (v := irr(flows)) is None else round(v, 3),
            "simple_payback_years": None if (p := payback_years(flows)) is None else round(p, 2),
            "lcoe_per_kwh": round(disc_cost / disc_energy, 4) if disc_energy else None,
            "total_savings": round(sum(x["savings"] for x in rows), 2)}


# ---------------------------------------------------------------- Stage 15 commercial models
def _series(year1, esc_pct, years):
    return [year1 * (1 + esc_pct / 100) ** y for y in range(years)]


def _energy(year1_kwh, degradation_pct, years):
    return [year1_kwh * (1 - degradation_pct / 100) ** y for y in range(years)]


def metering_split(year1_kwh, self_consumption_pct, scheme, retail_tariff, export_tariff=0.0):
    """Value of year-1 energy under net/gross metering or zero export. Tariffs are caller inputs."""
    sc = year1_kwh * self_consumption_pct / 100
    ex = year1_kwh - sc
    if scheme == "zero_export":
        return {"self_consumed_kwh": sc, "exported_kwh": 0.0, "curtailed_kwh": ex, "year1_value": sc * retail_tariff}
    if scheme == "net":
        return {"self_consumed_kwh": sc, "exported_kwh": ex, "curtailed_kwh": 0.0, "year1_value": sc * retail_tariff + ex * export_tariff}
    if scheme == "gross":
        return {"self_consumed_kwh": 0.0, "exported_kwh": year1_kwh, "curtailed_kwh": 0.0, "year1_value": year1_kwh * export_tariff}
    raise ValueError("scheme must be net, gross or zero_export")


def loan_schedule(principal, annual_rate_pct, years):
    """Annual view of a monthly-EMI loan: interest, principal, balance."""
    m = emi(principal, annual_rate_pct, years * 12)
    bal, rows, r = principal, [], annual_rate_pct / 1200
    for y in range(1, years + 1):
        i_y = p_y = 0.0
        for _ in range(12):
            i = bal * r
            p = m - i
            bal -= p; i_y += i; p_y += p
        rows.append({"year": y, "emi_total": round(12 * m, 2), "interest": round(i_y, 2), "principal": round(p_y, 2),
                     "closing_balance": round(max(bal, 0.0), 2)})
    return {"monthly_emi": round(m, 2), "rows": rows}


def financed_purchase(capex, year1_value, *, down_payment_pct, loan_rate_pct, loan_years, degradation_pct,
                      tariff_escalation_pct, om_year1, om_escalation_pct, discount_rate_pct, years=25, subsidy=0.0):
    """Customer buys with a loan (EMI model)."""
    loan = (capex - subsidy) * (1 - down_payment_pct / 100)
    sched = loan_schedule(loan, loan_rate_pct, loan_years) if loan > 0 else {"monthly_emi": 0, "rows": []}
    flows = [-(capex - subsidy - loan)]
    vals = [year1_value * (1 - degradation_pct / 100) ** y * (1 + tariff_escalation_pct / 100) ** y for y in range(years)]
    om = _series(om_year1, om_escalation_pct, years)
    for y in range(years):
        debt = sched["rows"][y]["emi_total"] if y < len(sched["rows"]) else 0.0
        flows.append(vals[y] - om[y] - debt)
    first_positive = next((i for i, f in enumerate(flows[1:], 1) if f > 0), None)
    return {"loan_amount": round(loan, 2), "monthly_emi": sched["monthly_emi"], "loan_schedule": sched["rows"],
            "cashflows": [round(f, 2) for f in flows], "npv": round(npv(discount_rate_pct, flows), 2),
            "irr_pct": None if (v := irr(flows)) is None else round(v, 3),
            "cash_positive_from_year": first_positive, "total_net_savings": round(sum(flows), 2)}


def ppa_model(capex, year1_kwh, *, ppa_tariff, ppa_escalation_pct, grid_tariff, grid_escalation_pct, term_years,
              degradation_pct, om_year1, om_escalation_pct, developer_discount_pct, customer_discount_pct,
              debt_pct=0.0, debt_rate_pct=0.0, debt_years=0, boot_transfer=True, life_years=25,
              tax_rate_pct=0.0, depreciation_years=0):
    """RESCO / OPEX / BOOT: developer owns and sells energy; optional transfer to customer at term end."""
    E = _energy(year1_kwh, degradation_pct, life_years)
    ppa = _series(ppa_tariff, ppa_escalation_pct, term_years)
    grid = _series(grid_tariff, grid_escalation_pct, life_years)
    om = _series(om_year1, om_escalation_pct, life_years)
    debt = capex * debt_pct / 100
    sched = loan_schedule(debt, debt_rate_pct, debt_years)["rows"] if debt > 0 and debt_years else []
    dev, cust, dscr = [-(capex - debt)], [0.0], []
    for y in range(term_years):
        rev = E[y] * ppa[y]
        ds = sched[y]["emi_total"] if y < len(sched) else 0.0
        intr = sched[y]["interest"] if y < len(sched) else 0.0
        dep = capex / depreciation_years if depreciation_years and y < depreciation_years else 0.0
        taxable = rev - om[y] - intr - dep
        tax = max(0.0, taxable) * tax_rate_pct / 100
        cfads = rev - om[y] - tax
        if ds:
            dscr.append(cfads / ds)
        dev.append(cfads - ds)
        cust.append(E[y] * (grid[y] - ppa[y]))
    for y in range(term_years, life_years):
        cust.append(E[y] * grid[y] - om[y] if boot_transfer else 0.0)
    rev_total = sum(E[y] * ppa[y] for y in range(term_years))
    return {"developer_cashflows": [round(x, 2) for x in dev],
            "developer_equity_irr_pct": None if (v := irr(dev)) is None else round(v, 3),
            "developer_npv": round(npv(developer_discount_pct, dev), 2),
            "customer_cashflows": [round(x, 2) for x in cust],
            "customer_savings_npv": round(npv(customer_discount_pct, cust), 2),
            "customer_total_savings": round(sum(cust), 2), "ppa_revenue_total": round(rev_total, 2),
            "min_dscr": round(min(dscr), 3) if dscr else None, "debt_amount": round(debt, 2),
            "model": "BOOT" if boot_transfer else "RESCO/OPEX"}


def group_captive_model(capex, year1_kwh, *, consumer_equity_pct, min_equity_pct, landed_charges_per_kwh,
                        generation_tariff, grid_tariff, grid_escalation_pct, degradation_pct, years, discount_rate_pct):
    """Consumer's view of a group-captive share. Eligibility thresholds and open-access charges are inputs
    (they differ by state and change over time)."""
    E = _energy(year1_kwh, degradation_pct, years)
    grid = _series(grid_tariff, grid_escalation_pct, years)
    landed = generation_tariff + landed_charges_per_kwh
    flows = [-capex * consumer_equity_pct / 100] + [E[y] * (grid[y] - landed) for y in range(years)]
    return {"eligible_equity": consumer_equity_pct >= min_equity_pct, "landed_cost_per_kwh": round(landed, 4),
            "year1_saving_per_kwh": round(grid_tariff - landed, 4), "equity_investment": round(-flows[0], 2),
            "cashflows": [round(f, 2) for f in flows], "npv": round(npv(discount_rate_pct, flows), 2),
            "irr_pct": None if (v := irr(flows)) is None else round(v, 3)}
