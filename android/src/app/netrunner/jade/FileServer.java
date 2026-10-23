package app.netrunner.jade;

import java.io.BufferedInputStream;
import java.io.Closeable;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLEncoder;
import java.security.SecureRandom;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Serves single vault files over the home network so a TV can play them (Chromecast fetches media by URL).
 * Only files that were explicitly shared are reachable, each under a random, unguessable path that expires.
 * Supports HEAD, byte ranges (needed for seeking in video) and CORS. Pure java.net: tested in android/test.
 */
public final class FileServer implements Closeable {
    private static final class Entry { final File f; final String mime; final long until; Entry(File f, String mime, long until) { this.f = f; this.mime = mime; this.until = until; } }
    private final ServerSocket ss;
    private final Map<String, Entry> shared = new ConcurrentHashMap<String, Entry>();
    private final SecureRandom rnd = new SecureRandom();
    private volatile boolean closed;

    public FileServer(int port) throws IOException {
        ss = new ServerSocket(port, 16, InetAddress.getByName("0.0.0.0"));
        Thread t = new Thread(new Runnable() { public void run() { acceptLoop(); } }, "jade-files");
        t.setDaemon(true); t.start();
    }

    public int port() { return ss.getLocalPort(); }

    /** Make one file reachable for ttlMs. Returns the path part of its URL ("/k3j…/movie.mp4"). */
    public String share(File f, String mime, long ttlMs) {
        byte[] b = new byte[12]; rnd.nextBytes(b);
        StringBuilder t = new StringBuilder();
        for (byte x : b) t.append(String.format("%02x", x & 0xFF));
        long now = System.currentTimeMillis();
        for (Map.Entry<String, Entry> e : shared.entrySet()) if (e.getValue().until < now) shared.remove(e.getKey());
        shared.put(t.toString(), new Entry(f, mime, now + ttlMs));
        String name;
        try { name = URLEncoder.encode(f.getName(), "UTF-8").replace("+", "%20"); } catch (Exception e) { name = "file"; }
        return "/" + t + "/" + name;
    }

    public int sharedCount() { return shared.size(); }

    private void acceptLoop() {
        while (!closed) {
            try {
                final Socket s = ss.accept();
                Thread t = new Thread(new Runnable() { public void run() { handle(s); } }, "jade-file");
                t.setDaemon(true); t.start();
            } catch (IOException e) { if (closed) return; }
        }
    }

    private static String line(InputStream in) throws IOException {
        StringBuilder b = new StringBuilder();
        int c;
        while ((c = in.read()) >= 0) { if (c == '\n') break; if (c != '\r') b.append((char) c); if (b.length() > 8192) throw new IOException("header too long"); }
        return c < 0 && b.length() == 0 ? null : b.toString();
    }

    private void handle(Socket s) {
        try {
            s.setSoTimeout(30000);
            InputStream in = new BufferedInputStream(s.getInputStream());
            OutputStream out = s.getOutputStream();
            String req = line(in);
            if (req == null) return;
            String range = null, h;
            while ((h = line(in)) != null && h.length() > 0) { int c = h.indexOf(':'); if (c > 0 && h.substring(0, c).trim().equalsIgnoreCase("range")) range = h.substring(c + 1).trim(); }
            String[] p = req.split(" ");
            if (p.length < 2) { status(out, 400, "Bad Request", null); return; }
            String method = p[0];
            if (method.equals("OPTIONS")) { out.write(("HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, HEAD, OPTIONS\r\nAccess-Control-Allow-Headers: *\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").getBytes("UTF-8")); return; }
            if (!method.equals("GET") && !method.equals("HEAD")) { status(out, 405, "Method Not Allowed", null); return; }
            String path = p[1]; int q = path.indexOf('?'); if (q >= 0) path = path.substring(0, q);
            String[] parts = path.split("/");
            Entry e = parts.length >= 2 ? shared.get(parts[1]) : null;
            if (e == null || e.until < System.currentTimeMillis() || !e.f.isFile()) { status(out, 404, "Not Found", null); return; }
            long len = e.f.length(), from = 0, to = len - 1;
            boolean partial = false;
            if (range != null && range.startsWith("bytes=")) {
                String r = range.substring(6).split(",")[0].trim();
                int d = r.indexOf('-');
                try {
                    if (d == 0) { long n = Long.parseLong(r.substring(1)); from = Math.max(0, len - n); }
                    else { from = Long.parseLong(r.substring(0, d)); if (d < r.length() - 1) to = Math.min(len - 1, Long.parseLong(r.substring(d + 1))); }
                } catch (NumberFormatException x) { from = len; }
                if (from >= len || from > to) { status(out, 416, "Range Not Satisfiable", "Content-Range: bytes */" + len); return; }
                partial = true;
            }
            long n = to - from + 1;
            StringBuilder hd = new StringBuilder("HTTP/1.1 ").append(partial ? "206 Partial Content" : "200 OK").append("\r\n");
            hd.append("Content-Type: ").append(e.mime).append("\r\nContent-Length: ").append(n).append("\r\nAccept-Ranges: bytes\r\n");
            if (partial) hd.append("Content-Range: bytes ").append(from).append('-').append(to).append('/').append(len).append("\r\n");
            hd.append("Access-Control-Allow-Origin: *\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n");
            out.write(hd.toString().getBytes("UTF-8"));
            if (method.equals("GET")) {
                RandomAccessFile f = new RandomAccessFile(e.f, "r");
                try {
                    f.seek(from);
                    byte[] buf = new byte[1 << 16];
                    while (n > 0) { int r = f.read(buf, 0, (int) Math.min(buf.length, n)); if (r < 0) break; out.write(buf, 0, r); n -= r; }
                } finally { f.close(); }
            }
            out.flush();
        } catch (IOException ignored) {
            // the TV stopped reading (seek, stop): normal
        } finally { try { s.close(); } catch (IOException ignored) { } }
    }

    private static void status(OutputStream out, int code, String msg, String extra) throws IOException {
        out.write(("HTTP/1.1 " + code + " " + msg + "\r\n" + (extra != null ? extra + "\r\n" : "") + "Content-Length: 0\r\nConnection: close\r\n\r\n").getBytes("UTF-8"));
    }

    public static String mime(String name) {
        String n = name.toLowerCase();
        int d = n.lastIndexOf('.');
        String x = d < 0 ? "" : n.substring(d + 1);
        if (x.equals("mp4") || x.equals("m4v")) return "video/mp4";
        if (x.equals("webm")) return "video/webm";
        if (x.equals("mkv")) return "video/x-matroska";
        if (x.equals("mov")) return "video/quicktime";
        if (x.equals("m3u8")) return "application/x-mpegURL";
        if (x.equals("mpd")) return "application/dash+xml";
        if (x.equals("mp3")) return "audio/mpeg";
        if (x.equals("m4a") || x.equals("aac")) return "audio/mp4";
        if (x.equals("ogg") || x.equals("oga") || x.equals("opus")) return "audio/ogg";
        if (x.equals("wav")) return "audio/wav";
        if (x.equals("flac")) return "audio/flac";
        if (x.equals("jpg") || x.equals("jpeg")) return "image/jpeg";
        if (x.equals("png")) return "image/png";
        if (x.equals("gif")) return "image/gif";
        if (x.equals("webp")) return "image/webp";
        if (x.equals("bmp")) return "image/bmp";
        return "application/octet-stream";
    }

    @Override
    public void close() { closed = true; try { ss.close(); } catch (IOException ignored) { } shared.clear(); }
}
