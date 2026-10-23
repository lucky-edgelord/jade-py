# Jade Link

The Android app that gives jade-py its phones and tablets. One install per device; up to four (or more) at once.

It does three things:

- **the link**: jade-py uses the device (phone, screen, senses and Sentry tools). It calls in to `jade serve`
  (Connect: jade-py's address and API token), or listens and jade-py calls it (Listen: add the `[[phones]]` lines it
  shows to jade-py's config), or both.
- **the dashboard**: once connected it opens jade-py's dashboard, with talk mode using the phone's own speech
  recognizer and voice.
- **kiosk mode**: Jade Link as the home screen, full screen and pinned; as device owner, a real lock.

Calls, texts and switching Sentry on ask the person holding the device first (a notification with Allow / Don't,
also listed in Jade Link). Screen control stays off until you switch on "Jade Link" in Android's Accessibility
settings, and notification access for Sentry is the same. A notice shows while it is connected, while Sentry
watches, and while Jade drives the screen.

## Set up

1. Install the APK and open it. Grant what you want Jade to use under "What Jade may use".
2. Connect: jade-py's address (e.g. `http://192.168.1.10:8760`, or a Tailscale name) and its API token; turn on
   Connect; Test. `jade serve` must listen beyond its machine: `api_host = "0.0.0.0"` and an `api_token`.
3. Or Listen: turn it on and add the snippet it shows to `~/.config/jade/config.toml`, then restart `jade serve`.
4. Give each device its own name (pixel, tablet, kitchen…): that is how Jade and Sentry tell them apart.
5. For a wall tablet: turn on "Start talk mode when it opens" and Kiosk mode. For a real lock, on a freshly reset
   device: `adb shell dpm set-device-owner app.netrunner.jadelink/app.netrunner.jade.AdminReceiver`.

Android only lets a background app use the camera and microphone if it was started while on screen: after the
device restarts, open Jade Link once. Turn battery optimisation off for it (the setup page has the button).

## Build

`./build-apk.sh` (Java 17+, no Android SDK: compile against the API stubs in `stubs/`, dex with dx, pack with apktool,
sign with uber-apk-signer; the three tools are downloaded once with checksums). Release signing:
`JADE_KEYSTORE=… JADE_KEYSTORE_PASSWORD=… ./build-apk.sh`; otherwise a local debug key. Output:
`../dist/jade-link-<VERSION>.apk`.

Tests: `test/run.sh` (Sentry's engine, casting) and, from the repository root, `pytest tests/test_link_java.py`
(the real link code on a plain JVM against a real `jade serve`, both directions).

Most of the device code (PhoneHost, JadeAccess, Sentry*, CastHost, World) comes from the Jade Vault app.
