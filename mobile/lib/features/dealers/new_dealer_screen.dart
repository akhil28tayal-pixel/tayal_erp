import 'package:flutter/material.dart';
import '../../core/api.dart';
import '../../core/session.dart';
import '../../core/ui.dart';

typedef Json = Map<String, dynamic>;

/// Add a dealer who isn't in Tally yet, while booking. The ERP also makes them as a ledger in Tally (right away,
/// or at the next Tally sync if Tally isn't open). Pops with the new dealer (or an existing one with that name).
class NewDealerScreen extends StatefulWidget {
  const NewDealerScreen({super.key, this.startName = ''});

  final String startName;

  @override
  State<NewDealerScreen> createState() => _NewDealerScreenState();
}

class _NewDealerScreenState extends State<NewDealerScreen> {
  late final _name = TextEditingController(text: widget.startName);
  final _mobile = TextEditingController();
  final _contact = TextEditingController();
  final _address = TextEditingController();
  final _city = TextEditingController();
  final _state = TextEditingController(text: 'Delhi');
  final _pin = TextEditingController();
  final _gstin = TextEditingController();
  List<String> _groups = const ['Sundry Debtors'];
  String? _group;
  bool _busy = false;
  Json? _exists;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      try {
        final g = await SessionScope.read(context).api.get('/dealers/tally-groups') as List;
        if (mounted && g.isNotEmpty) setState(() => _groups = List<String>.from(g));
      } on ApiException {
        // keep "Sundry Debtors"
      }
    });
  }

  String? _clean(TextEditingController c) => c.text.trim().isEmpty ? null : c.text.trim();

  Future<void> _save() async {
    final name = _name.text.trim();
    if (name.length < 2) {
      showError(context, ApiException(422, 'VALIDATION_ERROR', 'Enter the dealer name'));
      return;
    }
    setState(() {
      _busy = true;
      _exists = null;
    });
    try {
      final r = await SessionScope.read(context).api.post('/dealers', body: {
        'name': name,
        'mobile': _clean(_mobile),
        'contact_name': _clean(_contact),
        'address': _clean(_address),
        'city': _clean(_city),
        'state': _clean(_state),
        'pincode': _clean(_pin),
        'gstin': _clean(_gstin)?.toUpperCase(),
        'group': _group,
        'make_in_tally': true,
      }) as Json;
      if (!mounted) return;
      final d = r['dealer'] as Json;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${d['name']} added. ${r['tally']}')));
      Navigator.of(context).pop(d);
    } on ApiException catch (e) {
      if (!mounted) return;
      if (e.code == 'DEALER_EXISTS') {
        try {
          final d = await SessionScope.read(context).api.get('/dealers', query: {'q': name, 'page_size': 5}) as Json;
          final hit = List<Json>.from(d['items'] as List)
              .where((x) => '${x['name']}'.toLowerCase() == name.toLowerCase().replaceAll(RegExp(r'\s+'), ' '))
              .toList();
          if (mounted) setState(() => _exists = hit.isNotEmpty ? hit.first : null);
        } on ApiException {
          // fall through to the message
        }
      }
      if (mounted) showError(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _field(TextEditingController c, String label, {TextInputType? type, bool caps = false}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextField(
          controller: c,
          keyboardType: type,
          textCapitalization: caps ? TextCapitalization.characters : TextCapitalization.words,
          decoration: InputDecoration(labelText: label),
        ),
      );

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.concrete,
      appBar: AppBar(title: const Text('New dealer')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        const Text("For a dealer who isn't in Tally yet. They are added here and made as a ledger in Tally "
            "(right away, or at the next Tally sync if Tally isn't open).",
            style: TextStyle(color: AppColors.steel)),
        Gap.m,
        _field(_name, 'Name, as it should be in Tally'),
        _field(_mobile, 'Mobile (for WhatsApp)', type: TextInputType.phone),
        _field(_contact, 'Contact person (optional)'),
        _field(_address, 'Address (optional)'),
        _field(_city, 'City / area'),
        Row(children: [
          Expanded(child: _field(_state, 'State')),
          const SizedBox(width: 12),
          Expanded(child: _field(_pin, 'PIN code', type: TextInputType.number)),
        ]),
        _field(_gstin, 'GSTIN (if registered)', caps: true),
        DropdownButtonFormField<String>(
          initialValue: _group ?? _groups.first,
          decoration: const InputDecoration(labelText: 'Tally group'),
          items: [for (final g in _groups) DropdownMenuItem(value: g, child: Text(g))],
          onChanged: (v) => setState(() => _group = v),
        ),
        if (_exists != null) ...[
          Gap.m,
          Card(
            child: ListTile(
              title: Text('${_exists!['name']} is already a dealer'),
              subtitle: const Text('Tap to book for them'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.of(context).pop(_exists),
            ),
          ),
        ],
        Gap.l,
        FilledButton(
          onPressed: _busy ? null : _save,
          child: Text(_busy ? 'Adding…' : 'Add dealer'),
        ),
      ]),
    );
  }
}
