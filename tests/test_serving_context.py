"""Tests for the Context tab's logic (serving/context.py), its HTML (style.timeline_rows) and its
two charts: sales against visits, the stored gap checks, Medicare adoption by state and the
events, by hand on small inputs and against the committed public-data subset. No Streamlit
needed."""

import pandas as pd
import pytest

from oa_market_intelligence.serving import context, data, style


def _company():
    """Quarterly net sales and IQVIA visits, 2021 to 2024, with part years either side that must
    not count: sales 100, 110, 120, 130 a quarter and visits 40, 44, 36, 24 a quarter."""
    rows = [("2020Q4", 90, 30)]
    for year, sales, visits in ((2021, 100, 40), (2022, 110, 44), (2023, 120, 36), (2024, 130, 24)):
        rows += [(f"{year}Q{q}", sales, visits) for q in (1, 2, 3, 4)]
    rows += [("2025Q1", 140, 20), ("2025Q2", 150, 21)]
    return pd.DataFrame(rows, columns=["quarter_id", "net_sales_usd", "iqvia_zilretta_visits"])


# ------------------------------------------------------------------ sales against visits


def test_only_complete_years_are_summed_and_compared_with_the_year_before():
    years = context.annual(_company())
    assert list(years["year"]) == [2021, 2022, 2023, 2024]  # 2020 and 2025 are part years
    assert years["net_sales_usd"].tolist() == [400, 440, 480, 520]
    assert years["visits"].tolist() == [160, 176, 144, 96]
    assert pd.isna(years["sales_change_pct"].iloc[0])
    assert years["sales_change_pct"].iloc[1:].tolist() == pytest.approx(
        [10.0, 100 * 40 / 440, 100 * 40 / 480]
    )
    assert years["visits_change_pct"].iloc[1:].tolist() == pytest.approx(
        [10.0, 100 * -32 / 176, 100 * -48 / 144]
    )


def test_the_headline_compares_the_latest_year_with_the_base_year_by_hand():
    head = context.headline(context.annual(_company()))
    assert head["year"] == 2024 and head["base_year"] == 2021
    assert head["sales_vs_base_pct"] == pytest.approx(30.0)  # 520 against 400
    assert head["visits_vs_base_pct"] == pytest.approx(-40.0)  # 96 against 160
    assert head["sales_change_pct"] == pytest.approx(100 * 40 / 480)


def test_each_quarter_is_indexed_to_the_average_quarter_of_the_base_year():
    indexed = context.indexed_quarters(_company())
    sales = indexed[indexed["series"] == "Company net sales"].set_index("quarter_id")["index"]
    visits = indexed[indexed["series"] == "IQVIA Zilretta visits"].set_index("quarter_id")["index"]
    assert sales["2021Q3"] == pytest.approx(100.0) and sales["2024Q1"] == pytest.approx(130.0)
    assert visits["2021Q1"] == pytest.approx(100.0) and visits["2024Q4"] == pytest.approx(60.0)
    assert sales["2020Q4"] == pytest.approx(90.0)  # part years are drawn, only not summed
    assert indexed.loc[indexed["quarter_id"] == "2022Q2", "date"].iloc[0] == pd.Timestamp(
        "2022-04-01"
    )


def test_a_base_year_without_four_quarters_is_refused():
    with pytest.raises(ValueError, match="needs four quarters"):
        context.indexed_quarters(_company(), base_year=2025)


def test_the_decline_caveat_is_the_stored_findings_own_wording():
    items = [{"id": 2, "impact": "other"}, {"id": 1, "impact": "The stored words."}]
    assert context.decline_caveat(items) == "The stored words."
    fallback = context.decline_caveat([{"id": 2, "impact": "other"}])
    assert "must not be quoted as a market fact" in fallback


def test_the_annual_download_has_the_year_the_sales_and_the_visits():
    lines = context.csv_sales_visits(context.annual(_company())).decode().strip().splitlines()
    assert lines[0] == "year,net_sales_usd,iqvia_zilretta_visits,sales_change_pct,visits_change_pct"
    assert lines[1] == "2021,400,160,," and lines[2].startswith("2022,440,176,10.0,10.0")


# ------------------------------------------------------------------ the gap checks


def _verdicts():
    rows = [
        # an older run that the latest must replace
        ("run1", "D1", "verdict", None, "supported"),
        ("run2", "D1", "gap_65_vs_medicare", -0.325, "not_supported"),
        ("run2", "D1", "verdict", None, "not_supported"),
        ("run2", "D2", "facility_share_shift", 0.00038, "not_supported"),
        ("run2", "D3", "sales_per_visit_change", 0.94, "supported"),
        ("run2", "D3", "services_per_patient_change", 0.152, "supported"),
        ("run2", "D3", "payment_per_service_change", -0.042, "not_supported"),
        ("run2", "D3", "verdict", None, "supported"),
        ("run2", "D4", "top2_share_of_fall", 0.642, "supported"),
        ("run2", "GAP", "any_of_D1_D3", None, "supported"),
    ]
    frame = pd.DataFrame(rows, columns=["run_id", "check_id", "metric", "value", "verdict"])
    frame["threshold"] = None
    return frame


