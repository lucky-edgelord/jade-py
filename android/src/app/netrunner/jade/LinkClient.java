package app.netrunner.jade;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;

/**
 * Dialing out: a long-poll to jade serve. One thread polls (POST /v1/link/poll, held open up to 25 s until jade-py has
 * something to ask), a small pool runs what it asks and posts each answer (POST /v1/link/result), and one thread pushes
 * events (POST /v1/link/events), keeping them until jade-py has taken them. Errors back off from 2 s to a minute.
 * Plain HttpURLConnection, so http and https both work; the API token goes in the Authorization header.
 */
final class LinkClient {
    private final Link link;
    private final Link.Config cfg;
    private final String base;
    private volatile boolean running;
    private Thread poller, pusher;
    private ExecutorService pool;
    private volatile long lastOk, lastTry;
    private volatile String error = "", name = "";
    private volatile int answered;
    private final Object wakePush = new Object();

    LinkClient(Link link, Link.Config cfg) {
        this.link = link; this.cfg = cfg;
        String b = cfg.server.trim(); while (b.endsWith("/")) b = b.substring(0, b.length() - 1);
        if (!b.matches("(?i)https?://.*")) b = "http://" + b;
        base = b;
    }

    void start() {
        running = true;
        pool = Executors.newFixedThreadPool(4, new ThreadFactory() { public Thread newThread(Runnable r) { Thread t = new Thread(r, "link-work"); t.setDaemon(true); return t; } });
        poller = new Thread(new Runnable() { public void run() { pollLoop(); } }, "link-poll"); poller.setDaemon(true); poller.start();
        pusher = new Thread(new Runnable() { public void run() { pushLoop(); } }, "link-push"); pusher.setDaemon(true); pusher.start();
    }

    void stop() {
        running = false;
        if (poller != null) poller.interrupt();
        wake();
        if (pool != null) pool.shutdownNow();
    }

    void wake() { synchronized (wakePush) { wakePush.notifyAll(); } }

    void status(Map<String, Object> m) {
        long now = System.currentTimeMillis();
        boolean connected = lastOk > 0 && now - lastOk < 60000;
        m.put("connected", connected);
        if (name.length() > 0) m.put("name", name);
        if (lastOk > 0) m.put("last_ok_s", (double) ((now - lastOk) / 1000));
        if (!connected && error.length() > 0) m.put("error", error);
        m.put("answered", (double) answered);
    }

    // ---------------------------------------------------------------- polling
    private void pollLoop() {
        long backoff = 2000;
        while (running) {
            lastTry = System.currentTimeMillis();
            try {
                Map<String, Object> dev = link.info.device();
                dev.put("id", cfg.deviceId);
                if (cfg.name.length() > 0) dev.put("name", cfg.name);
                java.util.Map<String, Object> body = new java.util.LinkedHashMap<String, Object>();
                body.put("device", dev); body.put("wait", 25.0);
                String r = post("/v1/link/poll", MiniJson.write(body), 45000);
                Map<String, Object> o = MiniJson.obj(r);
                lastOk = System.currentTimeMillis(); error = ""; backoff = 2000;
                String n = MiniJson.str(o, "name"); if (n != null) name = n;
                List<Object> reqs = MiniJson.l(o, "requests");
                if (reqs != null) for (Object q : reqs) if (q instanceof Map) run(q);
            } catch (Unauthorized e) {
                error = "jade-py refused the token: check the API token";
                sleep(60000);
            } catch (Throwable e) {
                if (!running) return;
                error = describe(e);
                sleep(backoff); backoff = Math.min(60000, backoff * 2);
            }
        }
    }

    @SuppressWarnings("unchecked")
    private void run(final Object q) {
        final Map<String, Object> m = (Map<String, Object>) q;
        final String id = MiniJson.str(m, "id"), op = MiniJson.str(m, "op");
        final Object args = m.get("args");
        if (id == null || op == null) return;
        pool.execute(new Runnable() { public void run() {
            String result = link.answer(op, args == null ? "{}" : MiniJson.write(args));
            String body = "{\"device_id\":" + MiniJson.quote(cfg.deviceId) + ",\"id\":" + MiniJson.quote(id) + ",\"result\":" + result + "}";
            for (int i = 0; i < 3 && running; i++) {
                try { post("/v1/link/result", body, 30000); answered++; return; }
                catch (Throwable e) { error = describe(e); sleep(1000L << i); }
            }
        } });
    }

    // ---------------------------------------------------------------- events
    private void pushLoop() {
        long backoff = 2000;
        while (running) {
            List<Link.Event> batch = new ArrayList<Link.Event>();
            synchronized (link.outbox) { for (Link.Event e : link.outbox) { batch.add(e); if (batch.size() >= 20) break; } }
            if (batch.isEmpty()) { synchronized (wakePush) { try { wakePush.wait(15000); } catch (InterruptedException e) { return; } } continue; }
            StringBuilder b = new StringBuilder("{\"device_id\":").append(MiniJson.quote(cfg.deviceId)).append(",\"events\":[");
            for (int i = 0; i < batch.size(); i++) { if (i > 0) b.append(','); b.append(MiniJson.write(batch.get(i).map())); }
            b.append("]}");
            try {
                post("/v1/link/events", b.toString(), 30000);
                synchronized (link.outbox) { link.outbox.removeAll(batch); }
                backoff = 2000;
            } catch (Throwable e) {
                if (!running) return;
                error = describe(e);
                synchronized (wakePush) { try { wakePush.wait(backoff); } catch (InterruptedException ie) { return; } }
                backoff = Math.min(60000, backoff * 2);
            }
        }
    }

    // ---------------------------------------------------------------- HTTP
    static final class Unauthorized extends IOException { Unauthorized() { super("unauthorized"); } }

    private String post(String path, String json, int readTimeout) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(base + path).openConnection();
        try {
            c.setRequestMethod("POST");
            c.setConnectTimeout(10000); c.setReadTimeout(readTimeout);
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            c.setRequestProperty("Accept", "application/json");
            if (cfg.token.length() > 0) c.setRequestProperty("Authorization", "Bearer " + cfg.token);
            byte[] data = json.getBytes("UTF-8");
            c.setFixedLengthStreamingMode(data.length);
            OutputStream o = c.getOutputStream(); try { o.write(data); } finally { o.close(); }
            int code = c.getResponseCode();
            if (code == 401) throw new Unauthorized();
            InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
            String text = in == null ? "" : read(in);
            if (code >= 400) throw new IOException("HTTP " + code + (text.length() > 0 ? ": " + text.substring(0, Math.min(200, text.length())) : ""));
            return text;
        } finally { c.disconnect(); }
    }

    static String read(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); byte[] buf = new byte[8192]; int n;
        try { while ((n = in.read(buf)) > 0) b.write(buf, 0, n); } finally { in.close(); }
        return b.toString("UTF-8");
    }

    private static String describe(Throwable e) {
        String m = e.getMessage();
        if (e instanceof java.net.ConnectException) return "cannot reach jade-py (is `jade serve` running and reachable from the phone?)";
        if (e instanceof java.net.UnknownHostException) return "unknown host " + m;
        if (e instanceof java.net.SocketTimeoutException) return "jade-py did not answer in time";
        return e.getClass().getSimpleName() + (m == null ? "" : ": " + m);
    }

    private void sleep(long ms) { try { Thread.sleep(ms); } catch (InterruptedException e) { /* stopping */ } }
}
