import 'dart:io';
import 'dart:ui' as ui;

import 'package:crypto/crypto.dart';
import 'package:flutter_image_compress/flutter_image_compress.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:uuid/uuid.dart';

import 'api_client.dart';
import 'local_database.dart';
import 'models.dart';

class RecipeRepository {
  RecipeRepository({
    LocalDatabase? database,
    FlutterSecureStorage? secureStorage,
  }) : database = database ?? LocalDatabase(),
       secureStorage = secureStorage ?? const FlutterSecureStorage();

  final LocalDatabase database;
  final FlutterSecureStorage secureStorage;
  final Uuid _uuid = const Uuid();

  Future<bool> get isPaired async =>
      (await database.setting('base_url')) != null &&
      (await secureStorage.read(key: 'access_token')) != null;

  Future<void> pair(PairingData data, {String? deviceName}) async {
    final deviceId = await database.setting('device_id') ?? _uuid.v4();
    final response = await ApiClient(
      data.baseUrl,
    ).pair(data, deviceName ?? Platform.localHostname, deviceId);
    if (response['server_id'] != data.serverId) {
      throw const FormatException('服务器身份与二维码不一致');
    }
    await database.saveSetting('device_id', response['device_id'] as String);
    await database.saveSetting('server_id', response['server_id'] as String);
    await database.saveSetting('base_url', data.baseUrl);
    await database.saveSetting('cursor', '0');
    await secureStorage.write(
      key: 'access_token',
      value: response['access_token'] as String,
    );
    await synchronize();
  }

  Future<void> unpair() async {
    await secureStorage.delete(key: 'access_token');
    await database.saveSetting('base_url', '');
  }

  Future<ApiClient> _client() async {
    final baseUrl = await database.setting('base_url');
    final token = await secureStorage.read(key: 'access_token');
    if (baseUrl == null || baseUrl.isEmpty || token == null) {
      throw const ApiException('尚未配对服务器', 401);
    }
    return ApiClient(baseUrl, accessToken: token);
  }

  Future<List<RecipeSummary>> recipes({
    String query = '',
    String category = '',
    String cuisine = '',
    String tag = '',
  }) => database.searchRecipes(
    query: query,
    category: category,
    cuisine: cuisine,
    tag: tag,
  );

  Future<List<PracticeLog>> practiceLogs(String recipeId) =>
      database.practiceLogs(recipeId);
  Future<List<Map<String, dynamic>>> conflicts() => database.conflicts();

  Future<MealOrder?> activeMealOrder() => database.activeMealOrder();

  Future<List<MealOrderItem>> mealOrderItems(String orderId) =>
      database.mealOrderItems(orderId);

  Future<bool> addRecipeToMeal(RecipeSummary recipe) async {
    var order = await database.activeMealOrder();
    if (order == null) {
      final now = DateTime.now().toUtc().toIso8601String();
      order = MealOrder(
        id: _uuid.v4(),
        title: '本餐',
        mealDate: DateTime.now().toIso8601String().substring(0, 10),
        status: MealOrderStatus.draft,
        createdAt: now,
        updatedAt: now,
      );
      await database.saveMealOrder(order);
    }
    if (await database.mealOrderItem(order.id, recipe.id) != null) {
      return false;
    }
    await database.saveMealOrderItem(
      MealOrderItem(
        id: _uuid.v4(),
        orderId: order.id,
        recipeId: recipe.id,
        recipe: recipe,
        servingsMultiplier: 1,
        note: '',
        sortOrder: await database.nextMealItemSortOrder(order.id),
        completed: false,
      ),
    );
    await _touchMealOrder(order);
    return true;
  }

  Future<void> updateMealOrderItem(MealOrderItem item) async {
    await database.saveMealOrderItem(item);
    final order = await database.activeMealOrder();
    if (order != null) await _touchMealOrder(order);
  }

  Future<void> removeMealOrderItem(MealOrderItem item) async {
    await database.removeMealOrderItem(item.id);
    final order = await database.activeMealOrder();
    if (order == null) return;
    final remaining = await database.mealOrderItems(order.id);
    if (remaining.isEmpty) {
      await database.deleteMealOrder(order.id);
    } else {
      await _touchMealOrder(order);
    }
  }

  Future<void> clearMealOrder(MealOrder order) =>
      database.deleteMealOrder(order.id);

