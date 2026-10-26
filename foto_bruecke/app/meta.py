"""Metadaten lesen (Aufnahmedatum, Ort) und iPhone-taugliche Kopien schreiben.

Das Original wird nie verändert. Für das iPhone wird eine Kopie erzeugt, in der
Datum und Ort in genau den Feldern stehen, die die Fotos-App auswertet:
  Bilder (JPEG/HEIC): EXIF DateTimeOriginal + OffsetTimeOriginal, EXIF GPS
  Videos (MP4/MOV):   QuickTime Keys:CreationDate (mit Zeitzone),
                      Keys:GPSCoordinates (com.apple.quicktime.location.ISO6709)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IMAGE_FIX_EXT = {".jpg", ".jpeg", ".heic", ".heif"}
VIDEO_FIX_EXT = {".mp4", ".mov", ".m4v", ".3gp"}

_FN_PATTERNS = [
    # IMG_20261020_140311.jpg, VID_20261020_140311.mp4, PXL_20261020_140311123.jpg
    re.compile(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})"),
    # 2026-10-20 14.03.11.jpg / Screenshot_2026-10-20-14-03-11
    re.compile(r"(20\d{2})-(\d{2})-(\d{2})[ _-](\d{2})[.\-:](\d{2})[.\-:](\d{2})"),
]


def _exiftool_json(path: str) -> dict:
    out = subprocess.run(
        ["exiftool", "-j", "-n", "-G", "-a", "-api", "LargeFileSupport=1", path],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return json.loads(out.stdout)[0]
    except (ValueError, IndexError):
        return {}


def _parse_exif_dt(value) -> tuple[datetime | None, str | None]:
    """'2026:10:20 14:03:11[.123][+02:00|Z]' -> (naive datetime, offset or None)."""
    if not isinstance(value, str):
        return None, None
    m = re.match(
        r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*(Z|[+\-]\d{2}:?\d{2})?",
        value.strip(),
    )
    if not m:
        return None, None
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    if y < 1971:  # 0000:00:00 oder 1904 (QuickTime-Nullwert)
        return None, None
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None, None
    off = m.group(7)
    if off == "Z":
        off = "+00:00"
    elif off and ":" not in off:
        off = off[:3] + ":" + off[3:]
    return dt, off


def _offset_to_tz(off: str) -> timezone:
    sign = -1 if off.startswith("-") else 1
    hh, mm = off[1:].split(":")
    return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))


def _fmt_offset(dt: datetime) -> str:
    total = int(dt.utcoffset().total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 60:02d}:{total % 60:02d}"


def _localize(naive: datetime, off: str | None, tz: ZoneInfo) -> datetime:
    if off:
        return naive.replace(tzinfo=_offset_to_tz(off))
    return naive.replace(tzinfo=tz)


def _from_filename(name: str) -> datetime | None:
    for pat in _FN_PATTERNS:
        m = pat.search(name)
        if m:
            try:
                dt = datetime(*(int(x) for x in m.groups()))
                if dt.year >= 1990:
                    return dt
            except ValueError:
                pass
    return None


def read_meta(path: str, original_name: str, tz: ZoneInfo,
              client_mtime_ms: int | None = None) -> dict:
    """Liefert Aufnahmezeit (lokal, mit Offset), Ort und ein paar Infos zur Datei."""
    t = _exiftool_json(path)
    mime = str(t.get("File:MIMEType", ""))
    is_video = mime.startswith("video/")
    taken: datetime | None = None
    source = None

    if is_video:
        # 1. Apple-Format mit Zeitzone
        for key in ("QuickTime:CreationDate", "Keys:CreationDate", "XMP:DateTimeOriginal"):
            dt, off = _parse_exif_dt(t.get(key))
            if dt:
                taken = _localize(dt, off, tz)
                source = key
                break
        # 2. QuickTime CreateDate ist laut Standard UTC
        if taken is None:
            for key in ("QuickTime:CreateDate", "QuickTime:MediaCreateDate", "QuickTime:TrackCreateDate"):
                dt, off = _parse_exif_dt(t.get(key))
                if dt:
                    utc = dt.replace(tzinfo=_offset_to_tz(off) if off else timezone.utc)
                    taken = utc.astimezone(tz)
                    source = key
                    break
    else:
        for key in ("EXIF:DateTimeOriginal", "XMP:DateTimeOriginal", "EXIF:CreateDate",
                    "XMP:CreateDate", "QuickTime:CreationDate"):
            dt, off = _parse_exif_dt(t.get(key))
            if dt:
                if not off and key.startswith("EXIF:"):
                    off_key = {"EXIF:DateTimeOriginal": "EXIF:OffsetTimeOriginal",
                               "EXIF:CreateDate": "EXIF:OffsetTimeDigitized"}.get(key)
                    off_val = t.get(off_key) or t.get("EXIF:OffsetTime")
                    if isinstance(off_val, str) and re.match(r"^[+\-]\d{2}:\d{2}$", off_val):
                        off = off_val
                taken = _localize(dt, off, tz)
                source = key
                break

    if taken is None:
        dt = _from_filename(original_name)
        if dt:
            taken = dt.replace(tzinfo=tz)
            source = "Dateiname"
    if taken is None and client_mtime_ms:
        taken = datetime.fromtimestamp(client_mtime_ms / 1000, tz)
        source = "Dateidatum"

    lat = t.get("Composite:GPSLatitude")
    lon = t.get("Composite:GPSLongitude")
    alt = t.get("Composite:GPSAltitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or (lat == 0 and lon == 0):
        lat = lon = alt = None

    return {
        "mime": mime,
        "kind": "video" if is_video else "image",
        "taken": taken.isoformat() if taken else None,
        "taken_source": source,
        "lat": lat,
        "lon": lon,
        "alt": alt if isinstance(alt, (int, float)) else None,
        "duration": t.get("Composite:Duration") or t.get("QuickTime:Duration"),
        "camera": " ".join(str(x) for x in (t.get("EXIF:Make"), t.get("EXIF:Model")) if x) or None,
        "has_exif_date": bool(t.get("EXIF:DateTimeOriginal")),
        "has_exif_offset": bool(t.get("EXIF:OffsetTimeOriginal")),
        "has_exif_gps": isinstance(t.get("EXIF:GPSLatitude"), (int, float)),
        "has_keys_date": bool(t.get("Keys:CreationDate") or t.get("QuickTime:CreationDate")),
        "has_keys_gps": bool(t.get("Keys:GPSCoordinates") or t.get("QuickTime:GPSCoordinates")),
    }


def build_fix_args(meta: dict, ext: str) -> list[str]:
    """exiftool-Argumente, damit die iPhone-Fotos-App Datum und Ort sicher übernimmt."""
    args: list[str] = []
    taken = datetime.fromisoformat(meta["taken"]) if meta.get("taken") else None
    lat, lon, alt = meta.get("lat"), meta.get("lon"), meta.get("alt")
    ext = ext.lower()

    if ext in IMAGE_FIX_EXT:
        if taken:
            stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
            off = _fmt_offset(taken)
            if not meta.get("has_exif_date"):
                args += [f"-EXIF:DateTimeOriginal={stamp}", f"-EXIF:CreateDate={stamp}"]
            if not meta.get("has_exif_offset"):
                args += [f"-EXIF:OffsetTimeOriginal={off}", f"-EXIF:OffsetTimeDigitized={off}",
                         f"-EXIF:OffsetTime={off}"]
        if lat is not None and not meta.get("has_exif_gps"):
            args += [f"-EXIF:GPSLatitude={abs(lat)}", f"-EXIF:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
                     f"-EXIF:GPSLongitude={abs(lon)}", f"-EXIF:GPSLongitudeRef={'E' if lon >= 0 else 'W'}"]
            if alt is not None:
                args += [f"-EXIF:GPSAltitude={abs(alt)}", f"-EXIF:GPSAltitudeRef={0 if alt >= 0 else 1}"]
    elif ext in VIDEO_FIX_EXT:
        if taken:
            local = taken.strftime("%Y:%m:%d %H:%M:%S") + _fmt_offset(taken)
            utc = taken.astimezone(timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
            args += [f"-Keys:CreationDate={local}",
                     f"-QuickTime:CreateDate={utc}", f"-QuickTime:ModifyDate={utc}"]
        if lat is not None:
            coords = f"{lat:.6f}, {lon:.6f}" + (f", {alt:.1f}" if alt is not None else "")
            args += [f"-Keys:GPSCoordinates={coords}"]
    return args


def make_ios_copy(src: str, dst: str, meta: dict) -> None:
    """Schreibt eine korrigierte Kopie nach dst. Das Original bleibt unverändert."""
    ext = os.path.splitext(src)[1]
    args = build_fix_args(meta, ext)
    tmp = dst + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    if args:
        res = subprocess.run(
            ["exiftool", "-m", "-q", "-q", "-api", "LargeFileSupport=1",
             "-api", "QuickTimeUTC=0", *args, "-o", tmp, src],
            capture_output=True, text=True, timeout=300,
        )
        if res.returncode != 0 or not os.path.exists(tmp):
            # Notfalls das Original unverändert ausliefern
            shutil.copyfile(src, tmp)
    else:
        shutil.copyfile(src, tmp)
    if meta.get("taken"):
        ts = datetime.fromisoformat(meta["taken"]).timestamp()
        os.utime(tmp, (ts, ts))
    os.replace(tmp, dst)
