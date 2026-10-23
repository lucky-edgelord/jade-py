package android.webkit;
public class WebViewClient {
  public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) { throw new RuntimeException(); }
  public void onPageStarted(WebView v, String url, android.graphics.Bitmap icon) { throw new RuntimeException(); }
  public void onPageFinished(WebView v, String url) { throw new RuntimeException(); }
  public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) { throw new RuntimeException(); }
}
