import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'models.dart';

class ApiClient {
  ApiClient(this.baseUrl, {this.accessToken});

  final String baseUrl;
  final String? accessToken;

  Map<String, String> get _headers => {
    'Content-Type': 'application/json',
    if (accessToken != null) 'Authorization': 'Bearer $accessToken',
  };

  Future<Map<String, dynamic>> pair(
    PairingData data,
    String deviceName,
    String deviceId,
  ) async {
    final response = await http
        .post(
          Uri.parse('${data.baseUrl}/api/v1/pair'),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode({
            'schema_version': 1,
            'pairing_token': data.pairingToken,
            'device_name': deviceName,
            'device_id': deviceId,
          }),
        )
        .timeout(const Duration(seconds: 10));
    return _jsonResponse(response);
  }

  Future<Map<String, dynamic>> sync(
    int cursor,
    List<Map<String, dynamic>> operations,
  ) async {
    final response = await http
        .post(
          Uri.parse('$baseUrl/api/v1/sync'),
          headers: _headers,
          body: jsonEncode({
            'schema_version': 1,
            'cursor': cursor,
            'operations': operations,
          }),
        )
        .timeout(const Duration(seconds: 20));
    return _jsonResponse(response);
  }

  Future<void> uploadAsset(String sha256, Uint8List bytes) async {
    final response = await http
        .put(
          Uri.parse('$baseUrl/api/v1/assets/$sha256'),
          headers: {..._headers, 'Content-Type': 'image/jpeg'},
          body: bytes,
        )
        .timeout(const Duration(seconds: 30));
    _ensureSuccess(response);
  }

  Future<Uint8List> downloadAsset(String sha256) async {
    final response = await http
        .get(Uri.parse('$baseUrl/api/v1/assets/$sha256'), headers: _headers)
        .timeout(const Duration(seconds: 30));
    _ensureSuccess(response);
    return response.bodyBytes;
  }

  static Map<String, dynamic> _jsonResponse(http.Response response) {
    _ensureSuccess(response);
    return jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
  }

  static void _ensureSuccess(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      var detail = 'HTTP ${response.statusCode}';
      try {
        final body = jsonDecode(utf8.decode(response.bodyBytes));
        detail = body is Map ? body['detail']?.toString() ?? detail : detail;
      } catch (_) {}
      throw ApiException(detail, response.statusCode);
    }
  }
}

class ApiException implements Exception {
  const ApiException(this.message, this.statusCode);
  final String message;
  final int statusCode;

  @override
  String toString() => message;
}
