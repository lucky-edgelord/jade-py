package android.media;
public class MediaPlayer {
  public interface OnPreparedListener { void onPrepared(MediaPlayer mp); }
  public interface OnCompletionListener { void onCompletion(MediaPlayer mp); }
  public interface OnErrorListener { boolean onError(MediaPlayer mp, int what, int extra); }
  public MediaPlayer() { throw new RuntimeException(); }
  public void setAudioAttributes(AudioAttributes a) { throw new RuntimeException(); }
  public void setDataSource(String path) throws java.io.IOException { throw new RuntimeException(); }
  public void setVolume(float l, float r) { throw new RuntimeException(); }
  public void setLooping(boolean b) { throw new RuntimeException(); }
  public void setOnPreparedListener(OnPreparedListener l) { throw new RuntimeException(); }
  public void setOnCompletionListener(OnCompletionListener l) { throw new RuntimeException(); }
  public void setOnErrorListener(OnErrorListener l) { throw new RuntimeException(); }
  public void prepareAsync() { throw new RuntimeException(); }
  public void start() { throw new RuntimeException(); }
  public void stop() { throw new RuntimeException(); }
  public void release() { throw new RuntimeException(); }
}
