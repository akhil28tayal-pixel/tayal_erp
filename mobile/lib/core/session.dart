import 'dart:math';

import 'package:flutter/widgets.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api.dart';

class CompanyAccess {
  CompanyAccess(this.id, this.code, this.name, this.roles, this.permissions);

  factory CompanyAccess.fromJson(Map<String, dynamic> j) => CompanyAccess(
        j['company_id'] as String,
        j['company_code'] as String,
        j['company_name'] as String,
        List<String>.from(j['roles'] as List),
        Set<String>.from(j['permissions'] as List),
      );

  final String id;
  final String code;
  final String name;
  final List<String> roles;
  final Set<String> permissions;
}

enum SessionStatus { loading, signedOut, signedIn }

/// App-wide session: server address, tokens (in the Android Keystore), user and current company.
class Session extends ChangeNotifier {
  Session() {
    api = ApiClient(baseUrl: '', onTokens: _saveTokens, onSessionLost: _lost);
  }

  static const _store = FlutterSecureStorage(aOptions: AndroidOptions(encryptedSharedPreferences: true));
  late final ApiClient api;

  SessionStatus status = SessionStatus.loading;
  String serverUrl = '';
  String userName = '';
  List<CompanyAccess> companies = [];
  CompanyAccess? company;
  String deviceId = '';

  bool can(String permission) => company?.permissions.contains(permission) ?? false;
  bool hasRole(String role) => company?.roles.contains(role) ?? false;

  Future<void> restore() async {
    serverUrl = await _store.read(key: 'server') ?? '';
    deviceId = await _store.read(key: 'device') ?? _newDeviceId();
    await _store.write(key: 'device', value: deviceId);
    api.baseUrl = serverUrl;
    api.refreshToken = await _store.read(key: 'refresh');
    if (serverUrl.isEmpty || api.refreshToken == null) {
      status = SessionStatus.signedOut;
      notifyListeners();
      return;
    }
    try {
      if (await api.refresh()) {
        final me = await api.get('/auth/me') as Map<String, dynamic>;
        await _apply(me['user'] as Map<String, dynamic>, me['companies'] as List);
        return;
      }
    } on ApiException {
      // offline or server down: fall through to sign-in screen with the saved server address
    }
    status = SessionStatus.signedOut;
    notifyListeners();
  }

  Future<void> signIn(String server, String phone, String password) async {
    serverUrl = server.trim();
    api.baseUrl = serverUrl;
    await _store.write(key: 'server', value: serverUrl);
    final data = await api.login(phone.trim(), password, deviceId);
    await _apply(data['user'] as Map<String, dynamic>, data['companies'] as List);
  }

  Future<void> signOut() async {
    await api.logout();
    await _store.delete(key: 'refresh');
    api.accessToken = null;
    api.refreshToken = null;
    status = SessionStatus.signedOut;
    notifyListeners();
  }

  Future<void> switchCompany(CompanyAccess c) async {
    company = c;
    api.companyId = c.id;
    await _store.write(key: 'company', value: c.id);
    notifyListeners();
  }

  Future<void> _apply(Map<String, dynamic> user, List companiesJson) async {
    userName = user['full_name'] as String;
    companies = companiesJson.map((c) => CompanyAccess.fromJson(c as Map<String, dynamic>)).toList();
    final saved = await _store.read(key: 'company');
    company = companies.where((c) => c.id == saved).firstOrNull ?? companies.firstOrNull;
    api.companyId = company?.id;
    status = SessionStatus.signedIn;
    notifyListeners();
  }

  Future<void> _saveTokens(String access, String refresh) => _store.write(key: 'refresh', value: refresh);

  void _lost() {
    _store.delete(key: 'refresh');
    status = SessionStatus.signedOut;
    notifyListeners();
  }

  static String _newDeviceId() {
    final r = Random.secure();
    return List.generate(16, (_) => r.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
  }
}

/// Makes the [Session] available to every screen: `SessionScope.of(context)`.
class SessionScope extends InheritedNotifier<Session> {
  const SessionScope({super.key, required Session session, required super.child}) : super(notifier: session);

  static Session of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<SessionScope>()!.notifier!;

  static Session read(BuildContext context) =>
      context.getInheritedWidgetOfExactType<SessionScope>()!.notifier!;
}
