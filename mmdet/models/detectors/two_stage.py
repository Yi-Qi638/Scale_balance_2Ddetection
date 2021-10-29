# Copyright (c) OpenMMLab. All rights reserved.
import warnings

import torch
from torch.autograd import Variable

from ..builder import DETECTORS, build_backbone, build_head, build_neck
from .base import BaseDetector

import numpy as np
from json import dumps


@DETECTORS.register_module()
class TwoStageDetector(BaseDetector):
    """Base class for two-stage detectors.

    Two-stage detectors typically consisting of a region proposal network and a
    task-specific regression head.
    """

    def __init__(self,
                 backbone,
                 neck=None,
                 rpn_head=None,
                 roi_head=None,
                 train_cfg=None,
                 test_cfg=None,
                 pretrained=None,
                 init_cfg=None):
        super(TwoStageDetector, self).__init__(init_cfg)
        if pretrained:
            warnings.warn('DeprecationWarning: pretrained is deprecated, '
                          'please use "init_cfg" instead')
            backbone.pretrained = pretrained
        self.backbone = build_backbone(backbone)

        if neck is not None:
            self.neck = build_neck(neck)

        if rpn_head is not None:
            rpn_train_cfg = train_cfg.rpn if train_cfg is not None else None
            rpn_head_ = rpn_head.copy()
            rpn_head_.update(train_cfg=rpn_train_cfg, test_cfg=test_cfg.rpn)
            self.rpn_head = build_head(rpn_head_)

        if roi_head is not None:
            # update train and test cfg here for now
            # TODO: refactor assigner & sampler
            rcnn_train_cfg = train_cfg.rcnn if train_cfg is not None else None
            roi_head.update(train_cfg=rcnn_train_cfg)
            roi_head.update(test_cfg=test_cfg.rcnn)
            roi_head.pretrained = pretrained
            self.roi_head = build_head(roi_head)

        self.train_cfg = train_cfg
        self.test_cfg = test_cfg

        self.class_gt_to_level = torch.zeros([80, 5]).cuda()

    @property
    def with_rpn(self):
        """bool: whether the detector has RPN"""
        return hasattr(self, 'rpn_head') and self.rpn_head is not None

    @property
    def with_roi_head(self):
        """bool: whether the detector has a RoI head"""
        return hasattr(self, 'roi_head') and self.roi_head is not None

    def extract_feat(self, img):
        """Directly extract features from the backbone+neck."""
        x = self.backbone(img)
        if self.with_neck:
            x = self.neck(x)
        return x

    def forward_dummy(self, img):
        """Used for computing network flops.

        See `mmdetection/tools/analysis_tools/get_flops.py`
        """
        outs = ()
        # backbone
        x = self.extract_feat(img)
        # rpn
        if self.with_rpn:
            rpn_outs = self.rpn_head(x)
            outs = outs + (rpn_outs, )
        proposals = torch.randn(1000, 4).to(img.device)
        # roi_head
        roi_outs = self.roi_head.forward_dummy(x, proposals)
        outs = outs + (roi_outs, )
        return outs
    def set_class_gt_to_level(self,gt_area,gt_labels):
        # print("gt_area:{}".format(gt_area))
        # print("gt_labels:{}".format(gt_labels))
       # print("gt_labels:{}".format(gt_labels))
        #print("gt_area.shape:{}".format(gt_area.shape))
        for _gt_area,_gt_labels in zip(gt_area,gt_labels):

            if _gt_area >0 and _gt_area<=32*32:
                self.class_gt_to_level[_gt_labels,0]+=1
            elif _gt_area >32*32 and _gt_area<=64*64:
                self.class_gt_to_level[_gt_labels,1]+=1
            elif _gt_area >64*64 and _gt_area<=128*128:
                self.class_gt_to_level[_gt_labels,2]+=1
            elif _gt_area >128*128 and _gt_area<=256*256:
                self.class_gt_to_level[_gt_labels,3]+=1
            elif _gt_area >256*256 and _gt_area<=np.inf:
                self.class_gt_to_level[_gt_labels,4]+=1

    def forward_train(self,
                      img,
                      img_metas,
                      gt_bboxes,
                      gt_labels,
                      gt_bboxes_ignore=None,
                      gt_masks=None,
                      proposals=None,
                      **kwargs):
        """
        Args:
            img (Tensor): of shape (N, C, H, W) encoding input images.
                Typically these should be mean centered and std scaled.

            img_metas (list[dict]): list of image info dict where each dict
                has: 'img_shape', 'scale_factor', 'flip', and may also contain
                'filename', 'ori_shape', 'pad_shape', and 'img_norm_cfg'.
                For details on the values of these keys see
                `mmdet/datasets/pipelines/formatting.py:Collect`.

            gt_bboxes (list[Tensor]): Ground truth bboxes for each image with
                shape (num_gts, 4) in [tl_x, tl_y, br_x, br_y] format.

            gt_labels (list[Tensor]): class indices corresponding to each box

            gt_bboxes_ignore (None | list[Tensor]): specify which bounding
                boxes can be ignored when computing the loss.

            gt_masks (None | Tensor) : true segmentation masks for each box
                used if the architecture supports a segmentation task.

            proposals : override rpn proposals with custom proposals. Use when
                `with_rpn` is False.

        Returns:
            dict[str, Tensor]: a dictionary of loss components
        """
        x = self.extract_feat(img)

        losses = dict()

        for i in range(len(gt_bboxes)):
            #gt_bboxes[i]=gt_bboxes[i].

            if len(gt_bboxes[i]) > 0:
                gt_bboxes_numpy = gt_bboxes[i].cpu().detach().numpy()

                #print("gt_bboxes[i]:{}".format(gt_bboxes[i])) img_metas[i]['scale_factor'][0]
                gt_w=(gt_bboxes_numpy[:,2]-gt_bboxes_numpy[:,0])/img_metas[i]['scale_factor'][0]
                gt_h = (gt_bboxes_numpy[:, 3]- gt_bboxes_numpy[:, 1])/img_metas[i]['scale_factor'][0]

                gt_area=gt_w*gt_h

                #print("gt_area:{}".format(gt_area))

                self.set_class_gt_to_level(gt_area,gt_labels[i])

        #print("class_gt_to_level:{}".format(self.class_gt_to_level))

        #RPN forward and loss
        if self.with_rpn:
            proposal_cfg = self.train_cfg.get('rpn_proposal',
                                              self.test_cfg.rpn)
            rpn_losses, proposal_list = self.rpn_head.forward_train(
                x,
                img_metas,
                gt_bboxes,
                gt_labels=None,
                gt_bboxes_ignore=gt_bboxes_ignore,
                proposal_cfg=proposal_cfg,
                **kwargs)
            losses.update(rpn_losses)
        else:
            proposal_list = proposals

        roi_losses = self.roi_head.forward_train(x, img_metas, proposal_list,
                                                 gt_bboxes, gt_labels,
                                                 gt_bboxes_ignore, gt_masks,
                                                 **kwargs)
        losses.update(roi_losses)

        # loss_cls = torch.zeros(1)
        #
        # loss_cls = Variable(loss_cls, requires_grad=True).cuda()
        #
        # loss_bbox = torch.zeros(1)
        # loss_bbox = Variable(loss_bbox, requires_grad=True).cuda()
        #
        # zero_loss = dict(loss_cls=loss_cls, loss_bbox=loss_bbox)
        # losses.update(zero_loss)

        return losses

    async def async_simple_test(self,
                                img,
                                img_meta,
                                proposals=None,
                                rescale=False):
        """Async test without augmentation."""

        A = {}
        self.total = (self.class_gt_to_level.sum(dim=1)) + 1
        # self.total=self.total.unsqueeze(1)
        self.total_2 = (1 / self.total).unsqueeze(1)
        self.class2level_scale = self.class_gt_to_level * self.total_2

        A['scale_total'] = torch.cat((self.class2level_scale, self.total.unsqueeze(1)), dim=1).cpu().numpy().tolist()

        json_data = dumps(A, indent=4)

        print("A:{}".format(A))

        with open('copypaste_large_balance_faster_rcnn_10_25.json', 'w') as json_file:
            json_file.write(json_data)

        assert self.with_bbox, 'Bbox head must be implemented.'
        x = self.extract_feat(img)

        if proposals is None:
            proposal_list = await self.rpn_head.async_simple_test_rpn(
                x, img_meta)
        else:
            proposal_list = proposals

        return await self.roi_head.async_simple_test(
            x, proposal_list, img_meta, rescale=rescale)

    def simple_test(self, img, img_metas, proposals=None, rescale=False):
        """Test without augmentation."""

        A = {}
        self.total = (self.class_gt_to_level.sum(dim=1)) + 1
        # self.total=self.total.unsqueeze(1)
        self.total_2 = (1 / self.total).unsqueeze(1)
        self.class2level_scale = self.class_gt_to_level * self.total_2

        A['scale_total'] = torch.cat((self.class2level_scale, self.total.unsqueeze(1)), dim=1).cpu().numpy().tolist()

        json_data = dumps(A, indent=4)

        #print("A:{}".format(A))

        # with open('copypaste_large_balance_faster_rcnn_10_27.json', 'w') as json_file:
        #     json_file.write(json_data)
        with open('faster_rcnn_28.json', 'w') as json_file:
            json_file.write(json_data)

        assert self.with_bbox, 'Bbox head must be implemented.'
        x = self.extract_feat(img)
        if proposals is None:
            proposal_list = self.rpn_head.simple_test_rpn(x, img_metas)
        else:
            proposal_list = proposals

        return self.roi_head.simple_test(
            x, proposal_list, img_metas, rescale=rescale)

    def aug_test(self, imgs, img_metas, rescale=False):
        """Test with augmentations.

        If rescale is False, then returned bboxes and masks will fit the scale
        of imgs[0].
        """

        A = {}
        self.total = (self.class_gt_to_level.sum(dim=1)) + 1
        # self.total=self.total.unsqueeze(1)
        self.total_2 = (1 / self.total).unsqueeze(1)
        self.class2level_scale = self.class_gt_to_level * self.total_2

        A['scale_total'] = torch.cat((self.class2level_scale, self.total.unsqueeze(1)), dim=1).cpu().numpy().tolist()

        json_data = dumps(A, indent=4)

        #print("A:{}".format(A))

        with open('copypaste_large_balance_faster_rcnn_10_25.json', 'w') as json_file:
            json_file.write(json_data)

        x = self.extract_feats(imgs)
        proposal_list = self.rpn_head.aug_test_rpn(x, img_metas)
        return self.roi_head.aug_test(
            x, proposal_list, img_metas, rescale=rescale)

    def onnx_export(self, img, img_metas):

        img_shape = torch._shape_as_tensor(img)[2:]
        img_metas[0]['img_shape_for_onnx'] = img_shape
        x = self.extract_feat(img)
        proposals = self.rpn_head.onnx_export(x, img_metas)
        if hasattr(self.roi_head, 'onnx_export'):
            return self.roi_head.onnx_export(x, proposals, img_metas)
        else:
            raise NotImplementedError(
                f'{self.__class__.__name__} can not '
                f'be exported to ONNX. Please refer to the '
                f'list of supported models,'
                f'https://mmdetection.readthedocs.io/en/latest/tutorials/pytorch2onnx.html#list-of-supported-models-exportable-to-onnx'  # noqa E501
            )
