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
from immich import Immich, UUID_RE

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
APP_PORT = int(os.environ.get("FB_APP_PORT", "8098"))        # Upload-Port für die Android-App
DATA_DIR = os.environ.get("FB_DATA", "/data")

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


def get_token() -> str:
    """Upload-Schlüssel für die Android-App (Option oder automatisch erzeugt)."""
    if OPTIONS.get("app_token"):
        return str(OPTIONS["app_token"]).strip()
    path = os.path.join(DATA_DIR, "app_token")
    try:
        with open(path, encoding="utf-8") as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    import secrets
    tok = secrets.token_urlsafe(24)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(tok)
    except OSError as e:
        log.warning("Token konnte nicht gespeichert werden: %s", e)
    return tok


APP_TOKEN = get_token()
for d in (ROOT, META_DIR, THUMB_DIR, TMP_DIR, CACHE_DIR):
    os.makedirs(d, exist_ok=True)

_ios_locks: dict[str, asyncio.Lock] = {}


# ---------------------------------------------------------------- Hilfsfunktionen
def safe_name(name: str) -> str:
    name = os.path.basename(name or "foto")
    name = re.sub(r"[^\w.\- ()]+", "_", name, flags=re.UNICODE).strip(" .") or "foto"
    return name[:120]


_EXT_MAP = {"jpeg": ".jpg", "jpg": ".jpg", "heic": ".heic", "heif": ".heif", "png": ".png",
            "webp": ".webp", "gif": ".gif", "dng": ".dng", "mp4": ".mp4", "mov": ".mov",
            "m4v": ".m4v", "3gp": ".3gp", "webm": ".webm", "avif": ".heif"}


def sniff_ext(path: str) -> str | None:
    """Dateityp anhand des Inhalts (exiftool), unabhängig vom Dateinamen."""
    try:
        out = subprocess.run(["exiftool", "-s3", "-FileTypeExtension", path],
                             capture_output=True, text=True, timeout=60).stdout.strip().lower()
    except Exception:  # noqa: BLE001
        out = ""
    return _EXT_MAP.get(out)


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

        name = safe_name(part.filename or "foto")
        ext = os.path.splitext(name)[1].lower()
        tmp = os.path.join(TMP_DIR, f"up_{os.getpid()}_{time.time_ns()}.bin")
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
            log.info("Upload empfangen: %s (%s Bytes, %s)", name, size,
                     req.headers.get("User-Agent", "?")[:120])
            if size == 0:
                raise web.HTTPBadRequest(text="Leere Datei erhalten")
            # Typ am Inhalt erkennen - Android liefert manchmal Namen ohne Endung
            real_ext = await asyncio.to_thread(sniff_ext, tmp)
            if real_ext not in ALLOWED_EXT:
                log.warning("Abgelehnt: %s (erkannt: %s)", name, real_ext or "unbekannt")
                raise web.HTTPBadRequest(
                    text=f"Kein Foto/Video erkannt ({real_ext or 'unbekannter Typ'})")
            if ext not in ALLOWED_EXT:
                name = name + real_ext            # z. B. "1000012345" -> "1000012345.jpg"
            elif _EXT_MAP.get(ext.lstrip(".")) != real_ext:
                name = os.path.splitext(name)[0] + real_ext   # falsche Endung korrigieren
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
                for d in (CACHE_DIR, TMP_DIR, IMMICH.cache):
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


# ---------------------------------------------------------------- Immich
IMMICH = Immich(OPTIONS.get("immich_url", ""), OPTIONS.get("immich_api_key", ""), CACHE_DIR)


def _immich_id(req: web.Request) -> str:
    if not IMMICH.configured:
        raise web.HTTPNotFound(text="Immich ist nicht eingerichtet")
    asset_id = req.match_info["id"]
    if not UUID_RE.match(asset_id):
        raise web.HTTPBadRequest()
    return asset_id


async def im_status(_req):
    return web.json_response(await IMMICH.status())


