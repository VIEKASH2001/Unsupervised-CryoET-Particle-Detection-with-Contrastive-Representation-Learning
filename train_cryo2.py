import copy
import os
import time
import torch, torchvision
import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm
import torch.optim as optim
import torch.nn.functional as F
import torch.backends.cudnn as cudnn
# from tensorboardX import SummaryWriter

from helper import utils, args
from shutil import copyfile
import pickle
from torch.utils.data import Dataset

from models_simclr.cifar_shallow import resnet20
# from models_simclr.simclr import SimCLR
import torch.nn as nn
from torchvision import transforms, datasets
from torchvision.transforms.functional import adjust_contrast, gaussian_blur

from dataloader.cryoDataset import cryoDataset2 as cryoDataset
from helper.utils import ToFloat
import random


class TwoCropTransform:
    """Create two crops of the same image"""

    def __init__(self, transform):
        self.transform = transform

    def __call__(self, x):
        return [self.transform(x), self.transform(x)]


class RandomContrastTransform:
    """Apply contrast transform with given probability"""

    def __init__(self, p, contrast_factor):
        self.contrast_factor = contrast_factor
        self.p = (1 - p)

    def __call__(self, x):
        if (random.random() >= self.p):
            return adjust_contrast(x, self.contrast_factor)

        return x


class RandomBlurTransform:
    """Apply contrast transform with given probability"""

    def __init__(self, p, kernel_size, sigma):
        self.kernel_size = kernel_size
        self.sigma = sigma
        self.p = (1 - p)

    def __call__(self, x):
        if (random.random() >= self.p):
            return gaussian_blur(x, self.kernel_size, self.sigma)

        return x


def assign_learning_rate(optimizer, new_lr):
    for param_group in optimizer.param_groups:
        param_group["lr"] = new_lr


