import numpy as np
import ctypes as ct
import cv2
import sys
import argparse
from datasets import ViewDataSet3D
from completion2 import CompletionNet2
import torch
from torchvision import datasets, transforms
from torch.autograd import Variable
import time
from numpy import cos, sin
import utils
from scipy.signal import convolve2d
import scipy
from PIL import Image


mousex,mousey=0.5,0.5
changed=True
pitch,yaw,x,y,z = 0,0,0,0,0
roll = 0
org_pitch, org_yaw, org_x, org_y, org_z = 0,0,0,0,0
org_roll = 0
mousedown = False
clickstart = (0,0)
fps = 0

dll=np.ctypeslib.load_library('render_cuda_f','.')


def onmouse(*args):
    global mousex,mousey,changed
    global pitch,yaw,x,y,z
    global org_pitch, org_yaw, org_x, org_y, org_z
    global org_roll, roll
    global clickstart

    if args[0] == cv2.EVENT_LBUTTONDOWN:
        org_pitch, org_yaw, org_x, org_y, org_z =\
        pitch,yaw,x,y,z
        clickstart = (mousex, mousey)

    if args[0] == cv2.EVENT_RBUTTONDOWN:
        org_roll = roll
        clickstart = (mousex, mousey)

    if (args[3] & cv2.EVENT_FLAG_LBUTTON):
        pitch = org_pitch + (mousex - clickstart[0])/10
        yaw = org_yaw + (mousey - clickstart[1])
        changed=True

    if (args[3] & cv2.EVENT_FLAG_RBUTTON):
        roll = org_roll + (mousex - clickstart[0])/50
        changed=True

    my=args[1]
    mx=args[2]
    mousex=mx/float(256)
    mousey=my/float(256 * 2)

def gkern(kernlen=10, nsig=2):
    """Returns a 2D Gaussian kernel array."""
    interval = (2*nsig+1.)/(kernlen)
    x = np.linspace(-nsig-interval/2., nsig+interval/2., kernlen+1)
    kern1d = np.diff(scipy.stats.norm.cdf(x))
    kernel_raw = np.sqrt(np.outer(kern1d, kern1d))
    kernel = kernel_raw/kernel_raw.sum()
    return kernel


