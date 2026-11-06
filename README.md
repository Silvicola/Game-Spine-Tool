# Game Spine Tool

整理游戏 Spine 资源的 Windows 桌面工具。界面是 WinUI 3，资源处理交给 Python 引擎，
两者通过标准输入输出的 JSON Lines 协议通信。

## 当前进展

首个支持的游戏是 **Echocalypse · 绯色回响 · 国际服**，目前整理角色立绘类 Spine 资源
（atlas / skel / png 三件套）。主链路已跑通：动态解析资源清单、筛选条目、解密纹理、
按 atlas 成套配对归档，并带阶段进度、运行日志与历史记录。

Live2D 与第二个游戏尚未接入，资源类型会在真正需要时再抽象。

## 功能

- 选择游戏与输出目录，一键整理
- 动态解析版本号与资源清单，不写死版本
- 通过 HTTP Range 读取 zip 中央目录，只取目标条目，不下载整包
- 还原 UF 头部并解码 ETC2 压缩纹理，输出标准 PNG
- 解析 atlas 声明的图片页，与 skel 成套配对，保留原始文件名
- 每组写入 `_meta.json` 与 `_src.json`，记录版本、来源与文件字节数
- 增量跳过已完成组：比对 CDN 版本、源条目签名与产物字节数，只重跑变化或失败组
- 阶段进度、运行日志、历史浮层（按次落盘，可一键复制日志）
- 可选 2 倍超分，产物另存 `Upscaled/`，不改动原模型

界面是单页卡片布局，Mica 背景配 Acrylic 历史浮层，遵循 Fluent Design。

## 目录结构

```
engine/gst_engine/   Python 引擎：协议、清单解析、条目筛选、解码、归档、超分
src/GameSpineTool/   C# WinUI 3 界面与服务
tools/               验证脚本；可选超分组件需自行放置
```

## 构建与运行

需要 .NET 10 SDK 与 Windows。WindowsAppSDK 的构建目标由 NuGet 包提供，
不强依赖系统级 Windows Kits。

```bash
cd src/GameSpineTool
dotnet build -c Release -p:SelfContained=true
```

运行时需要 Python 3.13 以及 `numpy`、`Pillow`、`texture2ddecoder`、`urllib3`。
引擎按「程序目录/python → 项目 .venv → PATH」的顺序查找解释器，
也可用环境变量 `GST_PYTHON` 指定。发布页提供的 Windows x64 便携版已随包带上
.NET 与 Python 运行时，解压即可运行。

## 可选超分组件

本仓库**不包含**第三方二进制。要使用 2 倍超分，请自行获取
`realesrgan-ncnn-vulkan.exe` 与 `realesr-animevideov3-x2` 模型，放到
`tools/realesrgan/`（模型放 `models/` 子目录）。缺少该组件不影响整理主流程。

## 说明

本工具不分发任何游戏素材，只整理使用者自行取得的数据；资源版权归原权利人所有。
资源解析规则参考公开教程，实现以实际请求结果为准。

## 许可

MIT，见 [LICENSE](LICENSE)。
