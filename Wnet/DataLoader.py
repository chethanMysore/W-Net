from PIL import Image
import torch
import torch.utils.data as Data
import os
import glob
import numpy as np
import pdb
from configure import Config
import math
import cupy as cp

config = Config()

class DataLoader():
    #initialization
    #datapath : the data folder of bsds500
    #mode : train/test/val
    def __init__(self, datapath,mode):
        #image container
        self.raw_data = []
        self.mode = mode
        #navigate to the image directory
        #images_path = os.path.join(datapath,'images')
        train_image_path = os.path.join(datapath,config.imagefolder,mode)
        
        #train_image_regex = os.path.join(train_image_path, '*.jpg')
        #find all the images
        train_list_file = os.path.join(datapath,config.imagelist)
        file_list = []
        with open(train_list_file) as f:
            for line in f.readlines():
                file_list.append(os.path.join(train_image_path,line[0:-1]+".jpg"))
        #file_list = glob.glob(train_image_regex)
        #load the images
        for file_name in file_list:
            with Image.open(file_name) as image:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                self.raw_data.append(np.array(image.resize((config.inputsize[0],config.inputsize[1]),Image.BILINEAR)))
        #resize and align
        self.scale()
        #normalize
        self.normalize()
        #calculate weights by 2
        if(mode == "train"):
            self.dataset = self.get_dataset(self.raw_data, self.raw_data.shape,45)
        
    
    def scale(self):
        for i in range(len(self.raw_data)):
            image = self.raw_data[i]
            self.raw_data[i] = np.stack((image[:,:,0],image[:,:,1],image[:,:,2]),axis = 0)
        self.raw_data = np.stack(self.raw_data,axis = 0)

    def normalize(self):
        #just for RGB 8-bit color
        self.raw_data = self.raw_data.astype(np.float)/256

    def torch_loader(self):
        return Data.DataLoader(
                                self.dataset,
                                batch_size = config.BatchSize,
                                shuffle = config.Shuffle,
                                num_workers = config.LoadThread,
                                pin_memory = False,
                            )
#Memory out, depressed
    def cal_dissim(self,raw_data,shape):
        dissim = np.zeros((shape[0],shape[2],shape[3],shape[2],shape[3]))
        for idx in range(shape[0]):
            for i in range(shape[2]):
                for j in range(shape[3]):
                    dissim[idx,i,j,i,j] = 0.0
                    for m in range(i):
                        for n in range(j):
                            dissim[idx,i,j,m,n] = dissimilarity(raw_data[idx,:,i,j],raw_data[idx,:,m,n])
                            dissim[idx,m,n,i,j] = dissim[idx,i,j,m,n]

    def cal_weight(self,raw_data,shape):
        #According to the weight formula, when Euclidean distance < r,the weight is 0, so reduce the dissim matrix size to radius-1 to save time and space.
        print("calculating weights.")
        dissim = cp.zeros((shape[0],shape[1],shape[2],shape[3],(config.radius-1)*2+1,(config.radius-1)*2+1))
        data = cp.asarray(raw_data)
        padded_data = cp.pad(data,((0,0),(0,0),(config.radius-1,config.radius-1),(config.radius-1,config.radius-1)),'constant')
        for m in range(2*(config.radius-1)+1):
            for n in range(2*(config.radius-1)+1):
                dissim[:,:,:,:,m,n] = data-padded_data[:,:,m:shape[2]+m,n:shape[3]+n]
        #for i in range(dissim.shape[0]):
        dissim = cp.exp(-cp.power(dissim,2).sum(1,keepdims = True)/config.sigmaI**2)
        dist = cp.zeros((2*(config.radius-1)+1,2*(config.radius-1)+1))
        for m in range(1-config.radius,config.radius):
            for n in range(1-config.radius,config.radius):
                if m**2+n**2<config.radius**2:
                    dist[m+config.radius-1,n+config.radius-1] = cp.exp(-(m**2+n**2)/config.sigmaX**2)
        for m in range(0,config.radius):
            dissim[:,:,m,:,0:config.radius-m,:]=0.0
            dissim[:,:,-1-m,:,-1-m:-1,:]=0.0
            dissim[:,:,:,m,:,0:config.radius-m]=0.0
            dissim[:,:,:,-1-m,:,-1-m:-1]=0.0
        print("weight calculated.")
        res = cp.multiply(dissim,dist)
        del dissim,data,padded_data,dist
        return res

    def get_dataset(self,raw_data,shape,batch_size):
        dataset = []
        cp.cuda.Device(config.cuda_dev).use()
        for batch_id in range(0,shape[0],batch_size):
            print(batch_id)
            batch = raw_data[batch_id:min(shape[0],batch_id+batch_size)]
            if(self.mode == "train"):
                tmp_weight = self.cal_weight(batch,batch.shape)
                sum_weight = cp.asnumpy(tmp_weight.sum(-1).sum(-1))
                weight = cp.asnumpy(tmp_weight)
                dataset.append(Data.TensorDataset(torch.from_numpy(batch).float(),torch.from_numpy(weight).float(),torch.from_numpy(sum_weight).float()))
                del tmp_weight
            else:
                dataset.append(Data.TensorDataset(torch.from_numpy(batch).float()))
        cp.get_default_memory_pool().free_all_blocks()
        return Data.ConcatDataset(dataset)



