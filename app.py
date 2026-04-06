"""
PGA Teleprompter — web (Shinylive) edition.

Self-contained: reads all data from songs_bundle.json (no filesystem access).
Transpose logic inlined (no pga.transpose dependency).

Deploy: shinylive export apps/teleprompter-web site --subdir teleprompter
"""

import html
import json
import re
from pathlib import Path

from shiny import App, Inputs, Outputs, Session, reactive, render, ui

# ---------------------------------------------------------------------------
# Inlined transpose logic (from pga.transpose)
# ---------------------------------------------------------------------------

_SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_NOTE_TO_SEMI = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4, "E#": 5, "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11, "B#": 0,
}
_CHORD_ROOT_RE = re.compile(r"^([A-G][#b]?)(.*)")
_BRACKETED_RE = re.compile(r"\[([A-G][#b]?[^\]]*)\]")
_BARE_RE = re.compile(r"\b([A-G][#b]?(?:m|maj|min|dim|aug|sus|add|7|9|11|13|6|/[A-G][#b]?)*)\b")


def _transpose_chord(chord: str, semi: int, sharps: bool = True) -> str:
    root_m = _CHORD_ROOT_RE.match(chord)
    if not root_m or root_m.group(1) not in _NOTE_TO_SEMI:
        return chord
    root, quality = root_m.group(1), root_m.group(2)
    # Handle slash chords embedded in quality
    bass_note = None
    if "/" in quality:
        parts = quality.split("/", 1)
        quality = parts[0]
        bm = _CHORD_ROOT_RE.match(parts[1])
        if bm and bm.group(1) in _NOTE_TO_SEMI:
            bass_note = bm.group(1)
    scale = _SHARPS if sharps else _FLATS
    new_root = scale[(_NOTE_TO_SEMI[root] + semi) % 12]
    result = new_root + quality
    if bass_note:
        result += "/" + scale[(_NOTE_TO_SEMI[bass_note] + semi) % 12]
    return result


def transpose_line(line: str, semi: int, use_sharps: bool = True) -> str:
    def _rep_bracket(m):
        return f"[{_transpose_chord(m.group(1), semi, use_sharps)}]"
    result = _BRACKETED_RE.sub(_rep_bracket, line)
    if result == line and not any(c.islower() for c in line.replace("m", "").replace("b", "")):
        result = _BARE_RE.sub(lambda m: _transpose_chord(m.group(1), semi, use_sharps), line)
    return result


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_BUNDLE_PATH = Path(__file__).parent / "songs_bundle.json"
DEFAULT_FONT_SIZE = 22
MIN_FONT_SIZE = 10
MAX_FONT_SIZE = 72
FONT_STEP = 2

# Regex for chord detection
_CHORD_TOKEN = r"[A-G][#b]?(?:m|maj|min|dim|aug|sus[24]?|add\d+|7|9|11|13|6|/[A-G][#b]?)*"
_CHORD_LINE_RE = re.compile(
    r"^\s*(?:" + _CHORD_TOKEN + r")(?:\s+" + _CHORD_TOKEN + r")*\s*$"
)
_CHORD_SPAN_RE = re.compile(r"(" + _CHORD_TOKEN + r")")
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")

# ---------------------------------------------------------------------------
# Bundle-based helpers (no filesystem access)
# ---------------------------------------------------------------------------

# Load bundle once at import
_BUNDLE_DATA = json.loads(_BUNDLE_PATH.read_text(encoding="utf-8")) if _BUNDLE_PATH.exists() else []

# Build indexes from bundle
_SONG_BY_KEY: dict[tuple[str, str], dict] = {}
_ARTISTS_SET: set[str] = set()
_SONGS_BY_ARTIST: dict[str, list[str]] = {}

for _entry in _BUNDLE_DATA:
    _a = _entry.get("artist", "")
    _t = _entry.get("title", "")
    _SONG_BY_KEY[(_a.lower(), _t.lower())] = _entry
    if _entry.get("song_text") and _a:
        _ARTISTS_SET.add(_a)
        _SONGS_BY_ARTIST.setdefault(_a, []).append(_t + ".txt")

for _a in _SONGS_BY_ARTIST:
    _SONGS_BY_ARTIST[_a].sort()


def get_artists(root=None) -> list[str]:
    return sorted(_ARTISTS_SET)


def get_songs(root, artist: str) -> list[str]:
    return _SONGS_BY_ARTIST.get(artist, [])


def get_all_songs(root=None) -> list[tuple[str, str]]:
    results = []
    for artist in sorted(_ARTISTS_SET):
        for song in _SONGS_BY_ARTIST.get(artist, []):
            results.append((artist, song))
    return results


def load_song(root, artist: str, filename: str) -> str:
    title = filename.removesuffix(".txt")
    entry = _SONG_BY_KEY.get((artist.lower(), title.lower()))
    if entry and entry.get("song_text"):
        return entry["song_text"]
    return ""


def song_display_name(filename: str) -> str:
    return filename.removesuffix(".txt")


def format_song_html(text: str, transpose: int = 0) -> str:
    lines = text.splitlines()
    html_lines = []
    for line in lines:
        if transpose != 0:
            line = transpose_line(line, transpose, use_sharps=(transpose > 0))
        escaped = html.escape(line)
        section_match = _SECTION_RE.match(line)
        if section_match:
            html_lines.append(f'<span class="section-header">{escaped}</span>')
            continue
        stripped = line.strip()
        if stripped and _CHORD_LINE_RE.match(stripped):
            def _highlight_chord(m: re.Match) -> str:
                return f'<span class="chord">{html.escape(m.group(1))}</span>'
            highlighted = _CHORD_SPAN_RE.sub(_highlight_chord, html.escape(line))
            html_lines.append(f'<span class="chord-line">{highlighted}</span>')
            continue
        html_lines.append(escaped)
    return "\n".join(html_lines)


