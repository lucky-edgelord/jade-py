package android.net;
public abstract class Uri { public abstract String getScheme(); public static Uri parse(String s) { throw new RuntimeException(); } public static String encode(String s) { throw new RuntimeException(); } }
