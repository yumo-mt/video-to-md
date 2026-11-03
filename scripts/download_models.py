#!/usr/bin/env python3
"""从原发布方 ModelScope 下载语音模型；不随 Skill 分发模型权重。"""

from pathlib import Path

from config import load

MODELS = {
    "fsmn-vad": ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "model.pt"),
    "paraformer-zh": ("iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "model.pt"),
    "ct-punc": ("iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch", "model.pt"),
    "sensevoice": ("iic/SenseVoiceSmall", "model.pt"),
    "campplus": ("iic/speech_campplus_sv_zh-cn_16k-common", "campplus_cn_common.bin"),
}


def download_required(cfg: dict) -> None:
    from modelscope.hub.snapshot_download import snapshot_download

    engine = cfg.get("engine", "hybrid")
    required = [cfg["vad_model"]]
    if engine in ("hybrid", "auto", "paraformer"):
        required += [cfg["asr_model"], cfg["punc_model"]]
    if engine in ("hybrid", "auto", "sensevoice"):
        required += [cfg["sensevoice_model"]]
    if cfg.get("spk_model") and cfg.get("download_speaker_model"):
        required.append(cfg["spk_model"])

    root = Path(cfg["_models_dir"])
    for name in dict.fromkeys(required):
        model_id, weight = MODELS[name]
        target = root / name
        if (target / weight).is_file():
            print(f"已存在: {name}")
            continue
        print(f"下载 https://modelscope.cn/models/{model_id} → {target}", flush=True)
        snapshot_download(model_id=model_id, local_dir=str(target))
        if not (target / weight).is_file():
            raise FileNotFoundError(f"模型下载后缺少权重: {target / weight}")


if __name__ == "__main__":
    download_required(load())
