<div align="center">
  <img src="assets/banner.png" alt="Barotrauma Mod Manager 横幅" width="100%">
  <h1>Barotrauma Mod Manager</h1>
  <p><strong>适用于 Barotrauma 的 Steam 创意工坊与模组加载顺序管理器。</strong></p>
  <p><a href="README.md">English</a> · <a href="README.ru.md">Русский</a></p>
</div>

Barotrauma Mod Manager 可以搜索 Steam 创意工坊中的模组和合集，将内容安装到 `LocalMods`，管理多个模组配置，并把选定的加载顺序应用到游戏。

> **项目状态：** 社区开发项目，仍在积极维护。当前源码版本为 2.7.1。经过 Windows 环境验证的 EXE 将在测试完成后发布到 GitHub Releases。

Project website: [Barotrauma Mod Manager](https://shiokazee.github.io/Barotrauma-Mod-Manager/) · Source code: [GitHub repository](https://github.com/ShiokazeE/Barotrauma-Mod-Manager)

## 功能

- 使用个人 Steam Web API Key 搜索创意工坊模组和合集。
- 一键下载模组到 `LocalMods` 并添加到当前配置。
- 导入合集，包括嵌套合集。
- 创建、复制、切换和删除多个配置。
- 手动调整加载顺序，并提供保守的自动排序。
- 导入和导出原生 `ModLists/*.xml`，保留作者指定的顺序。
- 检查模组更新并下载新版本。
- 扫描可能存在冲突的 XML 标识符。
- 安装 LuaCs 客户端补丁，并支持备份与回滚。
- 安全解压归档，并在修改游戏配置前创建备份。

## 截图

<div align="center">
  <img src="assets/screenshots/manager-2.7.png" alt="Barotrauma Mod Manager 配置界面" width="100%">
</div>

## 下载

前往 **[GitHub Releases](https://github.com/ShiokazeE/Barotrauma-Mod-Manager/releases)**，在经过验证的 Windows 安装包发布后下载最新版本。

解压后运行 `BarotraumaModManager.exe`。配置文件、SteamCMD 数据和加密后的 API Key 保存在当前 Windows 用户的数据目录中，不会放在 EXE 旁边。

## 从源码运行

需要 Windows 10/11 和 Python 3.10 或更高版本。

```powershell
py -3 -m pip install -r requirements.txt
py -3 barotrauma_mod_manager.py
```

Pillow 用于显示创意工坊缩略图。未安装 Pillow 时，搜索和下载仍可使用，但不显示缩略图。

## 构建 Windows EXE

1. 安装 Python 3.10+，并启用 Python Launcher（`py`）。
2. 将 `barotrauma_mod_manager.py` 与 `build_exe.bat` 放在同一目录。
3. 运行 `build_exe.bat`。
4. 脚本会在 BAT 文件旁生成 `BarotraumaModManager.exe`。

推送 `v*` 格式的 Git 标签时，GitHub Actions 也会自动构建 Windows EXE。详见[构建工作流](.github/workflows/build-windows.yml)。

## Steam Web API Key

创意工坊搜索使用 `IPublishedFileService/QueryFiles`。请在管理器的 Workshop 设置中输入自己的 Steam Web API Key。Windows 使用 DPAPI 加密保存该密钥；它不会被写入公开仓库或配置 JSON。

- [IPublishedFileService 文档](https://partner.steamgames.com/doc/webapi/IPublishedFileService)
- [ISteamRemoteStorage 文档](https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage)

**不要将 API Key、个人配置文件、SteamCMD 缓存或游戏目录提交到 Git。** `.gitignore` 已排除常见的本地状态与凭据文件。

## 下载机制

如果模组已存在于 Steam 客户端缓存中，管理器会优先复用；之后会尝试可用的 Steam CDN 地址，失败时回退到 SteamCMD。Steam 并不会为所有 Workshop 项目提供直接文件 URL。管理器不会绕过所有权、订阅、隐私或访问权限限制。

大型合集最多可使用三个相互隔离的 SteamCMD 工作进程，每个进程都有独立缓存。增加进程数量不保证下载速度按比例提升。

## 加载顺序与 Barotrauma XML

管理器区分两种 XML：

- **`Barotrauma/ModLists/<preset>.xml`**：用于分享的作者预设，包含 `<mods>`、`<Vanilla />` 和按顺序排列的 `<Workshop ... />`。导入时保留作者顺序；导出时生成相同格式。
- **`config_player.xml`**：本地游戏激活配置。应用配置时，管理器会在 `contentpackages/regularpackages` 中写入有序的 `<package path="LocalMods/<ID>/filelist.xml" />` 条目，并提前备份原文件。

作者提供的顺序比通用启发式规则更可靠。自动排序采用保守策略，保留每个分组内原有的相对顺序，并在存在 Lua For Barotrauma（`2559634234`）时将其放在第一位。请始终检查排序预览，并遵循模组作者的特殊说明。

## 测试

运行核心回归测试：

```powershell
py -3 tests/test_barotrauma_mod_manager.py
```

运行 Workshop 滚动与窗口缩放测试（需要 Windows 图形桌面会话）：

```powershell
py -3 tests/test_workshop_gui_hotfix.py
```

测试使用临时目录和模拟 Steam 响应，不会执行真实的创意工坊下载。

## 报告问题

请提供管理器版本、Windows 版本、复现步骤和相关日志行。**上传日志前请删除 API Key、账户信息及其他私人数据。**

## 参与贡献

欢迎提交 Bug 报告和小型 Pull Request。提交之前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

目前尚未选择开源许可证。在添加许可证文件之前，所有权利仍归版权所有者；仓库公开并不自动授予重新分发或发布修改版本的许可。待项目维护者确认条款后再添加许可证。

## 免责声明

这是一个非官方社区工具。本项目与 FakeFish、Daedalic Entertainment 或 Valve 没有关联，也未获得其背书。Barotrauma 和 Steam 是其各自所有者的商标。