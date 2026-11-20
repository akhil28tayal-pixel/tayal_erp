import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/notifier.dart';
import '../../core/offline.dart';
import '../../core/session.dart';
import '../../core/share_in.dart';
import '../../core/ui.dart';
import '../../core/updater.dart';
import '../dealers/dealers_screen.dart';
import '../dispatch/lists_screens.dart';
import '../extras/extras_screens.dart';
import '../more/more_screen.dart';
import '../orders/orders_screen.dart';
import '../products/products_screen.dart';
import '../wa_orders/wa_orders_screen.dart';

const roleNames = {
  'SUPER_ADMIN': 'Super admin',
  'BUSINESS_OWNER': 'Owner',
  'OPERATIONS_MANAGER': 'Operations',
  'ACCOUNTS_EXECUTIVE': 'Accounts',
  'SALES_EXECUTIVE': 'Sales',
  'COLLECTION_EXECUTIVE': 'Collections',
  'GODOWN_SUPERVISOR': 'Godown supervisor',
  'DRIVER': 'Driver',
};

/// Bottom navigation built from the user's permissions in the current company (the server enforces them
/// anyway; hiding tabs keeps the app focused on each person's job).
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _Tab {
  const _Tab(this.label, this.icon, this.selectedIcon, this.page);

  final String label;
  final IconData icon;
  final IconData selectedIcon;
  final Widget page;
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  int _index = 0;
  bool _started = false;

  @override
  void initState() {
    super.initState();
    goHome.addListener(_toHome);
    WidgetsBinding.instance.addObserver(this);
    // A tapped phone notification opens its order (or truck / dealer) on top of whatever is showing.
    openLink = (link) {
      final ctx = appNavigator.currentContext;
      if (ctx != null) openServerLink(ctx, link);
    };
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_started) return;
    _started = true;
    final s = SessionScope.read(context);
    // Phone notifications, orders booked without network, and the phone's copy of dealers and materials.
    unawaited(Notifier.instance.start(s.api, '${s.userName}|${s.company!.id}'));
    unawaited(Offline.instance.start(s.api));
    unawaited(Offline.refreshCaches(s.api));
    // A WhatsApp message shared to the app (Share → Tayal ERP) opens the WhatsApp orders screen with it.
    ShareIn.onShared = _checkShared;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _checkShared();
      // A newer version of the app published on the server: offer it (once per app start).
      if (mounted) unawaited(Updater.check(context, s.api));
    });
  }

  Future<void> _checkShared() async {
    final text = await ShareIn.take();
    if (text == null || !mounted || !SessionScope.read(context).can('orders.create')) return;
    appNavigator.currentState?.push(MaterialPageRoute(builder: (_) => WhatsAppOrdersScreen(sharedText: text)));
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // back in the app: check for news at once and send anything booked without network
      unawaited(Notifier.instance.poll());
      unawaited(Offline.instance.flush());
      unawaited(_checkShared());
    }
  }

  @override
  void dispose() {
    goHome.removeListener(_toHome);
    WidgetsBinding.instance.removeObserver(this);
    Notifier.instance.stop();
    Offline.instance.stop();
    super.dispose();
  }

  void _toHome() {
    if (mounted) setState(() => _index = 0);
  }

  List<_Tab> _tabs(Session s) => [
        _Tab('Home', Icons.space_dashboard_outlined, Icons.space_dashboard, _TodayScreen(onOpenTab: _openTab)),
        if (s.can('orders.view'))
          const _Tab('Orders', Icons.receipt_long_outlined, Icons.receipt_long, OrdersScreen()),
        if (s.can('masters.view'))
          const _Tab('Dealers', Icons.storefront_outlined, Icons.storefront, DealersScreen()),
        if (s.can('masters.view'))
          const _Tab('Products', Icons.inventory_2_outlined, Icons.inventory_2, ProductsScreen()),
        const _Tab('Profile', Icons.person_outline, Icons.person, MoreScreen()),
      ];

  void _openTab(String label) {
    final i = _tabs(SessionScope.read(context)).indexWhere((t) => t.label == label);
    if (i >= 0) setState(() => _index = i);
  }

  @override
  Widget build(BuildContext context) {
    final s = SessionScope.of(context);
    final tabs = _tabs(s);
    if (_index >= tabs.length) _index = 0;
    return Scaffold(
      body: IndexedStack(index: _index, children: [for (final t in tabs) t.page]),
      bottomNavigationBar: DecoratedBox(
        decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.seam))),
        child: NavigationBar(
          selectedIndex: _index,
          onDestinationSelected: (i) => setState(() => _index = i),
          destinations: [
            for (final t in tabs)
              NavigationDestination(icon: Icon(t.icon), selectedIcon: Icon(t.selectedIcon), label: t.label),
          ],
        ),
      ),
    );
  }
}

