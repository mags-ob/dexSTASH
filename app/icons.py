"""The app's flat icons: one line style, 2px stroke and
round ends, drawn inline so nothing is fetched. They replace the emoji and
symbols that used to stand in for icons.

icon("camera") in a template; a button that is only an icon gets an
aria-label (and a title for PC) where it's used.
"""

from markupsafe import Markup, escape

# Each icon is SVG drawing inside a 24x24 box.
PATHS = {
    "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h13V10"/><path d="M10 19.5v-5h4v5"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "camera": '<path d="M4 8a2 2 0 0 1 2-2h2l1.5-2h5L16 6h2a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/>'
              '<circle cx="12" cy="13" r="3.5"/>',
    "cards": '<rect x="7" y="3" width="12" height="16" rx="2"/><path d="M5 6.5v12A2.5 2.5 0 0 0 7.5 21H15"/>',
    "back": '<path d="M19 12H5"/><path d="m11 18-6-6 6-6"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "star": '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    "heart": '<path d="M12 20s-7.5-4.4-7.5-10A4.3 4.3 0 0 1 12 7.3 4.3 4.3 0 0 1 19.5 10C19.5 15.6 12 20 12 20z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "dollar": '<path d="M12 3v18"/><path d="M16.5 7.5c-.6-1.5-2.3-2.5-4.5-2.5-2.6 0-4.5 1.3-4.5 3.3 0 4.6 9.5 2.4 9.5 7.2 '
              '0 2-2 3.5-5 3.5-2.4 0-4.2-1-4.9-2.7"/>',
    "gear": '<circle cx="12" cy="12" r="6.2"/><circle cx="12" cy="12" r="2.4"/><path d="M18.2 12.0L21.0 12.0M16.4 16.4L18.4 18.4M12.0 18.2L12.0 21.0M7.6 16.4L5.6 18.4M5.8 12.0L3.0 12.0M7.6 7.6L5.6 5.6M12.0 5.8L12.0 3.0M16.4 7.6L18.4 5.6"/>',  # Binder settings
    "more": '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "right": '<path d="m9 6 6 6-6 6"/>',
    "up-arrow": '<path d="M12 19V5"/><path d="m6 11 6-6 6 6"/>',
    "warning": '<path d="M10.3 4.2 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z"/>'
               '<path d="M12 9.5v4"/><path d="M12 17h.01"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/>'
             '<path d="m20.5 16-4.5-4.5L6 19.5"/>',
    "flash": '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
    "pencil": '<path d="M16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1 1-4z"/>',
    "upload": '<path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M4 16v3a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 20 19v-3"/>',
    "filter": '<path d="M4 6h16M7 12h10M10 18h4"/>',
    "sparkle": '<path d="M12 3c.6 4.6 2.4 6.4 7 7-4.6.6-6.4 2.4-7 7-.6-4.6-2.4-6.4-7-7 4.6-.6 6.4-2.4 7-7z"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4.5h6V7"/><path d="M6.5 7l1 12.5h9l1-12.5"/>',
    "note": '<path d="M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 3.5V16.5H5A1.5 1.5 0 0 1 3.5 15V6A1.5 1.5 0 0 1 5 4.5z"/>',
    "binder": '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M9 3.5v17"/>',
    "bag": '<path d="M5.5 8h13l-1 12.5h-11z"/><path d="M9 10V7a3 3 0 0 1 6 0v3"/>',
    "box": '<path d="M3.5 8 12 4l8.5 4v8.5L12 20.5 3.5 16.5z"/><path d="M3.5 8 12 12l8.5-4"/><path d="M12 12v8.5"/>',
    "gift": '<rect x="4" y="9" width="16" height="11.5" rx="1.5"/><path d="M3 9h18V6.5H3z"/><path d="M12 6.5v14"/>'
            '<path d="M12 6.5C10.5 3 7 3.5 7.5 5.5c.3 1 2.5 1 4.5 1zM12 6.5c1.5-3.5 5-3 4.5-1-.3 1-2.5 1-4.5 1z"/>',
    "card": '<rect x="6" y="3" width="12" height="18" rx="2"/><circle cx="12" cy="11" r="3"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "palette": '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.6-.9 1.1-1.8-.6-1.1.1-2.2 1.3-2.2h2.1a4 4 0 0 0 4-4c0-5-3.8-9-8.5-9z"/>'
               '<circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7.3" r="1"/><circle cx="14.5" cy="7.3" r="1"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "help": '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.1-2.4 3.6"/><path d="M12 17h.01"/>',
}
# Icons drawn filled rather than outlined (a chased star, a want heart).
FILLED = {"star-on": "star", "heart-on": "heart"}


def icon(name: str, size: int = 20, label: str | None = None, cls: str = "") -> Markup:
    filled = name in FILLED
    body = PATHS[FILLED.get(name, name)]
    fill = "currentColor" if filled else "none"
    a11y = f' role="img" aria-label="{escape(label)}"' if label else ' aria-hidden="true"'
    return Markup(f'<svg class="ic{(" " + cls) if cls else ""}" width="{size}" height="{size}" viewBox="0 0 24 24" '
                  f'fill="{fill}" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
                  f'stroke-linejoin="round"{a11y}>{body}</svg>')
