package android.app;
import android.content.Context;
public class Notification {
  public static final String EXTRA_TITLE = "android.title", EXTRA_TEXT = "android.text";
  public static final int FLAG_ONGOING_EVENT = 2, FLAG_GROUP_SUMMARY = 512, PRIORITY_HIGH = 1;
  public static final String CATEGORY_CALL = "call";
  public android.os.Bundle extras;
  public String category;
  public int flags;
  public abstract static class Style { }
  public static class BigTextStyle extends Style { public BigTextStyle() { throw new RuntimeException(); } public BigTextStyle bigText(CharSequence c) { throw new RuntimeException(); } }
  public static class Builder {
    public Builder(Context c, String channel) { throw new RuntimeException(); }
    public Builder(Context c) { throw new RuntimeException(); }
    public Builder setSmallIcon(int icon) { throw new RuntimeException(); }
    public Builder setContentTitle(CharSequence t) { throw new RuntimeException(); }
    public Builder setContentText(CharSequence t) { throw new RuntimeException(); }
    public Builder setStyle(Style s) { throw new RuntimeException(); }
    public Builder setColor(int c) { throw new RuntimeException(); }
    public Builder setAutoCancel(boolean b) { throw new RuntimeException(); }
    public Builder setContentIntent(PendingIntent p) { throw new RuntimeException(); }
    public Builder setOngoing(boolean b) { throw new RuntimeException(); }
    public Builder setOnlyAlertOnce(boolean b) { throw new RuntimeException(); }
    public Builder setShowWhen(boolean b) { throw new RuntimeException(); }
    public Notification build() { throw new RuntimeException(); }
    public Builder setPriority(int p) { throw new RuntimeException(); }
    public Builder setCategory(String c) { throw new RuntimeException(); }
    public Builder addAction(int icon, CharSequence title, PendingIntent intent) { throw new RuntimeException(); }
  }
}
