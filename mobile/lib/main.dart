import 'package:flutter/material.dart';

import 'core/notifier.dart';
import 'core/session.dart';
import 'core/ui.dart';
import 'features/auth/login_screen.dart';
import 'features/home/home_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final session = Session()..restore();
  runApp(TayalApp(session: session));
}

class TayalApp extends StatelessWidget {
  const TayalApp({super.key, required this.session});

  final Session session;

  @override
  Widget build(BuildContext context) {
    return SessionScope(
      session: session,
      child: MaterialApp(
        title: 'Tayal ERP',
        navigatorKey: appNavigator,
        debugShowCheckedModeBanner: false,
        theme: appTheme(),
        home: const _Root(),
      ),
    );
  }
}

/// Shows sign-in or the role-aware home, depending on the session.
class _Root extends StatelessWidget {
  const _Root();

  @override
  Widget build(BuildContext context) {
    final s = SessionScope.of(context);
    switch (s.status) {
      case SessionStatus.loading:
        return const _Splash();
      case SessionStatus.signedOut:
        return const LoginScreen();
      case SessionStatus.signedIn:
        if (s.company == null) {
          return Scaffold(
            body: Center(
              child: EmptyState(
                icon: Icons.lock_outline,
                title: 'No company access yet',
                message: 'Ask your admin to give your account a role, then sign in again.',
                action: OutlinedButton(onPressed: s.signOut, child: const Text('Sign out')),
              ),
            ),
          );
        }
        // Key by company so screens reload when the company changes.
        return HomeScreen(key: ValueKey(s.company!.id));
    }
  }
}


/// Shown while the saved session is restored: same look as the Android launch screen.
class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.blue,
      body: Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Container(
            width: 72,
            height: 72,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: AppColors.hivis, borderRadius: BorderRadius.circular(20)),
            child: const Text('T', style: TextStyle(fontSize: 40, fontWeight: FontWeight.w900, color: AppColors.blue)),
          ),
          Gap.xl,
          const SizedBox(width: 28, height: 28, child: CircularProgressIndicator(strokeWidth: 2.5, color: AppColors.hivis)),
        ]),
      ),
    );
  }
}
