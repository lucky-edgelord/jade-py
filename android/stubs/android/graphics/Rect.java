package android.graphics;
public class Rect {
  public int left, top, right, bottom;
  public Rect() {}
  public int width() { return right - left; }
  public int height() { return bottom - top; }
  public int centerX() { return (left + right) / 2; }
  public int centerY() { return (top + bottom) / 2; }
}
