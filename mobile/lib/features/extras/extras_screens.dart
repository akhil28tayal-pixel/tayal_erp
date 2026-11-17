import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api.dart';
import '../../core/notifier.dart';
import '../../core/offline.dart';
import '../../core/session.dart';
import '../../core/ui.dart';
import '../dealers/dealers_screen.dart';
import '../dispatch/dispatch_screens.dart' show DeliveryOrderScreen, doChip;
import '../orders/orders_screen.dart' show OrderDetailScreen, orderStatus, shortDate;

typedef Json = Map<String, dynamic>;

String _ago(String iso) {
  final t = DateTime.tryParse(iso)?.toLocal();
  if (t == null) return '';
  final m = DateTime.now().difference(t).inMinutes;
  if (m < 1) return 'just now';
  if (m < 60) return '$m min ago';
  if (m < 1440) return '${m ~/ 60} h ago';
  return '${t.day}/${t.month}';
}

String _ymd(DateTime d) => '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

/// Opens the screen behind a link from the server ("/orders/<id>", "/dealers/<id>", "/dispatch/<id>").
Future<void> openServerLink(BuildContext context, String link) async {
  final parts = link.split('/').where((p) => p.isNotEmpty).toList();
  if (parts.length < 2) return;
  final id = parts[1].split('?').first;
  Widget? screen;
  if (parts[0] == 'orders') screen = OrderDetailScreen(orderId: id);
  if (parts[0] == 'dispatch') screen = DeliveryOrderScreen(doId: id);
  if (parts[0] == 'dealers') screen = DealerDetailScreen(dealerId: id, name: '');
  if (screen != null) await Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen!));
}

// ================================================================== bell
/// Bell with the number of unread notifications, for app bars.
class BellButton extends StatelessWidget {
  const BellButton({super.key, this.color = Colors.white});

  final Color color;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Notifier.instance,
      builder: (context, _) {
        final n = Notifier.instance.unread;
        return IconButton(
          tooltip: 'Notifications',
          iconSize: 30,
          onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => const NotificationsScreen())),
          icon: Badge(
            isLabelVisible: n > 0,
            label: Text(n > 99 ? '99+' : '$n', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w800)),
            backgroundColor: AppColors.bad,
            child: Icon(Icons.notifications_outlined, color: color),
          ),
        );
      },
    );
  }
}

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  Future<List<Json>>? _future;

  @override
  void initState() {
    super.initState();
    _future = Notifier.instance.list();
  }

  Future<void> _refresh() async {
    final f = Notifier.instance.list();
    setState(() => _future = f);
    await f;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Notifications'), actions: [
        TextButton(
          onPressed: () async {
            await Notifier.instance.markRead();
            _refresh();
          },
          child: const Text('Mark all read', style: TextStyle(color: Colors.white)),
        ),
      ]),
      body: AsyncList<Json>(
        future: _future,
        onRefresh: _refresh,
        empty: const EmptyState(icon: Icons.notifications_none, title: 'Nothing yet',
            message: 'Orders waiting for you, approvals and deliveries show up here.'),
        itemBuilder: (context, n) {
          final unread = n['read_at'] == null;
          return Container(
            color: unread ? AppColors.blueTint : AppColors.slab,
            child: ListTile(
              contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
              leading: Icon(
                n['kind'] == 'order.delivered'
                    ? Icons.task_alt
                    : n['kind'] == 'tally.rejected' || n['kind'] == 'order.rejected'
                        ? Icons.error_outline
                        : n['kind'] == 'order.on_hold'
                            ? Icons.pan_tool_outlined
                            : Icons.receipt_long,
                color: unread ? AppColors.blue : AppColors.steel,
                size: 30,
              ),
              title: Text(n['title'] as String,
                  style: TextStyle(fontSize: 17, fontWeight: unread ? FontWeight.w800 : FontWeight.w500)),
              subtitle: Text('${n['body'] ?? ''}\n${_ago(n['created_at'] as String)}',
                  style: const TextStyle(fontSize: 14, height: 1.35)),
              isThreeLine: true,
              onTap: () async {
                if (unread) unawaited(Notifier.instance.markRead([n['id'] as String]));
                final link = n['link'] as String?;
                if (link != null) await openServerLink(context, link);
                if (mounted) _refresh();
              },
            ),
          );
        },
      ),
    );
  }
}

