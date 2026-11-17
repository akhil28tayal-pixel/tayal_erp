# Tayal ERP — Android app (Flutter)

Field app for sales, collection, godown and delivery staff. Talks to the same API as the web dashboard.

**Now:** sign-in, role-aware home, dealers (assigned only for sales/collection staff) with contacts (tap to call), delivery sites and customer rates, products in use with a bags/MT/kg converter, company switch, sign-out.
**Coming with each module:** orders (Step 3), godown stock (Step 4), loading and delivery/POD (Step 5), follow-ups and payments (Step 6).

## One-time setup on the Mac

```bash
brew install --cask flutter
brew install --cask android-studio      # open it once and let it install the Android SDK
flutter doctor --android-licenses       # accept all
flutter doctor                          # Android toolchain should be ✓
```

## Run

1. Start the server so phones on the same Wi-Fi can reach it: `./start-mac.sh` (it prints the address to use, e.g. `http://192.168.1.20:8000`). If macOS asks whether Python may accept incoming connections, choose **Allow**.
2. Then either:
   - phone on USB with USB debugging on: `./mobile/run-android.sh`
   - or build an APK to share: `./mobile/run-android.sh apk`
3. In the app, enter the server address and sign in with a user created on the web dashboard (Users page).

The phone must be on the same Wi-Fi as the Mac, or on Tailscale (then use the Mac's Tailscale IP).
