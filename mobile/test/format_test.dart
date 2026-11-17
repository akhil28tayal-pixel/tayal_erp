import 'package:flutter_test/flutter_test.dart';
import 'package:tayal_erp/core/ui.dart';

void main() {
  test('Indian rupee grouping', () {
    expect(inr('750000'), '₹7,50,000');
    expect(inr('12345678.50'), '₹1,23,45,678.50');
    expect(inr('999'), '₹999');
    expect(inr(null), '—');
  });

  test('API decimal strings are shown without trailing zeros', () {
    expect(qty('500.000000'), '500');
    expect(qty('22.500'), '22.5');
    expect(qty('7'), '7');
  });
}