# Pre-build song index
ALL_SONGS = get_all_songs()

# Songs database is the bundle itself
SONGS_DB = _SONG_BY_KEY
SONGS_LIST = _BUNDLE_DATA

# Build filter option lists from the data
_genres = sorted({s["genre"] for s in SONGS_LIST if s.get("genre")})
_keys = sorted({s["key"] for s in SONGS_LIST if s.get("key") and len(s["key"]) <= 4})
_energies = sorted({s["energy"] for s in SONGS_LIST if s.get("energy")})


def lookup_song_metadata(artist: str, filename: str) -> dict | None:
    title = song_display_name(filename).lower()
    return SONGS_DB.get((artist.lower(), title))


def format_settings_html(meta: dict) -> str:
    """Build collapsible accordion HTML for equipment settings."""
    sections = []

    # Song info summary (always visible in the accordion header)
    info = []
    if meta.get("key"):
        info.append(f'<span class="set-key">{html.escape(str(meta["key"]))}</span>')
    if meta.get("bpm"):
        info.append(f'<span class="set-bpm">{meta["bpm"]}bpm</span>')
    if meta.get("genre"):
        info.append(f'<span class="set-genre">{html.escape(str(meta["genre"]))}</span>')
    if meta.get("unique_chords"):
        info.append(f'<span class="set-chords">{meta["unique_chords"]} chords</span>')
    if meta.get("energy"):
        info.append(f'<span class="set-energy">{html.escape(str(meta["energy"]))}</span>')
    summary = " &middot; ".join(info) if info else "No metadata"

    # Guitar section
    gs = meta.get("guitar_settings", {})
    if gs and gs.get("guitar_used"):
        items = [f'<b>{html.escape(gs["guitar_used"])}</b>']
        if gs.get("pickup"):
            items.append(f'Pickup: {gs["pickup"]}')
        if gs.get("volume"):
            items.append(f'Vol: {gs["volume"]}')
        if gs.get("tone"):
            items.append(f'Tone: {gs["tone"]}')
        sections.append(("Guitar", " &middot; ".join(items)))

    # Amp section
    amp = meta.get("amp_settings", {})
    if amp and amp.get("voice"):
        items = [f'<b>{html.escape(str(amp["voice"]))}</b>']
        for k, label in [("gain", "Gain"), ("treble", "Tre"), ("bass", "Bas"), ("fx_select", "FX"), ("fx_level", "Lvl")]:
            if amp.get(k):
                items.append(f'{label}: {amp[k]}')
        sections.append(("Amp", " &middot; ".join(items)))

    # Piano CDP-S360
    ps = meta.get("piano_settings", {})
    cdp = ps.get("cdp_s360", {}) if ps else {}
    if cdp and cdp.get("tone_name"):
        items = [f'<b>#{cdp.get("tone", "?")} {html.escape(str(cdp["tone_name"]))}</b>']
        if cdp.get("reverb_type") is not None:
            items.append(f'Rev: {cdp["reverb_type"]}')
        if cdp.get("chorus_mode") is not None:
            items.append(f'Cho: {cdp["chorus_mode"]}')
        if cdp.get("touch"):
            items.append(f'Touch: {cdp["touch"]}')
        sections.append(("CDP-S360", " &middot; ".join(items)))

    # Piano AP-650M
    ap = ps.get("ap_650m", {}) if ps else {}
    if ap and ap.get("tone"):
        items = [f'<b>{html.escape(str(ap["tone"]))}</b>']
        if ap.get("reverb") is not None:
            items.append(f'Rev: {ap["reverb"]}')
        if ap.get("brilliance") is not None:
            items.append(f'Brill: {ap["brilliance"]}')
        sections.append(("AP-650M", " &middot; ".join(items)))

    # Build Bootstrap accordion
    body_lines = []
    for label, content in sections:
        body_lines.append(f'<div class="set-row"><span class="set-label">{label}</span>{content}</div>')
    body_html = "".join(body_lines)

    return f"""<div class="settings-accordion">
  <div class="settings-header" onclick="this.parentElement.classList.toggle('open')">
    <span class="settings-toggle">&#9654;</span>
    <span class="settings-summary">{summary}</span>
  </div>
  <div class="settings-body">{body_html}</div>
</div>"""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

