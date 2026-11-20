import 'dart:async';

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api.dart';
import '../../core/offline.dart';
import '../../core/session.dart';
import '../../core/ui.dart';
import '../dealers/new_dealer_screen.dart' show NewDealerScreen;
import '../dispatch/dispatch_screens.dart';
import '../extras/extras_screens.dart' show OrderActivity;

typedef Json = Map<String, dynamic>;

/// The app theme gives filled buttons full width (Size.fromHeight). Next to other widgets — in a row or a
/// dialog — that width is infinite and the whole screen fails to lay out, so these buttons size to content.
final compactButton = FilledButton.styleFrom(minimumSize: const Size(88, 48));

/// Order status → label and colour, matching the web app.
const orderStatus = <String, (String, Color)>{
  'DRAFT': ('Draft', AppColors.steel),
  'UNDER_REVIEW': ('Waiting approval', AppColors.warn),
  'ON_HOLD': ('On hold', AppColors.bad),
  'APPROVED': ('Approved', AppColors.ok),
  'PARTIALLY_DISPATCHED': ('Part dispatched', AppColors.ok),
  'DISPATCHED': ('Dispatched', AppColors.ok),
  'PARTIALLY_DELIVERED': ('Part delivered', AppColors.ok),
  'DELIVERED': ('Delivered', AppColors.ok),
  'CLOSED': ('Closed', AppColors.steel),
  'REJECTED': ('Rejected', AppColors.bad),
  'CANCELLED': ('Cancelled', AppColors.steel),
};

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

String shortDate(String? iso) {
  if (iso == null) return '—';
  final d = DateTime.tryParse(iso)?.toLocal();
  return d == null ? iso : '${d.day} ${_months[d.month - 1]}';
}

/// Orders not finished yet: waiting for approval, on hold, approved, or on their way.
const pendingStatuses = ['UNDER_REVIEW', 'ON_HOLD', 'APPROVED', 'PARTIALLY_DISPATCHED', 'DISPATCHED', 'PARTIALLY_DELIVERED'];

/// Segmented tabs for the dark header. Plain tap targets, so they work on every phone size.
class _Tabs extends StatelessWidget {
  const _Tabs({required this.labels, required this.selected, required this.onSelect});

  final List<String> labels;
  final int selected;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(color: AppColors.inkSoft, borderRadius: BorderRadius.circular(10)),
      child: Row(children: [
        for (final (i, label) in labels.indexed)
          Expanded(
            child: Semantics(
              button: true,
              selected: i == selected,
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () => onSelect(i),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 150),
                  height: 36,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: i == selected ? AppColors.hivis : Colors.transparent,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          color: i == selected ? AppColors.blue : Colors.white,
                          fontWeight: FontWeight.w700,
                          fontSize: 13.5)),
                ),
              ),
            ),
          ),
      ]),
    );
  }
}

Widget orderChip(Json o) {
  final (label, color) = orderStatus[o['status']] ?? (o['status'] as String, AppColors.steel);
  return StatusChip(label, color: color);
}

// ================================================================== list
class OrdersScreen extends StatefulWidget {
  const OrdersScreen({super.key});

  @override
  State<OrdersScreen> createState() => _OrdersScreenState();
}

class _OrdersScreenState extends State<OrdersScreen> {
  Future<List<Json>>? _future;
  String _filter = 'open';

  static const _filters = <String, (String, List<String>?)>{
    'open': ('Open', pendingStatuses),
    'review': ('Waiting', ['UNDER_REVIEW', 'ON_HOLD']),
    'done': ('Delivered', ['DELIVERED', 'CLOSED']),
    'all': ('All', null),
  };

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<List<Json>> _load() async {
    final status = _filters[_filter]!.$2;
    final page = await SessionScope.read(context).api.get('/orders', query: {
      'page_size': 100,
      if (status != null) 'status': status,
    }) as Json;
    return List<Json>.from(page['items'] as List);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    try {
      await f;
    } catch (_) {
      // shown by the list itself ("Could not load")
    }
  }

  Future<void> _newOrder() async {
    final created = await Navigator.of(context).push<bool>(MaterialPageRoute(builder: (_) => const NewOrderScreen()));
    if (created == true) _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final s = SessionScope.of(context);
    return Scaffold(
      backgroundColor: AppColors.concrete,
      floatingActionButton: s.can('orders.create')
          ? FloatingActionButton.extended(
              onPressed: _newOrder,
              backgroundColor: AppColors.blue,
              foregroundColor: Colors.white,
              icon: const Icon(Icons.add),
              label: const Text('Book Order', style: TextStyle(fontWeight: FontWeight.w800, fontSize: 17)),
            )
          : null,
      body: Column(children: [
        ScreenHeader(
          title: 'Orders',
          subtitle: s.can('dealers.view_all') ? 'All dealers' : 'Your dealers and orders you booked',
          bottom: _Tabs(
            labels: [for (final e in _filters.entries) e.value.$1],
            selected: _filters.keys.toList().indexOf(_filter),
            onSelect: (i) {
              if (_filters.keys.elementAt(i) == _filter) return;
              setState(() => _filter = _filters.keys.elementAt(i));
              _refresh();
            },
          ),
        ),
        Expanded(
          child: AsyncList<Json>(
            future: _future,
            onRefresh: _refresh,
            empty: EmptyState(
              icon: Icons.receipt_long_outlined,
              title: 'No orders here',
              message: s.can('orders.create') ? 'Tap "New order" to book one.' : null,
            ),
            header: (n) => Padding(
              padding: const EdgeInsets.fromLTRB(20, 14, 20, 6),
              child: Text('$n ${n == 1 ? 'order' : 'orders'} · ${_filters[_filter]!.$1}',
                  style: const TextStyle(color: AppColors.steel, fontWeight: FontWeight.w600, fontSize: 13)),
            ),
            itemBuilder: (context, o) => OrderCard(order: o, onChanged: _refresh),
          ),
        ),
      ]),
    );
  }
}

class OrderCard extends StatelessWidget {
  const OrderCard({super.key, required this.order, required this.onChanged});

