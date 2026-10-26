"""Messy, realistic human handwriting on phone photos of spiral-notebook pages.

Library used by app.py (web UI) and make_handwriting.py (command line).

    hw = Handwriter(text="hey how r u", pages=1, mess=0.7, seed=3)
    hw.layout()
    for index, image in hw.iter_pages():
        image.save(f"page_{index + 1}.jpg", quality=86)

Input format: a line that starts with a label like "26-" is a numbered item. Other lines continue
the previous item on a new ruled line. With tidy=True (default) blank lines are ignored (use gap
for spacing between items) and "Term: definition. Other term: definition" is split so each term
starts a new line, the way people lay out answers in a notebook.

Arabic: words in Arabic script are joined up with an Arabic hand and lines that start in Arabic run
right to left. A page whose text is mostly Arabic is laid out as an Arabic notebook page, with the
margin and the coil on the right. Shaping uses Pillow's raqm layout when the build has it, else the
arabic-reshaper + python-bidi packages with a font that carries presentation forms (Amiri).
"""
import os, re, math, random, unicodedata
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, features

try:
    import arabic_reshaper
    from bidi.algorithm import get_display as _bidi_display
except Exception:   # the packages are optional; Arabic then needs raqm
    arabic_reshaper = None
    _bidi_display = None

HAS_RAQM = bool(features.check("raqm"))
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")
WIN_FONTS = r"C:\Windows\Fonts"

FONT_CHOICES = {
    "kalam":    ("Kalam - neat print, mixed pen weight (closest to the sample)", ["Kalam-Regular.ttf", "Kalam-Light.ttf"]),
    "patrick":  ("Patrick Hand - rounder print", ["PatrickHand-Regular.ttf"]),
    "indie":    ("Indie Flower - loose, bubbly print", ["IndieFlower-Regular.ttf"]),
    "handlee":  ("Handlee - light, quick print", ["Handlee-Regular.ttf"]),
    "homemade": ("Homemade Apple - joined-up cursive", ["HomemadeApple-Regular.ttf"]),
    "inkfree":  ("Ink Free - casual marker (Windows font)", [os.path.join(WIN_FONTS, "Inkfree.ttf")]),
    "segoe":    ("Segoe Print - childlike print (Windows font)", [os.path.join(WIN_FONTS, "segoepr.ttf")]),
}
# (label, files, needs_raqm, thicken): fonts without Arabic presentation-form glyphs can only be shaped by raqm;
# thicken adds a uniform ballpoint stroke (fraction of the letter size) to thin, monoline fonts
ARABIC_FONT_CHOICES = {
    "harmattan":    ("Harmattan - plain school hand, ballpoint (closest to a real pen)", ["Harmattan-Regular.ttf"], True, 0.012),
    "vibes":        ("Vibes - quick, loose marker hand", ["Vibes-Regular.ttf"], True, 0.026),
    "arefruqaa":    ("Aref Ruqaa - calligraphic ruq'ah", ["ArefRuqaa-Regular.ttf", "ArefRuqaa-Bold.ttf"], True, 0.0),
    "amiri":        ("Amiri - naskh, like a careful student", ["Amiri-Regular.ttf"], False, 0.0),
    "scheherazade": ("Scheherazade New - clear naskh", ["ScheherazadeNew-Regular.ttf"], True, 0.0),
}

DEFAULTS = dict(text="hey how r u", pages=1, gap=0, seed=7, mess=0.7, font="kalam", arabic_font="harmattan",
                size=0.54, narrow=0.88, crop="auto", tidy=True, ink="#1e349e", fit=True, dir="auto")
SIZE_MIN, SIZE_MAX = 0.40, 0.74

# page geometry (page pixels)
PW, PH = 1700, 2600          # the sheet
LINE_GAP = 60                 # ruled-line spacing
LINE0 = 34                    # y of the first ruled line
MARGIN_X = 150                # red margin line (from the binding side)
RIGHT_LIMIT = PW - 85         # usable width budget, measured from the binding side
SS = 2                        # glyph supersampling
FIRST_ROW = 2                 # first ruled line that gets written on
RULES = list(range(LINE0, PH, LINE_GAP))
CAPACITY = len(RULES) - 2 - FIRST_ROW
LABEL_X = MARGIN_X + 14
PHOTO_W, PHOTO_H = 1800, 2400

LABEL_RE = re.compile(r"^[\d\u0660-\u0669]+[-)]$")
HEAD_RE = re.compile(r"(?<![\w(])([A-Z][A-Za-z-]*(?: [a-z][A-Za-z-]*)?):(?=\s|$)")
NOT_HEADS = {"Example", "Examples", "Note", "Answer"}
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
LATIN_RE = re.compile(r"[A-Za-z\u00C0-\u024F]")


def _resolve(p):
    return p if os.path.isabs(p) else os.path.join(FONT_DIR, p)


def _usable(p):
    return os.path.exists(p) and os.path.getsize(p) > 20000


def available_fonts():
    """{key: description} for every Latin font choice whose files are present."""
    return {k: v[0] for k, v in FONT_CHOICES.items() if all(_usable(_resolve(p)) for p in v[1])}


