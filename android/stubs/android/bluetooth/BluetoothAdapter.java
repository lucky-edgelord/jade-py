package android.bluetooth;
public class BluetoothAdapter {
  public static final String ACTION_DISCOVERY_FINISHED = "android.bluetooth.adapter.action.DISCOVERY_FINISHED";
  public static BluetoothAdapter getDefaultAdapter() { throw new RuntimeException(); }
  public static boolean checkBluetoothAddress(String a) { throw new RuntimeException(); }
  public boolean isEnabled() { throw new RuntimeException(); }
  public boolean enable() { throw new RuntimeException(); }
  public boolean disable() { throw new RuntimeException(); }
  public boolean isDiscovering() { throw new RuntimeException(); }
  public boolean startDiscovery() { throw new RuntimeException(); }
  public boolean cancelDiscovery() { throw new RuntimeException(); }
  public BluetoothDevice getRemoteDevice(String address) { throw new RuntimeException(); }
  public java.util.Set<BluetoothDevice> getBondedDevices() { throw new RuntimeException(); }
  public boolean getProfileProxy(android.content.Context c, BluetoothProfile.ServiceListener l, int profile) { throw new RuntimeException(); }
  public void closeProfileProxy(int profile, BluetoothProfile proxy) { throw new RuntimeException(); }
}
