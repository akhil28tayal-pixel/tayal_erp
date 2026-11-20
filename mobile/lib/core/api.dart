import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

/// Error returned by the API (problem+json): a stable [code] plus a readable [detail].
class ApiException implements Exception {
  ApiException(this.status, this.code, this.detail, [this.fieldErrors = const {}]);

  final int status;
  final String code;
  final String detail;
  final Map<String, String> fieldErrors;

  @override
  String toString() => detail;
}

/// Thin HTTP client for the ERP API. The backend is the only source of truth: this class never decides
/// anything about business rules, it only sends requests, refreshes the session and reports errors.
class ApiClient {
  ApiClient({required this.baseUrl, required this.onTokens, required this.onSessionLost});

  String baseUrl; // e.g. http://192.168.1.20:8000
  String? accessToken;
  String? refreshToken;
  String? companyId;

  /// Called whenever the server issues new tokens (so they can be stored securely).
  final Future<void> Function(String access, String refresh) onTokens;

  /// Called when the refresh token is rejected: the user must sign in again.
  final void Function() onSessionLost;

  Future<bool>? _refreshing;

  /// One client for the whole app: it keeps the connection to the server open between requests, so each
  /// screen avoids a fresh TCP handshake (noticeably faster on Wi-Fi and mobile data).
  static final http.Client _http = http.Client();
  static const _timeout = Duration(seconds: 20);

  Uri _uri(String path, [Map<String, dynamic>? query]) {
    final q = <String, dynamic>{};
    query?.forEach((k, v) {
      if (v is Iterable) {
        if (v.isNotEmpty) q[k] = v.map((e) => e.toString()).toList(); // ?status=A&status=B
      } else if (v != null && v.toString().isNotEmpty) {
        q[k] = v.toString();
      }
    });
    final base = baseUrl.endsWith('/') ? baseUrl.substring(0, baseUrl.length - 1) : baseUrl;
    return Uri.parse('$base/api/v1$path').replace(queryParameters: q.isEmpty ? null : q);
  }

  Map<String, String> _headers({bool auth = true}) => {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        if (auth && accessToken != null) 'Authorization': 'Bearer $accessToken',
        if (auth && companyId != null) 'X-Company-Id': companyId!,
      };

  Future<dynamic> get(String path, {Map<String, dynamic>? query}) =>
      _send(() => _http.get(_uri(path, query), headers: _headers()));

  Future<dynamic> post(String path, {Object? body}) =>
      _send(() => _http.post(_uri(path), headers: _headers(), body: jsonEncode(body ?? {})));

  Future<dynamic> put(String path, {Object? body}) =>
      _send(() => _http.put(_uri(path), headers: _headers(), body: jsonEncode(body ?? {})));

  Future<dynamic> delete(String path) => _send(() => _http.delete(_uri(path), headers: _headers()));

  /// Streams a file from the server to disk (app updates), reporting bytes received and the total size.
  Future<void> download(String path, File to, {void Function(int got, int total)? progress}) async {
    Future<http.StreamedResponse> go() {
      final r = http.Request('GET', _uri(path));
      r.headers.addAll(_headers()..remove('Content-Type'));
      return _http.send(r);
    }

    var res = await go().timeout(_timeout);
    if (res.statusCode == 401 && await refresh()) res = await go().timeout(_timeout);
    if (res.statusCode != 200) {
      throw ApiException(res.statusCode, 'DOWNLOAD_FAILED', 'Download failed (${res.statusCode}). Try again.');
    }
    final total = res.contentLength ?? 0;
    var got = 0;
    final sink = to.openWrite();
    try {
      await for (final chunk in res.stream) {
        sink.add(chunk);
        got += chunk.length;
        progress?.call(got, total);
      }
    } finally {
      await sink.close();
    }
    if (total > 0 && got != total) {
      throw ApiException(0, 'DOWNLOAD_FAILED', 'The download was cut off. Try again.');
    }
  }

  /// Sign in. Mobile keeps the refresh token in the Android Keystore (flutter_secure_storage).
  Future<Map<String, dynamic>> login(String phone, String password, String deviceId) async {
    final res = await _raw(() => _http.post(_uri('/auth/login'),
        headers: _headers(auth: false),
        body: jsonEncode({'phone': phone, 'password': password, 'device_id': deviceId})));
    final data = _decode(res) as Map<String, dynamic>;
    accessToken = data['access_token'] as String;
    refreshToken = data['refresh_token'] as String;
    await onTokens(accessToken!, refreshToken!);
    return data;
  }

  Future<void> logout() async {
    if (refreshToken == null) return;
    try {
      await _raw(() => _http.post(_uri('/auth/logout'),
          headers: _headers(), body: jsonEncode({'refresh_token': refreshToken})));
    } catch (_) {
      // signing out locally is enough if the server is unreachable
    }
  }

  /// Rotates the refresh token. Concurrent callers share one request and its result.
  Future<bool> refresh() {
    if (refreshToken == null) return Future.value(false);
    return _refreshing ??= _doRefresh().whenComplete(() => _refreshing = null);
  }

  Future<bool> _doRefresh() async {
    final res = await _raw(() => _http.post(_uri('/auth/refresh'),
        headers: _headers(auth: false), body: jsonEncode({'refresh_token': refreshToken})));
    if (res.statusCode != 200) return false;
    final data = jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>;
    accessToken = data['access_token'] as String;
    refreshToken = data['refresh_token'] as String;
    await onTokens(accessToken!, refreshToken!);
    return true;
  }

  Future<dynamic> _send(Future<http.Response> Function() call) async {
    var res = await _raw(call);
    if (res.statusCode == 401) {
      final refreshed = await refresh();
      if (!refreshed) {
        onSessionLost();
        throw ApiException(401, 'UNAUTHENTICATED', 'Your session ended. Please sign in again.');
      }
      res = await _raw(call); // headers are rebuilt with the new token inside call()
    }
    return _decode(res);
  }

  Future<http.Response> _raw(Future<http.Response> Function() call) async {
    try {
      return await call().timeout(_timeout);
    } on SocketException {
      throw ApiException(0, 'NETWORK', 'Cannot reach the server at $baseUrl. Check Wi-Fi and the server address.');
    } on TimeoutException {
      throw ApiException(0, 'TIMEOUT', 'The server took too long to answer. Try again.');
    } on http.ClientException {
      throw ApiException(0, 'NETWORK', 'Cannot reach the server at $baseUrl.');
    }
  }

  dynamic _decode(http.Response res) {
    final text = utf8.decode(res.bodyBytes);
    final body = text.isEmpty ? null : jsonDecode(text);
    if (res.statusCode >= 200 && res.statusCode < 300) return body;
    if (body is Map<String, dynamic>) {
      final fields = <String, String>{};
      for (final e in (body['errors'] as List? ?? const [])) {
        if (e is Map && e['field'] != null) fields[e['field'].toString()] = e['message'].toString();
      }
      throw ApiException(res.statusCode, (body['code'] ?? 'HTTP_ERROR').toString(),
          (body['detail'] ?? 'Request failed (${res.statusCode})').toString(), fields);
    }
    throw ApiException(res.statusCode, 'HTTP_ERROR', 'Request failed (${res.statusCode})');
  }
}
