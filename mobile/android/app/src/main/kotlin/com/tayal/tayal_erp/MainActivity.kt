package com.tayal.tayal_erp

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterActivity() {
    private var shared: String? = null
    private var channel: MethodChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // Text shared from another app (WhatsApp → Share → Tayal ERP), kept until the Flutter side asks for it.
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
        // App updates: this build's number, where to save a download, and handing a downloaded APK to Android.
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "tayal/app").setMethodCallHandler { call, result ->
            when (call.method) {
                "versionCode" -> {
                    val info = packageManager.getPackageInfo(packageName, 0)
                    val code = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
                    result.success(code)
                }
                "versionName" -> result.success(packageManager.getPackageInfo(packageName, 0).versionName)
                "updateDir" -> {
                    val d = File(cacheDir, "updates")
                    d.mkdirs()
                    result.success(d.absolutePath)
                }
                "canInstall" -> result.success(Build.VERSION.SDK_INT < 26 || packageManager.canRequestPackageInstalls())
                "allowInstall" -> {
                    startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                    result.success(null)
                }
                "install" -> {
                    val f = File(call.argument<String>("path")!!)
                    val uri = FileProvider.getUriForFile(this, "$packageName.updates", f)
                    val i = Intent(Intent.ACTION_VIEW)
                        .setDataAndType(uri, "application/vnd.android.package-archive")
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                    startActivity(i)
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
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
