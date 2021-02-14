import os
import sys
import argparse
import logging
import yaml

import numpy as np
import torch
import torch.optim as optim
import torch.nn as nn
from munch import Munch
from tqdm.auto import tqdm
import wandb

from dataset.dataset import Im2LatexDataset
from models import get_model
from utils import *


def train(args):
    dataloader = Im2LatexDataset().load(args.data)
    dataloader.update(**args)
    device = args.device

    model = get_model(args)
    if args.load_chkpt is not None:
        model.load_state_dict(torch.load(args.load_chkpt, map_location=device))
    encoder, decoder = model.encoder, model.decoder
    opt = get_optimizer(args.optimizer)(model.parameters(), args.lr, betas=args.betas)
    scheduler = get_scheduler(args.scheduler)(opt, max_lr=args.max_lr, steps_per_epoch=len(dataloader)*2, epochs=args.epochs) # scheduler steps are weird.

    for e in range(args.epoch, args.epochs):
        args.epoch = e
        dset = tqdm(iter(dataloader))
        for i, (seq, im) in enumerate(dset):
            opt.zero_grad()
            tgt_seq, tgt_mask = seq['input_ids'].to(device), seq['attention_mask'].bool().to(device)
            encoded = encoder(im.to(device))
            loss = decoder(tgt_seq, mask=tgt_mask, context=encoded)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            opt.step()
            scheduler.step()

            dset.set_description('Loss: %.4f' % loss.item())
            if args.wandb:
                wandb.log({'train/loss': loss.item()})
            if (i+1) % args.sample_freq == 0:
                model.eval()
                dec = decoder.generate(torch.LongTensor([args.bos_token]*len(encoded[:args.test_samples]))[:, None].to(device), args.max_seq_len,
                                       eos_token=args.pad_token, context=encoded.detach()[:args.test_samples])
                pred = token2str(dec[:args.test_samples], dataloader.tokenizer)
                truth = token2str(seq['input_ids'], dataloader.tokenizer)
                if args.wandb:
                    table = wandb.Table(columns=["Truth", "Prediction"])
                    for k in range(min([len(pred), args.test_samples])):
                        table.add_data(truth[k], pred[k])
                    wandb.log({"test/examples": table})
                else:
                    print('\n%s\n%s' % (truth, pred))
                model.train()
        if (e+1) % args.save_freq == 0:
            torch.save(model.state_dict(), os.path.join(args.out_path, '%s_e%02d.pth' % (args.name, e+1)))
            yaml.dump(dict(args), open(os.path.join(args.out_path, 'config.yaml'), 'w+'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Train model')
    parser.add_argument('--config', default='settings/default.yaml', help='path to yaml config file', type=argparse.FileType('r'))
    parser.add_argument('-d', '--data', default='dataset/data/dataset.pkl', type=str, help='Path to Dataset pkl file')
    parser.add_argument('--no_cuda', action='store_true', help='Use CPU')
    parser.add_argument('--debug', action='store_true', help='DEBUG')
    parser.add_argument('--resume', help='path to checkpoint folder', action='store_true')

    parsed_args = parser.parse_args()
    with parsed_args.config as f:
        params = yaml.load(f, Loader=yaml.FullLoader)
    args = parse_args(Munch(params))

    logging.getLogger().setLevel(logging.DEBUG if parsed_args.debug else logging.WARNING)
    seed_everything(args.seed)
    if args.wandb:
        if not parsed_args.resume:
            args.id = wandb.util.generate_id()
        wandb.init(config=dict(args), resume='allow', name=args.name, id=args.id)
    train(args)
