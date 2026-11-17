import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api.dart';

/// Bumped to send the user back to the Home tab (closing any open screens), e.g. after a delivery is recorded.
/// The Home tab also reloads its pending orders.
final goHome = ValueNotifier<int>(0);

/// White and blue: white cards on a very light blue page, a strong blue for bars and buttons, dark navy text.
class AppColors {
  static const concrete = Color(0xFFF3F6FC); // page background
  static const slab = Color(0xFFFFFFFF); // cards
  static const seam = Color(0xFFDCE4F2); // lines
  static const ink = Color(0xFF13234A); // text
  static const blue = Color(0xFF1B4FD8); // bars, buttons
  static const blueDark = Color(0xFF143CA6);
  static const blueSoft = Color(0xFF3D6BE6); // tracks on a blue bar
  static const blueTint = Color(0xFFE6EEFF); // selected things on white
  static const inkSoft = blueSoft;
  static const steel = Color(0xFF5B6B86);
  static const mist = Color(0xFF98A6BF);
  static const hivis = Colors.white; // highlight on a blue bar
  static const ok = Color(0xFF2E7D4F);
  static const warn = Color(0xFFA86B12);
  static const bad = Color(0xFFB23A3A);
}

class Gap {
  static const xs = SizedBox(height: 4, width: 4);
  static const s = SizedBox(height: 8, width: 8);
  static const m = SizedBox(height: 12, width: 12);
  static const l = SizedBox(height: 16, width: 16);
  static const xl = SizedBox(height: 24, width: 24);
}

ThemeData appTheme() {
  const scheme = ColorScheme.light(
    primary: AppColors.blue,
    onPrimary: Colors.white,
    secondary: AppColors.blueDark,
    onSecondary: Colors.white,
    primaryContainer: AppColors.blueTint,
    onPrimaryContainer: AppColors.blueDark,
    surface: AppColors.slab,
    onSurface: AppColors.ink,
    error: AppColors.bad,
  );
  final base = ThemeData(useMaterial3: true, colorScheme: scheme, scaffoldBackgroundColor: AppColors.concrete);
  final text = base.textTheme.apply(bodyColor: AppColors.ink, displayColor: AppColors.ink);
  return base.copyWith(
    textTheme: text.copyWith(
      headlineSmall: text.headlineSmall?.copyWith(fontWeight: FontWeight.w800, letterSpacing: -0.3),
      titleLarge: text.titleLarge?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.2),
      titleMedium: text.titleMedium?.copyWith(fontWeight: FontWeight.w600),
      bodyMedium: text.bodyMedium?.copyWith(height: 1.35),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.blue,
      foregroundColor: Colors.white,
      elevation: 0,
      centerTitle: false,
      systemOverlayStyle: SystemUiOverlayStyle.light,
      titleTextStyle: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: Colors.white),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: AppColors.slab,
      surfaceTintColor: Colors.transparent,
      indicatorColor: AppColors.blueTint,
      height: 66,
      labelTextStyle: WidgetStateProperty.resolveWith((s) => TextStyle(
            fontSize: 12,
            fontWeight: s.contains(WidgetState.selected) ? FontWeight.w700 : FontWeight.w500,
            color: s.contains(WidgetState.selected) ? AppColors.blue : AppColors.steel,
          )),
      iconTheme: WidgetStateProperty.resolveWith(
          (s) => IconThemeData(color: s.contains(WidgetState.selected) ? AppColors.blue : AppColors.steel)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.slab,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.seam)),
      enabledBorder:
          OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.seam)),
      focusedBorder:
          OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.blue, width: 1.8)),
      labelStyle: const TextStyle(color: AppColors.steel),
      hintStyle: const TextStyle(color: AppColors.mist),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.blue,
        foregroundColor: Colors.white,
        minimumSize: const Size.fromHeight(50),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.blue,
        side: const BorderSide(color: AppColors.seam, width: 1.4),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        textStyle: const TextStyle(fontWeight: FontWeight.w600),
      ),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: AppColors.ink,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
    ),
    dividerTheme: const DividerThemeData(color: AppColors.seam, thickness: 1, space: 1),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: AppColors.slab,
      showDragHandle: true,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
    ),
  );
}

