package de.keregera.fotobruecke;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/** Einrichtung: Server-Adresse, Schlüssel, Berechtigungen. */
public class MainActivity extends Activity {
    private static final int REQ_PERM = 1;

    private EditText urlField;
    private EditText tokenField;
    private TextView serverState;
    private TextView permState;
    private Button permButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout root = Ui.page(this);

        Ui.title(this, root, "Foto-Brücke");
        Ui.text(this, root, "Fotos aus der Galerie mit Aufnahmedatum und Ort in deine Home-Assistant-Foto-Brücke hochladen.");

        Ui.heading(this, root, "So geht's");
        Ui.text(this, root, "1. Galerie öffnen und Fotos lange antippen, um mehrere zu markieren.\n"
                + "2. Teilen → Foto-Brücke wählen.\n"
                + "3. Auf dem iPhone in der Foto-Brücke auswählen → Sichern.");

        Ui.heading(this, root, "1. Berechtigung");
        permState = Ui.text(this, root, "");
        permButton = Ui.button(this, root, "Zugriff auf Fotos mit Ort erlauben");
        permButton.setOnClickListener(v -> askPermissions());

        Ui.heading(this, root, "2. Verbindung");
        Ui.text(this, root, "Am einfachsten: In Home Assistant → Foto-Brücke → Hochladen auf „App einrichten“ tippen. "
                + "Oder hier von Hand eintragen:");
        urlField = new EditText(this);
        urlField.setHint("http://192.168.0.10:8098");
        urlField.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        urlField.setSingleLine(true);
        root.addView(urlField);
        tokenField = new EditText(this);
        tokenField.setHint("Schlüssel");
        tokenField.setSingleLine(true);
        tokenField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        root.addView(tokenField);
        Button save = Ui.button(this, root, "Speichern und testen");
        save.setOnClickListener(v -> {
            Config.save(this, urlField.getText().toString(), tokenField.getText().toString());
            urlField.setText(Config.url(this));
            test();
        });
        serverState = Ui.text(this, root, "");

        urlField.setText(Config.url(this));
        tokenField.setText(Config.token(this));
        handleSetupLink(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleSetupLink(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        updatePermState();
        if (Config.configured(this)) test();
        else serverState.setText("Noch nicht eingerichtet.");
    }

    /** fotobruecke://setup?url=…&token=… */
    private void handleSetupLink(Intent intent) {
        if (intent == null || intent.getData() == null) return;
        Uri d = intent.getData();
        if (!"fotobruecke".equals(d.getScheme())) return;
        String url = d.getQueryParameter("url");
        String token = d.getQueryParameter("token");
        if (url == null || token == null) return;
        Config.save(this, url, token);
        urlField.setText(Config.url(this));
        tokenField.setText(Config.token(this));
        Toast.makeText(this, "Eingerichtet", Toast.LENGTH_SHORT).show();
        test();
        if (!Config.hasLocationAccess(this)) askPermissions();
    }

    private void test() {
        serverState.setText("Verbindung wird geprüft …");
        final String url = Config.url(this);
        final String token = Config.token(this);
        new Thread(() -> {
            final String err = Uploader.ping(url, token);
            runOnUiThread(() -> serverState.setText(err == null
                    ? "✓ Verbunden mit " + url
                    : "✗ " + err + "\nIst das Handy im Heimnetz? Läuft die Foto-Brücke?"));
        }).start();
    }

    private void askPermissions() {
        if (shouldShowSettings) {
            Intent i = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName()));
            startActivity(i);
            return;
        }
        requestPermissions(Config.neededPermissions(), REQ_PERM);
    }

    private boolean shouldShowSettings = false;

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        updatePermState();
        if (!Config.hasLocationAccess(this)) {
            // Android fragt nach zweimaliger Ablehnung nicht mehr – dann über die Einstellungen
            shouldShowSettings = true;
            permButton.setText("In den Einstellungen erlauben");
        }
    }

    private void updatePermState() {
        boolean ok = Config.hasLocationAccess(this);
        permState.setText(ok
                ? "✓ Die App darf den Ort aus deinen Fotos lesen."
                : "Ohne diese Berechtigung entfernt Android den Ort aus den Fotos. "
                + "Bei „Fotos und Videos“ bitte „Alle zulassen“ wählen.");
        permButton.setVisibility(ok ? android.view.View.GONE : android.view.View.VISIBLE);
    }
}
