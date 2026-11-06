"""The plain-words layer of the site (DL-107): a glossary, one "in plain words" paragraph for each
page, a short "how to read it" note for each chart, and the terms each page uses.

Only wording lives here, so it can be tested: every term a page lists is defined, every page and
every chart note exists, and the notes keep the caveats the numbers need (nothing here gives a
figure, so nothing can drift from the data). It says what a thing is and how to read it, and says
what a chart does not tell you; it never claims a cause.
"""

from __future__ import annotations

GLOSSARY: dict[str, str] = {
    "Share of visits": (
        "Out of every 100 visits where Zilretta, a generic steroid injection or an NSAID/OTC "
        "product was recorded, how many were Zilretta. It is a share of visits in IQVIA's data, "
        "not of patients, prescriptions or sales."
    ),
    "Visits": (
        "A treatment visit counted in IQVIA's data. The site sees monthly totals only, never "
        "individual patients."
    ),
    "IQVIA data": (
        "The data set behind most of the site: monthly counts of treatment visits from IQVIA's "
        "panel. A panel is a sample, so it is not every visit in the country, and its coverage "
        "can change over time."
    ),
    "Percentage point (pp)": (
        "The gap between two percentages. A share moving from 2.0% to 2.5% has risen by 0.5 "
        "points. The site gives gaps in points so a change in a share is never confused with a "
        "percentage of a percentage."
    ),
    "Forecast range (80%, 90%)": (
        "The span a forecast is expected to land inside. A 90% range should hold in about 9 test "
        "months out of 10, and an 80% range in about 8. The further ahead, the wider the range."
    ),
    "Baseline (same as last month)": (
        "The simplest forecast there is: next month will be what last month was. Every model was "
        "tested against it. For the national share no model beat it, so that is the national "
        "forecast."
    ),
    "Bias": (
        "A tendency to miss in the same direction. The segment model's forecasts ran high on "
        "average in testing, so the site shows how far, instead of hiding it."
    ),
    "Segment": (
        "One combination of specialty, age band and gender, for example rheumatology, ages 65 to "
        "74, female."
    ),
    "Small group": (
        "A group with so few visits that one visit more or less moves its share a lot. The site "
        "fades it, marks it small and keeps it out of headlines."
    ),
    "Decline alert": (
        "A flag, one month ahead, on a segment the model expects to fall by 30% or more. About "
        "half the alerts were real declines in testing, and much of that is a group drifting back "
        "to its usual level. It is a reason to look, never a reason to act on its own."
    ),
    "Regression to the mean": (
        "A group that jumped is likely to drift back toward its usual level, and one that dipped "
        "to rise, with no cause needed. A model can look good just by predicting that."
    ),
    "Ranking score (PR-AUC)": (
        "How well the alert puts the segments that really fall near the top of its list. Higher "
        "is better. A rule that guessed would score about the share of segments that truly fall."
    ),
    "Rank correlation": (
        "How closely two orderings agree: +1 is the same order, 0 is unrelated and -1 is reversed."
    ),
    "Pre-registered": (
        "Written down before any model ran: the tests, the thresholds and what counted as a pass. "
        "It stops the results being bent to fit afterwards."
    ),
    "Round 2 (provisional)": (
        "Ideas tried after seeing the results of round 1. Because they were chosen with that "
        "knowledge, a pass is provisional: a lead to confirm, not a result. Nothing from "
        "round 2 is used on the site."
    ),
    "What the model relies on (SHAP)": (
        "A way of seeing which kinds of information the segment model leaned on most. It shows "
        "what the model used, not what drives the market."
    ),
    "Net sales": (
        "The company's reported sales after discounts and rebates, from its filings. They are "
        "company-wide, so they are not comparable to IQVIA visits in size."
    ),
    "Index (average quarter of 2021 = 100)": (
        "Each line is divided by its own 2021 average and shown as a percentage, so sales and "
        "visits can share one axis. It shows direction and timing, not size."
    ),
    "Original Medicare": (
        "The government's own Medicare. It does not include Medicare Advantage, the plans run by "
        "private insurers, so those patients are not in the Medicare figures here."
    ),
    "Visible provider": (
        "A provider that appears in the public Medicare file as billing any steroid injection "
        "code. The adoption rate is the share of them that billed Zilretta (billing code J3304)."
    ),
    "95% interval": (
        "The range a state's true rate plausibly sits in, given how many providers were seen. It "
        "is wide where few were seen, so a faded tile should be read loosely."
    ),
    "Room to the national rate": (
        "How many more providers would have to bill Zilretta for a state to reach the national "
        "rate. It is arithmetic, not a target and not a forecast."
    ),
    "Not verified": ("Shown, but not yet checked against its source, so it is not relied on."),
    "Neighbours": (
        "The other groups along the same split, for example the other specialties, shown beside "
        "the group so you can see where it sits."
    ),
}

