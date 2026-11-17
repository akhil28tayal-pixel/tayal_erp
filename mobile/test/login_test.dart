import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tayal_erp/core/session.dart';
import 'package:tayal_erp/core/ui.dart';
import 'package:tayal_erp/features/auth/login_screen.dart';

void main() {
  testWidgets('sign-in screen asks for server, phone and password', (tester) async {
    final session = Session();
    await tester.pumpWidget(SessionScope(
      session: session,
      child: MaterialApp(theme: appTheme(), home: const LoginScreen()),
    ));
    expect(find.text('Server address'), findsOneWidget);
    expect(find.text('Phone number'), findsOneWidget);
    expect(find.text('Password'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Sign in'), findsOneWidget);
  });
}