class _TodayScreen extends StatefulWidget {
  const _TodayScreen({required this.onOpenTab});

  final void Function(String tab) onOpenTab;

  @override
  State<_TodayScreen> createState() => _TodayScreenState();
}

/// One coloured tile on the home screen: a count of orders in a stage, tap to see them.
class _Stage {
  const _Stage(this.label, this.icon, this.statuses, this.colors, {this.dark = false, this.days});

  final String label;
  final IconData icon;
  final List<String> statuses;
  final List<Color> colors;
  final bool dark; // dark text on a light tile
  final int? days; // only the last N days (delivered)
}

const _stages = [
  _Stage('Waiting for approval', Icons.how_to_reg, ['UNDER_REVIEW', 'ON_HOLD'], [Color(0xFF1B4FD8), Color(0xFF3D72F2)]),
  _Stage('Ready to send', Icons.inventory, ['APPROVED', 'PARTIALLY_DISPATCHED'], [Color(0xFF0D2A72), Color(0xFF1C428F)]),
  _Stage('On the road', Icons.local_shipping, ['DISPATCHED', 'PARTIALLY_DELIVERED'], [Color(0xFF0B7DD6), Color(0xFF3AA8F5)]),
  _Stage('Delivered', Icons.task_alt, ['DELIVERED', 'CLOSED'], [Color(0xFFFFFFFF), Color(0xFFE6EEFF)],
      dark: true, days: 30),
];

/// Home: big tiles with order counts, one big "Book Order" button, and plain list rows — made to be read
/// at a glance (large text, few words, everything one tap away).
class _TodayScreenState extends State<_TodayScreen> {
  Future<Map<String, int?>>? _counts;

  @override
  void initState() {
    super.initState();
    goHome.addListener(_onGoHome);
  }

  @override
  void dispose() {
    goHome.removeListener(_onGoHome);
    super.dispose();
  }

  void _onGoHome() {
    if (mounted) _refresh();
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _counts ??= _load();
  }

