package app.netrunner.jade;

import android.content.Intent;
import android.net.nsd.NsdManager;
import android.net.nsd.NsdServiceInfo;
import android.net.wifi.WifiManager;

import org.json.JSONObject;

import java.io.File;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Casting to Chromecast / Google TV: finds Cast devices on the Wi-Fi (mDNS "_googlecast._tcp"), plays a URL or a
 * vault file on one (vault files are served to the TV by FileServer), and controls playback and volume.
 * The protocol itself is CastClient. Screen mirroring has no app API: cast_mirror opens Android's Cast screen.
 */
final class CastHost {
    private static final class Dev { String name, model, host; int port; Dev(String n, String m, String h, int p) { name = n; model = m; host = h; port = p; } }
    private final android.content.Context a;
    private final Map<String, Dev> devices = Collections.synchronizedMap(new LinkedHashMap<String, Dev>());
    private CastClient client;
    private FileServer files;
    private String lastHost;

    CastHost(android.content.Context a) { this.a = a; }

    private static String err(String m) { return "{\"error\":" + MiniJson.quote(m) + "}"; }

    String run(String op, String json) {
        try {
            JSONObject o = new JSONObject(json == null || json.length() == 0 ? "{}" : json);
            if (op.equals("cast_devices")) return discover(o.optInt("seconds", 4));
            if (op.equals("cast_play")) return play(o);
            if (op.equals("cast_pause")) return media(o, "PAUSE");
            if (op.equals("cast_resume")) return media(o, "PLAY");
            if (op.equals("cast_stop")) return media(o, "STOP");
            if (op.equals("cast_seek")) return media(o, "SEEK");
            if (op.equals("cast_volume")) { CastClient c = client(o); c.setVolume(o.optDouble("level", 0.5)); return c.summary(); }
            if (op.equals("cast_mute")) { CastClient c = client(o); c.setMuted(o.optBoolean("on", true)); return c.summary(); }
            if (op.equals("cast_status")) { CastClient c = client(o); c.status(); try { c.mediaStatus(); } catch (Exception ignored) { } return c.summary(); }
            if (op.equals("cast_close")) { CastClient c = client(o); c.stopApp(); return "{\"ok\":true,\"note\":\"the player on the TV was closed\"}"; }
            if (op.equals("cast_mirror")) {
                Intent i = new Intent("android.settings.CAST_SETTINGS"); i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                String r = Starter.start(a, i); if (r.contains("\"error\"")) return err("this phone has no Cast screen in its settings; use Quick Settings → Screen cast / Smart View");
                return "{\"ok\":true,\"note\":\"Android's Cast screen is open on the phone: the user picks the TV there\"}";
            }
            return err("unknown cast action " + op);
        } catch (Throwable e) { return err(op + ": " + e.getMessage()); }
    }