// ================================================================== search
/// One box for everything: order number, dealer name or mobile, truck number.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  Timer? _debounce;
  Json? _res;
  String _term = '';
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  void _changed(String v) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), () => _run(v.trim()));
  }

  Future<void> _run(String q) async {
    if (q.length < 2) {
      setState(() {
        _res = null;
        _term = q;
      });
      return;
    }
    setState(() {
      _busy = true;
      _term = q;
    });
    try {
      final r = await SessionScope.read(context).api.get('/search', query: {'q': q}) as Json;
      if (mounted && q == _term) setState(() => _res = r);
      if (mounted) setState(() => _error = null);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.detail);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _res;
    final orders = List<Json>.from((r?['orders'] as List?) ?? const []);
    final dealers = List<Json>.from((r?['dealers'] as List?) ?? const []);
    final trucks = List<Json>.from((r?['trucks'] as List?) ?? const []);
    final direct = List<Json>.from((r?['direct_trucks'] as List?) ?? const []);
    final none = r != null && orders.isEmpty && dealers.isEmpty && trucks.isEmpty && direct.isEmpty;
    Widget tile(IconData icon, String title, String sub, VoidCallback? onTap, {Widget? trailing}) => ListTile(
          contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 4),
          leading: Icon(icon, color: AppColors.blue, size: 28),
          title: Text(title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
          subtitle: Text(sub, style: const TextStyle(fontSize: 14)),
          trailing: trailing,
          onTap: onTap,
        );
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(
        title: TextField(
          autofocus: true,
          style: const TextStyle(color: Colors.white, fontSize: 18),
          cursorColor: Colors.white,
          textInputAction: TextInputAction.search,
          decoration: const InputDecoration(
            hintText: 'Order no., dealer, mobile, truck…',
            hintStyle: TextStyle(color: Colors.white70),
            border: InputBorder.none,
            enabledBorder: InputBorder.none,
            focusedBorder: InputBorder.none,
            filled: false,
          ),
          onChanged: _changed,
          onSubmitted: (v) => _run(v.trim()),
        ),
      ),
      body: ListView(children: [
        if (_busy) const LinearProgressIndicator(minHeight: 2),
        if (_error != null) Padding(padding: const EdgeInsets.all(20), child: Text(_error!, style: const TextStyle(color: AppColors.bad))),
        if (r == null && !_busy)
          const EmptyState(icon: Icons.search, title: 'Search everything',
              message: 'Type an order number, a dealer\'s name or mobile number, or a truck number.'),
        if (none) EmptyState(icon: Icons.search_off, title: 'Nothing found for "$_term"'),
        if (orders.isNotEmpty) ...[
          const SectionTitle('Orders'),
          Panel(child: Column(children: [
            for (final o in orders)
              tile(Icons.receipt_long, (o['order_no'] as String?) ?? 'Draft',
                  '${o['dealer_name']} · ${inr(o['total_amount']?.toString())}',
                  () => openServerLink(context, o['link'] as String),
                  trailing: Text(orderStatus[o['status']]?.$1 ?? '${o['status']}',
                      style: TextStyle(color: orderStatus[o['status']]?.$2 ?? AppColors.steel, fontWeight: FontWeight.w700))),
          ])),
        ],
        if (dealers.isNotEmpty) ...[
          const SectionTitle('Dealers'),
          Panel(child: Column(children: [
            for (final d in dealers)
              tile(Icons.storefront, d['name'] as String,
                  d['balance'] == null ? '' : 'Balance ${inr(d['balance'].toString())}',
                  () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => DealerDetailScreen(dealerId: d['id'] as String, name: d['name'] as String)))),
          ])),
        ],
        if (trucks.isNotEmpty) ...[
          const SectionTitle('Trucks'),
          Panel(child: Column(children: [
            for (final t in trucks)
              tile(Icons.local_shipping, t['vehicle'] as String, '${t['do_no']} · ${t['dealer_name']}',
                  () => openServerLink(context, t['link'] as String), trailing: doChip(t['status'] as String)),
          ])),
        ],
        if (direct.isNotEmpty) ...[
          const SectionTitle('Factory trucks'),
          Panel(child: Column(children: [
            for (final t in direct)
              tile(Icons.factory_outlined, t['truck_no'] as String,
                  'Invoice ${t['invoice_no']} · ${t['status'] == 'OPEN' ? 'loaded' : 'done'}', null),
          ])),
        ],
        Gap.xl,
      ]),
    );
  }
}

