"""The wording of the site's "Live model" page (DL-133): what pressing "Run the model" does, how it
works, what it gives a brand manager and what makes it dependable, in plain words.

The page draws; this module holds the text, so it can be read and tested in one place. Every
statement here is a fact about the system that is written down in the repository (the decision log
entry or document is named beside it) and nothing is claimed that is not. The service is described
as "production-style": it has the controls a real service has, on a small scale and on a free
hosting plan, and the limits below say what that does and does not mean.
"""

from __future__ import annotations

HEADLINE = (
    "Press a button and a separate service on AWS works the forecast out again from the saved "
    "model. If its answer equals the forecast the monthly job stored, the number on this site "
    "is not a screenshot: it can be reproduced on demand."
)

# the journey of one request, left to right: (box, a few words under it)
FLOW = (
    ("Your choice", "a segment and a horizon"),
    ("This website", "asks with a secret key"),
    ("Model service", "AWS Lambda, FastAPI"),
    ("Data release", "S3, behind a pointer"),
    ("Saved model", "stored numbers"),
    ("Answer and checks", "equal to the stored forecast?"),
)

# (title, what happens in plain words, the technical detail for the curious)
STEPS = (
    (
        "You pick a segment and press the button",
        "You choose a specialty, an age band and a gender, and how many months ahead (1, 3 or 6). "
        "Nothing is called until you press the button.",
        "The site lets one visitor start 20 live runs an hour and all visitors together 200, so "
        "nobody can use up the service for everyone else. A repeated question is answered from a "
        "ten-minute cache instead of being asked again.",
    ),
    (
        "The website asks the model service",
        "The website's server (not your browser) sends the question to a model service running "
        "on AWS. The service's address and key are kept as secrets on the server: your browser "
        "never receives either.",
        "FastAPI in a container on AWS Lambda, behind HTTPS, with an API key on every data "
        "endpoint and a limit of 60 requests a minute per key (DL-118 to DL-120). A wrong or "
        "missing key gets a 401 and does not use up the limit.",
    ),
    (
        "The service finds the current data",
        "The service works from the latest published release of the data. When a new month is "
        "published, the service switches to it by itself, with no new software to install.",
        "The monthly job puts the warehouse in a private S3 bucket and writes a small pointer file "
        "last, so nobody can see a half-finished release. The service reads the pointer every "
        "five minutes and uses a file only if its size, checksum, data version and month all "
        "match; if anything is wrong it keeps serving what it had (DL-124).",
    ),
    (
        "It applies the saved model",
        "The monthly job fitted the model and saved its numbers. The service applies those "
        "numbers to the segment's saved inputs. It does not train anything while you wait: it is "
        "arithmetic, so the answer comes back in a moment.",
        "The saved numbers are the model's scaling, its categories and its coefficients, "
        "applied with NumPy only (no machine-learning library at request time). At 6 months the "
        "served 'model' is the last month's value, so there is nothing to apply (DL-117, DL-130).",
    ),
    (
        "The service checks itself",
        "The service compares its own answer with the forecast the monthly job stored for the "
        "same segment, and says whether they are equal. It also reports which model and which "
        "version of the data it used.",
        "The comparison is called matches_stored. The tests run every segment at every horizon, "
        "and every one is predicted live and equals its stored value, to about 5e-7 (the "
        "rounding of the stored shares).",
    ),
    (
        "The website checks the service",
        "The site does its own check: is the answer equal to the stored forecast, to four "
        "decimals, and does the service hold the same version of the data as the site? It shows "
        "green when both are true and amber, with the gap, when either is not.",
        "A mismatch of data versions (for example, a new month published but not yet picked up) "
        "is reported as such instead of being shown as a wrong number. The known bias of the "
        "segment forecasts is shown beside every answer.",
    ),
)

# (label, the point in a sentence, one line of why it matters), for the four cards
BRAND_MANAGER = (
    (
        "Trust you can test",
        "A forecast you can check, not only read",
        "The number on the Forecasts page is reproduced by an independent service while you "
        "watch. If the two ever disagree, the page says so instead of hiding it.",
    ),
    (
        "Any segment, on demand",
        "Ask about one segment without waiting for an analyst",
        "Any segment the monthly job forecasts, at 1, 3 and 6 months, in seconds: handy before a "
        "meeting about a territory or a specialty.",
    ),
    (
        "Honest by default",
        "Every answer carries its caveat",
        "The model, its data version, the small-segment flag and how far the segment forecasts "
        "ran high in testing come with the number, so it is not quoted with more confidence than "
        "it has earned.",
    ),
    (
        "One source of numbers",
        "The same service can feed other tools",
        "The website, a spreadsheet or a planning tool can ask the same service and get the same "
        "answer, so two reports cannot quietly use two different numbers.",
    ),
)

# (control, what it does, where it is written down)
DEPENDABLE = (
    (
        "Same-answer check",
        "Every segment at every horizon is predicted live and must equal the stored forecast; "
        "each answer says whether it does.",
        "docs/api.md, tests/test_api.py",
    ),
    (
        "Versioned data release",
        "New data reach the service as a checked, versioned file behind a pointer written last; "
        "a bad file is refused and the old data stay in service.",
        "DL-124, docs/deployment.md",
    ),
    (
        "A gate before a refit is published",
        "A refit must pass sanity, reproducibility and stability checks (11 to 13, depending on "
        "whether the month moved) before it replaces what is served.",
        "DL-126, docs/promotion_gate.md",
    ),
    (
        "Keys, limits and a cache",
        "A key on every data endpoint, 60 requests a minute per key, a bounded answer cache, and "
        "per-visitor limits on this page.",
        "docs/api.md, docs/loadtest.md",
    ),
    (
        "Monitoring",
        "A daily check that the service is up, still asks for a key and serves the same data "
        "version as the repository; alarms to email and a dashboard.",
        "DL-121, .github/workflows/canary.yml",
    ),
    (
        "A load test",
        "On a laptop, at 50 users at once: 193 requests a second, 95% of answers within 190 ms, "
        "no failures. Not yet run against the live function with a key.",
        "docs/loadtest.md (DL-127)",
    ),
    (
        "Automated tests",
        "About 1,800 tests run on every change, including the API's own suite and an end-to-end "
        "run of the real data through every stage.",
        "tests/, .github/workflows/ci.yml",
    ),
)

# what it does not do, so the page does not overclaim
LIMITS = (
    "It is not a what-if tool. It reproduces the forecasts for the segments and the month in "
    "the data; a segment with no stored forecast is refused, not guessed.",
    "It does not make a forecast more accurate. It shows that the number is reproducible and "
    "which version of the data it came from. The segment forecasts still ran high in testing.",
    "At 6 months the forecast is the last month's value, because no model beat that rule "
    "(DL-130).",
    "It is production-style, not a service-level promise: one AWS region, hosting on a free "
    "plan, no separate staging copy, and the service's address is not public.",
)

DEMO = (
    "A 30-second demo: leave the choices as they are (they start on the busiest segment), press "
    "Run the model, and look for the green line saying the live answer equals the stored "
    "forecast."
)