  final Json order;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final o = order;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Material(
        color: AppColors.slab,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () async {
            await Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => OrderDetailScreen(orderId: o['id'] as String)));
            onChanged();
          },
          child: Container(
            padding: const EdgeInsets.all(14),
            decoration:
                BoxDecoration(borderRadius: BorderRadius.circular(14), border: Border.all(color: AppColors.seam)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(
                  child: Text(o['dealer_name'] as String,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
                ),
                Gap.s,
                Text(inr(o['total_amount'] as String?), style: const TextStyle(fontWeight: FontWeight.w800)),
              ]),
              Gap.xs,
              Text(o['summary'] as String,
                  maxLines: 2, overflow: TextOverflow.ellipsis, style: const TextStyle(color: AppColors.steel)),
              Gap.s,
              Row(children: [
                orderChip(o),
                if (o['priority'] == 'URGENT') ...[Gap.s, const StatusChip('Urgent', color: AppColors.bad)],
                const Spacer(),
                Text('${o['order_no'] ?? 'Draft'} · ${shortDate(o['order_date'] as String?)}',
                    style: const TextStyle(color: AppColors.mist, fontSize: 12.5)),
              ]),
            ]),
          ),
        ),
      ),
    );
  }
}

// ================================================================== detail
class OrderDetailScreen extends StatefulWidget {
  const OrderDetailScreen({super.key, required this.orderId});

  final String orderId;

  @override
  State<OrderDetailScreen> createState() => _OrderDetailScreenState();
}

