package android.media;
public class AudioFormat {
  public static final int CHANNEL_IN_MONO = 16, ENCODING_PCM_16BIT = 2, CHANNEL_OUT_MONO = 4;
  public static class Builder {
    public Builder() { throw new RuntimeException(); }
    public Builder setSampleRate(int r) { throw new RuntimeException(); }
    public Builder setEncoding(int e) { throw new RuntimeException(); }
    public Builder setChannelMask(int m) { throw new RuntimeException(); }
    public AudioFormat build() { throw new RuntimeException(); }
  }
}
