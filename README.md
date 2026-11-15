# VideoToMD｜视频转知识

把公开抖音视频或本地媒体文件转换为视频、音频、逐字稿，或整理成 Obsidian 笔记。可以只执行其中一步，也可以从抖音链接完成整个流程。

![VideoToMD 封面](images/readme-cover.png)

## 下载 macOS 版

**[下载 VideoToMD v0.1.0 beta 2（Apple Silicon）](https://github.com/yumo-mt/video-to-md/releases/tag/v0.1.0-beta.2)**

在 Release 页的 **Assets** 中下载 `VideoToMD-arm64.dmg`。`Source code (zip)` 和 `Source code (tar.gz)` 只有源码，不能直接安装。

1. 打开 DMG，把 `VideoToMD.app` 拖入“应用程序”。
2. 从“应用程序”启动 VideoToMD，选择输入来源和需要的最终产物。
3. 如果要转写或生成笔记，在 App 顶部按提示下载语音模型；只下载视频或提取音频无需下载模型。

当前安装包仅适用于 **Apple Silicon Mac**。DMG 约 725 MB，已包含 App 所需的 Python 运行环境、FFmpeg 和 Chromium，无需预装 Python、FFmpeg 或 Chrome。语音模型**不在 DMG 中**：首次使用转写功能时，App 会提示下载约 2.1 GB 的模型，保存到 `~/Library/Application Support/video-to-md/models/`。选择“区分说话人”时还需按提示下载额外模型。

此版本尚未完成 Apple Developer ID 签名与公证，macOS 可能阻止首次打开。请确认文件来自本仓库的 Release 页面，再按系统提示手动允许打开。发布文件的 SHA-256 为：

```text
a2bf9601a37e1b20ae92112df8f572946110dd1e2cbc91229522dc5316a3f5a8  VideoToMD-arm64.dmg
```

可在终端运行 `shasum -a 256 ~/Downloads/VideoToMD-arm64.dmg` 核对下载文件。

## 功能与使用

| 输入来源 | 可选最终产物 |
| --- | --- |
| 公开抖音视频链接 | 视频文件、音频文件、逐字稿、Obsidian 笔记 |
| 本地视频 | 音频文件、逐字稿 |
| 本地音频 | 逐字稿 |

软件只执行得到所选产物所需的步骤。例如，从抖音链接生成 Obsidian 笔记会依次下载视频、提取音频、转写、整理笔记；只选“视频文件”则下载后结束。可以勾选“保留视频”“保留音频”“保留逐字稿与字幕”保存中间产物。未保留的任务缓存会在**成功 24 小时后**清理；失败任务缓存保留 7 天供排查。同一来源再次处理时，本次任务生成的同名输出文件会覆盖。

逐字稿和笔记可以选择“区分说话人”。说话人标记由模型推断，重叠讲话、声音相近或长录音可能分错，请人工核对。界面会显示模型状态、下载进度信息和处理步骤；模型下载目前按文件阶段显示，**不显示准确的字节百分比**。

### 生成 Obsidian 笔记

目前笔记功能的输入是**公开抖音视频链接**。选择 Obsidian 知识库文件夹，并填写兼容 Chat Completions 的 HTTPS API 地址、模型名称和自己的 API Key。使用 DeepSeek 可以点击“使用 DeepSeek 配置”填入接口地址和模型，再填写 API Key。选择“Obsidian 笔记”时还可编辑默认提示词；逐字稿由程序自动附加。

语音识别在本机运行，不需要语音识别 API Key。生成笔记时，逐字稿会发送给你选择的 AI 服务；费用和数据处理规则取决于该服务。API Key 保存在 macOS 钥匙串中，首次使用请允许钥匙串访问。

## 当前限制

- 仅处理公开可访问的抖音**视频**；图文、私密、已删除或需要登录的作品不支持。抖音页面变化或访问限制也可能导致下载失败。
- 这是测试版，尚未在干净的 Mac 上完成完整流程验证；处理重要内容前请检查输出。
- 本地视频和本地音频暂不能直接生成 Obsidian 笔记。

## 从源码运行 macOS App

以下步骤面向开发者；普通用户直接下载上面的 DMG 即可。源码运行需要 macOS、Python 3.13、FFmpeg 和 Chromium：

```bash
git clone -b codex/mac-app https://github.com/yumo-mt/video-to-md.git
cd video-to-md
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python -m playwright install chromium --no-shell
VIDEO_TO_MD_BROWSER_CHANNEL=chromium .venv/bin/python mac_app.py
```

在 Apple Silicon Mac 上重新构建安装包，还需安装 `pyinstaller`，然后运行：

```bash
.venv/bin/python -m pip install pyinstaller
PLAYWRIGHT_BROWSERS_PATH="$PWD/vendor-browsers" .venv/bin/python -m playwright install chromium --no-shell
bash build_mac.sh
bash package_dmg.sh
```

构建产物是 `dist/VideoToMD-arm64.dmg`。正式分发前仍需检查第三方组件许可，并完成 Developer ID 签名、公证和独立 Mac 验收。

## Codex Skill 与命令行

仓库也保留原有的 [Codex Skill](SKILL.md) 和命令行流程。它们与桌面 App 是两种使用方式；Skill 可将公开抖音视频转写、提炼并归档到 Obsidian。安装脚本见 [install.sh](install.sh)，配置示例见 [config.example.json](config.example.json)。

```bash
bash install.sh
bash run.sh check
bash run.sh extract '<公开抖音分享链接>'
```

在 Codex 中可输入：`用 $video-to-md 把这条公开抖音视频整理成知识笔记：<链接>`。命令行的 `extract` 输出临时目录 `out_dir`；生成带原文依据的 `knowledge.json` 后，可执行 `bash run.sh preview 'output/<作品 ID>'` 和 `bash run.sh archive 'output/<作品 ID>'`。详细步骤见 [SKILL.md](SKILL.md)。

本地 `config.json`、模型、输出、缓存和 API Key 不会随源码提交。语音模型从 ModelScope 原发布方下载；相关依赖和模型分别遵循上游许可。
