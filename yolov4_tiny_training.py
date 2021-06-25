
import os
import fastface as ff
import pytorch_lightning as pl
import torch

# set seed
pl.seed_everything(41)

# build pl.LightningModule with random weights
model = ff.FaceDetector.build_from_yaml("config_zoo/yolov4_tiny.yaml")

hparams = model.hparams["hparams"]

augmentations = hparams.get("augmentations", {})

# build training transforms
train_transforms = ff.transforms.Compose(
	#ff.transforms.RandomRotate(p=0.5, degree_range=augmentations.get("degree_range", 0)),
	ff.transforms.Interpolate(target_size=hparams["image_size"]),
	ff.transforms.Padding(target_size=(hparams["image_size"], hparams["image_size"])),
	ff.transforms.FaceDiscarder(min_face_size=1),
	ff.transforms.RandomHorizontalFlip(p=0.5),
	ff.transforms.ColorJitter(p=0.5,
		brightness=augmentations.get("brightness", 0),
		contrast=augmentations.get("contrast", 0),
		saturation=augmentations.get("saturation", 0)
	)
)

# build val transforms
val_transforms = ff.transforms.Compose(
	ff.transforms.Interpolate(target_size=hparams["image_size"]),
	ff.transforms.Padding(target_size=(hparams["image_size"], hparams["image_size"])),
	ff.transforms.FaceDiscarder(min_face_size=1)
)

# build torch.utils.data.DataLoader for training
train_ds = ff.dataset.WiderFaceDataset(phase="train", transforms=train_transforms)
train_dl = train_ds.get_dataloader(
	batch_size=hparams["mini_batch_size"], shuffle=True, num_workers=8
)

val_dls = []
# build torch.utils.data.DataLoader for validation
val_dls.append(ff.dataset.WiderFaceDataset(
	phase="val", partitions=["hard"], transforms=val_transforms).get_dataloader(
		batch_size=hparams["mini_batch_size"], shuffle=False, num_workers=8))

## DEBUG
"""
from cv2 import cv2
for img, target in val_ds:
	img = img[:, :, [2,1,0]]
	for x1,y1,x2,y2 in target["target_boxes"]:
		print(x1,y1)
		img = cv2.rectangle(img, (x1,y1), (x2,y2), (0,255,0), 2)
	cv2.imshow("", img)
	if cv2.waitKey(0) == 27:
		exit(0)
"""

# add average precision pl.metrics.Metric to the model
model.add_metric("average_precision", ff.metric.WiderFaceAP(iou_threshold=0.5))

model_save_name = "yolov4_tiny_fddb_best"
ckpt_save_path = "./checkpoints"

# resume with checkpoint, if exists
ckpt_resume_path = os.path.join(ckpt_save_path, model_save_name+".ckpt")
if not os.path.isfile(ckpt_resume_path):
	ckpt_resume_path = None

# define checkpoint callback
checkpoint_callback = pl.callbacks.ModelCheckpoint(
	dirpath=ckpt_save_path,
	verbose=True,
	filename=model_save_name,
	monitor="average_precision",
	save_top_k=1,
	mode="max" # only pick max of `average_precision`
)


# define pl.Trainer
trainer = pl.Trainer(
	default_root_dir=".",
	accumulate_grad_batches=hparams["batch_size"] // hparams["mini_batch_size"],
	callbacks=[checkpoint_callback],
	gpus=1 if torch.cuda.is_available() else 0,
	precision=32,
	resume_from_checkpoint=ckpt_resume_path,
	max_epochs=50,
	check_val_every_n_epoch=1,
	gradient_clip_val=hparams["gradient_clip_val"]
)

# start training
trainer.fit(model, train_dataloader=train_dl,
	val_dataloaders=val_dls)