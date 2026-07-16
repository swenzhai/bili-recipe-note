import 'package:flutter/foundation.dart';
import 'package:image_picker/image_picker.dart';

import 'models.dart';
import 'recipe_repository.dart';

class AppController extends ChangeNotifier {
  AppController({RecipeRepository? repository})
    : repository = repository ?? RecipeRepository();

  final RecipeRepository repository;
  bool initialized = false;
  bool paired = false;
  bool syncing = false;
  String? syncError;
  DateTime? lastSync;
  String query = '';
  String category = '';
  String cuisine = '';
  String tag = '';
  List<RecipeSummary> recipes = [];
  List<Map<String, dynamic>> conflicts = [];

  Future<void> initialize() async {
    paired = await repository.isPaired;
    await refreshLocal();
    initialized = true;
    notifyListeners();
    if (paired) await synchronize();
  }

  Future<void> pair(String raw, {String? deviceName}) async {
    syncing = true;
    syncError = null;
    notifyListeners();
    try {
      await repository.pair(PairingData.parse(raw), deviceName: deviceName);
      paired = true;
      lastSync = DateTime.now();
      await refreshLocal();
    } catch (error) {
      syncError = error.toString();
      rethrow;
    } finally {
      syncing = false;
      notifyListeners();
    }
  }

  Future<void> refreshLocal() async {
    recipes = await repository.recipes(
      query: query,
      category: category,
      cuisine: cuisine,
      tag: tag,
    );
    conflicts = await repository.conflicts();
    notifyListeners();
  }

  Future<void> setSearch(String value) async {
    query = value;
    await refreshLocal();
  }

  Future<void> setFilters({
    String? category,
    String? cuisine,
    String? tag,
  }) async {
    this.category = category ?? this.category;
    this.cuisine = cuisine ?? this.cuisine;
    this.tag = tag ?? this.tag;
    await refreshLocal();
  }

  Future<void> synchronize() async {
    if (!paired || syncing) return;
    syncing = true;
    syncError = null;
    notifyListeners();
    try {
      await repository.synchronize();
      lastSync = DateTime.now();
      await refreshLocal();
    } catch (error) {
      syncError = error.toString();
    } finally {
      syncing = false;
      notifyListeners();
    }
  }

  Future<void> unpair() async {
    await repository.unpair();
    paired = false;
    syncError = null;
    notifyListeners();
  }

  Future<List<PracticeLog>> logs(String recipeId) =>
      repository.practiceLogs(recipeId);

  Future<void> saveLog({
    PracticeLog? existing,
    required String recipeId,
    required DateTime cookedOn,
    required String notes,
    String? outcome,
    int? rating,
    XFile? photo,
  }) async {
    await repository.savePractice(
      existing: existing,
      recipeId: recipeId,
      cookedOn: cookedOn,
      notes: notes,
      outcome: outcome,
      rating: rating,
      photo: photo,
    );
    notifyListeners();
    await synchronize();
  }

  Future<void> deleteLog(PracticeLog log) async {
    await repository.deletePractice(log);
    notifyListeners();
    await synchronize();
  }

  Future<void> resolveConflict(
    Map<String, dynamic> conflict, {
    required bool keepMine,
    Map<String, dynamic>? merged,
  }) async {
    await repository.resolveConflict(
      conflict,
      keepMine: keepMine,
      merged: merged,
    );
    await refreshLocal();
    await synchronize();
  }

  @override
  void dispose() {
    repository.close();
    super.dispose();
  }
}
