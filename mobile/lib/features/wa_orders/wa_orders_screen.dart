import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/api.dart';
import '../../core/session.dart';
import '../../core/ui.dart';
import '../orders/orders_screen.dart' show NewOrderScreen, OrderDetailScreen;

typedef Json = Map<String, dynamic>;

const _green = Color(0xFF1F8F4E);
const _bubble = Color(0xFFDCF8C6);

/// Orders from WhatsApp: paste (or share) a dealer's message; it is read into a draft order to check and book.
/// Messages dealers send to the business number show up here by themselves.
class WhatsAppOrdersScreen extends StatefulWidget {
  const WhatsAppOrdersScreen({super.key, this.sharedText});

  /// Text shared from WhatsApp (message → Share → Tayal ERP): read straight away.
  final String? sharedText;

  @override
  State<WhatsAppOrdersScreen> createState() => _WhatsAppOrdersScreenState();
}

class _WhatsAppOrdersScreenState extends State<WhatsAppOrdersScreen> {
  final _text = TextEditingController();
  final _phone = TextEditingController();
  bool _reading = false;
  Future<List<Json>>? _future;

  @override
  void initState() {
    super.initState();
    if (widget.sharedText != null) {
      _text.text = widget.sharedText!;
      WidgetsBinding.instance.addPostFrameCallback((_) => _read());
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<List<Json>> _load() async {
    final r = await SessionScope.read(context).api.get('/whatsapp-orders', query: {'status': 'OPEN'}) as Json;
    return List<Json>.from(r['items'] as List);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<void> _paste() async {
    final d = await Clipboard.getData(Clipboard.kTextPlain);
    final t = d?.text?.trim() ?? '';
    if (t.isEmpty) {
      if (mounted) showError(context, 'Nothing copied. In WhatsApp long-press the message → Copy.');
      return;
    }
    setState(() => _text.text = t);
  }

  Future<void> _read() async {
    final text = _text.text.trim();
    if (text.length < 2) return;
    setState(() => _reading = true);
    try {
      final m = await SessionScope.read(context).api.post('/whatsapp-orders/read', body: {
        'text': text,
        if (_phone.text.trim().isNotEmpty) 'from_phone': _phone.text.trim(),
      }) as Json;
      if (!mounted) return;
      _text.clear();
      _phone.clear();
      unawaited(_refresh());
      await _open(m);
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _reading = false);
    }
  }

  /// Draft made → check & book it. Not sure → finish it in the order form. Not an order → say so.
  Future<void> _open(Json m) async {
    final nav = Navigator.of(context);
    switch (m['status']) {
      case 'DRAFTED':
        await nav.push(MaterialPageRoute(builder: (_) => NewOrderScreen(draftId: m['order_id'] as String)));
      case 'NEEDS_HELP':
        await nav.push(MaterialPageRoute(builder: (_) => NewOrderScreen(message: m)));
      case 'BOOKED':
        await nav.push(MaterialPageRoute(builder: (_) => OrderDetailScreen(orderId: m['order_id'] as String)));
      case 'NOT_ORDER':
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('That does not look like an order.')));
      default:
        showError(context, (m['note'] as String?) ?? 'Could not read the message');
    }
    if (mounted) _refresh();
  }