APP_CSS = """
:root {
    --bg: #1a1a1a;
    --bg-sidebar: #111111;
    --fg: #e0e0e0;
    --fg-muted: #888888;
    --accent: #5cabff;
    --chord-color: #f0c040;
    --section-color: #66bbff;
    --speed-color: #66dd88;
}

* { box-sizing: border-box; }

html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: system-ui, sans-serif;
    height: 100%;
    overflow: hidden;
}

.bslib-page-sidebar {
    --bs-body-bg: var(--bg) !important;
    --bs-body-color: var(--fg) !important;
    height: 100vh !important;
}

.bslib-sidebar-layout { height: 100vh !important; }

.bslib-sidebar-layout > .sidebar {
    background: var(--bg-sidebar) !important;
    border-right: 1px solid #333 !important;
    overflow-y: auto !important;
}

.bslib-sidebar-layout > .main {
    background: var(--bg) !important;
    padding: 0 !important;
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
}

.sidebar label, .sidebar .control-label,
.sidebar .form-label, .sidebar .shiny-input-container label {
    color: var(--fg) !important;
}

.sidebar select, .sidebar .form-select,
.sidebar input[type="text"], .sidebar input[type="number"],
.sidebar .form-control {
    background: #222 !important;
    color: var(--fg) !important;
    border-color: #444 !important;
    font-size: 12px !important;
}

.sidebar .form-check-label { color: var(--fg) !important; }
.sidebar .form-check-input:checked {
    background-color: var(--accent) !important;
    border-color: var(--accent) !important;
}

/* Header bar */
#header-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 16px;
    background: #111;
    border-bottom: 1px solid #333;
    flex-shrink: 0;
    min-height: 38px;
}

#header-bar .song-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--accent);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
}

#header-bar .status {
    font-size: 12px;
    color: var(--fg-muted);
    display: flex;
    gap: 12px;
    flex-shrink: 0;
}

.status .speed-val { color: var(--speed-color); font-weight: 600; }
.status .playing   { color: #66dd88; }
.status .paused    { color: #dd6666; }

/* Settings accordion — collapsible */
#settings-bar {
    flex-shrink: 0;
    display: none;
}
#settings-bar.visible { display: block; }

.settings-accordion {
    background: #0d1117;
    border-bottom: 1px solid #333;
}
.settings-header {
    padding: 4px 16px;
    cursor: pointer;
    font-size: 12px;
    color: var(--fg-muted);
    user-select: none;
    display: flex;
    align-items: center;
    gap: 6px;
}
.settings-header:hover { background: #111; }

.settings-toggle {
    font-size: 9px;
    transition: transform 0.15s;
    color: var(--fg-muted);
}
.settings-accordion.open .settings-toggle { transform: rotate(90deg); }

.settings-summary { flex: 1; }

.settings-body {
    display: none;
    padding: 2px 16px 6px 28px;
    font-size: 12px;
    line-height: 1.7;
}
.settings-accordion.open .settings-body { display: block; }

.set-row { color: var(--fg-muted); }
.set-row b { color: var(--fg); font-weight: 600; }

.set-label {
    display: inline-block;
    min-width: 68px;
    color: var(--chord-color);
    font-weight: 600;
    font-size: 11px;
}

.set-key { color: var(--accent); font-weight: 600; }
.set-bpm { color: var(--speed-color); }
.set-genre { color: var(--fg); }
.set-chords { color: var(--chord-color); }
.set-energy { color: var(--fg-muted); font-style: italic; }

/* Song display area */
#song-container {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 20px 24px;
    scroll-behavior: auto;
}

#song-container::-webkit-scrollbar { width: 8px; }
#song-container::-webkit-scrollbar-track { background: #111; }
#song-container::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }

#song-text {
    font-family: 'Courier New', Courier, monospace;
    white-space: pre;
    line-height: 1.5;
    color: var(--fg);
    margin: 0;
    padding-bottom: 80vh;
    /* No overflow — font auto-fits to width */
}

#song-text.two-col {
    column-count: 2;
    column-gap: 48px;
    column-rule: 1px solid #333;
    padding-bottom: 10vh;
}

#song-text.three-col {
    column-count: 3;
    column-gap: 36px;
    column-rule: 1px solid #333;
    padding-bottom: 10vh;
}

.chord { color: var(--chord-color); font-weight: 600; }
.chord-line { color: var(--chord-color); }
.section-header {
    color: var(--section-color);
    font-weight: 700;
    font-style: italic;
}

/* Help bar */
#help-bar {
    padding: 4px 16px;
    background: #111;
    border-top: 1px solid #333;
    font-size: 11px;
    color: var(--fg-muted);
    flex-shrink: 0;
    text-align: center;
}

#help-bar kbd {
    background: #333;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 10px;
    color: var(--fg);
}

/* Sidebar controls */
.ctrl-btn {
    display: inline-block;
    width: 32px; height: 32px;
    line-height: 32px; text-align: center;
    background: #333; color: var(--fg);
    border: none; border-radius: 4px;
    cursor: pointer; font-size: 16px; margin: 0 2px;
}
.ctrl-btn:hover { background: #555; }

.ctrl-val {
    display: inline-block; width: 36px; text-align: center;
    color: var(--fg); font-size: 13px; vertical-align: middle;
}

.transpose-val {
    display: inline-block; width: 28px; text-align: center;
    color: var(--chord-color); font-size: 13px; font-weight: 600;
    vertical-align: middle;
}

.sidebar-section { margin-bottom: 6px; }

.sidebar-section-title {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--fg-muted);
    margin-bottom: 3px;
}

#setlist-panel {
    margin-top: 6px;
    padding: 6px;
    background: #1a1a1a;
    border-radius: 4px;
    max-height: 160px;
    overflow-y: auto;
}

.setlist-item {
    padding: 3px 6px;
    font-size: 11px;
    color: var(--fg-muted);
    cursor: pointer;
    border-radius: 3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.setlist-item:hover { background: #333; }
.setlist-item.active {
    background: #2a3a4a;
    color: var(--accent);
    font-weight: 600;
}
.setlist-item .sl-artist { font-size: 10px; color: var(--fg-muted); }

#search-results, #filter-results {
    max-height: 200px;
    overflow-y: auto;
    margin-top: 4px;
}

.search-result, .filter-result {
    padding: 3px 6px;
    font-size: 11px;
    color: var(--fg);
    cursor: pointer;
    border-radius: 3px;
}
.search-result:hover, .filter-result:hover { background: #333; }
.search-result .sr-artist, .filter-result .fr-meta {
    color: var(--fg-muted); font-size: 10px;
}

/* Font size fit indicator */
#fit-indicator {
    font-size: 10px;
    color: var(--fg-muted);
    margin-top: 2px;
}
"""

# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

APP_JS = """
(function() {
    let scrolling = false;
    let speed = 2;
    let scrollInterval = null;
    let mode = 'scroll';
    let timedMode = false;
    let timedPxPerTick = 0;
    let currentFontSize = """ + str(DEFAULT_FONT_SIZE) + """;
    let maxLongestLineChars = 80;

    function getContainer() { return document.getElementById('song-container'); }
    function getSongText() { return document.getElementById('song-text'); }

    // --- Auto-fit font size to container width ---
    function measureMaxFont() {
        // Calculate max font where longest line fits container width
        const container = getContainer();
        const el = getSongText();
        if (!container || !el) return """ + str(MAX_FONT_SIZE) + """;

        const containerWidth = container.clientWidth - 48; // padding
        const cols = el.classList.contains('three-col') ? 3 : el.classList.contains('two-col') ? 2 : 1;
        const gap = cols === 3 ? 36 : 48;
        const colWidth = cols > 1 ? (containerWidth - gap * (cols - 1)) / cols : containerWidth;

        // Measure char width at current font: create a hidden span
        var probe = document.getElementById('font-probe');
        if (!probe) {
            probe = document.createElement('span');
            probe.id = 'font-probe';
            probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-family:Courier New,Courier,monospace;';
            document.body.appendChild(probe);
        }

        // Binary search for max font size
        var lo = """ + str(MIN_FONT_SIZE) + """, hi = """ + str(MAX_FONT_SIZE) + """;
        var testStr = 'M'.repeat(maxLongestLineChars);
        while (lo < hi) {
            var mid = Math.ceil((lo + hi) / 2);
            probe.style.fontSize = mid + 'px';
            probe.textContent = testStr;
            if (probe.offsetWidth <= colWidth) {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }
        return lo;
    }

    function applyFontSize(size) {
        var maxFit = measureMaxFont();
        var actual = Math.min(size, maxFit);
        currentFontSize = actual;
        var el = getSongText();
        if (el) el.style.fontSize = actual + 'px';
        var label = document.getElementById('font-size-label');
        if (label) label.textContent = actual;
        var fit = document.getElementById('fit-indicator');
        if (fit) {
            if (actual < size) {
                fit.textContent = 'max fit: ' + actual + 'px';
            } else {
                fit.textContent = '';
            }
        }
        // Tell Python the actual applied size
        Shiny.setInputValue('_actual_font_size', actual);
    }

    function updateLongestLine(text) {
        // Measure longest line in raw text (for font fitting)
        var lines = text.split('\\n');
        var max = 0;
        for (var i = 0; i < lines.length; i++) {
            if (lines[i].length > max) max = lines[i].length;
        }
        maxLongestLineChars = Math.max(max, 20);
    }

    // --- Scroll engine ---
    function startScroll() {
        stopScroll();
        scrolling = true;
        var pxPerTick = timedMode ? timedPxPerTick : speed;
        scrollInterval = setInterval(function() {
            var el = getContainer();
            if (el) el.scrollTop += pxPerTick;
        }, 50);
        updateStatus();
    }

    function stopScroll() {
        scrolling = false;
        if (scrollInterval) { clearInterval(scrollInterval); scrollInterval = null; }
        updateStatus();
    }

    function pageFlip() {
        var el = getContainer();
        if (!el) return;
        el.scrollTop += el.clientHeight - 40;
    }

    function updateStatus() {
        var effectiveSpeed = timedMode ? timedPxPerTick.toFixed(1) : speed;
        Shiny.setInputValue('_scroll_state', {
            scrolling: scrolling,
            speed: effectiveSpeed,
            mode: mode,
            timed: timedMode,
            ts: Date.now()
        });
    }

    // --- Keyboard ---
    document.addEventListener('keydown', function(e) {
        var tag = e.target.tagName;
        if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
        if (e.target.contentEditable === 'true') return;

        switch(e.code) {
            case 'Space':
                e.preventDefault();
                if (mode === 'scroll') {
                    if (scrolling) stopScroll(); else startScroll();
                } else { pageFlip(); }
                break;
            case 'ArrowUp':
                e.preventDefault();
                if (!timedMode) { speed = Math.min(speed + 1, 20); if (scrolling) startScroll(); updateStatus(); }
                break;
            case 'ArrowDown':
                e.preventDefault();
                if (!timedMode) { speed = Math.max(speed - 1, 1); if (scrolling) startScroll(); updateStatus(); }
                break;
            case 'Equal': case 'NumpadAdd':
                e.preventDefault();
                Shiny.setInputValue('_font_up', Date.now());
                break;
            case 'Minus': case 'NumpadSubtract':
                e.preventDefault();
                Shiny.setInputValue('_font_down', Date.now());
                break;
            case 'Escape':
                e.preventDefault();
                stopScroll();
                var el = getContainer();
                if (el) el.scrollTop = 0;
                break;
            case 'ArrowRight':
                e.preventDefault();
                Shiny.setInputValue('_next_song', Date.now());
                break;
            case 'ArrowLeft':
                e.preventDefault();
                Shiny.setInputValue('_prev_song', Date.now());
                break;
        }
    });

    // Recalculate font fit on window resize
    window.addEventListener('resize', function() {
        applyFontSize(currentFontSize);
    });

    // --- Message handlers ---

    Shiny.addCustomMessageHandler('set_mode', function(msg) {
        mode = msg;
        if (mode === 'page') stopScroll();
        updateStatus();
    });

    Shiny.addCustomMessageHandler('reset_scroll', function(msg) {
        stopScroll();
        var el = getContainer();
        if (el) el.scrollTop = 0;
    });

    Shiny.addCustomMessageHandler('set_font_size', function(size) {
        applyFontSize(size);
    });

    Shiny.addCustomMessageHandler('load_song', function(msg) {
        updateLongestLine(msg.raw_text || '');
        var songEl = getSongText();
        if (songEl) songEl.innerHTML = msg.html;
        var titleEl = document.getElementById('current-song-title');
        if (titleEl) titleEl.textContent = msg.title;
        // Refit font after content change
        applyFontSize(currentFontSize);
    });

    Shiny.addCustomMessageHandler('update_status_bar', function(msg) {
        var speedEl = document.getElementById('speed-display');
        if (speedEl) speedEl.textContent = msg.speed;
        var statusEl = document.getElementById('play-status');
        if (statusEl) {
            statusEl.textContent = msg.scrolling ? 'Playing' : 'Paused';
            statusEl.className = msg.scrolling ? 'playing' : 'paused';
        }
        var timedEl = document.getElementById('timed-indicator');
        if (timedEl) timedEl.textContent = msg.timed ? '(timed)' : '';
    });

    Shiny.addCustomMessageHandler('set_timed_scroll', function(msg) {
        timedMode = msg.enabled;
        timedPxPerTick = msg.pxPerTick || 0;
        if (scrolling) startScroll();
        updateStatus();
    });

    Shiny.addCustomMessageHandler('set_columns', function(cols) {
        var el = getSongText();
        if (!el) return;
        el.classList.remove('two-col', 'three-col');
        if (cols === 2) { el.classList.add('two-col'); }
        else if (cols === 3) { el.classList.add('three-col'); }
        // Refit font for new column width
        applyFontSize(currentFontSize);
    });

    Shiny.addCustomMessageHandler('load_settings', function(msg) {
        var bar = document.getElementById('settings-bar');
        if (!bar) return;
        if (msg.html) {
            bar.innerHTML = msg.html;
            bar.classList.add('visible');
        } else {
            bar.innerHTML = '';
            bar.classList.remove('visible');
        }
    });

    Shiny.addCustomMessageHandler('update_setlist_ui', function(msg) {
        var panel = document.getElementById('setlist-panel');
        if (!panel) return;
        var h = '';
        msg.items.forEach(function(item, i) {
            var cls = (i === msg.activeIndex) ? 'setlist-item active' : 'setlist-item';
            h += '<div class="' + cls + '" onclick="Shiny.setInputValue(\\'_setlist_jump\\', ' + i + ')">';
            h += item.display + ' <span class="sl-artist">' + item.artist + '</span></div>';
        });
        panel.innerHTML = h;
    });

    Shiny.addCustomMessageHandler('update_search_results', function(msg) {
        var panel = document.getElementById('search-results');
        if (!panel) return;
        if (!msg.results.length) {
            panel.innerHTML = '<div style="color:#666;font-size:11px;padding:3px 6px;">No results</div>';
            return;
        }
        var h = '';
        msg.results.forEach(function(item) {
            h += '<div class="search-result" onclick="Shiny.setInputValue(\\'_search_pick\\', {artist:\\'' +
                item.artist.replace(/'/g, "\\\\\\\\'") + '\\',song:\\'' +
                item.song.replace(/'/g, "\\\\\\\\'") + '\\',ts:Date.now()})">';
            h += item.display + ' <span class="sr-artist">' + item.artist + '</span></div>';
        });
        panel.innerHTML = h;
    });

    Shiny.addCustomMessageHandler('update_filter_results', function(msg) {
        var panel = document.getElementById('filter-results');
        if (!panel) return;
        if (!msg.results.length) {
            panel.innerHTML = '<div style="color:#666;font-size:11px;padding:3px 6px;">No matches</div>';
            return;
        }
        var h = '';
        msg.results.forEach(function(item) {
            h += '<div class="filter-result" onclick="Shiny.setInputValue(\\'_filter_pick\\', {artist:\\'' +
                item.artist.replace(/'/g, "\\\\\\\\'") + '\\',song:\\'' +
                item.song.replace(/'/g, "\\\\\\\\'") + '\\',ts:Date.now()})">';
            h += item.display + ' <span class="fr-meta">' + item.meta + '</span></div>';
        });
        panel.innerHTML = h;
    });

    Shiny.addCustomMessageHandler('update_transpose_label', function(val) {
        var el = document.getElementById('transpose-label');
        if (el) el.textContent = (val > 0 ? '+' : '') + val;
    });

    // Initial status
    if (typeof Shiny !== 'undefined' && Shiny.setInputValue) { updateStatus(); }
    else { document.addEventListener('shiny:connected', function() { updateStatus(); }); }
})();
"""

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

