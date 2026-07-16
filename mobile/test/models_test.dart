import 'package:bili_recipe_mobile/models.dart';
import 'package:bili_recipe_mobile/screens.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('pairing accepts private LAN and rejects public addresses', () {
    final future = DateTime.now()
        .toUtc()
        .add(const Duration(minutes: 5))
        .toIso8601String();
    final data = PairingData.parse(
      '{"schema_version":1,"server_id":"server","base_url":"http://192.168.1.2:8765/",'
      '"pairing_token":"secret","expires_at":"$future"}',
    );
    expect(data.baseUrl, 'http://192.168.1.2:8765');
    expect(
      () => PairingData.parse(
        '{"schema_version":1,"server_id":"server","base_url":"http://8.8.8.8:8765",'
        '"pairing_token":"secret","expires_at":"$future"}',
      ),
      throwsFormatException,
    );
  });

  test('recipe search text includes ingredients and steps', () {
    final recipe = RecipeSummary.fromPayload({
      'id': 'recipe',
      'title': '番茄炒蛋',
      'ingredients': [
        {'name': '番茄'},
      ],
      'steps': [
        {'title': '炒制', 'action': '大火翻炒'},
      ],
    });
    expect(recipe.searchableText, contains('番茄'));
    expect(recipe.searchableText, contains('大火翻炒'));
  });

  test('amount scaling is conservative', () {
    expect(scaleAmount('200克', 1.5), '300克');
    expect(scaleAmount('少许', 3), '少许');
    expect(scaleAmount('2-3个', 2), '2-3个');
  });
}
