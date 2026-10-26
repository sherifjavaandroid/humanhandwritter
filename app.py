"""Local web app for the handwriting generator.

    python app.py            # opens http://127.0.0.1:8765 in your browser
    python app.py --port 9000 --no-browser

Uses only the Python standard library plus Pillow/numpy (already needed by handwriting.py).
"""
import os, io, sys, json, time, uuid, queue, zipfile, argparse, threading, webbrowser, mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from handwriting import Handwriter, DEFAULTS, CAPACITY, available_fonts, save_image

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
OUT_DIR = os.path.join(HERE, "output")
SAMPLE = os.path.join(HERE, "sample_text.txt")
MAX_TEXT = 40000
MAX_PAGES = 20

JOBS = {}
JOBS_LOCK = threading.Lock()
WORK = queue.Queue()


def clean_opts(body):
    """Validate the JSON from the browser and turn it into Handwriter options."""
    def num(key, lo, hi, cast=float):
        try:
            v = cast(body.get(key, DEFAULTS.get(key)))
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if not (lo <= v <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return v

    text = str(body.get("text", "")).strip("\n")
    if not text.strip():
        raise ValueError("write some text first")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text is too long (max {MAX_TEXT} characters)")
    font = str(body.get("font", DEFAULTS["font"]))
    if font not in available_fonts():
        raise ValueError("unknown handwriting style")
    ink = str(body.get("ink", DEFAULTS["ink"]))
    if len(ink) != 7 or ink[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in ink[1:]):
        raise ValueError("ink colour must look like #1e349e")
    fmt = body.get("format", "jpg")
    if fmt not in ("jpg", "png"):
        raise ValueError("format must be jpg or png")
    return dict(text=text, pages=num("pages", 0, MAX_PAGES, int), gap=num("gap", 0, 3, int),
                seed=num("seed", -10 ** 9, 10 ** 9, int), mess=num("mess", 0, 1), font=font,
                size=num("size", 0.35, 0.8), narrow=num("narrow", 0.6, 1.2), ink=ink,
                crop="auto" if body.get("crop", True) else "full", tidy=bool(body.get("tidy", True)),
                fit=bool(body.get("fit", True))), fmt


JOB_TTL = 3 * 3600   # generated pages are kept this long


def janitor():
    """Forget finished jobs and delete their files after JOB_TTL."""
    import shutil
    while True:
        time.sleep(600)
        now = time.time()
        with JOBS_LOCK:
            old = [j for j, job in JOBS.items() if job.get("finished") and now - job["finished"] > JOB_TTL]
            for j in old:
                JOBS.pop(j, None)
        for j in old:
            shutil.rmtree(os.path.join(OUT_DIR, j), ignore_errors=True)
        for name in os.listdir(OUT_DIR):
            p = os.path.join(OUT_DIR, name)
            if os.path.isdir(p) and name not in JOBS and now - os.path.getmtime(p) > JOB_TTL:
                shutil.rmtree(p, ignore_errors=True)


def worker():
    while True:
        job_id = WORK.get()
        job = JOBS[job_id]
        job["status"] = "running"
        job["started"] = time.time()
        try:
            hw = Handwriter(**job["opts"])
            pages = hw.layout()
            job["total"] = len(pages)
            job["lines"] = [len(p) for p in pages]
            job["warnings"] = hw.warnings
            job_dir = os.path.join(OUT_DIR, job_id)
            os.makedirs(job_dir, exist_ok=True)

            def progress(frac, msg):
                job["progress"] = round(frac, 3)
                job["message"] = msg

            for i, img in hw.iter_pages(progress):
                name = f"page_{i + 1:02d}.{job['format']}"
                save_image(img, os.path.join(job_dir, name))
                job["files"].append({"name": name, "url": f"/output/{job_id}/{name}", "size": list(img.size)})
            job["status"] = "done"
            job["progress"] = 1.0
            job["message"] = "done"
        except Exception as e:  # report to the browser instead of killing the worker
            job["status"] = "error"
            job["error"] = str(e)
        finally:
            job["finished"] = time.time()


class Handler(BaseHTTPRequestHandler):
    server_version = "HandwritingStudio/1.0"

    def log_message(self, fmt, *args):
        if "/api/jobs/" not in self.path:
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    # ---- helpers
    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path, download_name=None):
        if not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def safe_output_path(self, rel):
        full = os.path.abspath(os.path.join(OUT_DIR, rel))
        if not full.startswith(os.path.abspath(OUT_DIR) + os.sep):
            return None
        return full

    # ---- routes
    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(WEB_DIR, "index.html"))
        if path == "/favicon.ico":
            svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#f6f1e7"/>'
                   '<path d="M8 20h48M8 32h48M8 44h48" stroke="#c9d6ef" stroke-width="2"/><path d="M18 4v56" stroke="#e05a68" stroke-width="2"/>'
                   '<path d="M24 40c4-10 8-14 10-10s0 10 4 8 6-8 10-10" fill="none" stroke="#1e349e" stroke-width="3.5" stroke-linecap="round"/></svg>')
            data = svg.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/api/config":
            sample = DEFAULTS["text"]
            if os.path.exists(SAMPLE):
                with open(SAMPLE, encoding="utf-8") as fh:
                    sample = fh.read()
            return self.send_json({"fonts": available_fonts(), "defaults": DEFAULTS, "sample_text": sample,
                                   "capacity": CAPACITY, "max_pages": MAX_PAGES})
        if path.startswith("/api/jobs/"):
            job = JOBS.get(path.rsplit("/", 1)[1])
            if not job:
                return self.send_json({"error": "no such job"}, 404)
            return self.send_json({k: v for k, v in job.items() if k != "opts"})
        if path.startswith("/api/zip/"):
            job_id = path.rsplit("/", 1)[1]
            job = JOBS.get(job_id)
            if not job or job["status"] != "done":
                return self.send_json({"error": "job not finished"}, 404)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for f in job["files"]:
                    z.write(os.path.join(OUT_DIR, job_id, f["name"]), f["name"])
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="handwritten_pages_{job_id[:8]}.zip"')
            self.end_headers()
            return self.wfile.write(data)
        if path.startswith("/output/"):
            full = self.safe_output_path(path[len("/output/"):])
            if not full:
                return self.send_json({"error": "bad path"}, 400)
            return self.send_file(full)
        if path.startswith("/web/"):
            full = os.path.abspath(os.path.join(WEB_DIR, path[len("/web/"):]))
            if full.startswith(os.path.abspath(WEB_DIR) + os.sep):
                return self.send_file(full)
        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/generate":
            return self.send_json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            opts, fmt = clean_opts(body)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        job_id = uuid.uuid4().hex
        job = dict(id=job_id, status="queued", progress=0.0, message="waiting for the pen", files=[],
                   total=None, lines=[], warnings=[], error=None, format=fmt, opts=opts, created=time.time(),
                   queued_ahead=WORK.qsize())
        with JOBS_LOCK:
            JOBS[job_id] = job
        WORK.put(job_id)
        return self.send_json({"id": job_id, "queued_ahead": job["queued_ahead"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Handwriting Studio running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