async def im_albums(_req):
    if not IMMICH.configured:
        raise web.HTTPNotFound()
    return web.json_response({"albums": await IMMICH.albums()})


async def im_assets(req):
    if not IMMICH.configured:
        raise web.HTTPNotFound()
    album = req.query.get("album") or None
    if album and not UUID_RE.match(album):
        raise web.HTTPBadRequest()
    return web.json_response(await IMMICH.assets(album, req.query.get("page"), req.query.get("cursor")))


async def im_thumb(req):
    asset_id = _immich_id(req)
    size = "preview" if req.query.get("size") == "big" else "thumbnail"
    return await IMMICH.proxy(req, f"/assets/{asset_id}/thumbnail", {"size": size})


async def im_original(req):
    asset_id = _immich_id(req)
    return await IMMICH.proxy(req, f"/assets/{asset_id}/original", cache="private, max-age=3600")


async def im_ios(req):
    asset_id = _immich_id(req)
    path, item = await IMMICH.ios_copy(asset_id)
    return web.FileResponse(path, headers={
        "Content-Disposition": _disposition(item["name"], False),
        "Content-Type": item.get("mime") or "application/octet-stream",
        "Cache-Control": "private, max-age=3600"})


async def immich_ctx(_app):
    await IMMICH.start()
    if IMMICH.configured:
        st = await IMMICH.status()
        log.info("Immich: %s", f"verbunden (v{st['version']})" if st.get("ok") else st.get("error"))
    yield
    await IMMICH.close()


# ---------------------------------------------------------------- Android-App
async def api_android(_req):
    """Nur über Ingress (HA-Login) erreichbar: Daten zum Einrichten der Android-App."""
    return web.json_response({"port": APP_PORT, "token": APP_TOKEN})


def _check_token(req: web.Request) -> None:
    import hmac
    got = req.headers.get("X-Foto-Bruecke-Token", "")
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        got = auth[7:]
    if not hmac.compare_digest(got.strip(), APP_TOKEN):
        raise web.HTTPUnauthorized(text="Falscher Schlüssel – App neu einrichten")


async def app_ping(req):
    _check_token(req)
    return web.json_response({"ok": True, "name": "Foto-Brücke"})


async def app_upload(req):
    _check_token(req)
    return await api_upload(req)


async def app_server_ctx(_app):
    """Zweiter, schlanker Server auf APP_PORT – nur Upload, nur mit Schlüssel."""
    ext = web.Application(client_max_size=4 * 1024 ** 3)
    ext.router.add_get("/api/ping", app_ping)
    ext.router.add_post("/api/upload", app_upload)
    runner = web.AppRunner(ext, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", APP_PORT)
    try:
        await site.start()
        log.info("Upload-Port für die Android-App: %s", APP_PORT)
    except OSError as e:
        log.warning("Upload-Port %s nicht verfügbar: %s", APP_PORT, e)
    yield
    await runner.cleanup()


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
    app.router.add_get("/api/android", api_android)
    app.router.add_get("/api/immich/status", im_status)
    app.router.add_get("/api/immich/albums", im_albums)
    app.router.add_get("/api/immich/assets", im_assets)
    app.router.add_get("/api/immich/thumb/{id}", im_thumb)
    app.router.add_get("/api/immich/original/{id}", im_original)
    app.router.add_get("/api/immich/ios/{id}", im_ios)
    app.router.add_static("/static", STATIC_DIR)
    app.cleanup_ctx.append(housekeeping)
    app.cleanup_ctx.append(immich_ctx)
    app.cleanup_ctx.append(app_server_ctx)
    return app


if __name__ == "__main__":
    log.info("Foto-Brücke startet – Speicher: %s, Zeitzone: %s, HEIC-Vorschau: %s",
             ROOT, TZ, "ja" if HEIF_OK else "über Vorschaubild")
    web.run_app(make_app(), host="0.0.0.0", port=PORT, access_log=None)