// ================================================================== sales report
class SalesReportScreen extends StatefulWidget {
  const SalesReportScreen({super.key});

  @override
  State<SalesReportScreen> createState() => _SalesReportScreenState();
}

class _SalesReportScreenState extends State<SalesReportScreen> {
  String _period = 'month';
  Future<(Json, Json)>? _future;

  (String, String) _range() {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    switch (_period) {
      case 'today':
        return (_ymd(today), _ymd(today));
      case 'yesterday':
        final y = today.subtract(const Duration(days: 1));
        return (_ymd(y), _ymd(y));
      case 'week':
        return (_ymd(today.subtract(Duration(days: today.weekday - 1))), _ymd(today));
      case 'last_month':
        return (_ymd(DateTime(now.year, now.month - 1, 1)), _ymd(DateTime(now.year, now.month, 0)));
      default:
        return (_ymd(DateTime(now.year, now.month, 1)), _ymd(today));
    }
  }

  Future<(Json, Json)> _load() async {
    final api = SessionScope.read(context).api;
    final (a, b) = _range();
    final r = await Future.wait([
      api.get('/reports/sales', query: {'date_from': a, 'date_to': b, 'group': 'product'}),
      api.get('/reports/sales', query: {'date_from': a, 'date_to': b, 'group': 'dealer'}),
    ]);
    return (r[0] as Json, r[1] as Json);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  void _pick(String p) => setState(() {
        _period = p;
        _future = _load();
      });

  @override
  Widget build(BuildContext context) {
    const periods = [('today', 'Today'), ('yesterday', 'Yesterday'), ('week', 'This week'), ('month', 'This month'), ('last_month', 'Last month')];
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Sales report')),
      body: Column(children: [
        SizedBox(
          height: 60,
          child: ListView(scrollDirection: Axis.horizontal, padding: const EdgeInsets.fromLTRB(12, 10, 12, 6), children: [
            for (final (k, l) in periods)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ChoiceChip(
                  label: Text(l, style: TextStyle(fontSize: 15, color: _period == k ? Colors.white : AppColors.ink)),
                  selected: _period == k,
                  selectedColor: AppColors.blue,
                  showCheckmark: false,
                  onSelected: (_) => _pick(k),
                ),
              ),
          ]),
        ),
        Expanded(
          child: FutureBuilder<(Json, Json)>(
            future: _future,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) return const SkeletonList(rows: 5);
              if (snap.hasError) {
                return EmptyState(icon: Icons.cloud_off_outlined, title: 'Could not load', message: '${snap.error}');
              }
              final (byProduct, byDealer) = snap.data!;
              final t = byProduct['total'] as Json;
              final prev = byProduct['previous'] as Json;
              final products = List<Json>.from(byProduct['rows'] as List);
              final dealers = List<Json>.from(byDealer['rows'] as List);
              String change(String a, String b) {
                final x = double.tryParse(a) ?? 0, y = double.tryParse(b) ?? 0;
                if (y == 0) return '';
                final p = (x - y) / y * 100;
                return '${p >= 0 ? '▲' : '▼'} ${p.abs().toStringAsFixed(0)}% vs before';
              }

              return RefreshIndicator(
                onRefresh: () async => _pick(_period),
                child: ListView(padding: const EdgeInsets.only(bottom: 24), children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                    child: Container(
                      padding: const EdgeInsets.all(18),
                      decoration: BoxDecoration(
                        gradient: const LinearGradient(colors: [Color(0xFF1B4FD8), Color(0xFF3D72F2)]),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        const Text('Delivered', style: TextStyle(color: Colors.white70, fontSize: 15)),
                        Text('${qty(t['bags'] as String)} bags',
                            style: const TextStyle(color: Colors.white, fontSize: 34, fontWeight: FontWeight.w800)),
                        Text('${t['tonnes']} tonnes · ${inr(t['amount'] as String)}',
                            style: const TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w600)),
                        Gap.xs,
                        Text('${t['trucks']} trucks · ${t['orders']} orders · ${t['dealers']} dealers',
                            style: const TextStyle(color: Colors.white70, fontSize: 14)),
                        if (change(t['amount'] as String, prev['amount'] as String).isNotEmpty)
                          Text(change(t['amount'] as String, prev['amount'] as String),
                              style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w700)),
                      ]),
                    ),
                  ),
                  if (products.isEmpty)
                    const EmptyState(icon: Icons.inventory_2_outlined, title: 'Nothing delivered in this period'),
                  if (products.isNotEmpty) ...[
                    const SectionTitle('By material'),
                    Panel(child: Column(children: [for (final r in products) _ReportRow(r)])),
                  ],
                  if (dealers.isNotEmpty) ...[
                    const SectionTitle('By dealer'),
                    Panel(child: Column(children: [for (final r in dealers.take(30)) _ReportRow(r)])),
                  ],
                ]),
              );
            },
          ),
        ),
      ]),
    );
  }
}

