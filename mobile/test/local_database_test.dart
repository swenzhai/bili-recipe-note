import 'dart:io';

import 'package:bili_recipe_mobile/local_database.dart';
import 'package:bili_recipe_mobile/models.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;
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

  test('meal order, portions, notes, and progress persist offline', () async {
    final database = LocalDatabase(
      factory: databaseFactoryFfi,
      databasePath: inMemoryDatabasePath,
    );
    final recipe = RecipeSummary.fromPayload({
      'id': '55555555-5555-4555-8555-555555555555',
      'title': '宫保鸡丁',
      'category': '中餐',
      'cuisine': '川菜',
      'tags': ['下饭'],
      'ingredients': [
        {'name': '鸡肉', 'amount': '300克'},
      ],
      'steps': [
        {'title': '炒制', 'action': '大火翻炒'},
      ],
      'assets': [],
    });
    await database.applyRecipe(recipe.payload, deleted: false);
    const order = MealOrder(
      id: '66666666-6666-4666-8666-666666666666',
      title: '本餐',
      mealDate: '2026-08-10',
      status: MealOrderStatus.draft,
      createdAt: '2026-08-10T10:00:00Z',
      updatedAt: '2026-08-10T10:00:00Z',
    );
    await database.saveMealOrder(order);
    await database.saveMealOrderItem(
      MealOrderItem(
        id: '77777777-7777-4777-8777-777777777777',
        orderId: order.id,
        recipeId: recipe.id,
        recipe: recipe,
        servingsMultiplier: 1.5,
        note: '少辣',
        sortOrder: 0,
        completed: true,
      ),
    );

    final saved = (await database.mealOrderItems(order.id)).single;
    expect(saved.recipe.title, '宫保鸡丁');
    expect(saved.servingsMultiplier, 1.5);
    expect(saved.note, '少辣');
    expect(saved.completed, isTrue);

    await database.saveMealOrder(
      order.copyWith(
        status: MealOrderStatus.cooking,
        updatedAt: '2026-08-10T11:00:00Z',
      ),
    );
    expect((await database.activeMealOrder())?.status, MealOrderStatus.cooking);
    expect(await database.mealOrderItems(order.id), hasLength(1));
    await database.close();
  });

  test('version 1 database upgrades without losing existing data', () async {
    final directory = await Directory.systemTemp.createTemp('bili-meal-test-');
    final path = p.join(directory.path, 'mobile.sqlite3');
    try {
      final legacy = await databaseFactoryFfi.openDatabase(
        path,
        options: OpenDatabaseOptions(
          version: 1,
          onCreate: (db, _) => db.execute(
            'CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
          ),
        ),
      );
      await legacy.insert('settings', {'key': 'cursor', 'value': '42'});
      await legacy.close();

      final upgraded = LocalDatabase(
        factory: databaseFactoryFfi,
        databasePath: path,
      );
      expect(await upgraded.setting('cursor'), '42');
      expect(await upgraded.activeMealOrder(), isNull);
      final raw = await upgraded.database;
      expect(await raw.getVersion(), 2);
      await upgraded.close();
    } finally {
      await directory.delete(recursive: true);
    }
  });
}