  static String _date(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Future<Map<String, int?>> _load() async {
    final s = SessionScope.read(context);
    final api = s.api;
    Future<int?> total(String path, Map<String, dynamic> q) async {
      try {
        final r = await api.get(path, query: {'page_size': 1, ...q}) as Map<String, dynamic>;
        return r['total'] as int?;
      } catch (_) {
        return null; // one count failing must not blank the screen
      }
    }

    final keys = <String>[];
    final jobs = <Future<int?>>[];
    void add(String k, Future<int?> f) {
      keys.add(k);
      jobs.add(f);
    }

    if (s.can('orders.view')) {
      for (final st in _stages) {
        add(st.label, total('/orders', {
          'status': st.statuses,
          if (st.days != null) 'date_from': _date(DateTime.now().subtract(Duration(days: st.days!))),
        }));
      }
      add('hold', total('/orders', {'status': ['ON_HOLD']}));
      add('cancelled', total('/orders', {'status': ['CANCELLED', 'REJECTED']}));
    }
    if (s.can('masters.view')) add('dealers', total('/dealers', {}));
    if (s.can('orders.view')) {
      add('wa', () async {
        try {
          final r = await api.get('/whatsapp-orders', query: {'status': 'OPEN', 'limit': 100}) as Map<String, dynamic>;
          return (r['items'] as List).length;
        } catch (_) {
          return null;
        }
      }());
    }
    if (s.can('dispatch.view')) {
      add('trucks', () async {
        try {
          return (await api.get('/direct-trucks') as List).length;
        } catch (_) {
          return null;
        }
      }());
    }
    if (s.can('tally.view')) {
      add('tally', () async {
        try {
          final r = await api.get('/tally/sales', query: {'status': 'NONE'}) as Map<String, dynamic>;
          return (r['counts'] as Map<String, dynamic>)['WAITING_APPROVAL'] as int? ?? 0;
        } catch (_) {
          return null;
        }
      }());
    }
    final values = await Future.wait(jobs);
    return {for (var i = 0; i < keys.length; i++) keys[i]: values[i]};
  }

  Future<void> _refresh() async {
    final f = _load();
    setState(() => _counts = f);
    await f;
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));
    if (mounted) _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final s = SessionScope.of(context);
    final c = s.company!;
    return RefreshIndicator(
      color: AppColors.blue,
      onRefresh: _refresh,
      child: FutureBuilder<Map<String, int?>>(
        future: _counts,
        builder: (context, snap) {
          final n = snap.data ?? const {};
          final loading = snap.connectionState != ConnectionState.done;
          String count(String k) => loading ? '…' : (n[k]?.toString() ?? '—');
          return ListView(padding: EdgeInsets.zero, children: [
            ScreenHeader(
              title: 'Namaste, ${s.userName.split(' ').first}',
              trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                IconButton(
                  tooltip: 'Search',
                  iconSize: 30,
                  icon: const Icon(Icons.search, color: Colors.white),
                  onPressed: () => _open(const SearchScreen()),
                ),
                const BellButton(),
              ]),
              bottom: Row(children: [
                const Icon(Icons.business, size: 18, color: AppColors.hivis),
                Gap.s,
                Expanded(
                  child: Text(c.name,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600, fontSize: 15)),
                ),
              ]),
            ),
            ListenableBuilder(
              listenable: Offline.instance,
              builder: (context, _) {
                final box = Offline.instance.outbox;
                if (box.isEmpty) return const SizedBox.shrink();
                final stuck = box.where((x) => x['error'] != null).length;
                return Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                  child: Material(
                    color: stuck > 0 ? AppColors.bad.withValues(alpha: 0.1) : const Color(0xFFFFF4DE),
                    borderRadius: BorderRadius.circular(12),
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: () => _open(const OutboxScreen()),
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Row(children: [
                          Icon(stuck > 0 ? Icons.error_outline : Icons.cloud_upload_outlined,
                              size: 32, color: stuck > 0 ? AppColors.bad : AppColors.warn),
                          Gap.m,
                          Expanded(
                            child: Text(
                              stuck > 0
                                  ? '$stuck order(s) need a decision · tap'
                                  : '${box.length} order(s) waiting for network · will send by itself',
                              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
                            ),
                          ),
                          const Icon(Icons.chevron_right, size: 28),
                        ]),
                      ),
                    ),
                  ),
                );
              },
            ),
            if (s.can('orders.view')) ...[
              const Padding(
                padding: EdgeInsets.fromLTRB(20, 20, 20, 4),
                child: Text('Track orders', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800)),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 20),
                child: Divider(thickness: 2, color: AppColors.seam),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                child: GridView.count(
                  crossAxisCount: 2,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 1.15,
                  children: [
                    for (final st in _stages)
                      _BigTile(
                        stage: st,
                        count: count(st.label),
                        onTap: () => _open(OrderListScreen(
                          title: st.days == null ? st.label : '${st.label} · last ${st.days} days',
                          statuses: st.statuses,
                          days: st.days,
                        )),
                      ),
                  ],
                ),
              ),
            ],
            if (s.can('orders.create'))
              Padding(
                padding: const EdgeInsets.fromLTRB(40, 24, 40, 8),
                child: SizedBox(
                  height: 64,
                  child: FilledButton(
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.blue,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                      textStyle: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
                    ),
                    onPressed: () async {
                      final booked = await Navigator.of(context)
                          .push<bool>(MaterialPageRoute(builder: (_) => const NewOrderScreen()));
                      if (booked == true) _refresh();
                    },
                    child: const Text('Book Order'),
                  ),
                ),
              ),
            Gap.m,
            if (s.can('orders.view'))
              _Row(
                label: 'WhatsApp orders',
                count: count('wa'),
                alert: (n['wa'] ?? 0) > 0,
                onTap: () => _open(const WhatsAppOrdersScreen()),
              ),
            if (s.can('reports.view'))
              _Row(label: 'Daily sales (dealer-wise)', onTap: () => _open(const DailySalesScreen())),
            if (s.can('reports.view'))
              _Row(label: 'Sales report', onTap: () => _open(const SalesReportScreen())),
            if (s.can('orders.view'))
              _Row(
                label: 'On hold',
                count: count('hold'),
                alert: (n['hold'] ?? 0) > 0,
                onTap: () => _open(const OrderListScreen(title: 'On hold', statuses: ['ON_HOLD'])),
              ),
            if (s.can('dispatch.view'))
              _Row(
                label: 'Direct trucks waiting',
                count: count('trucks'),
                onTap: () => _open(const DirectTrucksScreen()),
              ),
            if (s.can('tally.view'))
              _Row(
                label: 'Sales waiting in Tally',
                count: count('tally'),
                alert: (n['tally'] ?? 0) > 0,
                onTap: () => _open(const TallySalesScreen()),
              ),
            if (s.can('orders.view'))
              _Row(
                label: 'Cancelled / rejected orders',
                count: count('cancelled'),
                onTap: () => _open(
                    const OrderListScreen(title: 'Cancelled / rejected', statuses: ['CANCELLED', 'REJECTED'])),
              ),
            if (s.can('masters.view'))
              _Row(label: s.can('dealers.view_all') ? 'Dealers' : 'My dealers', count: count('dealers'),
                  onTap: () => widget.onOpenTab('Dealers')),
            if (s.can('orders.view')) _Row(label: 'All orders', onTap: () => widget.onOpenTab('Orders')),
            Gap.xl,
          ]);
        },
      ),
    );
  }
}

