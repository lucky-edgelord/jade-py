package android.database;
public interface Cursor {
  boolean moveToNext(); int getColumnIndex(String name); String getString(int i); long getLong(int i); int getInt(int i); void close();
}