class _ReportRow extends StatelessWidget {
  const _ReportRow(this.r);

  final Json r;

  @override
  Widget build(BuildContext context) {
    final bags = qty(r['bags'] as String);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
      title: Text(r['label'] as String, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
      subtitle: Text('${bags == '0' ? '' : '$bags bags · '}${r['tonnes']} t · ${r['trucks']} trucks'),
      trailing: Text(inr(r['amount'] as String), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
    );
  }
}

// ================================================================== dealer statement
class StatementScreen extends StatefulWidget {
  const StatementScreen({super.key, required this.dealerId, required this.name});

  final String dealerId;
  final String name;

  @override
  State<StatementScreen> createState() => _StatementScreenState();
}

class _StatementScreenState extends State<StatementScreen> {
  int _months = 3;
  Future<Json>? _future;

  Future<Json> _load() async {
    final now = DateTime.now();
    final from = DateTime(now.year, now.month - (_months - 1), 1);
    return await SessionScope.read(context).api.get('/dealers/${widget.dealerId}/statement',
        query: {'date_from': _ymd(from), 'date_to': _ymd(now)}) as Json;
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  static String drcr(String v) {
    final n = double.tryParse(v) ?? 0;
    if (n == 0) return '₹0';
    return '${inr(n.abs().toString())} ${n > 0 ? 'Dr' : 'Cr'}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: Text(widget.name.isEmpty ? 'Statement' : widget.name)),
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
          child: Wrap(spacing: 8, children: [
            for (final (m, l) in [(1, 'This month'), (3, '3 months'), (6, '6 months')])
              ChoiceChip(
                label: Text(l, style: TextStyle(fontSize: 15, color: _months == m ? Colors.white : AppColors.ink)),
                selected: _months == m,
                selectedColor: AppColors.blue,
                showCheckmark: false,
                onSelected: (_) => setState(() {
                  _months = m;
                  _future = _load();
                }),
              ),
          ]),
        ),
        Expanded(
          child: FutureBuilder<Json>(
            future: _future,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) return const SkeletonList(rows: 6);
              if (snap.hasError) {
                final e = snap.error;
                return EmptyState(icon: Icons.cloud_off_outlined, title: 'Could not read Tally',
                    message: e is ApiException ? e.detail : '$e');
              }
              final s = snap.data!;
              final rows = List<Json>.from(s['rows'] as List);
              final closing = double.tryParse(s['closing'] as String) ?? 0;
              return ListView(padding: const EdgeInsets.only(bottom: 24), children: [
                Panel(
                  margin: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                  padding: const EdgeInsets.all(16),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('Closing balance (Tally)', style: TextStyle(color: AppColors.steel, fontSize: 14)),
                    Text(drcr(s['closing'] as String),
                        style: TextStyle(fontSize: 30, fontWeight: FontWeight.w800,
                            color: closing > 0 ? AppColors.bad : AppColors.ok)),
                    Gap.xs,
                    Text('Opening ${drcr(s['opening'] as String)} · ${shortDate(s['from'] as String)} to ${shortDate(s['to'] as String)}',
                        style: const TextStyle(color: AppColors.steel)),
                    Text('Bills ${inr(s['total_debit'] as String)} · Payments ${inr(s['total_credit'] as String)}',
                        style: const TextStyle(color: AppColors.steel)),
                  ]),
                ),
                const SectionTitle('Entries'),
                if (rows.isEmpty) const EmptyState(icon: Icons.receipt_outlined, title: 'No entries in this period'),
                if (rows.isNotEmpty)
                  Panel(
                    child: Column(children: [
                      for (final (i, r) in rows.reversed.indexed) ...[
                        if (i > 0) const Divider(height: 1),
                        ListTile(
                          title: Text('${r['type']} ${r['number']}', style: const TextStyle(fontWeight: FontWeight.w700)),
                          subtitle: Text('${shortDate(r['date'] as String)}'
                              '${(r['reference'] as String).isNotEmpty ? ' · ${r['reference']}' : (r['narration'] as String).isNotEmpty ? ' · ${r['narration']}' : ''}\n'
                              'Balance ${drcr(r['balance'] as String)}'),
                          isThreeLine: true,
                          trailing: Text(
                            double.parse(r['debit'] as String) > 0 ? '+ ${inr(r['debit'] as String)}' : '− ${inr(r['credit'] as String)}',
                            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800,
                                color: double.parse(r['debit'] as String) > 0 ? AppColors.ink : AppColors.ok),
                          ),
                        ),
                      ],
                    ]),
                  ),
                const Padding(
                  padding: EdgeInsets.fromLTRB(20, 12, 20, 0),
                  child: Text('+ bill (dealer owes more) · − payment. To send it as a PDF, open the dealer on the web '
                      'dashboard → Statement → Print / Save as PDF.', style: TextStyle(color: AppColors.steel, fontSize: 13)),
                ),
              ]);
            },
          ),
        ),
      ]),
    );
  }
}