class _OrderDetailScreenState extends State<OrderDetailScreen> {
  Future<Json>? _future;
  bool _busy = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<Json> _load() async =>
      await SessionScope.read(context).api.get('/orders/${widget.orderId}') as Json;

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  Future<String?> _askReason(String title, {required bool required}) {
    final c = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: c,
          autofocus: true,
          maxLines: 3,
          decoration: InputDecoration(hintText: required ? 'Reason' : 'Note (optional)'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Back')),
          FilledButton(
            style: compactButton,
            onPressed: () {
              if (required && c.text.trim().length < 3) return;
              Navigator.pop(ctx, c.text.trim());
            },
            child: Text(title),
          ),
        ],
      ),
    );
  }

  /// "Plan dispatch" when this user may plan and something is left to send; otherwise say why not.
  List<Widget> _dispatchSection(Json o, List<String> can, List<Json> items) {
    final s = SessionScope.of(context);
    if (!s.can('dispatch.plan')) return const [];
    final left = items.any((i) => (double.tryParse('${i['remaining_qty']}') ?? 0) > 0);
    String? why;
    switch (o['status']) {
      case 'DRAFT':
        why = 'This is a draft. Book it first.';
      case 'UNDER_REVIEW':
        why = 'Dispatch opens once the order is approved. An order cannot be approved by the person who booked it.';
      case 'ON_HOLD':
        why = 'On hold — dispatch opens once an admin approves it.';
      case 'REJECTED' || 'CANCELLED' || 'CLOSED' || 'DELIVERED':
        return const [];
    }
    if (why == null && !left) return const [];
    return [
      const SectionTitle('Dispatch'),
      if (why != null)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 20),
          child: Text(why, style: const TextStyle(color: AppColors.steel)),
        )
      else if (can.contains('plan_dispatch'))
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: FilledButton.icon(
            onPressed: () => _openDispatch(o),
            icon: const Icon(Icons.local_shipping_outlined),
            label: const Text('Dispatch'),
          ),
        ),
    ];
  }

  /// Pick the source and send; then straight on to the truck (send out → delivery) without coming back here.
  Future<void> _openDispatch(Json o) async {
    final nav = Navigator.of(context);
    final planned = await nav.push<Json>(MaterialPageRoute(builder: (_) => PlanDispatchScreen(order: o)));
    if (planned == null || !mounted) return;
    _refresh();
    final changed = await nav.push<bool>(MaterialPageRoute(
        builder: (_) => DeliveryOrderScreen(doId: planned['id'] as String, initial: planned)));
    if (changed == true && mounted) _refresh();
  }

  Future<void> _command(String action, Json order) async {
    final creditHold = order['status'] == 'ON_HOLD' && order['hold_kind'] != 'MANUAL';
    final title = action == 'approve' ? 'Approve' : 'Reject';
    var changes = <Json>[];
    if (action == 'approve' && SessionScope.read(context).can('orders.edit_on_approval')) {
      final c = await Navigator.of(context)
          .push<List<Json>>(MaterialPageRoute(builder: (_) => ApproveEditScreen(order: order)));
      if (c == null || !mounted) return;
      changes = c;
    }
    final reason = await _askReason(title, required: action == 'reject' || creditHold);
    if (reason == null || !mounted) return;
    setState(() => _busy = true);
    try {
      final api = SessionScope.read(context).api;
      final body = action == 'approve'
          ? {'reason': reason.isEmpty ? null : reason, 'override_credit': creditHold, 'lines': changes}
          : {'reason': reason};
      final updated = await api.post('/orders/${widget.orderId}/$action', body: body) as Json;
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(action == 'approve' ? 'Approved' : 'Rejected')));
      // Approved → the next thing is to send it, so open dispatch straight away.
      if (action == 'approve' && List<String>.from(updated['can'] as List).contains('plan_dispatch')) {
        setState(() => _busy = false);
        _refresh();
        await _openDispatch(updated);
        return;
      }
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    } finally {
      if (mounted) {
        setState(() => _busy = false);
        _refresh();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Order')),
      body: FutureBuilder<Json>(
        future: _future,
        builder: (context, snap) {
          final busy = snap.connectionState != ConnectionState.done;
          // Reloads keep the order on screen (no grey flash); only the first open shows the placeholder.
          if (busy && !snap.hasData) return const SkeletonList(rows: 4);
          if (!busy && snap.hasError) {
            return EmptyState(icon: Icons.cloud_off_outlined, title: 'Could not load', message: '${snap.error}');
          }
          final o = snap.data!;
          final can = List<String>.from(o['can'] as List);
          final reasons = List<Json>.from(o['review_reasons'] as List);
          final items = List<Json>.from(o['items'] as List);
          final dos = List<Json>.from(o['delivery_orders'] as List);
          final waiting = o['status'] == 'UNDER_REVIEW' || o['status'] == 'ON_HOLD';
          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView(padding: const EdgeInsets.only(bottom: 32), children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 16, 20, 4),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(o['dealer_name'] as String, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
                  Gap.xs,
                  Text('${o['order_no'] ?? 'Draft'} · ${shortDate(o['order_date'] as String?)}',
                      style: const TextStyle(color: AppColors.steel)),
                  Gap.s,
                  orderChip(o),
                ]),
              ),
              const SectionTitle('Deliver to'),
              Panel(
                child: Column(children: [
                  ListTile(
                    leading: const Icon(Icons.place_outlined),
                    title: Text((o['delivery_location'] as String?) ?? 'No address yet',
                        style: TextStyle(color: o['delivery_location'] == null ? AppColors.warn : AppColors.ink)),
                    subtitle: Text([
                      if (o['site_name'] != null) 'Site: ${o['site_name']}',
                      if (o['delivery_location_source'] == 'tally') 'Address from Tally',
                      if (o['delivery_location'] == null) 'Add one on the dealer page (web) or in the Tally ledger',
                    ].join(' · ')),
                    trailing: o['delivery_location'] == null
                        ? null
                        : IconButton(
                            tooltip: 'Open in Maps',
                            icon: const Icon(Icons.map_outlined),
                            onPressed: () => openMaps(o['delivery_location'] as String),
                          ),
                  ),
                  if (o['dealer_phone'] != null) ...[
                    const Divider(height: 1),
                    ListTile(
                      leading: const Icon(Icons.call_outlined),
                      title: Text('+91 ${o['dealer_phone']}'),
                      subtitle: const Text('Dealer mobile'),
                      onTap: () => launchUrl(Uri.parse('tel:+91${o['dealer_phone']}')),
                    ),
                  ],
                ]),
              ),
              if (waiting && reasons.isNotEmpty)
                Container(
                  margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: (o['status'] == 'ON_HOLD' ? AppColors.bad : AppColors.warn).withValues(alpha: 0.08),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(o['status'] == 'ON_HOLD' ? 'On hold — needs an admin' : 'Needs approval',
                        style: const TextStyle(fontWeight: FontWeight.w700)),
                    for (final r in reasons) Padding(padding: const EdgeInsets.only(top: 4), child: Text('• ${r['detail']}')),
                  ]),
                ),
              const SectionTitle('Material'),
              Panel(
                child: Column(children: [
                  for (final (i, it) in items.indexed) ...[
                    if (i > 0) const Divider(height: 1),
                    ListTile(
                      title: Text(it['product_name'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text(
                          '${qty(it['qty'] as String?)} ${it['uom']} × ₹${qty(it['rate'] as String?)}/${it['rate_uom']}'
                          '${it['godown_name'] != null ? ' · ${it['godown_name']}' : ''}\n'
                          'Sent ${qty(it['dispatched_qty'] as String?)} · delivered ${qty(it['delivered_qty'] as String?)}'),
                      isThreeLine: true,
                      trailing: Text(inr(it['amount'] as String?), style: const TextStyle(fontWeight: FontWeight.w700)),
                    ),
                  ],
                  const Divider(height: 1),
                  KeyValueRow('Total', inr(o['total_amount'] as String?)),
                ]),
              ),
              ..._dispatchSection(o, can, items),
              if (dos.isNotEmpty) ...[
                const SectionTitle('Trucks'),
                Panel(
                  child: Column(children: [
                    for (final (i, d) in dos.indexed) ...[
                      if (i > 0) const Divider(height: 1),
                      ListTile(
                        leading: const Icon(Icons.local_shipping_outlined),
                        title: Text(d['vehicle_no'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                        subtitle: Text('${d['do_no']} · ${shortDate(d['dispatch_date'] as String?)}'),
                        trailing: doChip(d['status'] as String),
                        onTap: () async {
                          final changed = await Navigator.of(context).push<bool>(
                              MaterialPageRoute(builder: (_) => DeliveryOrderScreen(doId: d['id'] as String)));
                          if (changed == true) _refresh();
                        },
                      ),
                    ],
                  ]),
                ),
              ],
              if (o['special_instructions'] != null) ...[
                const SectionTitle('Instructions'),
                Panel(padding: const EdgeInsets.all(16), child: Text(o['special_instructions'] as String)),
              ],
              const SectionTitle('Booked'),
              Panel(
                child: Column(children: [
                  KeyValueRow('By', (o['booked_by_name'] as String?) ?? '—'),
                  if (o['approved_by_name'] != null) KeyValueRow('Approved by', o['approved_by_name'] as String),
                  if (o['tally_status'] != null)
                    KeyValueRow('Tally', const {
                          'PENDING': 'Sending to Tally',
                          'WAITING_APPROVAL': 'Waiting for admin approval in Tally',
                          'APPROVED': 'Approved in Tally',
                          'REJECTED': 'Rejected in Tally',
                          'FAILED': 'Not sent to Tally yet',
                        }[o['tally_status']] ??
                        '${o['tally_status']}'),
                ]),
              ),
              if ((can.contains('approve') || can.contains('reject')) && o['credit_now'] != null) ...[
                const SectionTitle('Dealer balance'),
                DealerBalanceCard(c: o['credit_now'] as Json),
              ],
              if (can.contains('approve') || can.contains('reject'))
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
                  child: Row(children: [
                    if (can.contains('reject'))
                      Expanded(
                        child: OutlinedButton(
                          onPressed: _busy ? null : () => _command('reject', o),
                          child: const Text('Reject'),
                        ),
                      ),
                    if (can.contains('approve')) ...[
                      Gap.m,
                      Expanded(
                        child: FilledButton(
                          onPressed: _busy ? null : () => _command('approve', o),
                          child: const Text('Approve'),
                        ),
                      ),
                    ],
                  ]),
                ),
              const SectionTitle('Activity'),
              OrderActivity(
                orderId: widget.orderId,
                version: '${o['status']}-${dos.map((d) => d['status']).join()}-${o['tally_status'] ?? ''}',
              ),
            ]),
          );
        },
      ),
    );
  }
}

// ================================================================== new order
class _Line {
  _Line(this.product, this.uom);

