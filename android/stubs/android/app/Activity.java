package android.app;
import android.content.Intent; import android.os.Bundle; import android.view.View; import android.view.Window;
public class Activity extends android.content.Context {
  protected void onCreate(Bundle b) { throw new RuntimeException(); }
  protected void onSaveInstanceState(Bundle b) { throw new RuntimeException(); }
  protected void onDestroy() { throw new RuntimeException(); }
  public void onBackPressed() { throw new RuntimeException(); }
  protected void onActivityResult(int req, int res, Intent data) { throw new RuntimeException(); }
  public Window getWindow() { throw new RuntimeException(); }
  public void setContentView(View v) { throw new RuntimeException(); }
  public void finish() { throw new RuntimeException(); }
  public void startActivity(Intent i) { throw new RuntimeException(); }
  public void startActivityForResult(Intent i, int code) { throw new RuntimeException(); }
  public final void runOnUiThread(Runnable r) { throw new RuntimeException(); }
  public final void requestPermissions(String[] p, int code) { throw new RuntimeException(); }
  public Object getSystemService(String name) { throw new RuntimeException(); }
  public void onRequestPermissionsResult(int code, String[] perms, int[] results) { throw new RuntimeException(); }
  protected void onResume() { throw new RuntimeException(); }
  public void onWindowFocusChanged(boolean hasFocus) { throw new RuntimeException(); }
  public void startLockTask() { throw new RuntimeException(); }
  public void stopLockTask() { throw new RuntimeException(); }
  protected void onNewIntent(Intent i) { throw new RuntimeException(); }
  public void setIntent(Intent i) { throw new RuntimeException(); }
  public Intent getIntent() { throw new RuntimeException(); }
}
