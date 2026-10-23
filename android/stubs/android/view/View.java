package android.view;
public class View {
  public boolean postDelayed(Runnable r, long ms) { throw new RuntimeException(); }
  public static final int VISIBLE = 0, INVISIBLE = 4, GONE = 8;
  public interface OnClickListener { void onClick(View v); }
  public View() { }
  public View(android.content.Context c) { throw new RuntimeException(); }
  public void setBackgroundColor(int c) { throw new RuntimeException(); }
  public boolean post(Runnable r) { throw new RuntimeException(); }
  public void setSystemUiVisibility(int v) { throw new RuntimeException(); }
  public void setOnClickListener(OnClickListener l) { throw new RuntimeException(); }
  public void bringToFront() { throw new RuntimeException(); }
  public int getWidth() { throw new RuntimeException(); }
  public int getHeight() { throw new RuntimeException(); }
  public void draw(android.graphics.Canvas c) { throw new RuntimeException(); }
  public void setVisibility(int v) { throw new RuntimeException(); }
  public void setPadding(int l, int t, int r, int b) { throw new RuntimeException(); }
}
