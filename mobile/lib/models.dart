import 'dart:convert';
import 'dart:io';

class PairingData {
  const PairingData({
    required this.serverId,
    required this.baseUrl,
    required this.pairingToken,
    required this.expiresAt,
  });

  final String serverId;
  final String baseUrl;
  final String pairingToken;
  final DateTime expiresAt;

  factory PairingData.parse(String raw) {
    final value = Map<String, dynamic>.from(jsonDecode(raw) as Map);
    if (value['schema_version'] != 1) throw const FormatException('不支持的配对协议版本');
    final baseUrl =
        value['base_url']?.toString().replaceAll(RegExp(r'/+$'), '') ?? '';
    if (!baseUrl.startsWith('http://')) {
      throw const FormatException('第一版只支持局域网 HTTP 地址');
    }
    final host = Uri.parse(baseUrl).host;
    if (!_privateHost(host)) throw const FormatException('服务器必须使用私有局域网地址');
    final expires = DateTime.parse(value['expires_at'].toString());
    if (expires.isBefore(DateTime.now().toUtc())) {
      throw const FormatException('配对二维码已过期');
    }
    return PairingData(
      serverId: value['server_id'].toString(),
      baseUrl: baseUrl,
      pairingToken: value['pairing_token'].toString(),
      expiresAt: expires,
    );
  }

  static bool _privateHost(String host) {
    if (host == 'localhost' || host.endsWith('.local')) return true;
    final address = InternetAddress.tryParse(host);
    if (address == null) return false;
    if (address.isLoopback || address.isLinkLocal) return true;
    final bytes = address.rawAddress;
    if (address.type == InternetAddressType.IPv4) {
      return bytes[0] == 10 ||
          (bytes[0] == 172 && bytes[1] >= 16 && bytes[1] <= 31) ||
          (bytes[0] == 192 && bytes[1] == 168);
    }
    return bytes.isNotEmpty && (bytes[0] & 0xfe) == 0xfc;
  }
}

class RecipeSummary {
  RecipeSummary({required this.id, required this.payload});

  final String id;
  final Map<String, dynamic> payload;

  factory RecipeSummary.fromPayload(Map<String, dynamic> payload) =>
      RecipeSummary(
        id: payload['id'].toString(),
        payload: Map<String, dynamic>.from(payload),
      );

  String get title => payload['title']?.toString() ?? '未命名菜谱';
  String get uploader => payload['uploader']?.toString() ?? '';
  String get category => payload['category']?.toString() ?? '未分类';
  String get cuisine => payload['cuisine']?.toString() ?? '未分类';
  String get servings => payload['servings']?.toString() ?? '';
  String get totalTime => payload['total_time']?.toString() ?? '';
  List<String> get tags => _strings(payload['tags']);
  List<Map<String, dynamic>> get ingredients => _maps(payload['ingredients']);
  List<Map<String, dynamic>> get seasonings => _maps(payload['seasonings']);
  List<Map<String, dynamic>> get steps => _maps(payload['steps']);
  List<String> get summaryTips => _strings(payload['summary_tips']);
  List<Map<String, dynamic>> get assets => _maps(payload['assets']);

  String get searchableText => <String>[
    title,
    uploader,
    category,
    cuisine,
    ...tags,
    ...ingredients.expand(
      (item) => item.values.map((value) => value?.toString() ?? ''),
    ),
    ...seasonings.expand(
      (item) => item.values.map((value) => value?.toString() ?? ''),
    ),
    ...steps.expand(
      (item) => item.values.map((value) => value?.toString() ?? ''),
    ),
    ...summaryTips,
  ].join(' ').toLowerCase();

  static List<String> _strings(dynamic value) =>
      value is List ? value.map((item) => item.toString()).toList() : const [];

  static List<Map<String, dynamic>> _maps(dynamic value) => value is List
      ? value
            .whereType<Map>()
            .map((item) => Map<String, dynamic>.from(item))
            .toList()
      : const [];
}

enum MealOrderStatus {
  draft,
  cooking,
  completed;

  static MealOrderStatus parse(String value) => values.firstWhere(
    (status) => status.name == value,
    orElse: () => MealOrderStatus.draft,
  );
}

class MealOrder {
  const MealOrder({
    required this.id,
    required this.title,
    required this.mealDate,
    required this.status,
    required this.createdAt,
    required this.updatedAt,
  });

  final String id;
  final String title;
  final String mealDate;
  final MealOrderStatus status;
  final String createdAt;
  final String updatedAt;

  MealOrder copyWith({
    String? title,
    MealOrderStatus? status,
    String? updatedAt,
  }) => MealOrder(
    id: id,
    title: title ?? this.title,
    mealDate: mealDate,
    status: status ?? this.status,
    createdAt: createdAt,
    updatedAt: updatedAt ?? this.updatedAt,
  );
}

class MealOrderItem {
  const MealOrderItem({
    required this.id,
    required this.orderId,
    required this.recipeId,
    required this.recipe,
    required this.servingsMultiplier,
    required this.note,
    required this.sortOrder,
    required this.completed,
  });

  final String id;
  final String orderId;
  final String recipeId;
  final RecipeSummary recipe;
  final double servingsMultiplier;
  final String note;
  final int sortOrder;
  final bool completed;

