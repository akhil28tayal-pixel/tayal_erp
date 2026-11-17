import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api.dart';
import '../../core/session.dart';
import '../../core/ui.dart';
import '../extras/extras_screens.dart' show StatementScreen;
import '../orders/orders_screen.dart' show NewOrderScreen;

/// Dealers the user may see. Sales and collection staff only get their assigned dealers (server-side rule).
class DealersScreen extends StatefulWidget {
  const DealersScreen({super.key});

  @override
  State<DealersScreen> createState() => _DealersScreenState();
}

class _DealersScreenState extends State<DealersScreen> {
  String _q = '';
  int? _total;
  Timer? _debounce;

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<(List<Map<String, dynamic>>, int)> _fetch(int page) async {
    final r = await SessionScope.read(context).api.get('/dealers', query: {'page': page, 'page_size': 50, 'q': _q})
        as Map<String, dynamic>;
    final total = r['total'] as int;
    if (mounted && _q.isEmpty && page == 1) setState(() => _total = total);
    return (List<Map<String, dynamic>>.from(r['items'] as List), total);
  }

  @override
  Widget build(BuildContext context) {
    final s = SessionScope.of(context);
    final all = s.can('dealers.view_all');
    return Column(children: [
      ScreenHeader(
        title: all ? 'Dealers' : 'My dealers',
        subtitle: _total == null ? null : '$_total ${_total == 1 ? 'dealer' : 'dealers'}${all ? '' : ' assigned to you'}',
        bottom: HeaderSearch(
          hint: 'Search name, part of name or code',
          onChanged: (v) {
            _debounce?.cancel();
            _debounce = Timer(const Duration(milliseconds: 350), () {
              if (mounted) setState(() => _q = v.trim());
            });
          },
        ),
      ),
      Expanded(
        child: PagedList<Map<String, dynamic>>(
          query: _q,
          fetch: _fetch,
          empty: _q.isEmpty
              ? const EmptyState(
                  icon: Icons.storefront_outlined,
                  title: 'No dealers yet',
                  message: 'Dealers assigned to you will appear here. Ask your manager to assign them.',
                )
              : EmptyState(icon: Icons.search_off, title: 'No match for "$_q"', message: 'Try part of the name.'),
          header: (count) => Padding(
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 6),
            child: Text(_q.isEmpty ? 'All' : '$count found',
                style: const TextStyle(color: AppColors.steel, fontWeight: FontWeight.w600, fontSize: 13)),
          ),
          itemBuilder: (context, d) => _DealerCard(dealer: d),
        ),
      ),
    ]);
  }
}

class _DealerCard extends StatelessWidget {
  const _DealerCard({required this.dealer});

  final Map<String, dynamic> dealer;

  @override
  Widget build(BuildContext context) {
    final d = dealer;
    final blocked = d['status'] == 'BLOCKED';
    final group = d['tally_parent_group'] as String?;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: Material(
        color: AppColors.slab,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => DealerDetailScreen(dealerId: d['id'] as String, name: d['name'] as String),
          )),
          child: Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: AppColors.seam),
            ),
            child: Row(children: [
              Monogram(d['name'] as String),
              Gap.m,
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(d['name'] as String,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
                  Gap.xs,
                  Text(
                    [d['code'], if (group != null && group != 'Sundry Debtors') group].join('  •  '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: AppColors.steel, fontSize: 13),
                  ),
                ]),
              ),
              Gap.s,
              if (blocked)
                const StatusChip('Blocked', color: AppColors.bad, icon: Icons.block)
              else if (d['credit_limit'] != null)
                Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                  Text(inr(d['credit_limit'] as String), style: const TextStyle(fontWeight: FontWeight.w700)),
                  const Text('limit', style: TextStyle(color: AppColors.steel, fontSize: 12)),
                ])
              else
                const Icon(Icons.chevron_right, color: AppColors.mist),
            ]),
          ),
        ),
      ),
    );
  }
}

class DealerDetailScreen extends StatefulWidget {
  const DealerDetailScreen({super.key, required this.dealerId, required this.name});

  final String dealerId;
  final String name;

  @override
  State<DealerDetailScreen> createState() => _DealerDetailScreenState();
}

