import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api.dart';
import '../../core/session.dart';
import '../../core/ui.dart';

typedef Json = Map<String, dynamic>;

const doStatus = <String, (String, Color)>{
  'PLANNED': ('Planned', AppColors.warn),
  'LOADED': ('Loaded', AppColors.warn),
  'DISPATCHED': ('On the road', AppColors.ok),
  'DELIVERED': ('Delivered', AppColors.ok),
  'PARTIALLY_DELIVERED': ('Delivered short', AppColors.bad),
  'CANCELLED': ('Cancelled', AppColors.steel),
};

Widget doChip(String status) {
  final (label, color) = doStatus[status] ?? (status, AppColors.steel);
  return StatusChip(label, color: color);
}

String _num(Object? v) {
  final d = double.tryParse('${v ?? ''}');
  if (d == null) return '';
  return d == d.roundToDouble() ? d.toStringAsFixed(0) : d.toStringAsFixed(2);
}

/// Opens Google Maps (app or browser) at an address.
Future<void> openMaps(String address) async {
  final q = address.replaceFirst(RegExp(r'^[^:]*: '), '');
  await launchUrl(Uri.parse('https://www.google.com/maps/search/?api=1&query=${Uri.encodeComponent(q)}'),
      mode: LaunchMode.externalApplication);
}

// ================================================================== send an order
/// Send an approved order: pick where it goes from — a godown with its Tally stock, or a factory truck in the
/// Direct godown (delivered straight from that truck) — then the quantities. An own truck can be sent out in the
/// same step when the driver is known; a direct truck always is.
class PlanDispatchScreen extends StatefulWidget {
  const PlanDispatchScreen({super.key, required this.order});

  final Json order;

  @override
  State<PlanDispatchScreen> createState() => _PlanDispatchScreenState();
}

class _PlanDispatchScreenState extends State<PlanDispatchScreen> {
  Json? _opt;
  List<Json> _vehicles = [], _drivers = [];
  String? _godownId, _truckId, _vehicleId, _driverId;
  bool _sendNow = false;
  final _qty = <String, TextEditingController>{};
  final _remarks = TextEditingController();
  final _override = TextEditingController();
  final _factoryTruck = TextEditingController();
  final _factoryRef = TextEditingController();
  List<String> _knownTrucks = const []; // factory truck numbers used before, most recent first

  List<String> get _truckChoices {
    final q = _factoryTruck.text.toUpperCase().replaceAll(RegExp(r'\s'), '');
    return _knownTrucks.where((t) => q.isEmpty || (t.contains(q) && t != q)).take(8).toList();
  }
  bool _loading = true, _saving = false, _needOverride = false, _refreshing = false;
  String? _error;

  List<Json> get _lines =>
      List<Json>.from(widget.order['items'] as List).where((i) => (double.tryParse('${i['remaining_qty']}') ?? 0) > 0).toList();
  List<Json> get _godowns => List<Json>.from((_opt?['godowns'] as List?) ?? const []);
  List<Json> get _trucks => List<Json>.from((_opt?['direct_trucks'] as List?) ?? const []);
  Json? get _truck => _trucks.where((t) => t['id'] == _truckId).firstOrNull;
  Json? get _godown => _godowns.where((g) => g['godown_id'] == _godownId).firstOrNull;
  /// The Direct godown: the material is on a factory truck (picked from the list, or its number typed in).
  bool get _directGodown => _godown?['kind'] == 'DIRECT';
  bool get _ownTruck => _godown != null && !_directGodown;

  @override
  void initState() {
    super.initState();
    for (final l in _lines) {
      _qty[l['id'] as String] = TextEditingController(text: _num(l['remaining_qty']));
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_loading && _opt == null) {
      _sendNow = SessionScope.read(context).can('dispatch.confirm');
      _load();
    }
  }

