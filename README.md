# Foto-Brücke für Home Assistant

Fotos aus der Android-Galerie hochladen und auf dem iPhone mit Aufnahmedatum und Ort sichern.

- `foto_bruecke/` – die Home-Assistant-App
- `android/` – die kleine Android-App (Teilen → Foto-Brücke). Wird bei jedem Push von GitHub Actions gebaut und unter **Releases** als `FotoBruecke.apk` veröffentlicht.

## Installation

1. Einstellungen → Apps → **App-Store** → ⋮ → **Repositories** → `https://github.com/KeRegera/ha-foto-bruecke`
2. **Foto-Brücke** installieren, starten, „In Seitenleiste anzeigen“ aktivieren
3. Anleitung: [foto_bruecke/DOCS.md](foto_bruecke/DOCS.md)

Hinweis: Der Signaturschlüssel der Android-App (`android/app/fotobruecke.jks`) liegt im Repo, damit Updates sich über die alte Version installieren lassen.
