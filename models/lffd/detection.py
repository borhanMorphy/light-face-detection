import torch
import torch.nn as nn
import torch.nn.functional as F

from typing import Tuple,List
from .conv import conv_layer
from utils.matcher import LFFDMatcher
from utils.utils import random_sample_selection

class DetectionHead(nn.Module):
    def __init__(self, head_idx:int, features:int, rf_size:int, rf_stride:int,
            lower_scale:int, upper_scale:int, num_classes:int=2, num_target_regs:int=4):

        super(DetectionHead,self).__init__()
        self.head_idx = head_idx
        self.rf_size = rf_size
        self.rf_stride = rf_stride
        self.num_classes = num_classes
        self.num_target_regs = num_target_regs

        self.det_conv = conv_layer(features, features, kernel_size=1, stride=1, padding=0)

        self.cls_head = nn.Sequential(
            nn.Conv2d(features, features,
                kernel_size=1, stride=1,
                padding=0, bias=True),
            nn.ReLU6(inplace=True),
            nn.Conv2d(features, self.num_classes,
                kernel_size=1, stride=1,
                padding=0, bias=True))

        self.reg_head = nn.Sequential(
            nn.Conv2d(features, features,
                kernel_size=1, stride=1,
                padding=0, bias=True),
            nn.ReLU6(inplace=True),
            nn.Conv2d(features, self.num_target_regs,
                kernel_size=1, stride=1,
                padding=0, bias=True))

        def conv_xavier_init(m):
            if type(m) == nn.Conv2d:
                nn.init.xavier_normal_(m.weight)

                if m.bias is not None:
                    m.bias.data.fill_(0)

        self.apply(conv_xavier_init)

        self.matcher = LFFDMatcher(lower_scale,upper_scale)

    def gen_rf_anchors(self, fmaph:int, fmapw:int, device:str='cpu',
            dtype:torch.dtype=torch.float32, clip:bool=False) -> torch.Tensor:
        """takes feature map h and w and reconstructs rf anchors as tensor

        Args:
            fmaph (int): featuremap hight
            fmapw (int): featuremap width
            device (str, optional): selected device to anchors will be generated. Defaults to 'cpu'.
            dtype (torch.dtype, optional): selected dtype to anchors will be generated. Defaults to torch.float32.
            clip (bool, optional): if True clips regions. Defaults to False.

        Returns:
            torch.Tensor: rf anchors as fmaph x fmapw x 4 (xmin, ymin, xmax, ymax)
        """

        # y: fmaph x fmapw
        # x: fmaph x fmapw
        y,x = torch.meshgrid(
            torch.arange(fmaph, dtype=dtype, device=device),
            torch.arange(fmapw, dtype=dtype, device=device)
        )

        # rfs: fmaph x fmapw x 2
        rfs = torch.stack([x,y], dim=-1) + 0.5

        rfs *= self.rf_stride

        # rfs: fh x fw x 2 as x,y
        rfs = rfs.repeat(1,1,2) # fh x fw x 2 => fh x fw x 4
        rfs[:,:,:2] = rfs[:,:,:2] - self.rf_size/2
        rfs[:,:,2:] = rfs[:,:,2:] + self.rf_size/2

        if clip:
            rfs[:,:,[0,2]] = torch.clamp(rfs[:,:,[0,2]],0,fmapw*self.rf_stride)
            rfs[:,:,[1,3]] = torch.clamp(rfs[:,:,[1,3]],0,fmaph*self.rf_stride)

        return rfs

    def build_targets(self, fmap:Tuple[int,int], gt_boxes:List[torch.Tensor], device:str='cpu',
            dtype:torch.dtype=torch.float32) -> Tuple[torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor]:
        """[summary]

        Args:
            fmap (Tuple[int,int]): fmap_h and fmap_w
            gt_boxes (List[torch.Tensor]): [N',4] as xmin,ymin,xmax,ymax
            device (str, optional): target device. Defaults to 'cpu'.
            dtype (torch.dtype, optional): target dtype. Defaults to torch.float32.

        Returns:
            Tuple[torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor,torch.Tensor]: [description]
        """
        # t_cls          : bs,fh',fw'      | type: torch.long           | device: model.device
        # t_regs         : bs,fh',fw',4    | type: model.dtype          | device: model.device
        # t_objness_mask : bs,fh',fw'      | type: torch.bool           | device: model.device
        # t_reg_mask     : bs,fh',fw'      | type: torch.bool           | device: model.device
        # ig             : bs,fh',fw'      | type: torch.bool           | device: model.device
        batch_size = len(gt_boxes)
        fh,fw = fmap

        t_cls = torch.zeros(*(batch_size,fh,fw) ,dtype=torch.long, device=device)
        t_regs = torch.zeros(*(batch_size,fh,fw,self.num_target_regs) ,dtype=dtype, device=device)
        t_objness_mask = torch.zeros(*(batch_size,fh,fw) ,dtype=torch.bool, device=device)
        t_reg_mask = torch.zeros(*(batch_size,fh,fw) ,dtype=torch.bool, device=device)
        ignore = torch.zeros(*(batch_size,fh,fw) ,dtype=torch.bool, device=device)

        # TODO cache rf anchors
        rf_anchors = self.gen_rf_anchors(fh, fw, device=device, dtype=dtype, clip=True)
        rf_centers = rf_anchors[:,:,:2]
        rf_centers[:, :, 0] = (rf_anchors[:,:,2] + rf_anchors[:,:,0]) / 2
        rf_centers[:, :, 1] = (rf_anchors[:,:,3] + rf_anchors[:,:,1]) / 2

        # rf_anchors: fh x fw x 4 as xmin,ymin,xmax,ymax
        for i in range(batch_size):
            if gt_boxes[i].shape[0] == 0: continue
            # fh x fw
            cls_mask,reg_mask = self.matcher(rf_centers, gt_boxes[i])

            t_cls[i, cls_mask>=0] = 1
            t_objness_mask[i, cls_mask>=0] = True
            t_reg_mask[i, reg_mask>=0] = True

            t_regs[i,reg_mask>=0] = gt_boxes[i][reg_mask[reg_mask>=0],:]
            ignore[i, cls_mask==-2] = True
            t_regs[i] = ((t_regs[i].view(-1,4) - rf_centers.view(-1,2).repeat(1,2)) / (self.rf_size*.5)).view(fh,fw,4)

        return t_cls,t_regs,t_objness_mask,t_reg_mask,ignore

    def forward(self, x:torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        data = self.det_conv(x)

        cls_logits = self.cls_head(data)
        # (b,c,h,w)
        reg_logits = self.reg_head(data)
        # (b,c,h,w)
        return cls_logits,reg_logits