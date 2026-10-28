# Foto-Brücke

Fotos und Videos vom Android-Handy hochladen und auf dem iPhone mit **Aufnahmedatum und Ort** in die Fotos-App sichern.

## Android – Hochladen aus der Galerie

**Einmalig einrichten**
1. Auf dem Android: Home Assistant → **Foto-Brücke** → **Hochladen** → **1. App laden**. Die `FotoBruecke.apk` herunterladen und installieren (Installation aus unbekannter Quelle erlauben).
2. Zurück in der Foto-Brücke auf **2. App einrichten** tippen. Die App übernimmt Adresse und Schlüssel automatisch.
3. In der App **Zugriff auf Fotos mit Ort erlauben** und bei „Fotos und Videos“ **Alle zulassen** wählen.

**Hochladen**
1. In der Galerie Fotos lange antippen und mehrere markieren.
2. **Teilen → Foto-Brücke**. Bei jedem Foto steht danach Datum und **Ort ✓**.

> Nur eine App mit der Berechtigung „Standort in Medien“ darf den Ort aus der Galerie lesen. Webseiten bekommen ihn unter Android nie – deshalb die kleine App.
> Samsung-Galerie: Im Teilen-Menü darf „Standortdaten entfernen“ nicht aktiviert sein.

Die App spricht mit der Foto-Brücke über **Port 8098** im Heimnetz (nur mit Schlüssel, nur Upload). Wird die App-Verbindung nicht gefunden, in der App als Adresse die IP von Home Assistant eintragen, z. B. `http://192.168.0.10:8098`.

## iPhone – Sichern

1. Foto-Brücke öffnen → **Galerie** (nach Aufnahmetag sortiert).
2. **Auswählen** tippen (oder lange drücken), Fotos markieren. **Alle** wählt einen ganzen Tag.
3. Warten, bis „Wird vorbereitet …“ fertig ist, dann **Sichern** → **Bilder sichern**.

Erscheint das Teilen-Menü in der Home-Assistant-App nicht, Home Assistant einmal in **Safari** öffnen.

## Was mit den Dateien passiert

- Das **Original** liegt unverändert unter `/media/foto-bruecke/<JJJJ-MM-TT>/`.
- Fürs iPhone wird eine **Kopie** erzeugt, in der Datum (mit Zeitzone) und Ort in genau den Feldern stehen, die die iPhone-Fotos-App liest – bei Videos auch die Apple-Felder, die Android-Videos fehlen.

## Optional: Immich als Quelle

Mit `immich_url` und `immich_api_key` zeigt die Galerie zusätzlich deine Immich-Fotos (mit Alben) und sichert sie ebenfalls mit Datum und Ort aufs iPhone.

## Optionen

| Option | Bedeutung |
|---|---|
| `app_token` | Schlüssel für die Android-App. Leer = automatisch erzeugt |
| `immich_url`, `immich_api_key` | Optional: Immich anbinden (Rechte: asset.read, asset.view, asset.download, album.read) |
| `keep_days` | Fotos nach so vielen Tagen automatisch löschen (0 = nie) |
| `timezone` | Zeitzone für Fotos ohne Zeitzonenangabe. Leer = Zeitzone von Home Assistant |