// ------------------------------------------------------------------ formatting
/// Indian-style grouping: 750000 → ₹7,50,000.
String inr(String? value) {
  if (value == null || value.isEmpty) return '—';
  final n = double.tryParse(value);
  if (n == null) return value;
  final neg = n < 0;
  final parts = n.abs().toStringAsFixed(2).split('.');
  final whole = parts[0];
  final paise = parts[1];
  String grouped;
  if (whole.length <= 3) {
    grouped = whole;
  } else {
    final last3 = whole.substring(whole.length - 3);
    var rest = whole.substring(0, whole.length - 3);
    final groups = <String>[];
    while (rest.length > 2) {
      groups.insert(0, rest.substring(rest.length - 2));
      rest = rest.substring(0, rest.length - 2);
    }
    if (rest.isNotEmpty) groups.insert(0, rest);
    grouped = '${groups.join(',')},$last3';
  }
  return '${neg ? '-' : ''}₹$grouped${paise == '00' ? '' : '.$paise'}';
}

/// Trims trailing zeros from API decimal strings: "500.000000" → "500", "22.500" → "22.5".
String qty(String? value) {
  if (value == null || value.isEmpty) return '—';
  if (!value.contains('.')) return value;
  var v = value.replaceFirst(RegExp(r'0+$'), '');
  if (v.endsWith('.')) v = v.substring(0, v.length - 1);
  return v;
}

String initials(String name) {
  final words = name.replaceAll(RegExp(r'[^A-Za-z0-9 ]'), ' ').split(' ').where((w) => w.isNotEmpty).toList();
  if (words.isEmpty) return '?';
  if (words.length == 1) return words.first.substring(0, words.first.length >= 2 ? 2 : 1).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

void showError(BuildContext context, Object error) {
  final text = error is ApiException ? error.detail : error.toString();
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
    content: Text(text),
    backgroundColor: AppColors.bad,
  ));
}

// ------------------------------------------------------------------ building blocks
/// Dark steel header used at the top of every main screen.
class ScreenHeader extends StatelessWidget {
  const ScreenHeader({super.key, required this.title, this.subtitle, this.trailing, this.bottom});

  final String title;
  final String? subtitle;
  final Widget? trailing;
  final Widget? bottom;

  @override
  Widget build(BuildContext context) {
    final top = MediaQuery.of(context).padding.top;
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: SystemUiOverlayStyle.light,
      child: Container(
        color: AppColors.blue,
        padding: EdgeInsets.fromLTRB(20, top + 16, 20, bottom == null ? 20 : 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title,
                    style: const TextStyle(
                        color: Colors.white, fontSize: 26, fontWeight: FontWeight.w800, letterSpacing: -0.4)),
                if (subtitle != null) ...[
                  Gap.xs,
                  Text(subtitle!, style: const TextStyle(color: Colors.white70, fontSize: 14)),
                ],
              ]),
            ),
            if (trailing != null) trailing!,
          ]),
          if (bottom != null) ...[Gap.l, bottom!],
        ]),
      ),
    );
  }
}

/// Search box styled for the dark header.
class HeaderSearch extends StatelessWidget {
  const HeaderSearch({super.key, required this.hint, required this.onChanged});

  final String hint;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return TextField(
      onChanged: onChanged,
      style: const TextStyle(color: Colors.white),
      cursorColor: AppColors.hivis,
      textInputAction: TextInputAction.search,
      decoration: InputDecoration(
        hintText: hint,
        hintStyle: const TextStyle(color: Colors.white70),
        prefixIcon: const Icon(Icons.search, color: Colors.white70),
        filled: true,
        fillColor: AppColors.inkSoft,
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(vertical: 12),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
        focusedBorder:
            OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.hivis)),
      ),
    );
  }
}

/// White rounded panel holding related rows.
class Panel extends StatelessWidget {
  const Panel({super.key, required this.child, this.padding = EdgeInsets.zero, this.margin});

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry? margin;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: margin ?? const EdgeInsets.symmetric(horizontal: 16),
      padding: padding,
      decoration: BoxDecoration(
        color: AppColors.slab,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.seam),
      ),
      clipBehavior: Clip.antiAlias,
      child: child,
    );
  }
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key, this.trailing});

  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 22, 20, 8),
      child: Row(children: [
        Expanded(
          child: Text(text,
              style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: AppColors.ink)),
        ),
        if (trailing != null) trailing!,
      ]),
    );
  }
}

/// Initials in a tinted square, the dealer's "logo" in lists.
class Monogram extends StatelessWidget {
  const Monogram(this.name, {super.key, this.size = 44, this.dark = false});

  final String name;
  final double size;
  final bool dark;

  static const _tints = [
    Color(0xFFE3ECF2), Color(0xFFEAF2E4), Color(0xFFF6EEDC), Color(0xFFF2E6E6), Color(0xFFE8E6F2), Color(0xFFE2F0EE),
  ];