class _DealerDetailScreenState extends State<DealerDetailScreen> {
  Future<(Map<String, dynamic>, List<Map<String, dynamic>>, Map<String, String>)>? _future;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<(Map<String, dynamic>, List<Map<String, dynamic>>, Map<String, String>)> _load() async {
    final api = SessionScope.read(context).api;
    final results = await Future.wait([
      api.get('/dealers/${widget.dealerId}'),
      api.get('/dealers/${widget.dealerId}/rates'),
      api.get('/products', query: {'page_size': 200, 'erp_enabled': 'true'}),
    ]);
    final products = <String, String>{
      for (final p in ((results[2] as Map<String, dynamic>)['items'] as List).cast<Map<String, dynamic>>())
        p['id'] as String: p['name'] as String,
    };
    final today = DateTime.now().toIso8601String().substring(0, 10);
    final rates = (results[1] as List)
        .cast<Map<String, dynamic>>()
        .where((r) => r['valid_to'] == null || (r['valid_to'] as String).compareTo(today) >= 0)
        .toList();
    return (results[0] as Map<String, dynamic>, rates, products);
  }

  bool _sending = false;

  /// Builds the balance message on the server, then opens WhatsApp with it typed in; the user presses send.
  Future<void> _sendBalance(Map<String, dynamic> d, {bool confirm = false}) async {
    setState(() => _sending = true);
    try {
      final r = await SessionScope.read(context).api.post('/dealers/${d['id']}/whatsapp/balance',
          body: {'confirm_repeat': confirm}) as Map<String, dynamic>;
      final url = r['url'] as String?;
      if (url == null) {
        // Automatic mode: the server already sent it through the WhatsApp Business API.
        if (!mounted) return;
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Balance sent on WhatsApp to +${r['number']}')));
        return;
      }
      final ok = await launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
      if (!mounted) return;
      if (!ok) showError(context, 'Could not open WhatsApp');
    } on ApiException catch (e) {
      if (!mounted) return;
      if (e.code == 'RECENTLY_REMINDED') {
        final again = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('Remind again?'),
            content: Text(e.detail),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('No')),
              TextButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Send again')),
            ],
          ),
        );
        if (again == true && mounted) {
          setState(() => _sending = false);
          return _sendBalance(d, confirm: true);
        }
      } else {
        showError(context, e);
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  bool _canEditContacts(BuildContext context) {
    final s = SessionScope.of(context);
    return s.can('masters.dealers_manage') || s.can('collections.followup');
  }

  void _reload() {
    if (mounted) setState(() => _future = _load());
  }

  /// Add (contact == null) or edit / remove a mobile number. The "WhatsApp" number gets balance reminders.
  Future<void> _editContact(Map<String, dynamic> d, [Map<String, dynamic>? contact]) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _ContactSheet(dealerId: d['id'] as String, contact: contact),
    );
    if (changed == true) _reload();
  }

  Future<void> _call(String phone) async {
    final ok = await launchUrl(Uri(scheme: 'tel', path: phone));
    if (!ok && mounted) showError(context, 'Could not open the phone dialer');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: FutureBuilder(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return Column(children: [_Header(name: widget.name), const Expanded(child: SkeletonList(rows: 5))]);
          }
          if (snap.hasError) {
            return Column(children: [
              _Header(name: widget.name),
              EmptyState(icon: Icons.cloud_off_outlined, title: 'Could not load', message: snap.error.toString()),
            ]);
          }
          final (d, rates, products) = snap.data!;
          final contacts = (d['contacts'] as List).cast<Map<String, dynamic>>();
          final sites = (d['addresses'] as List).cast<Map<String, dynamic>>();
          final primary = contacts.where((c) => c['phone'] != null).toList()
            ..sort((a, b) => (b['is_primary'] == true ? 1 : 0) - (a['is_primary'] == true ? 1 : 0));
          final blocked = d['status'] == 'BLOCKED';

          return ListView(padding: EdgeInsets.zero, children: [
            _Header(
              name: d['name'] as String,
              subtitle: d['tally_ledger_name'] != null && d['tally_ledger_name'] != d['name']
                  ? 'Tally: ${d['tally_ledger_name']}'
                  : d['code'] as String,
              chips: [
                if (blocked) const StatusChip('Blocked', color: AppColors.bad, icon: Icons.block),
                if (d['tally_parent_group'] != null) StatusChip(d['tally_parent_group'] as String, color: AppColors.mist),
              ],
              action: primary.isEmpty
                  ? null
                  : FilledButton.icon(
                      style: FilledButton.styleFrom(
                        backgroundColor: Colors.white,
                        foregroundColor: AppColors.blue,
                        minimumSize: const Size.fromHeight(46),
                      ),
                      onPressed: () => _call(primary.first['phone'] as String),
                      icon: const Icon(Icons.call),
                      label: Text('Call ${primary.first['name']}'),
                    ),
            ),
            if (blocked)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                      color: AppColors.bad.withValues(alpha: 0.08), borderRadius: BorderRadius.circular(12)),
                  child: Row(children: [
                    const Icon(Icons.block, color: AppColors.bad),
                    Gap.m,
                    Expanded(
                      child: Text('No new orders. ${d['blocked_reason'] ?? ''}',
                          style: const TextStyle(color: AppColors.bad, fontWeight: FontWeight.w600)),
                    ),
                  ]),
                ),
              ),
            const SectionTitle('Credit'),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(children: [
                Expanded(
                  child: StatTile(
                    label: 'Credit limit',
                    value: d['credit_limit'] == null ? 'Not set' : inr(d['credit_limit'] as String),
                  ),
                ),
                Gap.m,
                Expanded(
                  child: StatTile(
                    label: 'Payment terms',
                    value: d['payment_terms_days'] == null ? '—' : '${d['payment_terms_days']} days',
                  ),
                ),
              ]),
            ),
            Builder(builder: (context) {
              final bal = double.tryParse((d['tally_balance'] ?? '') as String) ?? 0;
              final asOf = d['tally_balance_at'] as String?;
              final when = asOf == null ? null : DateTime.tryParse(asOf)?.toLocal();
              return Panel(
                margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                padding: const EdgeInsets.all(16),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  const Text('Tally balance', style: TextStyle(color: AppColors.steel, fontSize: 13)),
                  Gap.xs,
                  Text(
                    d['tally_balance'] == null ? 'Not synced yet' : '${inr(bal.abs().toString())} ${bal >= 0 ? 'Dr' : 'Cr'}',
                    style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
                  ),
                  if (when != null)
                    Text('as of ${when.day}/${when.month} ${when.hour.toString().padLeft(2, '0')}:${when.minute.toString().padLeft(2, '0')}',
                        style: const TextStyle(color: AppColors.mist, fontSize: 12.5)),
                  Gap.m,
                  Row(children: [
                    if (SessionScope.of(context).can('credit.view'))
                      Expanded(
                        child: OutlinedButton.icon(
                          style: OutlinedButton.styleFrom(minimumSize: const Size(0, 52)),
                          onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                              builder: (_) => StatementScreen(dealerId: d['id'] as String, name: d['name'] as String))),
                          icon: const Icon(Icons.receipt_long_outlined),
                          label: const Text('Statement', style: TextStyle(fontSize: 16)),
                        ),
                      ),
                    if (SessionScope.of(context).can('credit.view') &&
                        SessionScope.of(context).can('orders.create') && d['status'] == 'ACTIVE')
                      Gap.m,
                    if (SessionScope.of(context).can('orders.create') && d['status'] == 'ACTIVE')
                      Expanded(
                        child: FilledButton.icon(
                          style: FilledButton.styleFrom(minimumSize: const Size(0, 52)),
                          onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                              builder: (_) => NewOrderScreen(dealer: d))),
                          icon: const Icon(Icons.add_shopping_cart),
                          label: const Text('Book order', style: TextStyle(fontSize: 16)),
                        ),
                      ),
                  ]),
                  if (bal > 0 && SessionScope.of(context).can('collections.followup')) ...[
                    Gap.m,
                    FilledButton.icon(
                      style: FilledButton.styleFrom(backgroundColor: const Color(0xFF1F8F4E)),
                      onPressed: _sending ? null : () => _sendBalance(d),
                      icon: const Icon(Icons.chat_outlined),
                      label: Text(_sending ? 'Sending…' : 'Send balance on WhatsApp'),
                    ),
                  ],
                ]),
              );
            }),
            SectionTitle('Mobile numbers',
                trailing: _canEditContacts(context)
                    ? TextButton.icon(
                        onPressed: () => _editContact(d),
                        icon: const Icon(Icons.add, size: 18),
                        label: const Text('Add'),
                      )
                    : Text('${contacts.length}', style: const TextStyle(color: AppColors.steel))),
            if (contacts.isEmpty)
              _Muted([d['tally_mobile'], d['tally_phone']].whereType<String>().isNotEmpty
                  ? 'From Tally: ${[d['tally_mobile'], d['tally_phone']].whereType<String>().join(' · ')}'
                  : 'No mobile number in Tally or here yet.${_canEditContacts(context) ? ' Tap Add to save one.' : ''}')
            else
              Panel(
                child: Column(children: [
                  for (final (i, c) in contacts.indexed) ...[
                    if (i > 0) const Divider(indent: 72),
                    ListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                      leading: Monogram(c['name'] as String, size: 40),
                      title: Row(children: [
                        Flexible(child: Text(c['name'] as String, style: const TextStyle(fontWeight: FontWeight.w600))),
                        if (c['is_primary'] == true) ...[Gap.s, const StatusChip('WhatsApp', color: AppColors.ok)],
                      ]),
                      subtitle: Text([c['role'], c['phone']].whereType<String>().join('  •  '),
                          style: const TextStyle(color: AppColors.steel)),
                      onTap: _canEditContacts(context) ? () => _editContact(d, c) : null,
                      trailing: c['phone'] == null
                          ? null
                          : IconButton.filledTonal(
                              tooltip: 'Call',
                              icon: const Icon(Icons.call, color: AppColors.ok),
                              onPressed: () => _call(c['phone'] as String),
                            ),
                      onLongPress: c['phone'] == null
                          ? null
                          : () {
                              Clipboard.setData(ClipboardData(text: c['phone'] as String));
                              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Number copied')));
                            },
                    ),
                  ],
                ]),
              ),
            SectionTitle('Delivery sites', trailing: Text('${sites.length}', style: const TextStyle(color: AppColors.steel))),
            if (sites.isEmpty)
              const _Muted('No delivery sites saved yet.')
            else
              Panel(
                child: Column(children: [
                  for (final (i, a) in sites.indexed) ...[
                    if (i > 0) const Divider(indent: 56),
                    ListTile(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                      leading: const Icon(Icons.location_on_outlined, color: AppColors.steel),
                      title: Text(a['label'] as String, style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text(
                        [a['line1'], a['area'], a['city'], a['pincode']].whereType<String>().join(', '),
                        style: const TextStyle(color: AppColors.steel),
                      ),
                    ),
                  ],
                ]),
              ),
            SectionTitle('Customer rates', trailing: Text('${rates.length}', style: const TextStyle(color: AppColors.steel))),
            if (rates.isEmpty)
              const _Muted('No rates set for this dealer.')
            else
              Panel(
                child: Column(children: [
                  for (final (i, r) in rates.indexed) ...[
                    if (i > 0) const Divider(indent: 16),
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      child: Row(children: [
                        Expanded(
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(products[r['product_id']] ?? 'Product', style: const TextStyle(fontWeight: FontWeight.w600)),
                            Gap.xs,
                            Text('Since ${r['valid_from']}', style: const TextStyle(color: AppColors.steel, fontSize: 12.5)),
                          ]),
                        ),
                        Text.rich(TextSpan(children: [
                          TextSpan(text: inr(r['rate'] as String),
                              style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
                          TextSpan(text: ' / ${(r['uom'] as String).toLowerCase()}',
                              style: const TextStyle(color: AppColors.steel)),
                        ])),
                      ]),
                    ),
                  ],
                ]),
              ),
            Gap.xl,
          ]);
        },
      ),
    );
  }
}

