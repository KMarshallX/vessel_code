"""
helper functions library

Editor: Marshall Xu
Last Edited: 03/01/2023
"""

import torch
from torch import nn
import torch.nn.functional as F
import numpy as np
import scipy.ndimage as scind
import nibabel as nib
from tqdm import tqdm
from patchify import patchify, unpatchify
import os

class DiceLoss(nn.Module):
    def __init__(self, smooth = 1e-4):
        super().__init__()
        self.smooth = smooth

    def forward(self, pred, target):


        pred = torch.sigmoid(pred)
        # flatten pred and target tensors
        pred = pred.view(-1)
        target = target.view(-1)

        intersection =  (pred * target).sum(-1)
        dice = (2.*intersection + self.smooth) / (pred.sum(-1) + target.sum(-1) + self.smooth)

        return 1 - dice
    
class FocalLoss(nn.Module):
    def __init__(self, alpha = 0.8, gamma = 0, smooth = 1e-6):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.smooth = smooth

    def forward(self, pred, target):
        pred = torch.sigmoid(pred)

        # Flatten
        pred = pred.reshape(-1)
        target = target.reshape(-1)

        # compute the binary cross-entropy
        bce = F.binary_cross_entropy(pred, target, reduction='mean')
        bce_exp = torch.exp(-bce)
        focal_loss = self.alpha * (1-bce_exp) ** self.gamma * bce

        return focal_loss

class BCELoss(nn.Module):
    def __init__(self):
        super().__init__()
    def forward(self, pred, target):
        batch_size = target.size(0)

        # flatten pred and target tensors
        pred = pred.view(batch_size, -1)
        target = target.view(batch_size, -1)
        loss = nn.BCEWithLogitsLoss()
        return loss(pred, target)
    
class TverskyLoss(nn.Module):
    def __init__(self, alpha=0.3, beta=0.7, smooth=1e-3):
        super().__init__()
        self.alpha = alpha
        self.beta = beta
        self.smooth = smooth

    def forward(self, pred, target):
        pred = torch.sigmoid(pred)
        # Flatten
        pred = pred.reshape(-1)
        target = target.reshape(-1)

        # cardinalities
        tp = (pred * target).sum()
        fp = ((1-target) * pred).sum()
        fn = (target * (1-pred)).sum()

        tversky_score = (tp + self.smooth) / (tp + self.alpha*fp + self.beta*fn + self.smooth)

        return 1 - tversky_score


