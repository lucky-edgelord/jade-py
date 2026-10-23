package android.view;
public class KeyEvent {
  public static final int ACTION_DOWN = 0, ACTION_UP = 1, KEYCODE_VOLUME_UP = 24, KEYCODE_VOLUME_DOWN = 25;
  public int getKeyCode() { throw new RuntimeException(); }
  public int getAction() { throw new RuntimeException(); }
  public int getRepeatCount() { throw new RuntimeException(); }
  public KeyEvent(long downTime, long eventTime, int action, int code, int repeat) { throw new RuntimeException(); }
}
