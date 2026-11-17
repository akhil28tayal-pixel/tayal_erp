import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/api.dart';
import '../../core/session.dart';
import '../../core/ui.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _server = TextEditingController();
  final _phone = TextEditingController();
  final _password = TextEditingController();
  bool _busy = false;
  bool _showPassword = false;
  bool _serverOpen = false;
  String? _error;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_server.text.isEmpty) {
      final saved = SessionScope.read(context).serverUrl;
      _server.text = saved.isNotEmpty ? saved : 'http://';
      _serverOpen = saved.isEmpty; // first launch: the server must be entered
    }
  }

  @override
  void dispose() {
    _server.dispose();
    _phone.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    FocusScope.of(context).unfocus();
    if (_phone.text.trim().isEmpty || _password.text.isEmpty) {
      setState(() => _error = 'Enter your phone number and password.');
      return;
    }
    if (!_server.text.trim().startsWith('http') || _server.text.trim().length < 12) {
      setState(() {
        _serverOpen = true;
        _error = 'Enter the server address, e.g. http://192.168.1.20:8000';
      });
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await SessionScope.read(context).signIn(_server.text, _phone.text, _password.text);
    } on ApiException catch (e) {
      setState(() {
        _error = e.detail;
        if (e.code == 'NETWORK' || e.code == 'TIMEOUT') _serverOpen = true;
      });
    } catch (_) {
      setState(() {
        _error = 'Check the server address, e.g. http://192.168.1.20:8000';
        _serverOpen = true;
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final top = MediaQuery.of(context).padding.top;
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Scaffold(
        backgroundColor: AppColors.concrete,
        body: ListView(
          padding: EdgeInsets.zero,
          children: [
            // Brand band
            Container(
              color: AppColors.blue,
              padding: EdgeInsets.fromLTRB(28, top + 36, 28, 40),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Container(
                  width: 52,
                  height: 52,
                  decoration: BoxDecoration(color: AppColors.hivis, borderRadius: BorderRadius.circular(14)),
                  alignment: Alignment.center,
                  child: const Text('T', style: TextStyle(fontSize: 28, fontWeight: FontWeight.w900, color: AppColors.blue)),
                ),
                Gap.xl,
                const Text('Tayal ERP',
                    style: TextStyle(color: Colors.white, fontSize: 30, fontWeight: FontWeight.w800, letterSpacing: -0.5)),
                Gap.s,
                const Text('Orders, stock, dispatch and collections for cement and TMT.',
                    style: TextStyle(color: AppColors.mist, fontSize: 15, height: 1.4)),
              ]),
            ),
            const _Stripe(),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 28, 20, 24),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Text('Sign in', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                Gap.xs,
                const Text('Use the phone number your admin registered.', style: TextStyle(color: AppColors.steel)),
                Gap.xl,
                TextField(
                  controller: _phone,
                  keyboardType: TextInputType.phone,
                  textInputAction: TextInputAction.next,
                  autofillHints: const [AutofillHints.telephoneNumber],
                  decoration: const InputDecoration(labelText: 'Phone number', prefixIcon: Icon(Icons.phone_outlined)),
                ),
                Gap.l,
                TextField(
                  controller: _password,
                  obscureText: !_showPassword,
                  autofillHints: const [AutofillHints.password],
                  onSubmitted: (_) => _submit(),
                  decoration: InputDecoration(
                    labelText: 'Password',
                    prefixIcon: const Icon(Icons.lock_outline),
                    suffixIcon: IconButton(
                      tooltip: _showPassword ? 'Hide password' : 'Show password',
                      icon: Icon(_showPassword ? Icons.visibility_off_outlined : Icons.visibility_outlined),
                      onPressed: () => setState(() => _showPassword = !_showPassword),
                    ),
                  ),
                ),
                if (_error != null) ...[
                  Gap.m,
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: AppColors.bad.withValues(alpha: 0.08),
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      const Icon(Icons.error_outline, color: AppColors.bad, size: 20),
                      Gap.s,
                      Expanded(child: Text(_error!, style: const TextStyle(color: AppColors.bad))),
                    ]),
                  ),
                ],
                Gap.xl,
                FilledButton(
                  onPressed: _busy ? null : _submit,
                  child: _busy
                      ? const SizedBox(
                          width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white))
                      : const Text('Sign in'),
                ),
                Gap.xl,
                // Server address: needed once, then tucked away.
                Panel(
                  margin: EdgeInsets.zero,
                  child: Column(children: [
                    ListTile(
                      leading: const Icon(Icons.dns_outlined, color: AppColors.steel),
                      title: const Text('Server settings', style: TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text(
                        _server.text.length > 8 ? _server.text : 'Not set',
                        style: const TextStyle(color: AppColors.steel),
                      ),
                      trailing: Icon(_serverOpen ? Icons.expand_less : Icons.expand_more),
                      onTap: () => setState(() => _serverOpen = !_serverOpen),
                    ),
                    if (_serverOpen)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                        child: TextField(
                          controller: _server,
                          keyboardType: TextInputType.url,
                          autocorrect: false,
                          onChanged: (_) => setState(() {}),
                          decoration: const InputDecoration(
                            labelText: 'Server address',
                            helperText: 'Shown on the office computer when the ERP starts',
                          ),
                        ),
                      ),
                  ]),
                ),
              ]),
            ),
          ],
        ),
      ),
    );
  }
}

/// Hi-vis safety stripe under the brand band.
class _Stripe extends StatelessWidget {
  const _Stripe();

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 8,
      child: CustomPaint(painter: _StripePainter(), size: const Size(double.infinity, 8)),
    );
  }
}

class _StripePainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = AppColors.blue);
    final p = Paint()..color = Colors.white.withValues(alpha: 0.18);
    for (double x = -size.height; x < size.width; x += 28) {
      final path = Path()
        ..moveTo(x, size.height)
        ..lineTo(x + 14, size.height)
        ..lineTo(x + 14 + size.height, 0)
        ..lineTo(x + size.height, 0)
        ..close();
      canvas.drawPath(path, p);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
