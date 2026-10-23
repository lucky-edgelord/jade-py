package android.content;
public abstract class Context {
  public static final String NOTIFICATION_SERVICE = "notification";
  public static final String POWER_SERVICE = "power";
  public static final String SENSOR_SERVICE = "sensor", WIFI_SERVICE = "wifi", CONNECTIVITY_SERVICE = "connectivity", LOCATION_SERVICE = "location", CAMERA_SERVICE = "camera", VIBRATOR_SERVICE = "vibrator";
  public static final int RECEIVER_NOT_EXPORTED = 4, RECEIVER_EXPORTED = 2;
  public Intent registerReceiver(BroadcastReceiver r, IntentFilter f, int flags) { throw new RuntimeException(); }
  public Intent registerReceiver(BroadcastReceiver r, IntentFilter f) { throw new RuntimeException(); }
  public void unregisterReceiver(BroadcastReceiver r) { throw new RuntimeException(); }
  public Context getApplicationContext() { throw new RuntimeException(); }
  public Object getSystemService(String name) { throw new RuntimeException(); }
  public int checkSelfPermission(String p) { throw new RuntimeException(); }
  public android.content.pm.ApplicationInfo getApplicationInfo() { throw new RuntimeException(); }
  public ContentResolver getContentResolver() { throw new RuntimeException(); }
  public java.io.File getFilesDir() { throw new RuntimeException(); }
  public java.io.File getExternalFilesDir(String type) { throw new RuntimeException(); }
  public String getPackageName() { throw new RuntimeException(); }
  public android.content.pm.PackageManager getPackageManager() { throw new RuntimeException(); }
  public android.content.res.Resources getResources() { throw new RuntimeException(); }
  public java.util.concurrent.Executor getMainExecutor() { throw new RuntimeException(); }
  public SharedPreferences getSharedPreferences(String name, int mode) { throw new RuntimeException(); }
  public ComponentName startForegroundService(Intent i) { throw new RuntimeException(); }
  public ComponentName startService(Intent i) { throw new RuntimeException(); }
  public boolean stopService(Intent i) { throw new RuntimeException(); }
  public void startActivity(Intent i) { throw new RuntimeException(); }
  public java.io.File getCacheDir() { throw new RuntimeException(); }
}
