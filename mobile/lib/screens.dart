import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import 'app_controller.dart';
import 'models.dart';

class PairingScreen extends StatefulWidget {
  const PairingScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<PairingScreen> createState() => _PairingScreenState();
}

class _PairingScreenState extends State<PairingScreen> {
  final scanner = MobileScannerController(
    formats: const [BarcodeFormat.qrCode],
  );
  final manual = TextEditingController();
  final deviceName = TextEditingController(
    text: Platform.isIOS ? '我的 iPhone' : '我的 Android',
  );
  bool busy = false;
  String? error;

  Future<void> pair(String value) async {
    if (busy || value.trim().isEmpty) return;
    setState(() {
      busy = true;
      error = null;
    });
    await scanner.stop();
    try {
      await widget.controller.pair(
        value.trim(),
        deviceName: deviceName.text.trim(),
      );
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
      await scanner.start();
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  void dispose() {
    scanner.dispose();
    manual.dispose();
    deviceName.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('连接菜谱服务器')),
    body: ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const Text('在 Mac 管理页打开“手机客户端”，生成二维码后扫描。HTTP 配对仅适用于可信家庭局域网。'),
        const SizedBox(height: 16),
        TextField(
          controller: deviceName,
          decoration: const InputDecoration(labelText: '设备名称'),
        ),
        const SizedBox(height: 16),
        SizedBox(
          height: 300,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(20),
            child: MobileScanner(
              controller: scanner,
              onDetect: (capture) {
                if (capture.barcodes.isNotEmpty) {
                  final value = capture.barcodes.first.rawValue;
                  if (value != null) pair(value);
                }
              },
            ),
          ),
        ),
        const SizedBox(height: 12),
        ExpansionTile(
          title: const Text('无法扫码？粘贴配对 JSON'),
          children: [
            TextField(controller: manual, minLines: 3, maxLines: 7),
            const SizedBox(height: 8),
            FilledButton(
              onPressed: busy ? null : () => pair(manual.text),
              child: const Text('连接'),
            ),
          ],
        ),
        if (busy)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: CircularProgressIndicator()),
          ),
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
      ],
    ),
  );
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int destination = 0;

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.controller,
    builder: (context, _) {
      final controller = widget.controller;
      final categories = {
        '',
        ...controller.recipes.map((recipe) => recipe.category),
      }.toList();
      final cuisines = {
        '',
        ...controller.recipes.map((recipe) => recipe.cuisine),
      }.toList();
      final tags = {
        '',
        ...controller.recipes.expand((recipe) => recipe.tags),
      }.toList();
      return Scaffold(
        appBar: AppBar(
          title: Text(destination == 0 ? '我的菜谱' : '本餐点菜'),
          actions: [
            IconButton(
              onPressed: controller.syncing ? null : controller.synchronize,
              icon: const Icon(Icons.sync),
              tooltip: '立即同步',
            ),
            IconButton(
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => ConflictScreen(controller: controller),
                ),
              ),
              icon: Badge(
                isLabelVisible: controller.conflicts.isNotEmpty,
                label: Text('${controller.conflicts.length}'),
                child: const Icon(Icons.merge),
              ),
              tooltip: '同步冲突',
            ),
            IconButton(
              onPressed: () => _confirmUnpair(context),
              icon: const Icon(Icons.link_off),
              tooltip: '重新配对',
            ),
          ],
        ),
        body: destination == 0
            ? Column(
                children: [
                  Material(
                    color: controller.syncError == null
                        ? Theme.of(context).colorScheme.secondaryContainer
                        : Theme.of(context).colorScheme.errorContainer,
                    child: ListTile(
                      dense: true,
                      leading: controller.syncing
                          ? const SizedBox.square(
                              dimension: 20,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Icon(
                              controller.syncError == null
                                  ? Icons.cloud_done
                                  : Icons.cloud_off,
                            ),
                      title: Text(
                        controller.syncing
                            ? '正在同步…'
                            : controller.syncError != null
                            ? '离线使用 · ${controller.syncError}'
                            : controller.lastSync == null
                            ? '本地缓存'
                            : '上次同步 ${_clock(controller.lastSync!)}',
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 6),
                    child: TextField(
                      onChanged: controller.setSearch,
                      decoration: const InputDecoration(
                        prefixIcon: Icon(Icons.search),
                        hintText: '标题、食材、步骤、技巧…',
                      ),
                    ),
                  ),
                  SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Row(
                      children: [
                        _filter(
                          '分类',
                          categories,
                          controller.category,
                          (value) => controller.setFilters(category: value),
                        ),
                        const SizedBox(width: 10),
                        _filter(
                          '菜系',
                          cuisines,
                          controller.cuisine,
                          (value) => controller.setFilters(cuisine: value),
                        ),
                        const SizedBox(width: 10),
                        _filter(
                          '标签',
                          tags,
                          controller.tag,
                          (value) => controller.setFilters(tag: value),
                        ),
                      ],
                    ),
                  ),
                  Expanded(
                    child: controller.recipes.isEmpty
                        ? const Center(
                            child: Padding(
                              padding: EdgeInsets.all(24),
                              child: Text('本地没有匹配菜谱。首次使用请连接 Mac 完成同步。'),
                            ),
                          )
                        : RefreshIndicator(
                            onRefresh: controller.synchronize,
                            child: ListView.separated(
                              padding: const EdgeInsets.all(16),
                              itemCount: controller.recipes.length,
                              separatorBuilder: (_, _) =>
                                  const SizedBox(height: 8),
                              itemBuilder: (context, index) {
                                final recipe = controller.recipes[index];
                                return Card(
                                  child: ListTile(
                                    title: Text(recipe.title),
                                    subtitle: Text(
                                      [
                                            recipe.category,
                                            recipe.cuisine,
                                            recipe.totalTime,
                                          ]
                                          .where((value) => value.isNotEmpty)
                                          .join(' · '),
                                    ),
                                    trailing: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        IconButton(
                                          key: ValueKey(
                                            'add-meal-${recipe.id}',
                                          ),
                                          onPressed:
                                              controller.isRecipeInMeal(
                                                recipe.id,
                                              )
                                              ? null
                                              : () => _addToMeal(
                                                  context,
                                                  controller,
                                                  recipe,
                                                ),
                                          icon: Icon(
                                            controller.isRecipeInMeal(recipe.id)
                                                ? Icons.check_circle
                                                : Icons.add_circle_outline,
                                          ),
                                          tooltip:
                                              controller.isRecipeInMeal(
                                                recipe.id,
                                              )
                                              ? '已加入本餐'
                                              : '加入本餐',
                                        ),
                                        const Icon(Icons.chevron_right),
                                      ],
                                    ),
                                    onTap: () => Navigator.push(
                                      context,
                                      MaterialPageRoute(
                                        builder: (_) => RecipeDetailScreen(
                                          controller: controller,
                                          recipe: recipe,
                                        ),
                                      ),
                                    ),
                                  ),
                                );
                              },
                            ),
                          ),
                  ),
                ],
              )
            : MealOrderView(
                controller: controller,
                onBrowseRecipes: () => setState(() => destination = 0),
              ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: destination,
          onDestinationSelected: (value) => setState(() => destination = value),
          destinations: [
            const NavigationDestination(
              icon: Icon(Icons.menu_book_outlined),
              selectedIcon: Icon(Icons.menu_book),
              label: '菜谱',
            ),
            NavigationDestination(
              icon: Badge(
                isLabelVisible: controller.mealItemCount > 0,
                label: Text('${controller.mealItemCount}'),
                child: const Icon(Icons.room_service_outlined),
              ),
              selectedIcon: Badge(
                isLabelVisible: controller.mealItemCount > 0,
                label: Text('${controller.mealItemCount}'),
                child: const Icon(Icons.room_service),
              ),
              label: '本餐',
            ),
          ],
        ),
      );
    },
  );

  Future<void> _confirmUnpair(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('重新配对服务器？'),
        content: const Text('本地菜谱、心得和待同步内容都会保留，只会清除当前设备令牌。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('重新配对'),
          ),
        ],
      ),
    );
    if (confirmed == true) await widget.controller.unpair();
  }

  Future<void> _addToMeal(
    BuildContext context,
    AppController controller,
    RecipeSummary recipe,
  ) async {
    final added = await controller.addRecipeToMeal(recipe);
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(added ? '已将“${recipe.title}”加入本餐' : '这道菜已经在本餐中'),
        action: SnackBarAction(
          label: '查看',
          onPressed: () => setState(() => destination = 1),
        ),
      ),
    );
  }

  Widget _filter(
    String label,
    List<String> values,
    String selected,
    ValueChanged<String> onChanged,
  ) => SizedBox(
    width: 140,
    child: DropdownButtonFormField<String>(
      initialValue: values.contains(selected) ? selected : '',
      decoration: InputDecoration(labelText: label, isDense: true),
      items: values
          .map(
            (value) => DropdownMenuItem(
              value: value,
              child: Text(
                value.isEmpty ? '全部' : value,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          )
          .toList(),
      onChanged: (value) => onChanged(value ?? ''),
    ),
  );
}

class MealOrderView extends StatefulWidget {
  const MealOrderView({
    super.key,
    required this.controller,
    required this.onBrowseRecipes,
  });

  final AppController controller;
  final VoidCallback onBrowseRecipes;

  @override
  State<MealOrderView> createState() => _MealOrderViewState();
}

class _MealOrderViewState extends State<MealOrderView> {
  final checkedShoppingItems = <String>{};

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final order = controller.mealOrder;
    if (order == null || controller.mealItems.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.room_service_outlined,
                size: 72,
                color: Theme.of(context).colorScheme.outline,
              ),
              const SizedBox(height: 16),
              Text('本餐还没有菜', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 8),
              const Text('从菜谱列表选择想吃的菜，系统会自动汇总采购清单。'),
              const SizedBox(height: 20),
              FilledButton.icon(
                onPressed: widget.onBrowseRecipes,
                icon: const Icon(Icons.add),
                label: const Text('去点菜'),
              ),
            ],
          ),
        ),
      );
    }
    final isCooking = order.status == MealOrderStatus.cooking;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
      children: [
        Card(
          color: Theme.of(context).colorScheme.primaryContainer,
          child: ListTile(
            leading: const Icon(Icons.today),
            title: Text('${order.title} · ${controller.mealItemCount} 道菜'),
            subtitle: Text(
              isCooking
                  ? '正在做饭 · 已完成 ${controller.completedMealItemCount} 道'
                  : '${order.mealDate} · 调整份量后再开始做饭',
            ),
            trailing: IconButton(
              onPressed: () => _confirmClearMeal(context),
              icon: const Icon(Icons.delete_sweep_outlined),
              tooltip: '清空本餐',
            ),
          ),
        ),
        const SizedBox(height: 8),
        ...controller.mealItems.map(
          (item) => _mealItemCard(context, item, isCooking),
        ),
        const SizedBox(height: 18),
        Text('采购清单', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 4),
        Text(
          '相同食材和相同单位会自动合并；复杂用量保留原文。',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 8),
        Card(
          child: Column(
            children: controller.shoppingList.map((entry) {
              final key = '${entry.name}\u0000${entry.amount}';
              return CheckboxListTile(
                value: checkedShoppingItems.contains(key),
                onChanged: (checked) => setState(() {
                  if (checked == true) {
                    checkedShoppingItems.add(key);
                  } else {
                    checkedShoppingItems.remove(key);
                  }
                }),
                title: Text(entry.name),
                subtitle: Text(entry.recipeTitles.join('、')),
                secondary: Text(entry.amount),
                controlAffinity: ListTileControlAffinity.leading,
              );
            }).toList(),
          ),
        ),
        const SizedBox(height: 20),
        FilledButton.icon(
          onPressed: _startCooking,
          icon: Icon(isCooking ? Icons.play_arrow : Icons.soup_kitchen),
          label: Text(isCooking ? '继续做饭' : '确认菜单并开始做饭'),
        ),
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: widget.onBrowseRecipes,
          icon: const Icon(Icons.add),
          label: const Text('继续点菜'),
        ),
      ],
    );
  }

  Widget _mealItemCard(
    BuildContext context,
    MealOrderItem item,
    bool isCooking,
  ) => Card(
    child: Padding(
      padding: const EdgeInsets.fromLTRB(8, 8, 4, 8),
      child: Column(
        children: [
          ListTile(
            leading: isCooking
                ? Checkbox(
                    value: item.completed,
                    onChanged: (value) => widget.controller
                        .setMealItemCompleted(item, value ?? false),
                  )
                : const Icon(Icons.restaurant_menu),
            title: Text(
              item.recipe.title,
              style: item.completed
                  ? const TextStyle(decoration: TextDecoration.lineThrough)
                  : null,
            ),
            subtitle: item.note.isEmpty ? null : Text(item.note),
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute(
                builder: (_) => RecipeDetailScreen(
                  controller: widget.controller,
                  recipe: item.recipe,
                ),
              ),
            ),
            trailing: PopupMenuButton<String>(
              onSelected: (action) {
                if (action == 'note') _editNote(context, item);
                if (action == 'remove') widget.controller.removeMealItem(item);
              },
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'note', child: Text('编辑备注')),
                PopupMenuItem(value: 'remove', child: Text('移出本餐')),
              ],
            ),
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              const Text('份量'),
              IconButton(
                onPressed: item.servingsMultiplier <= 0.5
                    ? null
                    : () => widget.controller.setMealItemMultiplier(
                        item,
                        item.servingsMultiplier - 0.5,
                      ),
                icon: const Icon(Icons.remove_circle_outline),
                tooltip: '减少份量',
              ),
              SizedBox(
                width: 44,
                child: Text(
                  '${item.servingsMultiplier.toStringAsFixed(1)}×',
                  textAlign: TextAlign.center,
                ),
              ),
              IconButton(
                onPressed: item.servingsMultiplier >= 4
                    ? null
                    : () => widget.controller.setMealItemMultiplier(
                        item,
                        item.servingsMultiplier + 0.5,
                      ),
                icon: const Icon(Icons.add_circle_outline),
                tooltip: '增加份量',
              ),
            ],
          ),
        ],
      ),
    ),
  );

  Future<void> _editNote(BuildContext context, MealOrderItem item) async {
    var draft = item.note;
    final note = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('给“${item.recipe.title}”加备注'),
        content: TextFormField(
          initialValue: item.note,
          autofocus: true,
          maxLength: 200,
          minLines: 2,
          maxLines: 4,
          onChanged: (value) => draft = value,
          decoration: const InputDecoration(hintText: '例如：少辣、不要香菜'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, draft),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    if (note != null) await widget.controller.setMealItemNote(item, note);
  }

  Future<void> _startCooking() async {
    await widget.controller.beginMealCooking();
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => MealCookingScreen(controller: widget.controller),
      ),
    );
  }

  Future<void> _confirmClearMeal(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('清空本餐？'),
        content: const Text('已选菜品、份量和备注都会删除。菜谱本身不会受到影响。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('清空'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      checkedShoppingItems.clear();
      await widget.controller.clearMeal();
    }
  }
}

