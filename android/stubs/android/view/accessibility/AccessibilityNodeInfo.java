package android.view.accessibility;
import android.os.Bundle;
public class AccessibilityNodeInfo {
  public static final int ACTION_CLICK = 0x10, ACTION_LONG_CLICK = 0x20, ACTION_SET_TEXT = 0x200000,
    ACTION_SCROLL_FORWARD = 0x1000, ACTION_SCROLL_BACKWARD = 0x2000, ACTION_FOCUS = 1;
  public static final String ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE = "ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE";
  public static AccessibilityNodeInfo obtain(AccessibilityNodeInfo n) { throw new RuntimeException(); }
  public CharSequence getText() { throw new RuntimeException(); }
  public CharSequence getContentDescription() { throw new RuntimeException(); }
  public CharSequence getClassName() { throw new RuntimeException(); }
  public CharSequence getPackageName() { throw new RuntimeException(); }
  public boolean isClickable() { throw new RuntimeException(); }
  public boolean isLongClickable() { throw new RuntimeException(); }
  public boolean isEditable() { throw new RuntimeException(); }
  public boolean isCheckable() { throw new RuntimeException(); }
  public boolean isChecked() { throw new RuntimeException(); }
  public boolean isScrollable() { throw new RuntimeException(); }
  public boolean isEnabled() { throw new RuntimeException(); }
  public boolean isVisibleToUser() { throw new RuntimeException(); }
  public int getChildCount() { throw new RuntimeException(); }
  public AccessibilityNodeInfo getChild(int i) { throw new RuntimeException(); }
  public AccessibilityNodeInfo getParent() { throw new RuntimeException(); }
  public void getBoundsInScreen(android.graphics.Rect r) { throw new RuntimeException(); }
  public boolean performAction(int action) { throw new RuntimeException(); }
  public boolean performAction(int action, Bundle args) { throw new RuntimeException(); }
  public void recycle() { throw new RuntimeException(); }
  public static final class AccessibilityAction {
    public static final AccessibilityAction ACTION_IME_ENTER = null;
    public int getId() { throw new RuntimeException(); }
  }
}
