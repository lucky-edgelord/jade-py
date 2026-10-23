package android.net.nsd;
public class NsdManager {
  public static final int PROTOCOL_DNS_SD = 1;
  public interface DiscoveryListener { void onStartDiscoveryFailed(String t, int c); void onStopDiscoveryFailed(String t, int c); void onDiscoveryStarted(String t); void onDiscoveryStopped(String t); void onServiceFound(NsdServiceInfo s); void onServiceLost(NsdServiceInfo s); }
  public interface ResolveListener { void onResolveFailed(NsdServiceInfo i, int c); void onServiceResolved(NsdServiceInfo i); }
  public void discoverServices(String type, int proto, DiscoveryListener l) { throw new RuntimeException(); }
  public void stopServiceDiscovery(DiscoveryListener l) { throw new RuntimeException(); }
  public void resolveService(NsdServiceInfo s, ResolveListener l) { throw new RuntimeException(); }
}
