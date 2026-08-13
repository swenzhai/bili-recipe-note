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
  MealOrder? mealOrder;
  List<MealOrderItem> mealItems = [];

  int get mealItemCount => mealItems.length;
  int get completedMealItemCount =>
      mealItems.where((item) => item.completed).length;
  List<ShoppingListEntry> get shoppingList => buildShoppingList(mealItems);

  bool isRecipeInMeal(String recipeId) =>
      mealItems.any((item) => item.recipeId == recipeId);

  Future<void> initialize() async {
    paired = await repository.isPaired;
    await refreshLocal();
    await refreshMeal();
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

  Future<void> refreshMeal() async {
    mealOrder = await repository.activeMealOrder();
    mealItems = mealOrder == null
        ? []
        : await repository.mealOrderItems(mealOrder!.id);
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
      await refreshMeal();
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

  Future<bool> addRecipeToMeal(RecipeSummary recipe) async {
    final added = await repository.addRecipeToMeal(recipe);
    await refreshMeal();
    return added;
  }

  Future<void> setMealItemMultiplier(
    MealOrderItem item,
    double multiplier,
  ) async {
    final normalized = multiplier.clamp(0.5, 4).toDouble();
    await repository.updateMealOrderItem(
      item.copyWith(servingsMultiplier: normalized),
    );
    await refreshMeal();
  }

  Future<void> setMealItemNote(MealOrderItem item, String note) async {
    await repository.updateMealOrderItem(item.copyWith(note: note.trim()));
    await refreshMeal();
  }

  Future<void> setMealItemCompleted(MealOrderItem item, bool completed) async {
    await repository.updateMealOrderItem(item.copyWith(completed: completed));
    await refreshMeal();
  }

  Future<void> removeMealItem(MealOrderItem item) async {
    await repository.removeMealOrderItem(item);
    await refreshMeal();
  }

  Future<void> clearMeal() async {
    final order = mealOrder;
    if (order == null) return;
    await repository.clearMealOrder(order);
    await refreshMeal();
  }

  Future<void> beginMealCooking() async {
    final order = mealOrder;
    if (order == null || mealItems.isEmpty) return;
    await repository.startMealOrder(order);
    await refreshMeal();
  }

  Future<void> finishMealCooking() async {
    final order = mealOrder;
    if (order == null) return;
    await repository.completeMealOrder(order);
    await refreshMeal();
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
