package app.netrunner.jade;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Bitmap;
import android.graphics.ImageFormat;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.Image;
import android.media.ImageReader;
import android.media.MediaRecorder;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Telephony;
import android.telephony.SmsMessage;

import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Sentry's body: a foreground service that holds exactly the watchers Sentry's rules need, no more. Each source of
 * events is a "system" (see SentrySystems). refresh() starts the ones that are switched on AND used by an enabled
 * rule, and stops the rest, so a system nobody uses costs no battery. Sensitive systems (camera, microphone,
 * location, calls, texts, notifications) never run without both the user's switch and the Android permission,
 * and the standing notification names everything that is running.
 */
public class SentryService extends Service {
    static final String CHANNEL = "jade-sentry";
    static final int ID = 7099;
    static volatile SentryService inst;

    private interface Mod { void stop(); }
    private final Map<String, Mod> mods = new LinkedHashMap<String, Mod>();
    private final Map<String, String> failed = new LinkedHashMap<String, String>();
    private final Handler main = new Handler(Looper.getMainLooper());
    private PowerManager.WakeLock wake;
    private HandlerThread bg;
    private Handler bgHandler;
    private int lastTypes = -1;
    private final Runnable ticker = new Runnable() { public void run() { try { SentryCore.tick(); } catch (Throwable ignored) { } schedule(); } };

