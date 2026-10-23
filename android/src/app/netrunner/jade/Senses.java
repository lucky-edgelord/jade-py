package app.netrunner.jade;

import android.content.Context;
import android.content.Intent;
import android.graphics.ImageFormat;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.Image;
import android.media.ImageReader;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Base64;
import android.util.Size;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * The senses tool's Android half: sensors (sampled and summarised), battery, Wi-Fi, location, the camera (a still
 * JPEG through camera2), the microphone (a WAV, or just how loud), the flashlight, vibration and permissions.
 * Android only lets a background service use the camera and microphone if it was started while Jade Link was on
 * screen; the link service is, when the app is opened.
 */
final class Senses {
    private final Context c;
    final World world;
    private HandlerThread bg;
    private Handler bgH;

    Senses(Context c) { this.c = c; this.world = new World(c); }

    private static String err(String m) { return "{\"error\":" + MiniJson.quote(m) + "}"; }

    private synchronized Handler bg() {
        if (bg == null) { bg = new HandlerThread("senses"); bg.start(); bgH = new Handler(bg.getLooper()); }
        return bgH;
    }

    boolean has(String perm) { try { return c.checkSelfPermission(perm) == 0; } catch (Throwable e) { return false; } }

    String run(JSONObject o) {
        String act = o.optString("action", "list");
        try {
            if (act.equals("list")) return world.sensorList();
            if (act.equals("device")) return device();
            if (act.equals("battery")) return world.battery();
            if (act.equals("wifi")) return world.wifi();
            if (act.equals("read")) return read(o);
            if (act.equals("location")) return location(o.optDouble("timeout", 20));
            if (act.equals("torch") || act.equals("flashlight")) return torch(o);
            if (act.equals("vibrate")) { JSONArray p = o.optJSONArray("pattern"); StringBuilder b = new StringBuilder(); if (p != null) for (int i = 0; i < p.length(); i++) { if (i > 0) b.append(','); b.append(p.optLong(i, 0)); } return world.vibrate(b.toString()); }
            if (act.equals("camera_snap")) return snap(o.optString("facing", "back"), o.optInt("max", 1600));
            if (act.equals("camera_watch")) return watch(o);
            if (act.equals("mic_record")) return micRecord(o.optDouble("seconds", 5), true);
            if (act.equals("mic_level")) return micRecord(o.optDouble("seconds", 2), false);
            if (act.equals("perms")) return Perms.status(c, Perms.SENSES);
            return err("unknown senses action " + act + " (camera_clip, transcribe and voice_mode are only in the Jade Vault app)");
        } catch (SecurityException e) {
            return err("Android permission missing for " + act + ": open Jade Link and allow it (" + e.getMessage() + ")");
        } catch (Throwable e) { return err(act + ": " + e.getMessage()); }
    }

