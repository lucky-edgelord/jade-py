package android.os;
public final class PowerManager {
  public static final int PARTIAL_WAKE_LOCK = 1;
  public WakeLock newWakeLock(int flags, String tag) { throw new RuntimeException(); }
  public boolean isIgnoringBatteryOptimizations(String pkg) { throw new RuntimeException(); }
  public final class WakeLock { public void acquire(long ms) { throw new RuntimeException(); } public void release() { throw new RuntimeException(); } public boolean isHeld() { throw new RuntimeException(); } public void setReferenceCounted(boolean b) { throw new RuntimeException(); } }
}