class RecipeDetailScreen extends StatefulWidget {
  const RecipeDetailScreen({
    super.key,
    required this.controller,
    required this.recipe,
  });
  final AppController controller;
  final RecipeSummary recipe;

  @override
  State<RecipeDetailScreen> createState() => _RecipeDetailScreenState();
}

class _RecipeDetailScreenState extends State<RecipeDetailScreen> {
  int refresh = 0;

  @override
  Widget build(BuildContext context) {
    final recipe = widget.recipe;
    return Scaffold(
      appBar: AppBar(title: Text(recipe.title)),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _editLog(),
        icon: const Icon(Icons.edit_note),
        label: const Text('记录心得'),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 96),
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: [for (final tag in recipe.tags) Chip(label: Text(tag))],
          ),
          if (recipe.servings.isNotEmpty || recipe.totalTime.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                [
                  recipe.servings,
                  recipe.totalTime,
                ].where((value) => value.isNotEmpty).join(' · '),
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
          AnimatedBuilder(
            animation: widget.controller,
            builder: (context, _) {
              final added = widget.controller.isRecipeInMeal(recipe.id);
              return FilledButton.tonalIcon(
                onPressed: added ? null : _addToMeal,
                icon: Icon(added ? Icons.check_circle : Icons.add_circle),
                label: Text(added ? '已加入本餐' : '加入本餐'),
              );
            },
          ),
          _listSection(context, '食材', recipe.ingredients.map(_ingredient)),
          _listSection(context, '调料', recipe.seasonings.map(_ingredient)),
          Text('步骤', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 8),
          ...recipe.steps.indexed.map(
            (entry) => StepCard(
              controller: widget.controller,
              index: entry.$1,
              step: entry.$2,
            ),
          ),
          const SizedBox(height: 8),
          FilledButton.icon(
            onPressed: recipe.steps.isEmpty
                ? null
                : () => Navigator.push(
                    context,
                    MaterialPageRoute(
                      builder: (_) => CookingScreen(
                        controller: widget.controller,
                        recipe: recipe,
                      ),
                    ),
                  ),
            icon: const Icon(Icons.soup_kitchen),
            label: const Text('进入烹饪模式'),
          ),
          const SizedBox(height: 24),
          Text('实践日志', style: Theme.of(context).textTheme.headlineSmall),
          FutureBuilder<List<PracticeLog>>(
            key: ValueKey(refresh),
            future: widget.controller.logs(recipe.id),
            builder: (context, snapshot) {
              final logs = snapshot.data ?? const [];
              if (logs.isEmpty) {
                return const Padding(
                  padding: EdgeInsets.symmetric(vertical: 16),
                  child: Text('还没有实践记录。'),
                );
              }
              return Column(
                children: logs
                    .map(
                      (log) => Card(
                        child: ListTile(
                          title: Text(
                            '${log.cookedOn}${log.rating == null ? '' : ' · ${'★' * log.rating!}'}',
                          ),
                          subtitle: Text(log.notes),
                          leading: log.photoSha256 == null
                              ? null
                              : const Icon(Icons.photo),
                          onTap: () => _editLog(log),
                        ),
                      ),
                    )
                    .toList(),
              );
            },
          ),
        ],
      ),
    );
  }

  Future<void> _editLog([PracticeLog? log]) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => PracticeEditor(
          controller: widget.controller,
          recipe: widget.recipe,
          existing: log,
        ),
      ),
    );
    if (mounted) setState(() => refresh++);
  }

  Future<void> _addToMeal() async {
    await widget.controller.addRecipeToMeal(widget.recipe);
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('已将“${widget.recipe.title}”加入本餐')));
  }

  Widget _listSection(
    BuildContext context,
    String title,
    Iterable<String> lines,
  ) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: Theme.of(context).textTheme.headlineSmall),
        ...lines.map(
          (line) => Padding(
            padding: const EdgeInsets.symmetric(vertical: 3),
            child: Text('• $line'),
          ),
        ),
      ],
    ),
  );

  String _ingredient(Map<String, dynamic> value) => [
    value['name'],
    value['amount'],
    value['note'],
  ].where((item) => item != null && item.toString().isNotEmpty).join(' · ');
}

