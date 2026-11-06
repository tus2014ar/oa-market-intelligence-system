"""The site's look, as tested functions: one block of CSS and small builders for the cards, bars and
ranked lists the redesigned tabs share (DL-97).

Everything that comes from data is escaped before it goes into HTML. The cards are a fixed navy
with white text and the bars use the site's teal on a faint track, so both read in light and dark
mode; green, red and grey for a change always come with a sign or a word.
"""

from __future__ import annotations

from html import escape

NAVY, TEAL, GREEN, RED, GREY = "#1B2A41", "#0F8B8D", "#1E9E5A", "#C8553D", "#7A8794"

CSS = f"""<style>
.oa-kpi {{background:{NAVY};color:#fff;border-radius:14px;padding:16px 20px;min-height:152px}}
@media (max-width:640px) {{.oa-kpi, .oa-kpi.tall {{min-height:0}}}}
.oa-kpi.tall {{min-height:260px}}
.oa-kpi .l {{font-size:.72rem;letter-spacing:.07em;text-transform:uppercase;opacity:.78}}
.oa-kpi .v {{font-size:2rem;font-weight:700;line-height:1.2;margin:.15rem 0}}
.oa-kpi .c {{font-size:.8rem;opacity:.82;line-height:1.35}}
.oa-pill {{display:inline-block;border-radius:999px;padding:1px 9px;font-size:.78rem;
  font-weight:600;margin-left:.4rem;vertical-align:middle}}
.oa-pill.up {{background:rgba(30,158,90,.22);color:#6fe0a3}}
.oa-pill.down {{background:rgba(200,85,61,.28);color:#ffab98}}
.oa-pill.flat {{background:rgba(255,255,255,.16);color:#fff}}
.oa-row {{display:grid;grid-template-columns:1fr auto;gap:.15rem .6rem;align-items:baseline;
  margin:.55rem 0;font-size:.9rem}}
.oa-track {{grid-column:1 / -1;display:block;background:rgba(127,127,127,.2);border-radius:6px;
  height:10px;overflow:hidden}}
.oa-fill {{display:block;background:{TEAL};height:10px;border-radius:6px}}
.oa-val {{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}
.oa-rank {{display:grid;grid-template-columns:minmax(110px,2fr) auto auto;gap:.8rem;
  align-items:center;padding:.42rem 0;font-size:.9rem;
  border-bottom:1px solid rgba(127,127,127,.18)}}
.oa-rank.head {{font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;opacity:.65}}
.oa-rank .n {{text-align:right;font-variant-numeric:tabular-nums}}
.oa-up {{color:{GREEN};font-weight:600}} .oa-down {{color:{RED};font-weight:600}}
.oa-flat {{color:{GREY};font-weight:600}}
.oa-call {{background:rgba(15,139,141,.13);border-radius:8px;padding:.45rem .75rem;
  font-size:.84rem;margin-top:.55rem}}
.oa-call.down {{background:rgba(200,85,61,.13)}}
.oa-test {{padding:.6rem 0;border-bottom:1px solid rgba(127,127,127,.18)}}
.oa-test-head {{display:flex;justify-content:space-between;gap:.6rem;font-weight:600}}
.oa-test-sub {{font-weight:400;opacity:.7}}
.oa-pill2 {{border-radius:999px;padding:1px 10px;font-size:.78rem;font-weight:600;
  white-space:nowrap;background:rgba(127,127,127,.2)}}
.oa-pill2.pass {{background:rgba(30,158,90,.18);color:{GREEN}}}
.oa-test-res {{font-size:.88rem;font-variant-numeric:tabular-nums;margin-top:.15rem}}
.oa-test-plain {{font-size:.84rem;opacity:.8;margin-top:.1rem}}
.oa-kpi .v.t {{font-size:1.1rem;line-height:1.3;margin:.3rem 0 .4rem}}
.oa-find {{padding:.8rem 0;border-bottom:1px solid rgba(127,127,127,.18)}}
.oa-find-head {{display:flex;gap:.7rem;align-items:center}}
.oa-find-id {{flex:none;width:1.7rem;height:1.7rem;border-radius:50%;background:{TEAL};
  color:#fff;text-align:center;line-height:1.7rem;font-size:.8rem;font-weight:700}}
.oa-find-title {{font-weight:700}}
.oa-find-impact {{font-size:.9rem;margin:.4rem 0 .25rem 2.4rem}}
.oa-find details {{margin-left:2.4rem;font-size:.85rem}}
.oa-find summary {{cursor:pointer;opacity:.75}}
.oa-find details div {{margin-top:.3rem;opacity:.9}}
.oa-stand {{display:flex;justify-content:space-between;align-items:baseline;
  padding:.4rem 0;border-bottom:1px solid rgba(127,127,127,.18)}}
.oa-stand .k {{font-size:.88rem;opacity:.8}} .oa-stand .x {{font-weight:700;font-size:1.05rem}}
</style>"""