  Future<void> _load({bool keepChoice = false}) async {
    final api = SessionScope.read(context).api;
    // Factory trucks typed before: offered as one-tap choices (a failure here never blocks planning).
    api.get('/dispatch/factory-truck-numbers', query: {'limit': 50}).then((r) {
      if (mounted) setState(() => _knownTrucks = [for (final t in r as List) '${(t as Json)['truck_no']}']);
    }).catchError((_) {});
    try {
      final r = await Future.wait([
        api.get('/orders/${widget.order['id']}/dispatch-options'),
        api.get('/dispatch/vehicles'),
        api.get('/drivers', query: {'page_size': 200}),
      ]);
      if (!mounted) return;
      setState(() {
        _opt = r[0] as Json;
        _vehicles = List<Json>.from(r[1] as List).where((v) => v['status'] != 'MAINTENANCE').toList();
        _drivers = List<Json>.from((r[2] as Json)['items'] as List).where((d) => d['is_active'] == true).toList();
        _loading = false;
        if (!keepChoice || (_godownId == null && _truckId == null)) {
          // start with a godown that has everything, else one with some stock, else a matching truck
          final best = _godowns.where((g) => g['enough'] == true).firstOrNull ??
              _godowns.where((g) => g['has_stock'] == true).firstOrNull;
          if (best != null) {
            _pick(godown: best['godown_id'] as String);
          } else {
            final t = _trucks.where((t) => t['matches'] == true).firstOrNull;
            if (t != null) _pick(truck: t['id'] as String);
          }
        }
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.detail;
          _loading = false;
        });
      }
    }
  }

  void _pick({String? godown, String? truck}) {
    _godownId = godown;
    _truckId = truck;
    for (final l in _lines) {
      var q = double.tryParse('${l['remaining_qty']}') ?? 0;
      if (truck != null) {
        final tl = List<Json>.from(_truck!['lines'] as List).where((x) => x['order_item_id'] == l['id']).firstOrNull;
        q = tl == null ? 0 : (q < (double.tryParse('${tl['left_qty']}') ?? 0) ? q : double.parse('${tl['left_qty']}'));
      }
      _qty[l['id']]!.text = _num(q);
    }
  }

  Future<void> _refreshStock() async {
    final api = SessionScope.read(context).api;
    setState(() => _refreshing = true);
    try {
      await api.post('/stock/refresh');
      for (var i = 0; i < 90; i++) {
        await Future.delayed(const Duration(seconds: 2));
        final s = await api.get('/stock') as Json;
        if ((s['latest'] as Json?)?['status'] != 'RUNNING') break;
      }
      await _load(keepChoice: true);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.detail);
    } finally {
      if (mounted) setState(() => _refreshing = false);
    }
  }

  bool _allowMore = false;
  bool _closeRest = false;

  bool get _goesNow => _truckId != null || _directGodown || (_ownTruck && _sendNow);

  double _q(Json l) => double.tryParse(_qty[l['id']]!.text.trim()) ?? 0;

  List<String> get _more => [
        for (final l in _lines)
          if (_q(l) > (double.tryParse('${l['remaining_qty']}') ?? 0))
            '${l['product_name']}: ${_num(_q(l))} ${l['uom']} is '
                '${_num(_q(l) - (double.tryParse('${l['remaining_qty']}') ?? 0))} more than left on the order',
      ];

  List<String> get _less => !_goesNow
      ? const []
      : [
          for (final l in _lines)
            if ((double.tryParse('${l['remaining_qty']}') ?? 0) - _q(l) > 0)
              '${_num((double.tryParse('${l['remaining_qty']}') ?? 0) - _q(l))} ${l['uom']} ${l['product_name']}',
        ];

  double get _loadKg {
    var kg = 0.0;
    for (final l in _lines) {
      final w = double.tryParse('${l['weight_per_unit_kg'] ?? ''}');
      final q = double.tryParse(_qty[l['id']]!.text) ?? 0;
      if (w != null) kg += w * q;
    }
    return kg;
  }

  Future<void> _save() async {
    final lines = [
      for (final l in _lines)
        if ((double.tryParse(_qty[l['id']]!.text) ?? 0) > 0) {'order_item_id': l['id'], 'qty': _qty[l['id']]!.text.trim()},
    ];
    final direct = _truckId != null;
    String? problem;
    if (_godownId == null && _truckId == null) {
      problem = 'Choose where it goes from';
    } else if (_directGodown && _factoryTruck.text.trim().length < 4) {
      problem = 'Type the factory truck number';
    } else if (_ownTruck && _vehicleId == null) {
      problem = 'Choose the truck';
    } else if (_ownTruck && _sendNow && _driverId == null) {
      problem = 'Choose the driver to send the truck out';
    } else if (lines.isEmpty) {
      problem = 'Enter at least one quantity';
    } else if (_more.isNotEmpty && !_allowMore) {
      problem = 'Tick "Send more" to increase the order, or correct the quantity';
    }
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final d = await SessionScope.read(context).api.post('/delivery-orders', body: {
        'sales_order_id': widget.order['id'],
        'godown_id': direct ? null : _godownId,
        'vehicle_id': _ownTruck ? _vehicleId : null,
        'direct_truck_id': direct ? _truckId : null,
        'direct_truck_no': _directGodown ? _factoryTruck.text.trim() : null,
        'supplier_ref': _directGodown && _factoryRef.text.trim().isNotEmpty ? _factoryRef.text.trim() : null,
        'send_now': _ownTruck && _sendNow,
        'driver_id': _ownTruck ? _driverId : null,
        'remarks': _remarks.text.trim().isEmpty ? null : _remarks.text.trim(),
        'capacity_override_reason': _override.text.trim().isEmpty ? null : _override.text.trim(),
        'lines': lines,
        'allow_more': _more.isNotEmpty && _allowMore,
        'close_rest': _less.isNotEmpty && _closeRest,
      }) as Json;
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(d['status'] == 'DISPATCHED'
              ? '${d['do_no']} sent out on ${d['vehicle_no']}. Record delivery when it reaches.'
              : '${d['do_no']} planned on ${d['vehicle_no']}')));
      Navigator.of(context).pop(d); // the order screen opens this truck next
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = e.detail;
        if (e.code == 'OVER_CAPACITY') _needOverride = true;
      });
    }
  }

  Widget _choice({required bool selected, required bool enabled, required VoidCallback onTap, required Widget child}) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
      child: Material(
        color: selected ? AppColors.concrete : AppColors.slab,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
            side: BorderSide(color: selected ? AppColors.ink : AppColors.seam, width: selected ? 1.6 : 1)),
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: enabled ? onTap : null,
          child: Opacity(
              opacity: enabled ? 1 : 0.45,
              child: Padding(padding: const EdgeInsets.fromLTRB(14, 10, 14, 10), child: child)),
        ),
      ),
    );
  }

  Widget _godownTile(Json g) {
    final lines = _lines;
    final gl = List<Json>.from(g['lines'] as List);
    final direct = g['kind'] == 'DIRECT';
    final (label, color) = direct
        ? ('Factory truck', AppColors.warn)
        : g['enough'] == true
        ? ('Enough stock', AppColors.ok)
        : g['has_stock'] == true
            ? ('Part stock', AppColors.warn)
            : ('No stock', AppColors.steel);
    return _choice(
      selected: _godownId == g['godown_id'],
      enabled: true,
      onTap: () => setState(() => _pick(godown: g['godown_id'] as String)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Text(g['name'] as String, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15))),
          StatusChip(label, color: color),
        ]),
        if (direct)
          const Text('Straight from the factory truck to the dealer',
              style: TextStyle(color: AppColors.steel, fontSize: 13)),
        if (!direct)
        for (final l in lines)
          Builder(builder: (_) {
            final x = gl.where((e) => e['order_item_id'] == l['id']).firstOrNull;
            final avail = x?['available_qty'];
            final reserved = double.tryParse('${x?['reserved_qty'] ?? 0}') ?? 0;
            return Text(
              '${lines.length > 1 ? '${l['product_name']}: ' : ''}'
              '${avail == null ? 'not in Tally stock' : '${_num(avail)} ${l['uom']} available'}'
              '${reserved > 0 ? ' (${_num(reserved)} on trucks at the gate)' : ''}',
              style: const TextStyle(color: AppColors.steel, fontSize: 13),
            );
          }),
      ]),
    );
  }

  Widget _truckTile(Json t) {
    final lines = List<Json>.from(t['lines'] as List);
    final ok = t['matches'] == true;
    return _choice(
      selected: _truckId == t['id'],
      enabled: ok,
      onTap: () => setState(() => _pick(truck: t['id'] as String)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Text(t['truck_no'] as String, style: const TextStyle(fontWeight: FontWeight.w800, fontSize: 15))),
          Text('${const {'ambuja': 'Ambuja', 'shree': 'Shree'}[t['supplier']] ?? 'Factory truck'} · Inv ${t['invoice_no']}',
              style: const TextStyle(color: AppColors.steel, fontSize: 12)),
        ]),
        Text(
          '${lines.map((x) => '${_num(x['left_qty'] ?? x['qty'])} ${(x['uom'] as String).toLowerCase()} ${x['tally_item']}').join(', ')} left'
          '${ok ? '' : ' · not what this order needs'}',
          style: const TextStyle(color: AppColors.steel, fontSize: 13),
        ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final o = widget.order;
    final canSend = SessionScope.of(context).can('dispatch.confirm');
    final vehicle = _vehicles.where((v) => v['vehicle_id'] == _vehicleId).firstOrNull;
    final room = vehicle == null
        ? null
        : (double.tryParse('${vehicle['capacity_kg']}') ?? 0) - (double.tryParse('${vehicle['planned_kg']}') ?? 0);
    final asOf = _opt?['stock_as_of'] as String?;
    final truck = _truck;
    final action = truck != null
        ? (canSend ? 'Send from ${truck['truck_no']}' : 'Plan from this truck')
        : _directGodown
            ? 'Send from the factory truck'
            : (_sendNow ? 'Send out now' : 'Plan truck');
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: Text('Dispatch · ${o['order_no'] ?? ''}')),
      body: _loading
          ? const SkeletonList(rows: 4)
          : ListView(padding: const EdgeInsets.only(bottom: 32), children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 16, 20, 0),
                child: Text(o['dealer_name'] as String, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
              ),
              if (o['delivery_location'] != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
                  child: Text(o['delivery_location'] as String, style: const TextStyle(color: AppColors.steel)),
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 18, 12, 4),
                child: Row(children: [
                  const Expanded(
                      child: Text('SEND FROM',
                          style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800, color: AppColors.steel, letterSpacing: .6))),
                  TextButton(
                    onPressed: _refreshing ? null : _refreshStock,
                    child: Text(_refreshing ? 'Reading Tally…' : 'Refresh stock'),
                  ),
                ]),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
                child: Text(
                  asOf == null ? 'Stock not read from Tally yet' : 'Stock as per Tally, ${_when(asOf)}',
                  style: const TextStyle(color: AppColors.steel, fontSize: 12),
                ),
              ),
              for (final g in _godowns) _godownTile(g),
              if (_trucks.isNotEmpty) const SectionTitle('Direct trucks · straight to the dealer'),
              for (final t in _trucks) _truckTile(t),
              if (_directGodown) ...[
                const SectionTitle('Factory truck'),
                Panel(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    TextField(
                      controller: _factoryTruck,
                      textCapitalization: TextCapitalization.characters,
                      decoration: const InputDecoration(labelText: 'Truck number', hintText: 'HR55AB1234'),
                      onChanged: (_) => setState(() {}),
                    ),
                    if (_truckChoices.isNotEmpty) ...[
                      Gap.s,
                      Text(_factoryTruck.text.trim().isEmpty ? 'Used before' : 'Matching',
                          style: const TextStyle(color: AppColors.steel, fontSize: 12.5)),
                      Wrap(spacing: 6, runSpacing: 0, children: [
                        for (final t in _truckChoices)
                          ActionChip(
                            label: Text(t),
                            visualDensity: VisualDensity.compact,
                            onPressed: () => setState(() => _factoryTruck.text = t),
                          ),
                      ]),
                    ],
                    TextField(
                      controller: _factoryRef,
                      decoration: const InputDecoration(labelText: 'Factory invoice / SAP reference (optional)'),
                    ),
                    Gap.s,
                    Text(
                      'Goes straight to ${o['dealer_name']} and is sent out as soon as you save.'
                      '${_trucks.any((t) => t['matches'] == true) ? ' Listed Direct trucks are below.' : ''}',
                      style: const TextStyle(color: AppColors.steel, fontSize: 13),
                    ),
                  ]),
                ),
              ],
              if (_ownTruck) ...[
                const SectionTitle('Own truck'),
                Panel(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
                  child: Column(children: [
                    DropdownButtonFormField<String>(
                      isExpanded: true,
                      value: _vehicleId,
                      decoration: const InputDecoration(labelText: 'Truck'),
                      items: [
                        for (final v in _vehicles)
                          DropdownMenuItem(
                            value: v['vehicle_id'] as String,
                            child: Text('${v['registration_no']} · ${_num((double.tryParse('${v['capacity_kg']}') ?? 0) / 1000)} MT'
                                '${(v['on_road_dos'] as int? ?? 0) > 0 ? ' · on the road' : ''}'),
                          ),
                      ],
                      onChanged: (v) => setState(() => _vehicleId = v),
                    ),
                    DropdownButtonFormField<String?>(
                      isExpanded: true,
                      value: _driverId,
                      decoration: InputDecoration(
                          labelText: 'Driver',
                          helperText: _sendNow ? 'Needed to send the truck out' : 'Needed before the truck leaves'),
                      items: [
                        const DropdownMenuItem<String?>(value: null, child: Text('Choose later')),
                        for (final d in _drivers)
                          DropdownMenuItem<String?>(value: d['id'] as String, child: Text('${d['name']} · ${d['phone']}')),
                      ],
                      onChanged: (v) => setState(() => _driverId = v),
                    ),
                    if (canSend)
                      SwitchListTile(
                        contentPadding: EdgeInsets.zero,
                        value: _sendNow,
                        onChanged: (v) => setState(() => _sendNow = v),
                        title: const Text('Send out now', style: TextStyle(fontWeight: FontWeight.w700)),
                        subtitle: const Text('Loaded and leaving. Turn off to plan it for loading later.'),
                      ),
                  ]),
                ),
              ],
              if (truck != null)
                Panel(
                  margin: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                  padding: const EdgeInsets.all(14),
                  child: Text(
                    'Truck ${truck['truck_no']} goes from the factory straight to ${o['dealer_name']}. It is sent out as soon '
                    'as you save; record the delivery when it reaches. Invoice ${truck['invoice_no']} is kept as the reference.',
                    style: const TextStyle(color: AppColors.steel),
                  ),
                ),
              if (_godownId != null || _truckId != null) ...[
                const SectionTitle('Quantity'),
                for (final l in _lines)
                  Panel(
                    margin: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                    padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
                    child: Row(children: [
                      Expanded(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text(l['product_name'] as String, style: const TextStyle(fontWeight: FontWeight.w700)),
                          Text('${_num(l['remaining_qty'])} ${l['uom']} left on the order',
                              style: const TextStyle(color: AppColors.steel, fontSize: 13)),
                        ]),
                      ),
                      SizedBox(
                        width: 110,
                        child: TextField(
                          controller: _qty[l['id']],
                          keyboardType: const TextInputType.numberWithOptions(decimal: true),
                          decoration: InputDecoration(suffixText: l['uom'] as String),
                          onChanged: (_) => setState(() {}),
                        ),
                      ),
                    ]),
                  ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: QtyChoice(
                    more: _more,
                    less: _less,
                    allowMore: _allowMore,
                    closeRest: _closeRest,
                    onAllowMore: (v) => setState(() => _allowMore = v),
                    onCloseRest: (v) => setState(() => _closeRest = v),
                  ),
                ),
                if (_ownTruck)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(20, 4, 20, 0),
                    child: Text(
                      'This load: ${_num(_loadKg / 1000)} MT'
                      '${room != null ? ' · room on truck: ${_num(room / 1000)} MT' : ''}',
                      style: TextStyle(
                          color: room != null && _loadKg > room ? AppColors.bad : AppColors.steel,
                          fontWeight: FontWeight.w600),
                    ),
                  ),
                Panel(
                  margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
                  child: Column(children: [
                    TextField(controller: _remarks, decoration: const InputDecoration(labelText: 'Remarks (optional)')),
                    if (_needOverride)
                      TextField(
                          controller: _override,
                          decoration: const InputDecoration(labelText: 'Reason to load over capacity')),
                  ]),
                ),
              ],
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 12, 20, 0),
                  child: Text(_error!, style: const TextStyle(color: AppColors.bad)),
                ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: FilledButton(onPressed: _saving ? null : _save, child: Text(_saving ? 'Saving…' : action)),
              ),
            ]),
    );
  }
}

