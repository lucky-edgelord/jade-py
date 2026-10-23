package app.netrunner.jade;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;

/**
 * Jade Link's connection to jade-py, both ways at once, with no Android dependencies (tested on a plain JVM):
 *   - dial out: LinkClient keeps a long-poll open to jade serve (POST /v1/link/poll) and answers what it is asked;
 *   - listen:   LinkServer lets jade-py call in (POST /v1/call, GET /v1/events).
 * Requests are {id, op, args}; the Handler answers each with a JSON string. Events (Sentry fires) go out both ways:
 * pushed to jade serve when dialing out, and kept in a numbered ring for jade-py to long-poll when listening.
 */
public final class Link {
    public interface Handler { String run(String op, String argsJson); }
    public interface Info { Map<String, Object> device(); }

    public static final class Config {
        public String server = "", token = "", deviceId = "", name = "";
        public boolean dialOut, listen;
        public int port = 8765;
        public String listenToken = "";
        public boolean lan = true;
    }

    static final class Event {
        final long seq, at; final String type; final Object data;
        Event(long seq, String type, Object data, long at) { this.seq = seq; this.type = type; this.data = data; this.at = at; }
        Map<String, Object> map() { Map<String, Object> m = new LinkedHashMap<String, Object>(); m.put("seq", (double) seq); m.put("type", type); m.put("data", data); m.put("at", (double) at); return m; }
    }

    final Handler handler;
    final Info info;
    private Config cfg = new Config();
    private LinkClient client;
    private LinkServer server;
    private final LinkedList<Event> ring = new LinkedList<Event>();
    final LinkedList<Event> outbox = new LinkedList<Event>();
    private long seq;
    static final int RING = 200;
    public volatile String log = "";

    public Link(Handler handler, Info info) { this.handler = handler; this.info = info; }

    /** Start, stop or restart the two directions to match the settings. */
    public synchronized void apply(Config c) {
        Config old = cfg; cfg = c;
        boolean clientSame = client != null && c.dialOut && old.dialOut && eq(old.server, c.server) && eq(old.token, c.token) && eq(old.deviceId, c.deviceId) && eq(old.name, c.name);
        if (!clientSame) {
            if (client != null) { client.stop(); client = null; }
            if (c.dialOut && c.server.length() > 0) { client = new LinkClient(this, c); client.start(); }
        }
        boolean serverSame = server != null && c.listen && old.listen && old.port == c.port && eq(old.listenToken, c.listenToken) && old.lan == c.lan;
        if (!serverSame) {
            if (server != null) { server.stop(); server = null; }
            if (c.listen && c.listenToken.length() > 0) { server = new LinkServer(this, c.port, c.listenToken, c.lan); server.start(); }
        }
    }

    public synchronized void stop() {
        if (client != null) { client.stop(); client = null; }
        if (server != null) { server.stop(); server = null; }
    }

    private static boolean eq(String a, String b) { return a == null ? b == null : a.equals(b); }

    public synchronized Config config() { return cfg; }

    // ---------------------------------------------------------------- events
    /** A Sentry fire or anything else for jade-py. Kept in the ring (for listening) and the outbox (for dialing out). */
    public void emit(String type, Object data) {
        synchronized (this) {
            Event e = new Event(++seq, type, data, System.currentTimeMillis());
            ring.add(e); while (ring.size() > RING) ring.removeFirst();
            boolean out = cfg.dialOut;
            if (out) { synchronized (outbox) { outbox.add(e); while (outbox.size() > RING) outbox.removeFirst(); } }
            notifyAll();
        }
        LinkClient c = client; if (c != null) c.wake();
    }

    /** Events newer than `since`, waiting up to waitMs for one to arrive. */
    synchronized List<Event> since(long since, long waitMs) {
        long end = System.currentTimeMillis() + waitMs;
        while (true) {
            List<Event> out = new ArrayList<Event>();
            for (Event e : ring) if (e.seq > since) out.add(e);
            long left = end - System.currentTimeMillis();
            if (!out.isEmpty() || left <= 0) return out;
            try { wait(left); } catch (InterruptedException ie) { return out; }
        }
    }

    synchronized long seq() { return seq; }

    // ---------------------------------------------------------------- status
    public synchronized Map<String, Object> status() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        Map<String, Object> d = new LinkedHashMap<String, Object>();
        d.put("on", cfg.dialOut); d.put("server", cfg.server);
        if (client != null) client.status(d);
        m.put("dial_out", d);
        Map<String, Object> l = new LinkedHashMap<String, Object>();
        l.put("on", cfg.listen); l.put("port", (double) cfg.port);
        if (server != null) server.status(l);
        m.put("listen", l);
        m.put("events", (double) seq);
        return m;
    }

    /** Answer one request; whatever the handler does, the result is valid JSON. */
    String answer(String op, String argsJson) {
        String r;
        try { r = handler.run(op, argsJson == null || argsJson.length() == 0 ? "{}" : argsJson); }
        catch (Throwable e) { r = "{\"error\":" + MiniJson.quote(op + ": " + e) + "}"; }
        if (r == null) return "{\"error\":\"no answer\"}";
        try { MiniJson.parse(r); return r; } catch (Throwable e) { return "{\"text\":" + MiniJson.quote(r) + "}"; }
    }
}
