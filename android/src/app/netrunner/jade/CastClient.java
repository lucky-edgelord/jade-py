package app.netrunner.jade;

import java.io.ByteArrayOutputStream;
import java.io.Closeable;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.Charset;
import java.security.cert.X509Certificate;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * A Google Cast (Chromecast, Google TV, "Chromecast built-in" TVs) sender written from the open Cast v2 protocol:
 * TLS to port 8009, length-prefixed protobuf CastMessages carrying JSON. It launches the Default Media Receiver and
 * plays a URL (video, audio or picture), then pauses, seeks, stops and sets the volume.
 * No Android or Google Play dependencies, so it is tested on a plain JVM against a fake receiver (android/test/CastTest).
 */
public final class CastClient implements Closeable {
    public static final String DEFAULT_MEDIA = "CC1AD845";
    static final String NS_CONN = "urn:x-cast:com.google.cast.tp.connection";
    static final String NS_HB = "urn:x-cast:com.google.cast.tp.heartbeat";
    static final String NS_RECV = "urn:x-cast:com.google.cast.receiver";
    static final String NS_MEDIA = "urn:x-cast:com.google.cast.media";
    static final String SENDER = "sender-jade", RECEIVER = "receiver-0";
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private final SSLSocket sock;
    private final DataOutputStream out;
    private final DataInputStream in;
    private final int timeoutMs;
    private final AtomicInteger reqId = new AtomicInteger(1);
    private final Map<Integer, LinkedBlockingQueue<Map<String, Object>>> waiting = new ConcurrentHashMap<Integer, LinkedBlockingQueue<Map<String, Object>>>();
    private final Map<String, Boolean> connected = new ConcurrentHashMap<String, Boolean>();
    private volatile boolean closed;
    public volatile Map<String, Object> receiverStatus, mediaStatus;
    public volatile String transportId, sessionId, appId;
    public volatile long mediaSessionId = -1;
    public final String host;

