package android.webkit;
public abstract class CookieManager {
  public static CookieManager getInstance() { throw new RuntimeException(); }
  public abstract void removeAllCookies(ValueCallback<Boolean> cb);
  public abstract void flush();
  public abstract void setAcceptCookie(boolean b);
  public abstract void setAcceptThirdPartyCookies(WebView v, boolean b);
}
