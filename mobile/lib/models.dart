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
