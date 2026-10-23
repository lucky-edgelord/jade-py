package android.webkit;
public class WebView extends android.view.View {
  public static final int RENDERER_PRIORITY_IMPORTANT = 2;
  public void setRendererPriorityPolicy(int p, boolean waivedWhenNotVisible) { throw new RuntimeException(); }
  public WebView(android.content.Context c) { throw new RuntimeException(); }
  public WebSettings getSettings() { throw new RuntimeException(); }
  public void setBackgroundColor(int c) { throw new RuntimeException(); }
  public void setWebViewClient(WebViewClient c) { throw new RuntimeException(); }
  public void setWebChromeClient(WebChromeClient c) { throw new RuntimeException(); }
  public void addJavascriptInterface(Object o, String name) { throw new RuntimeException(); }
  public WebBackForwardList restoreState(android.os.Bundle b) { throw new RuntimeException(); }
  public WebBackForwardList saveState(android.os.Bundle b) { throw new RuntimeException(); }
  public String getUrl() { throw new RuntimeException(); }
  public void loadUrl(String url) { throw new RuntimeException(); }
  public void destroy() { throw new RuntimeException(); }
  public void evaluateJavascript(String script, ValueCallback<String> cb) { throw new RuntimeException(); }
  public String getTitle() { throw new RuntimeException(); }
  public int getProgress() { throw new RuntimeException(); }
  public void stopLoading() { throw new RuntimeException(); }
  public void reload() { throw new RuntimeException(); }
  public boolean canGoBack() { throw new RuntimeException(); }
  public boolean canGoForward() { throw new RuntimeException(); }
  public void goBack() { throw new RuntimeException(); }
  public void goForward() { throw new RuntimeException(); }
  public void clearHistory() { throw new RuntimeException(); }
  public void clearCache(boolean includeDisk) { throw new RuntimeException(); }
  public void clearFormData() { throw new RuntimeException(); }
}
