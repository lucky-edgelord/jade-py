import app.netrunner.jade.MiniJson;
import app.netrunner.jade.SentryEngine;
import app.netrunner.jade.SentrySystems;

import java.util.*;

/** Sentry's rules engine on a fake clock: edges, debounce, hysteresis, cooldown, hourly limits, hours, events, geofence, hot reload. */
public class SentryTest {
    static int pass = 0, fail = 0;
    static void ok(boolean c, String m) { if (c) { pass++; System.out.println("PASS " + m); } else { fail++; System.out.println("FAIL " + m); } }

    static long T0;
    static long now;
    static SentryEngine eng() { Calendar c = Calendar.getInstance(); c.set(2026, 9, 21, 12, 0, 0); c.set(Calendar.MILLISECOND, 0); T0 = c.getTimeInMillis(); now = T0; return new SentryEngine(new SentryEngine.Clock() { public long now() { return now; } }); }
    static void at(double sec) { now = T0 + (long) (sec * 1000); }
    static Map<String, Object> m(Object... kv) { Map<String, Object> r = new LinkedHashMap<String, Object>(); for (int i = 0; i < kv.length; i += 2) r.put((String) kv[i], kv[i + 1]); return r; }
    static String cfg(String... triggers) { return "{\"triggers\":[" + String.join(",", triggers) + "]}"; }
    static String ids(List<SentryEngine.Fire> f) { StringBuilder b = new StringBuilder(); for (SentryEngine.Fire x : f) b.append(x.id).append(','); return b.toString(); }
    static int n(List<SentryEngine.Fire> f) { return f.size(); }

