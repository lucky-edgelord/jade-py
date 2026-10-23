package android.webkit;
public class WebChromeClient {
  public abstract static class FileChooserParams {
    public abstract android.content.Intent createIntent();
    public static android.net.Uri[] parseResult(int resultCode, android.content.Intent data) { throw new RuntimeException(); }
  }
  public boolean onJsAlert(WebView v, String url, String message, JsResult r) { throw new RuntimeException(); }
  public boolean onJsConfirm(WebView v, String url, String message, JsResult r) { throw new RuntimeException(); }
  public boolean onJsPrompt(WebView v, String url, String message, String def, JsPromptResult r) { throw new RuntimeException(); }
  public void onPermissionRequest(PermissionRequest r) { throw new RuntimeException(); }
  public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback cb) { throw new RuntimeException(); }
  public boolean onShowFileChooser(WebView v, ValueCallback<android.net.Uri[]> cb, FileChooserParams p) { throw new RuntimeException(); }
}
