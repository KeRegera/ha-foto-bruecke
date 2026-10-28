package de.keregera.fotobruecke;

import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;

/** Empfängt Fotos/Videos aus dem Teilen-Menü und lädt sie hoch. */
public class ShareActivity extends Activity {

    private LinearLayout root;
    private TextView header;
    private ProgressBar bar;
    private Button closeButton;
    private Thread worker;
    private volatile boolean finished = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        root = Ui.page(this);
        Ui.title(this, root, "Foto-Brücke");
        header = Ui.text(this, root, "");

        if (!Config.configured(this)) {
            header.setText("Die App ist noch nicht eingerichtet.\nIn Home Assistant → Foto-Brücke → Hochladen auf „App einrichten“ tippen.");
            Button open = Ui.button(this, root, "Einrichtung öffnen");
            open.setOnClickListener(v -> {
                startActivity(new Intent(this, MainActivity.class));
                finish();
            });
            return;
        }

        final List<Uri> uris = collectUris(getIntent());
        if (uris.isEmpty()) {
            header.setText("Keine Fotos erhalten.");
            return;
        }

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(1000);
        root.addView(bar, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, Ui.dp(this, 24)));

        if (!Config.hasLocationAccess(this)) {
            TextView warn = Ui.text(this, root, "⚠ Ohne Berechtigung „Standort in Medien“ entfernt Android den Ort. "
                    + "App „Foto-Brücke“ öffnen und Zugriff erlauben.");
            warn.setTextColor(0xFFD98A00);
        }

        final List<TextView> rows = new ArrayList<>();
        for (Uri u : uris) {
            TextView t = Ui.text(this, root, "○ " + Uploader.displayName(this, u));
            rows.add(t);
        }
        closeButton = Ui.button(this, root, "Im Hintergrund weiter");
        // Während des Uploads nur in den Hintergrund – beim Beenden könnte Android den Dateizugriff entziehen
        closeButton.setOnClickListener(v -> {
            if (finished) finish();
            else moveTaskToBack(true);
        });

        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        header.setText(String.format(Locale.GERMANY, "Lade %d %s hoch …", uris.size(), uris.size() == 1 ? "Datei" : "Dateien"));

        final Uploader up = new Uploader(getApplicationContext());
        worker = new Thread(() -> {
            int ok = 0, withLoc = 0, failed = 0, dup = 0;
            final int n = uris.size();
            for (int i = 0; i < n; i++) {
                final int idx = i;
                final TextView row = rows.get(i);
                runOnUiThread(() -> row.setText("↑ " + row.getText().toString().substring(2)));
                Uploader.Result r = up.upload(uris.get(i), (done, total) -> {
                    final double frac = total > 0 ? Math.min(1.0, (double) done / total) : 0;
                    runOnUiThread(() -> bar.setProgress((int) ((idx + frac) / n * 1000)));
                });
                final String line;
                if (r.ok) {
                    ok++;
                    if (r.duplicate) dup++;
                    if (r.hasLocation) withLoc++;
                    line = "✓ " + r.name + "  ·  " + formatTaken(r.taken) + "  ·  "
                            + (r.hasLocation ? "Ort ✓" : "kein Ort") + (r.duplicate ? "  ·  schon vorhanden" : "");
                } else {
                    failed++;
                    line = "✗ " + r.name + "  ·  " + r.error;
                }
                runOnUiThread(() -> {
                    row.setText(line);
                    if (line.startsWith("✗") || line.contains("kein Ort")) row.setTextColor(0xFFD98A00);
                    bar.setProgress((int) ((idx + 1.0) / n * 1000));
                });
            }
            final String summary = String.format(Locale.GERMANY,
                    "Fertig: %d von %d hochgeladen, %d mit Ort%s%s",
                    ok, n, withLoc,
                    dup > 0 ? String.format(Locale.GERMANY, ", %d schon vorhanden", dup) : "",
                    failed > 0 ? String.format(Locale.GERMANY, ", %d fehlgeschlagen", failed) : "");
            final boolean allGood = failed == 0;
            runOnUiThread(() -> {
                finished = true;
                header.setText(summary);
                closeButton.setText("Schließen");
                getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                if (allGood) bar.setVisibility(View.GONE);
            });
        }, "upload");
        worker.start();
    }

    private static String formatTaken(String iso) {
        // "2026-10-18T16:42:05+02:00" -> "18.10.2026 16:42"
        if (iso == null || iso.length() < 16) return "kein Datum";
        try {
            return Integer.parseInt(iso.substring(8, 10)) + "." + Integer.parseInt(iso.substring(5, 7)) + "."
                    + iso.substring(0, 4) + " " + iso.substring(11, 16);
        } catch (Exception e) {
            return iso;
        }
    }

    @SuppressWarnings("deprecation")
    private static List<Uri> collectUris(Intent intent) {
        LinkedHashSet<Uri> set = new LinkedHashSet<>();
        if (intent == null) return new ArrayList<>();
        String action = intent.getAction();
        if (Intent.ACTION_SEND.equals(action)) {
            Uri u = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (u != null) set.add(u);
        } else if (Intent.ACTION_SEND_MULTIPLE.equals(action)) {
            ArrayList<Uri> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) set.addAll(list);
        }
        ClipData clip = intent.getClipData();
        if (clip != null) {
            for (int i = 0; i < clip.getItemCount(); i++) {
                Uri u = clip.getItemAt(i).getUri();
                if (u != null) set.add(u);
            }
        }
        return new ArrayList<>(set);
    }
}
