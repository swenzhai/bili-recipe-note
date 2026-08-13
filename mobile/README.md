# Bili Recipe Mobile

Flutter 离线客户端。菜谱正文来自同一局域网中的 Mac，所有查询直接读取手机 SQLite；实践心得先保存在本机，重连后通过 outbox 增量同步。

手机端内置“本餐点菜”模块：可从菜谱列表或详情页加入菜品，按 0.5 倍调整份量、填写口味备注，并自动合并生成采购清单。确认菜单后会依次进入分步烹饪模式，完成状态保存在手机本地；点菜单目前不参与多设备同步。

## 本机运行

```bash
cd mobile
./flutterw pub get
./flutterw doctor -v
./flutterw test
./flutterw run -d "Bili Recipe iPhone"
```

`flutterw` 默认使用 `/Users/swen/Documents/SDK/flutter`，也可通过 `FLUTTER_ROOT` 指定其他 Flutter SDK。
它默认使用 Flutter 国内镜像，并自动加入当前 Mac 的用户级 CocoaPods 路径；本机已安装 CocoaPods 1.16.2，无需修改全局 PATH。

第一阶段只验证 iOS。Android 工程和局域网配置已保留，但不要求安装 Android SDK 或运行 Android 测试。

启动 Mac 端 `start-ui-mac.command` 后，在管理页选择“手机客户端”，生成二维码并用 App 扫描。当前协议使用 HTTP + 设备令牌，只能用于可信家庭局域网。
