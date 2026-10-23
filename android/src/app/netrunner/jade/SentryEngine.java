package app.netrunner.jade;

import java.util.ArrayList;
import java.util.Calendar;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Sentry's rules engine. No Android dependencies, so it is tested on a plain JVM (android/test/SentryTest) with a
 * fake clock. The host (SentryHost) feeds it sensor readings and events and gets back the triggers that fired.
 *
 * A trigger is  { id, when?, on?, for_s, cooldown_s, max_per_hour, hours, rearm, ... }  (see the page's schema).
 *  - "when" is a state condition: all / any / not over leaves {src, op, value, clear}. A leaf with "clear" is a
 *    hysteresis band: it turns on at "value" but only turns off at "clear", so a reading hovering around the
 *    threshold does not flap.
 *  - a state trigger (no "on") FIRES ON THE EDGE: when its condition has held for "for_s" seconds. It will not
 *    fire again until the condition has cleared (rearm "clear", the default) or the cooldown has passed while
 *    it still holds (rearm "cooldown").
 *  - an event trigger ("on": {event: "notification", ...}) fires when a matching event arrives while "when" holds.
 *  - guards on both: cooldown_s, max_per_hour, and an active-hours window.
 *  - after (re)configuring, a condition that is already true is a baseline, not an edge: restarting the app does
 *    not make every standing condition fire.
 */
public final class SentryEngine {
    public interface Clock { long now(); }

    public static final class Fire {
        public final String id, kind;       // kind: "state" or "event"
        public final long at;
        public final Map<String, Object> rule, values, event;
        Fire(String id, String kind, long at, Map<String, Object> rule, Map<String, Object> values, Map<String, Object> event) { this.id = id; this.kind = kind; this.at = at; this.rule = rule; this.values = values; this.event = event; }
    }

    private static final int F = 0, T = 1, U = -1;   // false, true, unknown

    private static final class Node {
        String type;                              // all, any, not, leaf
        List<Node> kids = new ArrayList<Node>();
        String src, op; Object value, clear;
        double lat, lon, radius, clearM;
        String key;                               // latch key
    }

    private static final class Trig {
        String id; String json; Map<String, Object> rule;
        boolean enabled = true; Node when; Map<String, Object> on;
        double forS, cooldownS, maxPerHour; int hoursFrom = -1, hoursTo = -1; boolean rearmCooldown;
        // state
        boolean baselined, holding; long since = -1, lastFire = -1; List<Long> fires = new ArrayList<Long>();
        int suppressed; String lastReason = ""; long lastSuppressed = -1;
        Map<String, Boolean> latch = new HashMap<String, Boolean>();
    }

    private final Clock clock;
    private final Map<String, Object> vals = new LinkedHashMap<String, Object>();
    private final Map<String, Trig> trigs = new LinkedHashMap<String, Trig>();
    private long lastActive = -1;

    public SentryEngine(Clock clock) { this.clock = clock; }

    // ---------- configuration ----------
    /** Replace the rules. Triggers whose definition did not change keep their state. Returns problems found (bad rules are skipped). */
    @SuppressWarnings("unchecked")
    public synchronized List<String> configure(String json) {
        List<String> errs = new ArrayList<String>();
        Map<String, Trig> next = new LinkedHashMap<String, Trig>();
        try {
            Map<String, Object> root = MiniJson.obj(json == null || json.trim().length() == 0 ? "{}" : json);
            List<Object> list = MiniJson.l(root, "triggers");
            if (list != null) for (Object o : list) {
                try {
                    Map<String, Object> r = (Map<String, Object>) o;
                    String id = MiniJson.str(r, "id");
                    if (id == null || id.length() == 0) throw new IllegalArgumentException("a trigger has no id");
                    if (next.containsKey(id)) throw new IllegalArgumentException("duplicate trigger id " + id);
                    String enc = MiniJson.write(r);
                    Trig old = trigs.get(id);
                    Trig t = old != null && old.json.equals(enc) ? old : build(id, r, enc);
                    next.put(id, t);
                } catch (Exception e) { errs.add(String.valueOf(e.getMessage())); }
            }
        } catch (Exception e) { errs.add("the rules are not valid JSON: " + e.getMessage()); }
        trigs.clear(); trigs.putAll(next);
        return errs;
    }

    @SuppressWarnings("unchecked")
    private Trig build(String id, Map<String, Object> r, String enc) {
        Trig t = new Trig(); t.id = id; t.json = enc; t.rule = r;
        t.enabled = MiniJson.bool(r, "enabled", true);
        Map<String, Object> w = MiniJson.m(r, "when");
        if (w != null) t.when = node(w, id + "/w");
        t.on = MiniJson.m(r, "on");
        if (t.on != null && !(t.on.get("event") instanceof String || t.on.get("event") instanceof List)) throw new IllegalArgumentException(id + ": on needs an event name (or a list of names)");
        if (t.when == null && t.on == null) throw new IllegalArgumentException(id + ": needs when or on");
        t.forS = Math.max(0, MiniJson.num(r, "for_s", 0));
        t.cooldownS = Math.max(0, MiniJson.num(r, "cooldown_s", 0));
        t.maxPerHour = Math.max(0, MiniJson.num(r, "max_per_hour", 0));
        t.rearmCooldown = "cooldown".equals(MiniJson.str(r, "rearm"));
        String hrs = MiniJson.str(r, "hours");
        if (hrs != null && hrs.length() > 0) {
            String[] p = hrs.split("-");
            if (p.length != 2) throw new IllegalArgumentException(id + ": hours must look like 22:00-07:00");
            t.hoursFrom = mins(p[0], id); t.hoursTo = mins(p[1], id);
        }
        return t;
    }

    private static int mins(String s, String id) {
        String[] p = s.trim().split(":");
        try { int h = Integer.parseInt(p[0]), m = p.length > 1 ? Integer.parseInt(p[1]) : 0; if (h < 0 || h > 24 || m < 0 || m > 59) throw new NumberFormatException(); return h * 60 + m; }
        catch (Exception e) { throw new IllegalArgumentException(id + ": bad time \"" + s + "\" in hours"); }
    }

    @SuppressWarnings("unchecked")
    private Node node(Map<String, Object> m, String key) {
        Node n = new Node(); n.key = key;
        if (m.containsKey("all") || m.containsKey("any")) {
            boolean all = m.containsKey("all"); n.type = all ? "all" : "any";
            List<Object> l = MiniJson.l(m, all ? "all" : "any");
            if (l == null || l.isEmpty()) throw new IllegalArgumentException(key + ": " + n.type + " needs a list of conditions");
            for (int i = 0; i < l.size(); i++) n.kids.add(node((Map<String, Object>) l.get(i), key + "/" + i));
            return n;
        }
        if (m.containsKey("not")) { n.type = "not"; n.kids.add(node(MiniJson.m(m, "not"), key + "/n")); return n; }
        n.type = "leaf"; n.src = MiniJson.str(m, "src"); n.op = MiniJson.str(m, "op"); n.value = m.get("value"); n.clear = m.get("clear");
        if (n.src == null || n.op == null) throw new IllegalArgumentException(key + ": a condition needs src and op");
        if (n.src.equals("geo")) {
            if (!"inside".equals(n.op) && !"outside".equals(n.op)) throw new IllegalArgumentException(key + ": geo op is inside or outside");
            n.lat = MiniJson.num(m, "lat", Double.NaN); n.lon = MiniJson.num(m, "lon", Double.NaN); n.radius = MiniJson.num(m, "radius_m", 100); n.clearM = MiniJson.num(m, "clear_m", 20);
            if (Double.isNaN(n.lat) || Double.isNaN(n.lon)) throw new IllegalArgumentException(key + ": geo needs lat and lon");
        } else {
            if (!(n.op.equals("lt") || n.op.equals("le") || n.op.equals("gt") || n.op.equals("ge") || n.op.equals("eq") || n.op.equals("ne") || n.op.equals("in"))) throw new IllegalArgumentException(key + ": unknown op " + n.op);
            if (n.value == null) throw new IllegalArgumentException(key + ": a condition needs a value");
        }
        return n;
    }

    // ---------- inputs ----------
    /** Record readings, then evaluate. */
    public synchronized List<Fire> update(Map<String, Object> readings) {
        long now = clock.now();
        vals.putAll(readings);
        return evaluate(now, null);
    }

    public synchronized List<Fire> set(String key, Object value) { Map<String, Object> m = new HashMap<String, Object>(); m.put(key, value); return update(m); }

    public synchronized List<Fire> tick() { return evaluate(clock.now(), null); }

    /** The user did something (unlocked, picked the phone up): resets user.idle_s. */
    public synchronized void touch() { lastActive = clock.now(); }

    /** An event (e.g. a notification). Event triggers that match and whose "when" holds fire. */
    public synchronized List<Fire> event(String name, Map<String, Object> data) { return evaluate(clock.now(), new Object[] { name, data }); }

    // ---------- evaluation ----------
    @SuppressWarnings("unchecked")
    private List<Fire> evaluate(long now, Object[] ev) {
        List<Fire> out = new ArrayList<Fire>();
        for (Trig t : trigs.values()) {
            int c = t.when == null ? T : eval(t, t.when, now);
            if (t.when != null) track(t, c, now);
            if (!t.enabled) continue;
            if (t.on != null) {
                if (ev == null || !matches(t.on, (String) ev[0], (Map<String, Object>) ev[1])) continue;
                if (t.when != null && !(c == T && t.since >= 0 && now - t.since >= t.forS * 1000)) continue;
                String why = guard(t, now);
                if (why != null) { suppress(t, why, now); continue; }
                record(t, now); out.add(new Fire(t.id, "event", now, t.rule, snapshot(now), (Map<String, Object>) ev[1]));
                continue;
            }
            // state trigger
            if (c == U) continue;
            if (!t.baselined) { t.baselined = true; if (c == T) { t.holding = true; } continue; }
            if (c != T) continue;
            if (t.holding && !(t.rearmCooldown && t.lastFire >= 0 && now - t.lastFire >= t.cooldownS * 1000)) continue;
            if (t.since < 0 || now - t.since < t.forS * 1000) continue;
            String why = guard(t, now);
            if (why != null) { if (!t.holding) { t.holding = true; suppress(t, why, now); } else if (t.rearmCooldown) { /* try again later */ } continue; }
            t.holding = true; record(t, now); out.add(new Fire(t.id, "state", now, t.rule, snapshot(now), null));
        }
        return out;
    }

    private void track(Trig t, int c, long now) {
        if (c == T) { if (t.since < 0) t.since = now; }
        else { t.since = -1; if (c == F) t.holding = false; }
    }

    private void suppress(Trig t, String why, long now) { t.suppressed++; t.lastReason = why; t.lastSuppressed = now; }

    private void record(Trig t, long now) {
        t.lastFire = now; t.fires.add(now);
        while (!t.fires.isEmpty() && now - t.fires.get(0) > 3600000L) t.fires.remove(0);
    }

    private String guard(Trig t, long now) {
        if (t.hoursFrom >= 0) {
            int m = minuteOfDay(now), a = t.hoursFrom, b = t.hoursTo;
            boolean in = a <= b ? (m >= a && m < b) : (m >= a || m < b);
            if (!in) return "outside active hours";
        }
        if (t.cooldownS > 0 && t.lastFire >= 0 && now - t.lastFire < t.cooldownS * 1000) return "cooling down";
        if (t.maxPerHour > 0) {
            int n = 0; for (long f : t.fires) if (now - f <= 3600000L) n++;
            if (n >= t.maxPerHour) return "hourly limit reached";
        }
        return null;
    }

    int minuteOfDay(long now) { Calendar c = Calendar.getInstance(); c.setTimeInMillis(now); return c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE); }

    /**
     * Does this event match the trigger's "on"?  on.event is a name or a list of names. on.where is a general filter on the
     * event's data: { field: value } for equality, or { field: { eq, ne, in, contains, starts, ends, gt, ge, lt, le, exists } }.
     * (package, category, min_importance and contains are older shorthands for notification events and still work.)
     */
    @SuppressWarnings("unchecked")
    private boolean matches(Map<String, Object> on, String name, Map<String, Object> d) {
        Object ev = on.get("event"); boolean named = false;
        if (ev instanceof List) { for (Object o : (List<Object>) ev) if (name.equals(String.valueOf(o))) named = true; } else named = name.equals(String.valueOf(ev));
        if (!named) return false;
        if (d == null) d = new HashMap<String, Object>();
        Object where = on.get("where");
        if (where instanceof Map) for (Map.Entry<String, Object> e : ((Map<String, Object>) where).entrySet()) if (!field(d.get(e.getKey()), e.getValue(), d.containsKey(e.getKey()))) return false;
        String pkg = MiniJson.str(on, "package"); if (pkg != null && !pkg.equals(String.valueOf(d.get("package")))) return false;
        String cat = MiniJson.str(on, "category"); if (cat != null && !cat.equals(String.valueOf(d.get("category")))) return false;
        if (on.containsKey("min_importance")) { Object i = d.get("importance"); if (!(i instanceof Number) || ((Number) i).doubleValue() < MiniJson.num(on, "min_importance", 0)) return false; }
        String has = MiniJson.str(on, "contains");
        if (has != null && has.length() > 0) { String hay = (String.valueOf(d.get("title")) + " " + String.valueOf(d.get("text"))).toLowerCase(); if (!hay.contains(has.toLowerCase())) return false; }
        return true;
    }

    @SuppressWarnings("unchecked")
    private static boolean field(Object v, Object spec, boolean present) {
        if (!(spec instanceof Map)) return present && same(v, spec);
        for (Map.Entry<String, Object> e : ((Map<String, Object>) spec).entrySet()) {
            String op = e.getKey(); Object w = e.getValue();
            if (op.equals("exists")) { if (Boolean.TRUE.equals(w) != present) return false; continue; }
            if (!present) return op.equals("ne");
            String sv = str(v), sw = str(w);
            if (op.equals("eq")) { if (!same(v, w)) return false; }
            else if (op.equals("ne")) { if (same(v, w)) return false; }
            else if (op.equals("in")) { boolean hit = false; if (w instanceof List) for (Object o : (List<Object>) w) if (same(v, o)) hit = true; if (!hit) return false; }
            else if (op.equals("contains")) { if (!sv.toLowerCase().contains(sw.toLowerCase())) return false; }
            else if (op.equals("starts")) { if (!sv.toLowerCase().startsWith(sw.toLowerCase())) return false; }
            else if (op.equals("ends")) { if (!sv.toLowerCase().endsWith(sw.toLowerCase())) return false; }
            else if (op.equals("gt") || op.equals("ge") || op.equals("lt") || op.equals("le")) {
                if (!(v instanceof Number) || !(w instanceof Number)) return false;
                double x = ((Number) v).doubleValue(), y = ((Number) w).doubleValue();
                if (op.equals("gt") ? !(x > y) : op.equals("ge") ? !(x >= y) : op.equals("lt") ? !(x < y) : !(x <= y)) return false;
            } else return false;   // an operator we do not know never matches
        }
        return true;
    }

    private static String str(Object o) { return o instanceof Double && ((Double) o) == Math.rint((Double) o) ? String.valueOf(((Double) o).longValue()) : String.valueOf(o); }
    private static boolean same(Object a, Object b) { if (a instanceof Number && b instanceof Number) return ((Number) a).doubleValue() == ((Number) b).doubleValue(); return str(a).equals(str(b)); }

    private Map<String, Object> snapshot(long now) {
        Map<String, Object> m = new LinkedHashMap<String, Object>(vals);
        if (lastActive >= 0) m.put("user.idle_s", Math.round((now - lastActive) / 1000.0));
        return m;
    }

    // ---------- conditions ----------
    private int eval(Trig t, Node n, long now) {
        if (n.type.equals("not")) { int k = eval(t, n.kids.get(0), now); return k == U ? U : k == T ? F : T; }
        if (n.type.equals("all") || n.type.equals("any")) {
            boolean all = n.type.equals("all"); int res = all ? T : F; boolean unknown = false;
            for (Node k : n.kids) {
                int v = eval(t, k, now);   // evaluate every child so their latches stay current
                if (v == U) unknown = true;
                else if (all && v == F) res = F;
                else if (!all && v == T) res = T;
            }
            return res == (all ? F : T) ? res : unknown ? U : res;
        }
        return leaf(t, n, now);
    }

    private Object read(String src, long now) {
        if (src.equals("user.idle_s")) return lastActive < 0 ? null : (now - lastActive) / 1000.0;
        return vals.get(src);
    }

    private int leaf(Trig t, Node n, long now) {
        if (n.src.equals("geo")) {
            Object la = vals.get("geo.lat"), lo = vals.get("geo.lon");
            if (!(la instanceof Number) || !(lo instanceof Number)) return U;
            double d = meters(((Number) la).doubleValue(), ((Number) lo).doubleValue(), n.lat, n.lon);
            Object acc = vals.get("geo.accuracy"); double margin = Math.max(n.clearM, acc instanceof Number ? ((Number) acc).doubleValue() * 0.5 : 0);
            boolean on = Boolean.TRUE.equals(t.latch.get(n.key));
            if (n.op.equals("inside")) on = on ? d <= n.radius + margin : d <= n.radius;
            else on = on ? d > n.radius : d > n.radius + margin;
            t.latch.put(n.key, on); return on ? T : F;
        }
        Object v = read(n.src, now);
        if (v == null) return U;
        boolean on = Boolean.TRUE.equals(t.latch.get(n.key));
        if (v instanceof Number && n.value instanceof Number) {
            double x = ((Number) v).doubleValue(), val = ((Number) n.value).doubleValue();
            double rel = n.clear instanceof Number ? ((Number) n.clear).doubleValue() : val;   // where the condition lets go
            String op = n.op;
            if (op.equals("lt")) on = on ? x < rel : x < val;
            else if (op.equals("le")) on = on ? x <= rel : x <= val;
            else if (op.equals("gt")) on = on ? x > rel : x > val;
            else if (op.equals("ge")) on = on ? x >= rel : x >= val;
            else if (op.equals("eq")) on = x == val;
            else if (op.equals("ne")) on = x != val;
            else if (op.equals("in")) on = false;
            else return U;
        } else if (n.op.equals("in") && n.value instanceof List) {
            on = false; for (Object o : (List<?>) n.value) if (String.valueOf(o).equals(String.valueOf(v))) on = true;
        } else {
            String a = v instanceof Double && ((Double) v) == Math.rint((Double) v) ? String.valueOf(((Double) v).longValue()) : String.valueOf(v);
            String b = n.value instanceof Double && ((Double) n.value) == Math.rint((Double) n.value) ? String.valueOf(((Double) n.value).longValue()) : String.valueOf(n.value);
            if (n.op.equals("eq")) on = a.equals(b); else if (n.op.equals("ne")) on = !a.equals(b); else return U;
        }
        t.latch.put(n.key, on);
        return on ? T : F;
    }

    static double meters(double la1, double lo1, double la2, double lo2) {
        double R = 6371000, p1 = Math.toRadians(la1), p2 = Math.toRadians(la2), dp = p2 - p1, dl = Math.toRadians(lo2 - lo1);
        double a = Math.sin(dp / 2) * Math.sin(dp / 2) + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
        return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    // ---------- scheduling hints and status ----------
    /** Which sources the rules read (so the host only registers the sensors that are needed). */
    @SuppressWarnings("unchecked")
    public synchronized List<String> sources() {
        List<String> out = new ArrayList<String>();
        for (Trig t : trigs.values()) { if (!t.enabled) continue; if (t.when != null) collect(t.when, out); if (t.on != null) { Object ev = t.on.get("event"); List<Object> names = ev instanceof List ? (List<Object>) ev : java.util.Collections.<Object>singletonList(ev); for (Object nm : names) { String e = "event:" + nm; if (!out.contains(e)) out.add(e); } } }
        return out;
    }

    private void collect(Node n, List<String> out) {
        if (n.type.equals("leaf")) { String s = n.src.equals("geo") ? "geo" : n.src; if (!out.contains(s)) out.add(s); return; }
        for (Node k : n.kids) collect(k, out);
    }

    /** Milliseconds until something needs re-evaluating with no new input (a debounce expiring, an idle timer, a cooldown), or -1. */
    public synchronized long nextWakeMs() {
        long now = clock.now(), best = -1;
        for (Trig t : trigs.values()) {
            if (!t.enabled) continue;
            if (t.since >= 0 && t.forS > 0) { long d = t.since + (long) (t.forS * 1000) - now; if (d > 0 && (best < 0 || d < best)) best = d; }
            if (t.rearmCooldown && t.holding && t.lastFire >= 0 && t.cooldownS > 0) { long d = t.lastFire + (long) (t.cooldownS * 1000) - now; if (d > 0 && (best < 0 || d < best)) best = d; }
            if (t.when != null && usesIdle(t.when) && (best < 0 || best > 10000)) best = 10000;
        }
        return best;
    }

    private boolean usesIdle(Node n) { if (n.type.equals("leaf")) return n.src.equals("user.idle_s"); for (Node k : n.kids) if (usesIdle(k)) return true; return false; }

    public synchronized Object value(String key) { return vals.get(key); }

    /** JSON: current readings and each trigger's state, for the sentry tool's state/list actions. */
    public synchronized String status() {
        long now = clock.now();
        Map<String, Object> root = new LinkedHashMap<String, Object>();
        root.put("values", snapshot(now));
        List<Object> l = new ArrayList<Object>();
        for (Trig t : trigs.values()) {
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("id", t.id); m.put("enabled", t.enabled);
            m.put("holding", t.holding);
            if (t.lastFire >= 0) m.put("last_fired_ago_s", Math.round((now - t.lastFire) / 1000.0));
            m.put("fired_last_hour", (double) countHour(t, now));
            if (t.suppressed > 0) { m.put("suppressed", (double) t.suppressed); m.put("last_suppressed_reason", t.lastReason); }
            l.add(m);
        }
        root.put("triggers", l);
        return MiniJson.write(root);
    }

    private int countHour(Trig t, long now) { int n = 0; for (long f : t.fires) if (now - f <= 3600000L) n++; return n; }
}
