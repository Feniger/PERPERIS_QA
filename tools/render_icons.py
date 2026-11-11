"""Render the PNG app icons from static/icon.svg (run by hand after changing the mark).

    python tools/render_icons.py

Needs Playwright with Chromium (requirements-browser.txt). Writes:
- static/icon-16.png, static/icon-32.png: browser tabs, rounded corners, transparent outside.
- static/icon-180.png: iPhone home screen, full-bleed white square (iOS rounds it itself).
- static/icon-192.png, static/icon-512.png: Android/manifest, rounded like the source.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parent.parent / 'static'
SVG = (STATIC / 'icon.svg').read_text(encoding='utf-8')
FULL_BLEED = SVG.replace('rx="112" ', '')
SIZES = {'icon-16.png': (16, SVG), 'icon-32.png': (32, SVG), 'icon-180.png': (180, FULL_BLEED),
         'icon-192.png': (192, SVG), 'icon-512.png': (512, SVG)}


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, (size, svg) in SIZES.items():
            page = browser.new_page(viewport={'width': size, 'height': size})
            page.set_content(f'<body style="margin:0">{svg.replace("<svg ", f"<svg width=\"{size}\" height=\"{size}\" ", 1)}</body>')
            page.screenshot(path=str(STATIC / name), omit_background=True)
            page.close()
        browser.close()


if __name__ == '__main__':
    main()
