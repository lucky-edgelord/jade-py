package android.os;
public class HandlerThread extends Thread {
  public HandlerThread(String name) { }
  public Looper getLooper() { throw new RuntimeException(); }
  public boolean quitSafely() { throw new RuntimeException(); }
}
