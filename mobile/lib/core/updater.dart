import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'ui.dart';

/// App updates from the ERP server (no Play Store, no APK sent around). When the app opens it asks the server for
/// the newest published build; if that is newer than this one it offers to download it and hands it to Android's
/// installer, which updates the app in place (sign-in and saved data are kept). Android always asks the user to
/// confirm the install; the first time it also asks to allow installing apps from Tayal ERP.
class Updater {
  Updater._();

  static const _ch = MethodChannel('tayal/app');
  static bool _offeredThisRun = false;

  static Future<int?> buildNumber() async {
    try {
      return await _ch.invokeMethod<int>('versionCode');
    } on Object {
      return null; // older Android project without the update channel
    }
  }

  static Future<String?> versionName() async {
    try {
      return await _ch.invokeMethod<String>('versionName');
    } on Object {
      return null;
    }
  }

  /// Ask the server; offer the update if there is one. [manual]: also say when there's nothing new.
  static Future<void> check(BuildContext context, ApiClient api, {bool manual = false}) async {
    if (!Platform.isAndroid || (!manual && _offeredThisRun)) return;
    final mine = await buildNumber();
    Map<String, dynamic>? latest;
    try {
      latest = await api.get('/app/android/latest') as Map<String, dynamic>?;
    } on ApiException catch (e) {
      if (manual && context.mounted) showError(context, e);
      return;
    }
    if (!context.mounted) return;
    final code = latest?['version_code'] as int?;
    if (mine == null || latest == null || code == null || code <= mine) {
      if (manual) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(mine == null
                ? 'This copy of the app has to be installed once by hand before it can update itself.'
                : 'You have the latest version.')));
      }
      return;
    }
    _offeredThisRun = true;
    final notes = latest['notes'] as String?;
    final mb = ((latest['size'] as int? ?? 0) / (1024 * 1024)).toStringAsFixed(0);
    final go = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Update available'),
        content: Text('Version ${latest!['version_name']} is ready ($mb MB).'
            '${notes != null && notes.isNotEmpty ? '\n\nWhat\'s new: $notes' : ''}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Later')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Update now')),
        ],
      ),
    );
    if (go == true && context.mounted) await _download(context, api, code);
  }

  static Future<void> _download(BuildContext context, ApiClient api, int code) async {
    // Android must allow this app to install apps (asked once, in Android's own settings).
    if (await _ch.invokeMethod<bool>('canInstall') != true) {
      if (!context.mounted) return;
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Allow updates'),
          content: const Text('Android asks once: turn on "Allow from this source" for Tayal ERP on the next '
              'screen, then come back and tap Update again.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Open settings')),
          ],
        ),
      );
      if (ok == true) await _ch.invokeMethod('allowInstall');
      _offeredThisRun = false; // offer again when they come back
      return;
    }
    final dir = await _ch.invokeMethod<String>('updateDir');
    final file = File('$dir/tayal-erp-$code.apk');
    final progress = ValueNotifier<double?>(null);
    if (!context.mounted) return;
    unawaited(showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: const Text('Downloading update'),
        content: ValueListenableBuilder<double?>(
          valueListenable: progress,
          builder: (_, v, __) => Column(mainAxisSize: MainAxisSize.min, children: [
            LinearProgressIndicator(value: v),
            Gap.s,
            Text(v == null ? 'Starting…' : '${(v * 100).toStringAsFixed(0)}%'),
          ]),
        ),
      ),
    ));
    try {
      await api.download('/app/android/download', file,
          progress: (got, total) => progress.value = total > 0 ? got / total : null);
      if (context.mounted) Navigator.of(context, rootNavigator: true).pop();
      await _ch.invokeMethod('install', {'path': file.path});
    } on Object catch (e) {
      if (context.mounted) {
        Navigator.of(context, rootNavigator: true).pop();
        showError(context, e is ApiException ? e : ApiException(0, 'DOWNLOAD_FAILED', 'Download failed: $e'));
      }
      _offeredThisRun = false;
    }
  }
}
