"""The serving layer: what the website shows, built only from the published warehouse.

`data` reads the published tables read-only and returns small aggregate frames; `views` turns them
into the frames and sentences the page draws. All the logic (and all the wording of the headlines)
lives here and is tested; `app/streamlit_app.py` only lays it out. Nothing is fitted while a page
loads: the numbers are the ones the publish run stored (DL-81 to DL-84).
"""
