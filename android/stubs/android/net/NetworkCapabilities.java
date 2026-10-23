package android.net;
public class NetworkCapabilities {
  public static final int TRANSPORT_CELLULAR = 0, TRANSPORT_WIFI = 1, TRANSPORT_ETHERNET = 3, TRANSPORT_VPN = 4;
  public static final int NET_CAPABILITY_VALIDATED = 16;
  public boolean hasTransport(int t) { throw new RuntimeException(); }
  public boolean hasCapability(int c) { throw new RuntimeException(); }
}