# what each page is, in a few plain sentences (shown under the title as "In plain words")
PAGE_SUMMARY: dict[str, str] = {
    "Overview": (
        "Zilretta is an injection for knee osteoarthritis pain. This site shows how often it is "
        "chosen against generic steroid injections and NSAIDs in IQVIA's visit data, where that "
        "share is heading, and how far the forecasts can be trusted. The short version: the best "
        "national forecast is simply last month's value, and the size of the fall in visits is "
        "not verified."
    ),
    "Market trend": (
        "Pick any group and see how Zilretta's share of visits has moved month by month. The "
        "cards give the latest share, the peak and the change. A group drawn faded is small, so "
        "read its direction, not single months."
    ),
    "Segments": (
        "Groups side by side: who has the highest share, who supplies most of the visits and "
        "where the share is rising or falling. Click any bar, cell or card to open that group."
    ),
    "Segment detail": (
        "One group on its own: its share against the whole market and against its neighbours, "
        "what the stored forecast says about it and links to see it on the other pages."
    ),
    "Forecasts": (
        "Where the share is expected to be in 1, 3 and 6 months, with a range around it. The "
        "national forecast is last month's value because nothing beat it. The segment forecasts "
        "ran high in testing, and the alert is a prompt to look, not a prediction to act on."
    ),
    "Model results": (
        "The scorecard: each model against a simple rule, tested under rules written before any "
        "model ran. Round 2 ideas were tried afterwards, so a pass there is a lead to confirm "
        "and none is used."
    ),
    "Data status": (
        "How fresh the data are and what ran last: whether the last run worked, how many months "
        "it holds, how old the newest month is, and what each stage and input contributed."
    ),
    "Findings": (
        "What the analyses found, in plain language and grouped by theme, with what each means "
        "for a decision and a link to the evidence. Start with the four to read first."
    ),
    "Context": (
        "Company sales kept rising while IQVIA's visits fell. This page puts the two side by "
        "side, shows the four checks tried to explain the gap (none accounts for its size) and "
        "adds Medicare billing by state and the events around the product. Nothing here says "
        "what caused the gap."
    ),
    "Market map": (
        "How products, makers, specialties and events connect. Pick one to see what it is linked "
        "to. A link means the two appear together in the data, not that one causes the other."
    ),
    "Ask Claude": (
        "Type a question in plain words and get an answer built from the site's own tables, with "
        "the figures to check. It is off until a key is configured for the site."
    ),
}

