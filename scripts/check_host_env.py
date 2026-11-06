"""Run every page of the site in a clean environment that has only app/requirements.txt installed,
as the free host would, and report any exception (DL-113).

    python -m venv .hostvenv
    .hostvenv/Scripts/python -m pip install -r app/requirements.txt
    .hostvenv/Scripts/python scripts/check_host_env.py

Run it from the project folder. On Linux or Mac the interpreter is .hostvenv/bin/python.

It clears the key settings first, so the AI Question Box is checked in its switched-off view.
Exit code 0 means every page ran without an error."""

import os
import re
import sys
from pathlib import Path

os.environ.pop("PYTHONPATH", None)
for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ACCESS_CODE", "CLAUDE_MODEL"):
    os.environ.pop(name, None)

from streamlit.testing.v1 import AppTest  # noqa: E402

root = Path.cwd()
app = str(root / "app" / "streamlit_app.py")
overview_py = (root / "src" / "oa_market_intelligence" / "serving" / "overview.py").read_text(
    "utf-8"
)
pages = re.findall(r'\(\s*"([^"]+)",\s*"(views/[a-z_]+\.py)"', overview_py)
print("python", sys.version.split()[0], "| pages found:", len(pages))

at = AppTest.from_file(app, default_timeout=180)
at.run()
failures = 0
for title, script in pages:
    if title != "Overview":
        at.switch_page(script).run()
    errors = [e.value[:120] for e in at.exception]
    status = "ok" if not errors else "ERROR " + "; ".join(errors)
    failures += bool(errors)
    charts = len(at.get("vega_lite_chart"))
    print(f"  {title:16s} {status}  (markdown {len(at.markdown)}, charts {charts})")
print("FAILED PAGES:", failures)
sys.exit(1 if failures else 0)
