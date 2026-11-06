"""The public site: Zilretta visit-share intelligence from IQVIA NMTA data (DL-85).

Run locally:  streamlit run app/streamlit_app.py

This file is only the frame: the page setup, the look, the navigation on the right and the one
filter bar. Each page is a few lines in `app/views/` over a tab module and a tested module in
`oa_market_intelligence.serving`, which hold the logic and the wording. The site reads the
published warehouse read-only, shows aggregates only and fits nothing.
"""

from __future__ import annotations

import common  # first: it puts src/ on the path
import streamlit as st

from oa_market_intelligence.serving import overview, style

st.set_page_config(page_title="OA and RA Market Intelligence", page_icon="📈", layout="wide")
st.markdown(style.CSS, unsafe_allow_html=True)  # the look, added once for every page

pages = [
    st.Page(script, title=title, icon=icon, url_path=slug, default=title == "Overview")
    for title, script, icon, slug in overview.PAGES
]
page = st.navigation(pages, position="sidebar")
common.init_from_url(page.title)  # a link carries what was clicked in the address

st.title("OA and RA Market Intelligence")
st.caption(
    "Zilretta's share of visits against generic steroid injections and NSAIDs, from IQVIA NMTA "
    "visit counts (aggregated; no patient-level data). Built for an OA and RA injectable brand manager."
)
if page.title in common.FILTERED_PAGES:
    common.filter_bar(page.title)
    common.sync_url(page.title)  # the address always shows the filters, so it can be shared
common.back_link(page.title)
page.run()
