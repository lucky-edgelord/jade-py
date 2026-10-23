package android.media;
public class ImageReader implements AutoCloseable {
  public interface OnImageAvailableListener { void onImageAvailable(ImageReader reader); }
  public static ImageReader newInstance(int width, int height, int format, int maxImages) { throw new RuntimeException(); }
  public android.view.Surface getSurface() { throw new RuntimeException(); }
  public void setOnImageAvailableListener(OnImageAvailableListener l, android.os.Handler h) { throw new RuntimeException(); }
  public Image acquireLatestImage() { throw new RuntimeException(); }
  public void close() { throw new RuntimeException(); }
}
