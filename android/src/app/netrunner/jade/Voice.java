package app.netrunner.jade;

import android.content.Intent;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import java.util.ArrayList;
import java.util.Locale;

/**
 * Talk mode's voice on the phone, the same as the Jade Vault app: Android's text-to-speech for her replies
 * (events back through window.__jadeTTS(event, id)) and its speech recognizer for what you say
 * (window.__jadeSTT(event, text): ready, begin, partial, end, final, error).
 */
final class Voice {
    private final MainActivity a;
    TextToSpeech tts;
    volatile boolean ttsReady;
    private SpeechRecognizer rec;
    private boolean recPartial = true;

    Voice(MainActivity a) {
        this.a = a;
        tts = new TextToSpeech(a, new TextToSpeech.OnInitListener() {
            @Override public void onInit(int status) {
                if (status != TextToSpeech.SUCCESS) { ttsReady = false; return; }
                int r = tts.setLanguage(Locale.UK);
                if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) tts.setLanguage(Locale.getDefault());
                tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                    @Override public void onStart(String id) { a.js("window.__jadeTTS&&window.__jadeTTS('start'," + MiniJson.quote(id) + ")"); }
                    @Override public void onDone(String id) { a.js("window.__jadeTTS&&window.__jadeTTS('done'," + MiniJson.quote(id) + ")"); }
                    @Override public void onError(String id) { a.js("window.__jadeTTS&&window.__jadeTTS('error'," + MiniJson.quote(id) + ")"); }
                });
                ttsReady = true;
            }
        });
    }

    boolean speak(String uid, String text, double pitch, double rate) {
        if (!ttsReady || tts == null) { a.js("window.__jadeTTS&&window.__jadeTTS('error'," + MiniJson.quote(uid) + ")"); return false; }
        tts.setPitch((float) Math.max(0.5, Math.min(2.0, pitch > 0 ? pitch : 1.0)));
        tts.setSpeechRate((float) Math.max(0.5, Math.min(2.0, rate > 0 ? rate : 1.0)));
        int res = tts.speak(text, TextToSpeech.QUEUE_ADD, null, uid);
        if (res != TextToSpeech.SUCCESS) { a.js("window.__jadeTTS&&window.__jadeTTS('error'," + MiniJson.quote(uid) + ")"); return false; }
        return true;
    }

    void stopSpeaking() { try { if (tts != null) tts.stop(); } catch (Throwable ignored) { } }

    boolean sttAvailable() { try { return SpeechRecognizer.isRecognitionAvailable(a); } catch (Throwable e) { return false; } }

    private void sttEvent(String ev, String text) { a.js("window.__jadeSTT&&window.__jadeSTT(" + MiniJson.quote(ev) + "," + MiniJson.quote(text) + ")"); }

    private static String first(Bundle b) {
        if (b == null) return "";
        ArrayList<String> r = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        return r == null || r.isEmpty() ? "" : r.get(0);
    }

    void sttStart(final String lang, final boolean partial, final boolean offline) {
        a.runOnUiThread(new Runnable() {
            @Override public void run() {
                try {
                    if (!sttAvailable()) { sttEvent("error", "no speech recognizer on this phone"); return; }
                    if (a.checkSelfPermission("android.permission.RECORD_AUDIO") != 0) { sttEvent("error", "9"); a.requestPermissions(new String[] { "android.permission.RECORD_AUDIO" }, MainActivity.REQ_PERMS); return; }
                    if (rec != null) { try { rec.destroy(); } catch (Throwable ignored) { } }
                    recPartial = partial;
                    rec = SpeechRecognizer.createSpeechRecognizer(a);
                    rec.setRecognitionListener(new RecognitionListener() {
                        @Override public void onReadyForSpeech(Bundle p) { sttEvent("ready", ""); }
                        @Override public void onBeginningOfSpeech() { sttEvent("begin", ""); }
                        @Override public void onRmsChanged(float rms) { }
                        @Override public void onBufferReceived(byte[] buf) { }
                        @Override public void onEndOfSpeech() { sttEvent("end", ""); }
                        @Override public void onError(int code) { sttEvent("error", String.valueOf(code)); }
                        @Override public void onResults(Bundle r) { sttEvent("final", first(r)); }
                        @Override public void onPartialResults(Bundle r) { if (recPartial) sttEvent("partial", first(r)); }
                        @Override public void onEvent(int t, Bundle p) { }
                    });
                    Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
                    i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
                    i.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, partial);
                    i.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
                    if (lang != null && lang.length() > 0) i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, lang);
                    if (offline) i.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true);
                    rec.startListening(i);
                } catch (Throwable e) { sttEvent("error", String.valueOf(e.getMessage())); }
            }
        });
    }

    void sttStop(final boolean cancel) {
        a.runOnUiThread(new Runnable() {
            @Override public void run() { try { if (rec != null) { if (cancel) rec.cancel(); else rec.stopListening(); } } catch (Throwable ignored) { } }
        });
    }

    void destroy() {
        try { if (rec != null) rec.destroy(); } catch (Throwable ignored) { }
        rec = null;
        try { if (tts != null) { tts.stop(); tts.shutdown(); } } catch (Throwable ignored) { }
    }
}
