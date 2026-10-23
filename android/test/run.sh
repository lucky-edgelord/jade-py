#!/usr/bin/env bash
# Plain-JVM tests of Jade Link's Android-free code: Sentry's rules engine and casting. (The link itself is tested from
# Python against a real jade serve: tests/test_link_java.py.)
set -euo pipefail
cd "$(dirname "$0")"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT; mkdir -p "$W/classes"
S=../src/app/netrunner/jade
javac -nowarn -d "$W/classes" $S/MiniJson.java $S/CastClient.java $S/FileServer.java $S/SentryEngine.java $S/SentrySystems.java CastTest.java SentryTest.java
keytool -genkeypair -keystore "$W/tv.jks" -storepass testpass -keypass testpass -alias tv -keyalg RSA -keysize 2048 -validity 2 -dname "CN=Fake Chromecast" >/dev/null 2>&1
java -cp "$W/classes" CastTest "$W/tv.jks" "$W/files"
java -cp "$W/classes" SentryTest
