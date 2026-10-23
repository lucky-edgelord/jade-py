package android.net.wifi;
public class WifiManager {
  public static class MulticastLock { public void setReferenceCounted(boolean b) { throw new RuntimeException(); } public void acquire() { throw new RuntimeException(); } public void release() { throw new RuntimeException(); } }
  public MulticastLock createMulticastLock(String tag) { throw new RuntimeException(); }
  public boolean isWifiEnabled() { throw new RuntimeException(); }
  public WifiInfo getConnectionInfo() { throw new RuntimeException(); }
}
