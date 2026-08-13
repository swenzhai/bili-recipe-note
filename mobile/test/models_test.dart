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

  test('shopping list merges matching ingredients after scaling', () {
    MealOrderItem item(
      String id,
      String title,
      double multiplier,
      List<Map<String, Object>> ingredients,
    ) => MealOrderItem(
      id: id,
      orderId: 'meal',
      recipeId: id,
      recipe: RecipeSummary.fromPayload({
        'id': id,
        'title': title,
        'ingredients': ingredients,
      }),
      servingsMultiplier: multiplier,
      note: '',
      sortOrder: 0,
      completed: false,
    );

    final shopping = buildShoppingList([
      item('one', '番茄炒蛋', 1, [
        {'name': '番茄', 'amount': '200克'},
        {'name': '盐', 'amount': '少许'},
      ]),
      item('two', '番茄汤', 2, [
        {'name': '番茄', 'amount': '100克'},
        {'name': '盐', 'amount': '适量'},
      ]),
    ]);

    final tomato = shopping.singleWhere((entry) => entry.name == '番茄');
    expect(tomato.amount, '400克');
    expect(tomato.recipeTitles, ['番茄炒蛋', '番茄汤']);
    expect(
      shopping.singleWhere((entry) => entry.name == '盐').amount,
      '少许 + 适量',
    );
  });
}
