package android.app;
public final class PendingIntent {
  public static final int FLAG_IMMUTABLE = 1 << 26;
  public static final int FLAG_UPDATE_CURRENT = 1 << 27;
  public static PendingIntent getActivity(android.content.Context c, int code, android.content.Intent i, int flags) { throw new RuntimeException(); }
  public static PendingIntent getBroadcast(android.content.Context c, int code, android.content.Intent i, int flags) { throw new RuntimeException(); }
}