// ================================================================== order activity
/// Everything that happened to an order: booked, approved, trucks, Tally, WhatsApp.
class OrderActivity extends StatefulWidget {
  const OrderActivity({super.key, required this.orderId, required this.version});

  final String orderId;
  final String version; // reload when the order changes

  @override
  State<OrderActivity> createState() => _OrderActivityState();
}

class _OrderActivityState extends State<OrderActivity> {
  Future<List<Json>>? _future;
  String? _loadedFor;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reload();
  }

  @override
  void didUpdateWidget(OrderActivity old) {
    super.didUpdateWidget(old);
    _reload();
  }

  void _reload() {
    if (_loadedFor == widget.version) return;
    _loadedFor = widget.version;
    _future = () async {
      final r = await SessionScope.read(context).api.get('/orders/${widget.orderId}/history') as List;
      return List<Json>.from(r);
    }();
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<Json>>(
      future: _future,
      builder: (context, snap) {
        if (!snap.hasData) return const Padding(padding: EdgeInsets.all(20), child: LinearProgressIndicator(minHeight: 2));
        final items = snap.data!;
        const dot = {'order': AppColors.blue, 'truck': Color(0xFF0B7DD6), 'tally': AppColors.ok, 'whatsapp': Color(0xFF25D366)};
        return Panel(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
          child: Column(children: [
            for (final (i, h) in items.indexed)
              IntrinsicHeight(
                child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                  SizedBox(
                    width: 22,
                    child: Column(children: [
                      Container(
                        margin: const EdgeInsets.only(top: 4),
                        width: 12,
                        height: 12,
                        decoration: BoxDecoration(color: dot[h['kind']] ?? AppColors.steel, shape: BoxShape.circle),
                      ),
                      if (i < items.length - 1) Expanded(child: Container(width: 2, color: AppColors.seam)),
                    ]),
                  ),
                  Gap.s,
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(h['what'] as String, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                        Text([_when(h['at'] as String), if (h['who'] != null) h['who']].join(' · '),
                            style: const TextStyle(color: AppColors.steel, fontSize: 13)),
                        if (h['detail'] != null && (h['detail'] as String).isNotEmpty)
                          Text(h['detail'] as String, style: const TextStyle(color: AppColors.steel, fontSize: 13)),
                      ]),
                    ),
                  ),
                ]),
              ),
          ]),
        );
      },
    );
  }

  static String _when(String iso) {
    final t = DateTime.tryParse(iso)?.toLocal();
    if (t == null) return '';
    final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
    return '${shortDate(_ymd(t))}, $h:${t.minute.toString().padLeft(2, '0')} ${t.hour < 12 ? 'am' : 'pm'}';
  }
}

