# Adapted from PyTorch's DenseNet source code

from detectron2.modeling import BACKBONE_REGISTRY, Backbone, ShapeSpec # FPN
from detectron2.layers import FrozenBatchNorm2d

import re
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.checkpoint as cp
from collections import OrderedDict
from torch.hub import load_state_dict_from_url
from torch import Tensor
from torch.jit.annotations import List

# from detectron2.modeling.backbone.fpn import LastLevelMaxPool
# from detectron2.layers import FrozenBatchNorm2d, get_norm


__all__ = ['DenseNetBackbone', 'densenet121', 'densenet161', 
            'densenet169', 'densenet201', 'build_densenet_backbone']

model_urls = {
    'densenet121': 'https://download.pytorch.org/models/densenet121-a639ec97.pth',
    'densenet169': 'https://download.pytorch.org/models/densenet169-b2777c0a.pth',
    'densenet201': 'https://download.pytorch.org/models/densenet201-c1103571.pth',
    'densenet161': 'https://download.pytorch.org/models/densenet161-8d451a50.pth',
}

class _DenseLayer(nn.Module):
    def __init__(self, num_input_features, growth_rate, bn_size, drop_rate, memory_efficient=False):
        super(_DenseLayer, self).__init__()
        self.add_module('norm1', nn.BatchNorm2d(num_input_features)),
        self.add_module('relu1', nn.ReLU(inplace=True)),
        self.add_module('conv1', nn.Conv2d(num_input_features, bn_size *
                                           growth_rate, kernel_size=1, stride=1,
                                           bias=False)),
        self.add_module('norm2', nn.BatchNorm2d(bn_size * growth_rate)),
        self.add_module('relu2', nn.ReLU(inplace=True)),
        self.add_module('conv2', nn.Conv2d(bn_size * growth_rate, growth_rate,
                                           kernel_size=3, stride=1, padding=1,
                                           bias=False)),
        self.drop_rate = float(drop_rate)
        self.memory_efficient = memory_efficient

    def bn_function(self, inputs):
        # type: (List[Tensor]) -> Tensor
        concated_features = torch.cat(inputs, 1)
        bottleneck_output = self.conv1(self.relu1(self.norm1(concated_features)))  # noqa: T484
        return bottleneck_output

    # todo: rewrite when torchscript supports any
    def any_requires_grad(self, input):
        # type: (List[Tensor]) -> bool
        for tensor in input:
            if tensor.requires_grad:
                return True
        return False

    @torch.jit.unused  # noqa: T484
    def call_checkpoint_bottleneck(self, input):
        # type: (List[Tensor]) -> Tensor
        def closure(*inputs):
            return self.bn_function(inputs)

        return cp.checkpoint(closure, *input)

    @torch.jit._overload_method  # noqa: F811
    def forward(self, input):
        # type: (List[Tensor]) -> (Tensor)
        pass

    @torch.jit._overload_method  # noqa: F811
    def forward(self, input):
        # type: (Tensor) -> (Tensor)
        pass

    # torchscript does not yet support *args, so we overload method
    # allowing it to take either a List[Tensor] or single Tensor
    def forward(self, input):  # noqa: F811
        if isinstance(input, Tensor):
            prev_features = [input]
        else:
            prev_features = input

        if self.memory_efficient and self.any_requires_grad(prev_features):
            if torch.jit.is_scripting():
                raise Exception("Memory Efficient not supported in JIT")

            bottleneck_output = self.call_checkpoint_bottleneck(prev_features)
        else:
            bottleneck_output = self.bn_function(prev_features)

        new_features = self.conv2(self.relu2(self.norm2(bottleneck_output)))
        if self.drop_rate > 0:
            new_features = F.dropout(new_features, p=self.drop_rate,
                                     training=self.training)
        return new_features


class _DenseBlock(nn.ModuleDict):
    _version = 2

    def __init__(self, num_layers, num_input_features, bn_size, growth_rate, drop_rate, memory_efficient=False):
        super(_DenseBlock, self).__init__()
        for i in range(num_layers):
            layer = _DenseLayer(
                num_input_features + i * growth_rate,
                growth_rate=growth_rate,
                bn_size=bn_size,
                drop_rate=drop_rate,
                memory_efficient=memory_efficient,
            )
            self.add_module('denselayer%d' % (i + 1), layer)

    def forward(self, init_features):
        features = [init_features]
        for name, layer in self.items():
            new_features = layer(features)
            features.append(new_features)
        return torch.cat(features, 1)


class _Transition(nn.Sequential):
    def __init__(self, num_input_features, num_output_features):
        super(_Transition, self).__init__()
        self.add_module('norm', nn.BatchNorm2d(num_input_features))
        self.add_module('relu', nn.ReLU(inplace=True))
        self.add_module('conv', nn.Conv2d(num_input_features, num_output_features,
                                          kernel_size=1, stride=1, bias=False))
        self.add_module('pool', nn.AvgPool2d(kernel_size=2, stride=2))


