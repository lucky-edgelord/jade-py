package android.media;
public class AudioRecord {
  public static final int STATE_INITIALIZED = 1;
  public AudioRecord(int audioSource, int sampleRateInHz, int channelConfig, int audioFormat, int bufferSizeInBytes) { throw new RuntimeException(); }
  public static int getMinBufferSize(int sampleRateInHz, int channelConfig, int audioFormat) { throw new RuntimeException(); }
  public int getState() { throw new RuntimeException(); }
  public void startRecording() { throw new RuntimeException(); }
  public int read(short[] audioData, int offsetInShorts, int sizeInShorts) { throw new RuntimeException(); }
  public void stop() { throw new RuntimeException(); }
  public void release() { throw new RuntimeException(); }
}
