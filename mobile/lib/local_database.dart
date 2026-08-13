import 'dart:convert';

import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

import 'models.dart';

class LocalDatabase {
  LocalDatabase({DatabaseFactory? factory, this._databasePath})
    : _factory = factory ?? databaseFactory,
      assert(_databasePath == null || _databasePath != '');

  final DatabaseFactory _factory;
  final String? _databasePath;
  Database? _database;

  Future<Database> get database async {
    if (_database != null) return _database!;
    final path =
        _databasePath ??
        p.join(await getDatabasesPath(), 'bili_recipe_mobile.sqlite3');
    _database = await _factory.openDatabase(
      path,
      options: OpenDatabaseOptions(
        version: 2,
        onConfigure: (db) => db.execute('PRAGMA foreign_keys = ON'),
        onCreate: (db, _) async {
          await _createVersionOneTables(db);
          await _createMealOrderTables(db);
        },
        onUpgrade: (db, oldVersion, _) async {
          if (oldVersion < 2) await _createMealOrderTables(db);
        },
      ),
    );
    return _database!;
  }

  Future<void> _createVersionOneTables(Database db) async {
    await db.execute('''
        CREATE TABLE recipes(
          id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
          cuisine TEXT NOT NULL, tags TEXT NOT NULL, search_text TEXT NOT NULL,
          payload_json TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
        )
      ''');
    await db.execute('''
        CREATE TABLE practice_logs(
          id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, cooked_on TEXT NOT NULL,
          outcome TEXT, rating INTEGER, notes TEXT NOT NULL, photo_sha256 TEXT,
          local_photo_path TEXT, version INTEGER NOT NULL, deleted_at TEXT
        )
      ''');
    await db.execute('''
        CREATE TABLE outbox(
          entity_id TEXT PRIMARY KEY, op_id TEXT NOT NULL, action TEXT NOT NULL,
          base_version INTEGER NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        )
      ''');
    await db.execute('''
        CREATE TABLE assets(
          sha256 TEXT PRIMARY KEY, mime_type TEXT, byte_size INTEGER,
          kind TEXT, local_path TEXT
        )
      ''');
    await db.execute('''
        CREATE TABLE conflicts(
          id TEXT PRIMARY KEY, entity_id TEXT NOT NULL,
          incoming_json TEXT NOT NULL, server_json TEXT NOT NULL
        )
      ''');
    await db.execute(
      'CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    );
  }

  Future<void> _createMealOrderTables(Database db) async {
    await db.execute('''
      CREATE TABLE meal_orders(
        id TEXT PRIMARY KEY, title TEXT NOT NULL, meal_date TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
      )
    ''');
    await db.execute('''
      CREATE TABLE meal_order_items(
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL REFERENCES meal_orders(id) ON DELETE CASCADE,
        recipe_id TEXT NOT NULL, recipe_snapshot_json TEXT NOT NULL,
        servings_multiplier REAL NOT NULL DEFAULT 1,
        note TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0,
        UNIQUE(order_id, recipe_id)
      )
    ''');
    await db.execute(
      'CREATE INDEX idx_meal_order_items_order ON meal_order_items(order_id, sort_order)',
    );
  }

  Future<void> saveSetting(String key, String value) async {
    final db = await database;
    await db.insert('settings', {
      'key': key,
      'value': value,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<String?> setting(String key) async {
    final db = await database;
    final rows = await db.query(
      'settings',
      where: 'key=?',
      whereArgs: [key],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first['value'] as String;
  }

  Future<List<RecipeSummary>> searchRecipes({
    String query = '',
    String category = '',
    String cuisine = '',
    String tag = '',
  }) async {
    final db = await database;
    final clauses = ['deleted=0'];
    final args = <Object?>[];
    if (query.trim().isNotEmpty) {
      clauses.add('search_text LIKE ?');
      args.add('%${query.trim().toLowerCase()}%');
    }
    if (category.isNotEmpty) {
      clauses.add('category=?');
      args.add(category);
    }
    if (cuisine.isNotEmpty) {
      clauses.add('cuisine=?');
      args.add(cuisine);
    }
    if (tag.isNotEmpty) {
      clauses.add('tags LIKE ?');
      args.add('%"$tag"%');
    }
    final rows = await db.query(
      'recipes',
      where: clauses.join(' AND '),
      whereArgs: args,
      orderBy: 'title COLLATE NOCASE',
    );
    return rows
        .map(
          (row) => RecipeSummary.fromPayload(
            jsonDecode(row['payload_json'] as String) as Map<String, dynamic>,
          ),
        )
        .toList();
  }

  Future<void> applyRecipe(
    Map<String, dynamic> payload, {
    required bool deleted,
  }) async {
    final db = await database;
    final id = payload['id'] as String;
    if (deleted) {
      await db.update(
        'recipes',
        {'deleted': 1},
        where: 'id=?',
        whereArgs: [id],
      );
      return;
    }
    final recipe = RecipeSummary.fromPayload(payload);
    await db.insert('recipes', {
      'id': recipe.id,
      'title': recipe.title,
      'category': recipe.category,
      'cuisine': recipe.cuisine,
      'tags': jsonEncode(recipe.tags),
      'search_text': recipe.searchableText,
      'payload_json': jsonEncode(payload),
      'deleted': 0,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
    for (final asset in recipe.assets) {
      await db.insert('assets', {
        'sha256': asset['sha256'],
        'mime_type': asset['mime_type'],
        'byte_size': asset['byte_size'],
        'kind': asset['kind'],
      }, conflictAlgorithm: ConflictAlgorithm.ignore);
    }
  }

  Future<MealOrder?> activeMealOrder() async {
    final db = await database;
    final rows = await db.query(
      'meal_orders',
      where: "status IN ('draft', 'cooking')",
      orderBy: 'updated_at DESC',
      limit: 1,
    );
    return rows.isEmpty ? null : _mealOrderFromRow(rows.first);
  }

  MealOrder _mealOrderFromRow(Map<String, Object?> row) => MealOrder(
    id: row['id'] as String,
    title: row['title'] as String,
    mealDate: row['meal_date'] as String,
    status: MealOrderStatus.parse(row['status'] as String),
    createdAt: row['created_at'] as String,
    updatedAt: row['updated_at'] as String,
  );

  Future<void> saveMealOrder(MealOrder order) async {
    final db = await database;
    final values = {
      'id': order.id,
      'title': order.title,
      'meal_date': order.mealDate,
      'status': order.status.name,
      'created_at': order.createdAt,
      'updated_at': order.updatedAt,
    };
    final updated = await db.update(
      'meal_orders',
      values,
      where: 'id=?',
      whereArgs: [order.id],
    );
    if (updated == 0) await db.insert('meal_orders', values);
  }

  Future<List<MealOrderItem>> mealOrderItems(String orderId) async {
    final db = await database;
    final rows = await db.rawQuery(
      '''
      SELECT item.*, recipe.payload_json AS current_recipe_json
      FROM meal_order_items AS item
      LEFT JOIN recipes AS recipe ON recipe.id = item.recipe_id AND recipe.deleted = 0
      WHERE item.order_id = ?
      ORDER BY item.sort_order, item.id
      ''',
      [orderId],
    );
    return rows.map((row) {
      final rawRecipe =
          row['current_recipe_json'] as String? ??
          row['recipe_snapshot_json'] as String;
      return MealOrderItem(
        id: row['id'] as String,
        orderId: row['order_id'] as String,
        recipeId: row['recipe_id'] as String,
        recipe: RecipeSummary.fromPayload(
          jsonDecode(rawRecipe) as Map<String, dynamic>,
        ),
        servingsMultiplier: (row['servings_multiplier'] as num).toDouble(),
        note: row['note'] as String,
        sortOrder: row['sort_order'] as int,
        completed: row['completed'] == 1,
      );
    }).toList();
  }

  Future<MealOrderItem?> mealOrderItem(String orderId, String recipeId) async {
    final items = await mealOrderItems(orderId);
    for (final item in items) {
      if (item.recipeId == recipeId) return item;
    }
    return null;
  }

  Future<int> nextMealItemSortOrder(String orderId) async {
    final db = await database;
    final rows = await db.rawQuery(
      'SELECT MAX(sort_order) AS maximum FROM meal_order_items WHERE order_id=?',
      [orderId],
    );
    return ((rows.single['maximum'] as int?) ?? -1) + 1;
  }

  Future<void> saveMealOrderItem(MealOrderItem item) async {
    final db = await database;
    await db.insert('meal_order_items', {
      'id': item.id,
      'order_id': item.orderId,
      'recipe_id': item.recipeId,
      'recipe_snapshot_json': jsonEncode(item.recipe.payload),
      'servings_multiplier': item.servingsMultiplier,
      'note': item.note,
      'sort_order': item.sortOrder,
      'completed': item.completed ? 1 : 0,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<void> removeMealOrderItem(String itemId) async {
    final db = await database;
    await db.delete('meal_order_items', where: 'id=?', whereArgs: [itemId]);
  }

  Future<void> deleteMealOrder(String orderId) async {
    final db = await database;
    await db.delete('meal_orders', where: 'id=?', whereArgs: [orderId]);
  }

  Future<List<PracticeLog>> practiceLogs(String recipeId) async {
    final db = await database;
    final rows = await db.query(
      'practice_logs',
      where: 'recipe_id=? AND deleted_at IS NULL',
      whereArgs: [recipeId],
      orderBy: 'cooked_on DESC',
    );
    return rows.map(_logFromRow).toList();
  }

  PracticeLog _logFromRow(Map<String, Object?> row) => PracticeLog(
    id: row['id'] as String,
    recipeId: row['recipe_id'] as String,
    cookedOn: row['cooked_on'] as String,
    notes: row['notes'] as String,
    outcome: row['outcome'] as String?,
    rating: row['rating'] as int?,
    photoSha256: row['photo_sha256'] as String?,
    localPhotoPath: row['local_photo_path'] as String?,
    version: row['version'] as int,
    deletedAt: row['deleted_at'] as String?,
  );

  Future<PracticeLog?> practiceLog(String id) async {
    final db = await database;
    final rows = await db.query(
      'practice_logs',
      where: 'id=?',
      whereArgs: [id],
      limit: 1,
    );
    return rows.isEmpty ? null : _logFromRow(rows.first);
  }

  Future<void> saveLocalPractice(PracticeLog log) async {
    final db = await database;
    await db.insert('practice_logs', {
      'id': log.id,
      'recipe_id': log.recipeId,
      'cooked_on': log.cookedOn,
      'outcome': log.outcome,
      'rating': log.rating,
      'notes': log.notes,
      'photo_sha256': log.photoSha256,
      'local_photo_path': log.localPhotoPath,
      'version': log.version,
      'deleted_at': log.deletedAt,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<void> applyServerPractice(Map<String, dynamic> payload) async {
    final local = await practiceLog(payload['id'] as String);
    final photoHash = payload['photo_sha256'] as String?;
    if (photoHash != null && photoHash.isNotEmpty) {
      final db = await database;
      await db.insert('assets', {
        'sha256': photoHash,
        'kind': 'practice_photo',
      }, conflictAlgorithm: ConflictAlgorithm.ignore);
    }
    await saveLocalPractice(
      PracticeLog.fromJson(payload, localPhotoPath: local?.localPhotoPath),
    );
  }

  Future<Map<String, Object?>?> outboxFor(String entityId) async {
    final db = await database;
    final rows = await db.query(
      'outbox',
      where: 'entity_id=?',
      whereArgs: [entityId],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first;
  }

  Future<void> queueOperation({
    required String entityId,
    required String opId,
    required String action,
    required int baseVersion,
    required Map<String, dynamic> payload,
  }) async {
    final db = await database;
    await db.insert('outbox', {
      'entity_id': entityId,
      'op_id': opId,
      'action': action,
      'base_version': baseVersion,
      'payload_json': jsonEncode(payload),
      'created_at': DateTime.now().toUtc().toIso8601String(),
    }, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<List<Map<String, dynamic>>> pendingOperations() async {
    final db = await database;
    final rows = await db.query('outbox', orderBy: 'created_at', limit: 100);
    return rows.map((row) {
      final payload =
          jsonDecode(row['payload_json'] as String) as Map<String, dynamic>;
      return {
        'op_id': row['op_id'],
        'entity_type': 'practice_log',
        'entity_id': row['entity_id'],
        'action': row['action'],
        'base_version': row['base_version'],
        'payload': payload,
      };
    }).toList();
  }

  Future<void> removeOutbox(String entityId) async {
    final db = await database;
    await db.delete('outbox', where: 'entity_id=?', whereArgs: [entityId]);
  }

  Future<String?> removeUnsyncedPractice(String id) async {
    final db = await database;
    return db.transaction((txn) async {
      final logs = await txn.query(
        'practice_logs',
        columns: ['photo_sha256'],
        where: 'id=?',
        whereArgs: [id],
        limit: 1,
      );
      final photoHash = logs.isEmpty
          ? null
          : logs.first['photo_sha256'] as String?;
      await txn.delete('outbox', where: 'entity_id=?', whereArgs: [id]);
      await txn.delete('practice_logs', where: 'id=?', whereArgs: [id]);
      if (photoHash == null) return null;
      final references = Sqflite.firstIntValue(
        await txn.rawQuery(
          'SELECT COUNT(*) FROM practice_logs WHERE photo_sha256=?',
          [photoHash],
        ),
      );
      if ((references ?? 0) != 0) return null;
      final assets = await txn.query(
        'assets',
        columns: ['local_path', 'kind'],
        where: 'sha256=?',
        whereArgs: [photoHash],
        limit: 1,
      );
      if (assets.isEmpty || assets.first['kind'] == 'recipe_image') return null;
      await txn.delete('assets', where: 'sha256=?', whereArgs: [photoHash]);
      return assets.first['local_path'] as String?;
    });
  }

  Future<void> saveConflict({
    required String id,
    required String entityId,
    required Map<String, dynamic> incoming,
    required Map<String, dynamic> server,
  }) async {
    final db = await database;
    await db.insert('conflicts', {
      'id': id,
      'entity_id': entityId,
      'incoming_json': jsonEncode(incoming),
      'server_json': jsonEncode(server),
    }, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<List<Map<String, dynamic>>> conflicts() async {
    final db = await database;
    final rows = await db.query('conflicts');
    return rows
        .map(
          (row) => {
            'id': row['id'],
            'entity_id': row['entity_id'],
            'incoming': jsonDecode(row['incoming_json'] as String),
            'server': jsonDecode(row['server_json'] as String),
          },
        )
        .toList();
  }

  Future<void> removeConflict(String id) async {
    final db = await database;
    await db.delete('conflicts', where: 'id=?', whereArgs: [id]);
  }

  Future<void> saveAssetPath(String sha256, String path) async {
    final db = await database;
    final updated = await db.update(
      'assets',
      {'local_path': path},
      where: 'sha256=?',
      whereArgs: [sha256],
    );
    if (updated == 0) {
      await db.insert('assets', {'sha256': sha256, 'local_path': path});
    }
    await db.update(
      'practice_logs',
      {'local_photo_path': path},
      where: 'photo_sha256=?',
      whereArgs: [sha256],
    );
  }

  Future<String?> assetPath(String sha256) async {
    final db = await database;
    final rows = await db.query(
      'assets',
      where: 'sha256=?',
      whereArgs: [sha256],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first['local_path'] as String?;
  }

  Future<List<String>> missingAssetHashes() async {
    final db = await database;
    final rows = await db.query(
      'assets',
      columns: ['sha256'],
      where: 'local_path IS NULL',
    );
    return rows.map((row) => row['sha256'] as String).toList();
  }

  Future<void> close() async {
    await _database?.close();
    _database = null;
  }
}
