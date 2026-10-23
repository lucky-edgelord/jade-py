package android.content.pm;
import android.content.Intent;
public abstract class PackageManager {
  public static final int COMPONENT_ENABLED_STATE_DEFAULT = 0, COMPONENT_ENABLED_STATE_ENABLED = 1, COMPONENT_ENABLED_STATE_DISABLED = 2, DONT_KILL_APP = 1;
  public abstract void setComponentEnabledSetting(android.content.ComponentName c, int state, int flags);
  public abstract int getComponentEnabledSetting(android.content.ComponentName c);
  public abstract Intent getLaunchIntentForPackage(String pkg);
  public abstract java.util.List<ResolveInfo> queryIntentActivities(Intent i, int flags);
  public abstract CharSequence getApplicationLabel(ApplicationInfo info);
  public abstract ApplicationInfo getApplicationInfo(String pkg, int flags) throws Exception;
  public abstract PackageInfo getPackageInfo(String pkg, int flags) throws Exception;
}
