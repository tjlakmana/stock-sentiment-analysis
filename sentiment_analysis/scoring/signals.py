"""
Importance signal definitions.

Each Signal is a (keyword, boost) pair where keyword is a lowercase substring
matched against the lowercased article text, and boost is points added to the
score (negative values are penalties).

Positive signals are organized into SignalGroups, each with a cap that limits
how much a single group can contribute. This prevents any one category from
dominating the score.

To tune the scorer:
  - Increase/decrease a Signal's boost to change how much it matters.
  - Adjust a SignalGroup's cap to limit the group's maximum contribution.
  - Add new Signals where you observe scoring errors.
  - Adjust PENALTY_CAP to widen or narrow the penalty floor.
"""
from __future__ import annotations

from typing import NamedTuple


class Signal(NamedTuple):
    keyword: str   # lowercase substring; matched with `keyword in text.lower()`
    boost: int     # points to add (negative for penalties)


class SignalGroup(NamedTuple):
    name: str
    signals: list[Signal]
    cap: int       # max total positive contribution from this group


# ── Source baseline scores ─────────────────────────────────────────────────
# Starting point before any keyword signals are applied.
# sec_edgar and fda are high because the sources themselves are material;
# press wires start low because most content is routine.

SOURCE_BASE: dict[str, int] = {
    "sec_edgar":              55,   # 8-K material event filings
    "sec_form4":              10,   # Routine insider trade disclosures
    "fda":                    52,   # FDA press releases (always healthcare-relevant)
    "globe_newswire_ma":      38,   # M&A-focused newswire (above-average base)
    "globe_newswire_finance": 18,   # General financial newswire
    "pr_newswire":            18,   # Corporate press releases
}

DEFAULT_BASE = 18   # fallback for unknown sources


# ── Positive signal groups ─────────────────────────────────────────────────
# Within each group, all matching signal boosts are summed, then capped at
# group.cap. Cross-group contributions stack without a cross-cap, so a
# single article can score from multiple groups (e.g. M&A + earnings).

