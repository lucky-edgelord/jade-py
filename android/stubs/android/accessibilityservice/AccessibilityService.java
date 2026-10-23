package android.accessibilityservice;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
public abstract class AccessibilityService extends android.content.Context {
  public static final int GLOBAL_ACTION_BACK = 1, GLOBAL_ACTION_HOME = 2, GLOBAL_ACTION_RECENTS = 3,
    GLOBAL_ACTION_NOTIFICATIONS = 4, GLOBAL_ACTION_QUICK_SETTINGS = 5, GLOBAL_ACTION_LOCK_SCREEN = 8, GLOBAL_ACTION_TAKE_SCREENSHOT = 9;
  public static final int TAKE_SCREENSHOT_DISPLAY = 0;
  public abstract void onAccessibilityEvent(AccessibilityEvent event);
  public abstract void onInterrupt();
  protected void onServiceConnected() { }
  protected boolean onKeyEvent(android.view.KeyEvent event) { return false; }
  public void onDestroy() { }
  public AccessibilityNodeInfo getRootInActiveWindow() { throw new RuntimeException(); }
  public java.util.List<android.view.accessibility.AccessibilityWindowInfo> getWindows() { throw new RuntimeException(); }
  public final boolean performGlobalAction(int action) { throw new RuntimeException(); }
  public final boolean dispatchGesture(GestureDescription gesture, GestureResultCallback callback, android.os.Handler handler) { throw new RuntimeException(); }
  public void takeScreenshot(int displayId, java.util.concurrent.Executor executor, TakeScreenshotCallback callback) { throw new RuntimeException(); }
  public static abstract class GestureResultCallback {
    public void onCompleted(GestureDescription gestureDescription) { }
    public void onCancelled(GestureDescription gestureDescription) { }
  }
  public interface TakeScreenshotCallback {
    void onSuccess(ScreenshotResult screenshot);
    void onFailure(int errorCode);
  }
  public static final class ScreenshotResult {
    public android.hardware.HardwareBuffer getHardwareBuffer() { throw new RuntimeException(); }
    public android.graphics.ColorSpace getColorSpace() { throw new RuntimeException(); }
  }
}