class StepCard extends StatelessWidget {
  const StepCard({
    super.key,
    required this.controller,
    required this.index,
    required this.step,
  });
  final AppController controller;
  final int index;
  final Map<String, dynamic> step;

  @override
  Widget build(BuildContext context) {
    final digest = step['image_sha256']?.toString();
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${index + 1}. ${step['title'] ?? ''}',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            if (digest != null)
              FutureBuilder<String?>(
                future: controller.repository.assetPath(digest),
                builder: (_, snapshot) =>
                    snapshot.data == null || !File(snapshot.data!).existsSync()
                    ? const SizedBox.shrink()
                    : Padding(
                        padding: const EdgeInsets.symmetric(vertical: 10),
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(12),
                          child: Image.file(
                            File(snapshot.data!),
                            fit: BoxFit.cover,
                          ),
                        ),
                      ),
              ),
            Text(step['action']?.toString() ?? ''),
            if (step['heat'] != null || step['duration'] != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  [
                    step['heat'],
                    step['duration'],
                  ].where((value) => value != null).join(' · '),
                ),
              ),
            if (step['tips'] != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text('提示：${step['tips']}'),
              ),
          ],
        ),
      ),
    );
  }
}

class CookingScreen extends StatefulWidget {
  const CookingScreen({
    super.key,
    required this.controller,
    required this.recipe,
    this.initialMultiplier = 1,
  });
  final AppController controller;
  final RecipeSummary recipe;
  final double initialMultiplier;

