package android.os;
public class Handler {
  public Handler() {}
  public Handler(Looper l) { throw new RuntimeException(); }
  public void removeCallbacks(Runnable r) { throw new RuntimeException(); }
  public void removeCallbacksAndMessages(Object token) { throw new RuntimeException(); }
  public boolean post(Runnable r) { throw new RuntimeException(); }
  public boolean postDelayed(Runnable r, long ms) { throw new RuntimeException(); }
}