# the glossary terms each page uses, in the order they are met
PAGE_TERMS: dict[str, tuple[str, ...]] = {
    "Overview": (
        "Share of visits",
        "IQVIA data",
        "Baseline (same as last month)",
        "Forecast range (80%, 90%)",
        "Bias",
        "Percentage point (pp)",
    ),
    "Market trend": ("Share of visits", "Visits", "Small group", "Percentage point (pp)"),
    "Segments": ("Share of visits", "Small group", "Percentage point (pp)", "Segment"),
    "Segment detail": (
        "Share of visits",
        "Neighbours",
        "Segment",
        "Percentage point (pp)",
        "Decline alert",
        "Small group",
    ),
    "Forecasts": (
        "Baseline (same as last month)",
        "Forecast range (80%, 90%)",
        "Bias",
        "Segment",
        "Decline alert",
        "Regression to the mean",
        "Small group",
    ),
    "Model results": (
        "Pre-registered",
        "Baseline (same as last month)",
        "Bias",
        "Ranking score (PR-AUC)",
        "Rank correlation",
        "Regression to the mean",
        "Round 2 (provisional)",
        "What the model relies on (SHAP)",
    ),
    "Data status": ("IQVIA data",),
    "Findings": ("Share of visits", "Round 2 (provisional)", "Regression to the mean"),
    "Context": (
        "Net sales",
        "Index (average quarter of 2021 = 100)",
        "Original Medicare",
        "Visible provider",
        "95% interval",
        "Room to the national rate",
        "Not verified",
    ),
    "Market map": ("Visits", "Not verified"),
    "Ask Claude": ("Share of visits", "IQVIA data"),
}