  @override
  State<CookingScreen> createState() => _CookingScreenState();
}

class _CookingScreenState extends State<CookingScreen> {
  int index = 0;
  late double multiplier;

  @override
  void initState() {
    super.initState();
    multiplier = widget.initialMultiplier.clamp(0.5, 4).toDouble();
  }

  @override
  Widget build(BuildContext context) {
    final steps = widget.recipe.steps;
    final step = steps[index];
    return Scaffold(
      appBar: AppBar(
        title: Text('${widget.recipe.title} · ${index + 1}/${steps.length}'),
      ),
      body: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Text('用量倍率'),
                const SizedBox(width: 12),
                Expanded(
                  child: Slider(
                    value: multiplier,
                    min: 0.5,
                    max: 4,
                    divisions: 14,
                    label: '${multiplier.toStringAsFixed(1)}×',
                    onChanged: (value) => setState(() => multiplier = value),
                  ),
                ),
                Text('${multiplier.toStringAsFixed(1)}×'),
              ],
            ),
            ExpansionTile(
              title: const Text('本次用量'),
              children: [
                ...widget.recipe.ingredients.map(
                  (item) => ListTile(
                    dense: true,
                    title: Text(item['name']?.toString() ?? ''),
                    trailing: Text(
                      scaleAmount(item['amount']?.toString() ?? '', multiplier),
                    ),
                  ),
                ),
                ...widget.recipe.seasonings.map(
                  (item) => ListTile(
                    dense: true,
                    title: Text(item['name']?.toString() ?? ''),
                    trailing: Text(
                      scaleAmount(item['amount']?.toString() ?? '', multiplier),
                    ),
                  ),
                ),
              ],
            ),
            Expanded(
              child: SingleChildScrollView(
                child: StepCard(
                  controller: widget.controller,
                  index: index,
                  step: step,
                ),
              ),
            ),
          ],
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
          child: Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: index == 0 ? null : () => setState(() => index--),
                  child: const Text('上一步'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton(
                  onPressed: () {
                    if (index < steps.length - 1) {
                      setState(() => index++);
                    } else {
                      Navigator.pop(context, true);
                    }
                  },
                  child: Text(index == steps.length - 1 ? '完成' : '下一步'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

String scaleAmount(String amount, double multiplier) {
  return scaleRecipeAmount(amount, multiplier);
}

class MealCookingScreen extends StatefulWidget {
  const MealCookingScreen({super.key, required this.controller});

  final AppController controller;

  @override
  State<MealCookingScreen> createState() => _MealCookingScreenState();
}

class _MealCookingScreenState extends State<MealCookingScreen> {
  String? selectedItemId;

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.controller,
    builder: (context, _) {
      final items = widget.controller.mealItems;
      if (items.isEmpty) {
        return const Scaffold(body: Center(child: Text('本餐已结束。')));
      }
      final incomplete = items.where((item) => !item.completed).toList();
      final selected = items.firstWhere(
        (item) => item.id == selectedItemId,
        orElse: () => incomplete.isNotEmpty ? incomplete.first : items.last,
      );
      return Scaffold(
        appBar: AppBar(title: const Text('本餐烹饪')),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            LinearProgressIndicator(
              value: items.isEmpty
                  ? 0
                  : widget.controller.completedMealItemCount / items.length,
            ),
            const SizedBox(height: 8),
            Text(
              '已完成 ${widget.controller.completedMealItemCount}/${items.length} 道菜',
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 16),
            ...items.map(
              (item) => Card(
                color: item.id == selected.id
                    ? Theme.of(context).colorScheme.secondaryContainer
                    : null,
                child: ListTile(
                  leading: Icon(
                    item.completed
                        ? Icons.check_circle
                        : Icons.radio_button_unchecked,
                    color: item.completed
                        ? Theme.of(context).colorScheme.primary
                        : null,
                  ),
                  title: Text(item.recipe.title),
                  subtitle: Text(
                    [
                      '${item.servingsMultiplier.toStringAsFixed(1)}× 份量',
                      if (item.note.isNotEmpty) item.note,
                    ].join(' · '),
                  ),
                  onTap: () => setState(() => selectedItemId = item.id),
                ),
              ),
            ),
            const SizedBox(height: 16),
            if (incomplete.isEmpty)
              FilledButton.icon(
                onPressed: _finishMeal,
                icon: const Icon(Icons.celebration),
                label: const Text('完成本餐'),
              )
            else ...[
              Text(
                '接下来：${selected.recipe.title}',
                style: Theme.of(context).textTheme.headlineSmall,
              ),
              if (selected.note.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text('备注：${selected.note}'),
                ),
              const SizedBox(height: 12),
              FilledButton.icon(
                onPressed: selected.completed ? null : () => _cook(selected),
                icon: const Icon(Icons.soup_kitchen),
                label: Text(
                  selected.recipe.steps.isEmpty ? '标记这道菜完成' : '开始烹饪这道菜',
                ),
              ),
            ],
          ],
        ),
      );
    },
  );

  Future<void> _cook(MealOrderItem item) async {
    var completed = true;
    if (item.recipe.steps.isNotEmpty) {
      completed =
          await Navigator.push<bool>(
            context,
            MaterialPageRoute(
              builder: (_) => CookingScreen(
                controller: widget.controller,
                recipe: item.recipe,
                initialMultiplier: item.servingsMultiplier,
              ),
            ),
          ) ??
          false;
    }
    if (!completed) return;
    await widget.controller.setMealItemCompleted(item, true);
    if (!mounted) return;
    final incomplete = widget.controller.mealItems.where(
      (candidate) => !candidate.completed,
    );
    setState(() => selectedItemId = incomplete.firstOrNull?.id);
    if (incomplete.isEmpty) await _finishMeal();
  }

  Future<void> _finishMeal() async {
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('本餐完成'),
        content: const Text('所有菜都已完成，辛苦了，开饭吧！'),
        actions: [
          FilledButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('开饭'),
          ),
        ],
      ),
    );
    if (!mounted) return;
    await widget.controller.finishMealCooking();
    if (mounted) Navigator.pop(context);
  }
}

