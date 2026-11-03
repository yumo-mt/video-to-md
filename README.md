# video-to-md

把公开抖音视频转成按主题分类的 Obsidian 知识笔记。视频解析和下载使用网络；FFmpeg 与 FunASR 在本机转写，转写无需 API Key。标题、摘要、分类、标签和有依据的知识关联由运行本 Skill 的 Agent 生成，Agent 的模型费用取决于你选用的服务。

## 支持范围

首版面向 macOS、Codex、公开可访问的抖音视频和本地 Obsidian vault。图文提取代码仍处于实验阶段，暂不列为支持功能。私密、已删除或需要登录的视频无法保证解析。

## 安装

安装本项目需要 macOS、网络连接和约 6 GB 可用磁盘空间；完整的知识归档还需要 Codex 和一个 Obsidian vault。Python、FFmpeg、浏览器运行环境、Python 依赖和语音模型由安装脚本检查并补齐；不要求预先安装 Python、Homebrew 或 Chrome。模型从 ModelScope 原发布方下载，不包含在仓库中。安装系统软件可能需要管理员密码。

如果已安装 Git，可运行：

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/yumo-mt/video-to-md.git ~/.codex/skills/video-to-md
cd ~/.codex/skills/video-to-md
bash install.sh
```

**没有 Git？** 在 [GitHub 页面](https://github.com/yumo-mt/video-to-md) 点 **Code → Download ZIP**，解压到任意文件夹，在终端进入该文件夹后运行 `bash install.sh`。脚本会将该目录加入 Codex 的 Skill 目录。它会先列出缺失项并征求确认，再安装 Homebrew（若需要）、Python 3.13、FFmpeg 和 Playwright Chromium（若没有 Chrome），创建项目专用 Python 环境、下载模型，并询问现有 Obsidian vault 路径。已有依赖和模型会复用。

只检查、不安装可运行 `bash install.sh --check`。需要修改设置时编辑本地 `config.json`；也可运行时传 `--vault` 或设置 `DY_VAULT`。默认 `hybrid` 需要两套识别模型；把 `engine` 改为 `paraformer` 或 `sensevoice` 可以减少模型下载。

安装完成后重新打开 Codex 聊天即可使用本 Skill；其他 Agent 可按 [SKILL.md](SKILL.md) 的步骤执行。需要手动排错时，运行 `.venv/bin/python scripts/setup.py` 查看具体缺失项。

### 语音模型的来源和位置

`bash install.sh` 从以下 ModelScope 页面下载模型，默认放在项目的 `models/` 下；已有权重会跳过。`config.json` 的 `models_dir` 可改为其他目录，表中的子目录名保持不变。模型权重不会提交到 Git。

| 用途 | 模型页面 | 本地目录 | 下载条件 |
| --- | --- | --- | --- |
| 语音分段 | [FSMN-VAD](https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch) | `models/fsmn-vad/` | 所有引擎 |
| 中文识别 | [SeACo-Paraformer](https://modelscope.cn/models/iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch) | `models/paraformer-zh/` | `hybrid`、`auto`、`paraformer` |
| 标点恢复 | [CT-Transformer](https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch) | `models/ct-punc/` | `hybrid`、`auto`、`paraformer` |
| 多语种识别 | [SenseVoiceSmall](https://modelscope.cn/models/iic/SenseVoiceSmall) | `models/sensevoice/` | `hybrid`、`auto`、`sensevoice` |
| 说话人识别 | [CAM++](https://modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common) | `models/campplus/` | 可选；在 `config.json` 设置 `spk_model` 为 `campplus`、`download_speaker_model` 为 `true` 后重跑安装脚本 |

默认 `hybrid` 下载前四项；CAM++ 默认不下载。运行视频提取时加 `--spk` 才会启用已配置的说话人模型。下载与目录选择的实现见 `scripts/download_models.py` 和 `config.example.json`。

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