class DenseNetBackbone(Backbone):
    r"""Densenet-BC model class, based on
    `"Densely Connected Convolutional Networks" <https://arxiv.org/pdf/1608.06993.pdf>`_

    Args:
        growth_rate (int) - how many filters to add each layer (`k` in paper)
        block_config (list of 4 ints) - how many layers in each pooling block
        num_init_features (int) - the number of filters to learn in the first convolution layer
        bn_size (int) - multiplicative factor for number of bottle neck layers
          (i.e. bn_size * k features in the bottleneck layer)
        drop_rate (float) - dropout rate after each dense layer
        num_classes (int) - number of classification classes
        memory_efficient (bool) - If True, uses checkpointing. Much more memory efficient,
          but slower. Default: *False*. See `"paper" <https://arxiv.org/pdf/1707.06990.pdf>`_
    """

    def __init__(self, cfg, growth_rate=32, block_config=(6, 12, 24, 16),
                 num_init_features=64, bn_size=4, drop_rate=0, memory_efficient=False):
                # added cfg
                # removed num_classes=1000

        super(DenseNetBackbone, self).__init__()

        # First convolution
        self.features = nn.Sequential(OrderedDict([
            ('conv0', nn.Conv2d(3, num_init_features, kernel_size=7, stride=2,
                                padding=3, bias=False)),
            ('norm0', nn.BatchNorm2d(num_init_features)),
            ('relu0', nn.ReLU(inplace=True)),
            ('pool0', nn.MaxPool2d(kernel_size=3, stride=2, padding=1)),
        ]))

        # Each denseblock
        num_features = num_init_features
        for i, num_layers in enumerate(block_config):
            block = _DenseBlock(
                num_layers=num_layers,
                num_input_features=num_features,
                bn_size=bn_size,
                growth_rate=growth_rate,
                drop_rate=drop_rate,
                memory_efficient=memory_efficient
            )
            self.features.add_module('denseblock%d' % (i + 1), block)
            num_features = num_features + num_layers * growth_rate
            if i != len(block_config) - 1:
                trans = _Transition(num_input_features=num_features,
                                    num_output_features=num_features // 2)
                self.features.add_module('transition%d' % (i + 1), trans)
                num_features = num_features // 2

        # Final batch norm
        self.features.add_module('norm5', nn.BatchNorm2d(num_features))

        # Linear layer
        # self.classifier = nn.Linear(num_features, num_classes)   # don't need

        # Official init from torch repo.
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)
            # elif isinstance(m, nn.Linear):
            #     nn.init.constant_(m.bias, 0)

        self._freeze_backbone(cfg.MODEL.BACKBONE.FREEZE_AT, max_layer=block_config[3])

    def _freeze_backbone(self, freeze_at, max_layer):
        if freeze_at < 0:
            return
        elif freeze_at > max_layer:
            freeze_at = max_layer

        for name, m in self.named_modules():
            # print(f"{name}")
            if 'denseblock4' in name and f'denselayer{freeze_at}' in name:
                print(f"{name}")
                break   # only want to train parameters starting at least in denseblock4
            else:
                print(f"{name}")
                for p in m.parameters():
                    # print(f"{p}")
                    p.requires_grad = False
                    FrozenBatchNorm2d.convert_frozen_batchnorm(self)   # arguments correct?


    # def _freeze_backbone(self, freeze_at):
    #     if freeze_at < 0:
    #         return

    #     for stage_index in range(freeze_at):
    #         if stage_index == 0:
    #             m = self.stem  # stage 0 is the stem
    #         else:
    #             m = getattr(self, "stage" + str(stage_index + 1))
    #         for p in m.parameters():
    #             p.requires_grad = False
    #             FrozenBatchNorm2d.convert_frozen_batchnorm(self)

    # def _freeze_backbone(self, freeze_at=8):
    #     """
    #     Freeze the first several stages of the DenseNet. Commonly used in
    #     fine-tuning.
    #     Layers that produce the same feature map spatial size are defined as one
    #     "stage" by :paper:`FPN`.
    #     Args:
    #         freeze_at (int): number of stages to freeze.
    #             `1` means freezing the stem. `2` means freezing the stem and
    #             one residual stage, etc.
    #     Returns:
    #         nn.Module: this ResNet itself
    #     """
    #     if freeze_at >= 1:
    #         self.stem.freeze()
    #     for idx, stage in enumerate(self.stages, start=2):
    #         if freeze_at >= idx:
    #             for block in stage.children():
    #                 block.freeze()
    #     return 

        
    def forward(self, x):   # x is the input image
        features = self.features(x)
        # Might want these two
        # out = F.relu(features, inplace=True)
        # out = F.adaptive_avg_pool2d(out, (1, 1))
        return {"SoleStage": features}
        # out = torch.flatten(out, 1)
        # out = self.classifier(out)
        # return out

    def output_shape(self):
        # Changed stride to 32 because input image's h and w is divided by
        # 32, corresponding to an over stride of 32 applied to the input image
        return {"SoleStage": ShapeSpec(channels=cfg.MODEL.DENSENET.OUT_CHANNELS, stride=32)}


