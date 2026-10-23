package android.hardware;
import java.util.List;
public class SensorManager {
  public static final int SENSOR_DELAY_FASTEST = 0, SENSOR_DELAY_GAME = 1, SENSOR_DELAY_UI = 2, SENSOR_DELAY_NORMAL = 3;
  public List<Sensor> getSensorList(int type) { throw new RuntimeException(); }
  public Sensor getDefaultSensor(int type) { throw new RuntimeException(); }
  public boolean registerListener(SensorEventListener l, Sensor s, int samplingPeriodUs) { throw new RuntimeException(); }
  public boolean registerListener(SensorEventListener l, Sensor s, int samplingPeriodUs, int maxReportLatencyUs) { throw new RuntimeException(); }
  public void unregisterListener(SensorEventListener l) { throw new RuntimeException(); }
  public boolean registerListener(SensorEventListener l, Sensor s, int samplingPeriodUs, android.os.Handler handler) { throw new RuntimeException(); }
}