def test_the_four_checks_are_read_from_the_latest_run_in_plain_words():
    checks = {c["check"]: c for c in context.gap_checks(_verdicts())}
    assert list(checks) == ["D1", "D2", "D3", "D4"]
    assert checks["D1"]["result"] == "Not supported"  # run2's, not run1's "supported"
    assert (
        checks["D1"]["number"]
        == "visits for ages 65+ and Medicare patients part by 32.5 points (15 allowed)"
    )
    assert checks["D2"]["result"] == "Not supported" and "0.04 points" in checks["D2"]["number"]
    assert checks["D3"]["result"] == "Supported on direction only"
    assert (
        checks["D3"]["number"] == "sales per IQVIA visit +94%, but value per Medicare patient +10%"
    )
    assert checks["D4"]["result"] == "Concentrated" and "64% of the fall" in checks["D4"]["number"]


def test_a_check_with_no_stored_row_is_left_out_and_a_missing_figure_reads_na():
    verdicts = _verdicts()
    verdicts = verdicts[verdicts["check_id"] != "D2"]
    assert [c["check"] for c in context.gap_checks(verdicts)] == ["D1", "D3", "D4"]
    no_value = _verdicts().assign(value=None)
    assert {c["number"] for c in context.gap_checks(no_value)} == {"n/a"}


def test_the_overall_verdict_says_no_check_accounts_for_the_size():
    assert "none of the four accounts for the size of the gap" in context.overall_gap(_verdicts())
    assert context.overall_gap(_verdicts().assign(verdict="not_supported")) == "No check holds."
    assert context.overall_gap(_verdicts()[lambda f: f["check_id"] != "GAP"]) == ""


# ------------------------------------------------------------------ Medicare adoption by state


def _states():
    rows = []
    for year, rates in ((2022, (0.01, 0.02, 0.03)), (2024, (0.011, 0.021, 0.031))):
        for code, name, visible, rate in zip(
            ("AK", "AL", "AR"),
            ("Alaska", "Alabama", "Arkansas"),
            (50, 400, 800),
            rates,
            strict=True,
        ):
            rows.append(
                dict(
                    year=year,
                    state_code=code,
                    state_name=name,
                    n_visible_providers=visible,
                    n_zilretta_providers=round(visible * rate),
                    adoption_rate=rate,
                    ci_low=rate - 0.005,
                    ci_high=rate + 0.005,
                    reported=1,
                    headroom=visible * (0.025 - rate),
                )
            )
    return pd.DataFrame(rows)


def test_national_adoption_is_pooled_over_the_states_each_year():
    nat = context.national_adoption(_states())
    # 2022: (0.5 + 8 + 24) / 1250 providers
    assert nat["pct"].iloc[0] == pytest.approx(100 * (round(50 * 0.01) + 8 + 24) / 1250, abs=0.2)
    assert list(nat["year"]) == [2022, 2024]


def test_a_states_year_has_its_rate_interval_size_flag_and_tile_position():
    view = context.state_year(_states(), 2024)
    assert list(view["state_code"]) == ["AK", "AL", "AR"]
    ak = view.iloc[0]
    assert ak["adoption_pct"] == pytest.approx(1.1) and ak["ci_low_pct"] == pytest.approx(0.6)
    assert ak["ci_high_pct"] == pytest.approx(1.6)
    assert view["small"].tolist() == [True, False, False]  # 50 providers is under 100
    assert (ak["row"], ak["col"]) == (0, 0)


def test_the_states_ranking_keeps_its_order_or_it_does_not():
    assert context.rank_stability(_states()) == pytest.approx(1.0)
    flipped = _states()
    flipped.loc[flipped["year"] == 2024, "adoption_rate"] = [0.031, 0.021, 0.011]
    assert context.rank_stability(flipped) == pytest.approx(-1.0)


def test_the_states_furthest_below_national_are_the_largest_positive_headroom_first():
    view = context.state_year(_states(), 2024)
    below = context.below_national(view, n=5)
    # headroom 50 x 0.014 = 0.7, 400 x 0.004 = 1.6, 800 x -0.006 = -4.8 (above national: left out)
    assert list(below["state_code"]) == ["AL", "AK"]
    assert list(context.below_national(view, n=1)["state_code"]) == ["AL"]
    unreported = view.assign(reported=0)
    assert context.below_national(unreported).empty


def test_every_state_and_dc_has_exactly_one_tile_inside_the_grid():
    assert len(context.TILE_GRID) == 51
    assert len(set(context.TILE_GRID.values())) == 51
    assert all(0 <= r <= 7 and 0 <= c <= 11 for r, c in context.TILE_GRID.values())
    assert context.tiles_complete({"AK", "DC", "TX"}) and not context.tiles_complete({"PR"})


