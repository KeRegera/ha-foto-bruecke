package de.keregera.fotobruecke;

import android.Manifest;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;

import java.util.ArrayList;
import java.util.List;

/** Gespeicherte Einstellungen und Berechtigungen. */
final class Config {
    private static final String PREFS = "foto_bruecke";

    private Config() {}

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String url(Context c) {
        return prefs(c).getString("url", "");
    }

    static String token(Context c) {
        return prefs(c).getString("token", "");
    }

    static boolean configured(Context c) {
        return !url(c).isEmpty() && !token(c).isEmpty();
    }

    static String normalizeUrl(String u) {
        u = u == null ? "" : u.trim();
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        if (!u.isEmpty() && !u.startsWith("http://") && !u.startsWith("https://")) u = "http://" + u;
        return u;
    }

    static void save(Context c, String url, String token) {
        prefs(c).edit().putString("url", normalizeUrl(url)).putString("token", token == null ? "" : token.trim()).apply();
    }

    /** Berechtigungen, die für Fotos MIT Ort nötig sind. */
    static String[] neededPermissions() {
        List<String> p = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33) {
            p.add(Manifest.permission.READ_MEDIA_IMAGES);
            p.add(Manifest.permission.READ_MEDIA_VIDEO);
        } else {
            p.add(Manifest.permission.READ_EXTERNAL_STORAGE);
        }
        p.add(Manifest.permission.ACCESS_MEDIA_LOCATION);
        return p.toArray(new String[0]);
    }

    static boolean hasLocationAccess(Context c) {
        return c.checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }
}
