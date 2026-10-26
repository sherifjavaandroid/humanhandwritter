# Handwriting Studio

Turns typed notes into photos of a spiral notebook with the notes written out in messy, human-looking blue ballpoint.

## Online version

`web/handwriting_studio.html` is a browser-only port of the renderer (canvas, no server). It is published at
https://claude.ai/artifact/YBgoAjzsesjLsEVi6jPEm3 and can be shared from that page's Share menu. Everything runs on the
viewer's own device, so it needs no hosting.

## Run the web app

```
python app.py
```

It opens http://127.0.0.1:8765/ in your browser. Paste your text, pick the number of pages and a handwriting style, press **Generate pages**, then save each page or download them all as a zip. Generated images are kept in the `output/` folder, one sub-folder per job.

Needs Python 3 with Pillow and numpy, plus the two small Arabic shaping packages:

```
pip install pillow numpy arabic-reshaper python-bidi
```

## Arabic

Arabic text is written joined up, right to left, and a page that is mostly Arabic becomes an Arabic notebook page with the
margin and the coil on the right. English words inside Arabic lines keep their place. Pick the Arabic hand separately from
the Latin one: Arslan Wessam (default, a real handwriting font), Harmattan (a plain school hand drawn with a ballpoint
line), Vibes (quick marker hand), Aref Ruqaa, Amiri and Scheherazade New.

The Arslan Wessam font (`fonts/ArslanWessamB.ttf`, from arfonts.net, licence unknown) is **not in this repository**: it is
git-ignored. Copy it into `fonts/` on every machine that should offer it. For the online page, run `python embed_fonts.py`
to embed it before publishing and `python embed_fonts.py --strip` before committing, so it never lands in the repo. Each word is written stroke by stroke, the pen lifting at letters that do not join, and every
stroke gets its own size, tilt and pressure. On Linux (where Pillow has the raqm layout engine, as on the server) all the
Arabic hands work; on Windows without raqm only Amiri is available, because the other fonts rely on OpenType shaping.

## Command line

```
python make_handwriting.py --text "hey how r u" --out note.jpg
python make_handwriting.py --file sample_text.txt --pages 6 --outdir result --gap 1 --seed 3
python make_handwriting.py --help
```

## From an image

The "From image" tab takes screenshots or photos of typed text (several at once, dropped, pasted or picked). The text is read
out of the images in the browser, numbered lists are turned into `1-` style answers, and the result lands in the text tab for
checking before generating. The local/server page reads with Tesseract.js on the viewer's device (the first use downloads about
15 MB). The online claude.ai version asks Claude to read the image instead, after the viewer allows it.

## Writing the text

- A line that starts with a number like `12-` begins a numbered answer. The number goes in the margin and the following lines of that answer are indented under the text.
- A line without a number continues the answer above it on a new ruled line.
- Blank lines are ignored. Use the "empty lines between answers" setting (`--gap` on the command line) for spacing.
- `Term: definition. Other term: definition` is split so each term starts a new line, like a student would write it. Pass `--raw` on the command line to turn that off.

## Settings

| Setting | What it does |
| --- | --- |
| Pages | The text is spread evenly over this many pages, breaking only between answers. 0 means as many pages as the text needs, each filled to the last line. |
| Fill the pages | On by default. The letters grow or shrink (and, if needed, the spacing between answers changes) so the requested pages come out full instead of leaving empty space at the bottom. `--no-fit` on the command line turns it off. |
| Hand (seed) | Any number. Same seed gives the same handwriting again, a different seed gives a different hand. |
| Messiness | 0 is neat, 1 is very messy. 0.7 matches a quick student hand. |
| Letter size / width | Height of capitals relative to the line spacing, and how squeezed the letters are. |
| Ink colour | Any colour. Blue ballpoint, black, dark blue and red are one click away. |
| Handwriting style | Kalam (closest to the sample), Patrick Hand, Indie Flower, Handlee, Homemade Apple (cursive), plus the Windows fonts Ink Free and Segoe Print. Drop more `.ttf` files in `fonts/` and add them to `FONT_CHOICES` in `handwriting.py`. |

## Files

- `app.py` - local web server (standard library only)
- `web/index.html` - the page
- `handwriting.py` - the renderer (letters, paper, coil, camera)
- `make_handwriting.py` - command-line wrapper
- `fonts/` - handwriting fonts (Google Fonts, open licences)
- `sample_text.txt` - the sample loaded into the editor
