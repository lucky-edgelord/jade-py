package android.media;
public class AudioTrack {
  public static final int MODE_STATIC = 0, MODE_STREAM = 1;
  public AudioTrack(AudioAttributes a, AudioFormat f, int bufferSize, int mode, int sessionId) { throw new RuntimeException(); }
  public int write(short[] data, int off, int len) { throw new RuntimeException(); }
  public void play() { throw new RuntimeException(); }
  public void stop() { throw new RuntimeException(); }
  public void release() { throw new RuntimeException(); }
}
