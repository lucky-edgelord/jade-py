package android.speech.tts;
import java.util.Locale;
public class TextToSpeech {
  public static final int SUCCESS = 0;
  public static final int LANG_MISSING_DATA = -1;
  public static final int LANG_NOT_SUPPORTED = -2;
  public static final int QUEUE_ADD = 1;
  public interface OnInitListener { void onInit(int status); }
  public TextToSpeech(android.content.Context c, OnInitListener l) { throw new RuntimeException(); }
  public int setLanguage(Locale l) { throw new RuntimeException(); }
  public int setOnUtteranceProgressListener(UtteranceProgressListener l) { throw new RuntimeException(); }
  public int setPitch(float p) { throw new RuntimeException(); }
  public int setSpeechRate(float r) { throw new RuntimeException(); }
  public int speak(CharSequence text, int queueMode, android.os.Bundle params, String utteranceId) { throw new RuntimeException(); }
  public int stop() { throw new RuntimeException(); }
  public void shutdown() { throw new RuntimeException(); }
}
