package de.keregera.fotobruecke;

import android.app.Activity;
import android.graphics.Typeface;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Kleine Helfer, um die Oberfläche ohne Layout-Dateien aufzubauen. */
final class Ui {
    private Ui() {}

    static int dp(Activity a, int v) {
        return Math.round(v * a.getResources().getDisplayMetrics().density);
    }

    static LinearLayout page(Activity a) {
        ScrollView sv = new ScrollView(a);
        sv.setFitsSystemWindows(true);
        LinearLayout root = new LinearLayout(a);
        root.setOrientation(LinearLayout.VERTICAL);
        int p = dp(a, 20);
        root.setPadding(p, p, p, p);
        sv.addView(root, new ScrollView.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        a.setContentView(sv);
        return root;
    }

    static TextView title(Activity a, LinearLayout parent, String text) {
        TextView t = text(a, parent, text);
        t.setTextSize(24);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    static TextView heading(Activity a, LinearLayout parent, String text) {
        TextView t = text(a, parent, text);
        t.setTextSize(17);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        ((LinearLayout.LayoutParams) t.getLayoutParams()).topMargin = dp(a, 20);
        return t;
    }

    static TextView text(Activity a, LinearLayout parent, String text) {
        TextView t = new TextView(a);
        t.setText(text);
        t.setTextSize(15);
        t.setLineSpacing(0, 1.15f);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(a, 6);
        parent.addView(t, lp);
        return t;
    }

    static Button button(Activity a, LinearLayout parent, String text) {
        Button b = new Button(a);
        b.setText(text);
        b.setAllCaps(false);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(a, 10);
        parent.addView(b, lp);
        return b;
    }
}
