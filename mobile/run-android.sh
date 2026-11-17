#!/bin/bash
# Builds and runs the Tayal ERP Android app from your Mac.
#   ./run-android.sh          install & run on a phone connected by USB (or an emulator)
#   ./run-android.sh apk      build an APK you can send to any Android phone
#   ./run-android.sh test     run the app's unit/widget tests on the Mac (needs working Xcode command line tools)
# Needs Flutter and Android Studio (see README.md in this folder). First run sets up the Android project.
set -e
cd "$(dirname "$0")"

if ! command -v flutter >/dev/null; then
  echo "Flutter is not installed. Run:  brew install --cask flutter   then  flutter doctor"; exit 1
fi

# --- one-time: generate the Android project files around our code (existing files are kept) ---------
if [ ! -d android ]; then
  echo "==> Creating Android project files"
  flutter create --platforms=android --org com.tayal --project-name tayal_erp . >/dev/null
  rm -f test/widget_test.dart   # template test for the demo counter app, not ours
  MANIFEST=android/app/src/main/AndroidManifest.xml
  # App name, plain-HTTP access to the office server on the local network, and the phone dialer.
  perl -0pi -e 's/android:label="tayal_erp"/android:label="Tayal ERP"/' "$MANIFEST"
  perl -0pi -e 's/<application/<application\n        android:usesCleartextTraffic="true"/' "$MANIFEST"
  grep -q 'android.permission.INTERNET' "$MANIFEST" || \
    perl -0pi -e 's/<application/<uses-permission android:name="android.permission.INTERNET"\/>\n    <application/' "$MANIFEST"
  grep -q 'android.intent.action.DIAL' "$MANIFEST" || \
    perl -0pi -e 's/<\/manifest>/    <queries>\n        <intent><action android:name="android.intent.action.DIAL" \/><data android:scheme="tel" \/><\/intent>\n    <\/queries>\n<\/manifest>/' "$MANIFEST"
  # flutter_secure_storage needs Android 6.0+
  for f in android/app/build.gradle.kts android/app/build.gradle; do
    [ -f "$f" ] && perl -pi -e 's/minSdk\s*=\s*flutter\.minSdkVersion/minSdk = 23/; s/minSdkVersion\s+flutter\.minSdkVersion/minSdkVersion 23/' "$f"
  done
fi

# --- phone notifications (every run; each step only if not done yet) ------------------------------
MANIFEST=android/app/src/main/AndroidManifest.xml
grep -q 'android.permission.POST_NOTIFICATIONS' "$MANIFEST" || \
  perl -0pi -e 's/<application/<uses-permission android:name="android.permission.POST_NOTIFICATIONS"\/>\n    <application/' "$MANIFEST"
# flutter_local_notifications needs Java 8+ library desugaring
GRADLE=android/app/build.gradle.kts
if [ -f "$GRADLE" ] && ! grep -q 'isCoreLibraryDesugaringEnabled' "$GRADLE"; then
  perl -0pi -e 's/compileOptions \{/compileOptions {\n        isCoreLibraryDesugaringEnabled = true/' "$GRADLE"
  printf '\ndependencies {\n    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")\n}\n' >> "$GRADLE"
fi
GRADLE=android/app/build.gradle
if [ -f "$GRADLE" ] && ! grep -q 'coreLibraryDesugaringEnabled' "$GRADLE"; then
  perl -0pi -e 's/compileOptions \{/compileOptions {\n        coreLibraryDesugaringEnabled true/' "$GRADLE"
  printf '\ndependencies {\n    coreLibraryDesugaring "com.android.tools:desugar_jdk_libs:2.1.4"\n}\n' >> "$GRADLE"
fi

# --- "Share → Tayal ERP" for WhatsApp messages (every run; only if not done yet) -------------------
grep -q 'android.intent.action.SEND"' "$MANIFEST" || \
  perl -0pi -e 's#(</intent-filter>)#$1\n            <intent-filter>\n                <action android:name="android.intent.action.SEND"/>\n                <category android:name="android.intent.category.DEFAULT"/>\n                <data android:mimeType="text/plain"/>\n            </intent-filter>#' "$MANIFEST"
ACT=$(find android/app/src/main -name MainActivity.kt | head -1)
if [ -n "$ACT" ] && ! grep -q 'tayal/share' "$ACT"; then
  PKG=$(sed -n 's/^package \(.*\)$/\1/p' "$ACT")
  cat > "$ACT" <<KOTLIN
package $PKG

import android.content.Intent
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

// Keeps text shared from another app (WhatsApp → Share → Tayal ERP) until the Flutter side asks for it.
class MainActivity : FlutterActivity() {
    private var shared: String? = null
    private var channel: MethodChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val ch = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "tayal/share")
        ch.setMethodCallHandler { call, result ->
            if (call.method == "takeShared") {
                result.success(shared)
                shared = null
            } else {
                result.notImplemented()
            }
        }
        channel = ch
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        keep(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        keep(intent)
        if (shared != null) channel?.invokeMethod("shared", null)
    }

    private fun keep(i: Intent?) {
        if (i?.action == Intent.ACTION_SEND && i.type?.startsWith("text/") == true) {
            shared = i.getStringExtra(Intent.EXTRA_TEXT)
        }
    }
}
KOTLIN
fi

# --- branding (every run, so icon/splash updates are picked up) -----------------------------------
for d in mdpi hdpi xhdpi xxhdpi xxxhdpi; do
  cp "assets/launcher/ic_launcher-$d.png" "android/app/src/main/res/mipmap-$d/ic_launcher.png"
done
for f in android/app/src/main/res/drawable/launch_background.xml android/app/src/main/res/drawable-v21/launch_background.xml; do
  [ -d "$(dirname "$f")" ] && cat > "$f" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<!-- Launch screen: dark steel with the app icon, matching the app's header colour. -->
<layer-list xmlns:android="http://schemas.android.com/apk/res/android">
    <item><color android:color="#1C2830" /></item>
    <item><bitmap android:gravity="center" android:src="@mipmap/ic_launcher" /></item>
</layer-list>
XML
done

echo "==> Getting packages"
flutter pub get
if [ "$1" = "test" ]; then
  echo "==> Running app tests (on the Mac)"
  flutter test
  exit $?
fi

if [ "$1" = "apk" ]; then
  echo "==> Building APK"
  # Release build: several times faster than a debug build on the phone. (Signed with the same local key as
  # before, so it installs over the existing app.)
  flutter build apk --release
  APK="$(pwd)/build/app/outputs/flutter-apk/app-release.apk"
  echo
  echo "APK ready: $APK"
  echo "Send it to the phone (WhatsApp/Drive), open it, allow 'Install unknown apps' once."
  open -R "$APK" 2>/dev/null || true
  exit 0
fi

DEVICE=$(flutter devices --machine 2>/dev/null | python3 -c 'import json,sys
d=[x for x in json.load(sys.stdin) if x.get("targetPlatform","").startswith("android")]
print(d[0]["id"] if d else "")')
if [ -z "$DEVICE" ]; then
  echo
  echo "No Android phone or emulator found."
  echo "  Phone: Settings → About phone → tap 'Build number' 7 times → Developer options → USB debugging ON, connect USB, accept the prompt."
  echo "  Or run:  ./run-android.sh apk   and send the APK to the phone."
  exit 1
fi
echo "==> Running on $DEVICE  (press q to quit, r to reload)"
flutter run -d "$DEVICE"