  final Json product;
  String uom;
  final qtyCtl = TextEditingController();
  final rateCtl = TextEditingController();

  List<String> get units {
    final u = List<Json>.from(product['units'] as List? ?? const [])
        .where((x) => x['is_sellable'] != false)
        .map((x) => x['uom'] as String)
        .toList();
    return u.isEmpty ? [product['base_uom'] as String] : u;
  }
}

class NewOrderScreen extends StatefulWidget {
  const NewOrderScreen({super.key, this.dealer, this.draftId, this.message});

  /// Book for this dealer straight away (from the dealer's page).
  final Json? dealer;

  /// Check and book a draft (e.g. one made from a WhatsApp message).
  final String? draftId;

  /// Finish an order from a WhatsApp message the reader was not sure about (what it understood is filled in).
  final Json? message;

  @override
  State<NewOrderScreen> createState() => _NewOrderScreenState();
}

class _NewOrderScreenState extends State<NewOrderScreen> {
  Json? _dealer;
  bool _urgent = false;
  final _site = TextEditingController();
  final _notes = TextEditingController();
  final List<_Line> _lines = [];
  List<Json> _products = [];
  Json? _preview;
  String? _previewError;
  Timer? _debounce;
  bool _saving = false;
  bool _offline = false; // the server could not be reached while booking
  Json? _last; // the dealer's previous order, for "Same as last time"
  // One reference for this booking: if it is sent twice (weak network), the server makes one order. For a WhatsApp
  // message it is the message's own reference, so the order is linked back to it.
  late final String _clientRef = (widget.message?['client_ref'] as String?) ?? Offline.newClientRef();
  Json? _draft; // the draft being checked
  bool get _fromWhatsApp => widget.message != null || _draft?['source'] == 'WHATSAPP';