  MealOrderItem copyWith({
    double? servingsMultiplier,
    String? note,
    int? sortOrder,
    bool? completed,
  }) => MealOrderItem(
    id: id,
    orderId: orderId,
    recipeId: recipeId,
    recipe: recipe,
    servingsMultiplier: servingsMultiplier ?? this.servingsMultiplier,
    note: note ?? this.note,
    sortOrder: sortOrder ?? this.sortOrder,
    completed: completed ?? this.completed,
  );
}

class ShoppingListEntry {
  const ShoppingListEntry({
    required this.name,
    required this.amount,
    required this.recipeTitles,
  });

  final String name;
  final String amount;
  final List<String> recipeTitles;
}

String scaleRecipeAmount(String amount, double multiplier) {
  if (multiplier == 1 || amount.isEmpty || ['少许', '适量'].any(amount.contains)) {
    return amount;
  }
  final matches = RegExp(r'\d+(?:\.\d+)?').allMatches(amount).toList();
  if (matches.length != 1) return amount;
  final match = matches.single;
  final original = double.tryParse(match.group(0)!);
  if (original == null) return amount;
  final scaled = original * multiplier;
  final text = _formatAmountNumber(scaled);
  return amount.replaceRange(match.start, match.end, text);
}

List<ShoppingListEntry> buildShoppingList(Iterable<MealOrderItem> items) {
  final accumulators = <String, _ShoppingAccumulator>{};
  for (final item in items) {
    for (final ingredient in [
      ...item.recipe.ingredients,
      ...item.recipe.seasonings,
    ]) {
      final name = ingredient['name']?.toString().trim() ?? '';
      if (name.isEmpty) continue;
      final rawAmount = ingredient['amount']?.toString().trim() ?? '';
      final amount = scaleRecipeAmount(rawAmount, item.servingsMultiplier);
      final key = name.toLowerCase();
      final accumulator = accumulators.putIfAbsent(
        key,
        () => _ShoppingAccumulator(name),
      );
      accumulator.add(amount, item.recipe.title);
    }
  }
  final entries = accumulators.values.map((item) => item.build()).toList();
  entries.sort((left, right) => left.name.compareTo(right.name));
  return entries;
}

String _formatAmountNumber(double value) => value == value.roundToDouble()
    ? value.toInt().toString()
    : value.toStringAsFixed(1).replaceFirst(RegExp(r'\.0$'), '');

class _ShoppingAccumulator {
  _ShoppingAccumulator(this.name);

  final String name;
  final Map<String, double> numericAmounts = {};
  final List<String> literalAmounts = [];
  final List<String> recipeTitles = [];

  void add(String amount, String recipeTitle) {
    if (!recipeTitles.contains(recipeTitle)) recipeTitles.add(recipeTitle);
    if (amount.isEmpty) return;
    final match = RegExp(r'^\s*(\d+(?:\.\d+)?)\s*(.*?)\s*$').firstMatch(amount);
    if (match == null) {
      if (!literalAmounts.contains(amount)) literalAmounts.add(amount);
      return;
    }
    final value = double.tryParse(match.group(1)!);
    final unit = match.group(2)!.trim();
    if (value == null) {
      if (!literalAmounts.contains(amount)) literalAmounts.add(amount);
      return;
    }
    numericAmounts[unit] = (numericAmounts[unit] ?? 0) + value;
  }

  ShoppingListEntry build() {
    final parts = <String>[
      ...numericAmounts.entries.map(
        (entry) => '${_formatAmountNumber(entry.value)}${entry.key}',
      ),
      ...literalAmounts,
    ];
    return ShoppingListEntry(
      name: name,
      amount: parts.isEmpty ? '按需准备' : parts.join(' + '),
      recipeTitles: List.unmodifiable(recipeTitles),
    );
  }
}

class PracticeLog {
  const PracticeLog({
    required this.id,
    required this.recipeId,
    required this.cookedOn,
    required this.notes,
    this.outcome,
    this.rating,
    this.photoSha256,
    this.localPhotoPath,
    this.version = 0,
    this.deletedAt,
  });

  final String id;
  final String recipeId;
  final String cookedOn;
  final String notes;
  final String? outcome;
  final int? rating;
  final String? photoSha256;
  final String? localPhotoPath;
  final int version;
  final String? deletedAt;

  Map<String, dynamic> toProtocolJson() => {
    'schema_version': 1,
    'id': id,
    'recipe_id': recipeId,
    'cooked_on': cookedOn,
    'outcome': outcome,
    'rating': rating,
    'notes': notes,
    'photo_sha256': photoSha256,
    'version': version,
    'deleted_at': deletedAt,
  };

  factory PracticeLog.fromJson(
    Map<String, dynamic> value, {
    String? localPhotoPath,
  }) => PracticeLog(
    id: value['id'].toString(),
    recipeId: value['recipe_id'].toString(),
    cookedOn: value['cooked_on'].toString(),
    notes: value['notes']?.toString() ?? '',
    outcome: value['outcome']?.toString(),
    rating: (value['rating'] as num?)?.toInt(),
    photoSha256: value['photo_sha256']?.toString(),
    localPhotoPath: localPhotoPath,
    version: (value['version'] as num?)?.toInt() ?? 0,
    deletedAt: value['deleted_at']?.toString(),
  );
}
