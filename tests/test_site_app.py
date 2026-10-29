"""The site itself (app/streamlit_app.py), run headlessly with Streamlit's test runner on the
committed warehouse: no exception, every tab present, and the numbers and caveats it must show.

Needs the site's packages (app/requirements.txt); the CI job `site` installs them and these tests
are skipped where Streamlit is not installed.
"""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from oa_market_intelligence.serving import data  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")
pytestmark = pytest.mark.skipif(not data.DB_PATH.exists(), reason="no published warehouse")


@pytest.fixture(scope="module")
def app():
    return AppTest.from_file(APP, default_timeout=90).run()


def test_the_page_runs_without_an_error_and_has_every_tab(app):
    assert not app.exception
    assert [t.label for t in app.tabs] == [
        "Overview", "Market trend", "Segments", "Forecasts", "Model results", "Data status",
        "Findings",
    ]


def test_the_headline_numbers_are_the_warehouses(app):
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Share in Jul 2025"] == "1.88%"
    assert metrics["Peak (Dec 2021)"] == "3.38%" and metrics["Change since the peak"] == "-44%"
    assert metrics["Months of data"] == "72" and metrics["Latest month"] == "Jul 2025"
    assert metrics["Data freshness"] in ("Stale", "Fresh")


def test_the_caveats_are_on_the_page(app):
    page = " ".join([w.value for w in app.warning] + [m.value for m in app.markdown])
    assert "No newer extract has arrived" in page or "Fresh" in {m.value for m in app.metric}
    assert "run high" in page and "5.5% at 1 month and 10.9% at 3 months" in page
    assert "Payer, regional and prescription-volume data are not part of this analysis" in page
    infos = " ".join(i.value for i in app.info)
    assert "+5.5% too high" in infos and "+10.9% too high" in infos


def test_the_findings_tab_lists_all_seventeen(app):
    assert len(app.expander) == 17
    assert app.expander[0].label.startswith("1. ")


def test_no_secret_or_sql_input_exists_on_the_page(app):
    assert not app.text_input and not app.text_area  # no free-text box in this phase
