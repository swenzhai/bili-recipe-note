import 'package:bili_recipe_mobile/local_database.dart';
import 'package:bili_recipe_mobile/models.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();

  test('recipes and pending practice remain available offline', () async {
    final database = LocalDatabase(
      factory: databaseFactoryFfi,
      databasePath: inMemoryDatabasePath,
    );
    await database.applyRecipe({
      'id': '11111111-1111-4111-8111-111111111111',
      'title': '离线番茄炒蛋',
      'category': '中餐',
      'cuisine': '中式',
      'tags': ['快手'],
      'ingredients': [
        {'name': '番茄'},
      ],
      'steps': [
        {'title': '炒制', 'action': '大火翻炒'},
      ],
      'assets': [],
    }, deleted: false);
    expect((await database.searchRecipes(query: '番茄')).single.title, '离线番茄炒蛋');
    expect((await database.searchRecipes(tag: '快手')).length, 1);

    const log = PracticeLog(
      id: '22222222-2222-4222-8222-222222222222',
      recipeId: '11111111-1111-4111-8111-111111111111',
      cookedOn: '2026-07-15',
      notes: '离线记录的心得',
    );
    await database.saveLocalPractice(log);
    await database.queueOperation(
      entityId: log.id,
      opId: '33333333-3333-4333-8333-333333333333',
      action: 'upsert',
      baseVersion: 0,
      payload: log.toProtocolJson(),
    );
    expect((await database.practiceLogs(log.recipeId)).single.notes, '离线记录的心得');
    expect((await database.pendingOperations()).single['entity_id'], log.id);
    await database.close();
  });

  test(
    'server practice photos enter the download queue and attach locally',
    () async {
      final database = LocalDatabase(
        factory: databaseFactoryFfi,
        databasePath: inMemoryDatabasePath,
      );
      const hash =
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
      await database.applyServerPractice({
        'id': '44444444-4444-4444-8444-444444444444',
        'recipe_id': '11111111-1111-4111-8111-111111111111',
        'cooked_on': '2026-07-16',
        'notes': '有照片的实践',
        'photo_sha256': hash,
        'version': 1,
      });

      expect(await database.missingAssetHashes(), contains(hash));
      await database.saveAssetPath(hash, '/tmp/practice.jpg');
      final log = (await database.practiceLogs(
        '11111111-1111-4111-8111-111111111111',
      )).single;
      expect(log.localPhotoPath, '/tmp/practice.jpg');
      await database.close();
    },
  );
}
