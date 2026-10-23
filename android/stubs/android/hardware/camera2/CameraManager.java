package android.hardware.camera2;
public class CameraManager {
  public String[] getCameraIdList() throws CameraAccessException { throw new RuntimeException(); }
  public CameraCharacteristics getCameraCharacteristics(String id) throws CameraAccessException { throw new RuntimeException(); }
  public void openCamera(String id, CameraDevice.StateCallback cb, android.os.Handler h) throws CameraAccessException { throw new RuntimeException(); }
  public void setTorchMode(String id, boolean on) throws CameraAccessException { throw new RuntimeException(); }
}