# a short note for a chart or table: how to read it, and what it does not tell you
EXPLAIN: dict[str, dict[str, str]] = {
    "overview_fan": {
        "read": (
            "The blue line is the real share each month. The orange marks are the forecasts for "
            "1, 3 and 6 months ahead, and the bars around them are the ranges they are expected "
            "to land inside (thick 80%, thin 90%). The ranges widen with distance because the "
            "further out, the less certain it is."
        ),
        "not": "It does not say why the share moves, and it is not a sales forecast.",
    },
    "trend_chart": {
        "read": (
            "Each point is one month: Zilretta's visits as a share of the three treatments' "
            "visits for the group you chose. The amber dashes are a straight line fitted to the "
            "points to show the overall direction. The shaded band is a stretch of months when "
            "the data capture dipped for all three treatments. Drag across the chart to pick a "
            "range: the cards and the panel beside it then describe only those months."
        ),
        "not": (
            "A move in one month, especially for a small group, can be chance. The line shows "
            "direction, not a cause."
        ),
    },
    "seg_bars": {
        "read": (
            "Each bar is a group, ranked. Longer means a bigger share of that group's visits "
            "went to Zilretta (or more visits, or a bigger change, if you chose those). Faded "
            "bars are small groups. Click a bar to open the group."
        ),
        "not": "A high share is not a high volume: a small group can have a high share.",
    },
    "seg_grid": {
        "read": (
            "Rows are specialties, columns are age bands, and a darker cell is a higher share of "
            "visits going to Zilretta. A grey cell has too few visits to show a rate."
        ),
        "not": "It compares groups; it does not say why one cell is darker than another.",
    },
    "seg_time": {
        "read": (
            "One line per group you pick, with Zilretta's share for each calendar year, so you "
            "can see whether groups rise and fall together. Point at a line to bring it forward."
        ),
        "not": "The first and last years are part years, so compare them with care.",
    },
    "detail_compare": {
        "read": (
            "The group's share each month (teal line) against the share across all visits "
            "(grey dashed line). Above the grey line, Zilretta takes a bigger share in this group "
            "than in the market as a whole. Drag across the chart to see the average of both over "
            "a range of months."
        ),
        "not": "A gap to the market is a description, not a reason.",
    },
    "fc_fan": {
        "read": (
            "The blue line is the real national share. The orange marks are the forecasts at 1, "
            "3 and 6 months ahead, and the bars around them are the 80% and 90% ranges. The "
            "forecast sits level with the last known month because no model beat that simple "
            "rule."
        ),
        "not": (
            "The months forecast here have already passed in real life; the site has no newer "
            "extract yet."
        ),
    },
    "fc_spec": {
        "read": (
            "For each specialty, the orange bar is the forecast share and the dark tick is the "
            "share in the latest month. If the bar is shorter than the tick, the model expects "
            "the share to slip."
        ),
        "not": (
            "These forecasts ran high on average in testing, so read them as a baseline, not as "
            "a prediction for one segment."
        ),
    },
    "fc_table": {
        "read": (
            "One row per segment: its latest share, the forecast, the change between them and "
            "the visits behind it. Segments marked small are noisy. Click a row to open it."
        ),
        "not": "A forecast change is the model's expectation, not something that will happen.",
    },
    "fc_alerts": {
        "read": (
            "The segments the model expects to fall next month, with the visits behind each. "
            "The more visits, the steadier the flag."
        ),
        "not": (
            "About half were real declines in testing, and much of that is regression to the "
            "mean. Use it to decide where to look, not what to do."
        ),
    },
    "ctx_company": {
        "read": (
            "Two lines on one axis: the company's net sales and IQVIA's Zilretta visits, each "
            "shown as a percentage of its own 2021 average. When they move together they rise "
            "and fall at the same time; when they part, one is going up while the other goes "
            "down."
        ),
        "not": (
            "It does not show that the visits fall is real in size: whether IQVIA's coverage "
            "changed is unverified."
        ),
    },
    "ctx_map": {
        "read": (
            "Each tile is a state, placed roughly where it is on a map, and a darker tile is a "
            "higher share of Medicare providers billing Zilretta. A faded tile (see the key) has "
            "fewer than 100 providers, so its rate is uncertain. Click a state to see it on its "
            "own."
        ),
        "not": (
            "It is Original Medicare billing, not IQVIA, and it does not say why states differ."
        ),
    },
    "ctx_state": {
        "read": (
            "The teal line is the state's rate each year with its 95% interval shaded. The "
            "dashed grey line is the national rate. The figures at the side are for the latest "
            "year."
        ),
        "not": "A state below the national rate is not necessarily under-served or a target.",
    },
    "map_events": {
        "read": (
            "The share over time with a thin line at the date of each event (approvals, payment "
            "rules, guidelines), shaded if the event lasted a period and dashed grey if it is "
            "not verified, so you can see what was going on around each move."
        ),
        "not": (
            "An event marked near a move does not mean it caused the move, and events marked "
            "not verified have not been checked against their source."
        ),
    },
    "ask_answer": {
        "read": (
            "Under each answer, Sources names the pages whose tables it used and opens them, "
            "'Check these figures' lists numbers it quoted that were not in the data it looked "
            "up, and 'Data the answer used' shows exactly what it looked up."
        ),
        "not": (
            "It is a language model, so it can be wrong even with sources. Check a figure on the "
            "page it came from before you quote it."
        ),
    },
    "mr_scoreboard": {
        "read": (
            "Each model is compared with the simple rule 'same as last month'. A model counts as "
            "better only if it beat that rule under tests written before any model ran."
        ),
        "not": "Doing well on one task says nothing about the others.",
    },
    "mr_round2": {
        "read": (
            "Seven ideas tried after round 1, each with a pass mark set in advance. A pass "
            "means it cleared that mark on this data."
        ),
        "not": (
            "They were chosen after seeing round 1, so every pass is provisional and nothing "
            "from round 2 is used on the site."
        ),
    },
    "mr_shap": {
        "read": (
            "Each bar is a kind of information (such as recent trend or visit volume) and how "
            "much of the model's movement in its forecasts comes from it. A longer bar means the "
            "model leaned on it more."
        ),
        "not": "It shows what the model used, not what drives the market.",
    },
}


def terms_for(page: str) -> list[tuple[str, str]]:
    """The glossary entries for a page, in the order the page meets them."""
    return [(term, GLOSSARY[term]) for term in PAGE_TERMS.get(page, ())]


def summary_for(page: str) -> str | None:
    """The page's "in plain words" paragraph, or None for a page with none."""
    return PAGE_SUMMARY.get(page)


def note_for(key: str) -> dict[str, str] | None:
    """The "how to read it" note for a chart, or None for a key with none."""
    return EXPLAIN.get(key)