// ================================================================== orders waiting to send
class OutboxScreen extends StatelessWidget {
  const OutboxScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final box = Offline.instance;
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Waiting to send')),
      body: ListenableBuilder(
        listenable: box,
        builder: (context, _) {
          if (box.outbox.isEmpty) {
            return const EmptyState(icon: Icons.cloud_done_outlined, title: 'Everything is sent',
                message: 'Orders booked without network wait here and go by themselves when the network is back.');
          }
          return ListView(padding: const EdgeInsets.only(bottom: 24), children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
              child: FilledButton.icon(
                onPressed: box.sending
                    ? null
                    : () async {
                        final done = await box.flush();
                        if (!context.mounted) return;
                        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                            content: Text(done.isEmpty ? 'Still no network — will try again' : '${done.length} order(s) sent')));
                      },
                icon: const Icon(Icons.send),
                label: Text(box.sending ? 'Sending…' : 'Send now'),
              ),
            ),
            const SectionTitle('Orders on this phone'),
            Panel(
              child: Column(children: [
                for (final (i, item) in box.outbox.indexed) ...[
                  if (i > 0) const Divider(height: 1),
                  ListTile(
                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                    leading: Icon(item['error'] == null ? Icons.schedule : Icons.error_outline,
                        color: item['error'] == null ? AppColors.warn : AppColors.bad, size: 30),
                    title: Text(item['label'] as String, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
                    subtitle: Text(item['error'] == null
                        ? 'Booked ${_ago(item['saved_at'] as String)} · will send by itself'
                        : '${item['error']}'),
                    onTap: item['error'] == null ? null : () => _decide(context, item),
                  ),
                ],
              ]),
            ),
          ]);
        },
      ),
    );
  }

  Future<void> _decide(BuildContext context, Map<String, dynamic> item) async {
    final dup = item['code'] == 'DUPLICATE_ORDER';
    final choice = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(dup ? 'Same order already booked?' : 'Could not book'),
        content: Text('${item['error']}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, 'delete'), child: const Text('Delete it')),
          FilledButton(onPressed: () => Navigator.pop(ctx, 'send'), child: Text(dup ? 'Book it anyway' : 'Try again')),
        ],
      ),
    );
    final box = Offline.instance;
    if (choice == 'delete') await box.remove(item);
    if (choice == 'send') {
      item['error'] = null;
      final done = await box.flush(confirmDuplicateFor: dup, only: item);
      if (context.mounted && done.isNotEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${done.first['order_no']} booked')));
      }
    }
  }
}

// ================================================================== daily dealer-wise sales
/// Each dealer's deliveries for one day: product, quantity, rate, amount.
class DailySalesScreen extends StatefulWidget {
  const DailySalesScreen({super.key});

