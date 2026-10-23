package android.net;
public class ConnectivityManager {
  public static class NetworkCallback {
    public void onAvailable(Network n) { }
    public void onLost(Network n) { }
    public void onCapabilitiesChanged(Network n, NetworkCapabilities c) { }
  }
  public void registerDefaultNetworkCallback(NetworkCallback cb) { throw new RuntimeException(); }
  public void unregisterNetworkCallback(NetworkCallback cb) { throw new RuntimeException(); }
  public Network getActiveNetwork() { throw new RuntimeException(); }
  public NetworkCapabilities getNetworkCapabilities(Network n) { throw new RuntimeException(); }
}
