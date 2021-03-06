import torch
import torch.nn as nn
from datetime import datetime
from pathlib import Path
import argparse

from generator import ConvDataGenerator, ConvMaskGenerator

from critic import ConvCritic
from masked_image import BlockMaskedImage, IndepMaskedImage

from imputer import UNetImputer
from misgan_impute import misgan_impute


use_cuda = torch.cuda.is_available()
device = torch.device('cuda' if use_cuda else 'cpu')


def parallelize(model):
    return nn.DataParallel(model).to(device)


def main():
    parser = argparse.ArgumentParser()

    # resume from checkpoint
    parser.add_argument('--resume')

    # path of CelebA dataset
    parser.add_argument('--data-dir', default='dataset')

    # training options
    parser.add_argument('--workers', type=int, default=0)
    parser.add_argument('--epoch', type=int, default=1000)
    parser.add_argument('--batch-size', type=int, default=2)
    parser.add_argument('--pretrain', default=None)
    parser.add_argument('--imputeronly', action='store_true')

    # log options: 0 to disable plot-interval or save-interval
    parser.add_argument('--plot-interval', type=int, default=50)
    parser.add_argument('--save-interval', type=int, default=499)
    parser.add_argument('--prefix', default='impute')

    # mask options (data): block|indep
    parser.add_argument('--mask', default='block')
    # option for block: set to 0 for variable size
    parser.add_argument('--block-len', type=int, default=48)
    # option for indep:
    parser.add_argument('--obs-prob', type=float, default=.8)
    parser.add_argument('--obs-prob-high', type=float, default=None)

    # model options
    parser.add_argument('--tau', type=float, default=.5)
    parser.add_argument('--alpha', type=float, default=.1)   # 0: separate
    parser.add_argument('--beta', type=float, default=.1)
    parser.add_argument('--gamma', type=float, default=0)
    # options for mask generator: sigmoid, hardsigmoid, fusion
    parser.add_argument('--maskgen', default='fusion')
    parser.add_argument('--gp-lambda', type=float, default=10)
    parser.add_argument('--n-critic', type=int, default=5)
    parser.add_argument('--n-latent', type=int, default=128)

    args = parser.parse_args()

    checkpoint = None
    # Resume from previously stored checkpoint
    if args.resume:
        print(f'Resume: {args.resume}')
        output_dir = Path(args.resume)
        checkpoint = torch.load(str(output_dir / 'log' / 'checkpoint.pth'),
                                map_location='cpu')
        for key, arg in vars(checkpoint['args']).items():
            if key not in ['resume']:
                setattr(args, key, arg)

    if args.imputeronly:
        assert args.pretrain is not None

    mask = args.mask
    obs_prob = args.obs_prob
    obs_prob_high = args.obs_prob_high
    block_len = args.block_len
    if block_len == 0:
        block_len = None

    if args.maskgen == 'sigmoid':
        hard_sigmoid = False
    elif args.maskgen == 'hardsigmoid':
        hard_sigmoid = True
    elif args.maskgen == 'fusion':
        hard_sigmoid = -.1, 1.1
    else:
        raise NotImplementedError

    if mask == 'indep':
        if obs_prob_high is None:
            mask_str = f'indep_{obs_prob:g}'
        else:
            mask_str = f'indep_{obs_prob:g}_{obs_prob_high:g}'
    elif mask == 'block':
        mask_str = 'block_{}'.format(block_len if block_len else 'varsize')
    else:
        raise NotImplementedError

    path = '{}_{}_{}'.format(
        args.prefix, datetime.now().strftime('%m%d.%H%M%S'),
        '_'.join([
            f'tau_{args.tau:g}',
            f'maskgen_{args.maskgen}',
            f'coef_{args.alpha:g}_{args.beta:g}_{args.gamma:g}',
            mask_str,
        ]))

    if not args.resume:
        output_dir = Path('results') / 'celeba' / path
        print(output_dir)

    if mask == 'indep':
        data = IndepMaskedCelebA(
            data_dir=args.data_dir,
            obs_prob=obs_prob, obs_prob_high=obs_prob_high)
    elif mask == 'block':
        data = BlockMaskedCelebA(
            data_dir=args.data_dir, block_len=block_len)

    n_gpu = torch.cuda.device_count()
    print(f'Use {n_gpu} GPUs.')
    data_gen = parallelize(ConvDataGenerator())
    mask_gen = parallelize(ConvMaskGenerator(hard_sigmoid=hard_sigmoid))
    imputer = UNetImputer().to(device)

    data_critic = parallelize(ConvCritic(n_channels=3))
    mask_critic = parallelize(ConvCritic(n_channels=1))
    impu_critic = parallelize(ConvCritic(n_channels=3))

    misgan_impute(args, data_gen, mask_gen, imputer,
                  data_critic, mask_critic, impu_critic,
                  data, output_dir, checkpoint)


if __name__ == '__main__':
    main()
