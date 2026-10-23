package app.netrunner.jade;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;

/**
 * Listening: jade-py calls the phone. A tiny HTTP/1.1 server (one request per connection):
 *   GET  /health                  public  {"ok":true,"app":"jade-link"}
 *   GET  /v1/info                         the device
 *   POST /v1/call   {op, args}            {"ok":true,"result":…}
 *   GET  /v1/events?since=N&wait=25       {"events":[{seq,type,data,at}…],"seq":N,"info":{…}} (long-poll)
 * Every route but /health needs "Authorization: Bearer <token>" (or ?token=). Binds to all interfaces when lan is on,
 * else to 127.0.0.1 (for adb forward or a VPN app that proxies). Plain HTTP: use it on a trusted network or a VPN.
 */
final class LinkServer {
    static final int MAX_HEAD = 16 * 1024, MAX_BODY = 64 * 1024 * 1024;
    private final Link link;
    private final int port;
    private final byte[] token;
    private final boolean lan;
    private ServerSocket ss;
    private ExecutorService pool;
    private volatile boolean running;
    private volatile String error = "";
    private volatile long lastCall;
    private volatile int calls;

    LinkServer(Link link, int port, String token, boolean lan) {
        this.link = link; this.port = port; this.lan = lan;
        byte[] t; try { t = token.getBytes("UTF-8"); } catch (Exception e) { t = new byte[0]; }
        this.token = t;
    }

    void start() {
        running = true;
        pool = Executors.newFixedThreadPool(8, new ThreadFactory() { public Thread newThread(Runnable r) { Thread t = new Thread(r, "link-http"); t.setDaemon(true); return t; } });
        Thread t = new Thread(new Runnable() { public void run() { acceptLoop(); } }, "link-listen");
        t.setDaemon(true); t.start();
    }

    void stop() {
        running = false;
        try { if (ss != null) ss.close(); } catch (IOException ignored) { }
        if (pool != null) pool.shutdownNow();
    }

    void status(Map<String, Object> m) {
        m.put("listening", ss != null && !ss.isClosed());
        if (error.length() > 0) m.put("error", error);
        m.put("calls", (double) calls);
        if (lastCall > 0) m.put("last_call_s", (double) ((System.currentTimeMillis() - lastCall) / 1000));
    }

    int boundPort() { return ss == null ? -1 : ss.getLocalPort(); }

    private void acceptLoop() {
        long backoff = 2000;
        while (running) {
            try {
                ss = new ServerSocket();
                ss.setReuseAddress(true);
                ss.bind(lan ? new InetSocketAddress(port) : new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 32);
                error = ""; backoff = 2000;
                while (running) {
                    final Socket s = ss.accept();
                    pool.execute(new Runnable() { public void run() { handle(s); } });
                }
            } catch (Throwable e) {
                if (!running) return;
                error = "could not listen on port " + port + ": " + e.getMessage();
                try { if (ss != null) ss.close(); } catch (IOException ignored) { }
                try { Thread.sleep(backoff); } catch (InterruptedException ie) { return; }
                backoff = Math.min(60000, backoff * 2);
            }
        }
    }

