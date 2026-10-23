package android.bluetooth;
public class BluetoothDevice {
  public static final String ACTION_FOUND = "android.bluetooth.device.action.FOUND", EXTRA_DEVICE = "android.bluetooth.device.extra.DEVICE";
  public String getName() { throw new RuntimeException(); }
  public String getAddress() { throw new RuntimeException(); }
  public int getBondState() { throw new RuntimeException(); }
  public boolean createBond() { throw new RuntimeException(); }
  public BluetoothClass getBluetoothClass() { throw new RuntimeException(); }
}