    // ---------- finding TVs ----------
    private String discover(int seconds) throws Exception {
        final NsdManager nsd = (NsdManager) a.getSystemService("servicediscovery");
        if (nsd == null) return err("network service discovery is not available");
        WifiManager wm = (WifiManager) a.getApplicationContext().getSystemService("wifi");
        WifiManager.MulticastLock lock = null;
        try { lock = wm.createMulticastLock("jade-cast"); lock.setReferenceCounted(false); lock.acquire(); } catch (Exception ignored) { }
        final List<NsdServiceInfo> found = Collections.synchronizedList(new ArrayList<NsdServiceInfo>());
        NsdManager.DiscoveryListener l = new NsdManager.DiscoveryListener() {
            public void onStartDiscoveryFailed(String t, int c) { }
            public void onStopDiscoveryFailed(String t, int c) { }
            public void onDiscoveryStarted(String t) { }
            public void onDiscoveryStopped(String t) { }
            public void onServiceFound(NsdServiceInfo s) { found.add(s); }
            public void onServiceLost(NsdServiceInfo s) { }
        };
        nsd.discoverServices("_googlecast._tcp", NsdManager.PROTOCOL_DNS_SD, l);
        try { Thread.sleep(Math.max(2, Math.min(15, seconds)) * 1000L); } finally { try { nsd.stopServiceDiscovery(l); } catch (Exception ignored) { } }
        List<NsdServiceInfo> todo;
        synchronized (found) { todo = new ArrayList<NsdServiceInfo>(found); }
        for (NsdServiceInfo s : todo) {   // resolve one at a time: Android refuses parallel resolves
            final NsdServiceInfo[] got = new NsdServiceInfo[1];
            final CountDownLatch done = new CountDownLatch(1);
            try {
                nsd.resolveService(s, new NsdManager.ResolveListener() {
                    public void onResolveFailed(NsdServiceInfo i, int c) { done.countDown(); }
                    public void onServiceResolved(NsdServiceInfo i) { got[0] = i; done.countDown(); }
                });
                done.await(4, TimeUnit.SECONDS);
            } catch (Exception ignored) { }
            NsdServiceInfo r = got[0];
            if (r == null || r.getHost() == null) continue;
            String name = attr(r, "fn"), model = attr(r, "md");
            if (name == null) name = r.getServiceName();
            devices.put(name, new Dev(name, model == null ? "" : model, r.getHost().getHostAddress(), r.getPort()));
        }
        try { if (lock != null) lock.release(); } catch (Exception ignored) { }
        StringBuilder b = new StringBuilder("{\"devices\":[");
        int n = 0;
        synchronized (devices) {
            for (Dev d : devices.values()) { if (n++ > 0) b.append(','); b.append("{\"name\":").append(MiniJson.quote(d.name)).append(",\"model\":").append(MiniJson.quote(d.model)).append(",\"host\":").append(MiniJson.quote(d.host)).append('}'); }
        }
        b.append("]");
        if (n == 0) b.append(",\"note\":\"no Cast devices answered; check the phone and TV are on the same Wi-Fi and the TV is on\"");
        return b.append('}').toString();
    }

    private static String attr(NsdServiceInfo s, String k) {
        try { Map<String, byte[]> m = s.getAttributes(); byte[] v = m == null ? null : m.get(k); return v == null ? null : new String(v, "UTF-8"); } catch (Exception e) { return null; }
    }

    /** device: a name from cast_devices (or part of one), or an IP address; empty = the last one used, or the only one known. */
    private synchronized CastClient client(JSONObject o) throws Exception {
        String want = o.optString("device", "").trim();
        String host = null; int port = 8009;
        if (want.length() == 0) {
            if (client != null && client.isOpen()) return client;
            if (lastHost != null) host = lastHost;
            else if (devices.size() == 1) { Dev d = devices.values().iterator().next(); host = d.host; port = d.port; }
            else if (devices.isEmpty()) { discover(4); if (devices.size() == 1) { Dev d = devices.values().iterator().next(); host = d.host; port = d.port; } }
            if (host == null) throw new Exception(devices.isEmpty() ? "no Cast devices found on this Wi-Fi" : "say which device: " + names());
        } else if (want.matches("[0-9.]+(:\\d+)?")) {
            String[] p = want.split(":"); host = p[0]; if (p.length > 1) port = Integer.parseInt(p[1]);
        } else {
            Dev d = find(want);
            if (d == null) { discover(4); d = find(want); }
            if (d == null) throw new Exception("no Cast device called \"" + want + "\"" + (devices.isEmpty() ? "" : "; found: " + names()));
            host = d.host; port = d.port;
        }
        if (client != null && client.isOpen() && client.host.equals(host)) return client;
        if (client != null) client.close();
        client = new CastClient(host, port, 8000);
        lastHost = host;
        return client;
    }

    private Dev find(String want) {
        String w = want.toLowerCase();
        synchronized (devices) {
            for (Dev d : devices.values()) if (d.name.equalsIgnoreCase(want)) return d;
            for (Dev d : devices.values()) if (d.name.toLowerCase().contains(w) || d.model.toLowerCase().contains(w)) return d;
        }
        return null;
    }

    private String names() { StringBuilder b = new StringBuilder(); synchronized (devices) { for (Dev d : devices.values()) { if (b.length() > 0) b.append(", "); b.append(d.name); } } return b.toString(); }