def _load_state_dict(model, model_url, progress):
    # '.'s are no longer allowed in module names, but previous _DenseLayer
    # has keys 'norm.1', 'relu.1', 'conv.1', 'norm.2', 'relu.2', 'conv.2'.
    # They are also in the checkpoints in model_urls. This pattern is used
    # to find such keys.
    pattern = re.compile(
        r'^(.*denselayer\d+\.(?:norm|relu|conv))\.((?:[12])\.(?:weight|bias|running_mean|running_var))$')

    state_dict = load_state_dict_from_url(model_url, progress=progress)
    # Delete these two since classifier is removed from the network
    del state_dict["classifier.weight"]
    del state_dict["classifier.bias"]
    for key in list(state_dict.keys()):
        res = pattern.match(key)
        if res:
            new_key = res.group(1) + res.group(2)
            state_dict[new_key] = state_dict[key]
            del state_dict[key]
    model.load_state_dict(state_dict)


def _densenet(cfg, arch, growth_rate, block_config, num_init_features, pretrained, progress,
              **kwargs):
    model = DenseNetBackbone(cfg, growth_rate, block_config, num_init_features, **kwargs)
    if pretrained:
        _load_state_dict(model, model_urls[arch], progress)
    return model

def densenet121(cfg, pretrained=True, progress=True, **kwargs):
    r"""Densenet-121 model from
    `"Densely Connected Convolutional Networks" <https://arxiv.org/pdf/1608.06993.pdf>`_

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
        memory_efficient (bool) - If True, uses checkpointing. Much more memory efficient,
          but slower. Default: *False*. See `"paper" <https://arxiv.org/pdf/1707.06990.pdf>`_
    """
    return _densenet(cfg, 'densenet121', 32, (6, 12, 24, 16), 64, pretrained, progress,
                     **kwargs)


def densenet161(cfg, pretrained=True, progress=True, **kwargs):
    r"""Densenet-161 model from
    `"Densely Connected Convolutional Networks" <https://arxiv.org/pdf/1608.06993.pdf>`_

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
        memory_efficient (bool) - If True, uses checkpointing. Much more memory efficient,
          but slower. Default: *False*. See `"paper" <https://arxiv.org/pdf/1707.06990.pdf>`_
    """
    return _densenet(cfg, 'densenet161', 48, (6, 12, 36, 24), 96, pretrained, progress,
                     **kwargs)


def densenet169(cfg, pretrained=True, progress=True, **kwargs):
    r"""Densenet-169 model from
    `"Densely Connected Convolutional Networks" <https://arxiv.org/pdf/1608.06993.pdf>`_

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
        memory_efficient (bool) - If True, uses checkpointing. Much more memory efficient,
          but slower. Default: *False*. See `"paper" <https://arxiv.org/pdf/1707.06990.pdf>`_
    """
    return _densenet(cfg, 'densenet169', 32, (6, 12, 32, 32), 64, pretrained, progress,
                     **kwargs)


def densenet201(cfg, pretrained=True, progress=True, **kwargs):
    r"""Densenet-201 model from
    `"Densely Connected Convolutional Networks" <https://arxiv.org/pdf/1608.06993.pdf>`_

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
        memory_efficient (bool) - If True, uses checkpointing. Much more memory efficient,
          but slower. Default: *False*. See `"paper" <https://arxiv.org/pdf/1707.06990.pdf>`_
    """
    return _densenet(cfg, 'densenet201', 32, (6, 12, 48, 32), 64, pretrained, progress,
                     **kwargs)


@BACKBONE_REGISTRY.register()
def build_densenet_backbone(cfg, input_shape: ShapeSpec):
    """
    Create a DenseNetBackbone instance from config.
    Returns:
        DenseNetBackbone: a :class:`DenseNetBackbone` instance.
    """
    conv_body = cfg.MODEL.DENSENET.CONV_BODY
    pretrained = cfg.MODEL.DENSENET.PRETRAINED
    if conv_body == "densenet121":
        return densenet121(cfg, pretrained=pretrained)
    elif conv_body == "densenet161":
        return densenet161(cfg, pretrained=pretrained)
    elif conv_body == "densenet169":
        return densenet169(cfg, pretrained=pretrained)
    elif conv_body == "densenet201":
        return densenet201(cfg, pretrained=pretrained)
    else:
        return densenet121(cfg, pretrained=pretrained)