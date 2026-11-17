import 'package:flutter/material.dart';

import '../../core/api.dart';
import '../../core/session.dart';
import '../../core/ui.dart';

/// Products switched on in the ERP, grouped by brand, with a quantity converter that uses the server's
/// rules (the same ones orders and stock use).
class ProductsScreen extends StatefulWidget {
  const ProductsScreen({super.key});

  @override
  State<ProductsScreen> createState() => _ProductsScreenState();
}

class _Row {
  const _Row.header(this.title) : product = null;
  const _Row.item(this.product) : title = null;

  final String? title;
  final Map<String, dynamic>? product;
}

class _ProductsScreenState extends State<ProductsScreen> {
  Future<List<Map<String, dynamic>>>? _future;
  Map<String, String> _brands = {};
  String _q = '';
  String _category = '';

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _future ??= _load();
  }

  Future<List<Map<String, dynamic>>> _load() async {
    final api = SessionScope.read(context).api;
    final r = await Future.wait([
      api.get('/products', query: {'page_size': 200, 'erp_enabled': 'true'}),
      api.get('/brands', query: {'page_size': 200}),
    ]);
    _brands = {
      for (final b in ((r[1] as Map<String, dynamic>)['items'] as List).cast<Map<String, dynamic>>())
        b['id'] as String: b['name'] as String,
    };
    return List<Map<String, dynamic>>.from((r[0] as Map<String, dynamic>)['items'] as List);
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _future = f);
    await f;
  }

  String _brand(Map<String, dynamic> p) => _brands[p['brand_id']] ?? 'Other';

  /// Flatten into brand headers + products, after search and category filters.
  List<_Row> _rows(List<Map<String, dynamic>> all) {
    final items = all
        .where((p) => _category.isEmpty || p['category'] == _category)
        .where((p) => _q.isEmpty || (p['name'] as String).toLowerCase().contains(_q) || _brand(p).toLowerCase().contains(_q))
        .toList()
      ..sort((a, b) {
        final c = _brand(a).compareTo(_brand(b));
        if (c != 0) return c;
        final da = (a['diameter_mm'] as int?) ?? 0, db = (b['diameter_mm'] as int?) ?? 0;
        return da != db ? da - db : (a['name'] as String).compareTo(b['name'] as String);
      });
    final rows = <_Row>[];
    String? last;
    for (final p in items) {
      final b = _brand(p);
      if (b != last) {
        rows.add(_Row.header(b));
        last = b;
      }
      rows.add(_Row.item(p));
    }
    return rows;
  }

  @override
  Widget build(BuildContext context) {
    return Column(children: [
      ScreenHeader(
        title: 'Products',
        subtitle: 'Tap a product to convert quantities',
        bottom: Column(children: [
          HeaderSearch(hint: 'Search product or brand', onChanged: (v) => setState(() => _q = v.trim().toLowerCase())),
          Gap.m,
          Row(children: [
            for (final (value, label) in const [('', 'All'), ('CEMENT', 'Cement'), ('TMT', 'TMT')]) ...[
              _FilterPill(label: label, selected: _category == value, onTap: () => setState(() => _category = value)),
              Gap.s,
            ],
          ]),
        ]),
      ),
      Expanded(
        child: FutureBuilder<List<Map<String, dynamic>>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) return const SkeletonList();
            if (snap.hasError) {
              final e = snap.error;
              return RefreshIndicator(
                onRefresh: _refresh,
                child: ListView(children: [
                  EmptyState(
                    icon: Icons.cloud_off_outlined,
                    title: 'Could not load',
                    message: e is ApiException ? e.detail : 'Pull down to try again.',
                  ),
                ]),
              );
            }
            final rows = _rows(snap.data!);
            return RefreshIndicator(
              color: AppColors.blue,
              onRefresh: _refresh,
              child: rows.isEmpty
                  ? ListView(children: [
                      EmptyState(
                        icon: Icons.inventory_2_outlined,
                        title: snap.data!.isEmpty ? 'No products switched on' : 'Nothing matches',
                        message: snap.data!.isEmpty ? 'Products are switched on from the web dashboard.' : null,
                      ),
                    ])
                  : ListView.builder(
                      padding: const EdgeInsets.only(bottom: 24),
                      itemCount: rows.length,
                      itemBuilder: (context, i) {
                        final r = rows[i];
                        if (r.title != null) return SectionTitle(r.title!);
                        return _ProductTile(product: r.product!, brand: _brand(r.product!));
                      },
                    ),
            );
          },
        ),
      ),
    ]);
  }
}

class _FilterPill extends StatelessWidget {
  const _FilterPill({required this.label, required this.selected, required this.onTap});

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: selected ? AppColors.hivis : AppColors.inkSoft,
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Text(label,
              style: TextStyle(
                  color: selected ? AppColors.blue : Colors.white, fontWeight: FontWeight.w700, fontSize: 13)),
        ),
      ),
    );
  }
}

class _ProductTile extends StatelessWidget {
  const _ProductTile({required this.product, required this.brand});

  final Map<String, dynamic> product;
  final String brand;

