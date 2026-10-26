# Foto-Brücke

Fotos und Videos vom Android-Handy hochladen und auf dem iPhone mit **Aufnahmedatum und Ort** in die Fotos-App sichern.

## So funktioniert es

**Android – Hochladen**
1. Home Assistant öffnen → **Foto-Brücke** in der Seitenleiste → Reiter **Hochladen**.
2. **Fotos aus „Dateien“ wählen** tippen, dann **☰ → Bilder** oder **Interner Speicher → DCIM → Camera** öffnen und Fotos markieren.
3. In der Liste siehst du zu jedem Foto Datum und **Ort ✓**. Steht dort „kein Ort“, wurde der Ort von Android entfernt: Foto entfernen und erneut über „Dateien“ hochladen.

> Die normale Galerie-Auswahl (Fotoauswahl) von Android entfernt den Ort aus Datenschutzgründen. Über „Dateien“ bekommt die App die unveränderte Originaldatei.

**iPhone – Sichern**
1. Foto-Brücke öffnen → Reiter **Galerie** (Fotos sind nach Aufnahmetag sortiert).
2. **Auswählen** tippen (oder ein Foto lange drücken), Fotos markieren. Mit **Alle** wählst du einen ganzen Tag.
3. Warten, bis „Wird vorbereitet …“ fertig ist, dann **Sichern** → im Teilen-Menü **Bilder sichern**.

Funktioniert das Teilen-Menü in der Home-Assistant-App nicht, öffne Home Assistant einmal in **Safari** – dort klappt es immer.

## Was die App mit den Dateien macht

- Das **Original** bleibt unverändert unter `/media/foto-bruecke/<JJJJ-MM-TT>/` liegen (auch per Samba sichtbar).
- Beim Sichern aufs iPhone wird eine **Kopie** erzeugt, in der Datum und Ort in genau den Feldern stehen, die die iPhone-Fotos-App liest:
  - Fotos: EXIF-Aufnahmedatum **mit Zeitzone** und EXIF-GPS
  - Videos: Apple-Felder `com.apple.quicktime.creationdate` und `com.apple.quicktime.location.ISO6709` – Android-Videos haben diese nicht, deshalb landen sie sonst oft mit falschem Datum oder ohne Ort auf dem iPhone.
- Fehlt im Foto das Datum, wird es aus dem Dateinamen (z. B. `IMG_20261020_140311.jpg`) übernommen.

## Optionen

| Option | Bedeutung |
|---|---|
| `keep_days` | Fotos nach so vielen Tagen automatisch löschen (0 = nie) |
| `timezone` | Zeitzone für Fotos ohne Zeitzonenangabe. Leer = Zeitzone von Home Assistant |
