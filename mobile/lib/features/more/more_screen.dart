import 'package:flutter/material.dart';

import '../../core/api.dart';
import '../../core/session.dart';
import '../../core/ui.dart';
import '../home/home_screen.dart';

class MoreScreen extends StatelessWidget {
  const MoreScreen({super.key});

  Future<void> _confirmSignOut(BuildContext context, Session s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Sign out?'),
        content: const Text('You will need your phone number and password to sign in again.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Sign out', style: TextStyle(color: AppColors.bad, fontWeight: FontWeight.w700)),
          ),
        ],
      ),
    );
    if (ok == true) await s.signOut();
  }

  void _pickCompany(BuildContext context, Session s) {
    showModalBottomSheet(
      context: context,
      builder: (c) => SafeArea(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(20, 0, 20, 8),
            child: Align(
              alignment: Alignment.centerLeft,
              child: Text('Switch company', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
            ),
          ),
          for (final co in s.companies)
            ListTile(
              leading: Monogram(co.name, size: 38),
              title: Text(co.name, style: const TextStyle(fontWeight: FontWeight.w600)),
              trailing: co.id == s.company!.id ? const Icon(Icons.check_circle, color: AppColors.ok) : null,
              onTap: () {
                Navigator.pop(c);
                s.switchCompany(co);
              },
            ),
          Gap.l,
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = SessionScope.of(context);
    final roles = s.company!.roles.map((r) => roleNames[r] ?? r).join(', ');
    return ListView(padding: EdgeInsets.zero, children: [
      ScreenHeader(
        title: 'Profile',
        bottom: Row(children: [
          Monogram(s.userName, size: 56, dark: true),
          Gap.l,
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(s.userName,
                  style: const TextStyle(color: Colors.white, fontSize: 19, fontWeight: FontWeight.w800)),
              Gap.xs,
              Text(roles, style: const TextStyle(color: AppColors.mist)),
            ]),
          ),
        ]),
      ),
      const SectionTitle('Work'),
      Panel(
        child: Column(children: [
          ListTile(
            leading: const Icon(Icons.business_outlined, color: AppColors.steel),
            title: const Text('Company'),
            subtitle: Text(s.company!.name, style: const TextStyle(color: AppColors.steel)),
            trailing: s.companies.length > 1 ? const Icon(Icons.chevron_right, color: AppColors.mist) : null,
            onTap: s.companies.length > 1 ? () => _pickCompany(context, s) : null,
          ),
          const Divider(indent: 56),
          ListTile(
            leading: const Icon(Icons.badge_outlined, color: AppColors.steel),
            title: const Text('Role'),
            subtitle: Text(roles, style: const TextStyle(color: AppColors.steel)),
          ),
          const Divider(indent: 56),
          ListTile(
            leading: const Icon(Icons.lock_outline, color: AppColors.steel),
            title: const Text('Change password'),
            trailing: const Icon(Icons.chevron_right, color: AppColors.mist),
            onTap: () => showModalBottomSheet(
              context: context,
              isScrollControlled: true,
              useSafeArea: true,
              builder: (_) => _ChangePasswordSheet(session: s),
            ),
          ),
        ]),
      ),
      const SectionTitle('App'),
      Panel(
        child: Column(children: [
          ListTile(
            leading: const Icon(Icons.dns_outlined, color: AppColors.steel),
            title: const Text('Server'),
            subtitle: Text(s.serverUrl, style: const TextStyle(color: AppColors.steel)),
          ),
          const Divider(indent: 56),
          const ListTile(
            leading: Icon(Icons.info_outline, color: AppColors.steel),
            title: Text('Version'),
            subtitle: Text('0.1.0', style: TextStyle(color: AppColors.steel)),
          ),
        ]),
      ),
      Gap.xl,
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: OutlinedButton.icon(
          style: OutlinedButton.styleFrom(
            foregroundColor: AppColors.bad,
            minimumSize: const Size.fromHeight(50),
            side: const BorderSide(color: AppColors.seam),
            backgroundColor: AppColors.slab,
          ),
          onPressed: () => _confirmSignOut(context, s),
          icon: const Icon(Icons.logout),
          label: const Text('Sign out'),
        ),
      ),
      Gap.xl,
    ]);
  }
}

/// Changing the password signs out every device, this phone included, so the user signs in again with it.
class _ChangePasswordSheet extends StatefulWidget {
  const _ChangePasswordSheet({required this.session});

  final Session session;

  @override
  State<_ChangePasswordSheet> createState() => _ChangePasswordSheetState();
}

class _ChangePasswordSheetState extends State<_ChangePasswordSheet> {
  final _current = TextEditingController();
  final _next = TextEditingController();
  final _again = TextEditingController();
  bool _busy = false;
  String? _error;

  Future<void> _save() async {
    final problem = _next.text.length < 8
        ? 'The new password needs at least 8 characters'
        : _next.text != _again.text
            ? 'The two new passwords do not match'
            : null;
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.session.api
          .post('/auth/password', body: {'current_password': _current.text, 'new_password': _next.text});
      if (!mounted) return;
      final messenger = ScaffoldMessenger.of(context);
      Navigator.of(context).pop();
      messenger.showSnackBar(const SnackBar(content: Text('Password changed. Sign in with the new password.')));
      await widget.session.signOut();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = e.detail;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 8, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text('Change password', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
        TextField(
            controller: _current,
            obscureText: true,
            decoration: const InputDecoration(labelText: 'Current password')),
        TextField(
            controller: _next,
            obscureText: true,
            decoration: const InputDecoration(labelText: 'New password', helperText: 'At least 8 characters')),
        TextField(
            controller: _again, obscureText: true, decoration: const InputDecoration(labelText: 'New password again')),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(_error!, style: const TextStyle(color: AppColors.bad)),
          ),
        Gap.l,
        FilledButton(onPressed: _busy ? null : _save, child: Text(_busy ? 'Saving…' : 'Change password')),
      ]),
    );
  }
}