    @Override public IBinder onBind(Intent i) { return null; }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        inst = this;
        SentryCore.init(getApplicationContext());
        try { refresh(); } catch (Throwable e) { failed.put("service", String.valueOf(e)); }
        return 1 /* START_STICKY: Android restarts it if it is killed, and the rules are reloaded from storage */;
    }

    @Override
    public void onDestroy() {
        main.removeCallbacksAndMessages(null);
        for (String id : new ArrayList<String>(mods.keySet())) stopMod(id);
        try { if (wake != null && wake.isHeld()) wake.release(); } catch (Throwable ignored) { }
        wake = null;
        if (bg != null) { bg.quitSafely(); bg = null; bgHandler = null; }
        if (inst == this) inst = null;
        super.onDestroy();
    }

    // ---------------------------------------------------------------- which systems run
    /** Bring the running systems in line with what the rules use. Safe to call any time. */
    synchronized void refresh() {
        Set<String> want = SentrySystems.active(SentryCore.engine.sources(), SentryCore.enabledSystems());
        for (String id : new ArrayList<String>(mods.keySet())) if (!want.contains(id)) stopMod(id);
        failed.keySet().retainAll(want);
        for (String id : want) {
            if (mods.containsKey(id)) continue;
            String why = blocker(id);
            if (why != null) { failed.put(id, why); continue; }
            try { Mod m = startMod(id); if (m != null) { mods.put(id, m); failed.remove(id); } }
            catch (Throwable e) { failed.put(id, String.valueOf(e.getMessage() == null ? e : e.getMessage())); }
        }
        goForeground();
        manageWake();
        schedule();
    }

    private boolean has(String perm) { try { return checkSelfPermission(perm) == 0; } catch (Throwable e) { return false; } }

    /** Why a system cannot run right now (a missing permission or access), or null. */
    private String blocker(String id) {
        SentrySystems.Info i = SentrySystems.ALL.get(id);
        if (i != null && i.perm != null && !has(i.perm)) {
            if (id.equals("bluetooth") && Build.VERSION.SDK_INT < 31) return null;
            return "needs the " + i.perm.substring(i.perm.lastIndexOf('.') + 1) + " permission";
        }
        if (id.equals("notifications") && !SentryCore.notificationAccess()) return "needs notification access (Settings, Notifications, Device and app notifications)";
        if (id.equals("app") && !JadeAccess.isRunning()) return "needs screen control switched on in Accessibility settings";
        return null;
    }

    private void stopMod(String id) { Mod m = mods.remove(id); if (m != null) { try { m.stop(); } catch (Throwable ignored) { } } }

    private Handler bgHandler() {
        if (bg == null) { bg = new HandlerThread("sentry"); bg.start(); bgHandler = new Handler(bg.getLooper()); }
        return bgHandler;
    }

    private Mod startMod(String id) throws Exception {
        if (id.equals("battery")) return battery();
        if (id.equals("network")) return network();
        if (id.equals("screen")) return screen();
        if (id.equals("headset")) return receiver("android.intent.action.HEADSET_PLUG", new Rx() { public void on(Intent i) {
            Map<String, Object> d = new HashMap<String, Object>(); d.put("plugged", i.getIntExtra("state", 0) == 1); String n = i.getStringExtra("name"); if (n != null) d.put("name", n);
            SentryCore.emit("headset", d); } });
        if (id.equals("bluetooth")) return bluetooth();
        if (id.equals("volume")) return receiver("android.media.VOLUME_CHANGED_ACTION", new Rx() { public void on(Intent i) {
            Map<String, Object> d = new HashMap<String, Object>(); d.put("stream", streamName(i.getIntExtra("android.media.EXTRA_VOLUME_STREAM_TYPE", -1))); d.put("level", (double) i.getIntExtra("android.media.EXTRA_VOLUME_STREAM_VALUE", -1));
            SentryCore.emit("volume", d); } });
        if (id.equals("light")) return light();
        if (id.equals("motion")) return motion();
        if (id.equals("location")) return location();
        if (id.equals("calls")) return calls();
        if (id.equals("sms")) return sms();
        if (id.equals("notifications")) return new Mod() { public void stop() { } };   // the listener service feeds SentryCore itself; this only marks it wanted
        if (id.equals("app")) return new Mod() { public void stop() { } };             // JadeAccess feeds SentryCore itself
        if (id.equals("audio")) return audio();
        if (id.equals("camera")) return camera();
        return null;
    }

    private static String streamName(int t) {
        switch (t) { case 0: return "call"; case 1: return "system"; case 2: return "ring"; case 3: return "music"; case 4: return "alarm"; case 5: return "notification"; default: return "stream" + t; }
    }

    // ---------------------------------------------------------------- receivers
    private interface Rx { void on(Intent i); }

    private Mod receiver(String action, final Rx rx) {
        return receiver(new String[] { action }, rx);
    }

    private Mod receiver(String[] actions, final Rx rx) {
        final BroadcastReceiver r = new BroadcastReceiver() { public void onReceive(Context c, Intent i) { try { rx.on(i); } catch (Throwable ignored) { } } };
        IntentFilter f = new IntentFilter(); for (String a : actions) f.addAction(a);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(r, f, Context.RECEIVER_NOT_EXPORTED); else registerReceiver(r, f);
        return new Mod() { public void stop() { unregisterReceiver(r); } };
    }

    private boolean lastPlugged; private boolean pluggedKnown;
    private Mod battery() {
        return receiver("android.intent.action.BATTERY_CHANGED", new Rx() { public void on(Intent i) {
            int level = i.getIntExtra("level", -1), scale = i.getIntExtra("scale", 100), plug = i.getIntExtra("plugged", 0), temp = i.getIntExtra("temperature", Integer.MIN_VALUE);
            Map<String, Object> m = new HashMap<String, Object>();
            if (level >= 0 && scale > 0) m.put("battery.level", Math.round(100.0 * level / scale));
            m.put("battery.charging", plug > 0);
            if (temp != Integer.MIN_VALUE) m.put("battery.temp_c", temp / 10.0);
            boolean p = plug > 0;
            SentryCore.set(m);
            if (pluggedKnown && p != lastPlugged) { Map<String, Object> d = new HashMap<String, Object>(); d.put("plugged", p); SentryCore.emit("charger", d); }
            lastPlugged = p; pluggedKnown = true;
        } });
    }

    private Mod screen() {
        final PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        SentryCore.touch();
        return receiver(new String[] { "android.intent.action.SCREEN_ON", "android.intent.action.SCREEN_OFF", "android.intent.action.USER_PRESENT" }, new Rx() { public void on(Intent i) {
            String a = i.getAction(); Map<String, Object> d = new HashMap<String, Object>();
            if ("android.intent.action.USER_PRESENT".equals(a)) { SentryCore.touch(); SentryCore.emit("user_present", d); return; }
            boolean on = "android.intent.action.SCREEN_ON".equals(a);
            if (on) SentryCore.touch();
            SentryCore.set("screen.on", on); d.put("on", on); SentryCore.emit("screen", d);
        } });
    }

    private Mod bluetooth() {
        return receiver(new String[] { "android.bluetooth.device.action.ACL_CONNECTED", "android.bluetooth.device.action.ACL_DISCONNECTED" }, new Rx() { public void on(Intent i) {
            Map<String, Object> d = new HashMap<String, Object>(); d.put("connected", "android.bluetooth.device.action.ACL_CONNECTED".equals(i.getAction()));
            try { android.bluetooth.BluetoothDevice dev = i.getParcelableExtra(android.bluetooth.BluetoothDevice.EXTRA_DEVICE); if (dev != null) { String n = dev.getName(); if (n != null) d.put("name", n); d.put("address", dev.getAddress()); } } catch (Throwable ignored) { }
            SentryCore.emit("bluetooth", d);
        } });
    }

    // ---------------------------------------------------------------- network
    private String lastNet = "";
    private Mod network() {
        final ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        final WifiManager wm = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        final ConnectivityManager.NetworkCallback cb = new ConnectivityManager.NetworkCallback() {
            @Override public void onCapabilitiesChanged(Network n, NetworkCapabilities c) {
                String t = c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ? "wifi" : c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ? "cell" : c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) ? "ethernet" : c.hasTransport(NetworkCapabilities.TRANSPORT_VPN) ? "vpn" : "other";
                String ssid = "";
                if (t.equals("wifi")) { try { WifiInfo w = wm.getConnectionInfo(); String s = w == null ? null : w.getSSID(); if (s != null && !s.contains("unknown ssid")) ssid = s.replace("\"", ""); } catch (Throwable ignored) { } }
                publishNet(t, ssid);
            }
            @Override public void onLost(Network n) { publishNet("none", ""); }
        };
        cm.registerDefaultNetworkCallback(cb);
        return new Mod() { public void stop() { cm.unregisterNetworkCallback(cb); } };
    }

    private void publishNet(String type, String ssid) {
        Map<String, Object> m = new HashMap<String, Object>(); m.put("net.type", type); m.put("wifi.connected", type.equals("wifi")); m.put("wifi.ssid", ssid);
        SentryCore.set(m);
        String key = type + "|" + ssid;
        if (!key.equals(lastNet)) { boolean first = lastNet.length() == 0; lastNet = key; if (!first) { Map<String, Object> d = new HashMap<String, Object>(); d.put("type", type); d.put("connected", !type.equals("none")); d.put("ssid", ssid); SentryCore.emit("network", d); } }
    }

    // ---------------------------------------------------------------- light and motion
    private Mod sensor(final int type, final int delay, final SensorEventListener l) {
        final SensorManager sm = (SensorManager) getSystemService(SENSOR_SERVICE);
        Sensor s = sm.getDefaultSensor(type);
        if (s == null) throw new IllegalStateException("this phone has no such sensor");
        sm.registerListener(l, s, delay);
        return new Mod() { public void stop() { sm.unregisterListener(l); } };
    }

    private double lastLux = -1; private long lastLuxAt;
    private Mod light() {
        return sensor(5 /* TYPE_LIGHT */, SensorManager.SENSOR_DELAY_NORMAL, new SensorEventListener() {
            public void onSensorChanged(SensorEvent e) {
                double lux = e.values[0]; long now = System.currentTimeMillis();
                if (lastLux >= 0 && Math.abs(lux - lastLux) < Math.max(1.0, lastLux * 0.1) && now - lastLuxAt < 5000) return;
                lastLux = lux; lastLuxAt = now; SentryCore.set("light.lux", lux);
            }
            public void onAccuracyChanged(Sensor s, int a) { }
        });
    }

    private double gx, gy, gz, motionAvg; private long lastShake; private String lastState = ""; private boolean lastMoving; private boolean lastShakeState;
    private Mod motion() {
        return sensor(1 /* TYPE_ACCELEROMETER */, SensorManager.SENSOR_DELAY_UI, new SensorEventListener() {
            public void onSensorChanged(SensorEvent e) {
                double x = e.values[0], y = e.values[1], z = e.values[2], now = System.currentTimeMillis();
                gx += 0.15 * (x - gx); gy += 0.15 * (y - gy); gz += 0.15 * (z - gz);          // slow average = which way is down
                double mag = Math.sqrt(x * x + y * y + z * z);
                motionAvg += 0.2 * (Math.abs(mag - 9.81) - motionAvg);                          // how much it is being jostled
                String st = gz < -7.0 ? "face_down" : gz > 7.0 ? "face_up" : Math.abs(gy) > 6.5 ? "upright" : "tilted";
                boolean moving = motionAvg > 0.8;
                boolean shaking = now - lastShake < 1500;
                if (mag > 22 && now - lastShake > 800) { lastShake = (long) now; shaking = true; SentryCore.emit("shake", new HashMap<String, Object>()); }
                if (!st.equals(lastState) || moving != lastMoving || shaking != lastShakeState) {
                    lastState = st; lastMoving = moving; lastShakeState = shaking;
                    Map<String, Object> m = new HashMap<String, Object>(); m.put("motion.state", st); m.put("motion.moving", moving); m.put("motion.shake", shaking); SentryCore.set(m);
                }
            }
            public void onAccuracyChanged(Sensor s, int a) { }
        });
    }

    // ---------------------------------------------------------------- location
    private Mod location() {
        final LocationManager lm = (LocationManager) getSystemService(LOCATION_SERVICE);
        final LocationListener l = new LocationListener() { public void onLocationChanged(Location loc) { publishLoc(loc); } };
        long every = (long) (SentryCore.settingNum("location_interval_s", 60) * 1000);
        boolean any = false;
        for (String p : new String[] { LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER }) {
            try { if (lm.isProviderEnabled(p)) { lm.requestLocationUpdates(p, every, 10f, l, Looper.getMainLooper()); any = true; Location last = lm.getLastKnownLocation(p); if (last != null) publishLoc(last); } } catch (SecurityException e) { throw e; } catch (Throwable ignored) { }
        }
        if (!any) throw new IllegalStateException("location is switched off on the phone");
        return new Mod() { public void stop() { lm.removeUpdates(l); } };
    }

    private void publishLoc(Location loc) {
        Map<String, Object> m = new HashMap<String, Object>(); m.put("geo.lat", loc.getLatitude()); m.put("geo.lon", loc.getLongitude()); m.put("geo.accuracy", (double) loc.getAccuracy());
        SentryCore.set(m);
    }

    // ---------------------------------------------------------------- calls and texts
    private String callState = "idle";
    private Mod calls() {
        return receiver("android.intent.action.PHONE_STATE", new Rx() { public void on(Intent i) {
            String s = i.getStringExtra("state"); if (s == null) return;
            String now = s.equals("RINGING") ? "ringing" : s.equals("OFFHOOK") ? "offhook" : "idle", ev = null, prev = callState;
            if (now.equals(prev)) return;
            if (prev.equals("idle") && now.equals("ringing")) ev = "ringing";
            else if (prev.equals("ringing") && now.equals("offhook")) ev = "answered";
            else if (prev.equals("idle") && now.equals("offhook")) ev = "outgoing";
            else if (prev.equals("ringing") && now.equals("idle")) ev = "missed";
            else if (prev.equals("offhook") && now.equals("idle")) ev = "ended";
            callState = now; SentryCore.set("call.state", now);
            if (ev != null) { Map<String, Object> d = new HashMap<String, Object>(); d.put("state", ev); String num = i.getStringExtra("incoming_number"); if (num != null) d.put("number", num); SentryCore.emit("call", d); }
        } });
    }

    private Mod sms() {
        return receiver(Telephony.Sms.Intents.SMS_RECEIVED_ACTION, new Rx() { public void on(Intent i) {
            SmsMessage[] parts = Telephony.Sms.Intents.getMessagesFromIntent(i);
            if (parts == null || parts.length == 0) return;
            StringBuilder b = new StringBuilder(); String from = parts[0].getOriginatingAddress();
            for (SmsMessage p : parts) { String t = p.getMessageBody(); if (t != null) b.append(t); }
            Map<String, Object> d = new HashMap<String, Object>(); d.put("from", from == null ? "" : from); d.put("text", b.toString());
            SentryCore.emit("sms", d);
        } });
    }

    // ---------------------------------------------------------------- the microphone: a loudness number, nothing is recorded or kept
    private Mod audio() {
        final int rate = 8000;
        final int buf = Math.max(AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), rate);
        final AudioRecord rec = new AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, buf);
        if (rec.getState() != AudioRecord.STATE_INITIALIZED) { rec.release(); throw new IllegalStateException("the microphone is busy or Android will not let a background service use it"); }
        final boolean[] run = { true };
        rec.startRecording();
        Thread t = new Thread(new Runnable() { public void run() {
            short[] pcm = new short[rate / 2]; long last = -1;
            while (run[0]) {
                int n = rec.read(pcm, 0, pcm.length);
                if (n <= 0) { try { Thread.sleep(200); } catch (InterruptedException e) { return; } continue; }
                double sum = 0; for (int i = 0; i < n; i++) sum += (double) pcm[i] * pcm[i];
                double rms = Math.sqrt(sum / n), db = rms < 1 ? -90 : 20 * Math.log10(rms / 32768.0);
                long level = Math.max(0, Math.min(100, Math.round(db + 90)));
                if (level != last) { last = level; SentryCore.set("audio.level", (double) level); }
            }
        } }, "sentry-audio");
        t.setDaemon(true); t.start();
        return new Mod() { public void stop() { run[0] = false; try { rec.stop(); } catch (Throwable ignored) { } rec.release(); } };
    }

    // ---------------------------------------------------------------- the camera: how much of the picture changed, nothing is streamed anywhere
    private static final int CW = 320, CH = 240, GRID_W = 32, GRID_H = 24;
    private int[] prevGrid; private long lastFrameAt; private byte[] lastGray;

    private Mod camera() throws Exception {
        final CameraManager cm = (CameraManager) getSystemService(CAMERA_SERVICE);
        String chosen = null;
        for (String id : cm.getCameraIdList()) { Integer face = cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING); if (face != null && face == CameraCharacteristics.LENS_FACING_BACK && chosen == null) chosen = id; }
        if (chosen == null) { String[] ids = cm.getCameraIdList(); if (ids.length == 0) throw new IllegalStateException("this phone has no camera"); chosen = ids[0]; }
        final long every = (long) (SentryCore.settingNum("camera_interval_s", 1) * 1000);
        final Handler h = bgHandler();
        final ImageReader reader = ImageReader.newInstance(CW, CH, ImageFormat.YUV_420_888, 2);
        final CameraDevice[] dev = { null };
        final CameraCaptureSession[] sess = { null };
        reader.setOnImageAvailableListener(new ImageReader.OnImageAvailableListener() { public void onImageAvailable(ImageReader r) {
            Image img = null;
            try {
                img = r.acquireLatestImage(); if (img == null) return;
                long now = System.currentTimeMillis(); if (now - lastFrameAt < every) return; lastFrameAt = now;
                analyse(img);
            } catch (Throwable ignored) { } finally { if (img != null) try { img.close(); } catch (Throwable ignored) { } }
        } }, h);
        cm.openCamera(chosen, new CameraDevice.StateCallback() {
            public void onOpened(CameraDevice d) {
                dev[0] = d;
                try {
                    final CaptureRequest.Builder b = d.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW); b.addTarget(reader.getSurface());
                    List<android.view.Surface> outs = new ArrayList<android.view.Surface>(); outs.add(reader.getSurface());
                    d.createCaptureSession(outs, new CameraCaptureSession.StateCallback() {
                        public void onConfigured(CameraCaptureSession s) { sess[0] = s; try { s.setRepeatingRequest(b.build(), null, null); } catch (CameraAccessException e) { failed.put("camera", "could not start the camera: " + e); } }
                        public void onConfigureFailed(CameraCaptureSession s) { failed.put("camera", "the camera would not start"); }
                    }, h);
                } catch (Throwable e) { failed.put("camera", "could not start the camera: " + e); }
            }
            public void onDisconnected(CameraDevice d) { failed.put("camera", "the camera was taken by another app"); try { d.close(); } catch (Throwable ignored) { } }
            public void onError(CameraDevice d, int error) { failed.put("camera", "the camera reported error " + error + " (Android only lets a background service use the camera if it was started while the app was on screen)"); try { d.close(); } catch (Throwable ignored) { } }
        }, h);
        return new Mod() { public void stop() {
            try { if (sess[0] != null) sess[0].close(); } catch (Throwable ignored) { }
            try { if (dev[0] != null) dev[0].close(); } catch (Throwable ignored) { }
            try { reader.close(); } catch (Throwable ignored) { }
            prevGrid = null; lastGray = null;
        } };
    }

    /** Reduce the luminance plane to a coarse grid; compare with the last grid, ignoring a change in overall brightness. */
    private void analyse(Image img) {
        Image.Plane y = img.getPlanes()[0]; java.nio.ByteBuffer bb = y.getBuffer(); int rs = y.getRowStride(), ps = y.getPixelStride(), w = img.getWidth(), hh = img.getHeight();
        int[] grid = new int[GRID_W * GRID_H]; int bw = w / GRID_W, bh = hh / GRID_H; if (bw < 1 || bh < 1) return;
        byte[] gray = SentryCore.wantFrames() ? new byte[w * hh] : null;
        long total = 0;
        for (int gy = 0; gy < GRID_H; gy++) for (int gx = 0; gx < GRID_W; gx++) {
            long s = 0; int c = 0;
            for (int py = gy * bh; py < gy * bh + bh; py += 2) for (int px = gx * bw; px < gx * bw + bw; px += 2) { s += bb.get(py * rs + px * ps) & 0xff; c++; }
            int v = c == 0 ? 0 : (int) (s / c); grid[gy * GRID_W + gx] = v; total += v;
        }
        if (gray != null) { for (int py = 0; py < hh; py++) for (int px = 0; px < w; px++) gray[py * w + px] = bb.get(py * rs + px * ps); lastGray = gray; frameW = w; frameH = hh; }
        double mean = (double) total / grid.length;
        Map<String, Object> m = new HashMap<String, Object>(); m.put("camera.brightness", Math.round(mean / 2.55));
        if (prevGrid != null) {
            double shift = 0; for (int i = 0; i < grid.length; i++) shift += grid[i] - prevGrid[i]; shift /= grid.length;   // overall brightness drift, discounted
            int changed = 0; double thr = SentryCore.settingNum("camera_cell_threshold", 22);
            for (int i = 0; i < grid.length; i++) if (Math.abs(grid[i] - prevGrid[i] - shift) > thr) changed++;
            m.put("camera.change", Math.round(100.0 * changed / grid.length));
        }
        prevGrid = grid;
        SentryCore.set(m);
    }

    private int frameW, frameH;
    /** Save the most recent camera picture (grayscale) for a rule that asked to keep one. Returns the file path or null. */
    String saveFrame(String name) {
        byte[] g = lastGray; if (g == null) return null;
        try {
            Bitmap bm = Bitmap.createBitmap(frameW, frameH, Bitmap.Config.ARGB_8888); int[] px = new int[frameW * frameH];
            for (int i = 0; i < px.length; i++) { int v = g[i] & 0xff; px[i] = 0xff000000 | (v << 16) | (v << 8) | v; }
            bm.setPixels(px, 0, frameW, 0, 0, frameW, frameH);
            File dir = new File(getExternalFilesDir(null), "sentry"); dir.mkdirs();
            File f = new File(dir, name.replaceAll("[^A-Za-z0-9_.-]", "_") + ".jpg");
            FileOutputStream o = new FileOutputStream(f); try { bm.compress(Bitmap.CompressFormat.JPEG, 80, o); } finally { o.close(); }
            File[] all = dir.listFiles(); if (all != null && all.length > 60) { java.util.Arrays.sort(all); for (int i = 0; i < all.length - 60; i++) all[i].delete(); }   // keep the newest 60
            return f.getAbsolutePath();
        } catch (Throwable e) { return null; }
    }

    // ---------------------------------------------------------------- upkeep: the standing notice, wake lock, ticking
    private void goForeground() {
        List<String> names = new ArrayList<String>();
        for (String id : mods.keySet()) { SentrySystems.Info i = SentrySystems.ALL.get(id); if (i != null) names.add(i.label.toLowerCase()); }
        String text = names.isEmpty() ? "Waiting: no system is in use" : "Watching: " + join(names);
        if (!failed.isEmpty()) text += " (" + failed.size() + " blocked)";
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            Notification.Builder b;
            if (Build.VERSION.SDK_INT >= 26) { nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Sentry", NotificationManager.IMPORTANCE_LOW)); b = new Notification.Builder(this, CHANNEL); } else b = new Notification.Builder(this);
            b.setSmallIcon(getApplicationInfo().icon).setContentTitle("Jade Sentry is on").setContentText(text).setOngoing(true).setOnlyAlertOnce(true).setShowWhen(false).setColor(0xff1fe5a5)
                .setContentIntent(PendingIntent.getActivity(this, 2, new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
            Notification n = b.build();
            int types = serviceTypes();
            if (Build.VERSION.SDK_INT >= 29 && types != 0) startForeground(ID, n, types); else startForeground(ID, n);
            lastTypes = types;
        } catch (Throwable e) { failed.put("service", "could not show its notice: " + e.getMessage()); }
    }

    private static String join(List<String> l) { StringBuilder b = new StringBuilder(); for (int i = 0; i < l.size(); i++) { if (i > 0) b.append(", "); b.append(l.get(i)); } return b.toString(); }

    /** Foreground-service type bits: only ones whose permission is held (Android 14 refuses the rest). */
    private int serviceTypes() {
        int t = 0;
        if (mods.containsKey("location") && has("android.permission.ACCESS_FINE_LOCATION")) t |= 8;
        if (mods.containsKey("camera") && has("android.permission.CAMERA")) t |= 64;
        if (mods.containsKey("audio") && has("android.permission.RECORD_AUDIO")) t |= 128;
        if (Build.VERSION.SDK_INT >= 34) t |= 1073741824;   // specialUse: the base reason this service exists
        else if (t == 0 && Build.VERSION.SDK_INT >= 29) t = 1;   // dataSync
        return t;
    }

    private void manageWake() {
        boolean need = SentryCore.awake && (mods.containsKey("light") || mods.containsKey("motion") || mods.containsKey("audio") || mods.containsKey("camera"));
        try {
            if (need && wake == null) { PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE); wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jadevault:sentry"); wake.setReferenceCounted(false); wake.acquire(12L * 60 * 60 * 1000); }
            if (!need && wake != null) { if (wake.isHeld()) wake.release(); wake = null; }
        } catch (Throwable ignored) { }
    }

    private void schedule() {
        main.removeCallbacks(ticker);
        long d = SentryCore.engine.nextWakeMs();
        main.postDelayed(ticker, d < 0 ? 30000 : Math.max(200, Math.min(d + 50, 30000)));
    }

    /** For the page: what is running and what is blocked, with reasons. */
    synchronized Map<String, Object> systemsStatus() {
        Map<String, Object> r = new LinkedHashMap<String, Object>();
        r.put("running", new ArrayList<Object>(mods.keySet()));
        r.put("blocked", new LinkedHashMap<String, Object>(failed));
        return r;
    }
}