  Future<void> startMealOrder(MealOrder order) =>
      _saveMealStatus(order, MealOrderStatus.cooking);

  Future<void> completeMealOrder(MealOrder order) =>
      _saveMealStatus(order, MealOrderStatus.completed);

  Future<void> _saveMealStatus(MealOrder order, MealOrderStatus status) =>
      database.saveMealOrder(
        order.copyWith(
          status: status,
          updatedAt: DateTime.now().toUtc().toIso8601String(),
        ),
      );

  Future<void> _touchMealOrder(MealOrder order) => database.saveMealOrder(
    order.copyWith(updatedAt: DateTime.now().toUtc().toIso8601String()),
  );

  Future<PracticeLog> savePractice({
    PracticeLog? existing,
    required String recipeId,
    required DateTime cookedOn,
    required String notes,
    String? outcome,
    int? rating,
    XFile? photo,
  }) async {
    String? photoHash = existing?.photoSha256;
    String? photoPath = existing?.localPhotoPath;
    if (photo != null) {
      final prepared = await _preparePhoto(photo);
      photoHash = prepared.$1;
      photoPath = prepared.$2;
    }
    final log = PracticeLog(
      id: existing?.id ?? _uuid.v4(),
      recipeId: recipeId,
      cookedOn: cookedOn.toIso8601String().substring(0, 10),
      notes: notes.trim(),
      outcome: outcome,
      rating: rating,
      photoSha256: photoHash,
      localPhotoPath: photoPath,
      version: existing?.version ?? 0,
    );
    await database.saveLocalPractice(log);
    final pending = await database.outboxFor(log.id);
    await database.queueOperation(
      entityId: log.id,
      opId: pending?['op_id'] as String? ?? _uuid.v4(),
      action: 'upsert',
      baseVersion: pending?['base_version'] as int? ?? log.version,
      payload: log.toProtocolJson(),
    );
    return log;
  }

  Future<void> deletePractice(PracticeLog log) async {
    final pending = await database.outboxFor(log.id);
    if (log.version == 0) {
      final orphanedPhoto = await database.removeUnsyncedPractice(log.id);
      if (orphanedPhoto != null) {
        final file = File(orphanedPhoto);
        if (await file.exists()) await file.delete();
      }
      return;
    }
    final deleted = PracticeLog(
      id: log.id,
      recipeId: log.recipeId,
      cookedOn: log.cookedOn,
      notes: log.notes,
      outcome: log.outcome,
      rating: log.rating,
      photoSha256: log.photoSha256,
      localPhotoPath: log.localPhotoPath,
      version: log.version,
      deletedAt: DateTime.now().toUtc().toIso8601String(),
    );
    await database.saveLocalPractice(deleted);
    await database.queueOperation(
      entityId: log.id,
      opId: pending?['op_id'] as String? ?? _uuid.v4(),
      action: 'delete',
      baseVersion: pending?['base_version'] as int? ?? log.version,
      payload: deleted.toProtocolJson(),
    );
  }

  Future<(String, String)> _preparePhoto(XFile source) async {
    final directory = Directory(
      p.join((await getApplicationSupportDirectory()).path, 'images'),
    );
    await directory.create(recursive: true);
    final temporaryPath = p.join(directory.path, '${_uuid.v4()}.jpg');
    final originalBytes = await source.readAsBytes();
    final codec = await ui.instantiateImageCodec(originalBytes);
    final frame = await codec.getNextFrame();
    final decoded = frame.image;
    final longestSide = decoded.width > decoded.height
        ? decoded.width
        : decoded.height;
    final scale = longestSide > 1600 ? 1600 / longestSide : 1.0;
    final targetWidth = (decoded.width * scale).round().clamp(1, 1600).toInt();
    final targetHeight = (decoded.height * scale)
        .round()
        .clamp(1, 1600)
        .toInt();
    decoded.dispose();
    codec.dispose();
    final compressed = await FlutterImageCompress.compressAndGetFile(
      source.path,
      temporaryPath,
      quality: 85,
      minWidth: targetWidth,
      minHeight: targetHeight,
      keepExif: false,
    );
    if (compressed == null) {
      throw const FileSystemException('照片压缩失败');
    }
    final bytes = await compressed.readAsBytes();
    if (bytes.length > 5 * 1024 * 1024) {
      throw const FileSystemException('压缩后的照片仍超过 5 MiB');
    }
    final hash = sha256.convert(bytes).toString();
    final finalPath = p.join(directory.path, '$hash.jpg');
    if (compressed.path != finalPath) {
      if (await File(finalPath).exists()) {
        await File(compressed.path).delete();
      } else {
        await File(compressed.path).rename(finalPath);
      }
    }
    await database.saveAssetPath(hash, finalPath);
    return (hash, finalPath);
  }

