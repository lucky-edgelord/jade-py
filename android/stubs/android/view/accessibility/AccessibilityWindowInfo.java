package android.view.accessibility;
public final class AccessibilityWindowInfo {
  public static final int TYPE_APPLICATION = 1, TYPE_INPUT_METHOD = 2, TYPE_SYSTEM = 3;
  public int getType() { throw new RuntimeException(); }
  public AccessibilityNodeInfo getRoot() { throw new RuntimeException(); }
  public void recycle() { throw new RuntimeException(); }
}
