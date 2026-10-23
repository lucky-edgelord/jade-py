package android.provider;
public class Settings {
  public static final String ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION = "android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION";
  public static final String ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION = "android.settings.MANAGE_ALL_FILES_ACCESS_PERMISSION";
  public static class Secure { public static String getString(android.content.ContentResolver cr, String name) { throw new RuntimeException(); } }
  public static class Global { public static int getInt(android.content.ContentResolver cr, String name, int def) { throw new RuntimeException(); } }
  public static boolean canDrawOverlays(android.content.Context c) { throw new RuntimeException(); }
  public static final class System {
    public static boolean canWrite(android.content.Context c) { throw new RuntimeException(); }
    public static boolean putInt(android.content.ContentResolver cr, String name, int value) { throw new RuntimeException(); }
    public static int getInt(android.content.ContentResolver cr, String name, int def) { throw new RuntimeException(); }
  }
}