    public CastClient(String host, int port, int timeoutMs) throws Exception {
        this.host = host; this.timeoutMs = timeoutMs;
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[] { new X509TrustManager() {   // Cast devices use their own self-signed certificates
            public void checkClientTrusted(X509Certificate[] c, String a) { }
            public void checkServerTrusted(X509Certificate[] c, String a) { }
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        } }, null);
        SSLSocket s = (SSLSocket) ctx.getSocketFactory().createSocket();
        s.connect(new InetSocketAddress(host, port), timeoutMs);
        s.setSoTimeout(0);
        s.startHandshake();
        sock = s;
        out = new DataOutputStream(s.getOutputStream());
        in = new DataInputStream(s.getInputStream());
        Thread r = new Thread(new Runnable() { public void run() { readLoop(); } }, "cast-read");
        r.setDaemon(true); r.start();
        Thread h = new Thread(new Runnable() { public void run() { heartbeat(); } }, "cast-ping");
        h.setDaemon(true); h.start();
        connect(RECEIVER);
    }

    public boolean isOpen() { return !closed; }

    // ---------- protobuf CastMessage ----------
    static byte[] encode(String src, String dst, String ns, String payload) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        b.write(1 << 3); b.write(0);                 // protocol_version = CASTV2_1_0
        field(b, 2, src.getBytes(UTF8));
        field(b, 3, dst.getBytes(UTF8));
        field(b, 4, ns.getBytes(UTF8));
        b.write(5 << 3); b.write(0);                 // payload_type = STRING
        field(b, 6, payload.getBytes(UTF8));
        return b.toByteArray();
    }

    private static void field(ByteArrayOutputStream b, int f, byte[] v) { b.write((f << 3) | 2); varint(b, v.length); b.write(v, 0, v.length); }

    private static void varint(ByteArrayOutputStream b, long v) { while ((v & ~0x7FL) != 0) { b.write((int) ((v & 0x7F) | 0x80)); v >>>= 7; } b.write((int) v); }

    /** [source, destination, namespace, payload] */
    static String[] decode(byte[] m) {
        String[] r = new String[4];
        int i = 0;
        while (i < m.length) {
            long key = 0; int sh = 0;
            while (true) { int c = m[i++] & 0xFF; key |= (long) (c & 0x7F) << sh; if (c < 0x80) break; sh += 7; }
            int f = (int) (key >>> 3), w = (int) (key & 7);
            if (w == 0) { while ((m[i++] & 0x80) != 0) { } continue; }
            if (w != 2) throw new IllegalArgumentException("unexpected wire type " + w);
            long len = 0; sh = 0;
            while (true) { int c = m[i++] & 0xFF; len |= (long) (c & 0x7F) << sh; if (c < 0x80) break; sh += 7; }
            String v = new String(m, i, (int) len, UTF8); i += (int) len;
            if (f == 2) r[0] = v; else if (f == 3) r[1] = v; else if (f == 4) r[2] = v; else if (f == 6) r[3] = v;
        }
        return r;
    }

    // ---------- wire ----------
    private synchronized void send(String dst, String ns, String json) throws IOException {
        byte[] m = encode(SENDER, dst, ns, json);
        out.writeInt(m.length); out.write(m); out.flush();
    }

    private void readLoop() {
        try {
            while (!closed) {
                int n = in.readInt();
                if (n < 0 || n > (1 << 22)) throw new IOException("bad frame length " + n);
                byte[] m = new byte[n]; in.readFully(m);
                String[] f = decode(m);
                if (f[3] == null) continue;
                Map<String, Object> p;
                try { p = MiniJson.obj(f[3]); } catch (Exception e) { continue; }
                String type = MiniJson.str(p, "type");
                if (NS_HB.equals(f[2])) { if ("PING".equals(type)) send(f[0], NS_HB, "{\"type\":\"PONG\"}"); continue; }
                if (NS_CONN.equals(f[2]) && "CLOSE".equals(type)) { connected.remove(f[0]); if (f[0] != null && f[0].equals(transportId)) { transportId = null; mediaSessionId = -1; } continue; }
                if ("RECEIVER_STATUS".equals(type)) onReceiver(MiniJson.m(p, "status"));
                if ("MEDIA_STATUS".equals(type)) onMedia(MiniJson.l(p, "status"));
                double rid = MiniJson.num(p, "requestId", 0);
                if (rid > 0) { LinkedBlockingQueue<Map<String, Object>> q = waiting.get((int) rid); if (q != null) q.offer(p); }
            }
        } catch (Exception e) {
            // the TV closed the connection or the network dropped
        } finally { close(); }
    }

    private void heartbeat() {
        while (!closed) {
            try { Thread.sleep(5000); send(RECEIVER, NS_HB, "{\"type\":\"PING\"}"); } catch (Exception e) { close(); }
        }
    }

    private void onReceiver(Map<String, Object> st) {
        if (st == null) return;
        receiverStatus = st;
        List<Object> apps = MiniJson.l(st, "applications");
        String tid = null, sid = null, aid = null;
        if (apps != null && !apps.isEmpty()) { Object a = apps.get(0); tid = MiniJson.str(a, "transportId"); sid = MiniJson.str(a, "sessionId"); aid = MiniJson.str(a, "appId"); }
        if (tid == null || !tid.equals(transportId)) mediaSessionId = -1;
        transportId = tid; sessionId = sid; appId = aid;
    }

    private void onMedia(List<Object> l) {
        if (l == null || l.isEmpty()) { mediaStatus = null; return; }
        @SuppressWarnings("unchecked") Map<String, Object> s = (Map<String, Object>) l.get(0);
        mediaStatus = s;
        double id = MiniJson.num(s, "mediaSessionId", -1);
        if (id >= 0) mediaSessionId = (long) id;
        if ("IDLE".equals(MiniJson.str(s, "playerState")) && "FINISHED".equals(MiniJson.str(s, "idleReason"))) { /* keep the id so status still answers */ }
    }

    private void connect(String dst) throws IOException {
        if (connected.containsKey(dst)) return;
        send(dst, NS_CONN, "{\"type\":\"CONNECT\",\"origin\":{},\"userAgent\":\"Jade\",\"senderInfo\":{\"sdkType\":2,\"version\":\"15.605.1.3\",\"browserVersion\":\"44.0.2403.30\",\"platform\":4,\"systemVersion\":\"Android\",\"connectionType\":1}}");
        connected.put(dst, Boolean.TRUE);
    }

    /** Send a request (body is a JSON object without its closing brace's requestId) and wait for the answer. */
    private Map<String, Object> request(String dst, String ns, String body) throws Exception {
        int id = reqId.getAndIncrement();
        LinkedBlockingQueue<Map<String, Object>> q = new LinkedBlockingQueue<Map<String, Object>>();
        waiting.put(id, q);
        try {
            String json = body.substring(0, body.length() - 1) + (body.length() > 2 ? "," : "") + "\"requestId\":" + id + "}";
            send(dst, ns, json);
            Map<String, Object> r = q.poll(timeoutMs, TimeUnit.MILLISECONDS);
            if (r == null || (closed && r.isEmpty())) throw new IOException(closed ? "the TV closed the connection" : "the TV did not answer");
            String t = MiniJson.str(r, "type");
            if (t != null && (t.endsWith("_FAILED") || t.endsWith("_ERROR") || t.startsWith("INVALID"))) throw new IOException("the TV said " + t + (MiniJson.str(r, "reason") != null ? " (" + MiniJson.str(r, "reason") + ")" : ""));
            return r;
        } finally { waiting.remove(id); }
    }

    // ---------- receiver ----------
    public Map<String, Object> status() throws Exception { return MiniJson.m(request(RECEIVER, NS_RECV, "{\"type\":\"GET_STATUS\"}"), "status"); }

    /** Make sure the given app is running on the TV (reuse it if it already is) and connect to it. */
    public void launch(String app) throws Exception {
        if (transportId == null || !app.equals(appId)) status();
        if (transportId == null || !app.equals(appId)) {
            request(RECEIVER, NS_RECV, "{\"type\":\"LAUNCH\",\"appId\":" + MiniJson.quote(app) + "}");
            long end = System.currentTimeMillis() + timeoutMs;
            while ((transportId == null || !app.equals(appId)) && System.currentTimeMillis() < end) { Thread.sleep(150); if (transportId == null) status(); }
            if (transportId == null || !app.equals(appId)) throw new IOException("the TV did not start the player");
        }
        connect(transportId);
    }

    public Map<String, Object> setVolume(double level) throws Exception {
        return MiniJson.m(request(RECEIVER, NS_RECV, "{\"type\":\"SET_VOLUME\",\"volume\":{\"level\":" + Math.max(0, Math.min(1, level)) + "}}"), "status");
    }

    public Map<String, Object> setMuted(boolean muted) throws Exception {
        return MiniJson.m(request(RECEIVER, NS_RECV, "{\"type\":\"SET_VOLUME\",\"volume\":{\"muted\":" + muted + "}}"), "status");
    }

    /** Close the player on the TV (back to its home screen / ambient mode). */
    public void stopApp() throws Exception {
        if (sessionId == null) status();
        if (sessionId == null) return;
        request(RECEIVER, NS_RECV, "{\"type\":\"STOP\",\"sessionId\":" + MiniJson.quote(sessionId) + "}");
    }

    // ---------- media ----------
    public Map<String, Object> load(String url, String contentType, String title, String image, boolean live, double startAt) throws Exception {
        launch(DEFAULT_MEDIA);
        StringBuilder m = new StringBuilder("{\"contentId\":").append(MiniJson.quote(url)).append(",\"contentType\":").append(MiniJson.quote(contentType))
            .append(",\"streamType\":\"").append(live ? "LIVE" : "BUFFERED").append("\",\"metadata\":{\"metadataType\":0");
        if (title != null && title.length() > 0) m.append(",\"title\":").append(MiniJson.quote(title));
        if (image != null && image.length() > 0) m.append(",\"images\":[{\"url\":").append(MiniJson.quote(image)).append("}]");
        m.append("}}");
        Map<String, Object> r = request(transportId, NS_MEDIA, "{\"type\":\"LOAD\",\"autoplay\":true,\"currentTime\":" + Math.max(0, startAt) + ",\"media\":" + m + "}");
        onMedia(MiniJson.l(r, "status"));
        return mediaStatus;
    }

    public Map<String, Object> mediaStatus() throws Exception {
        if (transportId == null) status();
        if (transportId == null) return null;
        connect(transportId);
        Map<String, Object> r = request(transportId, NS_MEDIA, "{\"type\":\"GET_STATUS\"}");
        onMedia(MiniJson.l(r, "status"));
        return mediaStatus;
    }

    /** PLAY, PAUSE, STOP or SEEK (seconds). */
    public Map<String, Object> media(String cmd, double seconds) throws Exception {
        if (mediaSessionId < 0) mediaStatus();
        if (mediaSessionId < 0) throw new IOException("nothing is playing on the TV");
        String extra = cmd.equals("SEEK") ? ",\"currentTime\":" + Math.max(0, seconds) : "";
        Map<String, Object> r = request(transportId, NS_MEDIA, "{\"type\":\"" + cmd + "\",\"mediaSessionId\":" + mediaSessionId + extra + "}");
        onMedia(MiniJson.l(r, "status"));
        return mediaStatus;
    }

    /** A short, JSON summary of what the TV is doing. */
    public String summary() {
        StringBuilder b = new StringBuilder("{\"host\":").append(MiniJson.quote(host));
        Map<String, Object> r = receiverStatus;
        if (r != null) {
            Map<String, Object> v = MiniJson.m(r, "volume");
            if (v != null) b.append(",\"volume\":").append(Math.round(MiniJson.num(v, "level", 0) * 100) / 100.0).append(",\"muted\":").append(MiniJson.bool(v, "muted", false));
            List<Object> apps = MiniJson.l(r, "applications");
            if (apps != null && !apps.isEmpty()) b.append(",\"app\":").append(MiniJson.quote(MiniJson.str(apps.get(0), "displayName")));
        }
        Map<String, Object> m = mediaStatus;
        if (m != null) {
            b.append(",\"player\":").append(MiniJson.quote(MiniJson.str(m, "playerState")));
            if (m.get("currentTime") != null) b.append(",\"position\":").append(Math.round(MiniJson.num(m, "currentTime", 0)));
            Map<String, Object> media = MiniJson.m(m, "media");
            if (media != null) {
                if (media.get("duration") != null) b.append(",\"duration\":").append(Math.round(MiniJson.num(media, "duration", 0)));
                Map<String, Object> md = MiniJson.m(media, "metadata");
                if (md != null && md.get("title") != null) b.append(",\"title\":").append(MiniJson.quote(MiniJson.str(md, "title")));
            }
            if (m.get("idleReason") != null) b.append(",\"idleReason\":").append(MiniJson.quote(MiniJson.str(m, "idleReason")));
        }
        return b.append('}').toString();
    }

    @Override
    public void close() {
        if (closed) return;
        closed = true;
        try { sock.close(); } catch (Exception ignored) { }
        for (LinkedBlockingQueue<Map<String, Object>> q : waiting.values()) q.offer(new java.util.HashMap<String, Object>());
    }
}
