package app.netrunner.jade;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A small JSON reader and string quoter with no Android dependencies, so the Cast code that uses it
 * can be tested on a plain JVM (android/test). Objects are LinkedHashMaps, arrays Lists, numbers Doubles.
 */
public final class MiniJson {
    private final String s;
    private int i;

    private MiniJson(String s) { this.s = s; }

    public static Object parse(String s) {
        MiniJson p = new MiniJson(s);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.i != s.length()) throw new IllegalArgumentException("trailing characters at " + p.i);
        return v;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> obj(String s) {
        Object o = parse(s);
        if (!(o instanceof Map)) throw new IllegalArgumentException("not a JSON object");
        return (Map<String, Object>) o;
    }

    private void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

    private Object value() {
        if (i >= s.length()) throw new IllegalArgumentException("unexpected end");
        char c = s.charAt(i);
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return string();
        if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
        if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
        if (s.startsWith("null", i)) { i += 4; return null; }
        return number();
    }

    private Map<String, Object> object() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        i++; ws();
        if (s.charAt(i) == '}') { i++; return m; }
        while (true) {
            ws(); String k = string(); ws();
            if (s.charAt(i++) != ':') throw new IllegalArgumentException("expected : at " + (i - 1));
            ws(); m.put(k, value()); ws();
            char c = s.charAt(i++);
            if (c == '}') return m;
            if (c != ',') throw new IllegalArgumentException("expected , or } at " + (i - 1));
        }
    }

    private List<Object> array() {
        List<Object> l = new ArrayList<Object>();
        i++; ws();
        if (s.charAt(i) == ']') { i++; return l; }
        while (true) {
            ws(); l.add(value()); ws();
            char c = s.charAt(i++);
            if (c == ']') return l;
            if (c != ',') throw new IllegalArgumentException("expected , or ] at " + (i - 1));
        }
    }

    private String string() {
        if (s.charAt(i) != '"') throw new IllegalArgumentException("expected a string at " + i);
        i++;
        StringBuilder b = new StringBuilder();
        while (true) {
            char c = s.charAt(i++);
            if (c == '"') return b.toString();
            if (c != '\\') { b.append(c); continue; }
            char e = s.charAt(i++);
            switch (e) {
                case 'n': b.append('\n'); break;
                case 't': b.append('\t'); break;
                case 'r': b.append('\r'); break;
                case 'b': b.append('\b'); break;
                case 'f': b.append('\f'); break;
                case 'u': b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; break;
                default: b.append(e);
            }
        }
    }

    private Double number() {
        int st = i;
        while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
        if (st == i) throw new IllegalArgumentException("unexpected character at " + i);
        return Double.valueOf(s.substring(st, i));
    }

    public static String quote(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (int k = 0; k < s.length(); k++) {
            char c = s.charAt(k);
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c == '\n') b.append("\\n");
            else if (c == '\r') b.append("\\r");
            else if (c == '\t') b.append("\\t");
            else if (c < 0x20 || c == 0x2028 || c == 0x2029) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }

    // ---- small helpers for walking parsed values ----
    @SuppressWarnings("unchecked")
    public static Map<String, Object> m(Object o, String k) { Object v = o instanceof Map ? ((Map<String, Object>) o).get(k) : null; return v instanceof Map ? (Map<String, Object>) v : null; }
    @SuppressWarnings("unchecked")
    public static List<Object> l(Object o, String k) { Object v = o instanceof Map ? ((Map<String, Object>) o).get(k) : null; return v instanceof List ? (List<Object>) v : null; }
    @SuppressWarnings("unchecked")
    public static String str(Object o, String k) { Object v = o instanceof Map ? ((Map<String, Object>) o).get(k) : null; return v == null ? null : v instanceof Double && ((Double) v) == Math.rint((Double) v) ? String.valueOf(((Double) v).longValue()) : String.valueOf(v); }
    @SuppressWarnings("unchecked")
    public static double num(Object o, String k, double dflt) { Object v = o instanceof Map ? ((Map<String, Object>) o).get(k) : null; return v instanceof Double ? (Double) v : dflt; }
    @SuppressWarnings("unchecked")
    public static boolean bool(Object o, String k, boolean dflt) { Object v = o instanceof Map ? ((Map<String, Object>) o).get(k) : null; return v instanceof Boolean ? (Boolean) v : dflt; }

    /** Write a parsed value back out as JSON. */
    @SuppressWarnings("unchecked")
    public static String write(Object v) {
        if (v == null) return "null";
        if (v instanceof String) return quote((String) v);
        if (v instanceof Boolean) return v.toString();
        if (v instanceof Double) { double d = (Double) v; return d == Math.rint(d) && Math.abs(d) < 1e15 ? String.valueOf((long) d) : String.valueOf(d); }
        if (v instanceof Number) return v.toString();
        StringBuilder b = new StringBuilder();
        if (v instanceof Map) {
            b.append('{'); boolean first = true;
            for (Map.Entry<String, Object> e : ((Map<String, Object>) v).entrySet()) { if (!first) b.append(','); first = false; b.append(quote(e.getKey())).append(':').append(write(e.getValue())); }
            return b.append('}').toString();
        }
        if (v instanceof List) {
            b.append('['); boolean first = true;
            for (Object o : (List<Object>) v) { if (!first) b.append(','); first = false; b.append(write(o)); }
            return b.append(']').toString();
        }
        return quote(String.valueOf(v));
    }
}