  Future<void> synchronize() async {
    final client = await _client();
    var cursor = int.tryParse(await database.setting('cursor') ?? '0') ?? 0;
    var operations = await database.pendingOperations();
    for (final operation in operations) {
      final payload = operation['payload'] as Map<String, dynamic>;
      final hash = payload['photo_sha256'] as String?;
      if (hash != null) {
        final path = await database.assetPath(hash);
        if (path != null && await File(path).exists()) {
          await client.uploadAsset(hash, await File(path).readAsBytes());
        }
      }
    }
    var firstPage = true;
    while (true) {
      final response = await client.sync(
        cursor,
        firstPage ? operations : const [],
      );
      if (firstPage) {
        for (final raw in response['operation_results'] as List<dynamic>) {
          final result = raw as Map<String, dynamic>;
          final operation = operations.firstWhere(
            (item) => item['op_id'] == result['op_id'],
          );
          final entityId = operation['entity_id'] as String;
          if (result['status'] == 'conflict') {
            await database.saveConflict(
              id: result['conflict_id'] as String? ?? _uuid.v4(),
              entityId: entityId,
              incoming: operation['payload'] as Map<String, dynamic>,
              server: (result['server'] as Map<String, dynamic>?) ?? const {},
            );
          }
          await database.removeOutbox(entityId);
        }
      }
      for (final raw in response['changes'] as List<dynamic>) {
        final change = raw as Map<String, dynamic>;
        final payload = change['payload'] as Map<String, dynamic>;
        if (change['entity_type'] == 'recipe') {
          await database.applyRecipe(
            payload,
            deleted: change['action'] == 'delete',
          );
        } else if (change['entity_type'] == 'practice_log') {
          await database.applyServerPractice(payload);
        }
      }
      cursor = response['next_cursor'] as int;
      await database.saveSetting('cursor', cursor.toString());
      if (response['has_more'] != true) break;
      firstPage = false;
    }
    await _downloadMissingAssets(client);
  }

  Future<void> _downloadMissingAssets(ApiClient client) async {
    final directory = Directory(
      p.join((await getApplicationSupportDirectory()).path, 'images'),
    );
    await directory.create(recursive: true);
    for (final hash in await database.missingAssetHashes()) {
      try {
        final bytes = await client.downloadAsset(hash);
        if (sha256.convert(bytes).toString() != hash) continue;
        final path = p.join(directory.path, '$hash.bin');
        await File(path).writeAsBytes(bytes, flush: true);
        await database.saveAssetPath(hash, path);
      } catch (_) {
        // Text remains usable; a later foreground sync retries missing images.
      }
    }
  }

  Future<void> resolveConflict(
    Map<String, dynamic> conflict, {
    required bool keepMine,
    Map<String, dynamic>? merged,
  }) async {
    final conflictId = conflict['id'] as String;
    final entityId = conflict['entity_id'] as String;
    final server = Map<String, dynamic>.from(conflict['server'] as Map);
    if (keepMine || merged != null) {
      final incoming = Map<String, dynamic>.from(
        merged ?? conflict['incoming'] as Map,
      );
      incoming['version'] = server['version'] ?? 0;
      incoming['_resolved_conflict_id'] = conflictId;
      incoming['_conflict_resolution'] = merged == null ? 'incoming' : 'merged';
      await database.applyServerPractice(incoming);
      await database.queueOperation(
        entityId: incoming['id'] as String,
        opId: _uuid.v4(),
        action: 'upsert',
        baseVersion: server['version'] as int? ?? 0,
        payload: incoming,
      );
    } else {
      await database.applyServerPractice(server);
      await database.queueOperation(
        entityId: entityId,
        opId: _uuid.v4(),
        action: 'resolve_conflict',
        baseVersion: server['version'] as int? ?? 0,
        payload: {
          'id': entityId,
          'conflict_id': conflictId,
          'resolution': 'server',
        },
      );
    }
    await database.removeConflict(conflictId);
  }

  Future<String?> assetPath(String digest) => database.assetPath(digest);

  Future<void> close() => database.close();
}
