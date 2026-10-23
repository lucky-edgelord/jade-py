package android.view;
public abstract class Window {
  public abstract void setStatusBarColor(int c);
  public abstract void setNavigationBarColor(int c);
  public abstract View getDecorView();
  public void addFlags(int f) { throw new RuntimeException(); }
  public void clearFlags(int f) { throw new RuntimeException(); }
  public WindowManager.LayoutParams getAttributes() { throw new RuntimeException(); }
  public void setAttributes(WindowManager.LayoutParams a) { throw new RuntimeException(); }
}