class _BigTile extends StatelessWidget {
  const _BigTile({required this.stage, required this.count, required this.onTap});

  final _Stage stage;
  final String count;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final fg = stage.dark ? AppColors.blueDark : Colors.white;
    return Material(
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: stage.dark ? const BorderSide(color: AppColors.blue, width: 1.6) : BorderSide.none),
      clipBehavior: Clip.antiAlias,
      elevation: 1,
      child: Ink(
        decoration: BoxDecoration(
          gradient: LinearGradient(colors: stage.colors, begin: Alignment.topLeft, end: Alignment.bottomRight),
        ),
        child: InkWell(
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(stage.icon, color: fg, size: 30),
                const Spacer(),
                Icon(Icons.arrow_circle_right_outlined, color: fg.withValues(alpha: 0.8), size: 26),
              ]),
              const Spacer(),
              Text(count, style: TextStyle(color: fg, fontSize: 38, fontWeight: FontWeight.w800, height: 1)),
              const SizedBox(height: 4),
              Text(stage.label,
                  maxLines: 2,
                  style: TextStyle(color: fg, fontSize: 17, fontWeight: FontWeight.w600, height: 1.15)),
            ]),
          ),
        ),
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.label, this.count, required this.onTap, this.alert = false});

  final String label;
  final String? count;
  final VoidCallback onTap;
  final bool alert;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
      child: Material(
        color: AppColors.slab,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8), side: const BorderSide(color: AppColors.seam)),
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 20),
            child: Row(children: [
              Expanded(
                child: Text(count == null ? label : '$label - $count',
                    style: TextStyle(
                        fontSize: 18,
                        fontWeight: alert ? FontWeight.w800 : FontWeight.w500,
                        color: alert ? AppColors.bad : AppColors.ink)),
              ),
              const Icon(Icons.chevron_right, size: 30, color: AppColors.blue),
            ]),
          ),
        ),
      ),
    );
  }
}
