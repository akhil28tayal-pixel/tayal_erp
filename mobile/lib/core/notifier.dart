import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api.dart';

/// The app's navigator, so a tapped notification can open the right screen from anywhere.
final appNavigator = GlobalKey<NavigatorState>();

/// Opens the screen for a notification link such as "/orders/<id>". Set by the home screen.
void Function(String link)? openLink;

/// Phone notifications: asks the server every 30 seconds while the app is open (or recently used) for anything
/// new for this user — an order waiting for them, their order approved / on hold / delivered, a sale rejected in
/// Tally — and shows each one as an Android notification. Tapping it opens the order.
///
/// When the phone has closed the app completely, notifications arrive the next time it is opened.
class Notifier extends ChangeNotifier {
  Notifier._();

  static final Notifier instance = Notifier._();
  static const _store = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));
  final _plugin = FlutterLocalNotificationsPlugin();

  int unread = 0;
  ApiClient? _api;
  Timer? _timer;
  bool _ready = false;
  String? _lastSeen;
  int _nextId = 1;

  static const _channel = AndroidNotificationDetails(
    'orders', 'Orders',
    channelDescription: 'Orders waiting for you, approvals, deliveries',
    importance: Importance.high,
    priority: Priority.high,
    color: Color(0xFF1B4FD8),
  );

  Future<void> _init() async {
    if (_ready) return;
    try {
      await _plugin.initialize(
        const InitializationSettings(android: AndroidInitializationSettings('@mipmap/ic_launcher')),
        onDidReceiveNotificationResponse: (r) {
          final link = r.payload;
          if (link != null && link.isNotEmpty) openLink?.call(link);
        },
      );
      await _plugin
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
          ?.requestNotificationsPermission();
      // Opened by tapping a notification while the app was closed
      final launch = await _plugin.getNotificationAppLaunchDetails();
      final link = launch?.notificationResponse?.payload;
      if (launch?.didNotificationLaunchApp == true && link != null) {
        Future.delayed(const Duration(milliseconds: 800), () => openLink?.call(link));
      }
      _ready = true;
    } catch (_) {
      _ready = false; // the in-app bell still works
    }
  }

  Future<void> start(ApiClient api, String userKey) async {
    _api = api;
    await _init();
    _lastSeen = await _store.read(key: 'notes_seen_$userKey');
    _key = userKey;
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 30), (_) => poll());
    await poll();
  }

  String _key = '';

  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  /// Ask the server for anything new; show new unread ones as phone notifications.
  Future<void> poll() async {
    final api = _api;
    if (api == null) return;
    try {
      final r = await api.get('/notifications', query: {
        'limit': 20,
        if (_lastSeen != null) 'after': _lastSeen,
      }) as Map<String, dynamic>;
      unread = r['unread'] as int? ?? 0;
      final items = List<Map<String, dynamic>>.from(r['items'] as List);
      if (items.isNotEmpty) {
        final first = _lastSeen == null; // first run on this phone: don't replay the old ones
        _lastSeen = items.first['created_at'] as String;
        await _store.write(key: 'notes_seen_$_key', value: _lastSeen);
        if (!first && _ready) {
          for (final n in items.reversed.where((n) => n['read_at'] == null).take(5)) {
            await _plugin.show(_nextId++, n['title'] as String, n['body'] as String?,
                const NotificationDetails(android: _channel), payload: n['link'] as String?);
          }
        }
      }
      notifyListeners();
    } catch (_) {
      // no network: try again next time
    }
  }

  Future<List<Map<String, dynamic>>> list() async {
    final r = await _api!.get('/notifications', query: {'limit': 50}) as Map<String, dynamic>;
    unread = r['unread'] as int? ?? 0;
    notifyListeners();
    return List<Map<String, dynamic>>.from(r['items'] as List);
  }

  Future<void> markRead([List<String>? ids]) async {
    await _api!.post('/notifications/read', body: {'ids': ids});
    await poll();
  }
}
