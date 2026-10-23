package android.os;
public abstract class Vibrator {
  public abstract boolean hasVibrator();
  public abstract void vibrate(long[] pattern, int repeat);
  public void vibrate(VibrationEffect effect) { throw new RuntimeException(); }
  public abstract void cancel();
}