def available_arabic_fonts():
    """{key: description} for the Arabic hands this machine can shape."""
    out = {}
    for k, (label, files, needs_raqm, _thicken) in ARABIC_FONT_CHOICES.items():
        if not all(_usable(_resolve(p)) for p in files):
            continue
        if needs_raqm and not HAS_RAQM:
            continue
        if not needs_raqm and not HAS_RAQM and arabic_reshaper is None:
            continue
        out[k] = label
    return out


def font_paths(name):
    if name in FONT_CHOICES:
        paths = [_resolve(p) for p in FONT_CHOICES[name][1]]
        if all(_usable(p) for p in paths):
            return paths
    elif name and _usable(name):
        return [name]
    for key in available_fonts():
        return [_resolve(p) for p in FONT_CHOICES[key][1]]
    raise RuntimeError("no handwriting font found - put a .ttf in ./fonts")


def arabic_font_paths(name):
    """-> (files, thicken) for the requested Arabic hand, or the first one this machine can shape."""
    avail = available_arabic_fonts()
    key = name if name in avail else next(iter(avail), None)
    if key is None:
        return [], 0.0
    return [_resolve(p) for p in ARABIC_FONT_CHOICES[key][1]], ARABIC_FONT_CHOICES[key][3]


def parse_color(c):
    if isinstance(c, (tuple, list)):
        return np.array(c[:3], np.float32) / 255
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255


def is_arabic(s):
    return bool(ARABIC_RE.search(s))


# letters that never join to the letter after them: the pen lifts here, so a word is written as several strokes
NON_JOINERS = set("اآأإٱدذرزوؤةءژڈڑۀەٷ")
MARK_RE = re.compile(r"[ً-ٰٟۖ-ۭ]")


def split_strokes(word):
    """Split an Arabic word into the pen strokes a hand would write it in. Shaping each stroke on its
    own gives the same letter forms, because the cuts sit exactly where letters do not join."""
    segs, cur, last_base = [], "", ""
    for ch in word:
        if MARK_RE.match(ch):
            cur += ch
            continue
        if not ARABIC_RE.match(ch):                 # punctuation or a digit: a stroke of its own
            if cur:
                segs.append(cur); cur = ""
            segs.append(ch); last_base = ""
            continue
        if cur and last_base in NON_JOINERS:
            segs.append(cur); cur = ""
        cur += ch; last_base = ch
    if cur:
        segs.append(cur)
    return segs


def word_type(w):
    """R for Arabic script, L for Latin letters, N for numbers and punctuation."""
    if ARABIC_RE.search(w):
        return "R"
    if LATIN_RE.search(w):
        return "L"
    return "N"


def text_direction(s, default="ltr"):
    """Direction of a piece of text from its first strong letter."""
    for ch in s:
        if ARABIC_RE.match(ch):
            return "rtl"
        if LATIN_RE.match(ch):
            return "ltr"
    return default


def visual_order(words, line_dir):
    """Word-level bidi: the words of one line in left-to-right display order."""
    types = [word_type(w) for w in words]
    base = "R" if line_dir == "rtl" else "L"
    types = [base if t == "N" else t for t in types]
    items = list(zip(words, types))
    if line_dir == "rtl":
        items.reverse()
        flip = "L"
    else:
        flip = "R"
    out, run = [], []
    for w, t in items + [(None, None)]:
        if t == flip:
            run.append(w)
        else:
            if run:
                out.extend(reversed(run)); run = []
            if w is not None:
                out.append(w)
    return out


