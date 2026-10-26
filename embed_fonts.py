"""Embed handwriting fonts that are not on Google Fonts into web/handwriting_studio.html as data URIs.

    python embed_fonts.py           # before publishing the online page
    python embed_fonts.py --strip   # before committing: the font must not go into the public repo

The block between the embedded-fonts markers is replaced, or emptied with --strip.
"""
import base64, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "handwriting_studio.html")
EMBED = {  # CSS family name -> font file in ./fonts
    "Arslan Wessam B": "ArslanWessamB.ttf",
}
START, END = "<!-- embedded-fonts-start -->", "<!-- embedded-fonts-end -->"

strip = "--strip" in sys.argv
css = []
for family, fname in ([] if strip else EMBED.items()):
    with open(os.path.join(HERE, "fonts", fname), "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    css.append(f'@font-face{{font-family:"{family}";src:url(data:font/ttf;base64,{b64}) format("truetype");font-display:swap;}}')
block = START + "\n" + (("<style>" + "\n".join(css) + "</style>\n") if css else "") + END

html = open(PAGE, encoding="utf-8").read()
new = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda m: block, html, flags=re.S)
open(PAGE, "w", encoding="utf-8", newline="\n").write(new)
print(("stripped the embedded fonts" if strip else f"embedded {len(EMBED)} font(s)") + f"; page is now {len(new) / 1024:.0f} KB")
