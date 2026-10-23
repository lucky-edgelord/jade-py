import app.netrunner.jade.Link;
import app.netrunner.jade.MiniJson;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The real Link (both directions) with a fake phone behind it, for tests/test_link_java.py: it dials out to the
 * jade serve given and listens on a port at the same time. Commands on stdin: "fire <flow>" emits a Sentry fire,
 * "status" prints the link status, "quit" stops. Every op it answers is printed as "OP <op> <args>".
 */
public class LinkTest {
    public static void main(String[] a) throws Exception {
        String server = a[0], token = a[1], listenToken = a[3];
        int port = Integer.parseInt(a[2]);
        final Map<String, Object> rules = new LinkedHashMap<String, Object>();
        final Link[] self = new Link[1];
        Link link = new Link(new Link.Handler() {
            public String run(String op, String args) {
                System.out.println("OP " + op + " " + args); System.out.flush();
                Map<String, Object> o = MiniJson.obj(args);
                if (op.equals("info")) return "{\"model\":\"JVM\",\"android\":\"none\",\"version\":\"test\"}";
                if (op.equals("ask")) return "{\"ok\":true,\"allowed\":true}";
                if (op.equals("phone")) {
                    String act = MiniJson.str(o, "action");
                    if ("volume".equals(act)) return "{\"media\":{\"level\":0.25,\"index\":4,\"max\":15}}";
                    if ("sms_send".equals(act)) return "{\"ok\":true,\"to\":" + MiniJson.quote(MiniJson.str(o, "to")) + ",\"parts\":1}";
                    if ("slow".equals(act)) { try { Thread.sleep(1500); } catch (InterruptedException ignored) { } return "{\"ok\":true,\"slow\":true}"; }
                    if ("boom".equals(act)) throw new IllegalStateException("kaboom");
                    return "{\"error\":\"unknown phone action " + act + "\"}";
                }
                if (op.equals("sentry.configure")) { synchronized (rules) { rules.clear(); rules.putAll(MiniJson.m(o, "rules")); } return "{\"errors\":[],\"sources\":[],\"running\":true}"; }
                if (op.equals("sentry.pending")) return "{\"items\":[],\"expired\":[]}";
                if (op.equals("sentry.ack") || op.equals("sentry.set")) return "{\"ok\":true}";
                if (op.equals("sentry.status")) return "{\"enabled\":true,\"running\":true,\"systems\":{\"running\":[],\"blocked\":{}}}";
                return "{\"error\":\"unknown op " + op + "\"}";
            }
        }, new Link.Info() {
            public Map<String, Object> device() { Map<String, Object> m = new LinkedHashMap<String, Object>(); m.put("model", "JVM"); m.put("version", "test"); return m; }
        });
        self[0] = link;
        Link.Config c = new Link.Config();
        c.server = server; c.token = token; c.deviceId = "jvm-1"; c.name = "JVM Phone"; c.dialOut = true;
        c.listen = true; c.port = port; c.listenToken = listenToken; c.lan = false;
        link.apply(c);
        System.out.println("READY"); System.out.flush();
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        String line; int n = 0;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.equals("quit")) break;
            if (line.equals("status")) { System.out.println("STATUS " + MiniJson.write(link.status())); System.out.flush(); }
            if (line.startsWith("fire ")) {
                Map<String, Object> f = new LinkedHashMap<String, Object>();
                f.put("qid", "jvm@" + (++n)); f.put("id", "jvm"); f.put("kind", "event"); f.put("at", (double) System.currentTimeMillis());
                f.put("flow", line.substring(5)); f.put("max_age_s", 600.0); f.put("values", new LinkedHashMap<String, Object>());
                link.emit("sentry_fire", f);
                System.out.println("FIRED jvm@" + n); System.out.flush();
            }
        }
        link.stop();
    }
}
