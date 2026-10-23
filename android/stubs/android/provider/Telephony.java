package android.provider;
public class Telephony {
  public static class Sms {
    public static class Intents {
      public static final String SMS_RECEIVED_ACTION = "android.provider.Telephony.SMS_RECEIVED";
      public static android.telephony.SmsMessage[] getMessagesFromIntent(android.content.Intent intent) { throw new RuntimeException(); }
    }
  }
}