  @override
  void initState() {
    super.initState();
    if (widget.dealer != null) {
      _dealer = widget.dealer;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _dealerChosen();
      });
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_products.isEmpty) _loadMasters();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<void> _loadMasters() async {
    final api = SessionScope.read(context).api;
    try {
      // The godown is chosen by dispatch when the truck is planned, not when booking.
      final r = await api.get('/products', query: {'page_size': 200, 'erp_enabled': 'true'}) as Json;
      if (!mounted) return;
      setState(() => _products = List<Json>.from(r['items'] as List));
      unawaited(Offline.writeCache('products', r['items'] as List));
      await _fillFromDraftOrMessage();
    } on ApiException catch (e) {
      // weak network: use the copy kept on the phone
      final cached = Offline.isNetwork(e) ? await Offline.readCache('products') as List? : null;
      if (!mounted) return;
      if (cached != null) {
        setState(() {
          _products = List<Json>.from(cached);
          _offline = true;
        });
      } else {
        showError(context, e);
      }
    }
  }

  /// Fill the form from the draft being checked, or from what was read in a WhatsApp message.
  Future<void> _fillFromDraftOrMessage() async {
    final api = SessionScope.read(context).api;
    final byId = {for (final p in _products) p['id']: p};
    void addLine(String? productId, Object? q, String? unit, Object? rate) {
      final p = byId[productId];
      if (p == null) return;
      final line = _Line(p, unit ?? '');
      if (!line.units.contains(line.uom)) line.uom = line.units.first;
      final n = double.tryParse('$q') ?? 0;
      line.qtyCtl.text = n > 0 ? qty('$q') : '';
      if (rate != null && '$rate'.isNotEmpty) line.rateCtl.text = qty('$rate');
      _lines.add(line);
    }

    try {
      if (widget.draftId != null) {
        final o = await api.get('/orders/${widget.draftId}') as Json;
        _draft = o;
        _dealer = {'id': o['dealer_id'], 'name': o['dealer_name'], 'code': ''};
        _site.text = (o['site_name'] as String?) ?? '';
        _notes.text = (o['special_instructions'] as String?) ?? '';
        _urgent = o['priority'] == 'URGENT';
        _lines.clear();
        for (final it in List<Json>.from(o['items'] as List)) {
          addLine(it['product_id'] as String?, it['qty'], it['uom'] as String?,
              it['rate_source'] == 'MANUAL' ? it['rate'] : null);
        }
      } else if (widget.message != null) {
        final m = widget.message!;
        final r = (m['reading'] as Json?) ?? const {};
        if (r['dealer_id'] != null) {
          final d = await api.get('/dealers/${r['dealer_id']}') as Json;
          _dealer = {'id': d['id'], 'name': d['name'], 'code': d['code']};
        }
        _site.text = (r['site'] as String?) ?? '';
        _urgent = r['urgent'] == true;
        _notes.text = [
          if (r['notes'] != null) r['notes'],
          'From WhatsApp${m['from_name'] != null ? ' (${m['from_name']})' : ''}: "${(m['text'] as String).length > 300 ? (m['text'] as String).substring(0, 300) : m['text']}"',
        ].join('\n');
        for (final l in List<Json>.from((r['lines'] as List?) ?? const [])) {
          addLine(l['product_id'] as String?, l['qty'], l['unit'] as String?, l['rate']);
        }
      } else {
        return;
      }
    } on ApiException catch (e) {
      if (mounted) showError(context, e);
    }
    if (mounted) _changed();
  }

  Json _body({bool submit = true, bool confirm = false}) => {
        'dealer_id': _dealer!['id'],
        'site_name': _site.text.trim().isEmpty ? null : _site.text.trim(),
        'priority': _urgent ? 'URGENT' : 'NORMAL',
        'source': _fromWhatsApp ? 'WHATSAPP' : 'SALES_EXEC',
        'special_instructions': _notes.text.trim().isEmpty ? null : _notes.text.trim(),
        'lines': [
          for (final l in _lines.where((l) => (double.tryParse(l.qtyCtl.text) ?? 0) > 0))
            {
              'product_id': l.product['id'],
              'qty': l.qtyCtl.text.trim(),
              'uom': l.uom,
              'rate': l.rateCtl.text.trim().isEmpty ? null : l.rateCtl.text.trim(),
            },
        ],
        'submit': submit,
        'confirm_duplicate': confirm,
        'client_ref': _clientRef,
      };

  bool get _ready => _dealer != null && (_body()['lines'] as List).isNotEmpty;

  void _changed() {
    _debounce?.cancel();
    setState(() {});
    if (!_ready) {
      setState(() => _preview = null);
      return;
    }
    _debounce = Timer(const Duration(milliseconds: 500), () async {
      if (!mounted) return;
      final body = _body()..remove('submit')..remove('confirm_duplicate')..remove('client_ref');
      try {
        final p = await SessionScope.read(context).api.post('/orders/preview', body: body) as Json;
        if (mounted) {
          setState(() {
            _preview = p;
            _previewError = null;
          });
        }
      } on ApiException catch (e) {
        if (mounted) {
          setState(() {
            _preview = null;
            // No network: book anyway; the price and credit are checked when it reaches the server.
            _offline = Offline.isNetwork(e);
            _previewError = _offline ? null : e.detail;
          });
        }
      }
    });
  }

  Future<void> _dealerChosen() async {
    _last = null;
    _changed();
    try {
      final r = await SessionScope.read(context).api.get('/orders/last', query: {'dealer_id': _dealer!['id']});
      if (mounted) setState(() => _last = r as Json?);
    } catch (_) {
      // no previous order / no network: nothing to offer
    }
  }

  /// "Same as last time": the previous order's material, quantity and unit, at today's dealer rate.
  void _repeat() {
    final last = _last;
    if (last == null) return;
    final byId = {for (final p in _products) p['id']: p};
    var missing = 0;
    _lines.clear();
    for (final l in List<Json>.from(last['lines'] as List)) {
      final p = byId[l['product_id']];
      if (p == null) {
        missing++;
        continue;
      }
      final line = _Line(p, l['uom'] as String);
      if (!line.units.contains(line.uom)) line.uom = line.units.first;
      line.qtyCtl.text = qty(l['qty'].toString());
      _lines.add(line);
    }
    if (last['site_name'] != null && _site.text.isEmpty) _site.text = last['site_name'] as String;
    _changed();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(missing > 0
            ? 'Filled from the last order ($missing item(s) no longer sold). Check the quantity, then book.'
            : 'Filled from the last order. Check the quantity, then book.')));
  }

  String _label() {
    final parts = [
      for (final l in _lines.where((l) => (double.tryParse(l.qtyCtl.text) ?? 0) > 0))
        '${l.qtyCtl.text.trim()} ${l.uom.toLowerCase()} ${l.product['name']}',
    ];
    return '${_dealer!['name']} · ${parts.join(', ')}';
  }

  Future<void> _pickDealer() async {
    final d = await Navigator.of(context).push<Json>(MaterialPageRoute(builder: (_) => const _DealerPicker()));
    if (d != null) {
      _dealer = d;
      _dealerChosen();
    }
  }

  Future<void> _addLine() async {
    final p = await showModalBottomSheet<Json>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _ProductPicker(products: _products),
    );
    if (p == null) return;
    final line = _Line(p, '');
    final units = List<Json>.from(p['units'] as List? ?? const []);
    final def = units.where((u) => u['is_default_entry'] == true).map((u) => u['uom'] as String);
    line.uom = def.isNotEmpty ? def.first : line.units.first;
    setState(() => _lines.add(line));
  }

  Future<void> _book({bool confirm = false}) async {
    setState(() => _saving = true);
    try {
      final api = SessionScope.read(context).api;
      final o = _draft != null
          ? await api.put('/orders/${_draft!['id']}',
              body: _body(confirm: confirm)..remove('client_ref')..['version'] = _draft!['version']) as Json
          : await api.post('/orders', body: _body(confirm: confirm)) as Json;
      if (!mounted) return;
      final status = orderStatus[o['status']]?.$1 ?? o['status'];
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${o['order_no']} booked · $status')));
      Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (!mounted) return;
      if (Offline.isNetwork(e)) {
        // No network: keep it on the phone; it goes to the server by itself when the network is back.
        await Offline.instance.keep(_body(confirm: confirm), _label());
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            duration: Duration(seconds: 6),
            content: Text('No network. The order is saved on the phone and will be sent by itself.')));
        Navigator.of(context).pop(true);
        return;
      }
      if (e.code == 'DUPLICATE_ORDER') {
        final again = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('Same order again?'),
            content: Text(e.detail),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('No')),
              FilledButton(
                style: compactButton,
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('Book again'),
              ),
            ],
          ),
        );
        if (again == true) return _book(confirm: true);
      } else {
        showError(context, e);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final priced = List<Json>.from((_preview?['lines'] as List?) ?? const []);
    final credit = _preview?['credit'] as Json?;
    final activeLines = _lines.where((l) => (double.tryParse(l.qtyCtl.text) ?? 0) > 0).toList();
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: Text(_fromWhatsApp ? 'Check WhatsApp order' : widget.draftId != null ? 'Draft order' : 'New order')),
      bottomNavigationBar: SafeArea(
        child: Container(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
          decoration: const BoxDecoration(color: AppColors.slab, border: Border(top: BorderSide(color: AppColors.seam))),
          child: Row(children: [
            Expanded(
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
                const Text('Total', style: TextStyle(color: AppColors.steel, fontSize: 12.5)),
                Text(_preview == null ? '—' : inr(_preview!['total_amount'] as String?),
                    style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
              ]),
            ),
            FilledButton(
              style: compactButton,
              onPressed: !_ready || _saving || (_previewError != null && !_offline) ? null : _book,
              child: Text(_saving ? 'Booking…' : 'Book order'),
            ),
          ]),
        ),
      ),
      body: ListView(padding: const EdgeInsets.only(bottom: 24), children: [
        const SectionTitle('Dealer'),
        Panel(
          child: ListTile(
            onTap: _pickDealer,
            leading: _dealer == null ? const Icon(Icons.storefront_outlined) : Monogram(_dealer!['name'] as String, size: 38),
            title: Text(_dealer == null ? 'Choose dealer' : _dealer!['name'] as String,
                style: const TextStyle(fontWeight: FontWeight.w700)),
            subtitle: _dealer == null || ((_dealer!['code'] as String?) ?? '').isEmpty
                ? null
                : Text(_dealer!['code'] as String),
            trailing: const Icon(Icons.chevron_right),
          ),
        ),
        if (_offline)
          Container(
            margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(color: const Color(0xFFFFF4DE), borderRadius: BorderRadius.circular(12)),
            child: const Row(children: [
              Icon(Icons.cloud_off, color: AppColors.warn),
              SizedBox(width: 10),
              Expanded(
                child: Text('Weak or no network. You can still book: the order is saved on the phone and sent '
                    'by itself. Price and credit are checked when it reaches the office.',
                    style: TextStyle(fontWeight: FontWeight.w600)),
              ),
            ]),
          ),
        if (widget.message != null)
          Container(
            margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(color: const Color(0xFFDCF8C6), borderRadius: BorderRadius.circular(12)),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text((widget.message!['from_name'] as String?) ?? 'Dealer\'s message',
                  style: const TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF075E54))),
              Gap.xs,
              Text(widget.message!['text'] as String, style: const TextStyle(fontSize: 16)),
              if (widget.message!['note'] != null) ...[
                Gap.s,
                Text('${widget.message!['note']}', style: const TextStyle(color: AppColors.warn, fontWeight: FontWeight.w600)),
              ],
            ]),
          ),
        if (_last != null && !_fromWhatsApp && widget.draftId == null)
          Container(
            margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
            decoration: BoxDecoration(
              color: AppColors.blueTint,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: AppColors.blue.withValues(alpha: 0.35), width: 1.5),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Last order · ${shortDate(_last!['order_date'] as String?)}',
                  style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
              Gap.xs,
              Text(
                [
                  for (final l in List<Json>.from(_last!['lines'] as List))
                    '${qty(l['qty'].toString())} ${(l['uom'] as String).toLowerCase()} ${l['product_name']}',
                ].join(', '),
                style: const TextStyle(color: AppColors.steel, fontSize: 15),
              ),
              Gap.m,
              SizedBox(
                width: double.infinity,
                height: 52,
                child: FilledButton.icon(
                  onPressed: _products.isEmpty ? null : _repeat,
                  icon: const Icon(Icons.replay),
                  label: const Text('Same as last time', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
                ),
              ),
            ]),
          ),
        const SectionTitle('Material'),
        if (_lines.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 20),
            child: Text('Add cement, TMT or other material.', style: TextStyle(color: AppColors.steel)),
          ),
        for (final (i, l) in _lines.indexed)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Panel(
              padding: const EdgeInsets.fromLTRB(16, 8, 8, 12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [
                  Expanded(
                    child: Text(l.product['name'] as String,
                        style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                  ),
                  IconButton(
                    tooltip: 'Remove',
                    icon: const Icon(Icons.close, size: 20),
                    onPressed: () {
                      _lines.removeAt(i);
                      _changed();
                    },
                  ),
                ]),
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: Row(children: [
                    Expanded(
                      flex: 3,
                      child: TextField(
                        controller: l.qtyCtl,
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: const InputDecoration(labelText: 'Quantity'),
                        onChanged: (_) => _changed(),
                      ),
                    ),
                    Gap.s,
                    Expanded(
                      flex: 2,
                      child: DropdownButtonFormField<String>(
                        isExpanded: true,
                        value: l.uom,
                        decoration: const InputDecoration(labelText: 'Unit'),
                        items: [for (final u in l.units) DropdownMenuItem(value: u, child: Text(u))],
                        onChanged: (v) {
                          if (v == null) return;
                          l.uom = v;
                          _changed();
                        },
                      ),
                    ),
                    Gap.s,
                    Expanded(
                      flex: 3,
                      child: TextField(
                        controller: l.rateCtl,
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: const InputDecoration(labelText: 'Rate', hintText: 'Dealer rate'),
                        onChanged: (_) => _changed(),
                      ),
                    ),
                  ]),
                ),
                Builder(builder: (_) {
                  final idx = activeLines.indexOf(l);
                  if (idx < 0 || idx >= priced.length) return const SizedBox.shrink();
                  final p = priced[idx];
                  final flags = List<String>.from(p['flags'] as List);
                  return Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      '${qty(p['rate_qty'] as String?)} ${p['rate_uom']} × ₹${qty(p['rate'] as String?)} = ${inr(p['amount'] as String?)}'
                      '${flags.contains('RATE_BELOW_MIN') ? '\nBelow the allowed rate — needs admin approval' : ''}'
                      '${flags.contains('NO_DEALER_RATE') ? '\nNo dealer rate on file — goes for approval' : ''}',
                      style: TextStyle(
                          color: flags.isEmpty ? AppColors.steel : AppColors.warn, fontSize: 13, height: 1.35),
                    ),
                  );
                }),
              ]),
            ),
          ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: OutlinedButton.icon(
            onPressed: _products.isEmpty ? null : _addLine,
            icon: const Icon(Icons.add),
            label: const Text('Add material'),
          ),
        ),
        if (_previewError != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 0),
            child: Text(_previewError!, style: const TextStyle(color: AppColors.bad)),
          ),
        const SectionTitle('Delivery'),
        Panel(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
          child: Column(children: [
            TextField(controller: _site, decoration: const InputDecoration(labelText: 'Site / customer name (optional)')),
            TextField(
              controller: _notes,
              maxLines: 2,
              decoration: const InputDecoration(labelText: 'Instructions (optional)'),
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Urgent'),
              value: _urgent,
              onChanged: (v) => setState(() => _urgent = v),
            ),
          ]),
        ),
        if (credit != null) ...[
          SectionTitle('Credit',
              trailing: StatusChip(
                credit['result'] == 'PASS' ? 'OK' : credit['result'] == 'WARN' ? 'Check' : 'Will go on hold',
                color: credit['result'] == 'PASS'
                    ? AppColors.ok
                    : credit['result'] == 'WARN'
                        ? AppColors.warn
                        : AppColors.bad,
              )),
          Panel(
            child: Column(children: [
              KeyValueRow('Credit limit', credit['credit_limit'] == null ? 'Not set' : inr(credit['credit_limit'] as String?)),
              KeyValueRow('Tally balance', inr(credit['tally_balance'] as String?)),
              if (credit['available_credit'] != null) KeyValueRow('Available', inr(credit['available_credit'] as String?)),
            ]),
          ),
        ],
      ]),
    );
  }
}