String _when(String iso) {
  final t = DateTime.tryParse(iso)?.toLocal();
  if (t == null) return '';
  const m = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
  return '${t.day} ${m[t.month - 1]}, $h:${t.minute.toString().padLeft(2, '0')} ${t.hour < 12 ? 'am' : 'pm'}';
}

// ================================================================== one truck (delivery order)
class DeliveryOrderScreen extends StatefulWidget {
  const DeliveryOrderScreen({super.key, required this.doId, this.initial});

  final String doId;

  /// The truck as the server just returned it (after planning), so the screen opens without another fetch.
  final Json? initial;

  @override
  State<DeliveryOrderScreen> createState() => _DeliveryOrderScreenState();
}

class _DeliveryOrderScreenState extends State<DeliveryOrderScreen> {
  Future<Json>? _future;
  bool _changed = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= widget.initial != null ? Future.value(widget.initial!) : _load();
  }

  Future<Json> _load() async => await SessionScope.read(context).api.get('/delivery-orders/${widget.doId}') as Json;

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    try {
      await f;
    } catch (_) {}
  }

  /// Runs one step, then goes straight back to the order, which refreshes to show where the truck is now
  /// (loaded → sent out → delivered). A short note there says what comes next.
  Future<void> _step(Json d, String step) async {
    final updated = await showModalBottomSheet<Json>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => _StepSheet(d: d, step: step),
    );
    if (updated == null || !mounted) return;
    _changed = true;
    final can = List<String>.from(updated['can'] as List);
    final note = switch (step) {
      'load' => can.contains('dispatch')
          ? '${updated['vehicle_no']} loaded. Open the truck below to send it out.'
          : '${updated['vehicle_no']} loaded.',
      'dispatch' => '${updated['vehicle_no']} sent out. Open the truck below to record delivery.',
      'deliver' => '${updated['vehicle_no']} delivered.',
      _ => '${updated['vehicle_no']} cancelled.',
    };
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(note)));
    if (step == 'deliver') {
      // Delivered: the job is done, so close the truck and the order and go back to Home.
      Navigator.of(context).popUntil((r) => r.isFirst);
      goHome.value++;
    } else {
      Navigator.of(context).pop(true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.of(context).pop(_changed);
      },
      child: Scaffold(
        backgroundColor: AppColors.concrete,
        appBar: AppBar(title: const Text('Truck')),
        body: FutureBuilder<Json>(
          future: _future,
          builder: (context, snap) {
            final busy = snap.connectionState != ConnectionState.done;
            if (busy && !snap.hasData) return const SkeletonList(rows: 4);
            if (!busy && snap.hasError) {
              return EmptyState(icon: Icons.cloud_off_outlined, title: 'Could not load', message: '${snap.error}');
            }
            final d = snap.data!;
            final can = List<String>.from(d['can'] as List);
            final items = List<Json>.from(d['items'] as List);
            return RefreshIndicator(
              onRefresh: _refresh,
              child: ListView(padding: const EdgeInsets.only(bottom: 32), children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 16, 20, 0),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('${d['vehicle_no']}', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                    if (d['source'] == 'DIRECT')
                      const Text('Direct from the factory truck', style: TextStyle(color: AppColors.warn, fontWeight: FontWeight.w600)),
                    Gap.xs,
                    Text('${d['do_no']} · ${d['dealer_name']}', style: const TextStyle(color: AppColors.steel)),
                    Gap.s,
                    doChip(d['status'] as String),
                  ]),
                ),
                const SectionTitle('Details'),
                Panel(
                  child: Column(children: [
                    KeyValueRow('From godown', '${d['godown_name']}'),
                    KeyValueRow('Driver', d['driver_name'] == null ? 'Not set' : '${d['driver_name']} · ${d['driver_phone']}'),
                    if (d['site_name'] != null) KeyValueRow('Site', '${d['site_name']}'),
                    if (d['supplier_ref'] != null) KeyValueRow('Factory ref', '${d['supplier_ref']}'),
                    if (d['recipient_name'] != null) KeyValueRow('Received by', '${d['recipient_name']}'),
                  ]),
                ),
                if (d['delivery_address'] != null) ...[
                  const SectionTitle('Deliver to'),
                  Panel(
                    child: ListTile(
                      title: Text('${d['delivery_address']}'),
                      trailing: IconButton(
                        tooltip: 'Open in Maps',
                        icon: const Icon(Icons.map_outlined),
                        onPressed: () => openMaps(d['delivery_address'] as String),
                      ),
                    ),
                  ),
                ],
                const SectionTitle('Material'),
                Panel(
                  child: Column(children: [
                    for (final (i, it) in items.indexed) ...[
                      if (i > 0) const Divider(height: 1),
                      ListTile(
                        title: Text('${it['product_name']}', style: const TextStyle(fontWeight: FontWeight.w600)),
                        subtitle: Text('Planned ${_num(it['planned_qty'])} · sent ${_num(it['dispatched_qty'])}'
                            ' · delivered ${_num(it['delivered_qty'])} ${it['uom']}'),
                      ),
                    ],
                  ]),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    if (can.contains('load'))
                      FilledButton(onPressed: () => _step(d, 'load'), child: const Text('Record loading')),
                    if (can.contains('dispatch')) ...[
                      Gap.s,
                      can.contains('load')
                          ? OutlinedButton(
                              onPressed: () => _step(d, 'dispatch'), child: const Text('Send out without loading slip'))
                          : FilledButton(onPressed: () => _step(d, 'dispatch'), child: const Text('Send out (gate pass)')),
                    ],
                    if (can.contains('deliver')) ...[
                      Gap.s,
                      FilledButton(onPressed: () => _step(d, 'deliver'), child: const Text('Record delivery')),
                    ],
                    if (can.contains('cancel')) ...[
                      Gap.s,
                      TextButton(
                        onPressed: () => _step(d, 'cancel'),
                        child: const Text('Cancel this truck', style: TextStyle(color: AppColors.bad)),
                      ),
                    ],
                  ]),
                ),
              ]),
            );
          },
        ),
      ),
    );
  }
}