class aug_utils:
    def __init__(self, size, mode):
        super().__init__()
        # size: expected size for the resampled data, tuple, e.g. (64,64,64)
        # mode: "off"-> augmentation is off; 
        #       "test"->test mode, no augmentation, but send one meaningful patch along with 5 other empty blocks;
        #       "on" -> augmentation is on.
        self.size = size
        self.mode = mode
    
    def rot(self, inp, k):
        # k: interger, Number of times the array is rotated by 90 degrees.

        return np.rot90(inp, k, axes=(0,1))

    def flip_hr(self, inp, k):
        # Filp horizontally, axis x -> axis z
        # k: interger, Number of times the array is rotated by 90 degrees.

        return np.rot90(inp, k, axes=(0,2))

    def flip_vt(self, inp, k):
        # Filp vertically, axis y -> axis z
        # k: interger, Number of times the array is rotated by 90 degrees.

        return np.rot90(inp, k, axes=(1,2))

    def zooming(self, inp):
        
        size = self.size
        assert (len(inp.shape)==3), "Only 3D data is accepted"
        w = inp.shape[0] # width
        h = inp.shape[1] # height
        d = inp.shape[2] # depth

        if size[0] == w and size[1] == h and size[2] == d:
            return inp
        else:
            return scind.zoom(inp, (size[0]/w, size[1]/h, size[2]/d), order=0, mode='nearest')

    def filter(self, inp, sigma):
        # apply gaussian filter to the patch
        return scind.gaussian_filter(inp, sigma)
    
    def __call__(self, input, segin):
        input = self.zooming(input)
        segin = self.zooming(segin)

        if self.mode == "on":
            input_batch = np.stack((input, self.rot(input, 1), self.rot(input, 2), self.rot(input, 3), 
            self.flip_hr(input, 1), self.flip_vt(input, 1)), axis=0)
            segin_batch = np.stack((segin, self.rot(segin, 1), self.rot(segin, 2), self.rot(segin, 3), 
            self.flip_hr(segin, 1), self.flip_vt(segin, 1)), axis=0)
        elif self.mode == "off":
            input_batch = np.stack((input, input, input, input, input, input), axis=0)
            segin_batch = np.stack((segin, segin, segin, segin, segin, segin), axis=0)
        elif self.mode == "mode1":
            input_batch = np.stack((input, self.rot(input, 1), self.rot(input, 2), self.rot(input, 3), self.filter(input, 2), self.filter(input, 3)), axis=0)
            segin_batch = np.stack((segin, self.rot(segin, 1), self.rot(segin, 2), self.rot(segin, 3), segin, segin), axis=0)
        elif self.mode == "test":
            
            input_batch = np.expand_dims(input, axis=0)
            segin_batch = np.expand_dims(segin, axis=0)
            
        input_batch = input_batch[:,None,:,:,:]
        segin_batch = segin_batch[:,None,:,:,:]

        return torch.from_numpy(input_batch.copy()).to(torch.float32), torch.from_numpy(segin_batch.copy()).to(torch.float32)


def sigmoid(z):
    return 1/(1+np.exp(-z))

def standardiser(x):
    # only campatible with dtype = numpy array
    return (x - np.mean(x)) / np.std(x)

def thresholding(arr,thresh):
    arr[arr<thresh] = 0
    arr[arr>thresh] = 1
    return arr

def make_prediction(test_patches, load_model, ori_size):

    # Predict each 3D patch  
    for i in tqdm(range(test_patches.shape[0])):
        for j in range(test_patches.shape[1]):
            for k in range(test_patches.shape[2]):

                single_patch = test_patches[i,j,k, :,:,:]
                single_patch_input = single_patch[None, :]
                single_patch_input = torch.from_numpy(single_patch_input).type(torch.FloatTensor).unsqueeze(0)
                single_patch_input = single_patch_input

                single_patch_prediction = load_model(single_patch_input)

                single_patch_prediction_out = single_patch_prediction.detach().numpy()[0,0,:,:,:]

                test_patches[i,j,k, :,:,:] = single_patch_prediction_out

    test_output = unpatchify(test_patches, (ori_size[0], ori_size[1], ori_size[2]))
    test_output_sigmoid = sigmoid(test_output)

    return test_output, test_output_sigmoid

