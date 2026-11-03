# video-to-md

![从短视频到知识笔记的视觉封面](images/readme-cover.png)

把公开抖音视频转成按主题分类的 Obsidian 知识笔记。视频解析和下载使用网络；FFmpeg 在本机提取音频，FunASR 在本机转写，不需要语音识别 API Key。标题、摘要、分类、标签和知识关联由 Codex 等 AI 助手生成，模型费用取决于你选用的服务。

## 支持范围

首版面向 macOS、Codex、公开可访问的抖音视频和本地 Obsidian 知识库。图文、私密、已删除或需要登录的作品暂不支持。

## 安装

安装本项目需要 macOS、网络连接和约 6 GB 可用磁盘空间；完整的知识归档还需要 Codex 和一个 Obsidian 知识库（vault）。Python、FFmpeg、浏览器运行环境、Python 依赖和语音模型由安装脚本检查并补齐；不要求预先安装 Python、Homebrew 或 Chrome。模型从 ModelScope 原发布方下载，不包含在仓库中。安装系统软件可能需要管理员密码。

如果已安装 Git，可运行：

```bash
mkdir -p ~/.agents/skills
git clone https://github.com/yumo-mt/video-to-md.git ~/.agents/skills/video-to-md
cd ~/.agents/skills/video-to-md
bash install.sh
```

**没有 Git？** 在 [GitHub 页面](https://github.com/yumo-mt/video-to-md) 点 **Code → Download ZIP**，解压后打开终端，输入 `cd `（末尾有空格），把解压后的文件夹拖进终端，再按回车。随后运行 `bash install.sh`。脚本会把该目录加入 Codex 的 Skill 目录。它会先列出缺失项并征求确认，再按需安装 Homebrew、Python 3.13、FFmpeg 和 Playwright Chromium，安装 Python 依赖、下载模型，并询问已有 Obsidian 知识库的路径。

已有依赖和模型会复用。只检查、不安装可运行 `bash run.sh check`。需要修改设置时编辑本地 `config.json`；默认 `hybrid` 使用两套识别模型，改成 `paraformer` 或 `sensevoice` 可以减少下载量。

安装脚本会自动创建项目专用的 Python 环境，使用者无需手动管理。Codex 会自动发现新 Skill；如果没有出现，重启 Codex。其他 Agent 可按 [SKILL.md](SKILL.md) 的步骤执行。

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

安装后，在 Codex 中输入：`用 $video-to-md 把这条公开抖音视频整理成知识笔记：<链接>`。Codex 会依照 [SKILL.md](SKILL.md) 完成转写、提炼和归档。

如只想手动提取逐字稿，在项目目录运行：

```bash
bash run.sh extract '<公开抖音分享链接>'
```

命令会输出临时文件夹路径 `out_dir`，目录名是作品 ID。安装时若没有设置知识库路径，可加 `--vault '<知识库文件夹路径>'`；设置后，下载前会检查这条作品是否已经归档。脚本只负责本地转写。

以下归档命令供 AI 助手或手动操作的用户使用。执行前，必须先根据逐字稿生成带原文依据的 `knowledge.json`；只想拿到逐字稿时无需继续：

```bash
bash run.sh preview 'output/<作品 ID>'
bash run.sh archive 'output/<作品 ID>'
```

如果安装时没有设置知识库路径，归档命令也需加 `--vault '<知识库文件夹路径>'`。笔记写入 `<知识库>/<主题分类>/<知识标题>.md`，属性包含原视频地址 `url`，相关知识笔记会互相链接。归档成功后，该作品的临时视频、逐字稿等文件会被删除；失败时保留供重试。知识库中不会创建来源目录或 `assets` 目录。

## 成本与数据流

| 环节 | 执行位置 | 费用说明 |
| --- | --- | --- |
| 链接解析、视频下载 | 网络 + 本机 Chrome 或 Chromium | 无本项目 API Key |
| 音频处理 | 本机 FFmpeg | 无模型费用 |
| 语音识别 | 本机 FunASR、Paraformer、SenseVoice 等 | 首次下载模型；运行时无 ASR API 费用 |
| 摘要、分类、标题、标签、知识关联 | 用户选用的 AI 助手 | 依该服务计费 |
| 笔记写入、双向链接、临时文件清理 | 本机脚本 | 无模型费用 |

## 目录与隐私

`config.json`、`hotwords.txt`、`models/`、`output/`、`.venv/` 和缓存均被 Git 忽略。`config.example.json` 是可公开的模板。不要把真实 vault、视频、逐字稿或自己的术语表提交到公开仓库。`models/` 不随 Skill 分发；相关模型和依赖各自遵循其上游许可。

## 检查安装

```bash
bash run.sh check
```

真实抖音页面可能变化，浏览器解析也可能受访问限制。遇到失败时先检查链接是否公开可访问，再查看脚本报错。
