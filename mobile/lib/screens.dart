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

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key, required this.controller});
  final AppController controller;

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: controller,
    builder: (context, _) {
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
          title: const Text('我的菜谱'),
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
        body: Column(
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
                        separatorBuilder: (_, _) => const SizedBox(height: 8),
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
                              trailing: const Icon(Icons.chevron_right),
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
    if (confirmed == true) await controller.unpair();
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
  });
  final AppController controller;
  final RecipeSummary recipe;

  @override
  State<CookingScreen> createState() => _CookingScreenState();
}

class _CookingScreenState extends State<CookingScreen> {
  int index = 0;
  double multiplier = 1;

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
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: index == 0
                        ? null
                        : () => setState(() => index--),
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
                        Navigator.pop(context);
                      }
                    },
                    child: Text(index == steps.length - 1 ? '完成' : '下一步'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

String scaleAmount(String amount, double multiplier) {
  if (multiplier == 1 || amount.isEmpty || ['少许', '适量'].any(amount.contains)) {
    return amount;
  }
  final matches = RegExp(r'\d+(?:\.\d+)?').allMatches(amount).toList();
  if (matches.length != 1) return amount;
  final match = matches.single;
  final original = double.tryParse(match.group(0)!);
  if (original == null) return amount;
  final scaled = original * multiplier;
  final text = scaled == scaled.roundToDouble()
      ? scaled.toInt().toString()
      : scaled.toStringAsFixed(1);
  return amount.replaceRange(match.start, match.end, text);
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
