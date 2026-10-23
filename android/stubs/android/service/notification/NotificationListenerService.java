package android.service.notification;
public abstract class NotificationListenerService extends android.app.Service {
  public static class Ranking { public int getImportance() { throw new RuntimeException(); } public Ranking() { } }
  public static class RankingMap { public boolean getRanking(String key, Ranking out) { throw new RuntimeException(); } }
  public void onNotificationPosted(StatusBarNotification sbn) { }
  public void onListenerConnected() { }
  public RankingMap getCurrentRanking() { throw new RuntimeException(); }
  public android.os.IBinder onBind(android.content.Intent i) { throw new RuntimeException(); }
}