# ------------------------------------------------------------------ noise helpers
def smooth_noise(h, w, scale, octaves=3, seed=None):
    """Low-frequency value noise in [0,1]."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32); amp = 1.0; tot = 0.0
    for o in range(octaves):
        s = max(2, int(scale / (2 ** o)))
        small = rng.random((h // s + 2, w // s + 2)).astype(np.float32)
        img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        acc += amp * (np.asarray(img, np.float32) / 255.0); tot += amp; amp *= 0.5
    acc /= tot
    acc -= acc.min(); acc /= max(acc.max(), 1e-6)
    return acc


def displacement(h, w, amp, scale, seed):
    dx = (smooth_noise(h, w, scale, 2, seed) - 0.5) * 2 * amp
    dy = (smooth_noise(h, w, scale, 2, seed + 1) - 0.5) * 2 * amp
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = np.clip(xx + dx, 0, w - 1); sy = np.clip(yy + dy, 0, h - 1)
    x0 = np.floor(sx).astype(np.int32); y0 = np.floor(sy).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1); y1 = np.minimum(y0 + 1, h - 1)
    return x0, y0, x1, y1, (sx - x0), (sy - y0)


def warp_with(m, d):
    x0, y0, x1, y1, fx, fy = d
    m = m.astype(np.float32)
    return (m[y0, x0] * (1 - fx) * (1 - fy) + m[y0, x1] * fx * (1 - fy) +
            m[y1, x0] * (1 - fx) * fy + m[y1, x1] * fx * fy)


def elastic_warp(mask, amp, scale, seed):
    h, w = mask.shape
    return warp_with(mask, displacement(h, w, amp, scale, seed))


def find_coeffs(pa, pb):
    m = []
    for p1, p2 in zip(pa, pb):
        m.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0] * p1[0], -p2[0] * p1[1]])
        m.append([0, 0, 0, p1[0], p1[1], 1, -p2[1] * p1[0], -p2[1] * p1[1]])
    return np.linalg.solve(np.array(m, dtype=np.float64), np.array(pb, dtype=np.float64).reshape(8))


def split_definitions(line):
    """'Data: raw facts. Information: processed data' -> two lines, one per term."""
    heads = [m for m in HEAD_RE.finditer(line) if m.group(1) not in NOT_HEADS]
    cuts = [m.start() for m in heads[1:] if m.start() > 0]
    out, prev = [], 0
    for c in cuts:
        out.append(line[prev:c].strip()); prev = c
    out.append(line[prev:].strip())
    return [o for o in out if o]


def parse_items(raw_lines, tidy):
    """-> list of items {label, lines}; with tidy=False a blank line becomes a marker item."""
    items, cur = [], None
    for raw in raw_lines:
        s = raw.rstrip()
        if not s.strip():
            if not tidy:
                items.append({"label": None, "lines": [], "blank": True}); cur = None
            continue
        words = s.split()
        if not s[:1].isspace() and LABEL_RE.match(words[0]):
            cur = {"label": words[0], "lines": [" ".join(words[1:])]}
            items.append(cur)
        elif s[:1].isspace() or (tidy and cur is not None):
            if cur is None:
                cur = {"label": None, "lines": []}; items.append(cur)
            cur["lines"].append(" ".join(words))
        else:
            cur = {"label": None, "lines": [" ".join(words)]}
            items.append(cur)
    if tidy:
        for it in items:
            it["lines"] = [part for ln in it["lines"] for part in (split_definitions(ln) if not is_arabic(ln) else [ln])]
    return items


class Handwriter:
    def __init__(self, **opts):
        o = dict(DEFAULTS)
        o.update({k: v for k, v in opts.items() if v is not None})
        self.o = o
        self.seed = int(o["seed"])
        self.R = random.Random(self.seed)
        self.mess = max(0.0, min(1.0, float(o["mess"])))
        self.narrow = float(o["narrow"])
        self.gap = max(0, int(o["gap"]))
        self.n_pages = max(0, int(o["pages"]))          # 0 = as many pages as the text needs
        self.fit = bool(o["fit"])                        # grow or shrink the letters so the pages come out full
        self.tidy = bool(o["tidy"])
        self.crop = o["crop"]
        self.ink_rgb = parse_color(o["ink"])
        self.font_files = font_paths(o["font"])
        self.ar_files, self.ar_thicken = arabic_font_paths(o["arabic_font"])
        self.shaper = "raqm" if HAS_RAQM else ("reshaper" if arabic_reshaper is not None else None)
        probe = ImageFont.truetype(self.font_files[0], 100)
        self.cap100 = -probe.getbbox("H", anchor="ls")[1]
        self.ar_ratio = 1.0
        if self.ar_files:
            ap = ImageFont.truetype(self.ar_files[0], 100)
            alef100 = -ap.getbbox("\u0627", anchor="ls")[1] or self.cap100
            self.ar_ratio = (self.cap100 * 1.45) / alef100          # Arabic is written big: an alef spans well over a line
        self.size_user = float(o["size"])
        self._set_size(self.size_user)
        self.font_w = [0.62, 0.38] if len(self.font_files) == 2 else None
        self.base_slant = self.R.uniform(0.04, 0.14)
        self.max_pen = 0.0
        self.min_pen = PW
        self.pages = None
        self.items = []
        self.warnings = []
        self.page_dir = "ltr"
        self._shape_cache = {}

    def _set_size(self, size):
        self.size = size
        self.font_size = (size * LINE_GAP) / self.cap100 * 100
        self.fonts_base = [ImageFont.truetype(p, int(round(self.font_size))) for p in self.font_files]
        self.fonts_big = [ImageFont.truetype(p, int(round(self.font_size * SS))) for p in self.font_files]
        ar = self.font_size * self.ar_ratio
        self.ar_base = [ImageFont.truetype(p, int(round(ar))) for p in self.ar_files]
        self.ar_big = [ImageFont.truetype(p, int(round(ar * SS))) for p in self.ar_files]
        self._shape_cache = {}

    # ---------------------------------------------------------------- Arabic shaping
    def _shape(self, word):
        """-> (string to draw, draw kwargs) for an Arabic word."""
        if word in self._shape_cache:
            return self._shape_cache[word]
        if self.shaper == "raqm":
            res = (word, {"direction": "rtl", "language": "ar"})
        elif self.shaper == "reshaper":
            res = (_bidi_display(arabic_reshaper.reshape(word)), {})
        else:
            res = (word, {})
        self._shape_cache[word] = res
        return res

    def _ar_len(self, word, fnt=None):
        fnt = fnt or (self.ar_base[0] if self.ar_base else self.fonts_base[0])
        s, kw = self._shape(word)
        return fnt.getlength(s, **kw)

    # ---------------------------------------------------------------- glyphs
    def _render_glyph(self, text, p, arabic=False):
        """Draw one character (or one joined-up Arabic word) with its own slant, tilt, size, weight
        and wobble. Returns (mask at 1x, origin x, origin y); the origin is the pen position on the baseline."""
        if arabic and self.ar_big:
            fb = self.ar_big[p["font"] % len(self.ar_big)]
            s, kw = self._shape(text)
            sw = int(round(self.ar_thicken * self.font_size * self.ar_ratio * SS * p.get("pen", 1.0)))   # ballpoint line
        else:
            fb = self.fonts_big[p["font"]]
            s, kw = text, {}
            sw = 0
        l, t, r, b = fb.getbbox(s, anchor="ls", stroke_width=sw, **kw)
        if r <= l or b <= t:
            return None
        pad = int(0.35 * self.font_size * SS)
        ox, oy = pad - l, pad - t
        cw, chh = (r - l) + 2 * pad, (b - t) + 2 * pad
        g = Image.new("L", (cw, chh), 0)
        ImageDraw.Draw(g).text((ox, oy), s, font=fb, fill=255, anchor="ls", stroke_width=sw, stroke_fill=255, **kw)
        k = p["shear"]
        g = g.transform(g.size, Image.AFFINE, (1, k, -k * oy, 0, 1, 0), resample=Image.BICUBIC)
        g = g.rotate(p["rot"], resample=Image.BICUBIC, center=(ox, oy))
        sx, sy = p["sx"] * self.narrow, p["sy"]
        nw, nh = max(2, int(cw * sx)), max(2, int(chh * sy))
        g = g.resize((nw, nh), Image.BICUBIC); ox *= sx; oy *= sy
        arr = np.asarray(g.filter(ImageFilter.GaussianBlur(0.75 * SS)), np.float32) / 255
        arr = np.clip((arr - p["weight"]) / 0.22, 0, 1)
        arr = elastic_warp(arr, p["warp"] * SS, max(6, int(p.get("warp_scale", 0.3) * self.font_size * SS)), p["seed"])
        gi = Image.fromarray((arr * 255).astype(np.uint8)).resize((max(1, nw // SS), max(1, nh // SS)), Image.LANCZOS)
        return np.asarray(gi, np.float32) / 255, ox / SS, oy / SS

    @staticmethod
    def _stamp(alpha, g, x, y, intensity):
        h, w = g.shape
        x0, y0 = int(round(x)), int(round(y))
        X0, Y0 = max(x0, 0), max(y0, 0)
        X1, Y1 = min(x0 + w, alpha.shape[1]), min(y0 + h, alpha.shape[0])
        if X1 <= X0 or Y1 <= Y0:
            return
        gg = g[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0] * intensity
        reg = alpha[Y0:Y1, X0:X1]
        alpha[Y0:Y1, X0:X1] = reg + gg - reg * gg

    def _pick_font(self):
        if self.font_w:
            return 0 if self.R.random() < self.font_w[0] else 1
        return self.R.randrange(len(self.fonts_base))

    def _advance(self, prev, ch, fnt):
        if prev is None:
            return fnt.getlength(ch) * self.narrow
        return (fnt.getlength(prev + ch) - fnt.getlength(prev)) * self.narrow

    def _measure(self, word):
        if is_arabic(word):
            # Arabic words come out wider than the font says: stretched strokes plus the ballpoint line
            widest = max(self._ar_len(word, f) for f in (self.ar_base or [self.fonts_base[0]]))
            pen_w = len(split_strokes(word)) * self.ar_thicken * self.font_size * self.ar_ratio
            return (widest * 1.07 + pen_w) * self.narrow * 1.04
        return self.fonts_base[0].getlength(word) * self.narrow * 1.04

    def _glyph_params(self, word_w, big=False):
        R, M = self.R, self.mess
        return dict(font=self._pick_font(),
                    shear=self.base_slant + R.gauss(0, 0.045 * M),
                    rot=R.gauss(0, 2.2 * (0.35 + M)),
                    sx=max(0.8, R.gauss(1.0, 0.045 * (0.5 + M))) * (1.12 if big else 1),
                    sy=max(0.8, R.gauss(1.0, 0.05 * (0.5 + M))) * (1.12 if big else 1),
                    weight=min(0.6, max(0.3, word_w + R.gauss(0, 0.02))),
                    warp=0.7 + 1.7 * M,
                    seed=R.randrange(10 ** 6))

    def _write_line(self, ink, words, start_x, text_x, row, label=None, line_dir="ltr"):
        """Write one physical line of words on ruled line `row`. On an Arabic page the line is
        right-aligned: start_x and text_x are then distances from the right edge."""
        R, MESS = self.R, self.mess
        baseline = RULES[row] - R.uniform(1, 4)
        slope = R.gauss(0, 0.0045 * (0.3 + MESS))
        line_drift = smooth_noise(1, PW, 260, 2, R.randrange(10 ** 6))[0]
        rtl_page = self.page_dir == "rtl"
        vis = visual_order(words, line_dir)
        # plan the words first so a right-aligned line can be placed exactly
        plan = []
        for word in vis:
            arabic = is_arabic(word)
            plan.append((word, arabic))
        # per-word rendering parameters are drawn in reading order for reproducibility
        pieces = []           # (x offset from line start, baseline offset, mask) collected then stamped
        pen = 0.0
        for word, arabic in plan:
            word_dy = R.gauss(0, 1.8 * (0.3 + MESS))
            word_int = R.uniform(0.80, 1.0)
            word_w = R.uniform(0.42, 0.54)
            fi_word = self._pick_font()
            if arabic:
                # the word is written stroke by stroke, the pen lifting at the letters that do not join;
                # every stroke gets its own tilt, size, pressure and wobble, like the Latin letters do
                n_ar = max(1, len(self.ar_base))
                strokes = []
                pen_press = R.uniform(0.85, 1.15)                             # how hard this word is pressed
                for seg in split_strokes(word):
                    # monoline fonts get a uniform ballpoint line instead of the blur-and-threshold weight
                    p = self._glyph_params(0.5 if self.ar_thicken else word_w - 0.05, big=False)
                    p["font"] = (fi_word if R.random() < 0.85 else self._pick_font()) % n_ar
                    p["rot"] *= 1.1
                    p["shear"] = p["shear"] * 0.3 + R.gauss(0, 0.05 * MESS)
                    p["sx"] = max(0.8, R.gauss(1.05, 0.10 * (0.5 + MESS)))   # strokes stretch sideways, like a drawn-out connection
                    p["sy"] = max(0.8, R.gauss(1.0, 0.08 * (0.5 + MESS)))
                    p["warp"] *= 1.1
                    p["warp_scale"] = 0.18                                    # wobble at letter scale, not word scale
                    p["pen"] = pen_press * R.uniform(0.9, 1.1)
                    g = self._render_glyph(seg, p, arabic=True)
                    ar_fnt = self.ar_base[p["font"]] if self.ar_base else self.fonts_base[0]
                    pen_w = self.ar_thicken * self.font_size * self.ar_ratio * p["pen"]   # the ballpoint line widens every stroke a little
                    w = (self._ar_len(seg, ar_fnt) * p["sx"] + pen_w) * self.narrow * R.uniform(0.97, 1.04)
                    strokes.append((g, w, R.gauss(0, 2.0 * (0.3 + MESS)), word_int * R.uniform(0.85, 1.0)))
                word_width = sum(w for _, w, _, _ in strokes)
                x_right = pen + word_width                                    # strokes run right to left inside the word
                for g, w, sdy, inten in strokes:
                    x_left = x_right - w
                    if g is not None:
                        dy = word_dy + sdy + x_left * slope + (line_drift[min(max(int(x_left), 0), PW - 1)] - 0.5) * 3.0
                        pieces.append((x_left, dy, g, inten))
                    x_right = x_left + R.gauss(0, 1.5 * MESS)                 # strokes nearly touch, sometimes overlap
                pen += word_width
                fnt = self.fonts_base[fi_word]
            else:
                prev = None
                fnt = self.fonts_base[fi_word]
                for ch in word:
                    fi = fi_word if R.random() < 0.92 else self._pick_font()
                    fnt = self.fonts_base[fi]
                    p = self._glyph_params(word_w, big=R.random() < 0.05 * MESS)
                    p["font"] = fi
                    g = self._render_glyph(ch, p)
                    dy = (word_dy + R.gauss(0, 1.5 * (0.3 + MESS)) + pen * slope
                          + (line_drift[min(max(int(pen), 0), PW - 1)] - 0.5) * 3.0)
                    if g is not None:
                        pieces.append((pen, dy, g, word_int * R.uniform(0.82, 1.0)))
                    pen += self._advance(prev, ch, fnt) * R.uniform(0.95, 1.07) + R.gauss(0, 1.2 * MESS)
                    prev = ch
            pen += fnt.getlength(" ") * self.narrow * R.uniform(0.95, 1.35)
        width = pen
        # the label sits at the margin; the text starts at text_x (both measured from the binding side)
        if rtl_page:
            line_left = PW - text_x - width
        else:
            line_left = text_x if label else start_x
        for off, dy, (mask, ox, oy), inten in pieces:
            self._stamp(ink, mask, line_left + off - ox, baseline + dy - oy, inten)
        self.max_pen = max(self.max_pen, line_left + width)
        self.min_pen = min(self.min_pen, line_left)
        if label:
            lw = 0.0
            lp = []
            prev = None
            fi_word = self._pick_font()
            word_w = R.uniform(0.42, 0.54)
            word_dy = R.gauss(0, 1.8 * (0.3 + MESS))
            for ch in label:
                p = self._glyph_params(word_w)
                p["font"] = fi_word
                g = self._render_glyph(ch, p)
                if g is not None:
                    lp.append((lw, word_dy + R.gauss(0, 1.5 * (0.3 + MESS)), g))
                lw += self._advance(prev, ch, self.fonts_base[fi_word]) * R.uniform(0.95, 1.07)
                prev = ch
            lx = (PW - start_x - lw) if rtl_page else start_x
            for off, dy, (mask, ox, oy) in lp:
                self._stamp(ink, mask, lx + off - ox, baseline + dy - oy, R.uniform(0.85, 1.0))
            self.max_pen = max(self.max_pen, lx + lw)
            self.min_pen = min(self.min_pen, lx)

    # ---------------------------------------------------------------- layout
    def _wrap_item(self, item):
        """-> list of physical lines: dict(words, start_x, text_x, label, dir); None = empty ruled line."""
        if item.get("blank"):
            return [None]
        label = item["label"]
        text_x = LABEL_X + self._measure(label) + 24 if label else LABEL_X
        out, first = [], True
        space = self._measure(" ")
        for ln in item["lines"]:
            d = text_direction(ln, self.page_dir)
            cur, cur_w = [], 0.0
            for w in ln.split():
                ww = self._measure(w)
                if cur and cur_w + space + ww > (RIGHT_LIMIT - text_x) + self.Rl.uniform(-30, 25):
                    out.append(dict(words=cur, start_x=LABEL_X if (first and label) else text_x,
                                    text_x=text_x, label=label if first else None, dir=d))
                    first = False; cur, cur_w = [w], ww
                else:
                    cur_w += (space if cur else 0) + ww; cur.append(w)
            if cur:
                out.append(dict(words=cur, start_x=LABEL_X if (first and label) else text_x,
                                text_x=text_x, label=label if first else None, dir=d))
                first = False
        return out

    def _paginate(self, item_lines):
        """Split items over n_pages so each page carries a similar number of rows."""
        gap, n_pages = self.gap, self.n_pages
        weights = [len(ls) + (gap if ls and ls[0] is not None else 0) for ls in item_lines]
        total = sum(weights)
        prefix = np.cumsum([0] + weights)
        bounds, last = [], 0
        for k in range(1, n_pages):
            lo, hi = last + 1, len(item_lines) - (n_pages - k)
            if lo > hi:
                break
            ideal = total * k / n_pages
            j = min(range(lo, hi + 1), key=lambda i: abs(prefix[i] - ideal))
            bounds.append(j); last = j
        groups = [item_lines[a:b] for a, b in zip([0] + bounds, bounds + [len(item_lines)])]
        pages = []
        for grp in groups:
            rows = []
            for ls in grp:
                if rows and ls and ls[0] is not None:
                    rows.extend([None] * gap)
                rows.extend(ls)
            while rows and rows[-1] is None:
                rows.pop()
            pages.append(rows)
        return pages

    def _greedy(self, item_lines):
        """Fill each page to the last line before starting the next one."""
        pages, rows = [], []
        for ls in item_lines:
            if not ls:
                continue
            if rows and ls[0] is not None:
                rows.extend([None] * self.gap)
            for ln in ls:
                if len(rows) >= CAPACITY:
                    while rows and rows[-1] is None:
                        rows.pop()
                    pages.append(rows); rows = []
                    if ln is None:
                        continue
                rows.append(ln)
        while rows and rows[-1] is None:
            rows.pop()
        if rows or not pages:
            pages.append(rows)
        return pages

    def _wrap_all(self):
        self.Rl = random.Random(self.seed + 1)     # same wrapping decisions every time a size is tried
        return [self._wrap_item(it) for it in self.items]

    def _fits(self, size):
        self._set_size(size)
        return all(len(p) <= CAPACITY for p in self._paginate(self._wrap_all()))

    def _rows_at(self, size):
        self._set_size(size)
        return sum(len(ls) + (self.gap if ls and ls[0] is not None else 0) for ls in self._wrap_all())

    def _fit_size(self):
        """Largest letter size whose layout fits the requested pages; shrinks when the text is too long,
        grows (within reason) when the pages would otherwise be left half empty."""
        user, n = self.size_user, self.n_pages
        if self._fits(user):
            lo, hi, best = user, max(user, SIZE_MAX), user
            for _ in range(8):
                mid = (lo + hi) / 2
                if self._fits(mid):
                    best, lo = mid, mid
                else:
                    hi = mid
            # a single short note keeps its size; a multi-page request is meant to be filled
            return best if (n >= 2 or self._rows_at(best) >= 0.6 * n * CAPACITY) else user
        lo, hi, best = SIZE_MIN, user, None
        for _ in range(8):
            mid = (lo + hi) / 2
            if self._fits(mid):
                best, lo = mid, mid
            else:
                hi = mid
        return best if best is not None else SIZE_MIN

    def _fit_layout(self):
        """Pick the letter size, and when needed the spacing between answers, so the requested
        number of pages comes out full. Returns the pages, or None when nothing fits."""
        n, cap, user_gap = self.n_pages, self.n_pages * CAPACITY, self.gap
        size = None
        for g in range(user_gap, -1, -1):               # too long? first drop the empty lines between answers
            self.gap = g
            s = self._fit_size()
            if self._fits(s):
                size = s
                break
        if size is None:
            self.gap = 0
            self._set_size(SIZE_MIN)
            return None
        if self.gap != user_gap:
            self.warnings.append("The empty lines between answers were reduced so the text fits the pages.")
        fill = self._rows_at(size) / cap
        while fill < 0.85 and self.gap < 2 and size >= SIZE_MAX - 0.01:   # letters maxed out and pages still airy: space the answers out
            self.gap += 1
            s2 = self._fit_size()
            f2 = self._rows_at(s2) / cap if self._fits(s2) else 0
            if f2 <= fill:
                self.gap -= 1
                break
            size, fill = s2, f2
        self._set_size(size)
        return self._paginate(self._wrap_all())

    def layout(self):
        """Wrap and paginate the text. Returns the list of pages (rows per page)."""
        raw_text = str(self.o["text"]).replace("\r\n", "\n").rstrip("\n")
        raw_lines = raw_text.split("\n")
        self.items = parse_items(raw_lines, self.tidy)
        self.warnings = []
        d = self.o.get("dir", "auto")
        if d in ("ltr", "rtl"):
            self.page_dir = d
        else:
            ar = len(ARABIC_RE.findall(raw_text)); la = len(LATIN_RE.findall(raw_text))
            self.page_dir = "rtl" if ar > la else "ltr"
        if self.page_dir == "rtl" or any(is_arabic(ln) for it in self.items for ln in it["lines"]):
            if not self.ar_files:
                self.warnings.append("No Arabic hand is available on this machine, so Arabic words are drawn with the Latin font.")
            elif self.shaper is None:
                self.warnings.append("Arabic letters cannot be joined on this machine (install arabic-reshaper and python-bidi).")
        if not self.items:
            self.pages = [[]]
            return self.pages
        if self.n_pages == 0:
            self._set_size(self.size_user)
            pages = self._greedy(self._wrap_all())
        else:
            if self.fit:
                pages = self._fit_layout()
            else:
                self._set_size(self.size_user)
                pages = self._paginate(self._wrap_all())
            if pages is None or any(len(p) > CAPACITY for p in pages):
                pages = self._greedy(self._wrap_all())
                self.warnings.append(f"The text needs {len(pages)} pages even at the smallest letters, "
                                     f"so {len(pages)} pages were written instead of {self.n_pages}.")
        self.pages = pages
        return pages

    # ---------------------------------------------------------------- rendering
    def render_page(self, rows, page_idx, progress=None):
        """Render one page (rows from layout()) and return the photo as a PIL image."""
        R, MESS = self.R, self.mess
        ps = self.seed + 1000 * page_idx
        report = progress or (lambda f, m: None)
        rtl = self.page_dir == "rtl"
        margin_x = PW - MARGIN_X if rtl else MARGIN_X

        # ---- paper
        paper = np.ones((PH, PW, 3), np.float32) * (np.array([251, 251, 249], np.float32) / 255)
        shade = smooth_noise(PH, PW, 520, 3, ps + 11)
        grain = smooth_noise(PH, PW, 4, 2, ps + 12)
        yy, xx = np.mgrid[0:PH, 0:PW].astype(np.float32)
        paper *= (0.90 + 0.09 * shade)[..., None]
        paper *= (0.985 + 0.015 * grain)[..., None]
        light_x = (PW - xx) if rtl else xx
        paper *= (0.93 + 0.08 * (light_x / PW) - 0.03 * (yy / PH))[..., None]
        paper[..., 2] *= 1.01
        pimg = Image.fromarray((np.clip(paper, 0, 1) * 255).astype(np.uint8)).convert("RGBA")
        lines_layer = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lines_layer)
        for i, y in enumerate(RULES):
            wob = smooth_noise(1, PW, 300, 2, ps + 1000 + i)[0]
            ld.line([(x, y + (wob[x] - 0.5) * 2.2) for x in range(0, PW, 6)],
                    fill=(158, 182, 224, R.randint(150, 190)), width=2)
        ld.line([(margin_x, 0), (margin_x + R.uniform(-2, 2), PH)], fill=(236, 96, 108, 175), width=2)
        lines_layer = lines_layer.filter(ImageFilter.GaussianBlur(0.5))
        page = np.asarray(Image.alpha_composite(pimg, lines_layer).convert("RGB"), np.float32) / 255
        report(0.05, "paper ready")

        # ---- writing
        ink = np.zeros((PH, PW), np.float32)
        self.max_pen = 0.0
        self.min_pen = PW
        row = FIRST_ROW
        n = max(1, len(rows))
        for i, ln in enumerate(rows):
            if ln is not None:
                self._write_line(ink, ln["words"], ln["start_x"], ln["text_x"], row, ln["label"], ln.get("dir", "ltr"))
            row += 1
            report(0.05 + 0.7 * (i + 1) / n, f"writing line {i + 1} of {len(rows)}")
        last_row = row

        pressure = 0.62 + 0.38 * smooth_noise(PH, PW, 26, 3, ps + 50)
        streak = 0.86 + 0.14 * smooth_noise(PH, PW, 3, 2, ps + 51)
        alpha = np.clip(ink * pressure * streak, 0, 1)
        alpha = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.45)), np.float32) / 255
        alpha = np.clip(alpha * 1.12, 0, 1)
        spk = Image.new("L", (PW, PH), 0); sd = ImageDraw.Draw(spk)
        for _ in range(int(6 + 14 * MESS)):
            x, y = R.uniform(60, PW - 60), R.uniform(RULES[1], PH - 60)
            r = R.uniform(0.5, 1.4)
            sd.ellipse([x - r, y - r, x + r, y + r], fill=R.randint(50, 160))
        alpha = np.clip(alpha + np.asarray(spk, np.float32) / 255 * 0.7, 0, 1)

        pool = smooth_noise(PH, PW, 60, 2, ps + 52)
        ink_col = np.clip(self.ink_rgb[None, None, :] * (0.78 + 0.4 * pool[..., None]), 0, 1)
        sheet = page * (1 - alpha[..., None] * (1 - ink_col))
        sheet += 0.04 * (np.roll(alpha, 1, axis=0) - alpha)[..., None]
        d = displacement(PH, PW, 3.5, 520, ps + 60)
        sheet = np.stack([warp_with(sheet[..., c], d) for c in range(3)], axis=-1)
        sheet_img = Image.fromarray((np.clip(sheet, 0, 1) * 255).astype(np.uint8))
        report(0.82, "ink dried")

        # ---- scene: desk, notebook, coil (built with the binding on the left, mirrored for Arabic)
        SW, SH = 2100, 2800
        desk = np.ones((SH, SW, 3), np.float32) * (np.array([150, 104, 64], np.float32) / 255)
        grain_v = np.asarray(Image.fromarray((np.random.default_rng(ps + 70).random((SH // 40 + 2, SW // 3 + 2)) * 255).astype(np.uint8))
                             .resize((SW, SH), Image.BICUBIC), np.float32) / 255
        desk *= (0.78 + 0.22 * grain_v + 0.12 * smooth_noise(SH, SW, 300, 2, ps + 71))[..., None]
        scene = Image.fromarray((np.clip(desk, 0, 1) * 255).astype(np.uint8)).convert("RGBA")
        sx, sy = 230, 100
        binding = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        ImageDraw.Draw(binding).rectangle([sx - 130, 0, sx + 2, SH], fill=(22, 18, 16, 235))
        binding = binding.filter(ImageFilter.GaussianBlur(10))
        sh = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle([sx + 6, sy + 8, sx + PW + 6, sy + PH + 8], fill=(0, 0, 0, 120))
        sh = sh.filter(ImageFilter.GaussianBlur(6))
        coil = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        cd = ImageDraw.Draw(coil)
        hy = sy + 48
        while hy < sy + PH:
            cd.rounded_rectangle([sx + 14, hy - 14, sx + 62, hy + 14], radius=11, fill=(70, 62, 58, 110))
            cd.rounded_rectangle([sx + 17, hy - 12, sx + 59, hy + 12], radius=10, fill=(26, 24, 24, 255))
            wire = [(sx + 50, hy - 2), (sx + 20, hy + 6), (sx - 25, hy + 20), (sx - 90, hy + 34)]
            cd.line(wire, fill=(10, 10, 12, 255), width=10, joint="curve")
            cd.line([(x, y - 3) for x, y in wire[1:]], fill=(118, 118, 124, 255), width=2, joint="curve")
            cd.arc([sx - 92, hy + 2, sx - 40, hy + 58], start=60, end=250, fill=(8, 8, 10, 255), width=8)
            hy += 80
        coil = coil.filter(ImageFilter.GaussianBlur(0.8))
        if rtl:
            binding, sh, coil = ImageOps.mirror(binding), ImageOps.mirror(sh), ImageOps.mirror(coil)
            sheet_x = SW - sx - PW
        else:
            sheet_x = sx
        scene.alpha_composite(binding)
        scene.alpha_composite(sh)
        scene.alpha_composite(sheet_img.convert("RGBA"), (sheet_x, sy))
        scene.alpha_composite(coil)

        # ---- camera
        angle = math.radians(R.uniform(-1.6, -0.6)) * (-1 if rtl else 1)
        key = R.uniform(0.008, 0.02)
        cx, cy = SW / 2, SH / 2

        def cam(x, y):
            dx, dy = x - cx, y - cy
            dx *= 1 + key * (dy / (SH / 2))
            return (cx + dx * math.cos(angle) - dy * math.sin(angle), cy + dx * math.sin(angle) + dy * math.cos(angle))

        src = [(0, 0), (SW, 0), (SW, SH), (0, SH)]
        scene = scene.convert("RGB").transform((SW, SH), Image.PERSPECTIVE, find_coeffs([cam(*p) for p in src], src),
                                               resample=Image.BICUBIC)
        W, H = PHOTO_W, PHOTO_H
        crop_x = (SW - 170 - W) if rtl else 170
        photo = scene.crop((crop_x, 150, crop_x + W, 150 + H))
        arr = np.asarray(photo, np.float32) / 255
        pyy, pxx = np.mgrid[0:H, 0:W].astype(np.float32)
        arr *= (1 - 0.28 * (((pxx / W - 0.5) ** 2 + (pyy / H - 0.5) ** 2) * 2.0))[..., None]
        arr *= np.array([1.0, 0.99, 0.965], np.float32)
        arr = (arr - 0.5) * 1.06 + 0.5
        arr += np.random.default_rng(ps + 80).standard_normal(arr.shape).astype(np.float32) * 0.013
        photo = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.55))
        if self.crop == "auto" and len(self.pages or [rows]) == 1 and last_row < 14:
            bottom = int(min(H, max(520, (last_row + 3) * LINE_GAP)))
            if rtl:
                left = int(max(0, min(W - 1000, self.min_pen + sheet_x - crop_x - 380)))
                photo = photo.crop((left, 0, W, bottom))
            else:
                right = int(min(W, max(1000, self.max_pen + sx - 170 + 380)))
                photo = photo.crop((0, 0, right, bottom))
        report(1.0, "photo taken")
        return photo

    def iter_pages(self, progress=None):
        """Yield (page_index, image) for every page; progress(fraction, message) covers the whole job."""
        if self.pages is None:
            self.layout()
        n = len(self.pages)
        for i, rows in enumerate(self.pages):
            def rep(f, m, i=i):
                if progress:
                    progress((i + f) / n, f"page {i + 1} of {n}: {m}")
            yield i, self.render_page(rows, i, rep)

    def render_all(self, progress=None):
        return [img for _, img in self.iter_pages(progress)]


def save_image(img, path):
    if path.lower().endswith((".jpg", ".jpeg")):
        img.save(path, quality=86, subsampling=2)
    else:
        img.save(path)