class _DealerPicker extends StatefulWidget {
  const _DealerPicker();

  @override
  State<_DealerPicker> createState() => _DealerPickerState();
}

class _DealerPickerState extends State<_DealerPicker> {
  String _q = '';
  Timer? _debounce;

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<(List<Json>, int)> _fetch(int page) async {
    try {
      final r = await SessionScope.read(context)
          .api
          .get('/dealers', query: {'page': page, 'page_size': 50, 'q': _q}) as Json;
      return (List<Json>.from(r['items'] as List), r['total'] as int);
    } on ApiException catch (e) {
      // Weak network: search the copy of the dealers kept on the phone.
      final cached = Offline.isNetwork(e) ? await Offline.readCache('dealers') as List? : null;
      if (cached == null) rethrow;
      final q = _q.toLowerCase();
      final all = List<Json>.from(cached)
          .where((d) => q.isEmpty || '${d['name']} ${d['code']}'.toLowerCase().contains(q))
          .toList();
      final start = (page - 1) * 50;
      return (all.skip(start).take(50).toList(), all.length);
    }
  }

  @override
  Widget build(BuildContext context) {
    final all = SessionScope.of(context).can('dealers.view_all');
    final canAdd = SessionScope.of(context).can('dealers.create');
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('Choose dealer')),
      floatingActionButton: canAdd
          ? FloatingActionButton.extended(
              icon: const Icon(Icons.person_add_alt_1_outlined),
              label: const Text('New dealer'),
              onPressed: () async {
                final d = await Navigator.of(context)
                    .push<Json>(MaterialPageRoute(builder: (_) => NewDealerScreen(startName: _q)));
                if (d != null && context.mounted) Navigator.of(context).pop(d);
              },
            )
          : null,
      body: Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: TextField(
            autofocus: true,
            textInputAction: TextInputAction.search,
            decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Name, part of name or code'),
            onChanged: (v) {
              _debounce?.cancel();
              _debounce = Timer(const Duration(milliseconds: 350), () {
                if (mounted) setState(() => _q = v.trim());
              });
            },
          ),
        ),
        Expanded(
          child: PagedList<Json>(
            query: _q,
            fetch: _fetch,
            header: (total) => Padding(
              padding: const EdgeInsets.fromLTRB(20, 4, 20, 6),
              child: Text(
                  _q.isEmpty ? '$total ${all ? 'dealers' : 'dealers assigned to you'}' : '$total found',
                  style: const TextStyle(color: AppColors.steel, fontWeight: FontWeight.w600, fontSize: 13)),
            ),
            empty: EmptyState(
              icon: Icons.storefront_outlined,
              title: _q.isEmpty ? 'No dealers' : 'No dealer matches "$_q"',
              message: all ? 'Try part of the name.' : 'You can only book for dealers assigned to you.',
            ),
            itemBuilder: (context, d) {
              final active = d['status'] == 'ACTIVE';
              return ListTile(
                enabled: active,
                leading: Monogram(d['name'] as String, size: 38),
                title: Text(d['name'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                subtitle: Text(active
                    ? d['code'] as String
                    : '${(d['status'] as String).toLowerCase()}${d['blocked_reason'] != null ? ' · ${d['blocked_reason']}' : ''} — cannot book'),
                onTap: active ? () => Navigator.of(context).pop(d) : null,
              );
            },
          ),
        ),
      ]),
    );
  }
}