/// Dark header of the dealer page with back button, monogram and main action.
class _Header extends StatelessWidget {
  const _Header({required this.name, this.subtitle, this.chips = const [], this.action});

  final String name;
  final String? subtitle;
  final List<Widget> chips;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final top = MediaQuery.of(context).padding.top;
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Container(
        color: AppColors.blue,
        padding: EdgeInsets.fromLTRB(8, top + 4, 20, 20),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          IconButton(
            tooltip: 'Back',
            icon: const Icon(Icons.arrow_back, color: Colors.white),
            onPressed: () => Navigator.of(context).maybePop(),
          ),
          Padding(
            padding: const EdgeInsets.only(left: 12),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Monogram(name, size: 52, dark: true),
                Gap.l,
                Expanded(
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text(name,
                        style: const TextStyle(color: Colors.white, fontSize: 21, fontWeight: FontWeight.w800, height: 1.15)),
                    if (subtitle != null) ...[
                      Gap.xs,
                      Text(subtitle!, style: const TextStyle(color: Colors.white70, fontSize: 13.5)),
                    ],
                  ]),
                ),
              ]),
              if (chips.isNotEmpty) ...[Gap.m, Wrap(spacing: 6, runSpacing: 6, children: chips)],
              if (action != null) ...[Gap.l, action!],
            ]),
          ),
        ]),
      ),
    );
  }
}

