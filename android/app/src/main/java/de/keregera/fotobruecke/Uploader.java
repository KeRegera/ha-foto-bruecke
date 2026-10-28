package de.keregera.fotobruecke;

import android.content.ContentResolver;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.provider.MediaStore;
import android.provider.OpenableColumns;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

/** Lädt eine Datei unverändert (inkl. Ort) zur Foto-Brücke hoch. */
final class Uploader {

    interface Progress {
        void onBytes(long done, long total);
    }

    static final class Result {
        boolean ok;
        boolean duplicate;
        boolean hasLocation;
        String name;
        String taken;
        String error;
    }

    private final Context ctx;
    private final String baseUrl;
    private final String token;

    Uploader(Context ctx) {
        this.ctx = ctx;
        this.baseUrl = Config.url(ctx);
        this.token = Config.token(ctx);
    }

    /** Verbindung prüfen. Gibt null zurück, wenn alles passt, sonst eine Fehlermeldung. */
    static String ping(String baseUrl, String token) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(baseUrl + "/api/ping").openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            c.setRequestProperty("X-Foto-Bruecke-Token", token);
            int code = c.getResponseCode();
            if (code == 200) return null;
            if (code == 401) return "Schlüssel falsch";
            return "Server antwortet mit " + code;
        } catch (IOException e) {
            return "Nicht erreichbar (" + e.getClass().getSimpleName() + ": " + e.getMessage() + ")";
        } finally {
            if (c != null) c.disconnect();
        }
    }

    /**
     * Öffnet die Originaldatei. Bei Galerie-Adressen (MediaStore) wird ausdrücklich das
     * Original mit Ort angefordert – sonst entfernt Android die GPS-Daten.
     */
    private Uri originalUri(Uri uri) {
        if (!"media".equals(uri.getAuthority()) || !Config.hasLocationAccess(ctx)) return uri;
        try {
            Uri orig = MediaStore.setRequireOriginal(uri);
            InputStream test = ctx.getContentResolver().openInputStream(orig);
            if (test != null) test.close();
            return orig;
        } catch (Exception e) {
            return uri;
        }
    }

    static String displayName(Context ctx, Uri uri) {
        String name = null;
        try (Cursor c = ctx.getContentResolver().query(uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) name = c.getString(0);
        } catch (Exception ignored) {
        }
        if (name == null || name.isEmpty()) {
            name = uri.getLastPathSegment();
        }
        return name == null || name.isEmpty() ? "foto" : name;
    }

    private long size(Uri uri) {
        try (Cursor c = ctx.getContentResolver().query(uri, new String[]{OpenableColumns.SIZE}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return c.getLong(0);
        } catch (Exception ignored) {
        }
        return -1;
    }

    /** Aufnahmezeit aus der Galerie-Datenbank (nur Ersatz, falls die Datei kein Datum hat). */
    private long dateTaken(Uri uri) {
        try (Cursor c = ctx.getContentResolver().query(uri, new String[]{MediaStore.MediaColumns.DATE_TAKEN}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return c.getLong(0);
        } catch (Exception ignored) {
        }
        return 0;
    }

    Result upload(Uri uri, Progress progress) {
        Result r = new Result();
        r.name = displayName(ctx, uri);
        ContentResolver cr = ctx.getContentResolver();
        String mime = cr.getType(uri);
        if (mime == null) mime = "application/octet-stream";
        long total = size(uri);
        long taken = dateTaken(uri);
        Uri src = originalUri(uri);

        String boundary = "----FotoBruecke" + UUID.randomUUID().toString().replace("-", "");
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(baseUrl + "/api/upload").openConnection();
            c.setDoOutput(true);
            c.setRequestMethod("POST");
            c.setConnectTimeout(10000);
            c.setReadTimeout(300000);
            c.setChunkedStreamingMode(256 * 1024);
            c.setRequestProperty("X-Foto-Bruecke-Token", token);
            c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);

            try (OutputStream out = c.getOutputStream(); InputStream in = cr.openInputStream(src)) {
                if (in == null) throw new IOException("Datei kann nicht geöffnet werden");
                if (taken > 0) {
                    out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"lastModified\"\r\n\r\n"
                            + taken + "\r\n").getBytes(StandardCharsets.UTF_8));
                }
                String safeName = r.name.replace("\"", "_").replace("\r", "").replace("\n", "");
                out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\""
                        + safeName + "\"\r\nContent-Type: " + mime + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                byte[] buf = new byte[256 * 1024];
                long done = 0;
                long lastReport = 0;
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                    done += n;
                    long now = System.currentTimeMillis();
                    if (progress != null && now - lastReport > 150) {
                        progress.onBytes(done, total);
                        lastReport = now;
                    }
                }
                if (progress != null) progress.onBytes(done, total > 0 ? total : done);
                out.write(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
            }

            int code = c.getResponseCode();
            String body = readAll(code < 400 ? c.getInputStream() : c.getErrorStream());
            if (code != 200) {
                r.error = code == 401 ? "Schlüssel falsch – App neu einrichten" : ("Fehler " + code + ": " + body);
                return r;
            }
            JSONObject json = new JSONObject(body);
            JSONObject item = json.getJSONObject("item");
            r.ok = true;
            r.duplicate = json.optBoolean("duplicate", false);
            r.hasLocation = !item.isNull("lat");
            r.taken = item.isNull("taken") ? null : item.optString("taken", null);
            r.name = item.optString("name", r.name);
        } catch (Exception e) {
            r.error = e.getClass().getSimpleName() + ": " + e.getMessage();
        } finally {
            if (c != null) c.disconnect();
        }
        return r;
    }

    private static String readAll(InputStream in) throws IOException {
        if (in == null) return "";
        try (InputStream i = in) {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = i.read(buf)) > 0) b.write(buf, 0, n);
            return b.toString("UTF-8");
        }
    }
}
