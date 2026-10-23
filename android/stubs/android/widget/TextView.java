package android.widget;
public class TextView extends android.view.View {
  public TextView(android.content.Context c) { super(c); }
  public void setText(CharSequence t) { throw new RuntimeException(); }
  public void setTextColor(int c) { throw new RuntimeException(); }
  public void setTextSize(float sp) { throw new RuntimeException(); }
  public void setSingleLine(boolean b) { throw new RuntimeException(); }
}