def main(args):
    args.bs = 1024
    args.lr = args.bs/256 * 0.3

    print('batch size is set to :', args.bs)
    print('learning rate is set to:%.2f' %args.lr)

    exp_name = '%s_%s' % (args.dataset, args.trial)
    args.save_dir = os.path.join(args.save_dir, exp_name)
    # writer = SummaryWriter(f'{args.save_dir}/summary')
    torch.set_num_threads(1)
    # create the log folder
    if not os.path.exists(args.save_dir):
        os.makedirs(args.save_dir)
    elif 'test' not in args.save_dir:
        a = input('log dir exists, override? (yes=y)')
        if a != 'y':
            exit()

    # creat logger
    logger = utils.Logger(args=args,
                          var_names=['Epoch', 'loss1_train', 'loss2_train', 'loss1_test', 'acc_train', 'acc_test',
                                     'acc_best',
                                     'lr'],
                          format=['%02d', '%.4f', '%.4f', '%.4f', '%.4f', '%.2f', '%.2f',
                                  '%.6f'],
                          print_args=True)

    # copy this file to the log dir
    a = os.path.basename(__file__)
    dir_path = os.path.dirname(os.path.realpath(__file__))
    copyfile(os.path.join(dir_path, a), os.path.join(args.save_dir, a))

    # save args
    with open(os.path.join(args.save_dir, 'args.pkl'), 'wb') as handle:
        pickle.dump(args, handle, protocol=pickle.HIGHEST_PROTOCOL)

    device = args.device

    best_acc = 0
    start_epoch = 0  # start from epoch 0 or last checkpoint epoch

    ######################################################################################
    ## Random rotation
    ## ColorJitter - turn off color manipulations, keep contrast
    ## Random crops, scale it back to same size

    train_transform = transforms.Compose([
        transforms.Normalize(mean=-0.0028557733, std=0.048372205),
        RandomBlurTransform(p=0.5, kernel_size=(5, 5), sigma=(0.1, 5)),
        # RandomContrastTransform(p=0.2, contrast_factor=3.0),
        transforms.Pad(padding=[16], padding_mode='reflect'),
        transforms.RandomRotation(degrees=(-90, 90)),
        transforms.CenterCrop(32),
        # transforms.CenterCrop(20),
        transforms.RandomHorizontalFlip(),
        ToFloat()
    ])

    # trainset = cryoDataset('F:\1. PG\3. Oncampus Jobs\1. RAship\Project\DATASET',
    #                        transform=TwoCropTransform(train_transform))

    trainset = cryoDataset('/home/aldb/dataset/viekash',
                           transform=TwoCropTransform(train_transform))

    # args.bs = 1
    trainloader = torch.utils.data.DataLoader(
        trainset, batch_size=args.bs, shuffle=True, num_workers=args.nw)
    ######################################################################################

    net = resnet20()

    print('# of params in the model    : %d' % utils.count_parameters(net))
    print('# of images in training {}'.format(len(trainset)))
    net = net.to(device)

    net = nn.DataParallel(net)

    if device == 'cuda':
        cudnn.benchmark = True
    optimizer = optim.SGD(net.parameters(),
                          lr=args.lr,
                          momentum=args.momentum, weight_decay=args.wd, nesterov=True)

    if args.chkpt != '':
        # Load checkpoint.
        print('==> Resuming from checkpoint..')
        checkpoint = torch.load(args.chkpt)
        net.load_state_dict(checkpoint['net'])
        # start_epoch = checkpoint['epoch']
        # for i in range(start_epoch):
        #     scheduler.step()

    for epoch in range(start_epoch, args.epochs):
        lr = optimizer.param_groups[0]['lr']

        # break
        print("\nlearning rate: %.4f" % (lr))
        t0 = time.time()
        train_loss1, train_loss2, train_acc = train(epoch, net, optimizer, trainloader,
                                                    device, args, None)
        # scheduler.step()

        if (epoch + 1) % 200 == 0 or epoch == args.epochs - 1:
            state = {'net': net.state_dict(),
                     'acc': best_acc,
                     'epoch': epoch + 1}
            torch.save(state, os.path.join(args.save_dir, 'model_' + str(epoch + 1) + '.pt'))

        e = epoch
        es = args.epochs
        lr_ = 0.5 * (1 + np.cos(np.pi * e / es)) * args.lr
        assign_learning_rate(optimizer, lr_)

        # compute acc on nat examples
        """test_acc = validate(trainloader, testloader, net, device)


        if test_acc>best_acc:
            best_acc = test_acc
            state = {'net': net.state_dict(),
                     'acc':best_acc}
            torch.save(state, os.path.join(args.save_dir, 'model_' + str(epoch) + '.pt'))

        print('test acc: %2.2f, best acc: %.2f' % (test_acc, best_acc))
        logger.store(
            [epoch, train_loss1, train_loss2, 0, 0, test_acc, best_acc,
             optimizer.param_groups[0]['lr']],
            log=True)"""

        t = time.time() - t0
        remaining = (args.epochs - epoch) * t
        print("epoch time: %.1f, rt:%s" % (t, utils.format_time(remaining)))


def knn_predict(feature, feature_bank, feature_labels, classes, knn_k, knn_t):
    # compute cos similarity between each feature vector and feature bank ---> [B, N]
    sim_matrix = torch.mm(feature, feature_bank)
    # [B, K]
    sim_weight, sim_indices = sim_matrix.topk(k=knn_k, dim=-1)
    # [B, K]
    sim_labels = torch.gather(feature_labels.expand(feature.size(0), -1), dim=-1, index=sim_indices)
    sim_weight = (sim_weight / knn_t).exp()

    # counts for each class
    one_hot_label = torch.zeros(feature.size(0) * knn_k, classes, device=sim_labels.device)
    # [B*K, C]
    one_hot_label = one_hot_label.scatter(dim=-1, index=sim_labels.view(-1, 1), value=1.0)
    # weighted score ---> [B, C]
    pred_scores = torch.sum(one_hot_label.view(feature.size(0), -1, classes) * sim_weight.unsqueeze(dim=-1), dim=1)
    pred_labels = pred_scores.argsort(dim=-1, descending=True)
    return pred_labels


