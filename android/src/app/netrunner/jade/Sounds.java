package app.netrunner.jade;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.List;

/** Sounds on the phone: a file or stream (radio) through MediaPlayer, and tones synthesised into an AudioTrack. */
final class Sounds {
    private final Context c;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final List<MediaPlayer> players = new ArrayList<MediaPlayer>();
    private final List<AudioTrack> tracks = new ArrayList<AudioTrack>();
    private int n;

    Sounds(Context c) { this.c = c; }

    private static String err(String m) { return "{\"error\":" + MiniJson.quote(m) + "}"; }

    /** {url} or {data_b64, name} (a vault file sent by jade-py); volume 0-1, loop, seconds (stop after). */
    String play(JSONObject o) {
        String url = o.optString("url", ""), data = o.optString("data_b64", "");
        String src;
        try {
            if (data.length() > 0) {
                File dir = new File(c.getCacheDir(), "sounds"); dir.mkdirs();
                File[] old = dir.listFiles(); if (old != null && old.length > 8) for (File f : old) f.delete();
                String name = o.optString("name", "sound").replaceAll("[^A-Za-z0-9_.-]", "_");
                File f = new File(dir, (++n) + "-" + name);
                FileOutputStream out = new FileOutputStream(f); try { out.write(android.util.Base64.decode(data, 0)); } finally { out.close(); }
                src = f.getAbsolutePath();
            } else if (url.matches("(?i)https?://.*")) src = url;
            else return err("url (http or https) or a vault path is required");
            final MediaPlayer mp = new MediaPlayer();
            mp.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build());
            mp.setDataSource(src);
            float v = (float) Math.max(0, Math.min(1, o.optDouble("volume", 1)));
            mp.setVolume(v, v);
            mp.setLooping(o.optBoolean("loop", false));
            mp.setOnPreparedListener(new MediaPlayer.OnPreparedListener() { public void onPrepared(MediaPlayer p) { try { p.start(); } catch (Throwable ignored) { } } });
            mp.setOnCompletionListener(new MediaPlayer.OnCompletionListener() { public void onCompletion(MediaPlayer p) { release(p); } });
            mp.setOnErrorListener(new MediaPlayer.OnErrorListener() { public boolean onError(MediaPlayer p, int what, int extra) { release(p); return true; } });
            synchronized (players) { players.add(mp); }
            mp.prepareAsync();
            double secs = o.optDouble("seconds", 0);
            if (secs > 0) main.postDelayed(new Runnable() { public void run() { release(mp); } }, (long) (Math.min(3600, secs) * 1000));
            return "{\"ok\":true,\"playing\":true,\"source\":" + MiniJson.quote(data.length() > 0 ? o.optString("name", "file") : url) + ",\"loop\":" + o.optBoolean("loop", false)
                + (data.length() == 0 ? ",\"note\":\"a stream plays until sound_stop\"" : "") + "}";
        } catch (Throwable e) { return err("could not play it: " + e.getMessage()); }
    }

    private void release(MediaPlayer p) {
        synchronized (players) { if (!players.remove(p)) return; }
        try { p.stop(); } catch (Throwable ignored) { }
        try { p.release(); } catch (Throwable ignored) { }
    }

    /** {hz, ms} or {pattern: [{hz, ms}…]} (hz 0 = a rest), wave sine|square|sawtooth|triangle, volume, wait. */
    String tone(JSONObject o) {
        try {
            JSONArray pat = o.optJSONArray("pattern");
            List<double[]> seq = new ArrayList<double[]>();
            if (pat != null && pat.length() > 0) {
                for (int i = 0; i < Math.min(60, pat.length()); i++) {
                    Object x = pat.get(i);
                    if (x instanceof JSONObject) seq.add(new double[] { ((JSONObject) x).optDouble("hz", 0), Math.max(10, Math.min(5000, ((JSONObject) x).optDouble("ms", 200))) });
                    else seq.add(new double[] { pat.optDouble(i, 0), 200 });
                }
            } else seq.add(new double[] { o.optDouble("hz", 880), Math.max(10, Math.min(5000, o.optDouble("ms", 300))) });
            double total = 0; for (double[] s : seq) total += s[1];
            if (total > 60000) return err("tones are limited to 60 seconds in all");
            final int rate = 22050;
            int samples = (int) (total / 1000 * rate);
            short[] pcm = new short[Math.max(samples, 1)];
            String wave = o.optString("wave", "sine");
            double vol = Math.max(0, Math.min(1, o.optDouble("volume", 0.5)));
            int at = 0;
            for (double[] s : seq) {
                int len = (int) (s[1] / 1000 * rate);
                double hz = Math.max(0, Math.min(20000, s[0]));
                for (int i = 0; i < len && at < pcm.length; i++, at++) {
                    if (hz <= 0) continue;
                    double ph = (i * hz / rate) % 1.0, v;
                    if (wave.equals("square")) v = ph < 0.5 ? 1 : -1;
                    else if (wave.equals("sawtooth")) v = 2 * ph - 1;
                    else if (wave.equals("triangle")) v = 1 - 4 * Math.abs(ph - 0.5);
                    else v = Math.sin(2 * Math.PI * ph);
                    double env = Math.min(1, Math.min(i, len - i) / (rate * 0.005));   // 5 ms fade in/out: no clicks
                    pcm[at] = (short) (v * env * vol * 32000);
                }
            }
            final AudioTrack t = new AudioTrack(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build(),
                new AudioFormat.Builder().setSampleRate(rate).setEncoding(AudioFormat.ENCODING_PCM_16BIT).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build(),
                pcm.length * 2, AudioTrack.MODE_STATIC, 0);
            t.write(pcm, 0, pcm.length);
            synchronized (tracks) { tracks.add(t); }
            t.play();
            final long ms = (long) total;
            main.postDelayed(new Runnable() { public void run() { drop(t); } }, ms + 300);
            if (o.optBoolean("wait", false)) { try { Thread.sleep(ms + 50); } catch (InterruptedException ignored) { } }
            return "{\"ok\":true,\"ms\":" + ms + ",\"notes\":" + seq.size() + "}";
        } catch (Throwable e) { return err("tone: " + e.getMessage()); }
    }

    private void drop(AudioTrack t) {
        synchronized (tracks) { if (!tracks.remove(t)) return; }
        try { t.stop(); } catch (Throwable ignored) { }
        try { t.release(); } catch (Throwable ignored) { }
    }

    String stop() {
        int k = 0;
        List<MediaPlayer> ps; synchronized (players) { ps = new ArrayList<MediaPlayer>(players); }
        for (MediaPlayer p : ps) { release(p); k++; }
        List<AudioTrack> ts; synchronized (tracks) { ts = new ArrayList<AudioTrack>(tracks); }
        for (AudioTrack t : ts) { drop(t); k++; }
        return "{\"ok\":true,\"stopped\":" + k + "}";
    }
}