class _ProductPicker extends StatefulWidget {
  const _ProductPicker({required this.products});

  final List<Json> products;

  @override
  State<_ProductPicker> createState() => _ProductPickerState();
}

class _ProductPickerState extends State<_ProductPicker> {
  String _q = '';

  @override
  Widget build(BuildContext context) {
    final q = _q.toLowerCase();
    final list = widget.products.where((p) => (p['name'] as String).toLowerCase().contains(q)).toList();
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.85,
      builder: (context, scroll) => Column(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
          child: TextField(
            decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Search material'),
            onChanged: (v) => setState(() => _q = v.trim()),
          ),
        ),
        Expanded(
          child: ListView.builder(
            controller: scroll,
            itemCount: list.length,
            itemBuilder: (context, i) {
              final p = list[i];
              return ListTile(
                title: Text(p['name'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                subtitle: Text('${p['category']} · sold in ${p['base_uom']}'),
                onTap: () => Navigator.of(context).pop(p),
              );
            },
          ),
        ),
      ]),
    );
  }
}


/// The dealer's money position in large type, for whoever approves the order.
class DealerBalanceCard extends StatelessWidget {
  const DealerBalanceCard({super.key, required this.c});

  final Json c;

  double _n(Object? v) => double.tryParse('${v ?? ''}') ?? 0;

  @override
  Widget build(BuildContext context) {
    final bal = c['tally_balance'] == null ? null : _n(c['tally_balance']);
    final limit = c['credit_limit'] == null ? null : _n(c['credit_limit']);
    final after = _n(c['total_after']);
    final over = limit != null && after > limit;
    final stale = c['is_stale'] == true;
    final asOf = DateTime.tryParse('${c['balance_as_of'] ?? ''}')?.toLocal();
    Widget row(String k, String v, {bool bold = false}) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 3),
          child: Row(children: [
            Expanded(child: Text(k, style: TextStyle(fontSize: 16, color: bold ? AppColors.ink : AppColors.steel,
                fontWeight: bold ? FontWeight.w800 : FontWeight.w500))),
            Text(v, style: TextStyle(fontSize: 16, fontWeight: bold ? FontWeight.w800 : FontWeight.w600)),
          ]),
        );
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: over ? AppColors.bad.withValues(alpha: 0.06) : AppColors.slab,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: over ? AppColors.bad : AppColors.seam, width: 2),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const Text('Dealer owes now (Tally)', style: TextStyle(fontSize: 15, color: AppColors.steel, fontWeight: FontWeight.w600)),
        Text(
          bal == null ? 'Not known' : bal < 0 ? '${inr((-bal).toString())} advance' : inr(bal.toString()),
          style: TextStyle(fontSize: 34, fontWeight: FontWeight.w800,
              color: bal == null ? AppColors.steel : bal > 0 ? AppColors.bad : AppColors.ok),
        ),
        Text(
          asOf == null
              ? 'Never synced from Tally'
              : 'As per Tally, ${asOf.day}/${asOf.month} ${asOf.hour.toString().padLeft(2, '0')}:${asOf.minute.toString().padLeft(2, '0')}'
                  '${stale ? ' — old, sync Tally first' : ''}',
          style: TextStyle(fontSize: 14, color: stale ? AppColors.warn : AppColors.steel,
              fontWeight: stale ? FontWeight.w700 : FontWeight.w400),
        ),
        const Divider(height: 24),
        row('Other orders not yet sent', inr('${c['open_order_exposure']}')),
        row('This order', inr('${c['this_order_amount']}')),
        row('Total after this order', inr(after.toString()), bold: true),
        row('Credit limit', limit == null ? 'Not set' : inr(limit.toString())),
        if (over)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text('Over the limit by ${inr((after - limit).toString())}',
                style: const TextStyle(fontSize: 16, color: AppColors.bad, fontWeight: FontWeight.w800)),
          ),
      ]),
    );
  }
}