  @override
  Widget build(BuildContext context) {
    final tint = _tints[name.codeUnits.fold<int>(0, (a, b) => a + b) % _tints.length];
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: dark ? AppColors.hivis : tint,
        borderRadius: BorderRadius.circular(size * 0.28),
      ),
      child: Text(initials(name),
          style: TextStyle(fontSize: size * 0.36, fontWeight: FontWeight.w800, color: AppColors.ink)),
    );
  }
}

class StatusChip extends StatelessWidget {
  const StatusChip(this.label, {super.key, this.color = AppColors.steel, this.icon});

  final String label;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
      child: Row(mainAxisSize: MainAxisSize.min, children: [
        if (icon != null) ...[Icon(icon, size: 13, color: color), const SizedBox(width: 4)],
        Text(label, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w700)),
      ]),
    );
  }
}

/// A labelled figure, e.g. "Credit limit  ₹7,50,000".
class StatTile extends StatelessWidget {
  const StatTile({super.key, required this.label, required this.value, this.icon, this.onTap, this.highlight = false});

  final String label;
  final String value;
  final IconData? icon;
  final VoidCallback? onTap;
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: highlight ? AppColors.blue : AppColors.slab,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: highlight ? AppColors.blue : AppColors.seam),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            if (icon != null) Icon(icon, size: 20, color: highlight ? AppColors.hivis : AppColors.steel),
            if (icon != null) Gap.m,
            Text(value,
                style: TextStyle(
                    fontSize: 22, fontWeight: FontWeight.w800, color: highlight ? Colors.white : AppColors.ink)),
            Gap.xs,
            Text(label, style: TextStyle(fontSize: 13, color: highlight ? Colors.white70 : AppColors.steel)),
          ]),
        ),
      ),
    );
  }
}

/// A row inside a [Panel]: label on the left, value on the right.
class KeyValueRow extends StatelessWidget {
  const KeyValueRow(this.label, this.value, {super.key, this.valueColor});

  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
      child: Row(children: [
        Expanded(child: Text(label, style: const TextStyle(color: AppColors.steel, fontSize: 14))),
        Text(value, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15, color: valueColor ?? AppColors.ink)),
      ]),
    );
  }
}

class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.title, this.message, this.action});

  final IconData icon;
  final String title;
  final String? message;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(32, 64, 32, 32),
      child: Column(children: [
        Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(color: AppColors.slab, borderRadius: BorderRadius.circular(18),
              border: Border.all(color: AppColors.seam)),
          child: Icon(icon, color: AppColors.steel, size: 30),
        ),
        Gap.l,
        Text(title, textAlign: TextAlign.center, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
        if (message != null) ...[
          Gap.s,
          Text(message!, textAlign: TextAlign.center, style: const TextStyle(color: AppColors.steel, height: 1.4)),
        ],
        if (action != null) ...[Gap.l, action!],
      ]),
    );
  }
}

/// Grey placeholder rows shown while a list loads.
class SkeletonList extends StatelessWidget {
  const SkeletonList({super.key, this.rows = 6});

  final int rows;

  @override
  Widget build(BuildContext context) {
    Widget bar(double w, double h) => Container(
        width: w, height: h, decoration: BoxDecoration(color: AppColors.seam, borderRadius: BorderRadius.circular(6)));
    return ListView.separated(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      itemCount: rows,
      separatorBuilder: (_, __) => Gap.m,
      itemBuilder: (_, __) => Row(children: [
        bar(44, 44),
        Gap.m,
        Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [bar(180, 14), Gap.s, bar(110, 12)])),
      ]),
    );
  }
}

/// Loading / error / empty handling for a list loaded from the API, with pull-to-refresh.
class AsyncList<T> extends StatelessWidget {
  const AsyncList({
    super.key,
    required this.future,
    required this.itemBuilder,
    required this.empty,
    required this.onRefresh,
    this.filter,
    this.header,
  });

