package org.json;
public class JSONArray {
  public int length() { throw new RuntimeException(); }
  public int optInt(int i, int d) { throw new RuntimeException(); }
  public long optLong(int i, long d) { throw new RuntimeException(); }
  public double optDouble(int i, double d) { throw new RuntimeException(); }
  public String optString(int i, String d) { throw new RuntimeException(); }
  public String getString(int i) throws JSONException { throw new RuntimeException(); }
  public Object get(int i) throws JSONException { throw new RuntimeException(); }
}