/// Loading, gate-out, delivery and cancel: confirm quantities line by line (defaults: as planned / as sent).
class _StepSheet extends StatefulWidget {
  const _StepSheet({required this.d, required this.step});

  final Json d;
  final String step;

  @override
  State<_StepSheet> createState() => _StepSheetState();
}

class _StepSheetState extends State<_StepSheet> {
  final _qty = <String, TextEditingController>{};
  final _bad = <String, TextEditingController>{};
  final _recipient = TextEditingController();
  final _remarks = TextEditingController();
  List<Json> _drivers = [];
  String? _driverId;
  bool _busy = false;
  String? _error;
  bool _allowMore = false;
  bool _closeRest = false;
  bool _gapIsShort = false; // deliver: the undelivered part was lost on the way instead of coming back
  DateTime _day = DateTime.now(); // deliver: the day it was delivered (earlier if entered late)

  static String _ymd(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  bool get _isToday => _ymd(_day) == _ymd(DateTime.now());

  static const _titles = {
    'load': 'Record loading',
    'dispatch': 'Send out',
    'deliver': 'Record delivery',
    'cancel': 'Cancel this truck',
  };

  List<Json> get _items => List<Json>.from(widget.d['items'] as List);

  static double _d(Object? v) => double.tryParse('${v ?? ''}'.trim()) ?? 0;

  double _start(Json it) => _d(widget.step == 'deliver'
      ? it['dispatched_qty']
      : widget.step == 'dispatch'
          ? (it['loaded_qty'] ?? it['planned_qty'])
          : it['planned_qty']);

  /// Deliver: sent − delivered − damaged. Positive = not delivered (came back or short); negative = more than sent.
  double _gap(Json it) => _d(it['dispatched_qty']) - _d(_qty[it['id']]!.text) - _d(_bad[it['id']]!.text);

  List<String> get _more {
    if (widget.step == 'deliver') {
      return [
        for (final it in _items)
          if (_gap(it) < -0.0005) '${it['product_name']}: ${_num(-_gap(it))} ${it['uom']} more than the ${_num(it['dispatched_qty'])} sent',
      ];
    }
    if (widget.step != 'dispatch') return const [];
    return [
      for (final it in _items)
        if (_d(_qty[it['id']]!.text) > _start(it) + 0.0005 || _d(_qty[it['id']]!.text) > _d(it['order_open_qty']) + 0.0005)
          '${it['product_name']}: ${_num(_qty[it['id']]!.text)} ${it['uom']} is more than '
              '${_d(_qty[it['id']]!.text) > _d(it['order_open_qty']) ? 'left on the order' : 'planned'}',
    ];
  }

  List<String> get _less {
    if (widget.step != 'deliver' && widget.step != 'dispatch') return const [];
    return [
      for (final it in _items)
        if (_rest(it) > 0.0005) '${_num(_rest(it))} ${it['uom']} ${it['product_name']}',
    ];
  }

  double _rest(Json it) => widget.step == 'deliver'
      ? _d(it['order_open_qty']) + (!_gapIsShort && _gap(it) > 0 ? _gap(it) : 0)
      : _d(it['order_open_qty']) - _d(_qty[it['id']]!.text);

  @override
  void initState() {
    super.initState();
    _driverId = widget.d['driver_id'] as String?;
    for (final it in _items) {
      final start = widget.step == 'deliver'
          ? it['dispatched_qty']
          : widget.step == 'dispatch'
              ? (it['loaded_qty'] ?? it['planned_qty'])
              : it['planned_qty'];
      _qty[it['id'] as String] = TextEditingController(text: _num(start));
      _bad[it['id'] as String] = TextEditingController(text: '0');
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (widget.step == 'dispatch' && _drivers.isEmpty) _loadDrivers();
  }

  Future<void> _loadDrivers() async {
    try {
      final r = await SessionScope.read(context).api.get('/drivers', query: {'page_size': 200}) as Json;
      if (mounted) {
        setState(() => _drivers = List<Json>.from(r['items'] as List).where((d) => d['is_active'] == true).toList());
      }
    } on ApiException catch (_) {}
  }

  Future<void> _submit() async {
    final api = SessionScope.read(context).api;
    final id = widget.d['id'];
    final remarks = _remarks.text.trim().isEmpty ? null : _remarks.text.trim();
    setState(() {
      _busy = true;
      _error = null;
    });
    Object? result;
    try {
      switch (widget.step) {
        case 'load':
          result = await api.post('/delivery-orders/$id/load', body: {
            'remarks': remarks,
            'lines': [for (final it in _items) {'do_item_id': it['id'], 'qty': _qty[it['id']]!.text.trim()}],
          });
        case 'dispatch':
          if (_more.isNotEmpty && !_allowMore) {
            throw ApiException(422, 'VALIDATION_ERROR', 'Tick "Send more" to increase the order, or correct the quantity');
          }
          result = await api.post('/delivery-orders/$id/dispatch', body: {
            'driver_id': _driverId,
            'remarks': remarks,
            'lines': [for (final it in _items) {'do_item_id': it['id'], 'qty': _qty[it['id']]!.text.trim()}],
            'allow_more': _more.isNotEmpty && _allowMore,
            'close_rest': _less.isNotEmpty && _closeRest,
          });
        case 'deliver':
          if (_recipient.text.trim().isEmpty) throw ApiException(422, 'VALIDATION_ERROR', 'Enter who received it');
          if (_more.isNotEmpty && !_allowMore) {
            throw ApiException(422, 'VALIDATION_ERROR', 'Tick "Send more" to increase the order, or correct the quantity');
          }
          final lines = <Json>[];
          for (final it in _items) {
            final got = double.tryParse(_qty[it['id']]!.text.trim()) ?? -1;
            final damaged = double.tryParse(_bad[it['id']]!.text.trim().isEmpty ? '0' : _bad[it['id']]!.text.trim()) ?? -1;
            if (got < 0 || damaged < 0) {
              throw ApiException(422, 'VALIDATION_ERROR', '${it['product_name']}: enter the quantity delivered');
            }
            final gap = _gap(it) > 0 ? _gap(it) : 0.0;
            lines.add({
              'do_item_id': it['id'],
              'delivered_qty': got.toStringAsFixed(3),
              'damaged_qty': damaged.toStringAsFixed(3),
              'short_qty': (_gapIsShort ? gap : 0).toStringAsFixed(3),
              'returned_qty': (_gapIsShort ? 0 : gap).toStringAsFixed(3),
            });
          }
          result = await api.post('/delivery-orders/$id/deliver', body: {
            'recipient_name': _recipient.text.trim(),
            'remarks': remarks,
            'delivered_on': _isToday ? null : _ymd(_day),
            'lines': lines,
            'allow_more': _more.isNotEmpty && _allowMore,
            'close_rest': _less.isNotEmpty && _closeRest,
          });
        case 'cancel':
          result = await api.post('/delivery-orders/$id/cancel', body: {'reason': remarks ?? ''});
      }
      if (mounted) Navigator.of(context).pop(result as Json);
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
    final step = widget.step;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 20, 20, 20 + MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(_titles[step]!, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
          Gap.m,
          if (step != 'cancel')
            for (final it in _items)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(children: [
                  Expanded(child: Text('${it['product_name']}', style: const TextStyle(fontWeight: FontWeight.w600))),
                  SizedBox(
                    width: 100,
                    child: TextField(
                      controller: _qty[it['id']],
                      keyboardType: const TextInputType.numberWithOptions(decimal: true),
                      decoration: InputDecoration(
                          labelText: step == 'deliver' ? 'Delivered' : 'Qty', suffixText: it['uom'] as String),
                      onChanged: (_) => setState(() {}),
                    ),
                  ),
                  if (step == 'deliver') ...[
                    Gap.s,
                    SizedBox(
                      width: 90,
                      child: TextField(
                        controller: _bad[it['id']],
                        keyboardType: const TextInputType.numberWithOptions(decimal: true),
                        decoration: const InputDecoration(labelText: 'Damaged'),
                        onChanged: (_) => setState(() {}),
                      ),
                    ),
                  ],
                ]),
              ),
          if (step == 'dispatch')
            DropdownButtonFormField<String?>(
              isExpanded: true,
              value: _drivers.any((d) => d['id'] == _driverId) ? _driverId : null,
              decoration: const InputDecoration(labelText: 'Driver'),
              items: [
                for (final d in _drivers)
                  DropdownMenuItem<String?>(value: d['id'] as String, child: Text('${d['name']} · ${d['phone']}')),
              ],
              onChanged: (v) => setState(() => _driverId = v),
            ),
          if (step == 'deliver')
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.event_outlined),
              title: Text(_isToday ? 'Delivered today' : 'Delivered on ${_day.day}/${_day.month}/${_day.year}',
                  style: const TextStyle(fontWeight: FontWeight.w600)),
              subtitle: Text(_isToday ? 'Tap to pick an earlier day' : 'The sale and Tally voucher use this date'),
              trailing: const Icon(Icons.edit_calendar_outlined),
              onTap: () async {
                final now = DateTime.now();
                final d = await showDatePicker(
                    context: context,
                    initialDate: _day,
                    firstDate: now.subtract(const Duration(days: 365)),
                    lastDate: now,
                    helpText: 'Delivered on');
                if (d != null) setState(() => _day = d);
              },
            ),
          if (step == 'deliver')
            TextField(controller: _recipient, decoration: const InputDecoration(labelText: 'Received by')),
          TextField(
            controller: _remarks,
            decoration: InputDecoration(labelText: step == 'cancel' ? 'Reason' : 'Remarks (optional)'),
          ),
          if (step == 'deliver' && _items.any((it) => _gap(it) > 0.0005)) ...[
            Gap.s,
            Text(
                'Not delivered: ${[
                  for (final it in _items)
                    if (_gap(it) > 0.0005) '${_num(_gap(it))} ${it['uom']} ${it['product_name']}'
                ].join(', ')}',
                style: const TextStyle(fontWeight: FontWeight.w600)),
            RadioListTile<bool>(
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: false,
              groupValue: _gapIsShort,
              onChanged: (v) => setState(() => _gapIsShort = v!),
              title: const Text('Came back with the truck (dealer took less)'),
            ),
            RadioListTile<bool>(
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: true,
              groupValue: _gapIsShort,
              onChanged: (v) => setState(() => _gapIsShort = v!),
              title: const Text('Short / lost on the way'),
            ),
          ],
          if (step == 'deliver' || step == 'dispatch')
            QtyChoice(
              more: _more,
              less: _less,
              allowMore: _allowMore,
              closeRest: _closeRest,
              lessWord: step == 'deliver' ? 'is still to be sent' : 'will be left',
              onAllowMore: (v) => setState(() => _allowMore = v),
              onCloseRest: (v) => setState(() => _closeRest = v),
            ),
          if (_error != null)
            Padding(padding: const EdgeInsets.only(top: 8), child: Text(_error!, style: const TextStyle(color: AppColors.bad))),
          Gap.m,
          FilledButton(
            onPressed: _busy || (step == 'dispatch' && _driverId == null && widget.d['source'] == 'OWN') ? null : _submit,
            child: Text(_busy ? 'Saving…' : _titles[step]!),
          ),
        ]),
      ),
    );
  }
}


