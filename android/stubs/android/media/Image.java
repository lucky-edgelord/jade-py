package android.media;
public abstract class Image implements AutoCloseable {
  public static abstract class Plane {
    public abstract java.nio.ByteBuffer getBuffer();
    public abstract int getRowStride();
    public abstract int getPixelStride();
  }
  public abstract int getWidth();
  public abstract int getHeight();
  public abstract Plane[] getPlanes();
  public abstract void close();
}