def verification(traw_path, idx, load_model, sav_img_name, mode):
    """
    idx: index of the test image slab in the file list
    mode: str, decide which output to save => ['sigmoid', 'normal']
    Note: the validate image slab has to be preprocessed (bias_field_correction, denoise_image)
    """

    # Load data
    raw_file_list = os.listdir(traw_path)

    raw_img_path = traw_path+raw_file_list[idx]
    print(f"The testing input image is: {raw_img_path}")

    raw_img = nib.load(raw_img_path)

    header = raw_img.header
    affine = raw_img.affine

    raw_arr = raw_img.get_fdata() # (1080*1280*52), (480, 640, 163)

    ori_size = raw_arr.shape    # record the original size of the input image slab

    # resize the input image, to make sure it can be cropped into small patches with size of (64,64,64)
    if (ori_size[0] // 64 != 0) and (ori_size[0] > 64):
        w = int(np.ceil(ori_size[0]/64)) * 64 # new width (x)
    else:
        w = ori_size[0]
    if (ori_size[1] // 64 != 0) and (ori_size[1] > 64):
        h = int(np.ceil(ori_size[1]/64)) * 64 # new height (y)
    else:
        h = ori_size[1]
    if (ori_size[2] // 64 != 0) and (ori_size[2] > 64):
        t = int(np.ceil(ori_size[2]/64)) * 64 # new thickness (z)
    elif ori_size[2] < 64:
        t = 64
    else:
        t = ori_size[2]
    new_raw = scind.zoom(raw_arr, (w/ori_size[0], h/ori_size[1], t/ori_size[2]), order=0, mode='nearest')
    
    # Standardization
    new_raw = standardiser(new_raw)
    new_size = new_raw.shape       # new size of the reshaped input image

    # pachify
    test_patches = patchify(new_raw, (64,64,64), 64)

    test_output, test_output_sigmoid = make_prediction(test_patches, load_model, new_size)

    # save as nifti image
    if mode == 'sigmoid':
        # reshape to original shape
        test_output_sigmoid = scind.zoom(test_output_sigmoid, (ori_size[0]/new_size[0], ori_size[1]/new_size[1], ori_size[2]/new_size[2]), order=0, mode="nearest")
        # mip = np.max(test_output_sigmoid, axis=2)
        nifimg = nib.Nifti1Image(test_output_sigmoid, affine, header)

    elif mode == 'normal':
        # reshape to original thickness
        test_output = scind.zoom(test_output, (ori_size[0]/new_size[0], ori_size[1]/new_size[1], ori_size[2]/new_size[2]), order=0, mode="nearest")
        # mip = np.max(test_output, axis=2)
        nifimg = nib.Nifti1Image(test_output, affine, header)

    # save the MIP image
    sav_img_path = "./saved_image/"+sav_img_name+".nii.gz"
    # sav_mip_img_path = "./saved_image/"+sav_img_name+".png"
    # plt.imsave(sav_mip_img_path, mip, cmap='gray')
    # print("Output MIP image is successfully saved!\n")
    # save the nifti image
    nib.save(nifimg, sav_img_path)
    print("Output Neuroimage is successfully saved!\n")

class RandomCrop3D():
    """
    Resample the input image slab by randomly cropping a 3D volume, and reshape to a fixed size e.g.(64,64,64)
    """
    def __init__(self, img_sz, exp_sz):
        h, w, d = img_sz
        crop_h = torch.randint(10, h, (1,)).item()
        crop_w = torch.randint(10, w, (1,)).item()
        crop_d = torch.randint(10, d, (1,)).item()
        assert (h, w, d) > (crop_h, crop_w, crop_d)
        self.img_sz  = tuple((h, w, d))
        self.crop_sz = tuple((crop_h, crop_w, crop_d))
        self.exp_sz = exp_sz
        
    def __call__(self, img, lab):
        slice_hwd = [self._get_slice(i, k) for i, k in zip(self.img_sz, self.crop_sz)]
        return scind.zoom(self._crop(img, *slice_hwd),(self.exp_sz[0]/self.crop_sz[0], self.exp_sz[1]/self.crop_sz[1], self.exp_sz[2]/self.crop_sz[2]), order=0, mode='nearest'), scind.zoom(self._crop(lab, *slice_hwd),(self.exp_sz[0]/self.crop_sz[0], self.exp_sz[1]/self.crop_sz[1], self.exp_sz[2]/self.crop_sz[2]), order=0, mode='nearest')
        
    @staticmethod
    def _get_slice(sz, crop_sz):
        try : 
            lower_bound = torch.randint(sz-crop_sz, (1,)).item()
            return lower_bound, lower_bound + crop_sz
        except: 
            return (None, None)
    
    @staticmethod
    def _crop(x, slice_h, slice_w, slice_d):
        return x[slice_h[0]:slice_h[1], slice_w[0]:slice_w[1], slice_d[0]:slice_d[1]]
    