package android.view;
public abstract class ViewGroup extends View {
  public static class LayoutParams {
    public static final int MATCH_PARENT = -1, WRAP_CONTENT = -2;
    public LayoutParams(int w, int h) { throw new RuntimeException(); }
  }
  public ViewGroup(android.content.Context c) { super(); }
  public void addView(View v, LayoutParams lp) { throw new RuntimeException(); }
  public void addView(View v, int index, LayoutParams lp) { throw new RuntimeException(); }
}
