package android.bluetooth;
public interface BluetoothProfile {
  interface ServiceListener { void onServiceConnected(int profile, BluetoothProfile proxy); void onServiceDisconnected(int profile); }
}
