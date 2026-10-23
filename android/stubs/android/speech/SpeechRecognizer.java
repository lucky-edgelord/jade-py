package android.speech;
import android.content.Context; import android.content.Intent;
public class SpeechRecognizer {
  public static final String RESULTS_RECOGNITION = "results_recognition";
  public static boolean isRecognitionAvailable(Context c) { throw new RuntimeException(); }
  public static SpeechRecognizer createSpeechRecognizer(Context c) { throw new RuntimeException(); }
  public void setRecognitionListener(RecognitionListener l) { throw new RuntimeException(); }
  public void startListening(Intent i) { throw new RuntimeException(); }
  public void stopListening() { throw new RuntimeException(); }
  public void cancel() { throw new RuntimeException(); }
  public void destroy() { throw new RuntimeException(); }
}