class PracticeEditor extends StatefulWidget {
  const PracticeEditor({
    super.key,
    required this.controller,
    required this.recipe,
    this.existing,
  });
  final AppController controller;
  final RecipeSummary recipe;
  final PracticeLog? existing;

  @override
  State<PracticeEditor> createState() => _PracticeEditorState();
}

class _PracticeEditorState extends State<PracticeEditor> {
  late final TextEditingController notes;
  late DateTime date;
  String outcome = '';
  int? rating;
  XFile? photo;
  bool busy = false;
  String? error;

  @override
  void initState() {
    super.initState();
    notes = TextEditingController(text: widget.existing?.notes ?? '');
    date = DateTime.tryParse(widget.existing?.cookedOn ?? '') ?? DateTime.now();
    outcome = widget.existing?.outcome ?? '';
    rating = widget.existing?.rating;
  }

  @override
  void dispose() {
    notes.dispose();
    super.dispose();
  }

  Future<void> pick(ImageSource source) async {
    final selected = await ImagePicker().pickImage(source: source);
    if (selected != null) setState(() => photo = selected);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(widget.existing == null ? '记录实践心得' : '编辑实践心得')),
    body: ListView(
      padding: const EdgeInsets.all(20),
      children: [
        ListTile(
          title: const Text('实践日期'),
          subtitle: Text(date.toIso8601String().substring(0, 10)),
          trailing: const Icon(Icons.calendar_month),
          onTap: () async {
            final selected = await showDatePicker(
              context: context,
              firstDate: DateTime(2000),
              lastDate: DateTime.now().add(const Duration(days: 1)),
              initialDate: date,
            );
            if (selected != null) setState(() => date = selected);
          },
        ),
        DropdownButtonFormField<String>(
          initialValue: outcome,
          decoration: const InputDecoration(labelText: '结果'),
          items: const [
            DropdownMenuItem(value: '', child: Text('未选择')),
            DropdownMenuItem(value: 'success', child: Text('成功')),
            DropdownMenuItem(value: 'partial', child: Text('部分成功')),
            DropdownMenuItem(value: 'failed', child: Text('失败')),
          ],
          onChanged: (value) => outcome = value ?? '',
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<int?>(
          initialValue: rating,
          decoration: const InputDecoration(labelText: '评分'),
          items: [
            const DropdownMenuItem<int?>(value: null, child: Text('未评分')),
            ...List.generate(
              5,
              (index) => DropdownMenuItem<int?>(
                value: index + 1,
                child: Text('${index + 1} 星'),
              ),
            ),
          ],
          onChanged: (value) => rating = value,
        ),
        const SizedBox(height: 12),
        TextField(
          controller: notes,
          minLines: 5,
          maxLines: 12,
          maxLength: 5000,
          onChanged: (_) => setState(() {}),
          decoration: const InputDecoration(
            labelText: '心得',
            hintText: '火候、口感、下次如何改进…',
          ),
        ),
        if (photo != null)
          Image.file(File(photo!.path), height: 220, fit: BoxFit.cover)
        else if (widget.existing?.localPhotoPath != null &&
            File(widget.existing!.localPhotoPath!).existsSync())
          Image.file(
            File(widget.existing!.localPhotoPath!),
            height: 220,
            fit: BoxFit.cover,
          ),
        Wrap(
          spacing: 8,
          children: [
            OutlinedButton.icon(
              onPressed: busy ? null : () => pick(ImageSource.camera),
              icon: const Icon(Icons.camera_alt),
              label: const Text('拍照'),
            ),
            OutlinedButton.icon(
              onPressed: busy ? null : () => pick(ImageSource.gallery),
              icon: const Icon(Icons.photo_library),
              label: const Text('相册'),
            ),
          ],
        ),
        const SizedBox(height: 12),
        FilledButton(
          onPressed: busy || notes.text.trim().isEmpty ? null : save,
          child: const Text('保存'),
        ),
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        if (widget.existing != null)
          TextButton(
            onPressed: busy ? null : delete,
            child: const Text('删除这条日志'),
          ),
      ],
    ),
  );