    private void handle(Socket s) {
        try {
            s.setSoTimeout(30000);
            InputStream in = s.getInputStream();
            String head = readHead(in);
            if (head == null) return;
            String[] lines = head.split("\r\n");
            String[] rl = lines[0].split(" ");
            if (rl.length < 2) { reply(s, 400, "{\"error\":\"bad request\"}"); return; }
            String method = rl[0], target = rl[1];
            Map<String, String> h = new LinkedHashMap<String, String>();
            for (int i = 1; i < lines.length; i++) { int c = lines[i].indexOf(':'); if (c > 0) h.put(lines[i].substring(0, c).trim().toLowerCase(), lines[i].substring(c + 1).trim()); }
            String path = target, query = "";
            int qm = target.indexOf('?'); if (qm >= 0) { path = target.substring(0, qm); query = target.substring(qm + 1); }
            Map<String, String> q = parseQuery(query);
            if (method.equals("OPTIONS")) { reply(s, 204, ""); return; }
            if (path.equals("/health")) { reply(s, 200, "{\"ok\":true,\"app\":\"jade-link\"}"); return; }
            String auth = h.containsKey("authorization") ? h.get("authorization").replaceFirst("(?i)^Bearer\\s+", "") : q.containsKey("token") ? q.get("token") : "";
            if (!MessageDigest.isEqual(auth.getBytes("UTF-8"), token)) { reply(s, 401, "{\"error\":\"unauthorized: send the listening token shown in Jade Link\"}"); return; }
            int len = 0; try { len = Integer.parseInt(h.containsKey("content-length") ? h.get("content-length") : "0"); } catch (NumberFormatException ignored) { }
            if (len > MAX_BODY) { reply(s, 413, "{\"error\":\"too big\"}"); return; }
            String body = len > 0 ? readBody(in, len) : "";
            lastCall = System.currentTimeMillis(); calls++;
            if (path.equals("/v1/info") && method.equals("GET")) { s.setSoTimeout(0); reply(s, 200, link.answer("info", "{}")); return; }
            if (path.equals("/v1/call") && method.equals("POST")) {
                Map<String, Object> o;
                try { o = MiniJson.obj(body.length() == 0 ? "{}" : body); } catch (Throwable e) { reply(s, 400, "{\"error\":\"the body is not a JSON object\"}"); return; }
                String op = MiniJson.str(o, "op");
                if (op == null || op.length() == 0) { reply(s, 400, "{\"error\":\"op is required\"}"); return; }
                Object args = o.get("args");
                reply(s, 200, "{\"ok\":true,\"result\":" + link.answer(op, args == null ? "{}" : MiniJson.write(args)) + "}");
                return;
            }
            if (path.equals("/v1/events") && method.equals("GET")) {
                long since = -1; double wait = 25;
                try { if (q.containsKey("since")) since = Long.parseLong(q.get("since")); } catch (NumberFormatException ignored) { }
                try { if (q.containsKey("wait")) wait = Math.max(0, Math.min(55, Double.parseDouble(q.get("wait")))); } catch (NumberFormatException ignored) { }
                if (since < 0) since = link.seq();   // a new listener starts from now; Sentry's own queue keeps fires until acknowledged
                List<Link.Event> evs = link.since(since, (long) (wait * 1000));
                StringBuilder b = new StringBuilder("{\"events\":[");
                for (int i = 0; i < evs.size(); i++) { if (i > 0) b.append(','); b.append(MiniJson.write(evs.get(i).map())); }
                b.append("],\"seq\":").append(link.seq()).append(",\"info\":").append(MiniJson.write(link.info.device())).append('}');
                reply(s, 200, b.toString());
                return;
            }
            reply(s, 404, "{\"error\":\"no such route\"}");
        } catch (Throwable e) {
            try { reply(s, 500, "{\"error\":" + MiniJson.quote(String.valueOf(e.getMessage())) + "}"); } catch (Throwable ignored) { }
        } finally {
            try { s.close(); } catch (IOException ignored) { }
        }
    }

    private static String readHead(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int state = 0;
        while (b.size() < MAX_HEAD) {
            int c = in.read();
            if (c < 0) return b.size() == 0 ? null : b.toString("ISO-8859-1");
            b.write(c);
            state = (c == '\r' && (state == 0 || state == 2)) ? state + 1 : (c == '\n' && (state == 1 || state == 3)) ? state + 1 : 0;
            if (state == 4) { String s = b.toString("ISO-8859-1"); return s.substring(0, s.length() - 4); }
        }
        throw new IOException("request head too large");
    }

    private static String readBody(InputStream in, int len) throws IOException {
        byte[] buf = new byte[len]; int off = 0;
        while (off < len) { int n = in.read(buf, off, len - off); if (n < 0) break; off += n; }
        return new String(buf, 0, off, "UTF-8");
    }

    private static Map<String, String> parseQuery(String q) {
        Map<String, String> m = new LinkedHashMap<String, String>();
        if (q.length() == 0) return m;
        for (String kv : q.split("&")) {
            int e = kv.indexOf('=');
            try { m.put(URLDecoder.decode(e < 0 ? kv : kv.substring(0, e), "UTF-8"), e < 0 ? "" : URLDecoder.decode(kv.substring(e + 1), "UTF-8")); } catch (Exception ignored) { }
        }
        return m;
    }

    private static void reply(Socket s, int code, String json) throws IOException {
        byte[] body = json.getBytes("UTF-8");
        String status = code == 200 ? "OK" : code == 204 ? "No Content" : code == 401 ? "Unauthorized" : code == 404 ? "Not Found" : code == 413 ? "Payload Too Large" : code == 400 ? "Bad Request" : "Error";
        String head = "HTTP/1.1 " + code + " " + status + "\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: " + body.length
            + "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
        OutputStream o = s.getOutputStream();
        o.write(head.getBytes("ISO-8859-1")); o.write(body); o.flush();
    }
}
