package android.speech.tts;
public abstract class UtteranceProgressListener {
  public abstract void onStart(String id);
  public abstract void onDone(String id);
  public abstract void onError(String id);
}
