import 'package:flutter/services.dart';

/// Text shared to the app from another app (WhatsApp → message → Share → Tayal ERP).
/// The Android side (MainActivity.kt, written by run-android.sh) keeps the text until Flutter asks for it.
class ShareIn {
  static const _channel = MethodChannel('tayal/share');
  static void Function()? onShared;
  static bool _listening = false;

  /// The shared text waiting to be used, once (null if nothing was shared).
  static Future<String?> take() async {
    if (!_listening) {
      _listening = true;
      _channel.setMethodCallHandler((call) async {
        if (call.method == 'shared') onShared?.call();
      });
    }
    try {
      final t = await _channel.invokeMethod<String>('takeShared');
      return (t == null || t.trim().isEmpty) ? null : t;
    } on MissingPluginException {
      return null; // not built with share support yet
    } on PlatformException {
      return null;
    }
  }
}
