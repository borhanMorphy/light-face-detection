from torch.utils.data import Dataset
import os
from typing import List,Tuple
import numpy as np
from cv2 import cv2

def parse_annotation_file(lines:List) -> Tuple[List,List]:
    idx = 0
    length = len(lines)
    def parse_box(box):
        x,y,w,h = [int(b) for b in box.split(" ")[:4]]
        return x,y,x+w,y+h

    ids = []
    targets = []
    while idx < length-1:
        img_file_name = lines[idx]
        bbox_count = int(lines[idx+1])
        if bbox_count == 0:
            idx += 3
            ids.append(img_file_name)
            targets.append([])
            continue
        boxes = lines[idx+2:idx+2+bbox_count]

        boxes = list(map(parse_box, boxes))

        ids.append(img_file_name)
        targets.append(boxes)
        idx = idx + len(boxes) + 2

    return ids,targets


class WiderFace(Dataset):
    __phases__ = ("train","val")
    def __init__(self, phase:str='train', transform=None, target_transform=None, transforms=None):
        assert phase in WiderFace.__phases__,f"given phase {phase} is not valid, must be one of: {WiderFace.__phases__}"
        super(WiderFace,self).__init__()
        source_image_dir = f"./data/widerface/WIDER_{phase}/images"
        annotation_path = f"./data/widerface/wider_face_split/wider_face_{phase}_bbx_gt.txt"
        with open(annotation_path,"r") as foo:
            annotations = foo.read().split("\n")
        ids,targets = parse_annotation_file(annotations)
        del annotations

        self.ids = list(map(lambda img_file_path: os.path.join(source_image_dir,img_file_path), ids))
        self.targets = [np.array(target, dtype=np.float32) for target in targets]
        self.transform = transform
        self.target_transform = target_transform
        self.transforms = transforms

    def __getitem__(self, idx:int):
        img = self._load_image(self.ids[idx])
        target_boxes = self.targets[idx].copy()

        if self.transform:
            img = self.transform(img)
        if self.target_transform:
            target_boxes = self.target_transform(target_boxes)
        if self.transforms:
            img,target_boxes = self.transforms(img,target_boxes)

        return img,target_boxes

    def __len__(self):
        return len(self.ids)

    @staticmethod
    def _load_image(img_file_path):
        return cv2.cvtColor(cv2.imread(img_file_path),cv2.COLOR_BGR2RGB)