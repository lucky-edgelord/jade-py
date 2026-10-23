package app.netrunner.jade;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.ViewGroup;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Jade Link's screen: a WebView that shows the setup page (assets/setup.html: where jade-py is, listening, permissions,
 * screen control, Jade's questions) and, once connected, jade-py's dashboard. The JadeLink bridge answers only the
 * setup page that ships inside the app, never the dashboard or any other site.
 */
public class MainActivity extends Activity {
    static final int REQ_PERMS = 17;
    static final int BG = 0xff040807;
    static final String SETUP = "file:///android_asset/setup.html";
    WebView web;
    volatile String url = SETUP;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Window w = getWindow(); w.setStatusBarColor(BG); w.setNavigationBarColor(BG);
        web = new WebView(this);
        web.setBackgroundColor(BG);
        FrameLayout root = new FrameLayout(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.addJavascriptInterface(new Bridge(), "JadeLink");
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView v, String u, android.graphics.Bitmap f) { url = u == null ? "" : u; }
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) { return route(r.getUrl().toString()); }
        });
        LinkService.startIfWanted(this);
        boolean connected = LinkService.config(this).dialOut;
        Intent i = getIntent();
        if (connected && !(i != null && (i.hasExtra("perms") || i.hasExtra("asks") || i.hasExtra("setup")))) web.loadUrl(dashboardUrl());
        else web.loadUrl(SETUP);
        handle(i);
    }

    @Override protected void onNewIntent(Intent i) { super.onNewIntent(i); setIntent(i); if (i != null && i.hasExtra("asks")) web.loadUrl(SETUP); handle(i); }

    private void handle(Intent i) {
        if (i == null) return;
        String perms = i.getStringExtra("perms");
        if (perms != null && perms.length() > 0) requestPermissions(perms.split(","), REQ_PERMS);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        Perms.answered();
        LinkService.startIfWanted(this);   // the service's types follow the permissions
        js("window.onPerms&&window.onPerms()");
    }

    @Override public void onBackPressed() { if (web.canGoBack()) web.goBack(); else if (!url.startsWith(SETUP)) web.loadUrl(SETUP); else super.onBackPressed(); }

    @Override protected void onResume() { super.onResume(); js("window.onResume&&window.onResume()"); }

    void js(final String code) { runOnUiThread(new Runnable() { public void run() { try { web.evaluateJavascript(code, null); } catch (Throwable ignored) { } } }); }

    /** Where a link may go: the setup page, jade-py's own pages, jadelink:// commands; anything else opens in the browser. */
    private boolean route(String u) {
        if (u.startsWith("jadelink://setup")) { web.loadUrl(SETUP); return true; }
        if (u.startsWith("file:///android_asset/")) return false;
        String server = LinkService.config(this).server;
        if (server.length() > 0 && origin(u).equals(origin(base(server)))) return false;
        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(u)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); } catch (Throwable ignored) { }
        return true;
    }

    static String base(String server) { String b = server.trim(); while (b.endsWith("/")) b = b.substring(0, b.length() - 1); return b.matches("(?i)https?://.*") ? b : "http://" + b; }
    static String origin(String u) { return u.replaceFirst("^((?i)https?://[^/?#]+).*$", "$1").toLowerCase(); }

    String dashboardUrl() { Link.Config k = LinkService.config(this); return base(k.server) + "/ui/#token=" + Uri.encode(k.token) + "&app=1"; }

    /** The setup page's bridge. Every method checks that the setup page (inside the app) is what is showing. */
    final class Bridge {
        private boolean local() { return url.startsWith("file:///android_asset/"); }

        @JavascriptInterface public String config() {
            if (!local()) return "{}";
            Link.Config k = LinkService.config(MainActivity.this);
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("server", k.server); m.put("token", k.token); m.put("name", k.name); m.put("dial_out", LinkService.prefs(MainActivity.this).getBoolean("dial_out", false));
            m.put("listen", k.listen); m.put("port", (double) k.port); m.put("listen_token", k.listenToken); m.put("lan", k.lan); m.put("device_id", k.deviceId);
            m.put("model", Build.MODEL); m.put("version", LinkService.version(MainActivity.this));
            return MiniJson.write(m);
        }

        @JavascriptInterface public String save(String json) {
            if (!local()) return "{}";
            try {
                JSONObject o = new JSONObject(json);
                SharedPreferences.Editor e = LinkService.prefs(MainActivity.this).edit();
                if (o.has("server")) e.putString("server", o.optString("server", "").trim());
                if (o.has("token")) e.putString("token", o.optString("token", "").trim());
                if (o.has("name")) e.putString("name", o.optString("name", "").trim());
                if (o.has("dial_out")) e.putBoolean("dial_out", o.optBoolean("dial_out", false));
                if (o.has("listen")) e.putBoolean("listen", o.optBoolean("listen", false));
                if (o.has("port")) e.putInt("port", Math.max(1024, Math.min(65535, o.optInt("port", 8765))));
                if (o.has("listen_token") && o.optString("listen_token", "").length() >= 12) e.putString("listen_token", o.optString("listen_token"));
                if (o.has("lan")) e.putBoolean("lan", o.optBoolean("lan", true));
                e.commit();
                LinkService.startIfWanted(MainActivity.this);
                Link l = LinkService.link; if (l != null && LinkService.wanted(MainActivity.this)) l.apply(LinkService.config(MainActivity.this));
                return "{\"ok\":true}";
            } catch (Throwable ex) { return "{\"error\":" + MiniJson.quote(String.valueOf(ex.getMessage())) + "}"; }
        }

        @JavascriptInterface public String newListenToken() {
            if (!local()) return "";
            String t = "jl_" + LinkService.random(24);
            LinkService.prefs(MainActivity.this).edit().putString("listen_token", t).commit();
            Link l = LinkService.link; if (l != null) l.apply(LinkService.config(MainActivity.this));
            return t;
        }

        @SuppressWarnings("unchecked")
        @JavascriptInterface public String status() {
            if (!local()) return "{}";
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            Link l = LinkService.link;
            m.put("running", LinkService.inst != null);
            if (l != null) m.put("link", l.status());
            Dispatch d = LinkService.dispatch;
            if (d == null) d = new Dispatch(getApplicationContext());
            m.put("info", d.info());
            m.put("ips", ips());
            m.put("asks", MiniJson.parse(Asker.list()));
            m.put("battery_optimized", batteryOptimized());
            m.put("can_write_settings", Build.VERSION.SDK_INT < 23 || Settings.System.canWrite(MainActivity.this));
            return MiniJson.write(m);
        }

        @JavascriptInterface public void answer(String id, boolean allow) { if (local()) Asker.answer(id, allow); }

        @JavascriptInterface public void requestPerms(final String csv) {
            if (!local()) return;
            runOnUiThread(new Runnable() { public void run() { requestPermissions((csv == null || csv.length() == 0 ? Perms.join(java.util.Arrays.asList(Perms.all())) : csv).split(","), REQ_PERMS); } });
        }

        @JavascriptInterface public void open(String what) {
            if (!local()) return;
            Intent i;
            if ("accessibility".equals(what)) i = new Intent("android.settings.ACCESSIBILITY_SETTINGS");
            else if ("notifications".equals(what)) i = new Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS");
            else if ("overlay".equals(what)) i = new Intent("android.settings.action.MANAGE_OVERLAY_PERMISSION", Uri.parse("package:" + getPackageName()));
            else if ("write_settings".equals(what)) i = new Intent("android.settings.action.MANAGE_WRITE_SETTINGS", Uri.parse("package:" + getPackageName()));
            else if ("battery".equals(what)) i = new Intent("android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS", Uri.parse("package:" + getPackageName()));
            else i = new Intent("android.settings.APPLICATION_DETAILS_SETTINGS", Uri.parse("package:" + getPackageName()));
            try { startActivity(i); } catch (Throwable e) { try { startActivity(new Intent("android.settings.APPLICATION_DETAILS_SETTINGS", Uri.parse("package:" + getPackageName()))); } catch (Throwable ignored) { } }
        }

        @JavascriptInterface public void dashboard() { if (local()) runOnUiThread(new Runnable() { public void run() { web.loadUrl(dashboardUrl()); } }); }

        /** Check that jade-py answers with this token: window.onTest({ok, …}) later. */
        @JavascriptInterface public void test(final String server, final String token) {
            if (!local()) return;
            new Thread(new Runnable() { public void run() {
                String r;
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(base(server) + "/v1/status").openConnection();
                    c.setConnectTimeout(8000); c.setReadTimeout(10000);
                    if (token.length() > 0) c.setRequestProperty("Authorization", "Bearer " + token.trim());
                    int code = c.getResponseCode();
                    String body = LinkClient.read(code >= 400 ? c.getErrorStream() : c.getInputStream());
                    r = code == 200 ? "{\"ok\":true,\"status\":" + body + "}" : code == 401 ? "{\"ok\":false,\"error\":\"jade-py refused the token\"}" : "{\"ok\":false,\"error\":\"HTTP " + code + "\"}";
                } catch (Throwable e) { r = "{\"ok\":false,\"error\":" + MiniJson.quote("cannot reach " + server + ": " + e.getMessage()) + "}"; }
                js("window.onTest&&window.onTest(" + r + ")");
            } }, "link-test").start();
        }
    }

    boolean batteryOptimized() {
        try { return !((android.os.PowerManager) getSystemService(POWER_SERVICE)).isIgnoringBatteryOptimizations(getPackageName()); } catch (Throwable e) { return false; }
    }

    static List<Object> ips() {
        List<Object> out = new ArrayList<Object>();
        try {
            for (NetworkInterface n : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!n.isUp() || n.isLoopback()) continue;
                for (InetAddress a : Collections.list(n.getInetAddresses())) if (a instanceof Inet4Address) out.add(a.getHostAddress());
            }
        } catch (Throwable ignored) { }
        return out;
    }
}