def tone(delta: float | None, flat: float = 0.005) -> str:
    """'up', 'down' or 'flat' for a change; None counts as flat."""
    if delta is None or abs(delta) < flat:
        return "flat"
    return "up" if delta > 0 else "down"


def signed(value: float | None, unit: str = " pp", digits: int = 2) -> str:
    """A change with its sign, or an en dash when there is none."""
    return "–" if value is None else f"{value:+.{digits}f}{unit}"


def kpi_card(
    label: str, value: str, caption: str = "", delta: float | None = None, pill: str = ""
) -> str:
    """A navy card: a small label, one big number, an optional change pill and a caption."""
    chip = f'<span class="oa-pill {tone(delta)}">{escape(pill)}</span>' if pill else ""
    return (
        f'<div class="oa-kpi"><div class="l">{escape(label)}</div>'
        f'<div class="v">{escape(value)}{chip}</div><div class="c">{escape(caption)}</div></div>'
    )


def bar_rows(rows: list[tuple[str, float, str]], scale_to: float | None = None) -> str:
    """Horizontal bars: (label, value, text shown at the right). The bar widths are relative to
    `scale_to` (or the largest value), so a long list stays comparable."""
    top = scale_to or max((v for _, v, _ in rows), default=0.0) or 1.0
    out = []
    for label, value, text in rows:
        width = max(0.0, min(100.0, 100 * value / top))
        out.append(
            f'<div class="oa-row"><span>{escape(label)}</span>'
            f'<span class="oa-val">{escape(text)}</span>'
            '<span class="oa-track">'
            f'<span class="oa-fill" style="width:{width:.1f}%"></span></span></div>'
        )
    return "".join(out)


def rank_rows(rows: list[tuple[str, str, float | None, str]], headers: tuple[str, str, str]) -> str:
    """A ranked list: (name, a number, a change, the change as text), with a header row. The change
    is coloured by its sign and always shows the sign as text."""
    out = [
        f'<div class="oa-rank head"><span>{escape(headers[0])}</span>'
        f'<span class="n">{escape(headers[1])}</span>'
        f'<span class="n">{escape(headers[2])}</span></div>'
    ]
    for name, number, delta, text in rows:
        out.append(
            f'<div class="oa-rank"><span>{escape(name)}</span>'
            f'<span class="n">{escape(number)}</span>'
            f'<span class="n oa-{tone(delta)}">{escape(text)}</span></div>'
        )
    return "".join(out)


def callout(text: str, kind: str = "up") -> str:
    return f'<div class="oa-call{" down" if kind == "down" else ""}">{escape(text)}</div>'


def test_rows(rows: list[tuple[str, str, str, bool, str, str]]) -> str:
    """A list of tests: (name, what it is, the verdict pill, passed, the result, a plain reading).
    A pass is green and a non-pass grey, and each pill carries its words."""
    out = []
    for name, sub, pill, passed, result, plain in rows:
        kind = "pass" if passed else "no"
        out.append(
            '<div class="oa-test"><div class="oa-test-head">'
            f'<span>{escape(name)}<span class="oa-test-sub"> · {escape(sub)}</span></span>'
            f'<span class="oa-pill2 {kind}">{escape(pill)}</span></div>'
            f'<div class="oa-test-res">{escape(result)}</div>'
            f'<div class="oa-test-plain">{escape(plain)}</div></div>'
        )
    return "".join(out)


def text_card(label: str, title: str, caption: str = "") -> str:
    """A navy card whose main line is a sentence (a finding's title) rather than a number."""
    return (
        f'<div class="oa-kpi tall"><div class="l">{escape(label)}</div>'
        f'<div class="v t">{escape(title)}</div><div class="c">{escape(caption)}</div></div>'
    )


def finding_card(finding_id: int, title: str, impact: str, evidence: str, source: str) -> str:
    """One finding: its number and title, what it means, and the evidence folded away under a
    heading that names the source."""
    return (
        '<div class="oa-find"><div class="oa-find-head">'
        f'<span class="oa-find-id">{int(finding_id)}</span>'
        f'<span class="oa-find-title">{escape(title)}</span></div>'
        f'<div class="oa-find-impact"><b>What it means:</b> {escape(impact)}</div>'
        f"<details><summary>Evidence · {escape(source)}</summary>"
        f"<div>{escape(evidence)}</div></details></div>"
    )


def stand_rows(rows: list[tuple[str, str]]) -> str:
    """Two-column facts: a plain label on the left, the figure on the right."""
    return "".join(
        f'<div class="oa-stand"><span class="k">{escape(k)}</span>'
        f'<span class="x">{escape(v)}</span></div>'
        for k, v in rows
    )
