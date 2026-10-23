package android.location;
public class LocationManager {
  public static final String GPS_PROVIDER = "gps", NETWORK_PROVIDER = "network", PASSIVE_PROVIDER = "passive";
  public boolean isProviderEnabled(String p) { throw new RuntimeException(); }
  public void requestLocationUpdates(String provider, long minTimeMs, float minDistanceM, LocationListener l, android.os.Looper looper) { throw new RuntimeException(); }
  public void removeUpdates(LocationListener l) { throw new RuntimeException(); }
  public Location getLastKnownLocation(String provider) { throw new RuntimeException(); }
}