    public static void main(String[] a) {
        // ---- baseline: a condition that is already true when the rules load does not fire ----
        SentryEngine e = eng();
        ok(e.configure(cfg("{\"id\":\"low\",\"when\":{\"src\":\"battery.level\",\"op\":\"lt\",\"value\":20}}")).isEmpty(), "valid rules load");
        ok(n(e.set("battery.level", 10.0)) == 0, "already low at start: baseline, no fire");
        ok(n(e.set("battery.level", 9.0)) == 0, "still low: still nothing");
        e.set("battery.level", 25.0); ok(n(e.set("battery.level", 15.0)) == 1, "recovers then drops below: fires on the edge");
        ok(n(e.set("battery.level", 14.0)) == 0 && n(e.set("battery.level", 13.0)) == 0, "staying low does not refire (no storm)");

        // ---- hysteresis band: turn on below 20, only off at 30 ----
        e = eng(); e.configure(cfg("{\"id\":\"h\",\"when\":{\"src\":\"battery.level\",\"op\":\"lt\",\"value\":20,\"clear\":30}}"));
        e.set("battery.level", 50.0);
        ok(n(e.set("battery.level", 19.0)) == 1, "hysteresis: fires below 20");
        e.set("battery.level", 22.0); e.set("battery.level", 25.0);
        ok(n(e.set("battery.level", 18.0)) == 0, "hovering inside the band (22, 25, back to 18) does not refire");
        e.set("battery.level", 31.0);
        ok(n(e.set("battery.level", 19.0)) == 1, "after clearing past 30 it can fire again");

        // ---- debounce: for_s ----
        e = eng(); e.configure(cfg("{\"id\":\"d\",\"for_s\":5,\"when\":{\"src\":\"motion.state\",\"op\":\"eq\",\"value\":\"face_down\"}}"));
        e.set("motion.state", "face_up");
        at(10); ok(n(e.set("motion.state", "face_down")) == 0, "debounce: not yet");
        at(13); ok(n(e.tick()) == 0, "debounce: 3 s is not enough");
        ok(e.nextWakeMs() == 2000, "the engine says when to look again: " + e.nextWakeMs());
        at(15.5); ok(n(e.tick()) == 1, "debounce: fires once it has held for 5 s");
        at(20); e.set("motion.state", "face_up"); at(22); e.set("motion.state", "face_down"); at(24); e.set("motion.state", "face_up"); at(30);
        ok(n(e.tick()) == 0, "a flicker shorter than for_s never fires");

        // ---- cooldown and hourly limit ----
        e = eng(); e.configure(cfg("{\"id\":\"c\",\"cooldown_s\":300,\"max_per_hour\":2,\"when\":{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2}}"));
        e.set("light.lux", 100.0);
        at(1); ok(n(e.set("light.lux", 0.0)) == 1, "cooldown: first fire");
        at(2); e.set("light.lux", 100.0); at(3); ok(n(e.set("light.lux", 0.0)) == 0, "cooldown: 2 s later it is suppressed even though the condition re-armed");
        at(400); e.set("light.lux", 100.0); at(401); ok(n(e.set("light.lux", 0.0)) == 1, "after the cooldown it fires again");
        at(800); e.set("light.lux", 100.0); at(801); ok(n(e.set("light.lux", 0.0)) == 0, "hourly limit (2) holds back the third");
        ok(e.status().contains("hourly limit reached") && e.status().contains("\"fired_last_hour\":2"), "status reports the suppression: " + e.status());
        at(3700); e.set("light.lux", 100.0); at(3701); ok(n(e.set("light.lux", 0.0)) == 1, "an hour later the budget is back");

        // ---- all / any / not ----
        e = eng(); e.configure(cfg("{\"id\":\"fd\",\"when\":{\"all\":[{\"src\":\"motion.state\",\"op\":\"eq\",\"value\":\"face_down\"},{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2}]}}",
            "{\"id\":\"any\",\"when\":{\"any\":[{\"src\":\"wifi.connected\",\"op\":\"eq\",\"value\":false},{\"not\":{\"src\":\"battery.charging\",\"op\":\"eq\",\"value\":true}}]}}"));
        e.update(m("motion.state", "face_up", "light.lux", 300.0, "wifi.connected", true, "battery.charging", true));
        ok(n(e.set("motion.state", "face_down")) == 0, "all: one of two true is not enough");
        List<SentryEngine.Fire> f = e.set("light.lux", 0.0); ok(ids(f).equals("fd,"), "all: both true fires: " + ids(f));
        f = e.set("wifi.connected", false); ok(ids(f).equals("any,"), "any: one true fires");
        e.set("wifi.connected", true); f = e.set("battery.charging", false); ok(ids(f).equals("any,"), "not: unplugging fires");

        // ---- unknown sources never fire (and never count as false) ----
        e = eng(); e.configure(cfg("{\"id\":\"u\",\"when\":{\"all\":[{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2},{\"src\":\"never.set\",\"op\":\"eq\",\"value\":1}]}}"));
        ok(n(e.set("light.lux", 0.0)) == 0, "a rule with a source nobody reports does not fire");

        // ---- events: notification while face down ----
        e = eng(); e.configure(cfg("{\"id\":\"n\",\"for_s\":3,\"when\":{\"src\":\"motion.state\",\"op\":\"eq\",\"value\":\"face_down\"},\"on\":{\"event\":\"notification\",\"min_importance\":4,\"contains\":\"urgent\"},\"cooldown_s\":60}"));
        e.set("motion.state", "face_up");
        ok(n(e.event("notification", m("title", "URGENT: call me", "importance", 4.0))) == 0, "event: phone is face up, nothing");
        at(5); e.set("motion.state", "face_down");
        at(6); ok(n(e.event("notification", m("title", "URGENT: call me", "importance", 4.0))) == 0, "event: face down only 1 s (needs 3)");
        at(9); ok(n(e.event("notification", m("title", "lunch?", "importance", 4.0))) == 0, "event: wrong text");
        ok(n(e.event("notification", m("title", "urgent", "importance", 2.0))) == 0, "event: not important enough");
        List<SentryEngine.Fire> nf = e.event("notification", m("title", "URGENT: call me", "text", "now", "importance", 4.0, "package", "com.x"));
        ok(n(nf) == 1 && "event".equals(nf.get(0).kind) && "com.x".equals(nf.get(0).event.get("package")) && "face_down".equals(nf.get(0).values.get("motion.state")), "event: face down 4 s + urgent high-importance notification fires, with the event and the readings attached");
        at(20); ok(n(e.event("notification", m("title", "URGENT again", "importance", 5.0))) == 0, "event: cooldown holds the second one");
        e.configure(cfg("{\"id\":\"p\",\"on\":{\"event\":\"notification\",\"package\":\"com.bank\"}}"));
        ok(n(e.event("notification", m("package", "com.other"))) == 0 && n(e.event("notification", m("package", "com.bank"))) == 1, "event with only a package filter and no state");

        // ---- active hours (12:00 is "now") ----
        e = eng(); e.configure(cfg("{\"id\":\"night\",\"hours\":\"22:00-07:00\",\"when\":{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2}}", "{\"id\":\"day\",\"hours\":\"08:00-18:00\",\"when\":{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2}}"));
        e.set("light.lux", 100.0); f = e.set("light.lux", 0.0);
        ok(ids(f).equals("day,"), "hours: at noon only the daytime rule fires (night wraps midnight): " + ids(f));

        // ---- rearm: cooldown (a rule that repeats while the condition keeps holding) ----
        e = eng(); e.configure(cfg("{\"id\":\"r\",\"rearm\":\"cooldown\",\"cooldown_s\":60,\"when\":{\"src\":\"battery.level\",\"op\":\"lt\",\"value\":10}}"));
        e.set("battery.level", 50.0); at(1); ok(n(e.set("battery.level", 5.0)) == 1, "rearm cooldown: first");
        at(30); ok(n(e.tick()) == 0, "rearm cooldown: not yet"); at(62); ok(n(e.tick()) == 1, "rearm cooldown: repeats after 60 s while still low");

        // ---- geofence with margin ----
        e = eng(); e.configure(cfg("{\"id\":\"home\",\"when\":{\"src\":\"geo\",\"op\":\"inside\",\"lat\":40.0,\"lon\":-74.0,\"radius_m\":100,\"clear_m\":50}}",
            "{\"id\":\"away\",\"when\":{\"src\":\"geo\",\"op\":\"outside\",\"lat\":40.0,\"lon\":-74.0,\"radius_m\":100,\"clear_m\":50}}"));
        e.update(m("geo.lat", 40.01, "geo.lon", -74.0, "geo.accuracy", 10.0));   // ~1.1 km away: baseline "away"
        ok(n(e.update(m("geo.lat", 40.0003, "geo.lon", -74.0))) == 1, "geo: arriving within 100 m fires 'home'");
        ok(n(e.update(m("geo.lat", 40.0013, "geo.lon", -74.0))) == 0, "geo: ~144 m away is inside the exit margin: no 'away' yet (no flapping at the edge)");
        f = e.update(m("geo.lat", 40.003, "geo.lon", -74.0)); ok(ids(f).equals("away,"), "geo: well outside fires 'away': " + ids(f));
        ok(n(e.update(m("geo.lat", 40.0013, "geo.lon", -74.0))) == 0 && n(e.update(m("geo.lat", 40.0004, "geo.lon", -74.0))) == 1, "geo: coming back fires 'home' again");
        ok(Math.abs(SentryEngineAccess.meters(40, -74, 40.001, -74) - 111.2) < 0.5, "distance maths");

        // ---- user idle: ack by touch ----
        e = eng(); e.configure(cfg("{\"id\":\"idle\",\"when\":{\"src\":\"user.idle_s\",\"op\":\"gt\",\"value\":120}}"));
        e.touch(); at(10); ok(n(e.tick()) == 0, "idle: user active 10 s ago");
        ok(e.nextWakeMs() > 0 && e.nextWakeMs() <= 10000, "idle rules ask to be ticked");
        at(60); e.tick(); at(130); ok(n(e.tick()) == 1, "idle: no activity for 130 s fires");
        at(131); e.touch(); at(140); ok(n(e.tick()) == 0, "idle: touching resets it");

        // ---- hot reload keeps the state of unchanged rules ----
        e = eng(); String ruleA = "{\"id\":\"a\",\"cooldown_s\":600,\"when\":{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2}}", ruleB = "{\"id\":\"b\",\"when\":{\"src\":\"battery.level\",\"op\":\"lt\",\"value\":10}}";
        e.configure(cfg(ruleA)); e.set("light.lux", 100.0); at(1); ok(n(e.set("light.lux", 0.0)) == 1, "reload: a fires");
        ok(e.configure(cfg(ruleA, ruleB)).isEmpty(), "reload: adding rule b works while running");
        at(2); e.set("light.lux", 100.0); at(3); ok(n(e.set("light.lux", 0.0)) == 0, "reload: rule a kept its cooldown through the reload (no storm from reloading)");
        e.set("battery.level", 50.0); ok(n(e.set("battery.level", 5.0)) == 1, "reload: the new rule is live");
        e.configure(cfg(ruleB)); ok(e.status().contains("\"id\":\"b\"") && !e.status().contains("\"id\":\"a\""), "reload: a removed rule is gone");

        // ---- disabled, and bad rules are reported but do not break the good ones ----
        e = eng(); List<String> errs = e.configure(cfg("{\"id\":\"off\",\"enabled\":false,\"when\":{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2}}", "{\"id\":\"bad\",\"when\":{\"src\":\"x\",\"op\":\"wobbly\",\"value\":1}}",
            "{\"when\":{\"src\":\"x\",\"op\":\"lt\",\"value\":1}}", "{\"id\":\"nohours\",\"hours\":\"25\",\"when\":{\"src\":\"x\",\"op\":\"lt\",\"value\":1}}", "{\"id\":\"good\",\"when\":{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2}}", "{\"id\":\"geo\",\"when\":{\"src\":\"geo\",\"op\":\"near\",\"lat\":1,\"lon\":1}}"));
        ok(errs.size() == 4, "four bad rules are reported: " + errs);
        e.set("light.lux", 100.0); f = e.set("light.lux", 0.0); ok(ids(f).equals("good,"), "disabled rules never fire, bad ones are skipped, good ones work: " + ids(f));
        ok(!e.configure("{nope").isEmpty(), "malformed JSON is reported");

        // ---- general events: calls, texts, wake word… matched with "where" ----
        e = eng(); e.configure(cfg("{\"id\":\"call\",\"on\":{\"event\":\"call\",\"where\":{\"state\":\"ringing\",\"number\":{\"starts\":\"+1555\"}}}}",
            "{\"id\":\"sms\",\"on\":{\"event\":[\"sms\",\"mms\"],\"where\":{\"text\":{\"contains\":\"Code\"},\"from\":{\"in\":[\"BANK\",\"+15550100\"]}}}}",
            "{\"id\":\"wake\",\"on\":{\"event\":\"wake_word\",\"where\":{\"confidence\":{\"ge\":0.5,\"lt\":1.1},\"phrase\":\"hey choom\"}}}",
            "{\"id\":\"ex\",\"on\":{\"event\":\"headset\",\"where\":{\"name\":{\"exists\":false}}}}"));
        ok(ids(e.event("call", m("state", "ringing", "number", "+15551234"))).equals("call,"), "where: a ringing call from +1555… matches");
        ok(n(e.event("call", m("state", "ended", "number", "+15551234"))) == 0 && n(e.event("call", m("state", "ringing", "number", "+44555"))) == 0, "where: other call states and numbers do not");
        ok(ids(e.event("sms", m("from", "BANK", "text", "Your CODE is 123"))).equals("sms,") && ids(e.event("mms", m("from", "+15550100", "text", "code"))).equals("sms,"), "where: an event list, contains is case-insensitive, in works");
        ok(n(e.event("sms", m("from", "Mum", "text", "code?"))) == 0, "where: sender not in the list");
        ok(ids(e.event("wake_word", m("phrase", "hey choom", "confidence", 0.9))).equals("wake,") && n(e.event("wake_word", m("phrase", "hey choom", "confidence", 0.2))) == 0, "where: numeric ranges and exact match on the wake word");
        ok(n(e.event("headset", m("name", "Buds"))) == 0 && ids(e.event("headset", m("plugged", true))).equals("ex,"), "where: exists:false");
        ok(e.sources().toString().equals("[event:call, event:sms, event:mms, event:wake_word, event:headset]"), "sources lists every event name: " + e.sources());
        e.configure(cfg("{\"id\":\"bad\",\"on\":{\"event\":\"x\",\"where\":{\"a\":{\"wobbly\":1}}}}"));
        ok(n(e.event("x", m("a", 1.0))) == 0, "an unknown where-operator never matches");
        e.configure(cfg("{\"id\":\"bad2\",\"on\":{\"event\":5}}")); ok(e.status().contains("\"triggers\":[]"), "an event that is not a name is rejected");

        // ---- which sensors are needed ----
        e = eng(); e.configure(cfg("{\"id\":\"1\",\"when\":{\"all\":[{\"src\":\"light.lux\",\"op\":\"lt\",\"value\":2},{\"src\":\"geo\",\"op\":\"inside\",\"lat\":1,\"lon\":1}]},\"on\":{\"event\":\"notification\"}}", "{\"id\":\"2\",\"enabled\":false,\"when\":{\"src\":\"battery.level\",\"op\":\"lt\",\"value\":2}}"));
        ok(e.sources().toString().equals("[light.lux, geo, event:notification]"), "sources() lists only what enabled rules read: " + e.sources());

        // ---- systems: add / remove ----
        java.util.List<String> src = java.util.Arrays.asList("light.lux", "motion.state", "geo", "event:sms", "event:notification", "var.armed", "event:wake_word", "wifi.ssid", "user.idle_s", "call.state", "event:volume_key");
        ok(SentrySystems.needed(src).toString().equals("[light, motion, location, sms, notifications, network, screen, calls, volume]"), "sources map to the systems that supply them (custom var.* and wake_word need none): " + SentrySystems.needed(src));
        java.util.Set<String> on = new java.util.LinkedHashSet<String>(java.util.Arrays.asList("light", "location", "network"));
        ok(SentrySystems.active(src, on).toString().equals("[light, location, network]"), "only systems that are both needed and switched on run: " + SentrySystems.active(src, on));
        ok(SentrySystems.missing(src, on).toString().equals("[motion, sms, notifications, screen, calls, volume]"), "a rule's systems that are switched off are reported: " + SentrySystems.missing(src, on));
        ok(SentrySystems.active(java.util.Arrays.asList("var.armed", "event:wake_word"), on).isEmpty(), "rules that only use custom sources start nothing");
        ok(SentrySystems.ALL.get("camera").sensitive && SentrySystems.ALL.get("audio").sensitive && SentrySystems.ALL.get("location").sensitive && SentrySystems.ALL.get("sms").sensitive && SentrySystems.ALL.get("notifications").sensitive && SentrySystems.ALL.get("calls").sensitive, "camera, microphone, location, calls, texts and notifications are marked sensitive");
        ok(!SentrySystems.ALL.get("battery").sensitive && SentrySystems.ALL.get("camera").cost.equals("high") && SentrySystems.ALL.get("battery").cost.equals("none"), "each system carries a battery cost");
        ok(SentrySystems.catalogue().contains("\"id\":\"camera\"") && SentrySystems.ofKey("camera.change").equals("camera") && SentrySystems.ofKey("audio.level").equals("audio") && SentrySystems.ofEvent("volume_key").equals("volume"), "catalogue and key lookups cover the camera, audio and volume sources");

        System.out.println("\n" + pass + " passed, " + fail + " failed");
        System.exit(fail == 0 ? 0 : 1);
    }
}

/** meters() is package-private; reach it through reflection so the test stays in the default package. */
class SentryEngineAccess {
    static double meters(double a, double b, double c, double d) {
        try { java.lang.reflect.Method m = SentryEngine.class.getDeclaredMethod("meters", double.class, double.class, double.class, double.class); m.setAccessible(true); return (Double) m.invoke(null, a, b, c, d); } catch (Exception e) { throw new RuntimeException(e); }
    }
}
