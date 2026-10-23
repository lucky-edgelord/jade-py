import app.netrunner.jade.CastClient;
import app.netrunner.jade.FileServer;
import app.netrunner.jade.MiniJson;

import java.io.*;
import java.lang.reflect.Method;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.KeyStore;
import java.util.*;
import java.util.concurrent.*;
import javax.net.ssl.*;

/** Cast v2 sender against a fake Chromecast (TLS, protobuf frames, JSON), plus the LAN file server and the JSON reader. */
public class CastTest {
    static int pass = 0, fail = 0;
    static void ok(boolean c, String m) { if (c) { pass++; System.out.println("PASS " + m); } else { fail++; System.out.println("FAIL " + m); } }

    // ---------------- a fake Chromecast ----------------
    static class FakeTV implements Runnable {
        final SSLServerSocket ss;
        final List<String> seen = Collections.synchronizedList(new ArrayList<String>());   // "dst ns type"
        volatile String app = null; volatile String transport = null; volatile String session = null;
        volatile String player = "IDLE"; volatile double pos = 0, vol = 0.5; volatile boolean muted = false; volatile String title = null;
        volatile boolean failLoad = false, slowLaunch = false, dropAfterLaunch = false;
        volatile int pongs = 0; volatile int launches = 0;
        DataOutputStream out;
        FakeTV(SSLContext ctx) throws Exception { ss = (SSLServerSocket) ctx.getServerSocketFactory().createServerSocket(0); Thread t = new Thread(this); t.setDaemon(true); t.start(); }
        int port() { return ss.getLocalPort(); }
        synchronized void send(String src, String dst, String ns, String json) throws IOException {
            byte[] m = enc(src, dst, ns, json); out.writeInt(m.length); out.write(m); out.flush();
        }
        String recvStatus(int rid) {
            String apps = app == null ? "[]" : "[{\"appId\":\"" + app + "\",\"displayName\":\"Default Media Receiver\",\"sessionId\":\"" + session + "\",\"transportId\":\"" + transport + "\"}]";
            return "{\"type\":\"RECEIVER_STATUS\",\"requestId\":" + rid + ",\"status\":{\"applications\":" + apps + ",\"volume\":{\"level\":" + vol + ",\"muted\":" + muted + "}}}";
        }
        String mediaStatus(int rid) {
            if (title == null && player.equals("IDLE")) return "{\"type\":\"MEDIA_STATUS\",\"requestId\":" + rid + ",\"status\":[]}";
            return "{\"type\":\"MEDIA_STATUS\",\"requestId\":" + rid + ",\"status\":[{\"mediaSessionId\":7,\"playerState\":\"" + player + "\",\"currentTime\":" + pos + ",\"media\":{\"duration\":120.5,\"metadata\":{\"title\":" + MiniJson.quote(title) + "}}}]}";
        }
        public void run() {
            try {
                SSLSocket s = (SSLSocket) ss.accept();
                DataInputStream in = new DataInputStream(s.getInputStream()); out = new DataOutputStream(s.getOutputStream());
                while (true) {
                    int n = in.readInt(); byte[] m = new byte[n]; in.readFully(m);
                    String[] f = dec(m);
                    Map<String, Object> p = MiniJson.obj(f[3]);
                    String type = MiniJson.str(p, "type"); int rid = (int) MiniJson.num(p, "requestId", 0);
                    seen.add(f[1] + " " + f[2].replace("urn:x-cast:com.google.cast.", "") + " " + type);
                    if (type.equals("PONG")) pongs++;
                    if (type.equals("PING")) send(f[1], f[0], f[2], "{\"type\":\"PONG\"}");
                    else if (type.equals("GET_STATUS") && f[2].endsWith("receiver")) send("receiver-0", f[0], f[2], recvStatus(rid));
                    else if (type.equals("LAUNCH")) {
                        launches++;
                        if (slowLaunch) { send("receiver-0", f[0], f[2], "{\"type\":\"RECEIVER_STATUS\",\"requestId\":" + rid + ",\"status\":{\"applications\":[]}}"); Thread.sleep(400); }
                        app = MiniJson.str(p, "appId"); transport = "web-5"; session = "sess-1";
                        send("receiver-0", f[0], f[2], recvStatus(slowLaunch ? 0 : rid));
                        if (dropAfterLaunch) { s.close(); return; }
                    }
                    else if (type.equals("SET_VOLUME")) { Map<String, Object> v = MiniJson.m(p, "volume"); if (v.containsKey("level")) vol = MiniJson.num(v, "level", vol); if (v.containsKey("muted")) muted = MiniJson.bool(v, "muted", muted); send("receiver-0", f[0], f[2], recvStatus(rid)); }
                    else if (type.equals("STOP") && f[2].endsWith("receiver")) { app = null; transport = null; session = null; send("receiver-0", f[0], f[2], recvStatus(rid)); }
                    else if (type.equals("LOAD")) {
                        if (!f[1].equals(transport)) { send(f[1], f[0], f[2], "{\"type\":\"INVALID_REQUEST\",\"requestId\":" + rid + ",\"reason\":\"INVALID_COMMAND\"}"); continue; }
                        if (failLoad) { send(f[1], f[0], f[2], "{\"type\":\"LOAD_FAILED\",\"requestId\":" + rid + "}"); continue; }
                        Map<String, Object> media = MiniJson.m(p, "media");
                        title = MiniJson.str(MiniJson.m(media, "metadata"), "title"); player = "PLAYING"; pos = MiniJson.num(p, "currentTime", 0);
                        seen.add("LOADED " + MiniJson.str(media, "contentId") + " " + MiniJson.str(media, "contentType") + " " + MiniJson.str(media, "streamType"));
                        send(f[1], f[0], f[2], mediaStatus(rid));
                    }
                    else if (f[2].endsWith("media")) {
                        if (type.equals("PAUSE")) player = "PAUSED"; else if (type.equals("PLAY")) player = "PLAYING"; else if (type.equals("SEEK")) pos = MiniJson.num(p, "currentTime", 0); else if (type.equals("STOP")) { player = "IDLE"; title = null; }
                        if (rid > 0) send(f[1], f[0], f[2], mediaStatus(rid));
                    }
                }
            } catch (Exception e) { /* client went away */ }
        }
    }

