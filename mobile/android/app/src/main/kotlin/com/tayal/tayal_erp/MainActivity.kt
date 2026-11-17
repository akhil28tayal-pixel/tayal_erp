package com.tayal.tayal_erp

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