class _Muted extends StatelessWidget {
  const _Muted(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 20),
        child: Text(text, style: const TextStyle(color: AppColors.steel)),
      );
}


class _ContactSheet extends StatefulWidget {
  const _ContactSheet({required this.dealerId, this.contact});

  final String dealerId;
  final Map<String, dynamic>? contact;

  @override
  State<_ContactSheet> createState() => _ContactSheetState();
}

class _ContactSheetState extends State<_ContactSheet> {
  late final _phone = TextEditingController(text: widget.contact?['phone'] as String? ?? '');
  late final _name = TextEditingController(text: widget.contact?['name'] as String? ?? '');
  late bool _primary = widget.contact == null || widget.contact!['is_primary'] == true;
  bool _busy = false;
  String? _error;

  Future<void> _save() async {
    if (_phone.text.trim().isEmpty || _name.text.trim().isEmpty) {
      setState(() => _error = 'Enter the number and a name');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final api = SessionScope.read(context).api;
    final body = {'name': _name.text.trim(), 'phone': _phone.text.trim(), 'is_primary': _primary, 'whatsapp_opt_in': false};
    try {
      if (widget.contact == null) {
        await api.post('/dealers/${widget.dealerId}/contacts', body: body);
      } else {
        await api.put('/dealers/${widget.dealerId}/contacts/${widget.contact!['id']}', body: body);
      }
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.detail;
          _busy = false;
        });
      }
    }
  }

  Future<void> _remove() async {
    setState(() => _busy = true);
    try {
      await SessionScope.read(context).api.delete('/dealers/${widget.dealerId}/contacts/${widget.contact!['id']}');
      if (mounted) Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.detail;
          _busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(widget.contact == null ? 'Add mobile number' : 'Edit mobile number',
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
        Gap.m,
        TextField(
          controller: _phone,
          autofocus: widget.contact == null,
          keyboardType: TextInputType.phone,
          decoration: const InputDecoration(labelText: 'Mobile number', hintText: '10 digits'),
        ),
        Gap.s,
        TextField(
          controller: _name,
          textCapitalization: TextCapitalization.words,
          decoration: const InputDecoration(labelText: 'Name', hintText: 'e.g. Owner, Munshi, Accounts'),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Send WhatsApp messages here'),
          subtitle: const Text('Balance reminders go to this number'),
          value: _primary,
          onChanged: (v) => setState(() => _primary = v),
        ),
        if (_error != null) Padding(padding: const EdgeInsets.only(bottom: 8), child: Text(_error!, style: const TextStyle(color: AppColors.bad))),
        FilledButton(onPressed: _busy ? null : _save, child: Text(widget.contact == null ? 'Add number' : 'Save')),
        if (widget.contact != null) ...[
          Gap.s,
          TextButton(
            onPressed: _busy ? null : _remove,
            child: const Text('Remove this number', style: TextStyle(color: AppColors.bad)),
          ),
        ],
      ]),
    );
  }
}