    static Method ENC, DEC;
    static byte[] enc(String a, String b, String c, String d) { try { return (byte[]) ENC.invoke(null, a, b, c, d); } catch (Exception e) { throw new RuntimeException(e); } }
    static String[] dec(byte[] m) { try { return (String[]) DEC.invoke(null, (Object) m); } catch (Exception e) { throw new RuntimeException(e); } }

    static SSLContext serverCtx(String ks) throws Exception {
        KeyStore k = KeyStore.getInstance("JKS"); k.load(new FileInputStream(ks), "testpass".toCharArray());
        KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()); kmf.init(k, "testpass".toCharArray());
        SSLContext c = SSLContext.getInstance("TLS"); c.init(kmf.getKeyManagers(), null, null); return c;
    }

    static String http(String url, String range, String method, int[] code, Map<String, String> hdrs) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod(method); if (range != null) c.setRequestProperty("Range", range);
        code[0] = c.getResponseCode();
        if (hdrs != null) for (String h : new String[] { "Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Access-Control-Allow-Origin" }) if (c.getHeaderField(h) != null) hdrs.put(h, c.getHeaderField(h));
        if (code[0] >= 400 || method.equals("HEAD")) return "";
        InputStream in = c.getInputStream(); ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] buf = new byte[4096]; int r; while ((r = in.read(buf)) > 0) b.write(buf, 0, r); return b.toString("UTF-8");
    }

    static void waitFor(java.util.concurrent.Callable<Boolean> c, int ms) throws Exception { long end = System.currentTimeMillis() + ms; while (!c.call() && System.currentTimeMillis() < end) Thread.sleep(50); }

    public static void main(String[] args) throws Exception {
        ENC = CastClient.class.getDeclaredMethod("encode", String.class, String.class, String.class, String.class); ENC.setAccessible(true);
        DEC = CastClient.class.getDeclaredMethod("decode", byte[].class); DEC.setAccessible(true);

        // ---- JSON ----
        Map<String, Object> j = MiniJson.obj("{\"a\":[1,2.5,{\"b\":\"x\\\"y\\u00e9\\n\"}],\"t\":true,\"n\":null,\"neg\":-3e2}");
        ok(MiniJson.l(j, "a").size() == 3 && "x\"yé\n".equals(MiniJson.str(MiniJson.l(j, "a").get(2), "b")) && MiniJson.bool(j, "t", false) && j.containsKey("n") && MiniJson.num(j, "neg", 0) == -300, "JSON reader handles nesting, escapes, numbers, null");
        ok(MiniJson.write(j).equals("{\"a\":[1,2.5,{\"b\":\"x\\\"yé\\n\"}],\"t\":true,\"n\":null,\"neg\":-300}"), "JSON writer round-trips: " + MiniJson.write(j));
        boolean threw = false; try { MiniJson.parse("{\"a\":1,}"); } catch (Exception e) { threw = true; } ok(threw, "bad JSON is rejected");

        // ---- protobuf framing ----
        String big = new String(new char[300]).replace('\0', 'é');
        String[] rt = dec(enc("sender-0", "receiver-0", "urn:x-cast:com.google.cast.media", "{\"t\":\"" + big + "\"}"));
        ok(rt[0].equals("sender-0") && rt[1].equals("receiver-0") && rt[2].endsWith("media") && rt[3].contains(big), "CastMessage encodes and decodes (multi-byte lengths, UTF-8)");

        SSLContext sctx = serverCtx(args[0]);

        // ---- full cast: connect, launch the player, load, control ----
        FakeTV tv = new FakeTV(sctx);
        CastClient c = new CastClient("127.0.0.1", tv.port(), 3000);
        Map<String, Object> st = c.load("http://192.168.1.5:8080/abc/movie.mp4", "video/mp4", "Movie night", null, false, 0);
        ok("PLAYING".equals(MiniJson.str(st, "playerState")) && c.mediaSessionId == 7, "load launches the Default Media Receiver and plays: " + MiniJson.write(st));
        ok(tv.seen.contains("receiver-0 tp.connection CONNECT") && tv.seen.contains("receiver-0 receiver LAUNCH") && tv.seen.contains("web-5 tp.connection CONNECT") && tv.seen.contains("LOADED http://192.168.1.5:8080/abc/movie.mp4 video/mp4 BUFFERED"), "it connects to the receiver, then to the player, then loads: " + tv.seen);
        ok("PAUSED".equals(MiniJson.str(c.media("PAUSE", 0), "playerState")), "pause");
        ok("PLAYING".equals(MiniJson.str(c.media("PLAY", 0), "playerState")), "play");
        c.media("SEEK", 42); ok(tv.pos == 42, "seek");
        Map<String, Object> vs = c.setVolume(0.8); ok(tv.vol == 0.8 && MiniJson.num(MiniJson.m(vs, "volume"), "level", 0) == 0.8, "volume");
        c.setMuted(true); ok(tv.muted, "mute");
        String sum = c.summary(); ok(sum.contains("\"player\":\"PLAYING\"") && sum.contains("\"title\":\"Movie night\"") && sum.contains("\"position\":42") && sum.contains("\"duration\":121") && sum.contains("\"muted\":true") && sum.contains("Default Media Receiver"), "summary: " + sum);
        // a second load reuses the running player (no second LAUNCH)
        c.load("http://x/song.mp3", "audio/mpeg", "Song", "http://x/cover.jpg", false, 5);
        ok(tv.launches == 1 && "Song".equals(tv.title) && tv.pos == 5, "a second load reuses the player and honours the start time");
        // the TV pings us: we answer
        tv.send("receiver-0", "sender-jade", "urn:x-cast:com.google.cast.tp.heartbeat", "{\"type\":\"PING\"}");
        waitFor(() -> tv.pongs > 0, 2000); ok(tv.pongs > 0, "answers the TV's PING with PONG");
        c.stopApp(); ok(tv.app == null && tv.seen.contains("receiver-0 receiver STOP"), "stop closes the player on the TV");
        boolean err = false; try { c.media("PAUSE", 0); } catch (IOException e) { err = e.getMessage().contains("nothing is playing"); } ok(err, "controls with nothing playing give a clear error");
        c.close(); ok(!c.isOpen(), "close");

        // ---- an already running player is reused; LOAD_FAILED is reported ----
        FakeTV tv2 = new FakeTV(sctx); tv2.app = CastClient.DEFAULT_MEDIA; tv2.transport = "web-9"; tv2.session = "s9"; tv2.failLoad = true;
        CastClient c2 = new CastClient("127.0.0.1", tv2.port(), 3000);
        String msg = ""; try { c2.load("http://x/bad.mkv", "video/x-matroska", null, null, false, 0); } catch (IOException e) { msg = e.getMessage(); }
        ok(tv2.launches == 0 && msg.contains("LOAD_FAILED"), "a running player is reused and LOAD_FAILED is reported: " + msg);
        c2.close();

        // ---- slow launch: we poll until the player shows up ----
        FakeTV tv3 = new FakeTV(sctx); tv3.slowLaunch = true;
        CastClient c3 = new CastClient("127.0.0.1", tv3.port(), 3000);
        c3.load("http://x/live.m3u8", "application/x-mpegURL", "Radio", null, true, 0);
        ok("PLAYING".equals(tv3.player) && tv3.seen.contains("LOADED http://x/live.m3u8 application/x-mpegURL LIVE"), "waits for a slow player launch; live streams are LIVE");
        c3.close();

        // ---- the TV drops the connection ----
        FakeTV tv4 = new FakeTV(sctx); tv4.dropAfterLaunch = true;
        CastClient c4 = new CastClient("127.0.0.1", tv4.port(), 1500);
        String m4 = ""; try { c4.load("http://x/a.mp4", "video/mp4", null, null, false, 0); } catch (IOException e) { m4 = e.getMessage(); }
        waitFor(() -> !c4.isOpen(), 2000);
        ok(!c4.isOpen() && m4.length() > 0, "a dropped connection fails cleanly: " + m4);

        // ---- nobody there ----
        String m5 = ""; try { new CastClient("127.0.0.1", 1, 800); } catch (Exception e) { m5 = String.valueOf(e.getMessage()); } ok(m5.length() > 0, "no TV at that address: " + m5);

        // ---- LAN file server ----
        File dir = new File(args[1]); dir.mkdirs();
        File f = new File(dir, "clip one.mp4"); try (FileOutputStream o = new FileOutputStream(f)) { o.write("0123456789abcdefghij".getBytes("UTF-8")); }
        FileServer fs = new FileServer(0);
        String path = fs.share(f, FileServer.mime(f.getName()), 60000);
        String base = "http://127.0.0.1:" + fs.port();
        int[] code = new int[1]; Map<String, String> h = new HashMap<String, String>();
        String body = http(base + path, null, "GET", code, h);
        ok(code[0] == 200 && body.equals("0123456789abcdefghij") && "video/mp4".equals(h.get("Content-Type")) && "bytes".equals(h.get("Accept-Ranges")) && "*".equals(h.get("Access-Control-Allow-Origin")) && path.endsWith("/clip%20one.mp4"), "serves a shared file: " + code[0] + " " + h + " " + path);
        h.clear(); body = http(base + path, "bytes=5-9", "GET", code, h);
        ok(code[0] == 206 && body.equals("56789") && "bytes 5-9/20".equals(h.get("Content-Range")), "byte range: " + code[0] + " " + body + " " + h);
        body = http(base + path, "bytes=15-", "GET", code, null); ok(code[0] == 206 && body.equals("fghij"), "open-ended range");
        body = http(base + path, "bytes=-3", "GET", code, null); ok(code[0] == 206 && body.equals("hij"), "suffix range");
        http(base + path, "bytes=50-60", "GET", code, null); ok(code[0] == 416, "range past the end: 416");
        h.clear(); http(base + path, null, "HEAD", code, h); ok(code[0] == 200 && "20".equals(h.get("Content-Length")), "HEAD");
        http(base + "/0000/clip%20one.mp4", null, "GET", code, null); ok(code[0] == 404, "unknown paths are 404");
        http(base + "/", null, "GET", code, null); ok(code[0] == 404, "no listing");
        String p2 = fs.share(f, "video/mp4", 1); Thread.sleep(20); http(base + p2, null, "GET", code, null); ok(code[0] == 404, "shares expire");
        ok(FileServer.mime("a.MP3").equals("audio/mpeg") && FileServer.mime("p.jpeg").equals("image/jpeg") && FileServer.mime("x.bin").equals("application/octet-stream"), "mime types");
        fs.close();

        System.out.println("\n" + pass + " passed, " + fail + " failed");
        System.exit(fail == 0 ? 0 : 1);
    }
}