# ------------------------------------------------------------------ the events


def test_events_are_oldest_first_with_readable_dates_and_an_unverified_flag():
    events = pd.DataFrame(
        {
            "event_id": [2, 1],
            "event_date": ["2018-04-01", "2017-10-06"],
            "event_end_date": ["2021-03-31", None],
            "event_type": ["payment", "regulatory"],
            "description": ["Pass-through status", "FDA approval"],
            "source": ["CMS", "FDA letter"],
            "verified": [0, 1],
        }
    )
    rows = context.event_rows(events)
    assert [r["date"] for r in rows] == ["6 Oct 2017", "1 Apr 2018 to 31 Mar 2021"]
    assert [r["type"] for r in rows] == ["regulatory", "payment"]
    assert [r["verified"] for r in rows] == [True, False]
    assert rows[0]["description"] == "FDA approval" and rows[1]["source"] == "CMS"
    assert (
        context.event_rows(events.assign(event_type="public_health"))[0]["type"] == "public health"
    )


def test_the_timeline_marks_only_the_unverified_events_and_escapes_text():
    html = style.timeline_rows(
        [
            {
                "date": "1 Jan 2020",
                "type": "data",
                "description": "<b>x</b>",
                "source": "a & b",
                "verified": True,
            },
            {
                "date": "2 Jan 2020",
                "type": "data",
                "description": "y",
                "source": "s",
                "verified": False,
            },
        ]
    )
    assert html.count('class="oa-tl"') == 2 and html.count("not verified against its source") == 1
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html and "a &amp; b" in html
    assert style.timeline_rows([]) == ""


# ------------------------------------------------------------------ against the public-data subset


@pytest.fixture(scope="module")
def subset():
    if not data.EXTERNAL_DB_PATH.exists():
        pytest.skip("no public-data subset")
    engine = data.open_engine(data.EXTERNAL_DB_PATH)
    try:
        yield data.external_context(engine)
    finally:
        engine.dispose()


def test_the_real_subset_gives_the_published_annual_changes(subset):
    years = context.annual(subset["company"])
    assert list(years["year"]) == [2021, 2022, 2023, 2024]
    assert years["sales_change_pct"].iloc[1:].round(1).tolist() == [2.7, 5.3, 6.3]
    assert years["visits_change_pct"].iloc[1:].round(1).tolist() == [6.7, -13.5, -35.9]
    head = context.headline(years)
    assert round(head["sales_vs_base_pct"]) == 15 and round(head["visits_vs_base_pct"]) == -41


def test_the_real_medicare_adoption_matches_the_stored_results(subset):
    nat = context.national_adoption(subset["states"])
    assert nat["pct"].round(2).tolist() == [1.08, 1.52, 1.78, 1.73, 1.58, 1.47]
    rho = context.rank_stability(subset["states"])
    stored = subset["verdicts"][subset["verdicts"]["metric"] == "rank_rho_2022_2024"]["value"].iloc[
        0
    ]
    assert rho == pytest.approx(stored) and round(rho, 2) == 0.92
    view = context.state_year(subset["states"], 2024)
    assert len(view) == 51 and context.tiles_complete(set(view["state_code"]))


def test_the_real_gap_checks_say_what_the_documents_say(subset):
    checks = {c["check"]: c for c in context.gap_checks(subset["verdicts"])}
    assert [checks[k]["verdict"] for k in ("D1", "D2", "D3", "D4")] == [
        "not_supported",
        "not_supported",
        "supported",
        "supported",
    ]
    assert "value per Medicare patient +10%" in checks["D3"]["number"]
    assert "none of the four accounts for the size of the gap" in context.overall_gap(
        subset["verdicts"]
    )


def test_the_real_events_are_fourteen_and_two_are_not_verified(subset):
    rows = context.event_rows(subset["events"])
    assert len(rows) == 14 and sum(not r["verified"] for r in rows) == 2
    assert rows[0]["date"] == "6 Oct 2017"


# ------------------------------------------------------------------ the charts


def _marks(spec):
    """The mark types of a layered chart, in order, however the layers are nested."""
    if "layer" in spec:
        return [m for layer in spec["layer"] for m in _marks(layer)]
    return [spec["mark"]["type"]]


def test_the_company_chart_has_a_base_line_the_two_series_and_points():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    spec = charts.company_visits_chart(context.indexed_quarters(_company())).to_dict()
    assert _marks(spec) == ["rule", "line", "circle"]


def test_the_state_map_has_a_tile_and_a_label_for_each_state():
    pytest.importorskip("altair")
    from oa_market_intelligence.serving import charts

    spec = charts.state_tile_map(context.state_year(_states(), 2024)).to_dict()
    assert [layer["mark"]["type"] for layer in spec["layer"]] == ["rect", "text"]
