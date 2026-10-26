"""Embed handwriting fonts that are not on Google Fonts into web/handwriting_studio.html as data URIs.

    python embed_fonts.py

Re-run after changing a font in EMBED; the block between the embedded-fonts markers is replaced.
"""
import base64, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "handwriting_studio.html")
EMBED = {  # CSS family name -> font file in ./fonts
    "Arslan Wessam B": "ArslanWessamB.ttf",
}

css = []
for family, fname in EMBED.items():
    with open(os.path.join(HERE, "fonts", fname), "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    css.append(f'@font-face{{font-family:"{family}";src:url(data:font/ttf;base64,{b64}) format("truetype");font-display:swap;}}')
block = "<!-- embedded-fonts-start -->
<!-- embedded-fonts-end -->"

html = open(PAGE, encoding="utf-8").read()
new = re.sub(r"<!-- embedded-fonts-start -->
<!-- embedded-fonts-end -->", lambda m: block, html, flags=re.S)
open(PAGE, "w", encoding="utf-8", newline="\n").write(new)
print(f"embedded {len(EMBED)} font(s); page is now {len(new) / 1024:.0f} KB")
