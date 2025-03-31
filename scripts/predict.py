import argparse
import gc
import itertools
import os
import sys

import pandas as pd
import torch
from torch.utils.data import DataLoader
from transformers import AutoTokenizer

sys.path.append(".")
from datasets import PLTNUMDataset
from models import PLTNUM
from utils import seed_everything
from tqdm import tqdm


def parse_args():
    parser = argparse.ArgumentParser(
        description="Prediction script for protein sequence classification/regression."
    )
    parser.add_argument(
        "--data_path",
        type=str,
        required=True,
        help="Path to the input data.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="westlake-repl/SaProt_650M_AF2",
        help="Pretrained model name or path.",
    )
    parser.add_argument(
        "--architecture",
        type=str,
        default="SaProt",
        help="Model architecture: 'ESM2', 'SaProt', or 'LSTM'.",
    )
    parser.add_argument(
        "--model_path",
        type=str,
        required=True,
        help="Path to the model for prediction.",
    )
    parser.add_argument("--batch_size", type=int, default=4, help="Batch size.")
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for reproducibility.",
    )
    parser.add_argument(
        "--use_amp",
        action="store_true",
        default=False,
        help="Use AMP for mixed precision prediction.",
    )
    parser.add_argument(
        "--num_workers",
        type=int,
        default=4,
        help="Number of workers for data loading.",
    )
    parser.add_argument(
        "--max_length",
        type=int,
        default=512,
        help="Maximum input sequence length. Two tokens are used fo <cls> and <eos> tokens. So the actual length of input sequence is max_length - 2. Padding or truncation is applied to make the length of input sequence equal to max_length.",
    )
    parser.add_argument(
        "--used_sequence",
        type=str,
        default="left",
        help="Which part of the sequence to use: 'left', 'right', 'both', or 'internal'.",
    )
    parser.add_argument(
        "--padding_side",
        type=str,
        default="right",
        help="Padding side: 'right' or 'left'.",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="./output",
        help="Output directory.",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="classification",
        help="Task type: 'classification' or 'regression'.",
    )
    parser.add_argument(
        "--sequence_col",
        type=str,
        default="aa_foldseek",
        help="Column name fot the input sequence.",
    )

    return parser.parse_args()


def predict_fn(dataloader, model, cfg):
    model.eval()
    predictions = []

    for inputs, _ in tqdm(dataloader, desc="Predicting", unit="batch"):
        inputs = inputs.to(cfg.device)
        with torch.no_grad():
            # Use the correct device type for autocast
            device_type = "mps" if cfg.device == "mps" else "cuda" if cfg.device == "cuda" else "cpu"
            # Use autocast for mixed precision
            with torch.amp.autocast(device_type=device_type, enabled=cfg.use_amp):
                preds = (
                    torch.sigmoid(model(inputs))
                    if cfg.task == "classification"
                    else model(inputs)
                )
        predictions += preds.cpu().tolist()

    return predictions


def predict(folds, model_path, cfg):
    dataset = PLTNUMDataset(cfg, folds, train=False)
    dataloader = DataLoader(
        dataset,
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=cfg.num_workers,
        pin_memory=True,
        drop_last=False,
    )

    model = PLTNUM(cfg)
    model.load_state_dict(torch.load(model_path, map_location=cfg.device))
    model.to(cfg.device)

    predictions = predict_fn(dataloader, model, cfg)
    predictions = list(itertools.chain.from_iterable(predictions))

    folds["raw prediction values"] = predictions
    if cfg.task == "classification":
        folds["binary prediction values"] = [1 if x > 0.5 else 0 for x in predictions]
    if config.device == "cuda":
        torch.cuda.empty_cache()
    elif config.device == "mps":
        torch.mps.empty_cache()
    gc.collect()
    return folds


if __name__ == "__main__":
    config = parse_args()
    print(f"torch.backends.mps.is_available(): {torch.backends.mps.is_available()}")
    config.device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    print("Using device:", config.device)
    
    config.token_length = 2 if config.architecture == "SaProt" else 1

    if not os.path.exists(config.output_dir):
        os.makedirs(config.output_dir)

    if config.used_sequence == "both":
        config.max_length += 1

    seed_everything(config.seed)

    df = pd.read_csv(config.data_path)

    tokenizer = AutoTokenizer.from_pretrained(
        config.model, padding_side=config.padding_side
    )
    config.tokenizer = tokenizer

    result = predict(df, config.model_path, config)
    result.to_csv(os.path.join(config.output_dir, "result.csv"), index=False)