  final Future<List<T>>? future;
  final Widget Function(BuildContext, T) itemBuilder;
  final Widget empty;
  final Future<void> Function() onRefresh;
  final bool Function(T)? filter;
  final Widget Function(int count)? header;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<T>>(
      future: future,
      builder: (context, snap) {
        final busy = snap.connectionState != ConnectionState.done;
        // First load: placeholder. Reloads keep the current list on screen with a thin progress bar.
        if (busy && !snap.hasData) return const SkeletonList();
        if (!busy && snap.hasError) {
          final e = snap.error;
          return RefreshIndicator(
            onRefresh: onRefresh,
            child: ListView(children: [
              EmptyState(
                icon: Icons.cloud_off_outlined,
                title: 'Could not load',
                message: e is ApiException ? e.detail : 'Check your connection and pull down to try again.',
              ),
            ]),
          );
        }
        final all = snap.data ?? const [];
        final items = filter == null ? all : all.where(filter!).toList();
        final list = _list(items);
        if (!busy) return list;
        return Stack(children: [list, const LinearProgressIndicator(minHeight: 2)]);
      },
    );
  }

  Widget _list(List<T> items) {
    return Builder(builder: (context) {
        return RefreshIndicator(
          color: AppColors.blue,
          onRefresh: onRefresh,
          child: items.isEmpty
              ? ListView(children: [empty])
              : ListView.builder(
                  padding: const EdgeInsets.only(bottom: 24),
                  itemCount: items.length + (header == null ? 0 : 1),
                  itemBuilder: (c, i) {
                    if (header != null) {
                      if (i == 0) return header!(items.length);
                      return itemBuilder(c, items[i - 1]);
                    }
                    return itemBuilder(c, items[i]);
                  },
                ),
        );
    });
  }
}

/// A list that pages through the API as the user scrolls (50 at a time), so long lists such as 1,700 dealers
/// are complete without loading everything at once. Changing [query] (e.g. the search text) starts over.
class PagedList<T> extends StatefulWidget {
  const PagedList({
    super.key,
    required this.fetch,
    required this.itemBuilder,
    required this.empty,
    this.query = '',
    this.header,
  });

  /// Loads one page (1-based) and returns its items plus the total count on the server.
  final Future<(List<T>, int)> Function(int page) fetch;
  final Widget Function(BuildContext, T) itemBuilder;
  final Widget empty;
  final String query;
  final Widget Function(int total)? header;

  @override
  State<PagedList<T>> createState() => _PagedListState<T>();
}

class _PagedListState<T> extends State<PagedList<T>> {
  final _items = <T>[];
  final _scroll = ScrollController();
  int _page = 0;
  int? _total;
  bool _loading = false;
  Object? _error;
  int _generation = 0; // ignores replies that belong to an older search

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (_scroll.position.pixels > _scroll.position.maxScrollExtent - 600) _more();
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _more()); // setState is not allowed inside initState
  }

  @override
  void didUpdateWidget(covariant PagedList<T> old) {
    super.didUpdateWidget(old);
    if (old.query != widget.query) _restart();
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  bool get _hasMore => _total == null || _items.length < _total!;

  Future<void> _restart() async {
    _generation++;
    setState(() {
      _items.clear();
      _page = 0;
      _total = null;
      _error = null;
      _loading = false;
    });
    await _more();
  }

  Future<void> _more() async {
    if (!mounted || _loading || !_hasMore) return;
    final gen = _generation;
    setState(() => _loading = true);
    try {
      final (items, total) = await widget.fetch(_page + 1);
      if (!mounted || gen != _generation) return;
      setState(() {
        _items.addAll(items);
        _total = total;
        _page++;
        _loading = false;
      });
    } catch (e) {
      if (!mounted || gen != _generation) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_items.isEmpty && _error == null && (_loading || _total == null)) return const SkeletonList();
    if (_items.isEmpty && _error != null) {
      final e = _error;
      return RefreshIndicator(
        onRefresh: _restart,
        child: ListView(children: [
          EmptyState(
            icon: Icons.cloud_off_outlined,
            title: 'Could not load',
            message: e is ApiException ? e.detail : 'Check your connection and pull down to try again.',
          ),
        ]),
      );
    }
    final head = widget.header == null || _total == null ? 0 : 1;
    final tail = _hasMore || _error != null ? 1 : 0;
    return RefreshIndicator(
      color: AppColors.blue,
      onRefresh: _restart,
      child: _items.isEmpty
          ? ListView(children: [widget.empty])
          : ListView.builder(
              controller: _scroll,
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.only(bottom: 24),
              itemCount: head + _items.length + tail,
              itemBuilder: (c, i) {
                if (head == 1 && i == 0) return widget.header!(_total!);
                final k = i - head;
                if (k < _items.length) return widget.itemBuilder(c, _items[k]);
                if (_error != null) {
                  return TextButton(onPressed: _more, child: const Text('Could not load more — tap to retry'));
                }
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: Center(child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))),
                );
              },
            ),
    );
  }
}
