"""Immich als Quelle: Fotos aus Immich mit Aufnahmedatum und Ort aufs iPhone.

Datum und Ort kommen aus der Immich-Datenbank (also auch nachträgliche Korrekturen
in Immich) und werden in eine Kopie der Originaldatei geschrieben.
Kompatibel mit Immich v1.1xx, v2 und v3.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import aiohttp
from aiohttp import web

import meta as metalib

log = logging.getLogger("foto-bruecke.immich")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
EXT_BY_MIME = {"image/jpeg": ".jpg", "image/heic": ".heic", "image/heif": ".heif", "image/png": ".png",
               "image/webp": ".webp", "image/gif": ".gif", "video/mp4": ".mp4",
               "video/quicktime": ".mov", "video/3gpp": ".3gp", "video/webm": ".webm"}


class Immich:
    def __init__(self, url: str, key: str, cache_dir: str):
        url = (url or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "http://" + url
        if url.endswith("/api"):
            url = url[:-4]
        self.base = url
        self.key = (key or "").strip()
        self.cache = os.path.join(cache_dir, "immich")
        os.makedirs(self.cache, exist_ok=True)
        self.session: aiohttp.ClientSession | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def configured(self) -> bool:
        return bool(self.base and self.key)

    async def start(self):
        self.session = aiohttp.ClientSession(
            headers={"x-api-key": self.key, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=120))

    async def close(self):
        if self.session:
            await self.session.close()

    def _url(self, path: str) -> str:
        return f"{self.base}/api{path}"

    async def _json(self, method: str, path: str, **kw):
        async with self.session.request(method, self._url(path), **kw) as r:
            if r.status >= 400:
                text = (await r.text())[:300]
                raise web.HTTPBadGateway(text=f"Immich {r.status}: {text}")
            return await r.json()

    # ------------------------------------------------------------ Umrechnung
    @staticmethod
    def _parse_utc(v) -> datetime | None:
        if not isinstance(v, str) or not v:
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None

    def taken(self, a: dict) -> datetime | None:
        """Aufnahmezeit mit dem Offset, der am Aufnahmeort galt.

        localDateTime = Ortszeit (als UTC kodiert), dateTimeOriginal/fileCreatedAt = echter Zeitpunkt.
        Die Differenz ist der Zeitzonen-Offset.
        """
        exif = a.get("exifInfo") or {}
        instant = self._parse_utc(exif.get("dateTimeOriginal")) or self._parse_utc(a.get("fileCreatedAt"))
        local = self._parse_utc(a.get("localDateTime"))
        if instant is None and local is None:
            return None
        if instant is None:
            return local.replace(tzinfo=None).replace(tzinfo=timezone.utc)
        if local is None:
            return instant
        diff = (local.replace(tzinfo=None) - instant.astimezone(timezone.utc).replace(tzinfo=None))
        minutes = round(diff.total_seconds() / 900) * 15     # auf 15 min runden
        if abs(minutes) > 14 * 60:
            minutes = 0
        return instant.astimezone(timezone(timedelta(minutes=minutes)))

    @staticmethod
    def _duration(v) -> float | None:
        if isinstance(v, (int, float)):
            return v / 1000           # v3: Millisekunden
        if isinstance(v, str):        # v1/v2: "0:00:12.345000"
            m = re.match(r"(\d+):(\d+):([\d.]+)", v)
            if m:
                return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
        return None

    def to_item(self, a: dict) -> dict:
        exif = a.get("exifInfo") or {}
        t = self.taken(a)
        lat, lon = exif.get("latitude"), exif.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or (lat == 0 and lon == 0):
            lat = lon = None
        local = self._parse_utc(a.get("localDateTime"))
        day = (local or t).strftime("%Y-%m-%d") if (local or t) else "unbekannt"
        kind = "video" if a.get("type") == "VIDEO" else "image"
        dur = self._duration(a.get("duration"))
        return {
            "id": a["id"], "name": a.get("originalFileName") or a["id"], "kind": kind,
            "taken": t.isoformat() if t else None, "taken_source": "Immich",
            "lat": lat, "lon": lon, "alt": None, "day": day,
            "size": exif.get("fileSizeInByte") or 0,
            "camera": " ".join(x for x in (exif.get("make"), exif.get("model")) if x) or None,
            "city": exif.get("city"),
            "duration": dur if kind == "video" else None,
            "thumb": True, "warnings": [] if lat is not None else ["ort"],
            "mime": a.get("originalMimeType"), "updatedAt": a.get("updatedAt"),
        }

    # ------------------------------------------------------------ API
    async def status(self) -> dict:
        if not self.configured:
            return {"configured": False}
        try:
            v = await self._json("GET", "/server/version")
            await self._json("POST", "/search/metadata", json={"size": 1})
            return {"configured": True, "ok": True,
                    "version": f"{v.get('major')}.{v.get('minor')}.{v.get('patch')}"}
        except web.HTTPException as e:
            return {"configured": True, "ok": False, "error": e.text}
        except Exception as e:  # noqa: BLE001
            return {"configured": True, "ok": False, "error": f"{type(e).__name__}: {e}"}

    async def albums(self) -> list[dict]:
        res = await self._json("GET", "/albums")
        seen, out = set(), []
        for al in res:
            if al["id"] in seen:
                continue
            seen.add(al["id"])
            out.append({"id": al["id"], "name": al.get("albumName") or "Album",
                        "count": al.get("assetCount", 0), "end": al.get("endDate") or al.get("updatedAt")})
        out.sort(key=lambda x: x["end"] or "", reverse=True)
        return out

    async def assets(self, album: str | None, page: str | None, cursor: str | None) -> dict:
        body = {"size": 120, "order": "desc", "withExif": True, "visibility": "timeline"}
        if album:
            body["albumIds"] = [album]
            body.pop("visibility")
        if cursor:
            body["cursor"] = cursor
        elif page:
            body["page"] = int(page)
        res = await self._json("POST", "/search/metadata", json=body)
        a = res.get("assets", {})
        items = [self.to_item(x) for x in a.get("items", [])
                 if x.get("type") in ("IMAGE", "VIDEO") and not x.get("isTrashed")]
        return {"items": items, "nextCursor": a.get("nextCursor"), "nextPage": a.get("nextPage")}

    async def asset(self, asset_id: str) -> dict:
        return await self._json("GET", f"/assets/{asset_id}")

    async def proxy(self, req: web.Request, path: str, params: dict | None = None,
                    cache: str = "private, max-age=86400") -> web.StreamResponse:
        headers = {}
        if "Range" in req.headers:
            headers["Range"] = req.headers["Range"]
        async with self.session.get(self._url(path), params=params, headers=headers) as r:
            if r.status >= 400:
                raise web.HTTPBadGateway(text=f"Immich {r.status}")
            out = web.StreamResponse(status=r.status)
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "ETag"):
                if h in r.headers:
                    out.headers[h] = r.headers[h]
            out.headers["Cache-Control"] = cache
            await out.prepare(req)
            async for chunk in r.content.iter_chunked(256 * 1024):
                await out.write(chunk)
            await out.write_eof()
            return out

    async def ios_copy(self, asset_id: str) -> tuple[str, dict]:
        """Lädt das Original und schreibt Datum/Ort aus Immich hinein. Ergebnis wird gecacht."""
        a = await self.asset(asset_id)
        item = self.to_item(a)
        ext = os.path.splitext(item["name"])[1].lower() or EXT_BY_MIME.get(item["mime"] or "", ".bin")
        tag = hashlib.sha1(f"{a.get('checksum')}|{item['taken']}|{item['lat']}|{item['lon']}|v1".encode()).hexdigest()[:10]
        dst = os.path.join(self.cache, f"{asset_id}_{tag}{ext}")
        if os.path.exists(dst):
            return dst, item
        lock = self._locks.setdefault(asset_id, asyncio.Lock())
        async with lock:
            if os.path.exists(dst):
                return dst, item
            for fn in os.listdir(self.cache):          # alte Versionen entfernen
                if fn.startswith(asset_id + "_"):
                    os.remove(os.path.join(self.cache, fn))
            orig = dst + ".orig"
            async with self.session.get(self._url(f"/assets/{asset_id}/original")) as r:
                if r.status >= 400:
                    raise web.HTTPBadGateway(text=f"Immich {r.status} beim Download")
                with open(orig, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
            try:
                # Werte aus Immich erzwingen (has_* = False -> alle Felder schreiben)
                meta = {"taken": item["taken"], "lat": item["lat"], "lon": item["lon"], "alt": None,
                        "has_exif_date": False, "has_exif_offset": False, "has_exif_gps": False}
                src = orig + ext                       # exiftool braucht die Endung
                os.replace(orig, src)
                await asyncio.to_thread(metalib.make_ios_copy, src, dst, meta)
            finally:
                for p in (orig, orig + ext):
                    if os.path.exists(p):
                        os.remove(p)
        return dst, item