def validate(train_loader, val_loader, model, device, num_classes=10, k=200, t=0.1):
    model.eval()
    total_top1, total_top5, total_num, feature_bank, feature_labels = 0.0, 0.0, 0, [], []

    trn_batch_size = train_loader.batch_size
    with torch.no_grad():
        # generate feature bank
        for i, data in enumerate(train_loader):
            imgs1, imgs2, target = data[0][0].to(device), data[0][1].cuda(), data[1].long().squeeze()

            feature = model(imgs1)
            feature = F.normalize(feature, dim=1)
            feature_bank.append(feature.cpu())
            feature_labels.append(target)

        # [D, N]
        feature_bank = torch.cat(feature_bank, dim=0).t().contiguous()
        # [N]
        # feature_labels = torch.tensor(train_loader.dataset.targets, device=feature_bank.device)
        feature_labels = torch.cat(feature_labels, dim=0)
        # loop test data to predict the label by weighted knn search
        for batch_idx, data in enumerate(val_loader):
            images, target = data[0].to(device), data[1].long().squeeze()
            # images, target = data[0]['data'], data[0]['data_aug'], data[0]['label'].long().squeeze()

            feature = model(images)
            feature = F.normalize(feature, dim=1).cpu()

            pred_labels = knn_predict(feature, feature_bank, feature_labels, num_classes, k, t)

            total_num += images.size(0)
            total_top1 += (pred_labels[:, 0] == target).float().sum().item()

    return total_top1 / total_num * 100


def nt_xent(x, t=0.5):
    x = F.normalize(x, dim=1)
    x_scores = (x @ x.t()).clamp(min=1e-7)  # normalized cosine similarity scores
    x_scale = x_scores / t  # scale with temperature

    # (2N-1)-way softmax without the score of i-th entry itself.
    # Set the diagonals to be large negative values, which become zeros after softmax.
    x_scale = x_scale - torch.eye(x_scale.size(0)).to(x_scale.device) * 1e5

    # targets 2N elements.
    targets = torch.arange(x.size()[0])
    targets[::2] += 1  # target of 2k element is 2k+1
    targets[1::2] -= 1  # target of 2k+1 element is 2k

    return F.cross_entropy(x_scale, targets.long().to(x_scale.device))


def train(epoch, net, optimizer, trainloader, device, args, writer):
    net.train()
    am_loss1 = utils.AverageMeter()
    am_loss2 = utils.AverageMeter()
    am_acc = utils.AverageMeter()

    prog_bar = tqdm(
        trainloader,
        ascii=True,
        bar_format="{desc}: {percentage:3.0f}% | {n_fmt}/{total_fmt} | {rate_fmt}{postfix}"
    )
    for batch_idx, data in enumerate(prog_bar):
        x1 = data[0]
        x2 = data[1]
        ## Repeating along channels to make it suitable for resnet
        x1 = x1.repeat([1, 3, 1, 1])
        x2 = x2.repeat([1, 3, 1, 1])
        # for i in range(10):
        # utils.plot_tensor([x1[0], x2[0]])

        # print(x1.min(), x1.max())

        # print(x1.shape, x2.shape)
        # exit()

        x1, x2 = x1.to(device), x2.to(device)

        bs = x1.shape[0]

        optimizer.zero_grad()
        x_tot = torch.cat((x1, x2), 0)
        feat = net(x_tot)
        # print("out")

        feat1, feat2 = feat[0:bs], feat[bs:]

        feat_tot = torch.zeros([bs * 2, 32])

        feat_tot[::2] = feat1

        feat_tot[1::2] = feat2

        loss = nt_xent(feat_tot, 0.5)

        loss.backward()

        optimizer.step()

        am_loss1.update(loss.item())

        prog_bar.set_description(
            "E{}/{}, loss1:{:2.3f}, loss2:{:2.6f}, acc:{:2.3f}".format(
                epoch, args.epochs, am_loss1.avg, am_loss2.avg, am_acc.avg))

        # if (epoch + 1) % 2 == 0:
        #     writer.add_scalar('train/train_loss1', am_loss1.avg, epoch + 1)
        # writer.add_scalar('train/train_loss2', am_loss2.avg, epoch + 1)
    prog_bar.close()

    return am_loss1.avg, am_loss2.avg, am_acc.avg


if __name__ == "__main__":
    args = args.get_params(train=True)
    main(args)