    // ---------------------------------------------------------------- device
    String device() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("manufacturer", Build.MANUFACTURER); m.put("model", Build.MODEL); m.put("android", Build.VERSION.RELEASE); m.put("sdk", (double) Build.VERSION.SDK_INT);
        try { m.put("battery", MiniJson.parse(world.battery())); } catch (Throwable ignored) { }
        try { android.util.DisplayMetrics d = c.getResources().getDisplayMetrics(); m.put("screen", d.widthPixels + "x" + d.heightPixels); } catch (Throwable ignored) { }
        return MiniJson.write(m);
    }

    // ---------------------------------------------------------------- sensors
    String read(JSONObject o) throws Exception {
        String k = o.optString("sensor", "");
        final Sensor s = world.find(k);
        if (s == null) return err("no sensor called " + k + " (see list)");
        double secs = Math.max(0.2, Math.min(30, o.optDouble("seconds", 1)));
        double hz = Math.max(1, Math.min(100, o.optDouble("hz", 10)));
        final boolean raw = o.optBoolean("raw", false);
        final List<float[]> samples = new ArrayList<float[]>();
        final long gap = (long) (1000 / hz);
        final long[] last = { 0 };
        SensorEventListener l = new SensorEventListener() {
            public void onSensorChanged(SensorEvent e) {
                long now = System.currentTimeMillis(); if (now - last[0] < gap) return; last[0] = now;
                synchronized (samples) { if (samples.size() < 3000) samples.add(e.values.clone()); }
            }
            public void onAccuracyChanged(Sensor x, int a) { }
        };
        SensorManager sm = world.sm();
        if (!sm.registerListener(l, s, (int) (gap * 1000), bg())) return err("the system refused to start " + k + " (it may need a permission)");
        try { Thread.sleep((long) (secs * 1000)); } finally { sm.unregisterListener(l); }
        List<float[]> got; synchronized (samples) { got = new ArrayList<float[]>(samples); }
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("sensor", World.key(s)); m.put("samples", (double) got.size()); m.put("seconds", secs);
        if (got.isEmpty()) { m.put("note", "no readings arrived (some sensors only report when something changes)"); return MiniJson.write(m); }
        int n = got.get(0).length;
        List<Object> axes = new ArrayList<Object>();
        for (int i = 0; i < n; i++) {
            double sum = 0, mn = Double.MAX_VALUE, mx = -Double.MAX_VALUE;
            for (float[] v : got) { double x = i < v.length ? v[i] : 0; sum += x; mn = Math.min(mn, x); mx = Math.max(mx, x); }
            double mean = sum / got.size(), var = 0;
            for (float[] v : got) { double x = i < v.length ? v[i] : 0; var += (x - mean) * (x - mean); }
            Map<String, Object> a = new LinkedHashMap<String, Object>();
            a.put("mean", r4(mean)); a.put("min", r4(mn)); a.put("max", r4(mx)); a.put("std", r4(Math.sqrt(var / got.size())));
            axes.add(a);
        }
        m.put("axes", axes);
        if (raw) { List<Object> rs = new ArrayList<Object>(); for (int j = 0; j < Math.min(200, got.size()); j++) { List<Object> row = new ArrayList<Object>(); for (float f : got.get(j)) row.add(r4(f)); rs.add(row); } m.put("raw", rs); }
        return MiniJson.write(m);
    }

    private static double r4(double x) { return Double.isNaN(x) || Double.isInfinite(x) ? 0 : Math.round(x * 10000) / 10000.0; }

    // ---------------------------------------------------------------- location
    @SuppressWarnings("MissingPermission")
    String location(double timeout) throws Exception {
        if (!has("android.permission.ACCESS_FINE_LOCATION") && !has("android.permission.ACCESS_COARSE_LOCATION")) return err("location permission is not given (perms, or open Jade Link)");
        final LocationManager lm = (LocationManager) c.getSystemService(Context.LOCATION_SERVICE);
        final Location[] best = { null };
        final CountDownLatch got = new CountDownLatch(1);
        final LocationListener l = new LocationListener() { public void onLocationChanged(Location loc) {
            synchronized (best) { if (best[0] == null || loc.getAccuracy() < best[0].getAccuracy()) best[0] = loc; }
            if (loc.getAccuracy() <= 30) got.countDown();
        } };
        Location last = null;
        boolean any = false;
        for (String p : new String[] { LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER }) {
            try {
                if (!lm.isProviderEnabled(p)) continue;
                Location x = lm.getLastKnownLocation(p);
                if (x != null && (last == null || x.getTime() > last.getTime())) last = x;
                lm.requestLocationUpdates(p, 1000, 0f, l, Looper.getMainLooper()); any = true;
            } catch (SecurityException e) { throw e; } catch (Throwable ignored) { }
        }
        if (!any && last == null) return err("location is switched off on the phone");
        try { got.await((long) (Math.max(3, Math.min(120, timeout)) * 1000), TimeUnit.MILLISECONDS); } finally { lm.removeUpdates(l); }
        Location loc; synchronized (best) { loc = best[0]; }
        boolean fresh = loc != null;
        if (loc == null) loc = last;
        if (loc == null) return err("no position yet (indoors? try again outside, or a longer timeout)");
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("lat", loc.getLatitude()); m.put("lon", loc.getLongitude()); m.put("accuracy_m", (double) Math.round(loc.getAccuracy()));
        if (loc.hasAltitude()) m.put("altitude_m", (double) Math.round(loc.getAltitude()));
        if (loc.hasSpeed()) m.put("speed_mps", (double) loc.getSpeed());
        m.put("provider", String.valueOf(loc.getProvider())); m.put("age_s", (double) Math.max(0, (System.currentTimeMillis() - loc.getTime()) / 1000));
        if (!fresh) m.put("note", "the phone's last known position (no fresh fix within the timeout)");
        m.put("map", "https://www.openstreetmap.org/?mlat=" + loc.getLatitude() + "&mlon=" + loc.getLongitude() + "#map=17/" + loc.getLatitude() + "/" + loc.getLongitude());
        return MiniJson.write(m);
    }

    // ---------------------------------------------------------------- torch
    private Runnable torchOff;
    String torch(JSONObject o) throws Exception {
        int blink = Math.max(0, Math.min(20, o.optInt("blink", 0)));
        if (blink > 0) {
            long gap = Math.max(80, Math.min(2000, o.optLong("ms", 300)));
            for (int i = 0; i < blink; i++) { String r = world.torch(true); if (r.contains("\"error\"")) return r; Thread.sleep(gap); world.torch(false); Thread.sleep(gap); }
            return "{\"ok\":true,\"blinked\":" + blink + ",\"on\":false}";
        }
        boolean on = o.optBoolean("on", true);
        Handler h = new Handler(Looper.getMainLooper());
        if (torchOff != null) h.removeCallbacks(torchOff);
        String r = world.torch(on);
        double sec = on ? Math.max(0, Math.min(600, o.optDouble("seconds", 0))) : 0;
        if (sec > 0 && !r.contains("\"error\"")) { torchOff = new Runnable() { public void run() { world.torch(false); } }; h.postDelayed(torchOff, (long) (sec * 1000)); return "{\"ok\":true,\"on\":true,\"offAfterSeconds\":" + sec + "}"; }
        return r;
    }

    // ---------------------------------------------------------------- camera
    private String pickCamera(CameraManager cm, String facing) throws Exception {
        int want = "front".equals(facing) ? 0 /* LENS_FACING_FRONT */ : 1 /* LENS_FACING_BACK */;
        String first = null;
        for (String id : cm.getCameraIdList()) {
            Integer f = cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING);
            if (first == null) first = id;
            if (f != null && f == want) return id;
        }
        return first;
    }

    /** One still photo as a JPEG (largest size up to max px on the long side), after letting exposure settle. */
    synchronized String snap(String facing, int maxPx) throws Exception {
        Map<String, Object> m = snapMap(facing, maxPx);
        return MiniJson.write(m);
    }

    private Map<String, Object> snapMap(String facing, int maxPx) throws Exception {
        if (!has("android.permission.CAMERA")) throw new SecurityException("the camera permission is not given");
        final CameraManager cm = (CameraManager) c.getSystemService(Context.CAMERA_SERVICE);
        final String id = pickCamera(cm, facing);
        if (id == null) throw new IllegalStateException("this phone has no camera");
        CameraCharacteristics cc = cm.getCameraCharacteristics(id);
        StreamConfigurationMap map = cc.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        Size best = null;
        int cap = Math.max(320, Math.min(4096, maxPx));
        for (Size s : map.getOutputSizes(ImageFormat.JPEG)) {
            if (Math.max(s.getWidth(), s.getHeight()) > cap) continue;
            if (best == null || s.getWidth() * s.getHeight() > best.getWidth() * best.getHeight()) best = s;
        }
        if (best == null) { Size[] all = map.getOutputSizes(ImageFormat.JPEG); best = all[all.length - 1]; }
        Integer so = cc.get(CameraCharacteristics.SENSOR_ORIENTATION);
        final int orient = so == null ? 0 : so;
        final Handler h = bg();
        final ImageReader jpeg = ImageReader.newInstance(best.getWidth(), best.getHeight(), ImageFormat.JPEG, 2);
        final ImageReader warm = ImageReader.newInstance(320, 240, ImageFormat.YUV_420_888, 2);
        final byte[][] out = { null };
        final String[] fail = { null };
        final CountDownLatch opened = new CountDownLatch(1), shot = new CountDownLatch(1);
        final CameraDevice[] dev = { null };
        final CameraCaptureSession[] sess = { null };
        warm.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() { public void onImageAvailable(ImageReader r) { Image i = r.acquireLatestImage(); if (i != null) i.close(); } }, h);
        jpeg.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() { public void onImageAvailable(ImageReader r) {
            Image i = r.acquireLatestImage(); if (i == null) return;
            try { ByteBuffer b = i.getPlanes()[0].getBuffer(); byte[] d = new byte[b.remaining()]; b.get(d); out[0] = d; } finally { i.close(); shot.countDown(); }
        } }, h);
        try {
            cm.openCamera(id, new CameraDevice.StateCallback() {
                public void onOpened(CameraDevice d) {
                    dev[0] = d;
                    try {
                        List<android.view.Surface> outs = new ArrayList<android.view.Surface>(); outs.add(warm.getSurface()); outs.add(jpeg.getSurface());
                        d.createCaptureSession(outs, new CameraCaptureSession.StateCallback() {
                            public void onConfigured(CameraCaptureSession s) { sess[0] = s; opened.countDown(); }
                            public void onConfigureFailed(CameraCaptureSession s) { fail[0] = "the camera would not start"; opened.countDown(); }
                        }, h);
                    } catch (Throwable e) { fail[0] = "the camera would not start: " + e.getMessage(); opened.countDown(); }
                }
                public void onDisconnected(CameraDevice d) { fail[0] = "the camera was taken by another app"; opened.countDown(); shot.countDown(); }
                public void onError(CameraDevice d, int e) { fail[0] = "the camera reported error " + e + (e == 3 || e == 4 ? " (Android allows the camera only to an app on screen or a service it started while on screen: open Jade Link once)" : ""); opened.countDown(); shot.countDown(); }
            }, h);
            if (!opened.await(8, TimeUnit.SECONDS)) throw new IllegalStateException("the camera did not open in time");
            if (fail[0] != null) throw new IllegalStateException(fail[0]);
            CaptureRequest.Builder pv = dev[0].createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
            pv.addTarget(warm.getSurface());
            pv.set(CaptureRequest.CONTROL_MODE, 1 /* AUTO */);
            sess[0].setRepeatingRequest(pv.build(), null, h);
            Thread.sleep(900);   // auto exposure, focus and white balance settle
            CaptureRequest.Builder still = dev[0].createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE);
            still.addTarget(jpeg.getSurface());
            still.set(CaptureRequest.CONTROL_MODE, 1);
            still.set(CaptureRequest.JPEG_ORIENTATION, orient);
            still.set(CaptureRequest.JPEG_QUALITY, (byte) 85);
            sess[0].capture(still.build(), null, h);
            if (!shot.await(8, TimeUnit.SECONDS) || out[0] == null) throw new IllegalStateException(fail[0] != null ? fail[0] : "the camera took no picture");
        } finally {
            try { if (sess[0] != null) sess[0].close(); } catch (Throwable ignored) { }
            try { if (dev[0] != null) dev[0].close(); } catch (Throwable ignored) { }
            try { jpeg.close(); } catch (Throwable ignored) { }
            try { warm.close(); } catch (Throwable ignored) { }
        }
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("ok", true); m.put("facing", "front".equals(facing) ? "front" : "back");
        m.put("width", (double) best.getWidth()); m.put("height", (double) best.getHeight()); m.put("bytes", (double) out[0].length);
        m.put("jpeg_base64", Base64.encodeToString(out[0], Base64.NO_WRAP));
        return m;
    }

    String watch(JSONObject o) throws Exception {
        int frames = Math.max(1, Math.min(8, o.optInt("frames", 3)));
        double every = Math.max(0.5, Math.min(60, o.optDouble("every", 2)));
        List<Object> got = new ArrayList<Object>();
        for (int i = 0; i < frames; i++) {
            long t0 = System.currentTimeMillis();
            Map<String, Object> f = snapMap(o.optString("facing", "back"), o.optInt("max", 1024));
            f.put("t", (double) t0); got.add(f);
            long left = (long) (every * 1000) - (System.currentTimeMillis() - t0);
            if (i < frames - 1 && left > 0) Thread.sleep(left);
        }
        Map<String, Object> m = new LinkedHashMap<String, Object>(); m.put("ok", true); m.put("frames", got);
        return MiniJson.write(m);
    }

    // ---------------------------------------------------------------- microphone
    String micRecord(double seconds, boolean keep) throws Exception {
        if (!has("android.permission.RECORD_AUDIO")) return err("the microphone permission is not given (open Jade Link and allow it)");
        int rate = 16000;
        double secs = Math.max(0.5, Math.min(keep ? 120 : 30, seconds));
        int min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord rec = new AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, rate));
        if (rec.getState() != AudioRecord.STATE_INITIALIZED) { rec.release(); return err("the microphone is busy, or Android will not let Jade Link use it now (open the app once)"); }
        int total = (int) (secs * rate);
        short[] pcm = new short[keep ? total : rate / 4];
        int at = 0; double peak = 0, sumAll = 0; long count = 0; List<Double> levels = new ArrayList<Double>();
        rec.startRecording();
        try {
            short[] buf = new short[rate / 10];
            while (at < total) {
                int n = rec.read(buf, 0, Math.min(buf.length, total - at));
                if (n <= 0) break;
                double sum = 0; for (int i = 0; i < n; i++) sum += (double) buf[i] * buf[i];
                double rms = Math.sqrt(sum / n), db = rms < 1 ? -90 : 20 * Math.log10(rms / 32768.0);
                double level = Math.max(0, Math.min(100, Math.round(db + 90)));
                levels.add(level); peak = Math.max(peak, level); sumAll += level; count++;
                if (keep) System.arraycopy(buf, 0, pcm, at, n);
                at += n;
            }
        } finally { try { rec.stop(); } catch (Throwable ignored) { } rec.release(); }
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("ok", true); m.put("seconds", Math.round(at * 10.0 / rate) / 10.0);
        m.put("level", count == 0 ? 0.0 : Math.round(sumAll / count) * 1.0); m.put("peak", peak);
        if (!keep) { m.put("scale", "0-100: a quiet room is about 25-40, talking 55-70, a clap or shout 75+"); return MiniJson.write(m); }
        m.put("wav_base64", Base64.encodeToString(wav(pcm, at, rate), Base64.NO_WRAP));
        return MiniJson.write(m);
    }

    static byte[] wav(short[] pcm, int n, int rate) {
        ByteArrayOutputStream b = new ByteArrayOutputStream(44 + n * 2);
        int data = n * 2;
        writeStr(b, "RIFF"); le32(b, 36 + data); writeStr(b, "WAVE"); writeStr(b, "fmt "); le32(b, 16); le16(b, 1); le16(b, 1); le32(b, rate); le32(b, rate * 2); le16(b, 2); le16(b, 16);
        writeStr(b, "data"); le32(b, data);
        for (int i = 0; i < n; i++) le16(b, pcm[i]);
        return b.toByteArray();
    }
    private static void writeStr(ByteArrayOutputStream b, String s) { for (int i = 0; i < s.length(); i++) b.write(s.charAt(i)); }
    private static void le32(ByteArrayOutputStream b, int v) { b.write(v); b.write(v >> 8); b.write(v >> 16); b.write(v >> 24); }
    private static void le16(ByteArrayOutputStream b, int v) { b.write(v); b.write(v >> 8); }

    void destroy() { world.destroy(); if (bg != null) { bg.quitSafely(); bg = null; } }
}
