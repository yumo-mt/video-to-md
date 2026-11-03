# video-to-md

把公开抖音视频转成按主题分类的 Obsidian 知识笔记。视频解析和下载使用网络；FFmpeg 与 FunASR 在本机转写，转写无需 API Key。标题、摘要、分类、标签和有依据的知识关联由运行本 Skill 的 Agent 生成，Agent 的模型费用取决于你选用的服务。

## 支持范围

首版面向 macOS、Codex、公开可访问的抖音视频和本地 Obsidian vault。图文提取代码仍处于实验阶段，暂不列为支持功能。私密、已删除或需要登录的视频无法保证解析。

## 安装

需要 Python 3.11+、FFmpeg、Google Chrome、网络连接，以及约 2–3 GB 的模型磁盘空间。模型从 ModelScope 原发布方下载，不包含在仓库中。

```bash
git clone git@github.com:yumo-mt/video-to-md.git ~/.codex/skills/video-to-md
cd ~/.codex/skills/video-to-md
python3 -m venv .venv
.venv/bin/python scripts/setup.py --install
cp config.example.json config.json
```

编辑 `config.json` 的 `vault` 为已有的 Obsidian vault 绝对路径，也可运行时传 `--vault` 或设置 `DY_VAULT`。`models_dir` 默认在项目内。可选设置 `engine` 为 `paraformer` 或 `sensevoice` 以减少模型下载；默认 `hybrid` 需要两套识别模型。

```bash
.venv/bin/python scripts/setup.py --models
.venv/bin/python scripts/setup.py
```

若没有 Google Chrome，可将 `browser_channel` 设为 `chromium`，并运行 `.venv/bin/python -m playwright install chromium`。环境检查通过后，重新打开 Codex 聊天即可使用安装在默认 Skill 目录中的本 Skill；其他 Agent 可按 [SKILL.md](SKILL.md) 的步骤执行。

## 使用

向 Agent 提供公开抖音分享链接和目标 vault，要求提炼知识笔记。手动提取逐字稿可运行：

```bash
.venv/bin/python scripts/dy_pipeline.py '<公开抖音分享链接>' --vault '<vault 绝对路径>' --no-video
```

命令会输出 `out_dir`，目录名是作品 ID。`--vault` 使下载前的重复归档检查生效。脚本只负责本地转写；Agent 需要阅读逐字稿并生成带原文依据的 `knowledge.json`，然后预览、归档：

```bash
.venv/bin/python scripts/note_builder.py 'output/<作品 ID>' --dry-run --require-knowledge
.venv/bin/python scripts/note_builder.py 'output/<作品 ID>' --vault '<vault 绝对路径>' --require-knowledge
```

笔记写入 `<vault>/<主题分类>/<知识标题>.md`。笔记属性包含原视频地址 `url`，笔记之间建立有依据的双向链接。归档成功后，`output/<作品 ID>` 中的视频、音频转写和其他原始文件会被删除；失败时保留以便重试。不会在 vault 中创建来源目录或 `assets` 目录。

## 成本与数据流

| 环节 | 执行位置 | 费用说明 |
| --- | --- | --- |
| 链接解析、视频下载 | 网络 + 本机 Chrome | 无本项目 API Key |
| 音频处理 | 本机 FFmpeg | 无模型费用 |
| 语音识别 | 本机 FunASR、Paraformer、SenseVoice 等 | 首次下载模型；运行时无 ASR API 费用 |
| 摘要、分类、标题、标签、知识关联 | 用户选用的 Agent | 依 Agent 服务计费 |
| 笔记写入、双向链接、临时文件清理 | 本机脚本 | 无模型费用 |

## 目录与隐私

`config.json`、`hotwords.txt`、`models/`、`output/`、`.venv/` 和缓存均被 Git 忽略。`config.example.json` 是可公开的模板。不要把真实 vault、视频、逐字稿或自己的术语表提交到公开仓库。`models/` 不随 Skill 分发；相关模型和依赖各自遵循其上游许可。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
```

真实抖音页面可能变化，浏览器解析也可能受访问限制。遇到失败时先检查公开链接能否在本机 Chrome 访问，再查看脚本报错。
