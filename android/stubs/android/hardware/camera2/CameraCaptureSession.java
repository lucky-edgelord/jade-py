package android.hardware.camera2;
public abstract class CameraCaptureSession implements AutoCloseable {
  public static abstract class StateCallback {
    public abstract void onConfigured(CameraCaptureSession session);
    public abstract void onConfigureFailed(CameraCaptureSession session);
  }
  public static abstract class CaptureCallback { }
  public abstract int setRepeatingRequest(CaptureRequest request, CaptureCallback listener, android.os.Handler handler) throws CameraAccessException;
  public abstract int capture(CaptureRequest request, CaptureCallback listener, android.os.Handler handler) throws CameraAccessException;
  public abstract void close();
}
