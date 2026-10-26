"""Command-line front end for handwriting.py.

  python make_handwriting.py --text "hey how r u" --out note.jpg
  python make_handwriting.py --file notes.txt --pages 6 --outdir result --gap 1 --seed 3
"""
import os, argparse
from handwriting import Handwriter, DEFAULTS, CAPACITY, available_fonts, available_arabic_fonts, save_image

HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--text", default=DEFAULTS["text"])
ap.add_argument("--file", help="text file to write (overrides --text)")
ap.add_argument("--out", default=os.path.join(HERE, "handwritten.jpg"), help="single-page output (.jpg or .png)")
ap.add_argument("--outdir", help="folder for multi-page output (page_01.jpg, page_02.jpg, ...)")
ap.add_argument("--pages", type=int, default=1, help="spread the text evenly over this many pages (0 = as many as needed)")
ap.add_argument("--no-fit", action="store_true", help="keep the letter size as given instead of growing/shrinking it to fill the pages")
ap.add_argument("--gap", type=int, default=0, help="empty ruled lines left between numbered items")
ap.add_argument("--seed", type=int, default=DEFAULTS["seed"], help="a different seed gives a different hand")
ap.add_argument("--mess", type=float, default=DEFAULTS["mess"], help="0 = neat, 1 = very messy")
ap.add_argument("--font", default=DEFAULTS["font"], help="one of: " + ", ".join(available_fonts()) + " or a .ttf path")
ap.add_argument("--arabic-font", default=DEFAULTS["arabic_font"], help="Arabic hand, one of: " + ", ".join(available_arabic_fonts()))
ap.add_argument("--dir", choices=["auto", "ltr", "rtl"], default="auto", help="page direction: rtl puts the margin and coil on the right (auto: follows the text)")
ap.add_argument("--size", type=float, default=DEFAULTS["size"], help="capital-letter height as a fraction of the line gap")
ap.add_argument("--narrow", type=float, default=DEFAULTS["narrow"], help="horizontal squeeze of the letters")
ap.add_argument("--ink", default=DEFAULTS["ink"], help="ink colour as hex, e.g. #1e349e (blue) or #1c1c22 (black)")
ap.add_argument("--crop", choices=["auto", "full"], default="auto", help="auto: a short single-page note gets a closer photo")
ap.add_argument("--format", choices=["jpg", "png"], default="jpg", help="file type for multi-page output")
ap.add_argument("--raw", action="store_true", help="keep the input as is: blank lines become empty ruled lines, no definition splitting")
args = ap.parse_args()

text = args.text
if args.file:
    with open(args.file, encoding="utf-8") as fh:
        text = fh.read()

hw = Handwriter(text=text, pages=args.pages, gap=args.gap, seed=args.seed, mess=args.mess, font=args.font,
                size=args.size, narrow=args.narrow, ink=args.ink, crop=args.crop, tidy=not args.raw, fit=not args.no_fit,
                arabic_font=args.arabic_font, dir=args.dir)
pages = hw.layout()
for w in hw.warnings:
    print("warning:", w)
print("fonts:", ", ".join(os.path.basename(p) for p in hw.font_files), f"| letter size {hw.size:.2f}")

if len(pages) == 1 and not args.outdir:
    photo = hw.render_page(pages[0], 0)
    save_image(photo, args.out)
    print(f"lines written: {len(pages[0])} of {CAPACITY}")
    print("saved:", args.out, photo.size)
else:
    outdir = args.outdir or os.path.join(HERE, "pages")
    os.makedirs(outdir, exist_ok=True)
    for i, photo in hw.iter_pages():
        path = os.path.join(outdir, f"page_{i + 1:02d}.{args.format}")
        save_image(photo, path)
        print(f"page {i + 1}: {len(pages[i])} lines of {CAPACITY} -> {path}")