  Future<void> _setAside(Json m) async {
    try {
      await SessionScope.read(context).api.post('/whatsapp-orders/${m['id']}/ignore');
      _refresh();
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final canBook = SessionScope.of(context).can('orders.create');
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('WhatsApp orders')),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(padding: const EdgeInsets.only(bottom: 32), children: [
          if (canBook)
            Panel(
              margin: const EdgeInsets.fromLTRB(16, 16, 16, 0),
              padding: const EdgeInsets.all(16),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                const Text('Dealer\'s message', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
                Gap.xs,
                const Text('In WhatsApp long-press the message → Copy, then tap Paste. Or use Share → Tayal ERP.',
                    style: TextStyle(color: AppColors.steel)),
                Gap.m,
                TextField(
                  controller: _text,
                  minLines: 3,
                  maxLines: 8,
                  style: const TextStyle(fontSize: 17),
                  decoration: const InputDecoration(hintText: 'e.g. Sharma ji 200 bag ambuja plus kal bhejna'),
                ),
                Gap.s,
                TextField(
                  controller: _phone,
                  keyboardType: TextInputType.phone,
                  decoration: const InputDecoration(labelText: 'Dealer\'s mobile (optional, helps find the dealer)'),
                ),
                Gap.m,
                Row(children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(minimumSize: const Size(0, 56)),
                      onPressed: _reading ? null : _paste,
                      icon: const Icon(Icons.content_paste),
                      label: const Text('Paste', style: TextStyle(fontSize: 17)),
                    ),
                  ),
                  Gap.m,
                  Expanded(
                    flex: 2,
                    child: FilledButton.icon(
                      style: FilledButton.styleFrom(minimumSize: const Size(0, 56), backgroundColor: _green),
                      onPressed: _reading ? null : _read,
                      icon: _reading
                          ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                          : const Icon(Icons.auto_awesome),
                      label: Text(_reading ? 'Reading…' : 'Read & make order', style: const TextStyle(fontSize: 17)),
                    ),
                  ),
                ]),
              ]),
            ),
          const SectionTitle('To check'),
          FutureBuilder<List<Json>>(
            future: _future,
            builder: (context, snap) {
              if (!snap.hasData && snap.connectionState != ConnectionState.done) {
                return const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator()));
              }
              if (snap.hasError) {
                return EmptyState(icon: Icons.cloud_off_outlined, title: 'Could not load', message: '${snap.error}');
              }
              final items = snap.data ?? const [];
              if (items.isEmpty) {
                return const EmptyState(icon: Icons.chat_outlined, title: 'Nothing to check',
                    message: 'WhatsApp orders appear here.');
              }
              return Column(children: [for (final m in items) _MessageCard(m: m, onOpen: () => _open(m), onSetAside: () => _setAside(m))]);
            },
          ),
        ]),
      ),
    );
  }
}

class _MessageCard extends StatelessWidget {
  const _MessageCard({required this.m, required this.onOpen, required this.onSetAside});

  final Json m;
  final VoidCallback onOpen;
  final VoidCallback onSetAside;

  @override
  Widget build(BuildContext context) {
    final r = (m['reading'] as Json?) ?? const {};
    final lines = List<Json>.from((r['lines'] as List?) ?? const []);
    final drafted = m['status'] == 'DRAFTED';
    final t = DateTime.tryParse(m['received_at'] as String)?.toLocal();
    return Panel(
      margin: const EdgeInsets.fromLTRB(16, 0, 16, 12),
      padding: const EdgeInsets.all(14),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(
            child: Text(
              [(m['from_name'] as String?) ?? (m['source'] == 'PASTE' ? 'Pasted' : 'Dealer'),
                if (t != null) '${t.day}/${t.month} ${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}'].join(' · '),
              style: const TextStyle(color: AppColors.steel),
            ),
          ),
          StatusChip(drafted ? 'Draft ready' : m['status'] == 'FAILED' ? 'Could not read' : 'Needs a look',
              color: drafted ? AppColors.ok : m['status'] == 'FAILED' ? AppColors.bad : AppColors.warn),
        ]),
        Gap.s,
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(color: _bubble, borderRadius: BorderRadius.circular(12)),
          child: Text(m['text'] as String, style: const TextStyle(fontSize: 16)),
        ),
        Gap.s,
        Text('Dealer: ${(m['dealer_name'] as String?) ?? 'not sure'}',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700,
                color: m['dealer_name'] == null ? AppColors.warn : AppColors.ink)),
        for (final l in lines)
          Text(l['product_id'] == null
              ? '"${l['said']}" — not matched'
              : '${qty('${l['qty']}')} ${(l['unit'] as String? ?? '').toLowerCase()} ${l['product_name']}',
              style: TextStyle(fontSize: 15, color: l['product_id'] == null ? AppColors.warn : AppColors.ink)),
        if (m['note'] != null) Text('${m['note']}', style: const TextStyle(color: AppColors.warn)),
        Gap.m,
        Row(children: [
          Expanded(
            child: FilledButton(
              style: FilledButton.styleFrom(minimumSize: const Size(0, 52)),
              onPressed: onOpen,
              child: Text(drafted ? 'Check & book' : 'Finish the order', style: const TextStyle(fontSize: 16)),
            ),
          ),
          Gap.s,
          TextButton(onPressed: onSetAside, child: const Text('Set aside')),
        ]),
      ]),
    );
  }
}