    // ---------- playing ----------
    private String play(JSONObject o) throws Exception {
        String url = o.optString("url", ""), path = o.optString("path", ""), type = o.optString("type", "");
        String title = o.optString("title", "");
        if (path.length() > 0) {
            // a vault file: jade-py sent its bytes (data_b64); keep it in the cache and serve it to the TV from here
            String data = o.optString("data_b64", "");
            if (data.length() == 0) return err("path needs the file's data (jade-py sends it)");
            File dir = new File(a.getCacheDir(), "cast"); dir.mkdirs();
            File[] old = dir.listFiles(); if (old != null && old.length > 4) for (File x : old) x.delete();
            File f = new File(dir, System.currentTimeMillis() + "-" + new File(path).getName().replaceAll("[^A-Za-z0-9_.-]", "_"));
            java.io.FileOutputStream fo = new java.io.FileOutputStream(f); try { fo.write(android.util.Base64.decode(data, 0)); } finally { fo.close(); }
            if (type.length() == 0) type = FileServer.mime(f.getName());
            String ip = lanIp();
            if (ip == null) return err("the phone is not on Wi-Fi, so the TV cannot fetch the file");
            synchronized (this) { if (files == null) files = new FileServer(0); }
            url = "http://" + ip + ":" + files.port() + files.share(f, type, 6 * 3600 * 1000L);
            if (title.length() == 0) title = f.getName();
        } else if (url.length() == 0) return err("url (an http/https media link) or path (a vault file) is required");
        else if (!url.matches("(?i)https?://.+")) return err("url must be http or https");
        if (path.length() == 0 && url.matches("(?i)https?://(www\\.|m\\.)?(youtube\\.com|youtu\\.be)/.*")) return err("YouTube links cannot be sent to the TV's media player; open the link with phone open_url and the user can tap the Cast button in YouTube");
        if (type.length() == 0) type = path.length() == 0 && o.optBoolean("audio", false) ? "audio/mpeg" : guessType(url);
        if (type.startsWith("text/html")) return err("that looks like a web page; the TV's player needs a direct video, audio or picture link (for YouTube, open the YouTube app and use its Cast button)");
        CastClient c = client(o);
        String bare = url.toLowerCase().replaceAll("[?#].*$", "");
        boolean live = o.has("live") ? o.optBoolean("live", false) : path.length() == 0 && type.startsWith("audio/") && FileServer.mime(bare).equals("application/octet-stream");
        c.load(url, type, title, o.optString("image", ""), live, o.optDouble("start", 0));
        return c.summary();
    }

    private static String guessType(String url) {
        String u = url.toLowerCase(); int q = u.indexOf('?'); if (q > 0) u = u.substring(0, q);
        String t = FileServer.mime(u);
        if (!t.equals("application/octet-stream")) return t;
        return "video/mp4";
    }

    private String media(JSONObject o, String cmd) throws Exception {
        CastClient c = client(o);
        c.media(cmd, o.optDouble("seconds", 0));
        return c.summary();
    }

    /** The phone's address on the home network (Wi-Fi), which the TV can reach. */
    static String lanIp() {
        try {
            String best = null;
            Enumeration<NetworkInterface> it = NetworkInterface.getNetworkInterfaces();
            while (it != null && it.hasMoreElements()) {
                NetworkInterface ni = it.nextElement();
                if (!ni.isUp() || ni.isLoopback()) continue;
                String nm = ni.getName();
                Enumeration<InetAddress> ads = ni.getInetAddresses();
                while (ads.hasMoreElements()) {
                    InetAddress ad = ads.nextElement();
                    if (!(ad instanceof Inet4Address) || !ad.isSiteLocalAddress()) continue;
                    if (nm.startsWith("wlan") || nm.startsWith("eth")) return ad.getHostAddress();
                    if (best == null && !nm.startsWith("rmnet") && !nm.startsWith("ccmni")) best = ad.getHostAddress();
                }
            }
            return best;
        } catch (Exception e) { return null; }
    }

    void destroy() {
        try { if (client != null) client.close(); } catch (Exception ignored) { }
        try { if (files != null) files.close(); } catch (Exception ignored) { }
    }
}
