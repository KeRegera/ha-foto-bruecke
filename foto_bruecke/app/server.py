"""Foto-Brücke – Home-Assistant-App (Ingress).

Android lädt Fotos/Videos hoch, das iPhone sichert sie mit Aufnahmedatum und Ort.
Speicherort: /media/foto-bruecke/<JJJJ-MM-TT>/<Dateiname>
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web

import meta as metalib

try:
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        HEIF_OK = True
    except Exception:  # noqa: BLE001
        HEIF_OK = False
except Exception:  # noqa: BLE001
    Image = None
    HEIF_OK = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("foto-bruecke")

OPTIONS_FILE = os.environ.get("FB_OPTIONS", "/data/options.json")
ROOT = os.environ.get("FB_ROOT", "/media/foto-bruecke")
APP_DIR = os.path.join(ROOT, ".app")
META_DIR = os.path.join(APP_DIR, "meta")
THUMB_DIR = os.path.join(APP_DIR, "thumbs")
TMP_DIR = os.path.join(APP_DIR, "tmp")
CACHE_DIR = os.environ.get("FB_CACHE", "/tmp/foto-bruecke-cache")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORT = int(os.environ.get("FB_PORT", "8099"))

ALLOWED_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".gif", ".dng",
               ".mp4", ".mov", ".m4v", ".3gp", ".webm"}
ID_RE = re.compile(r"^[0-9a-f]{16}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_options() -> dict:
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


OPTIONS = load_options()


def get_tz() -> ZoneInfo:
    for name in (OPTIONS.get("timezone"), os.environ.get("TZ"), "Europe/Berlin"):
        if name:
            try:
                return ZoneInfo(name)
            except Exception:  # noqa: BLE001
                log.warning("Unbekannte Zeitzone: %s", name)
    return ZoneInfo("UTC")


TZ = get_tz()
for d in (ROOT, META_DIR, THUMB_DIR, TMP_DIR, CACHE_DIR):
    os.makedirs(d, exist_ok=True)

_ios_locks: dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------- Hilfsfunktionen
def safe_name(name: str) -> str:
    name = os.path.basename(name or "foto")
    name = re.sub(r"[^\w.\- ()]+", "_", name, flags=re.UNICODE).strip(" .") or "foto"
    return name[:120]


def meta_path(item_id: str) -> str:
    return os.path.join(META_DIR, item_id + ".json")


def load_item(item_id: str) -> dict | None:
    if not ID_RE.match(item_id):
        return None
    try:
        with open(meta_path(item_id), encoding="utf-8") as f:
            item = json.load(f)
    except (OSError, ValueError):
        return None
    if not os.path.exists(os.path.join(ROOT, item["path"])):
        return None
    return item


def save_item(item: dict) -> None:
    tmp = meta_path(item["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(item, f, ensure_ascii=False, indent=1)
    os.replace(tmp, meta_path(item["id"]))


def all_items() -> list[dict]:
    items = []
    for fn in os.listdir(META_DIR):
        if fn.endswith(".json"):
            item = load_item(fn[:-5])
            if item:
                items.append(item)
    return items


def public(item: dict) -> dict:
    keys = ("id", "name", "kind", "taken", "taken_source", "lat", "lon", "alt", "size",
            "uploaded", "day", "camera", "duration", "thumb", "warnings")
    return {k: item.get(k) for k in keys}


def make_thumbs(src: str, item_id: str, kind: str) -> bool:
    """Erzeugt <id>.jpg (Raster) und <id>_p.jpg (Vorschau, 1600 px)."""
    small = os.path.join(THUMB_DIR, item_id + ".jpg")
    big = os.path.join(THUMB_DIR, item_id + "_p.jpg")
    frame = None
    try:
        if kind == "video":
            frame = os.path.join(TMP_DIR, item_id + "_frame.jpg")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", src,
                            "-frames:v", "1", "-vf", "scale='min(1600,iw)':-2", frame],
                           timeout=120, check=False)
            if not os.path.exists(frame):
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                                "-frames:v", "1", "-vf", "scale='min(1600,iw)':-2", frame],
                               timeout=120, check=False)
            src_img = frame
        else:
            src_img = src
        if Image is None:
            return False
        try:
            im = Image.open(src_img)
        except Exception:  # noqa: BLE001  (z. B. HEIC ohne pillow-heif)
            prev = os.path.join(TMP_DIR, item_id + "_prev.jpg")
            for tag in ("PreviewImage", "JpgFromRaw", "ThumbnailImage"):
                with open(prev, "wb") as f:
                    subprocess.run(["exiftool", "-b", f"-{tag}", src_img], stdout=f, timeout=60)
                if os.path.getsize(prev) > 1000:
                    break
            im = Image.open(prev)
        im = ImageOps.exif_transpose(im).convert("RGB")
        b = im.copy()
        b.thumbnail((1600, 1600))
        b.save(big, "JPEG", quality=85)
        s = im.copy()
        s.thumbnail((420, 420))
        s.save(small, "JPEG", quality=78)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Vorschau fehlgeschlagen für %s: %s", src, e)
        return False
    finally:
        for p in (frame, os.path.join(TMP_DIR, item_id + "_prev.jpg")):
            if p and os.path.exists(p):
                os.remove(p)


def remove_item(item: dict) -> None:
    for p in (os.path.join(ROOT, item["path"]),
              os.path.join(THUMB_DIR, item["id"] + ".jpg"),
              os.path.join(THUMB_DIR, item["id"] + "_p.jpg"),
              meta_path(item["id"])):
        if os.path.exists(p):
            os.remove(p)
    for fn in os.listdir(CACHE_DIR):
        if fn.startswith(item["id"]):
            os.remove(os.path.join(CACHE_DIR, fn))
    day_dir = os.path.join(ROOT, item["day"])
    if os.path.isdir(day_dir) and not os.listdir(day_dir):
        os.rmdir(day_dir)


def ios_cache_path(item: dict) -> str:
    ext = os.path.splitext(item["path"])[1].lower()
    return os.path.join(CACHE_DIR, f"{item['id']}_ios{ext}")


async def ensure_ios_copy(item: dict) -> str:
    dst = ios_cache_path(item)
    if os.path.exists(dst):
        return dst
    lock = _ios_locks.setdefault(item["id"], asyncio.Lock())
    async with lock:
        if not os.path.exists(dst):
            await asyncio.to_thread(metalib.make_ios_copy, os.path.join(ROOT, item["path"]), dst, item)
    return dst


# ---------------------------------------------------------------- HTTP-Handler
async def index(_req: web.Request) -> web.StreamResponse:
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"),
                            headers={"Cache-Control": "no-cache"})


async def api_items(_req: web.Request) -> web.Response:
    items = sorted(all_items(), key=lambda i: (i.get("taken") or i["uploaded"]), reverse=True)
    return web.json_response({"items": [public(i) for i in items], "tz": str(TZ),
                              "keep_days": OPTIONS.get("keep_days", 0)})


async def api_upload(req: web.Request) -> web.Response:
    reader = await req.multipart()
    client_mtime = None
    item = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "lastModified":
            try:
                client_mtime = int(float(await part.text()))
            except ValueError:
                client_mtime = None
            continue
        if part.name != "file":
            await part.release()
            continue

        name = safe_name(part.filename or "foto.jpg")
        ext = os.path.splitext(name)[1].lower()
        if ext not in ALLOWED_EXT:
            raise web.HTTPBadRequest(text=f"Dateityp {ext or '?'} wird nicht unterstützt")
        tmp = os.path.join(TMP_DIR, f"up_{os.getpid()}_{time.time_ns()}{ext}")
        h = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as f:
                while True:
                    chunk = await part.read_chunk(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
                    size += len(chunk)
                    f.write(chunk)
            item_id = h.hexdigest()[:16]
            existing = load_item(item_id)
            if existing:
                os.remove(tmp)
                return web.json_response({"duplicate": True, "item": public(existing)})

            m = await asyncio.to_thread(metalib.read_meta, tmp, name, TZ, client_mtime)
            now = datetime.now(TZ)
            taken = datetime.fromisoformat(m["taken"]) if m["taken"] else None
            day = (taken or now).strftime("%Y-%m-%d")
            day_dir = os.path.join(ROOT, day)
            os.makedirs(day_dir, exist_ok=True)
            final_name = name
            base, ex = os.path.splitext(name)
            n = 1
            while os.path.exists(os.path.join(day_dir, final_name)):
                n += 1
                final_name = f"{base}_{n}{ex}"
            final = os.path.join(day_dir, final_name)
            shutil.move(tmp, final)
            # Dateizeit = Aufnahmezeit (praktisch beim Durchsuchen per SMB)
            if taken:
                os.utime(final, (taken.timestamp(), taken.timestamp()))

            warnings = []
            if m["lat"] is None:
                warnings.append("ort")
            if m["taken_source"] in (None, "Dateidatum"):
                warnings.append("datum")
            item = {"id": item_id, "name": name, "path": os.path.join(day, final_name),
                    "size": size, "uploaded": now.isoformat(), "day": day,
                    "warnings": warnings, **m}
            item["thumb"] = await asyncio.to_thread(make_thumbs, final, item_id, m["kind"])
            save_item(item)
            log.info("Hochgeladen: %s (%s, Ort: %s)", final_name, m["taken"],
                     "ja" if m["lat"] is not None else "nein")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if item is None:
        raise web.HTTPBadRequest(text="Keine Datei erhalten")
    return web.json_response({"duplicate": False, "item": public(item)})


def _get_item(req: web.Request) -> dict:
    item = load_item(req.match_info["id"])
    if not item:
        raise web.HTTPNotFound(text="Nicht gefunden")
    return item


async def api_thumb(req: web.Request) -> web.StreamResponse:
    item = _get_item(req)
    suffix = "_p.jpg" if req.query.get("size") == "big" else ".jpg"
    p = os.path.join(THUMB_DIR, item["id"] + suffix)
    if not os.path.exists(p):
        raise web.HTTPNotFound()
    return web.FileResponse(p, headers={"Cache-Control": "private, max-age=31536000, immutable"})


def _disposition(name: str, inline: bool) -> str:
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "_")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


async def api_original(req: web.Request) -> web.StreamResponse:
    item = _get_item(req)
    return web.FileResponse(os.path.join(ROOT, item["path"]), headers={
        "Content-Disposition": _disposition(item["name"], req.query.get("inline") == "1"),
        "Content-Type": item.get("mime") or "application/octet-stream"})


async def api_ios(req: web.Request) -> web.StreamResponse:
    item = _get_item(req)
    p = await ensure_ios_copy(item)
    return web.FileResponse(p, headers={
        "Content-Disposition": _disposition(item["name"], False),
        "Content-Type": item.get("mime") or "application/octet-stream",
        "Cache-Control": "private, max-age=3600"})


async def api_zip(req: web.Request) -> web.StreamResponse:
    day = req.match_info["day"]
    if not DAY_RE.match(day):
        raise web.HTTPBadRequest()
    items = [i for i in all_items() if i["day"] == day]
    if not items:
        raise web.HTTPNotFound()
    resp = web.StreamResponse(headers={
        "Content-Type": "application/zip",
        "Content-Disposition": _disposition(f"Fotos_{day}.zip", False)})
    await resp.prepare(req)
    zpath = os.path.join(TMP_DIR, f"zip_{time.time_ns()}.zip")
    try:
        paths = [(await ensure_ios_copy(i), i["name"]) for i in items]

        def build():
            used = set()
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                for p, n in paths:
                    arc, k = n, 1
                    while arc in used:
                        k += 1
                        b, e = os.path.splitext(n)
                        arc = f"{b}_{k}{e}"
                    used.add(arc)
                    z.write(p, arc)
        await asyncio.to_thread(build)
        with open(zpath, "rb") as f:
            while chunk := f.read(1024 * 1024):
                await resp.write(chunk)
    finally:
        if os.path.exists(zpath):
            os.remove(zpath)
    await resp.write_eof()
    return resp


async def api_delete(req: web.Request) -> web.Response:
    data = await req.json()
    n = 0
    for item_id in data.get("ids", []):
        item = load_item(str(item_id))
        if item:
            remove_item(item)
            n += 1
    return web.json_response({"deleted": n})


# ---------------------------------------------------------------- Hintergrund
async def housekeeping(_app: web.Application):
    async def loop():
        while True:
            try:
                keep = int(OPTIONS.get("keep_days") or 0)
                if keep > 0:
                    limit = datetime.now(TZ) - timedelta(days=keep)
                    for item in all_items():
                        if datetime.fromisoformat(item["uploaded"]) < limit:
                            log.info("Aufbewahrung abgelaufen, lösche %s", item["path"])
                            remove_item(item)
                cutoff = time.time() - 2 * 86400
                for d in (CACHE_DIR, TMP_DIR):
                    for fn in os.listdir(d):
                        p = os.path.join(d, fn)
                        # ctime: Kopien tragen als mtime die Aufnahmezeit
                        if os.path.isfile(p) and os.stat(p).st_ctime < cutoff:
                            os.remove(p)
            except Exception as e:  # noqa: BLE001
                log.warning("Aufräumen fehlgeschlagen: %s", e)
            await asyncio.sleep(6 * 3600)

    task = asyncio.create_task(loop())
    yield
    task.cancel()


@web.middleware
async def no_index(req: web.Request, handler):
    resp = await handler(req)
    resp.headers.setdefault("X-Robots-Tag", "noindex")
    return resp


def make_app() -> web.Application:
    app = web.Application(client_max_size=4 * 1024 ** 3, middlewares=[no_index])
    app.router.add_get("/", index)
    app.router.add_get("/api/items", api_items)
    app.router.add_post("/api/upload", api_upload)
    app.router.add_get("/api/thumb/{id}", api_thumb)
    app.router.add_get("/api/original/{id}", api_original)
    app.router.add_get("/api/ios/{id}", api_ios)
    app.router.add_get("/api/zip/{day}", api_zip)
    app.router.add_post("/api/delete", api_delete)
    app.router.add_static("/static", STATIC_DIR)
    app.cleanup_ctx.append(housekeeping)
    return app


if __name__ == "__main__":
    log.info("Foto-Brücke startet – Speicher: %s, Zeitzone: %s, HEIC-Vorschau: %s",
             ROOT, TZ, "ja" if HEIF_OK else "über Vorschaubild")
    web.run_app(make_app(), host="0.0.0.0", port=PORT, access_log=None)
