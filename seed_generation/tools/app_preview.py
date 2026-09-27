#!/usr/bin/env python3
"""Render one entry from a root-level Devocional_year JSON file as HTML.

The markup mirrors ``DevocionalesContentWidget`` in devocional_nuevo: its
header, copyable verse cards, reflection, meditation cards, prayer, and
details block.  This is intentionally a preview of the app's content region,
not a replacement for Flutter's navigation, TTS, notes, or sharing flows.

Usage:
    python3 devocionales_scripts/app_preview.py Devocional_year_2026_es_NVI.json
    python3 devocionales_scripts/app_preview.py Devocional_year_2026_es_NVI.json --date 2026-08-01
"""

import argparse
import json
import re
import sys
import webbrowser
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared_preview.markdown import escape, render_emphasis_markdown  # noqa: E402
from shared_preview.unrendered import TrackedDict, find_unrendered_keys  # noqa: E402


DART_MODEL_REL = "lib/models/devocional_model.dart"
DART_CONTENT_REL = "lib/widgets/devocionales/devocionales_content_widget.dart"

CSS = """
:root { --bg:#141218; --surface:#201f24; --text:#e8e0e9; --muted:#cac1cc;
  --primary:#d0bcff; --primary-soft:rgba(208,188,255,.14); --border:rgba(208,188,255,.32); --shadow:rgba(0,0,0,.35); }
html[data-theme="light"] { --bg:#f8f7fb; --surface:#ffffff; --text:#1d1b20; --muted:#655f6f;
  --primary:#6850a4; --primary-soft:rgba(104,80,164,.12); --border:rgba(104,80,164,.3); --shadow:rgba(30,20,45,.12); }
* { box-sizing:border-box; } body { margin:0; padding:24px 16px 48px; background:var(--bg); color:var(--text); font-family:Roboto,Arial,sans-serif; }
.toolbar { max-width:680px; margin:0 auto 14px; display:flex; justify-content:flex-end; gap:8px; }
button { border:1px solid var(--border); border-radius:12px; padding:8px 12px; background:var(--surface); color:var(--primary); font:600 14px inherit; cursor:pointer; }
.page { max-width:680px; margin:auto; padding:16px; background:var(--surface); border-radius:20px; box-shadow:0 8px 28px var(--shadow); }
.header { display:flex; align-items:center; gap:8px; padding-bottom:16px; }.streak { width:48px; }.date { flex:1; color:var(--primary); font-size:16px; font-weight:700; text-align:center; }.actions { display:flex; gap:8px; }.action { width:40px; height:40px; padding:0; font-size:21px; background:var(--primary-soft); }
.verse-card { position:relative; padding:12px 40px 12px 12px; border:1.5px solid var(--border); border-radius:20px; background:linear-gradient(135deg,var(--primary-soft),transparent); box-shadow:0 8px 20px var(--shadow); color:var(--text); font-size:20px; font-weight:600; line-height:1.45; text-align:center; }.copy { position:absolute; top:10px; right:12px; color:var(--primary); font-size:18px; }
h2 { margin:20px 0 10px; color:var(--primary); font-size:21px; } .body { font-size:16px; line-height:1.65; white-space:pre-line; }.meditate { margin:8px 0; font-size:16px; text-align:left; }.citation { color:var(--primary); font-weight:700; }.details { margin-top:20px; }.details h2 { margin-bottom:10px; }.detail { color:var(--text); font-size:14px; margin:5px 0; }.copyright { color:var(--muted); font-size:12px; text-align:center; margin:18px 20px 0; }.notice { max-width:680px; margin:0 auto 14px; padding:12px 16px; background:#fff3cd; color:#785b00; border:1px solid #d8ad37; border-radius:12px; }.meta { max-width:680px; margin:0 auto 10px; color:var(--muted); font:600 12px monospace; text-align:center; }
"""

THEME_SCRIPT = """
(function () {
  var key = 'devocionalYearPreviewTheme';
  if (localStorage.getItem(key) === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  }
  document.addEventListener('DOMContentLoaded', function () {
    var button = document.getElementById('theme');
    function updateLabel() {
      button.textContent = document.documentElement.getAttribute('data-theme') === 'light'
        ? 'Dark mode' : 'Light mode';
    }
    updateLabel();
    button.addEventListener('click', function () {
      var light = document.documentElement.getAttribute('data-theme') === 'light';
      if (light) {
        document.documentElement.removeAttribute('data-theme');
        localStorage.setItem(key, 'dark');
      } else {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem(key, 'light');
      }
      updateLabel();
    });
  });
})();
"""


def pick_entry(payload: dict, requested_date: str | None) -> tuple[dict, str]:
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise ValueError("Expected root object with a non-empty 'data' map.")
    language, days = next(iter(data.items()))
    if not isinstance(days, dict) or not days:
        raise ValueError(f"Expected non-empty date map at data.{language}.")
    selected = requested_date or (date.today().isoformat() if date.today().isoformat() in days else sorted(days)[0])
    entries = days.get(selected)
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        available = f"Available range: {min(days)} to {max(days)}."
        raise ValueError(f"No devotional found for {selected}. {available}")
    return entries[0], selected


