package android.content;
public class ClipData {
  public static class Item { public CharSequence getText() { throw new RuntimeException(); } }
  public static ClipData newPlainText(CharSequence label, CharSequence text) { throw new RuntimeException(); }
  public int getItemCount() { throw new RuntimeException(); }
  public Item getItemAt(int i) { throw new RuntimeException(); }
}
