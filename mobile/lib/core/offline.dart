import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api.dart';

/// Booking with a weak network.
///
/// * Dealers and materials are kept on the phone (a copy refreshed whenever the network works), so the order
///   screen opens and the dealer can be picked without a signal.
/// * An order that cannot reach the server is saved on the phone ("waiting to send") and sent by itself as soon
///   as the network is back. Each booking carries a reference ([newClientRef]); if the same booking reaches the
///   server twice, the server gives back the first order instead of making a second one.
class Offline extends ChangeNotifier {
  Offline._();

  static final Offline instance = Offline._();
  static const _store = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));
  static const _key = 'outbox_v1';

  List<Map<String, dynamic>> outbox = [];
  bool sending = false;
  Timer? _timer;
  ApiClient? _api;

  /// True when the error means "no network / server not reachable" (as opposed to the server saying no).
  static bool isNetwork(Object e) => e is ApiException && (e.code == 'NETWORK' || e.code == 'TIMEOUT');

  static String newClientRef() {
    final r = Random.secure();
    return 'app-${DateTime.now().millisecondsSinceEpoch}-${List.generate(6, (_) => r.nextInt(256).toRadixString(16).padLeft(2, '0')).join()}';
  }

  Future<void> start(ApiClient api) async {
    _api = api;
    try {
      final raw = await _store.read(key: _key);
      outbox = raw == null ? [] : List<Map<String, dynamic>>.from(jsonDecode(raw) as List);
    } catch (_) {
      outbox = [];
    }
    notifyListeners();
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 45), (_) => flush());
    unawaited(flush());
  }

  void stop() {
    _timer?.cancel();
    _timer = null;
  }

  Future<void> _save() async {
    await _store.write(key: _key, value: jsonEncode(outbox));
    notifyListeners();
  }

  /// Keep an order on the phone to send later. [label] is what the list shows ("Sharma · 400 bag Ambuja").
  Future<void> keep(Map<String, dynamic> body, String label) async {
    outbox.add({'body': body, 'label': label, 'saved_at': DateTime.now().toIso8601String(), 'error': null});
    await _save();
  }

  Future<void> remove(Map<String, dynamic> item) async {
    outbox.remove(item);
    await _save();
  }

  /// Send everything waiting. Stops at the first network failure (still no signal). An order the server refuses
  /// (e.g. "same order already booked") stays with the reason so the user can decide. Returns orders booked.
  Future<List<Map<String, dynamic>>> flush({bool confirmDuplicateFor = false, Map<String, dynamic>? only}) async {
    final api = _api;
    if (api == null || sending || outbox.isEmpty) return const [];
    sending = true;
    notifyListeners();
    final booked = <Map<String, dynamic>>[];
    try {
      for (final item in List<Map<String, dynamic>>.from(outbox)) {
        if (only != null && !identical(item, only)) continue;
        if (only == null && item['error'] != null) continue; // waits for the user
        final body = Map<String, dynamic>.from(item['body'] as Map);
        if (confirmDuplicateFor) body['confirm_duplicate'] = true;
        try {
          final o = await api.post('/orders', body: body) as Map<String, dynamic>;
          outbox.remove(item);
          booked.add(o);
        } on ApiException catch (e) {
          if (isNetwork(e)) break;
          item['error'] = e.detail;
          item['code'] = e.code;
        }
      }
    } finally {
      sending = false;
      await _save();
    }
    return booked;
  }

  // ---------------------------------------------------------------- copies of dealers and materials
  static File _file(String name) => File('${Directory.systemTemp.path}/tayal_$name.json');

  static Future<void> writeCache(String name, Object data) async {
    try {
      await _file(name).writeAsString(jsonEncode(data));
    } catch (_) {
      // a cache that cannot be written is just not there next time
    }
  }

  static Future<dynamic> readCache(String name) async {
    try {
      final f = _file(name);
      if (!await f.exists()) return null;
      return jsonDecode(await f.readAsString());
    } catch (_) {
      return null;
    }
  }

  /// Refresh the phone's copy of the dealers this user can book for (all pages) and the materials.
  static Future<void> refreshCaches(ApiClient api) async {
    try {
      final products = await api.get('/products', query: {'page_size': 200, 'erp_enabled': 'true'}) as Map<String, dynamic>;
      await writeCache('products', products['items'] as List);
      final dealers = <dynamic>[];
      for (var page = 1; page <= 20; page++) {
        final r = await api.get('/dealers', query: {'page': page, 'page_size': 200}) as Map<String, dynamic>;
        dealers.addAll((r['items'] as List).map((d) => {
              'id': d['id'], 'name': d['name'], 'code': d['code'], 'status': d['status'],
              'blocked_reason': d['blocked_reason'],
            }));
        if (dealers.length >= (r['total'] as int)) break;
      }
      await writeCache('dealers', dealers);
    } catch (_) {
      // no network: keep the copy we have
    }
  }
}
