import 'package:flutter/material.dart';

import '../../core/session.dart';
import '../../core/ui.dart';
import '../orders/orders_screen.dart';

typedef Json = Map<String, dynamic>;

String _n(Object? v) {
  final d = double.tryParse('${v ?? ''}');
  if (d == null) return '';
  return d == d.roundToDouble() ? d.toStringAsFixed(0) : d.toStringAsFixed(2);
}

/// Factory trucks in the Direct godown that still have material on them.
class DirectTrucksScreen extends StatefulWidget {
  const DirectTrucksScreen({super.key});

  @override
  State<DirectTrucksScreen> createState() => _DirectTrucksScreenState();
}

class _DirectTrucksScreenState extends State<DirectTrucksScreen> {
  Future<List<Json>>? _future;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<List<Json>> _load() async =>
      List<Json>.from(await SessionScope.read(context).api.get('/direct-trucks') as List);

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    try {
      await f;
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Direct trucks waiting')),
      body: AsyncList<Json>(
        future: _future,
        onRefresh: _refresh,
        empty: const EmptyState(
            icon: Icons.local_shipping_outlined,
            title: 'No direct trucks waiting',
            message: 'They appear when a purchase to the Direct godown is pushed to Tally.'),
        itemBuilder: (context, t) {
          final lines = List<Json>.from(t['lines'] as List);
          return Panel(
            margin: const EdgeInsets.fromLTRB(16, 0, 16, 10),
            padding: const EdgeInsets.all(16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${t['truck_no']}', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
              Text('${const {'ambuja': 'Ambuja', 'shree': 'Shree'}[t['supplier']] ?? 'Factory truck'} · Inv ${t['invoice_no']}',
                  style: const TextStyle(color: AppColors.steel, fontSize: 15)),
              Gap.s,
              for (final l in lines)
                Text('${_n(l['left_qty'] ?? l['qty'])} ${(l['uom'] as String).toLowerCase()} ${l['tally_item']} left',
                    style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
            ]),
          );
        },
      ),
    );
  }
}

/// Delivered orders sent to Tally that the admin has not approved there yet (or that failed / were rejected).
class TallySalesScreen extends StatefulWidget {
  const TallySalesScreen({super.key});

  @override
  State<TallySalesScreen> createState() => _TallySalesScreenState();
}

class _TallySalesScreenState extends State<TallySalesScreen> {
  Future<List<Json>>? _future;
  bool _checking = false;

  static const _label = {
    'WAITING_APPROVAL': ('Waiting for approval in Tally', AppColors.warn),
    'FAILED': ('Not sent to Tally', AppColors.bad),
    'REJECTED': ('Rejected in Tally', AppColors.bad),
    'APPROVED': ('Approved in Tally', AppColors.ok),
    'PENDING': ('Sending', AppColors.steel),
  };

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<List<Json>> _load() async {
    final api = SessionScope.read(context).api;
    final out = <Json>[];
    for (final st in ['WAITING_APPROVAL', 'FAILED', 'REJECTED']) {
      final r = await api.get('/tally/sales', query: {'status': st}) as Json;
      out.addAll(List<Json>.from(r['items'] as List));
    }
    return out;
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    try {
      await f;
    } catch (_) {}
  }

  Future<void> _check() async {
    setState(() => _checking = true);
    try {
      final r = await SessionScope.read(context).api.post('/tally/sales/check') as Json;
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text('${r['approved'] ?? 0} approved, ${r['rejected'] ?? 0} rejected, ${r['waiting'] ?? 0} waiting')));
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _checking = false);
      _refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Sales in Tally')),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: SizedBox(
            width: double.infinity,
            height: 52,
            child: FilledButton(
              onPressed: _checking ? null : _check,
              child: Text(_checking ? 'Reading Tally…' : 'Check Tally for approvals', style: const TextStyle(fontSize: 17)),
            ),
          ),
        ),
        const Padding(
          padding: EdgeInsets.fromLTRB(20, 4, 20, 8),
          child: Text('The admin approves in Tally: open the Optional voucher and press Ctrl+L, then save.',
              style: TextStyle(color: AppColors.steel)),
        ),
        Expanded(
          child: AsyncList<Json>(
            future: _future,
            onRefresh: _refresh,
            empty: const EmptyState(icon: Icons.verified_outlined, title: 'Nothing waiting', message: 'All sales are approved in Tally.'),
            itemBuilder: (context, s) {
              final (label, color) = _label[s['status']] ?? ('${s['status']}', AppColors.steel);
              return Panel(
                margin: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: ListTile(
                  contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                  onTap: () => Navigator.of(context)
                      .push(MaterialPageRoute(builder: (_) => OrderDetailScreen(orderId: s['order_id'] as String))),
                  title: Text('${s['dealer_name']}', style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
                  subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('${s['order_no']} · ${inr(s['amount'] as String?)}', style: const TextStyle(fontSize: 15)),
                    StatusChip(label, color: color),
                    if (s['error'] != null) Text('${s['error']}', style: const TextStyle(color: AppColors.bad)),
                  ]),
                  trailing: const Icon(Icons.chevron_right),
                ),
              );
            },
          ),
        ),
      ]),
    );
  }
}
