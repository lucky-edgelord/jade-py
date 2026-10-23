#!/usr/bin/env bash
# Build and sign the Jade Link APK (no Android SDK needed: API stubs + dx + apktool, like the Jade Vault app).
#   Release signing:  JADE_KEYSTORE=path.jks JADE_KEYSTORE_PASSWORD=... [JADE_KEY_ALIAS=netrunner] android/build-apk.sh
#   Without those, a local debug key is made (that APK cannot update one signed with the release key).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"; T=.tools; mkdir -p "$T"
VERSION="$(cat VERSION)"; CODE="$(cat VERSION_CODE)"
fetch() { # url file sha256
  if [ ! -f "$T/$2" ] || ! echo "$3  $T/$2" | sha256sum -c --status 2>/dev/null; then
    echo "Downloading $2…"; curl -fsSL -o "$T/$2" "$1"
    echo "$3  $T/$2" | sha256sum -c --status || { echo "Checksum mismatch for $2"; rm -f "$T/$2"; exit 1; }
  fi
}
fetch https://github.com/iBotPeaches/Apktool/releases/download/v2.10.0/apktool_2.10.0.jar apktool.jar c0350abbab5314248dfe2ee0c907def4edd14f6faef1f5d372d3d4abd28f0431
fetch https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar uber-apk-signer.jar e1299fd6fcf4da527dd53735b56127e8ea922a321128123b9c32d619bba1d835
fetch https://github.com/pxb1988/dex2jar/releases/download/v2.4/dex-tools-v2.4.zip dex-tools.zip ee7c45eb3c1d2474a6145d8d447e651a736a22d9664b6d3d3be5a5a817dda23a
[ -f "$T/dx.jar" ] || unzip -p "$T/dex-tools.zip" dex-tools-v2.4/lib/dx-30.0.2.jar > "$T/dx.jar"

rm -rf build && mkdir -p build/stubs build/classes build/out
cp -r proj build/proj
sed -i.bak -E "s/android:versionCode=\"[0-9]+\"/android:versionCode=\"$CODE\"/; s/android:versionName=\"[^\"]+\"/android:versionName=\"$VERSION\"/" build/proj/AndroidManifest.xml
sed -i.bak -E "s/versionCode: .*/versionCode: $CODE/; s/versionName: .*/versionName: \"$VERSION\"/" build/proj/apktool.yml
rm -f build/proj/*.bak
javac -nowarn --release 8 -d build/stubs $(find stubs -name '*.java')
javac -nowarn --release 8 -cp build/stubs -d build/classes $(find src -name '*.java')
java -cp "$T/dx.jar" com.android.dx.command.Main --dex --min-sdk-version=26 --output=build/proj/classes.dex build/classes
java -jar "$T/apktool.jar" b build/proj -o build/out/unsigned.apk >/dev/null

if [ -n "${JADE_KEYSTORE:-}" ]; then
  KS="$JADE_KEYSTORE"; PASS="${JADE_KEYSTORE_PASSWORD:?set JADE_KEYSTORE_PASSWORD}"; ALIAS="${JADE_KEY_ALIAS:-netrunner}"
else
  KS="$T/debug.jks"; PASS=android; ALIAS=debug
  [ -f "$KS" ] || keytool -genkeypair -keystore "$KS" -storepass "$PASS" -keypass "$PASS" -alias "$ALIAS" -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Jade Link Debug" >/dev/null
  echo "Signing with a DEBUG key (set JADE_KEYSTORE for a release build)."
fi
java -jar "$T/uber-apk-signer.jar" -a build/out/unsigned.apk --ks "$KS" --ksAlias "$ALIAS" --ksPass "$PASS" --ksKeyPass "$PASS" -o build/out/signed | grep -E "signature verified|SHA256:" || true
mkdir -p "$ROOT/dist"; cp build/out/signed/*-aligned-signed.apk "$ROOT/dist/jade-link-$VERSION.apk"
echo "dist/jade-link-$VERSION.apk"
