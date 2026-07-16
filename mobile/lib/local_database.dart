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
        version: 1,
        onCreate: (db, _) async {
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
        },
      ),
    );
    return _database!;
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
