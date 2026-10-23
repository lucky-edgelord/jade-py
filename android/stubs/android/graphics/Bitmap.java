package android.graphics;
public class Bitmap {
  public enum Config { ARGB_8888 }
  public enum CompressFormat { JPEG, PNG }
  public static Bitmap createBitmap(int w, int h, Config c) { throw new RuntimeException(); }
  public static Bitmap wrapHardwareBuffer(android.hardware.HardwareBuffer b, android.graphics.ColorSpace cs) { throw new RuntimeException(); }
  public Bitmap copy(Config c, boolean mutable) { throw new RuntimeException(); }
  public static Bitmap createScaledBitmap(Bitmap b, int w, int h, boolean filter) { throw new RuntimeException(); }
  public boolean compress(CompressFormat f, int q, java.io.OutputStream o) { throw new RuntimeException(); }
  public void setPixels(int[] pixels, int offset, int stride, int x, int y, int width, int height) { throw new RuntimeException(); }
  public int getWidth() { throw new RuntimeException(); }
  public int getHeight() { throw new RuntimeException(); }
  public void recycle() { throw new RuntimeException(); }
}
