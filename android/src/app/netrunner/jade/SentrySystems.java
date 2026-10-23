package app.netrunner.jade;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The catalogue of Sentry "systems": each source of events is a module that can be added (switched on) or removed
 * (switched off) on its own. A system only runs when it is switched on AND an enabled rule actually uses it, so an
 * unused one costs no battery. Pure Java, tested on a plain JVM.
 */
public final class SentrySystems {
    public static final class Info {
        public final String id, label, cost, perm, gist; public final boolean sensitive;
        Info(String id, String label, String cost, String perm, boolean sensitive, String gist) { this.id = id; this.label = label; this.cost = cost; this.perm = perm; this.sensitive = sensitive; this.gist = gist; }
    }

    public static final Map<String, Info> ALL = new LinkedHashMap<String, Info>();
    private static void add(String id, String label, String cost, String perm, boolean sensitive, String gist) { ALL.put(id, new Info(id, label, cost, perm, sensitive, gist)); }
    static {
        //  id            label                    battery   permission (Android, null = none)        sensitive  what it provides
        add("battery",   "Battery and charger",   "none",   null,                                    false, "state battery.level, battery.charging, battery.temp_c; event charger {plugged}");
        add("network",   "Network and Wi-Fi",     "none",   null,                                    false, "state net.type (wifi/cell/none), wifi.connected, wifi.ssid; event network");
        add("screen",    "Screen and unlock",     "none",   null,                                    false, "state screen.on, user.idle_s (seconds since you last used the phone); events screen, user_present");
        add("headset",   "Headphones",            "none",   null,                                    false, "event headset {plugged, name}");
        add("bluetooth", "Bluetooth devices",     "none",   "android.permission.BLUETOOTH_CONNECT",  false, "event bluetooth {connected, name, address}");
        add("volume",    "Volume and buttons",    "none",   null,                                    false, "events volume {stream, level}, volume_key {key, presses} (needs screen control for the buttons)");
        add("light",     "Light sensor",          "low",    null,                                    false, "state light.lux");
        add("motion",    "Motion and orientation","medium", null,                                    false, "state motion.state (face_down, face_up, upright, tilted), motion.moving, motion.shake; event shake");
        add("location",  "Location and geofences","medium", "android.permission.ACCESS_FINE_LOCATION", true, "state geo.lat, geo.lon, geo.accuracy (use geo conditions with a radius)");
        add("calls",     "Phone calls",           "none",   "android.permission.READ_PHONE_STATE",   true,  "state call.state; event call {state: ringing/answered/outgoing/ended/missed, number}");
        add("sms",       "Incoming texts",        "none",   "android.permission.RECEIVE_SMS",        true,  "event sms {from, text}");
        add("notifications", "App notifications", "none",   null,                                    true,  "event notification {package, title, text, category, importance} (needs notification access)");
        add("app",       "App in front",          "none",   null,                                    false, "state app.foreground; event app {package, label} (needs screen control switched on in Accessibility)");
        add("audio",     "Sound level (microphone)", "high", "android.permission.RECORD_AUDIO",      true,  "state audio.level (0-100; a quiet room is roughly 25-40, talking 55-70, a clap or shout 75+, and it varies by phone so test it); nothing is recorded or kept");
        add("camera",    "Camera change detection", "high", "android.permission.CAMERA",             true,  "state camera.change (percent of the picture that changed), camera.brightness; a rule can ask to keep a frame");
    }

    /** Which system supplies a state key such as "motion.state", or null if it is custom (var.*, anything the page sets). */
    public static String ofKey(String key) {
        if (key == null) return null;
        if (key.equals("geo")) return "location";
        int dot = key.indexOf('.'); String head = dot < 0 ? key : key.substring(0, dot);
        if (head.equals("wifi") || head.equals("net")) return "network";
        if (head.equals("user")) return "screen";
        if (head.equals("geo")) return "location";
        if (head.equals("call")) return "calls";
        return ALL.containsKey(head) ? head : null;
    }

    /** Which system supplies an event name such as "sms", or null if it is custom (wake_word, anything the page emits). */
    public static String ofEvent(String name) {
        if (name == null) return null;
        switch (name) {
            case "charger": return "battery";
            case "network": return "network";
            case "screen": case "user_present": return "screen";
            case "headset": return "headset";
            case "bluetooth": return "bluetooth";
            case "volume": case "volume_key": return "volume";
            case "shake": return "motion";
            case "call": return "calls";
            case "sms": case "mms": return "sms";
            case "notification": return "notifications";
            case "app": return "app";
            default: return null;
        }
    }

    /** Systems the given engine sources ("motion.state", "geo", "event:sms"…) call for. */
    public static Set<String> needed(List<String> sources) {
        Set<String> out = new LinkedHashSet<String>();
        for (String s : sources) { String sys = s.startsWith("event:") ? ofEvent(s.substring(6)) : ofKey(s); if (sys != null) out.add(sys); }
        return out;
    }

    /** needed ∩ enabled: what actually has to run. */
    public static Set<String> active(List<String> sources, Set<String> enabled) {
        Set<String> out = new LinkedHashSet<String>(needed(sources)); out.retainAll(enabled); return out;
    }

    /** Systems a rule needs that are switched off (the rule cannot fire until they are on). */
    public static Set<String> missing(List<String> sources, Set<String> enabled) {
        Set<String> out = new LinkedHashSet<String>(needed(sources)); out.removeAll(enabled); return out;
    }

    /** Catalogue as JSON for the page: [{id,label,cost,perm,sensitive,gist}]. */
    public static String catalogue() {
        List<Object> l = new ArrayList<Object>();
        for (Info i : ALL.values()) {
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("id", i.id); m.put("label", i.label); m.put("cost", i.cost); m.put("perm", i.perm); m.put("sensitive", i.sensitive); m.put("gist", i.gist);
            l.add(m);
        }
        return MiniJson.write(l);
    }
}
