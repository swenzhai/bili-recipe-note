import 'package:bili_recipe_mobile/app_controller.dart';
import 'package:bili_recipe_mobile/local_database.dart';
import 'package:bili_recipe_mobile/models.dart';
import 'package:bili_recipe_mobile/recipe_repository.dart';
import 'package:bili_recipe_mobile/screens.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();

  testWidgets('the full offline meal ordering flow works from the home list', (
    tester,
  ) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(800, 1200);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    const recipeId = '88888888-8888-4888-8888-888888888888';
    final recipe = RecipeSummary.fromPayload({
      'id': recipeId,
      'title': '鱼香肉丝',
      'category': '中餐',
      'cuisine': '川菜',
      'tags': ['下饭'],
      'ingredients': [
        {'name': '猪肉', 'amount': '200克'},
      ],
      'steps': [
        {'title': '炒制', 'action': '快速翻炒'},
      ],
      'assets': [],
    });
    final controller = _FakeMealController(recipe);

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData(splashFactory: NoSplash.splashFactory),
        home: HomeScreen(controller: controller),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('add-meal-$recipeId')));
    await tester.pumpAndSettle();

    expect(controller.mealItemCount, 1);
    expect(find.textContaining('已将“鱼香肉丝”加入本餐'), findsOneWidget);
    await tester.pump(const Duration(seconds: 5));
    await tester.pumpAndSettle();

    await tester.tap(find.text('本餐'));
    await tester.pumpAndSettle();
    expect(find.text('本餐点菜'), findsOneWidget);
    expect(find.text('鱼香肉丝'), findsWidgets);
    expect(find.text('采购清单'), findsOneWidget);

    await tester.tap(find.byTooltip('增加份量'));
    await tester.pumpAndSettle();
    expect(find.text('1.5×'), findsOneWidget);
    expect(find.text('300克'), findsOneWidget);

    await tester.tap(find.byType(PopupMenuButton<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('编辑备注'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField), '少辣');
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(find.text('少辣'), findsOneWidget);

    await tester.ensureVisible(find.text('确认菜单并开始做饭'));
    await tester.drag(find.byType(ListView), const Offset(0, -160));
    await tester.pumpAndSettle();
    await tester.tap(find.text('确认菜单并开始做饭'));
    await tester.pumpAndSettle();
    expect(find.text('本餐烹饪'), findsOneWidget);
    await tester.tap(find.text('开始烹饪这道菜'));
    await tester.pumpAndSettle();
    expect(find.text('鱼香肉丝 · 1/1'), findsOneWidget);
    expect(find.text('1.5×'), findsOneWidget);
    await tester.tap(find.text('完成'));
    await tester.pumpAndSettle();
    expect(find.text('本餐完成'), findsOneWidget);
    await tester.tap(find.text('开饭'));
    await tester.pumpAndSettle();
    expect(find.text('本餐还没有菜'), findsOneWidget);
    expect(controller.mealOrder, isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });
}

class _FakeMealController extends AppController {
  _FakeMealController(this.recipe)
    : super(
        repository: RecipeRepository(
          database: LocalDatabase(
            factory: databaseFactoryFfi,
            databasePath: inMemoryDatabasePath,
          ),
        ),
      ) {
    initialized = true;
    paired = true;
    recipes = [recipe];
  }

  final RecipeSummary recipe;

  @override
  Future<bool> addRecipeToMeal(RecipeSummary recipe) async {
    if (isRecipeInMeal(recipe.id)) return false;
    const now = '2026-08-10T10:00:00Z';
    mealOrder = const MealOrder(
      id: '99999999-9999-4999-8999-999999999999',
      title: '本餐',
      mealDate: '2026-08-10',
      status: MealOrderStatus.draft,
      createdAt: now,
      updatedAt: now,
    );
    mealItems = [
      MealOrderItem(
        id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        orderId: mealOrder!.id,
        recipeId: recipe.id,
        recipe: recipe,
        servingsMultiplier: 1,
        note: '',
        sortOrder: 0,
        completed: false,
      ),
    ];
    notifyListeners();
    return true;
  }

  @override
  Future<void> setMealItemMultiplier(
    MealOrderItem item,
    double multiplier,
  ) async {
    _replace(item.copyWith(servingsMultiplier: multiplier));
  }

  @override
  Future<void> setMealItemNote(MealOrderItem item, String note) async {
    _replace(item.copyWith(note: note));
  }

  @override
  Future<void> setMealItemCompleted(MealOrderItem item, bool completed) async {
    _replace(item.copyWith(completed: completed));
  }

  @override
  Future<void> beginMealCooking() async {
    mealOrder = mealOrder?.copyWith(status: MealOrderStatus.cooking);
    notifyListeners();
  }

  @override
  Future<void> finishMealCooking() async {
    mealOrder = null;
    mealItems = [];
    notifyListeners();
  }

  void _replace(MealOrderItem updated) {
    mealItems = [
      for (final item in mealItems)
        if (item.id == updated.id) updated else item,
    ];
    notifyListeners();
  }
}
