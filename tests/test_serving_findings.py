"""Tests for the Findings tab's logic (serving/findings.py) and its HTML (serving/style.py): every
finding has exactly one theme, nothing is lost by a grouping or a filter, the four to read first,
the CSV and the escaping, on small inputs and on the real findings. No Streamlit needed."""

import pytest

from oa_market_intelligence.serving import data, findings, style


def _items(ids=range(1, 18)):
    return [
        {
            "id": i,
            "finding": f"Finding {i}",
            "evidence": f"Evidence {i}",
            "impact": f"Impact {i}",
            "source": "analyses" if i <= 12 else "models",
        }
        for i in ids
    ]


# ------------------------------------------------------------------ the themes


def test_every_finding_id_belongs_to_exactly_one_theme():
    listed = [i for spec in findings.THEMES.values() for i in spec["ids"]]
    assert sorted(listed) == list(range(1, 18)) and len(listed) == len(set(listed))
    assert findings.theme_of(1) == "The gap" and findings.theme_of(16) == "The models"
    assert findings.theme_of(99) is None


def test_the_themes_come_in_their_order_with_their_ids_in_their_order():
    groups = findings.themed(_items())
    assert [name for name, _ in groups] == list(findings.THEMES)
    assert [[x["id"] for x in group] for _, group in groups] == [
        [1, 2, 3],
        [4, 5, 6],
        [7, 8, 10, 11],
        [9, 12, 17],
        [13, 14, 15, 16],
    ]


def test_a_theme_filter_keeps_only_that_theme_and_all_keeps_everything():
    one = findings.themed(_items(), "Possible causes")
    assert [name for name, _ in one] == ["Possible causes"]
    assert [x["id"] for x in one[0][1]] == [7, 8, 10, 11]
    assert sum(len(g) for _, g in findings.themed(_items())) == 17
    assert findings.themed(_items(), "No such theme") == []


def test_a_finding_with_no_theme_is_not_lost():
    groups = findings.themed(_items([1, 2, 3, 99]))
    assert groups[-1][0] == "Other" and [x["id"] for x in groups[-1][1]] == [99]
    assert [name for name, _ in findings.themed(_items([1, 99]), "Other")] == ["Other"]
    assert all(name != "Other" for name, _ in findings.themed(_items([1, 2])))


def test_a_missing_finding_leaves_the_rest_of_its_theme():
    group = findings.themed(_items([13, 15]), "The models")[0][1]
    assert [x["id"] for x in group] == [13, 15]


def test_the_four_to_read_first_are_in_the_stated_order():
    assert findings.START_HERE == (1, 2, 14, 16)
    assert [x["id"] for x in findings.start_here(_items())] == [1, 2, 14, 16]
    assert [x["id"] for x in findings.start_here(_items([2, 16, 5]))] == [2, 16]


def test_the_models_theme_points_to_round_2_without_adopting_it():
    note = findings.ROUND2_NOTE
    assert "provisional" in note and "not adopted" in note and "Model results" in note


def test_the_sources_are_counted():
    assert findings.counts(_items()) == {"analyses": 12, "models": 5}


def test_the_download_has_every_finding_with_its_theme():
    lines = findings.csv(_items()).decode().strip().splitlines()
    assert lines[0] == "id,theme,finding,evidence,impact,source" and len(lines) == 18
    assert lines[1] == "1,The gap between sales and visits,Finding 1,Evidence 1,Impact 1,analyses"
    assert findings.csv(_items([99])).decode().splitlines()[1].startswith("99,,")


# ------------------------------------------------------------------ the HTML


def test_a_finding_card_says_what_it_means_first_and_folds_the_evidence_away():
    html = style.finding_card(4, "Title", "The meaning", "The numbers", "the analyses")
    assert html.index("What it means:") < html.index("<details>")
    assert "<summary>Evidence · the analyses</summary>" in html and ">4</span>" in html
    assert "The meaning" in html and "The numbers" in html


def test_what_goes_into_a_finding_card_is_escaped():
    html = style.finding_card(1, "<b>t</b>", "<script>x</script>", "a & b", "<i>s</i>")
    assert "<b>t</b>" not in html and "<script>" not in html and "<i>s</i>" not in html
    assert "&lt;b&gt;" in html and "&amp;" in html
    card = style.text_card("<l>", "<t>", "<c>")
    assert "<t>" not in card and "&lt;t&gt;" in card and 'class="v t"' in card
    assert 'class="oa-kpi tall"' in card


# ------------------------------------------------------------------ the real findings


def test_the_real_findings_are_all_themed_and_all_shown_once():
    items = data.findings()
    assert len(items) == 17
    assert {findings.theme_of(int(x["id"])) for x in items} <= set(findings.THEMES)
    shown = [x["id"] for _, group in findings.themed(items) for x in group]
    assert sorted(shown) == sorted(x["id"] for x in items) and len(shown) == 17
    assert [x["id"] for x in findings.start_here(items)] == [1, 2, 14, 16]
    assert sum(findings.counts(items).values()) == 17


def test_the_real_start_here_findings_keep_the_decline_caveat_in_their_own_words():
    first = findings.start_here(data.findings())[0]
    assert "must not be quoted as a market fact" in first["impact"]
    assert "Ask IQVIA whether panel coverage changed" in first["impact"]


def test_every_real_finding_card_carries_its_text_unchanged():
    for item in data.findings():
        html = style.finding_card(
            item["id"], item["finding"], item["impact"], item["evidence"], item["source"]
        )
        for field in ("finding", "impact", "evidence"):
            assert style.escape(item[field]) in html


@pytest.mark.parametrize("theme", list(findings.THEMES))
def test_every_real_theme_has_findings_and_a_blurb(theme):
    spec = findings.THEMES[theme]
    assert spec["title"] and spec["blurb"] and len(spec["ids"]) >= 3
    group = findings.themed(data.findings(), theme)[0][1]
    assert len(group) == len(spec["ids"])
