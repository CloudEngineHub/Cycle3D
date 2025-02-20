import os
import cv2
import random
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms.functional as TF
from torch.utils.data import Dataset
import json
import kiui
from core.options_latents_diffusion import Options
from core.utils import get_rays, grid_distortion, orbit_camera_jitter
import tyro
from core.options import AllConfigs
# import debugpy; debugpy.connect(("localhost", 5677)) 
IMAGENET_DEFAULT_MEAN = (0.485, 0.456, 0.406)
IMAGENET_DEFAULT_STD = (0.229, 0.224, 0.225)


class GobjaverseDataset(Dataset):

    def _warn(self):
        raise NotImplementedError('this dataset is just an example and cannot be used directly, you should modify it to your own setting! (search keyword TODO)')

    def __init__(self, opt: Options, training=True):
        
        self.opt = opt
        self.training = training

        # TODO: remove this barrier
        # self._warn()

        # TODO: load the list of objects for training
        self.items = []
        with open('/remote-home1/yeyang/aigc/gobj_merged.json', 'r') as f:
            self.items = json.load(f)

        with open('/remote-home1/yeyang/aigc/text_captions_cap3d.json', 'r') as cap:
            self.captions = json.load(cap)

        # naive split
        if self.training:
            self.items = self.items[:-self.opt.batch_size]
        else:
            self.items = self.items[-self.opt.batch_size:]
            #self.items = self.items[:self.opt.batch_size]
        # default camera intrinsics
        self.tan_half_fov = np.tan(0.5 * np.deg2rad(self.opt.fovy))
        self.proj_matrix = torch.zeros(4, 4, dtype=torch.float32)
        self.proj_matrix[0, 0] = 1 / self.tan_half_fov
        self.proj_matrix[1, 1] = 1 / self.tan_half_fov
        self.proj_matrix[2, 2] = (self.opt.zfar + self.opt.znear) / (self.opt.zfar - self.opt.znear)
        self.proj_matrix[3, 2] = - (self.opt.zfar * self.opt.znear) / (self.opt.zfar - self.opt.znear)
        self.proj_matrix[2, 3] = 1


    def __len__(self):
        return len(self.items)
        #return 250
    
    def __getitem__(self, idx):

        uid = self.items[idx]
        results = {}
        results["prompt"] = [self.captions[uid]] *self.opt.num_input_views
        # load num_views images
        images = []
        images2 = []
        masks = []
        cam_poses = []
        
        vid_cnt = 0

        # TODO: choose views, based on your rendering settings
        if self.training:
            # input views are in (36, 72), other views are randomly selected
            #input = np.random.permutation(np.arange(27, 39))[:self.opt.num_input_views].tolist()
            input_1 = np.random.permutation(np.arange(27, 30))[:1].tolist()
            input_2 = np.random.permutation(np.arange(30, 33))[:1].tolist()
            input_3 = np.random.permutation(np.arange(33, 36))[:1].tolist()
            input_4 = np.random.permutation(np.arange(36, 39))[:1].tolist()
            render = np.random.permutation(np.append(np.arange(1, 25), np.arange(27, 39))).tolist()
            #vids = np.random.permutation(np.arange(36, 73))[:self.opt.num_input_views].tolist() + np.random.permutation(100).tolist()’
            vids = input_1 + input_2 + input_3 + input_4 + render
        else:
            # fixed views
            vids = np.arange(27, 39, 4).tolist() + np.arange(1, 39).tolist()
            #vids = [27, 30, 33, 36] + np.random.permutation(np.append(np.arange(1, 25), np.arange(27, 39))).tolist()
            #vids = np.arange(36, 73, 4).tolist() + np.arange(100).tolist()
        
        for vid in vids:
            #if not os.path.exists(os.path.join(self.opt.data_path, uid, f'{vid:05d}', f'{vid:05d}.pt')):
            #uid = "1/15039"
            image_path = os.path.join(self.opt.data_path, uid, f'{vid:05d}', f'{vid:05d}.pt')
            #mask_path = os.path.join(self.opt.data_path, uid, f'{vid:05d}', f'{vid:05d}_mask.pt')
            camera_path = os.path.join(self.opt.json_path, uid, f'{vid:05d}', f'{vid:05d}.json')
            image2_path = os.path.join(self.opt.json_path, uid, f'{vid:05d}', f'{vid:05d}.png')
          
            try:
                # TODO: load data (modify self.client here)
                image2 = torch.from_numpy(cv2.imread(image2_path, cv2.IMREAD_UNCHANGED).astype(np.float32) / 255)
                image = torch.load(image_path)
                #mask = torch.load(mask_path)
                with open(camera_path, 'r', encoding='utf8') as f:
                    meta = json.load(f)
            except Exception as e:
                print(f'[WARN] dataset {uid} {vid}: {e}')
                continue
            
            # TODO: you may have a different camera system
            # blender world + opencv cam --> opengl world & cam
            c2w = np.eye(4)
            c2w[:3, 0] = np.array(meta['x'])
            c2w[:3, 1] = np.array(meta['y'])
            c2w[:3, 2] = np.array(meta['z'])
            c2w[:3, 3] = np.array(meta['origin'])
            c2w = torch.tensor(c2w, dtype=torch.float32).reshape(4, 4)

            c2w[1] *= -1
            c2w[[1, 2]] = c2w[[2, 1]]
            c2w[:3, 1:3] *= -1 # invert up and forward direction

            # scale up radius to fully use the [-1, 1]^3 space!
            #c2w[:3, 3] *= self.opt.cam_radius / 1.5 # 1.5 is the default scale
          
            image2 = image2.permute(2, 0, 1) # [4, 512, 512]
            mask2 = image2[3:4] # [1, 512, 512]
            image2 = image2[:3] * mask2 + (1 - mask2) # [3, 512, 512], to white bg
            image2 = image2[[2,1,0]].contiguous() # bgr to rgb

            images.append(image.squeeze(0).float()* 0.18215)
            images2.append(image2)
            masks.append(mask2.squeeze(0))
            #masks.append(mask.squeeze(0).squeeze(0).to(image.dtype))
            cam_poses.append(c2w)

            vid_cnt += 1
            if vid_cnt == self.opt.num_views:
                break

        if vid_cnt < self.opt.num_views:
            print(f'[WARN] dataset {uid}: not enough valid views, only {vid_cnt} views found!')
            n = self.opt.num_views - vid_cnt
            images = images + [images[-1]] * n
            images2 = images2 + [images2[-1]] * n
            masks = masks + [masks[-1]] * n
            cam_poses = cam_poses + [cam_poses[-1]] * n
          
        images = torch.stack(images, dim=0) # [V, C, H, W]
        images2 = torch.stack(images2, dim=0) # [V, C, H, W]
        masks = torch.stack(masks, dim=0) # [V, H, W]

        # images = torch.randn(self.opt.num_views, 4, 64, 64).to(images.device)
        # masks = torch.randn(self.opt.num_views, 64, 64).to(masks.device)

        cam_poses = torch.stack(cam_poses, dim=0) # [V, 4, 4]
        
        radius = torch.norm(cam_poses[0, :3, 3])
        cam_poses[:, :3, 3] *= self.opt.cam_radius / radius
        # normalized camera feats as in paper (transform the first pose to a fixed position)
        transform = torch.tensor([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, self.opt.cam_radius], [0, 0, 0, 1]], dtype=torch.float32) @ torch.inverse(cam_poses[0])
        cam_poses = transform.unsqueeze(0) @ cam_poses  # [V, 4, 4]

        images_input = F.interpolate(images[:self.opt.num_input_views].clone(), size=(self.opt.input_size, self.opt.input_size), mode='bilinear', align_corners=False) # [V, C, H, W]
        cam_poses_input = cam_poses[:self.opt.num_input_views].clone()

        # data augmentation
        # if self.training:
        #     # apply random grid distortion to simulate 3D inconsistency
        #     if random.random() < self.opt.prob_grid_distortion:
        #         images_input[1:] = grid_distortion(images_input[1:])
        #     # apply camera jittering (only to input!)
        #     if random.random() < self.opt.prob_cam_jitter:
        #         cam_poses_input[1:] = orbit_camera_jitter(cam_poses_input[1:])

        # images_input = TF.normalize(images_input, IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD)

        # resize render ground-truth images, range still in [0, 1]
        results['images_output'] = F.interpolate(images, size=(self.opt.output_size, self.opt.output_size), mode='bilinear', align_corners=False) # [V, C, output_size, output_size]
        results['masks_output'] = F.interpolate(masks.unsqueeze(1), size=(512, 512), mode='bilinear', align_corners=False) # [V, 1, output_size, output_size]
        results['images2_output'] = F.interpolate(images2, size=(512, 512), mode='bilinear', align_corners=False) # [V, C, output_size, output_size]
        
        # build rays for input views
        rays_embeddings = []
        for i in range(self.opt.num_input_views):
            rays_o, rays_d = get_rays(cam_poses_input[i], self.opt.input_ray_size, self.opt.input_ray_size, self.opt.fovy) # [h, w, 3]
            rays_plucker = torch.cat([torch.cross(rays_o, rays_d, dim=-1), rays_d], dim=-1) # [h, w, 6]
            rays_embeddings.append(rays_plucker)

     
        rays_embeddings = torch.stack(rays_embeddings, dim=0).permute(0, 3, 1, 2).contiguous() # [V, 6, h, w]
        #final_input = torch.cat([images_input, rays_embeddings], dim=1) # [V=4, 9, H, W]
        #results['input'] = final_input
        results['input'] = images_input
        results['ray'] = rays_embeddings
        # opengl to colmap camera for gaussian renderer
        cam_poses[:, :3, 1:3] *= -1 # invert up & forward direction
        
        # cameras needed by gaussian rasterizer
        cam_view = torch.inverse(cam_poses).transpose(1, 2) # [V, 4, 4]
        cam_view_proj = cam_view @ self.proj_matrix # [V, 4, 4]
        cam_pos = - cam_poses[:, :3, 3] # [V, 3]
        
        results['cam_view'] = cam_view
        results['cam_view_proj'] = cam_view_proj
        results['cam_pos'] = cam_pos

        return results
    
if __name__=="__main__":
    opt = tyro.cli(AllConfigs)
    GobjaverseDataset(opt, training=True)