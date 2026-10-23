package android.hardware.camera2;
public abstract class CameraDevice implements AutoCloseable {
  public static final int TEMPLATE_PREVIEW = 1, TEMPLATE_STILL_CAPTURE = 2;
  public static abstract class StateCallback {
    public abstract void onOpened(CameraDevice camera);
    public abstract void onDisconnected(CameraDevice camera);
    public abstract void onError(CameraDevice camera, int error);
  }
  public abstract void createCaptureSession(java.util.List<android.view.Surface> outputs, CameraCaptureSession.StateCallback callback, android.os.Handler handler) throws CameraAccessException;
  public abstract CaptureRequest.Builder createCaptureRequest(int templateType) throws CameraAccessException;
  public abstract void close();
}
