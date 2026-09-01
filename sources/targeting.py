"""
Targeting brief builder — turns a detected company's structured fields into
a DEEP, buyer-useful summary: category positioning, signal strength, ICP
relevance to a finance-ops GTM buyer, and a suggested outreach angle.

All synthesis is grounded ONLY in the fields we actually have from the
source (one_liner, description, industry, subindustry, team_size, stage,
cohort, founders, links). We do NOT invent market data, revenue figures,
or traction the source does not provide.

Designed for the task's buyer: a Senior GTM at a finance company (Rho),
so relevance scoring favours fintech / finance-adjacent / B2B-enterprise
targets.
"""

# ── taxonomy: keyword -> (category, relevance_to_finance_gtm) ──
# relevance: 3 = directly finance/fintech; 2 = B2B enterprise adjacent;
#            1 = infra/devtools/consumer; 0 = unknown
TAXONOMY = [
    (("fintech", "payments", "banking", "spend", "treasury", "revenue cycle",
      "invoicing", "accounting", "lending", "insurance", "regul", "compliance",
      "fx", "crypto", "defi", "credit", "card", "finance", "money", "billing",
      "funding", "capital", "payroll"), 3, "Fintech / Finance"),
    (("enterprise", "b2b", "sales", "crm", "marketing", "support", "workflow",
      "automation", "document", "contract", "procurement", "hr",
      "operations"), 2, "B2B Enterprise"),
    (("ai", "ml", "agent", "model", "llm", "inference", "training",
      "robot", "compute", "cloud", "infrastructure", "devtools", "api",
      "database", "data", "security", "cyber"), 1, "AI / Infrastructure"),
    (("consumer", "social", "gaming", "creator", "content", "travel",
      "health", "food", "fitness", "shopping"), 1, "Consumer / Other"),
]

# Category -> suggested outreach angle (template, filled with verbatim facts)
ANGLES = {
    3: ("Direct finance-ops fit — open on the specific {clip}, tie it to "
        "spend/treasury/revenue outcomes the buyer owns, offer a concrete "
        "pilot for their vertical."),
    2: ("Enterprise workflow angle — open on {clip}, position around the "
        "process it replaces and a named buyer inside finance/ops."),
    1: ("Infra/efficiency angle — open on {clip}, connect to cost or "
        "infrastructure budget the buyer optimises."),
    0: ("Discovery angle — open on the founder's public statement ({clip}), "
        "ask what problem they are attacking and whether finance is in scope."),
}

# Signal text per alert type
SIGNALS = {
    "early_founder": "⚡ EARLY — founder announced on social BEFORE YC official listing",
    "new_yc_company": "✅ CONFIRMED — now in the YC directory",
    "new_speedrun_company": "✅ CONFIRMED — in a16z Speedrun program",
    "": "—",
}


def _classify(text: str, industry: str) -> tuple[int, str]:
    """Return (relevance, category) from all available descriptive text."""
    hay = f"{text} {industry}".lower()
    for keywords, rel, cat in TAXONOMY:
        if any(k in hay for k in keywords):
            return rel, cat
    return 0, "Unclassified"


def _clip(company: dict) -> str:
    """Pick the single most descriptive verbatim line (fields order matters)."""
    for k in ("key_signal", "description", "one_liner", "long_description"):
        v = (company.get(k) or "").strip()
        if v:
            return v
    return company.get("company_name", "")


def build_targeting_brief(company: dict, source: str = "") -> dict:
    """Build the deep value/targeting brief from a company record.

    Args:
        company: raw record dict (from YC directory or Speedrun sources),
                 or an alert dict (keys: company_name, description/one_liner,
                 industry, team_size, stage, cohort, founders, alert_type).
    Returns:
        dict with: what, category, relevance, relevance_label, signal,
                   timing, angle, confidence, founder.
    """
    clip = _clip(company)
    industry = (company.get("industry") or company.get("industries") or "")
    if isinstance(industry, list):
        industry = ", ".join(industry)

    relevance, cat = _classify(clip, str(industry))

    # Signal / status
    atype = company.get("alert_type", source if source in SIGNALS else "")
    signal = SIGNALS.get(atype, company.get("status", "—"))

    # Timing / momentum
    timing_parts = []
    batch = company.get("batch") or company.get("cohort")
    if batch:
        timing_parts.append(f"batch/cohort {batch}")
    stage = company.get("stage")
    if stage:
        timing_parts.append(f"stage {stage}")
    team = company.get("team_size")
    if team:
        timing_parts.append(f"~{team} person team")
    launched = company.get("launched_at") or company.get("founded_year")
    if launched:
        timing_parts.append(f"launched {launched}")
    timing = ", ".join(timing_parts) if timing_parts else "timing TBD from public data"

    # Outreach angle
    angle_tpl = ANGLES.get(relevance, ANGLES[0])
    angle = angle_tpl.format(clip=clip.strip().replace("|", "")[:160])

    # Founder evidence chain
    founder = ""
    founders = company.get("founders") or company.get("founder_set")
    if founders and isinstance(founders, list) and isinstance(founders[0], dict):
        f = founders[0]
        fname = f.get("name", "")
        role = f.get("role", "")
        founder = f"{fname}" + (f" ({role})" if role else "")
    if not founder:
        founder = company.get("founder_name", "")
        handle = company.get("founder_handle", "")
        if handle:
            founder = f"{founder} (@{handle})".strip()

    confidence = "strong" if atype != "early_founder" else "early-signal — verify before acting"

    return {
        "what": clip,
        "category": cat,
        "relevance": relevance,
        "relevance_label": {
            3: "HIGH — direct finance-ops fit for this buyer",
            2: "MEDIUM — enterprise/B2B, adjacent to finance workstreams",
            1: "LOW-MEDIUM — infra/devtools/consumer, indirect finance angle",
            0: "TBD — not yet classified; verify category manually",
        }.get(relevance, ""),
        "signal": signal,
        "timing": timing,
        "angle": angle,
        "confidence": confidence,
        "founder": founder,
    }