  Future<void> save() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.controller.saveLog(
        existing: widget.existing,
        recipeId: widget.recipe.id,
        cookedOn: date,
        notes: notes.text,
        outcome: outcome.isEmpty ? null : outcome,
        rating: rating,
        photo: photo,
      );
      if (mounted) Navigator.pop(context);
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> delete() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.controller.deleteLog(widget.existing!);
      if (mounted) Navigator.pop(context);
    } catch (exception) {
      if (mounted) setState(() => error = exception.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }
}

class ConflictScreen extends StatefulWidget {
  const ConflictScreen({super.key, required this.controller});
  final AppController controller;

  @override
  State<ConflictScreen> createState() => _ConflictScreenState();
}

class _ConflictScreenState extends State<ConflictScreen> {
  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.controller,
    builder: (context, _) => Scaffold(
      appBar: AppBar(
        title: Text('同步冲突（${widget.controller.conflicts.length}）'),
      ),
      body: widget.controller.conflicts.isEmpty
          ? const Center(child: Text('没有待处理冲突。'))
          : ListView(
              children: widget.controller.conflicts
                  .map(
                    (conflict) => Card(
                      margin: const EdgeInsets.all(12),
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text(
                              '服务器版本',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                            Text(
                              (conflict['server'] as Map)['notes']
                                      ?.toString() ??
                                  '',
                            ),
                            const SizedBox(height: 12),
                            const Text(
                              '我的离线版本',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                            Text(
                              (conflict['incoming'] as Map)['notes']
                                      ?.toString() ??
                                  '',
                            ),
                            Wrap(
                              spacing: 8,
                              children: [
                                TextButton(
                                  onPressed: () =>
                                      widget.controller.resolveConflict(
                                        conflict,
                                        keepMine: false,
                                      ),
                                  child: const Text('保留服务器'),
                                ),
                                FilledButton.tonal(
                                  onPressed: () =>
                                      widget.controller.resolveConflict(
                                        conflict,
                                        keepMine: true,
                                      ),
                                  child: const Text('保留我的'),
                                ),
                                FilledButton(
                                  onPressed: () => merge(conflict),
                                  child: const Text('手工合并'),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  )
                  .toList(),
            ),
    ),
  );

  Future<void> merge(Map<String, dynamic> conflict) async {
    final incoming = Map<String, dynamic>.from(conflict['incoming'] as Map);
    final text = TextEditingController(
      text: incoming['notes']?.toString() ?? '',
    );
    final mergedNotes = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('合并心得'),
        content: TextField(controller: text, minLines: 5, maxLines: 12),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, text.text),
            child: const Text('应用'),
          ),
        ],
      ),
    );
    text.dispose();
    if (mergedNotes != null && mergedNotes.trim().isNotEmpty) {
      await widget.controller.resolveConflict(
        conflict,
        keepMine: false,
        merged: {...incoming, 'notes': mergedNotes.trim()},
      );
    }
  }
}

String _clock(DateTime value) =>
    '${value.hour.toString().padLeft(2, '0')}:${value.minute.toString().padLeft(2, '0')}';