/// Orders in one stage (from a home-screen tile), newest first.
class OrderListScreen extends StatefulWidget {
  const OrderListScreen({super.key, required this.title, required this.statuses, this.days});

  final String title;
  final List<String> statuses;
  final int? days;

  @override
  State<OrderListScreen> createState() => _OrderListScreenState();
}

class _OrderListScreenState extends State<OrderListScreen> {
  Future<List<Json>>? _future;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<List<Json>> _load() async {
    final from = widget.days == null ? null : DateTime.now().subtract(Duration(days: widget.days!));
    final page = await SessionScope.read(context).api.get('/orders', query: {
      'page_size': 100,
      'status': widget.statuses,
      if (from != null)
        'date_from': '${from.year}-${from.month.toString().padLeft(2, '0')}-${from.day.toString().padLeft(2, '0')}',
    }) as Json;
    return List<Json>.from(page['items'] as List);
  }

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
      appBar: AppBar(title: Text(widget.title)),
      body: AsyncList<Json>(
        future: _future,
        onRefresh: _refresh,
        empty: const EmptyState(icon: Icons.receipt_long_outlined, title: 'No orders here'),
        itemBuilder: (context, o) => OrderCard(order: o, onChanged: _refresh),
      ),
    );
  }
}


/// Super admin, before approving: change any line's quantity or rate. Pops with the changed lines (empty when
/// nothing changed), or null to go back without approving.
class ApproveEditScreen extends StatefulWidget {
  const ApproveEditScreen({super.key, required this.order});

  final Json order;

  @override
  State<ApproveEditScreen> createState() => _ApproveEditScreenState();
}

class _ApproveEditScreenState extends State<ApproveEditScreen> {
  late final List<Json> _items = List<Json>.from(widget.order['items'] as List);
  late final Map<int, TextEditingController> _qty = {
    for (final i in _items) i['line_no'] as int: TextEditingController(text: _plain(i['qty'])),
  };
  late final Map<int, TextEditingController> _rate = {
    for (final i in _items) i['line_no'] as int: TextEditingController(text: _plain(i['rate'])),
  };

  static String _plain(Object? v) {
    final d = double.tryParse('$v') ?? 0;
    return d == d.roundToDouble() ? d.toStringAsFixed(0) : d.toString();
  }

  static double _d(Object? v) => double.tryParse('${v ?? ''}'.trim()) ?? 0;

  double _amount(Json i) {
    final n = i['line_no'] as int;
    final q0 = _d(i['qty']), r0 = _d(i['rate']);
    if (q0 == 0 || r0 == 0) return _d(i['amount']);
    return _d(i['amount']) * (_d(_qty[n]!.text) / q0) * (_d(_rate[n]!.text) / r0);
  }

  List<Json> get _changes => [
        for (final i in _items)
          if ((_d(_qty[i['line_no']]!.text) - _d(i['qty'])).abs() > 0.0005 ||
              (_d(_rate[i['line_no']]!.text) - _d(i['rate'])).abs() > 0.00005)
            {'line_no': i['line_no'], 'qty': _qty[i['line_no']]!.text.trim(), 'rate': _rate[i['line_no']]!.text.trim()},
      ];

  bool get _valid => _items.every((i) => _d(_qty[i['line_no']]!.text) > 0 && _d(_rate[i['line_no']]!.text) > 0);

  @override
  Widget build(BuildContext context) {
    final total = _items.fold<double>(0, (t, i) => t + _amount(i));
    final changed = _changes.isNotEmpty;
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: Text('Approve ${widget.order['order_no'] ?? ''}')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Text('${widget.order['dealer_name']}', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
        Gap.xs,
        const Text('Change quantity or rate if needed, then approve.', style: TextStyle(color: AppColors.steel)),
        Gap.m,
        for (final i in _items)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('${i['product_name']}', style: const TextStyle(fontWeight: FontWeight.w600)),
                Gap.s,
                Row(children: [
                  Expanded(
                    child: TextField(
                      controller: _qty[i['line_no']],
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: InputDecoration(labelText: 'Quantity', suffixText: '${i['uom']}'.toLowerCase()),
                      onChanged: (_) => setState(() {}),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: TextField(
                      controller: _rate[i['line_no']],
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: InputDecoration(
                          labelText: 'Rate ₹', suffixText: '/${'${i['rate_uom']}'.toLowerCase()}'),
                      onChanged: (_) => setState(() {}),
                    ),
                  ),
                ]),
                Gap.s,
                Text('Amount ₹${_amount(i).toStringAsFixed(2)}', style: const TextStyle(color: AppColors.steel)),
              ]),
            ),
          ),
        Gap.m,
        Text('Total ₹${total.toStringAsFixed(2)}${changed ? ' (was ₹${widget.order['total_amount']})' : ''}',
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
        Gap.l,
        FilledButton(
          onPressed: _valid ? () => Navigator.of(context).pop(_changes) : null,
          child: Text(changed ? 'Save changes and approve' : 'Approve without changes'),
        ),
      ]),
    );
  }
}
