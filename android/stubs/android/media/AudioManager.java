package android.media;
public class AudioManager {
  public static final int MODE_IN_CALL = 2;
  public int getStreamVolume(int stream) { throw new RuntimeException(); }
  public int getStreamMaxVolume(int stream) { throw new RuntimeException(); }
  public void setStreamVolume(int stream, int index, int flags) { throw new RuntimeException(); }
  public void setSpeakerphoneOn(boolean on) { throw new RuntimeException(); }
  public boolean isSpeakerphoneOn() { throw new RuntimeException(); }
  public boolean isMusicActive() { throw new RuntimeException(); }
  public void dispatchMediaKeyEvent(android.view.KeyEvent e) { throw new RuntimeException(); }
  public int getMode() { throw new RuntimeException(); }
}
