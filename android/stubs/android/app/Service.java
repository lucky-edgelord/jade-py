package android.app;
public abstract class Service extends android.content.Context {
  public static final int START_STICKY = 1;
  public static final int START_NOT_STICKY = 2;
  public void onCreate() { throw new RuntimeException(); }
  public int onStartCommand(android.content.Intent i, int flags, int startId) { throw new RuntimeException(); }
  public abstract android.os.IBinder onBind(android.content.Intent i);
  public void onDestroy() { throw new RuntimeException(); }
  public final void startForeground(int id, Notification n) { throw new RuntimeException(); }
  public final void startForeground(int id, Notification n, int type) { throw new RuntimeException(); }
  public final void stopForeground(boolean remove) { throw new RuntimeException(); }
  public final void stopSelf() { throw new RuntimeException(); }
}