SIGNAL_GROUPS: list[SignalGroup] = [

    # ── FDA / Regulatory ───────────────────────────────────────────────────
    # Top-tier: any FDA approval event sends a stock moving regardless of size.
    SignalGroup("fda_regulatory", [
        Signal("fda approved",                    52),
        Signal("fda grants approval",             52),
        Signal("fda granted approval",            52),
        Signal("grants accelerated approval",     55),
        Signal("accelerated approval",            48),
        Signal("breakthrough therapy designation", 44),
        Signal("breakthrough therapy",            40),
        Signal("priority review designation",     36),
        Signal("priority review",                 34),
        Signal("complete response letter",        46),
        Signal("clinical hold",                   42),
        Signal("fda rejected",                    44),
        Signal("fda placed a clinical hold",      46),
        Signal("advisory committee",              32),
        Signal("adcom",                           32),
        Signal("nda submission",                  26),
        Signal("bla submission",                  26),
        Signal("510(k) clearance",                24),
        Signal("510k clearance",                  24),
        Signal("510k cleared",                    24),
        Signal("510(k) cleared",                  24),
        Signal("phase 3 results",                 54),
        Signal("phase iii results",               54),
        Signal("pivotal trial results",           54),
        Signal("phase 3 data",                    40),
        Signal("phase iii data",                  40),
        Signal("meets primary endpoint",          22),
        Signal("missed primary endpoint",         22),
        Signal("phase 3 trial",                   22),
        Signal("phase iii trial",                 22),
        Signal("phase 2 results",                 18),
        Signal("phase ii results",                18),
    ], cap=55),

    # ── Earnings / Financial results ───────────────────────────────────────
    # Earnings surprises and guidance changes are the most traded events.
    SignalGroup("earnings", [
        Signal("beats estimates",                 42),
        Signal("beat estimates",                  42),
        Signal("exceeds estimates",               42),
        Signal("topped estimates",                40),
        Signal("misses estimates",                42),
        Signal("missed estimates",                42),
        Signal("falls short of estimates",        40),
        Signal("beats expectations",              40),
        Signal("misses expectations",             40),
        Signal("earnings beat",                   40),
        Signal("earnings miss",                   40),
        Signal("raises full-year",                36),
        Signal("raises annual",                   34),
        Signal("raises guidance",                 32),
        Signal("raised guidance",                 32),
        Signal("increases outlook",               30),
        Signal("lowers guidance",                 38),
        Signal("lowered guidance",                38),
        Signal("cuts guidance",                   38),
        Signal("reduces guidance",                36),
        Signal("withdraws guidance",              40),
        Signal("suspends guidance",               40),
        Signal("quarterly results",               26),
        Signal("fourth quarter results",          26),
        Signal("q4 results",                      24),
        Signal("third quarter results",           26),
        Signal("q3 results",                      24),
        Signal("second quarter results",          26),
        Signal("q2 results",                      24),
        Signal("first quarter results",           26),
        Signal("q1 results",                      24),
        Signal("full-year results",               26),
        Signal("annual results",                  24),
        Signal("earnings per share",              20),
        Signal("revenue of $",                    16),
        Signal("revenue grew",                    15),
        Signal("revenue declined",                18),
        Signal("revenue fell",                    18),
    ], cap=55),

    # ── M&A / Corporate actions ────────────────────────────────────────────
    # Definitive agreement language is highly specific; hostile/unsolicited is even more so.
    SignalGroup("corporate_action", [
        Signal("agrees to acquire",               52),
        Signal("agreement to acquire",            52),
        Signal("definitive agreement to acquire", 55),
        Signal("definitive agreement",            48),
        Signal("merger agreement",                50),
        Signal("hostile bid",                     55),
        Signal("hostile takeover",                55),
        Signal("unsolicited offer",               50),
        Signal("unsolicited bid",                 50),
        Signal("acquires for $",                  50),
        Signal("acquires for approximately",      48),
        Signal("agreed to be acquired",           52),
        Signal("buyout",                          44),
        Signal("take private",                    46),
        Signal("going-private",                   46),
        Signal("takeover bid",                    48),
        Signal("spin-off",                        28),
        Signal("spinoff",                         28),
        Signal("divestiture",                     26),
        Signal("strategic review",                28),
        Signal("exploring strategic alternatives", 32),
        Signal("special dividend",                32),
        Signal("dividend cut",                    52),
        Signal("dividend suspension",             55),
        Signal("suspends dividend",               55),
        Signal("reduces dividend",                40),
        Signal("dividend declared",               12),
        Signal("declares dividend",               12),
        Signal("share repurchase",                16),
        Signal("stock buyback",                   16),
        Signal("going public",                    28),
        Signal("goes public",                     28),
        Signal("initial public offering",         28),
        Signal("ipo priced",                      34),
        Signal("s-1 registration",                20),
    ], cap=55),

    # ── Corporate distress / Legal / Regulatory ────────────────────────────
    # These events cause the largest single-day moves; score them as highest tier.
    SignalGroup("distress", [
        Signal("going concern",                   58),
        Signal("chapter 11",                      60),
        Signal("chapter 7",                       60),
        Signal("bankruptcy protection",           60),
        Signal("filed for bankruptcy",            60),
        Signal("restatement",                     48),
        Signal("restating financial",             50),
        Signal("material weakness",               44),
        Signal("material misstatement",           46),
        Signal("internal control weakness",       40),
        Signal("sec investigation",               50),
        Signal("doj investigation",               50),
        Signal("subpoena",                        40),
        Signal("class action lawsuit",            36),
        Signal("class action",                    34),
        Signal("securities fraud",                48),
        Signal("accounting fraud",                50),
        Signal("fraud investigation",             50),
        Signal("delisted",                        50),
        Signal("nasdaq delisting",                50),
        Signal("nyse delisting",                  50),
        Signal("trading suspended",               50),
        Signal("share trading halted",            50),
    ], cap=60),

    # ── Analyst actions ────────────────────────────────────────────────────
    # Upgrade/downgrade alone is medium importance; target changes are lower.
    SignalGroup("analyst", [
        Signal("upgraded to buy",                 28),
        Signal("upgraded to strong buy",          30),
        Signal("downgraded to sell",              30),
        Signal("downgraded to underperform",      28),
        Signal("downgraded to underweight",       28),
        Signal("downgraded to neutral",           22),
        Signal("upgraded to overweight",          24),
        Signal("upgraded to outperform",          24),
        Signal("downgraded from",                 20),
        Signal("upgraded from",                   20),
        Signal("price target raised",             16),
        Signal("price target cut",                18),
        Signal("price target increased",          14),
        Signal("price target lowered",            16),
        Signal("pt raised",                       14),
        Signal("pt cut",                          16),
        Signal("initiated coverage with",         16),
        Signal("initiates coverage with",         16),
        Signal("initiated at buy",                18),
        Signal("initiated at sell",               18),
    ], cap=30),

    # ── Macroeconomic events ───────────────────────────────────────────────
    # Fed decisions and major data releases move all equities.
    SignalGroup("macro", [
        Signal("federal reserve",                 35),
        Signal("federal open market committee",   38),
        Signal("fomc meeting",                    38),
        Signal("fomc decision",                   40),
        Signal("rate hike",                       36),
        Signal("rate cut",                        36),
        Signal("rate pause",                      32),
        Signal("interest rate decision",          36),
        Signal("nonfarm payroll",                 34),
        Signal("jobs report",                     34),
        Signal("unemployment rate",               30),
        Signal("cpi report",                      34),
        Signal("consumer price index",            32),
        Signal("inflation data",                  32),
        Signal("pce inflation",                   34),
        Signal("core pce",                        32),
        Signal("gdp growth",                      30),
        Signal("gdp report",                      30),
        Signal("treasury yield",                  24),
        Signal("yield curve inversion",           28),
        Signal("jackson hole",                    38),
    ], cap=55),

    # ── Insider activity ───────────────────────────────────────────────────
    # Large trades matter; routine Form 4s don't (handled via low source base).
    SignalGroup("insider", [
        Signal("insider purchased",               14),
        Signal("director purchased",              12),
        Signal("ceo purchased",                   18),
        Signal("cfo purchased",                   16),
        Signal("director bought",                 12),
        Signal("ceo bought",                      18),
        Signal("significant insider",             16),
        Signal("cluster of insider",              20),
    ], cap=20),
]


