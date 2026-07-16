import 'package:flutter/material.dart';

import 'app_controller.dart';
import 'screens.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final controller = AppController();
  runApp(RecipeMobileApp(controller: controller));
  controller.initialize();
}

class RecipeMobileApp extends StatefulWidget {
  const RecipeMobileApp({super.key, required this.controller});
  final AppController controller;

  @override
  State<RecipeMobileApp> createState() => _RecipeMobileAppState();
}

class _RecipeMobileAppState extends State<RecipeMobileApp>
    with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) widget.controller.synchronize();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    widget.controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Bili 菜谱',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xffc54b32)),
      useMaterial3: true,
      inputDecorationTheme: const InputDecorationTheme(
        border: OutlineInputBorder(),
      ),
    ),
    home: AnimatedBuilder(
      animation: widget.controller,
      builder: (_, _) {
        if (!widget.controller.initialized) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        return widget.controller.paired
            ? HomeScreen(controller: widget.controller)
            : PairingScreen(controller: widget.controller);
      },
    ),
  );
}