  @override
  State<DailySalesScreen> createState() => _DailySalesScreenState();
}

class _DailySalesScreenState extends State<DailySalesScreen> {
  DateTime _day = DateTime.now();
  Future<Json>? _future;

  Future<Json> _load() async =>
      await SessionScope.read(context).api.get('/reports/daily', query: {'day': _ymd(_day)}) as Json;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  void _go(DateTime d) => setState(() {
        _day = d;
        _future = _load();
      });

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final isToday = _day.year == now.year && _day.month == now.month && _day.day == now.day;
    const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Daily sales')),
      body: Column(children: [
        Container(
          color: AppColors.slab,
          padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
          child: Row(children: [
            IconButton(iconSize: 32, tooltip: 'Previous day', icon: const Icon(Icons.chevron_left),
                onPressed: () => _go(_day.subtract(const Duration(days: 1)))),
            Expanded(
              child: InkWell(
                onTap: () async {
                  final d = await showDatePicker(context: context, initialDate: _day,
                      firstDate: DateTime(now.year - 2), lastDate: now);
                  if (d != null) _go(d);
                },
                child: Column(children: [
                  Text(isToday ? 'Today' : days[_day.weekday - 1],
                      style: const TextStyle(color: AppColors.steel, fontSize: 14)),
                  Text(shortDate(_ymd(_day)), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                ]),
              ),
            ),
            IconButton(iconSize: 32, tooltip: 'Next day', icon: const Icon(Icons.chevron_right),
                onPressed: isToday ? null : () => _go(_day.add(const Duration(days: 1)))),
          ]),
        ),
        Expanded(
          child: FutureBuilder<Json>(
            future: _future,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) return const SkeletonList(rows: 5);
              if (snap.hasError) {
                return EmptyState(icon: Icons.cloud_off_outlined, title: 'Could not load', message: '${snap.error}');
              }
              final r = snap.data!;
              final t = r['total'] as Json;
              final dealers = List<Json>.from(r['dealers'] as List);
              return RefreshIndicator(
                onRefresh: () async => _go(_day),
                child: ListView(padding: const EdgeInsets.only(bottom: 24), children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                    child: Container(
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        gradient: const LinearGradient(colors: [Color(0xFF1B4FD8), Color(0xFF3D72F2)]),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text(inr(t['amount'] as String),
                            style: const TextStyle(color: Colors.white, fontSize: 30, fontWeight: FontWeight.w800)),
                        Text('${t['dealers']} dealers · ${qty(t['bags'] as String)} bags · ${t['tonnes']} t · ${t['trucks']} trucks',
                            style: const TextStyle(color: Colors.white, fontSize: 15)),
                      ]),
                    ),
                  ),
                  if (dealers.isEmpty)
                    const EmptyState(icon: Icons.inventory_2_outlined, title: 'Nothing delivered on this day'),
                  for (final d in dealers) ...[
                    SectionTitle(d['dealer'] as String,
                        trailing: Text(inr(d['amount'] as String),
                            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
                    Panel(
                      child: Column(children: [
                        for (final (i, l) in List<Json>.from(d['lines'] as List).indexed) ...[
                          if (i > 0) const Divider(height: 1),
                          ListTile(
                            title: Text(l['product'] as String,
                                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                            subtitle: Text(
                                '${qty(l['qty'] as String)} ${(l['unit'] as String).toLowerCase()} × '
                                '${inr(l['rate'] as String)}/${(l['rate_unit'] as String).toLowerCase()}',
                                style: const TextStyle(fontSize: 15)),
                            trailing: Text(inr(l['amount'] as String),
                                style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
                          ),
                        ],
                      ]),
                    ),
                  ],
                  const Padding(
                    padding: EdgeInsets.fromLTRB(20, 14, 20, 0),
                    child: Text('Excel and print: web dashboard → Daily sales.',
                        style: TextStyle(color: AppColors.steel, fontSize: 13)),
                  ),
                ]),
              );
            },
          ),
        ),
      ]),
    );
  }
}
