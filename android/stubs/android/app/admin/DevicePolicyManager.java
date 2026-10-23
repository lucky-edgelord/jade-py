package android.app.admin;
import android.content.ComponentName; import android.content.IntentFilter;
public class DevicePolicyManager {
  public boolean isDeviceOwnerApp(String pkg) { throw new RuntimeException(); }
  public void setLockTaskPackages(ComponentName admin, String[] pkgs) { throw new RuntimeException(); }
  public void addPersistentPreferredActivity(ComponentName admin, IntentFilter f, ComponentName activity) { throw new RuntimeException(); }
  public void clearPackagePersistentPreferredActivities(ComponentName admin, String pkg) { throw new RuntimeException(); }
  public void setKeyguardDisabled(ComponentName admin, boolean disabled) { throw new RuntimeException(); }
}
