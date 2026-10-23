package android.app;
public class NotificationManager {
  public void cancel(int id) { throw new RuntimeException(); }
  public static final int IMPORTANCE_DEFAULT = 3;
  public static final int IMPORTANCE_LOW = 2, IMPORTANCE_HIGH = 4;
  public void createNotificationChannel(NotificationChannel c) { throw new RuntimeException(); }
  public void notify(int id, Notification n) { throw new RuntimeException(); }
}