artists = get_artists(None)
first_artist = artists[0] if artists else ""
first_songs = get_songs(None, first_artist) if first_artist else []

app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h4("Teleprompter", style="color: #5cabff; margin-top: 0;"),

        # -- Search --
        ui.div(
            ui.div("SEARCH", class_="sidebar-section-title"),
            ui.input_text("search", None, placeholder="Search all songs..."),
            ui.div(id="search-results"),
            class_="sidebar-section",
        ),
        ui.hr(style="border-color: #333; margin: 4px 0;"),

        # -- Filter by metadata --
        ui.div(
            ui.div("FILTER", class_="sidebar-section-title"),
            ui.input_select("filter_genre", "Genre", choices={"": "All"} | {g: g for g in _genres}, selected=""),
            ui.input_select("filter_key", "Key", choices={"": "All"} | {k: k for k in _keys}, selected=""),
            ui.input_select("filter_energy", "Energy", choices={"": "All"} | {e: e for e in _energies}, selected=""),
            ui.input_select("sort_by", "Sort by", choices={
                "title": "Title",
                "artist": "Artist",
                "bpm": "BPM",
                "unique_chords": "# Chords",
                "key": "Key",
            }, selected="title"),
            ui.input_action_button("apply_filter", "Apply Filter",
                                   style="width:100%;background:#2a3a4a;color:#e0e0e0;border:1px solid #444;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;margin-top:4px;"),
            ui.div(id="filter-results"),
            class_="sidebar-section",
        ),
        ui.hr(style="border-color: #333; margin: 4px 0;"),

        # -- Browse --
        ui.div(
            ui.div("BROWSE", class_="sidebar-section-title"),
            ui.input_select("artist", "Artist", choices=artists, selected=first_artist),
            ui.input_select("song", "Song", choices={s: song_display_name(s) for s in first_songs}),
            class_="sidebar-section",
        ),
        ui.hr(style="border-color: #333; margin: 4px 0;"),

        # -- Mode + Timed scroll --
        ui.div(
            ui.div("SCROLL", class_="sidebar-section-title"),
            ui.input_radio_buttons("mode", None, choices={"scroll": "Auto-Scroll", "page": "Page Flip"}, selected="scroll"),
            ui.input_text("duration", "Duration (m:ss)", placeholder="3:30"),
            class_="sidebar-section",
        ),
        ui.hr(style="border-color: #333; margin: 4px 0;"),

        # -- Transpose --
        ui.div(
            ui.div("TRANSPOSE", class_="sidebar-section-title"),
            ui.div(
                ui.input_action_button("transpose_down", "-", class_="ctrl-btn"),
                ui.span("0", id="transpose-label", class_="transpose-val"),
                ui.input_action_button("transpose_up", "+", class_="ctrl-btn"),
                style="display:flex; align-items:center; gap:4px;",
            ),
            class_="sidebar-section",
        ),
        ui.hr(style="border-color: #333; margin: 4px 0;"),

        # -- Display --
        ui.div(
            ui.div("DISPLAY", class_="sidebar-section-title"),
            ui.div(
                ui.tags.label("Font", style="color:#e0e0e0;font-size:11px;margin-right:6px;"),
                ui.input_action_button("font_down_btn", "-", class_="ctrl-btn"),
                ui.span(str(DEFAULT_FONT_SIZE), id="font-size-label", class_="ctrl-val"),
                ui.input_action_button("font_up_btn", "+", class_="ctrl-btn"),
                style="display:flex; align-items:center; gap:3px;",
            ),
            ui.div(id="fit-indicator"),
            ui.div(
                ui.input_radio_buttons("columns", "Columns", choices={"1": "1", "2": "2", "3": "3"}, selected="1", inline=True),
                style="margin-top:4px;",
            ),
            class_="sidebar-section",
        ),
        ui.hr(style="border-color: #333; margin: 4px 0;"),

        # -- Setlist --
        ui.div(
            ui.div("SETLIST", class_="sidebar-section-title"),
            ui.input_action_button("add_to_setlist", "Add Current Song",
                                   style="width:100%;background:#2a3a4a;color:#e0e0e0;border:1px solid #444;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;"),
            ui.input_action_button("clear_setlist", "Clear",
                                   style="width:100%;background:#333;color:#888;border:1px solid #444;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer;margin-top:3px;"),
            ui.div(id="setlist-panel"),
            class_="sidebar-section",
        ),

        width=270,
    ),

    # Main content
    ui.div(
        ui.div(
            ui.span("Select a song", class_="song-title", id="current-song-title"),
            ui.div(
                ui.span("Speed: ", style="color:#888;"),
                ui.span("2", class_="speed-val", id="speed-display"),
                ui.span("", style="color:#888;font-size:10px;margin-left:3px;", id="timed-indicator"),
                ui.span("Paused", id="play-status", class_="paused"),
                class_="status",
            ),
            id="header-bar",
        ),
        ui.div(id="settings-bar"),
        ui.div(
            ui.tags.pre("", id="song-text", style=f"font-size: {DEFAULT_FONT_SIZE}px;"),
            id="song-container",
        ),
        ui.div(
            ui.HTML(
                "<kbd>Space</kbd> Play/Pause &nbsp; "
                "<kbd>&uarr;</kbd><kbd>&darr;</kbd> Speed &nbsp; "
                "<kbd>&larr;</kbd><kbd>&rarr;</kbd> Prev/Next &nbsp; "
                "<kbd>+</kbd><kbd>-</kbd> Font &nbsp; "
                "<kbd>Esc</kbd> Stop"
            ),
            id="help-bar",
        ),
    ),
    ui.tags.style(APP_CSS),
    ui.tags.script(APP_JS),
    title="PGA Teleprompter",
    fillable=True,
)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def server(input: Inputs, output: Outputs, session: Session):
    font_size = reactive.value(DEFAULT_FONT_SIZE)
    transpose_val = reactive.value(0)
    current_artist = reactive.value("")
    current_song = reactive.value("")
    current_raw_text = reactive.value("")
    setlist = reactive.value([])
    setlist_index = reactive.value(-1)

    async def _display_song(artist: str, song: str):
        if not artist or not song:
            return
        current_artist.set(artist)
        current_song.set(song)
        text = load_song(None, artist, song)
        current_raw_text.set(text)
        transpose_val.set(0)
        await session.send_custom_message("update_transpose_label", 0)
        await _render_song(text, 0)
        meta = lookup_song_metadata(artist, song)
        if meta:
            await session.send_custom_message("load_settings", {"html": format_settings_html(meta)})
        else:
            await session.send_custom_message("load_settings", {"html": ""})

    async def _render_song(text: str, transpose: int):
        song_html = format_song_html(text, transpose)
        title = f"{song_display_name(current_song.get())} \u2014 {current_artist.get()}"
        await session.send_custom_message("reset_scroll", True)
        await session.send_custom_message("load_song", {
            "html": song_html,
            "title": title,
            "raw_text": text,
        })
        await _update_timed_scroll()

    async def _update_timed_scroll():
        dur_str = input.duration() if input.duration() else ""
        if not dur_str.strip():
            await session.send_custom_message("set_timed_scroll", {"enabled": False, "pxPerTick": 0})
            return
        seconds = _parse_duration(dur_str)
        if seconds and seconds > 0:
            text = current_raw_text.get()
            line_count = text.count("\n") + 1 if text else 1
            current_fs = font_size.get()
            line_height = current_fs * 1.5
            total_height = line_count * line_height
            ticks = seconds * 20
            px_per_tick = total_height / ticks if ticks > 0 else 1
            px_per_tick = max(0.5, min(px_per_tick, 20))
            await session.send_custom_message("set_timed_scroll", {"enabled": True, "pxPerTick": round(px_per_tick, 2)})
        else:
            await session.send_custom_message("set_timed_scroll", {"enabled": False, "pxPerTick": 0})

    # -- Browse --
    @reactive.effect
    @reactive.event(input.artist)
    def _update_songs():
        songs = get_songs(None, input.artist())
        choices = {s: song_display_name(s) for s in songs}
        ui.update_select("song", choices=choices)

    @reactive.effect
    @reactive.event(input.song)
    async def _on_song_select():
        await _display_song(input.artist(), input.song())

    # -- Search --
    @reactive.effect
    @reactive.event(input.search)
    async def _on_search():
        query = (input.search() or "").strip().lower()
        if len(query) < 2:
            await session.send_custom_message("update_search_results", {"results": []})
            return
        results = []
        for artist, song in ALL_SONGS:
            display = song_display_name(song)
            if query in display.lower() or query in artist.lower():
                results.append({"artist": artist, "song": song, "display": display})
                if len(results) >= 25:
                    break
        await session.send_custom_message("update_search_results", {"results": results})

    @reactive.effect
    @reactive.event(input._search_pick)
    async def _on_search_pick():
        pick = input._search_pick()
        if not pick:
            return
        artist, song = pick.get("artist", ""), pick.get("song", "")
        if artist and song:
            ui.update_select("artist", selected=artist)
            songs = get_songs(None, artist)
            choices = {s: song_display_name(s) for s in songs}
            ui.update_select("song", choices=choices, selected=song)
            await _display_song(artist, song)

    # -- Filter --
    @reactive.effect
    @reactive.event(input.apply_filter)
    async def _on_filter():
        genre = input.filter_genre() or ""
        key = input.filter_key() or ""
        energy = input.filter_energy() or ""
        sort_by = input.sort_by() or "title"

        filtered = []
        for entry in SONGS_LIST:
            if not entry.get("file_chords"):
                continue
            if genre and entry.get("genre") != genre:
                continue
            if key and entry.get("key") != key:
                continue
            if energy and entry.get("energy") != energy:
                continue
            filtered.append(entry)

        reverse = sort_by in ("bpm", "unique_chords")
        filtered.sort(key=lambda s: (s.get(sort_by) or "", s.get("title", "")), reverse=reverse)

        results = []
        for entry in filtered[:50]:
            artist = entry.get("artist", "")
            title = entry.get("title", "")
            # Find matching file
            file_chords = entry.get("file_chords", "")
            song_file = Path(file_chords).name if file_chords else ""
            meta_parts = []
            if entry.get("key"):
                meta_parts.append(entry["key"])
            if entry.get("bpm"):
                meta_parts.append(f'{entry["bpm"]}bpm')
            if entry.get("unique_chords"):
                meta_parts.append(f'{entry["unique_chords"]}ch')
            meta_str = " ".join(meta_parts)
            results.append({"artist": artist, "song": song_file, "display": title, "meta": meta_str})
        await session.send_custom_message("update_filter_results", {"results": results})

    @reactive.effect
    @reactive.event(input._filter_pick)
    async def _on_filter_pick():
        pick = input._filter_pick()
        if not pick:
            return
        artist, song = pick.get("artist", ""), pick.get("song", "")
        if artist and song:
            ui.update_select("artist", selected=artist)
            songs = get_songs(None, artist)
            choices = {s: song_display_name(s) for s in songs}
            ui.update_select("song", choices=choices, selected=song)
            await _display_song(artist, song)

    # -- Mode --
    @reactive.effect
    @reactive.event(input.mode)
    async def _mode_change():
        await session.send_custom_message("set_mode", input.mode())

    # -- Timed scroll --
    @reactive.effect
    @reactive.event(input.duration)
    async def _on_duration_change():
        await _update_timed_scroll()

    # -- Transpose --
    @reactive.effect
    @reactive.event(input.transpose_up)
    async def _transpose_up():
        new = transpose_val.get() + 1
        if new > 11:
            new = -11
        transpose_val.set(new)
        await session.send_custom_message("update_transpose_label", new)
        await _render_song(current_raw_text.get(), new)

    @reactive.effect
    @reactive.event(input.transpose_down)
    async def _transpose_down():
        new = transpose_val.get() - 1
        if new < -11:
            new = 11
        transpose_val.set(new)
        await session.send_custom_message("update_transpose_label", new)
        await _render_song(current_raw_text.get(), new)

    # -- Font size (auto-fit aware) --
    @reactive.effect
    @reactive.event(input.font_up_btn)
    async def _font_up_btn():
        new = min(font_size.get() + FONT_STEP, MAX_FONT_SIZE)
        font_size.set(new)
        await session.send_custom_message("set_font_size", new)

    @reactive.effect
    @reactive.event(input.font_down_btn)
    async def _font_down_btn():
        new = max(font_size.get() - FONT_STEP, MIN_FONT_SIZE)
        font_size.set(new)
        await session.send_custom_message("set_font_size", new)

    @reactive.effect
    @reactive.event(input._font_up)
    async def _font_up_key():
        new = min(font_size.get() + FONT_STEP, MAX_FONT_SIZE)
        font_size.set(new)
        await session.send_custom_message("set_font_size", new)

    @reactive.effect
    @reactive.event(input._font_down)
    async def _font_down_key():
        new = max(font_size.get() - FONT_STEP, MIN_FONT_SIZE)
        font_size.set(new)
        await session.send_custom_message("set_font_size", new)

    # -- Columns --
    @reactive.effect
    @reactive.event(input.columns)
    async def _on_columns():
        await session.send_custom_message("set_columns", int(input.columns()))

    # -- Setlist --
    async def _refresh_setlist_ui():
        sl = setlist.get()
        items = [{"artist": s["artist"], "song": s["song"],
                  "display": song_display_name(s["song"])} for s in sl]
        await session.send_custom_message("update_setlist_ui", {
            "items": items,
            "activeIndex": setlist_index.get(),
        })

    @reactive.effect
    @reactive.event(input.add_to_setlist)
    async def _add_to_setlist():
        artist = current_artist.get()
        song = current_song.get()
        if not artist or not song:
            return
        sl = setlist.get().copy()
        sl.append({"artist": artist, "song": song})
        setlist.set(sl)
        if setlist_index.get() < 0:
            setlist_index.set(0)
        await _refresh_setlist_ui()

    @reactive.effect
    @reactive.event(input.clear_setlist)
    async def _clear_setlist():
        setlist.set([])
        setlist_index.set(-1)
        await _refresh_setlist_ui()

    @reactive.effect
    @reactive.event(input._setlist_jump)
    async def _on_setlist_jump():
        idx = input._setlist_jump()
        if idx is None:
            return
        sl = setlist.get()
        if 0 <= idx < len(sl):
            setlist_index.set(idx)
            entry = sl[idx]
            await _display_song(entry["artist"], entry["song"])
            await _refresh_setlist_ui()

    # -- Next/Prev --
    @reactive.effect
    @reactive.event(input._next_song)
    async def _next_song():
        sl = setlist.get()
        if sl:
            idx = min(setlist_index.get() + 1, len(sl) - 1)
            setlist_index.set(idx)
            entry = sl[idx]
            await _display_song(entry["artist"], entry["song"])
            await _refresh_setlist_ui()
        else:
            songs = get_songs(None, current_artist.get())
            if not songs:
                return
            try:
                cur_idx = songs.index(current_song.get())
            except ValueError:
                return
            next_idx = min(cur_idx + 1, len(songs) - 1)
            ui.update_select("song", selected=songs[next_idx])
            await _display_song(current_artist.get(), songs[next_idx])

    @reactive.effect
    @reactive.event(input._prev_song)
    async def _prev_song():
        sl = setlist.get()
        if sl:
            idx = max(setlist_index.get() - 1, 0)
            setlist_index.set(idx)
            entry = sl[idx]
            await _display_song(entry["artist"], entry["song"])
            await _refresh_setlist_ui()
        else:
            songs = get_songs(None, current_artist.get())
            if not songs:
                return
            try:
                cur_idx = songs.index(current_song.get())
            except ValueError:
                return
            prev_idx = max(cur_idx - 1, 0)
            ui.update_select("song", selected=songs[prev_idx])
            await _display_song(current_artist.get(), songs[prev_idx])

    # -- Status bar --
    @reactive.effect
    @reactive.event(input._scroll_state)
    async def _update_status():
        state = input._scroll_state()
        if not state:
            return
        await session.send_custom_message("update_status_bar", {
            "speed": str(state.get("speed", 2)),
            "scrolling": bool(state.get("scrolling", False)),
            "timed": bool(state.get("timed", False)),
        })


def _parse_duration(s: str) -> int | None:
    s = s.strip()
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            return None
    try:
        return int(s)
    except ValueError:
        return None


app = App(app_ui, server)