/// When the quantity going out differs from the order: confirm sending more (the order is increased), and say
/// whether the rest is still to be sent or the dealer doesn't want it (closed).
class QtyChoice extends StatelessWidget {
  const QtyChoice({
    super.key,
    required this.more,
    required this.less,
    required this.allowMore,
    required this.closeRest,
    required this.onAllowMore,
    required this.onCloseRest,
    this.lessWord = 'will be left',
  });

  final List<String> more;
  final List<String> less;
  final bool allowMore;
  final bool closeRest;
  final ValueChanged<bool> onAllowMore;
  final ValueChanged<bool> onCloseRest;
  final String lessWord;

  @override
  Widget build(BuildContext context) {
    if (more.isEmpty && less.isEmpty) return const SizedBox.shrink();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (more.isNotEmpty)
        Container(
          margin: const EdgeInsets.only(top: 8),
          decoration: BoxDecoration(
              color: allowMore ? AppColors.concrete : AppColors.warn.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(8)),
          child: CheckboxListTile(
            dense: true,
            controlAffinity: ListTileControlAffinity.leading,
            value: allowMore,
            onChanged: (v) => onAllowMore(v ?? false),
            title: Text(more.join('\n')),
            subtitle: const Text('Send more — increase the order (same rate)',
                style: TextStyle(fontWeight: FontWeight.w700)),
          ),
        ),
      if (less.isNotEmpty)
        Container(
          margin: const EdgeInsets.only(top: 8),
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
          decoration: BoxDecoration(color: AppColors.concrete, borderRadius: BorderRadius.circular(8)),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${less.join(', ')} of the order $lessWord:'),
            RadioListTile<bool>(
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: false,
              groupValue: closeRest,
              onChanged: (v) => onCloseRest(v!),
              title: const Text('Keep it — it will go on another truck'),
            ),
            RadioListTile<bool>(
              dense: true,
              contentPadding: EdgeInsets.zero,
              value: true,
              groupValue: closeRest,
              onChanged: (v) => onCloseRest(v!),
              title: const Text("Close it — the dealer doesn't want it"),
            ),
          ]),
        ),
    ]);
  }
}