  @override
  Widget build(BuildContext context) {
    final p = product;
    final (icon, detail) = switch (p['category']) {
      'CEMENT' => (Icons.shopping_bag_outlined,
          '${qty(p['bag_weight_kg'] as String?)} kg bag${p['cement_grade'] != null ? '  •  ${p['cement_grade']}' : ''}'),
      'TMT' => (Icons.straighten, '${p['diameter_mm']} mm${p['tmt_grade'] != null ? '  •  ${p['tmt_grade']}' : ''}'),
      _ => (Icons.category_outlined, 'per ${(p['base_uom'] as String).toLowerCase()}'),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 3),
      child: Material(
        color: AppColors.slab,
        borderRadius: BorderRadius.circular(12),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => showModalBottomSheet(
            context: context,
            isScrollControlled: true,
            builder: (_) => _ConvertSheet(product: p, brand: brand),
          ),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
            decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), border: Border.all(color: AppColors.seam)),
            child: Row(children: [
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(color: AppColors.concrete, borderRadius: BorderRadius.circular(10)),
                child: Icon(icon, color: AppColors.steel, size: 22),
              ),
              Gap.m,
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(p['name'] as String, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                  Gap.xs,
                  Text(detail, style: const TextStyle(color: AppColors.steel, fontSize: 13)),
                ]),
              ),
              const Icon(Icons.swap_horiz, color: AppColors.mist),
            ]),
          ),
        ),
      ),
    );
  }
}

class _ConvertSheet extends StatefulWidget {
  const _ConvertSheet({required this.product, required this.brand});

  final Map<String, dynamic> product;
  final String brand;

  @override
  State<_ConvertSheet> createState() => _ConvertSheetState();
}

class _ConvertSheetState extends State<_ConvertSheet> {
  final _qty = TextEditingController(text: '1');
  late final List<String> _units;
  late String _from;
  late String _to;
  String? _result;
  String? _error;
  bool _busy = false;

  static const _unitNames = {
    'BAG': 'Bags', 'MT': 'Tonnes (MT)', 'KG': 'Kg', 'QTL': 'Quintal', 'PC': 'Pieces', 'BDL': 'Bundles',
    'M': 'Metres', 'CUM': 'Cubic metre', 'FT': 'Ft', 'LTR': 'Litres', 'PKT': 'Packets', 'NOS': 'Numbers',
    'RKS': 'Rickshaw loads', 'TRIP': 'Trips',
  };

  @override
  void initState() {
    super.initState();
    final p = widget.product;
    final base = p['base_uom'] as String;
    _units = {base, ...((p['units'] as List).map((u) => (u as Map<String, dynamic>)['uom'] as String))}.toList();
    _from = _units.contains('MT') ? 'MT' : _units.first;
    _to = _units.contains('MT') && base != 'MT' ? base : (_units.length > 1 ? _units[1] : base);
  }

  @override
  void dispose() {
    _qty.dispose();
    super.dispose();
  }

  Future<void> _convert() async {
    setState(() {
      _busy = true;
      _result = null;
      _error = null;
    });
    try {
      final r = await SessionScope.read(context).api.post('/products/convert', body: {
        'product_id': widget.product['id'],
        'qty': _qty.text.trim(),
        'from_uom': _from,
        'to_uom': _to,
      }) as Map<String, dynamic>;
      setState(() => _result = '${qty(r['result'] as String)} ${_unitNames[r['to_uom']] ?? r['to_uom']}');
    } on ApiException catch (e) {
      setState(() => _error = e.detail);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  DropdownButtonFormField<String> _unitPicker(String label, String value, ValueChanged<String> onChanged) {
    return DropdownButtonFormField<String>(
      key: ValueKey('$label-$value'), // rebuild when units are swapped
      value: value,
      isExpanded: true,
      decoration: InputDecoration(labelText: label),
      items: [for (final u in _units) DropdownMenuItem(value: u, child: Text(_unitNames[u] ?? u))],
      onChanged: (v) {
        if (v != null) onChanged(v);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, 24 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(widget.brand, style: const TextStyle(color: AppColors.steel, fontWeight: FontWeight.w600)),
        Gap.xs,
        Text(widget.product['name'] as String, style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
        Gap.l,
        TextField(
          controller: _qty,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
          decoration: const InputDecoration(labelText: 'Quantity'),
          onSubmitted: (_) => _convert(),
        ),
        Gap.m,
        Row(children: [
          Expanded(child: _unitPicker('From', _from, (v) => setState(() => _from = v))),
          IconButton(
            tooltip: 'Swap units',
            icon: const Icon(Icons.swap_horiz),
            onPressed: () => setState(() {
              final t = _from;
              _from = _to;
              _to = t;
              _result = null;
            }),
          ),
          Expanded(child: _unitPicker('To', _to, (v) => setState(() => _to = v))),
        ]),
        Gap.l,
        FilledButton(onPressed: _busy ? null : _convert, child: Text(_busy ? 'Converting…' : 'Convert')),
        if (_result != null || _error != null) ...[
          Gap.l,
          Container(
            padding: const EdgeInsets.all(18),
            decoration: BoxDecoration(
              color: _error != null ? AppColors.bad.withValues(alpha: 0.08) : AppColors.blue,
              borderRadius: BorderRadius.circular(14),
            ),
            child: _error != null
                ? Text(_error!, style: const TextStyle(color: AppColors.bad))
                : Column(children: [
                    Text('${_qty.text.trim()} ${_unitNames[_from] ?? _from} =',
                        style: const TextStyle(color: Colors.white70)),
                    Gap.xs,
                    Text(_result!,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: AppColors.hivis, fontSize: 28, fontWeight: FontWeight.w800)),
                  ]),
          ),
        ],
      ]),
    );
  }
}
