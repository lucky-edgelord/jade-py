package android.hardware.camera2;
public final class CaptureRequest {
  public static final class Key<T> { }
  public static final Key<Integer> CONTROL_MODE = null, JPEG_ORIENTATION = null;
  public static final Key<Byte> JPEG_QUALITY = null;
  public static final class Builder {
    public void addTarget(android.view.Surface s) { throw new RuntimeException(); }
    public <T> void set(Key<T> key, T value) { throw new RuntimeException(); }
    public CaptureRequest build() { throw new RuntimeException(); }
  }
}