def dart_contract(dart_repo: Path) -> tuple[set[str], set[str], list[str]]:
    """Read the JSON/model and content-field contract from the app itself.

    This gives the preview a live drift check, equivalent to the existing
    discovery and encounters preview scripts.  It deliberately reports model
    fields that the content widget does not display rather than treating them
    as harmless metadata.
    """
    model = dart_repo / DART_MODEL_REL
    content = dart_repo / DART_CONTENT_REL
    warnings = []
    if not model.exists() or not content.exists():
        warnings.append(
            f"Could not read the live Dart contract at {dart_repo}; pass --dart-repo to enable it."
        )
        return set(), set(), warnings
    model_source = model.read_text(encoding="utf-8")
    content_source = content.read_text(encoding="utf-8")
    model_fields = set(re.findall(r"json\['([^']+)'\]", model_source))
    content_fields = set(re.findall(r"devocional\.(\w+)", content_source))
    dart_to_json = {
        "paraMeditar": "para_meditar",
        "imageUrl": "imageUrl",
        "id": "id", "versiculo": "versiculo", "reflexion": "reflexion",
        "oracion": "oracion", "date": "date", "version": "version",
        "language": "language", "tags": "tags", "emoji": "emoji",
    }
    rendered_json = {dart_to_json[field] for field in content_fields if field in dart_to_json}
    return model_fields, rendered_json, warnings


def render_entry(entry: TrackedDict, selected_date: str) -> str:
    verse = escape(entry.get("versiculo", ""))
    reflection = render_emphasis_markdown(entry.get("reflexion", ""))
    prayer = render_emphasis_markdown(entry.get("oracion", ""))
    meditate = []
    for item in entry.get("para_meditar", []):
        if isinstance(item, dict):
            meditate.append('<div class="verse-card meditate"><span class="citation">%s: </span>%s<span class="copy">⧉</span></div>' % (escape(item.get("cita", "")), escape(item.get("texto", ""))))
    tags = entry.get("tags") or []
    language = entry.get("language")
    # The app's header gets its displayed date from the selected Devocional.
    entry_date = entry.get("date", selected_date)
    details = []
    if tags:
        details.append(f'<div class="detail">Topics: {escape(", ".join(map(str, tags)))}</div>')
    if entry.get("version"):
        details.append(f'<div class="detail">Version: {escape(entry["version"])}</div>')
    details_html = ''
    # This condition mirrors DevocionalesContentWidget exactly: language
    # causes the Details region to exist even though it has no separate text.
    if details or language is not None:
        details_html = '<section class="details"><h2>Details</h2>' + ''.join(details) + '<div class="copyright">Bible text copyright belongs to its respective publisher.</div></section>'
    return f'''<div class="meta">{escape(entry_date)} · {escape(entry.get("id", ""))}</div>
<main class="page"><header class="header"><span class="streak"></span><div class="date">{escape(entry_date)}</div><div class="actions"><button class="action" title="Favorite">♡</button><button class="action" title="Share">↗</button></div></header>
<div class="verse-card">{verse}<span class="copy">⧉</span></div>
<section><h2>Reflection</h2><div class="body">{reflection}</div></section>
<section><h2>To meditate</h2>{''.join(meditate)}</section>
<section><h2>Prayer</h2><div class="body">{prayer}</div></section>{details_html}</main>'''


def build_html(entry: TrackedDict, selected_date: str, warnings: list[str]) -> str:
    warning_html = ''.join(f'<div class="notice">{escape(w)}</div>' for w in warnings)
    return f'''<!doctype html><html lang="{escape(entry.get("language", ""))}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Devotional preview · {escape(selected_date)}</title><style>{CSS}</style></head><body><div class="toolbar"><button id="theme">Light mode</button></div>{warning_html}{render_entry(entry, selected_date)}<script>{THEME_SCRIPT}</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", help="Root-level Devocional_year JSON file")
    parser.add_argument("--date", help="Entry date (YYYY-MM-DD); defaults to today if present, otherwise the file's first date")
    parser.add_argument("--out", help="Output HTML path (default: <json file>.preview.html)")
    parser.add_argument(
        "--dart-repo",
        default=None,
        help="Path to a local devocional_nuevo checkout (default: ~/Projects/devocional_nuevo)",
    )
    parser.add_argument("--no-open", action="store_true", help="Do not open the generated preview")
    args = parser.parse_args()
    source = Path(args.json_file)
    payload = json.loads(source.read_text(encoding="utf-8"))
    raw_entry, selected_date = pick_entry(payload, args.date)
    entry = TrackedDict(raw_entry)
    dart_repo = Path(args.dart_repo) if args.dart_repo else Path.home() / "Projects" / "devocional_nuevo"
    model_fields, dart_rendered, warnings = dart_contract(dart_repo)
    # Render first: TrackedDict now tells us precisely which JSON properties
    # were consumed by the HTML. Every populated property it did not consume
    # becomes a visible warning in the generated preview.
    content_html = render_entry(entry, selected_date)
    unrendered = find_unrendered_keys(entry)
    # ``para_meditar`` has its own JSON shape. Track each item as well so a
    # newly added field there cannot disappear without a visible warning.
    for index, item in enumerate(entry.get("para_meditar", [])):
        if isinstance(item, TrackedDict):
            unrendered.extend(
                f"para_meditar[{index}].{key}" for key in find_unrendered_keys(item)
            )
    if unrendered:
        warnings.append("Preview does not render populated field(s): " + ", ".join(unrendered))
    if model_fields:
        preview_fields = entry.accessed_keys
        if dart_rendered - preview_fields:
            warnings.append("Dart content field(s) not consumed by this preview: " + ", ".join(sorted(dart_rendered - preview_fields)))
    # Keep the tracked rendering result; build_html only wraps it with page UI.
    warning_html = ''.join(f'<div class="notice">{escape(w)}</div>' for w in warnings)
    html = f'''<!doctype html><html lang="{escape(entry.get("language", ""))}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Devotional preview · {escape(selected_date)}</title><style>{CSS}</style></head><body><div class="toolbar"><button id="theme">Light mode</button></div>{warning_html}{content_html}<script>{THEME_SCRIPT}</script></body></html>'''
    out = Path(args.out) if args.out else source.with_suffix(".preview.html")
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