# ── Penalty signals ────────────────────────────────────────────────────────
# Applied after all positive group boosts. Total penalty is capped at
# PENALTY_CAP so penalties can't push a genuinely important article below 0.

PENALTY_SIGNALS: list[Signal] = [
    # Market research / industry reports (most common low-value content type)
    Signal("market research report",             -30),
    Signal("industry research report",           -30),
    Signal("global market research",             -28),
    Signal("global market",                      -22),
    Signal("market is expected to reach",        -26),
    Signal("market size",                        -22),
    Signal("market forecast",                    -22),
    Signal("market analysis report",             -24),
    Signal("market report",                      -22),
    Signal("industry report",                    -24),
    Signal("research report",                    -22),
    Signal("white paper",                        -20),
    Signal("new study shows",                    -16),
    Signal("survey reveals",                     -16),
    Signal("survey finds",                       -16),
    Signal("report finds",                       -14),
    Signal("report reveals",                     -14),
    # Executive appointments / personnel changes (routine)
    Signal("appoints",                           -12),
    Signal("named as chief",                     -10),
    Signal("joins as",                           -10),
    Signal("to join as",                         -10),
    # Conference / presentation announcements
    Signal("to present at",                      -12),
    Signal("presents at",                        -12),
    Signal("will present at",                    -12),
    Signal("to participate in",                  -10),
    Signal("to attend",                          -8),
    # Awards and recognition
    Signal("named best",                         -16),
    Signal("ranked best",                        -16),
    Signal("award-winning",                      -14),
    Signal("recognized as",                      -14),
    Signal("certification",                      -10),
    # Generic partnership announcements
    Signal("enters into partnership",            -8),
    Signal("signs partnership agreement",        -8),
]

PENALTY_CAP = -35   # penalties can reduce the score by at most 35 points


# ── Label thresholds ──────────────────────────────────────────────────────

def score_to_label(score: int) -> str:
    """Map a 0–100 score to a display label."""
    if score >= 70:
        return "High"
    if score >= 40:
        return "Medium"
    return "Low"