def showpoints(imgs, depths, poses, model, target, tdepth):
    global mousex,mousey,changed
    global pitch,yaw,x,y,z,roll
    global fps

    showsz = target.shape[0]
    global show
    show = np.zeros((showsz,showsz * 2,3),dtype='uint8')
    target_depth = np.zeros((showsz,showsz * 2)).astype(np.int32)

    target_depth[:] = (tdepth[:,:,0] * 12800).astype(np.int32)
    #from IPython import embed; embed()
    overlay = False
    show_depth = False
    cv2.namedWindow('show3d')
    cv2.moveWindow('show3d',0,0)
    cv2.setMouseCallback('show3d',onmouse)

    imgv = Variable(torch.zeros(1,3, showsz, showsz*2), volatile=True).cuda()
    maskv = Variable(torch.zeros(1,2, showsz, showsz*2), volatile=True).cuda()

    cpose = np.eye(4)

    def render(imgs, depths, pose, model, poses):
        global fps, show
        t0 = time.time()
        #target_depth[:] = 65535
        
        show_org=np.zeros((4, showsz,showsz * 2,3),dtype='uint8')
        
        show[:] = 0
        before = time.time()
        for i in range(len(imgs)):
            #print(poses[0])

            pose_after = pose.dot(np.linalg.inv(poses[0])).dot(poses[i]).astype(np.float32)
            #from IPython import embed; embed()
            print('after',pose_after)

            dll.render(ct.c_int(imgs[i].shape[0]),
                       ct.c_int(imgs[i].shape[1]),
                       imgs[i].ctypes.data_as(ct.c_void_p),
                       depths[i].ctypes.data_as(ct.c_void_p),
                       pose_after.ctypes.data_as(ct.c_void_p),
                       show_org[i].ctypes.data_as(ct.c_void_p),
                       target_depth.ctypes.data_as(ct.c_void_p)
                      )
            
            
        density = np.zeros((4, 1024, 2048))
        for i in range(4):
            #print(i)
            mask = np.sum(show_org[i], axis=2) > 0
            density[i] = convolve2d(mask, gkern(), mode = 'same')
            density[i] = convolve2d(density[i], gkern(), mode = 'same')
            density[i] = convolve2d(density[i], gkern(), mode = 'same')
    

        m = np.argmax(density, axis = 0)
        final = np.zeros((1024, 2048, 3))
    
        for i in range(4):
            final += show_org[i] * np.expand_dims(m == i, 2)

        final = final.astype(np.uint8)
        show[:] = final[:]
        show = show.astype(np.uint8)
                
        Image.fromarray(final).save('probe.png')

        print('PC render time:', time.time() - before)

        if model:
            tf = transforms.ToTensor()
            before = time.time()
            source = tf(show)
            source_depth = tf(np.expand_dims(target_depth, 2).astype(np.float32)/65536 * 255)

            mean = torch.from_numpy(np.array([0.57441127,  0.54226291,  0.50356019]).astype(np.float32))

            mask = (torch.sum(source,0)==0).float().unsqueeze(0)
            
            
            print(source.size(), mask.size())
            img_mean = (torch.sum(torch.sum(source, 1),1) / torch.sum(torch.sum(mask, 1),1)).view(3,1)
            print(img_mean)
            
            
            
            print(source.size(), mask.size(), mean.size())
            source += mask.repeat(3,1,1) * img_mean.view(3,1,1).repeat(1,1024,2048)
            print(source_depth.size())
            print(mask.size())
            source_depth = torch.cat([source_depth, mask], 0)

            #print(source.size(), source_depth.size())
            imgv.data.copy_(source)
            maskv.data.copy_(source_depth)
            print('Transfer time', time.time() - before)
            before = time.time()
            recon = model(imgv, maskv)
            print('NNtime:', time.time() - before)
            before = time.time()
            show2 = recon.data.cpu().numpy()[0].transpose(1,2,0)
            show[:] = (show2[:] * 255).astype(np.uint8)
            print('Transfer to CPU time:', time.time() - before)

        t1 =time.time()
        t = t1-t0
        fps = 1/t

        cv2.waitKey(5)%256

    while True:

        if changed:
            alpha = yaw
            beta = pitch
            gamma = roll
            cpose = cpose.flatten()

            cpose[0] = cos(alpha) * cos(beta);
            cpose[1] = cos(alpha) * sin(beta) * sin(gamma) - sin(alpha) * cos(gamma);
            cpose[2] = cos(alpha) * sin(beta) * cos(gamma) + sin(alpha) * sin(gamma);
            cpose[3] = 0

            cpose[4] = sin(alpha) * cos(beta);
            cpose[5] = sin(alpha) * sin(beta) * sin(gamma) + cos(alpha) * cos(gamma);
            cpose[6] = sin(alpha) * sin(beta) * cos(gamma) - cos(alpha) * sin(gamma);
            cpose[7] = 0

            cpose[8] = -sin(beta);
            cpose[9] = cos(beta) * sin(gamma);
            cpose[10] = cos(beta) * cos(gamma);
            cpose[11] = 0

            cpose[12:16] = 0
            cpose[15] = 1

            cpose = cpose.reshape((4,4))

            cpose2 = np.eye(4)
            cpose2[0,3] = x
            cpose2[1,3] = y
            cpose2[2,3] = z

            cpose = np.dot(cpose, cpose2)

            print('cpose',cpose)
            render(imgs, depths, cpose.astype(np.float32), model, poses)
            changed = False

        if overlay:
            show_out = (show/2 + target/2).astype(np.uint8)
        elif show_depth:
            show_out = (target_depth * 10).astype(np.uint8)
        else:
            show_out = show

        #cv2.putText(show,'pitch %.3f yaw %.2f roll %.3f x %.2f y %.2f z %.2f'%(pitch, yaw, roll, x, y, z),(15,showsz-15),0,0.5,cv2.CV_RGB(255,255,255))
        cv2.putText(show,'pitch %.3f yaw %.2f roll %.3f x %.2f y %.2f z %.2f'%(pitch, yaw, roll, x, y, z),(15,showsz-15),0,0.5,(255,255,255))
        #cv2.putText(show,'fps %.1f'%(fps),(15,15),0,0.5,cv2.cv.CV_RGB(255,255,255))
        cv2.putText(show,'fps %.1f'%(fps),(15,15),0,0.5,(255,255,255))

        show_rgb = cv2.cvtColor(show_out, cv2.COLOR_BGR2RGB)
        cv2.imshow('show3d',show_rgb)

        cmd=cv2.waitKey(5)%256

        if cmd==ord('q'):
            break
        elif cmd == ord('w'):
            x -= 0.05
            changed = True
        elif cmd == ord('s'):
            x += 0.05
            changed = True
        elif cmd == ord('a'):
            y += 0.05
            changed = True
        elif cmd == ord('d'):
            y -= 0.05
            changed = True
        elif cmd == ord('z'):
            z += 0.01
            changed = True
        elif cmd == ord('x'):
            z -= 0.01
            changed = True

        elif cmd == ord('r'):
            pitch,yaw,x,y,z = 0,0,0,0,0
            roll = 0
            changed = True
        elif cmd == ord('t'):
            pose = poses[0]
            print('pose', pose)
            RT = pose.reshape((4,4))
            R = RT[:3,:3]
            T = RT[:3,-1]

            x,y,z = np.dot(np.linalg.inv(R),T)
            roll, pitch, yaw = (utils.rotationMatrixToEulerAngles(R))

            changed = True


        elif cmd == ord('o'):
            overlay = not overlay
        elif cmd == ord('f'):
            show_depth = not show_depth
        elif cmd == ord('v'):
            cv2.imwrite('save.jpg', show_rgb)


def show_target(target_img):
    cv2.namedWindow('target')
    cv2.moveWindow('target',0,256 + 50)
    show_rgb = cv2.cvtColor(target_img, cv2.COLOR_BGR2RGB)
    cv2.imshow('target', show_rgb)

if __name__=='__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--debug'  , action='store_true', help='debug mode')
    parser.add_argument('--dataroot'  , required = True, help='dataset path')
    parser.add_argument('--idx'  , type = int, default = 0, help='index of data')
    parser.add_argument('--model'  , type = str, default = '', help='path of model')

    opt = parser.parse_args()
    d = ViewDataSet3D(root=opt.dataroot, transform = np.array, mist_transform = np.array, seqlen = 5, off_3d = False)
    idx = opt.idx

    data = d[idx]

    sources = data[0]
    target = data[1]
    source_depths = data[2]
    target_depth = data[3]
    poses = [item.numpy() for item in data[-1]]
    print('target', np.max(target_depth[:]))

    model = None
    if opt.model != '':
        comp = CompletionNet2()
        comp = torch.nn.DataParallel(comp).cuda()
        comp.load_state_dict(torch.load(opt.model))
        model = comp.module
        model.eval()
    print(model)
    print(poses[0])
    # print(source_depth)
    print(sources[0].shape, source_depths[0].shape)
    show_target(target)
    showpoints(sources, source_depths, poses, model, target, target_depth)