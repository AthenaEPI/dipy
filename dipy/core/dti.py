#!/usr/bin/python
# Created by Christopher Nguyen
# 5/17/2010

#import modules
import time
import sys, os, traceback, optparse
import numpy as np
import scipy as sp

#dipy modules
from dipy.core.maskedview import MaskedView
from dipy.core.memory import memory

class tensor(object):
    """
    Tensor object that when initialized calculates single self diffusion tensor[1]_ in 
    each voxel using selected fitting algorithm (DEFAULT: weighted least squares[1]_)

    Requires a given gradient table, b value for each diffusion-weighted gradient vector,
    and image data given all as numpy ndarrays.

    Parameters
    ----------
    data : ndarray (X,Y,Z,g)
        The image data as a numpy ndarray.
    gtab : ndarray (3,g)
        Diffusion gradient table found in DICOM header as a numpy ndarray.
    bval : ndarray (g,1)
        Diffusion weighting factor b for each vector in gtab.
    mask : ndarray (X,Y,Z)
        Mask that excludes fit in voxels where mask == 0
    thresh : integer (0,np.max(data))
        Simple threshold to exclude fit in voxels where b0 < thresh

    Key Methods
    -----------
    evals : ndarray (X,Y,Z,EV1,EV2,EV3)Z
        Returns cached eigenvalues of self diffusion tensor [1]_ for given index.
    evecs : ndarray (X,Y,Z,EV1x,EV1y,EV1z,EV2x,EV2y,EV2z,EV3x,EV3y,EV3z)
        Returns cached associated eigenvector of self diffusion tensor [1]_ for given 
        index.
    FA : ndarray
        Calculates fractional anisotropy [2]_ for given index.
    ADC : ndarray
        Calculates apparent diffusion coefficient or mean diffusitivity [2]_ for given
        index.

    References
    ----------
    ..    [1] Basser, P.J., Mattiello, J., LeBihan, D., 1994. Estimation of the effective 
        self-diffusion tensor from the NMR spin echo. J Magn Reson B 103, 247-254.
    ..    [2] Basser, P., Pierpaoli, C., 1996. Microstructural and physiological features 
        of tissues elucidated by quantitative-diffusion-tensor MRI. Journal of Magnetic 
        Resonance 111, 209-219.
    
    """
    def _getshape(self):
        pass

    def _getndim(self):
        pass

    def __getitem__(self,index):
        pass
    
    def __init__(self, data, grad_table, b_values, mask=None,thresh=5,verbose=False):
        if mask == None:
            mask = data[:,:,:,0]
            mask[mask<thresh] = 0
            
        dims = data.shape
        if mask != None and dims[0:3] != mask.shape:
            raise ValueError('Data image and mask MUST have same 3D volume shape!')
       
        mask4d = mask[:,:,:,np.newaxis]
        for i in range(dims[3]-1):
            mask4d = np.concatenate((mask4d,mask[:,:,:,np.newaxis]),axis=3)

        #no support from maskedview to handle 3D mask for 4D data
        #   therefore input mask must be 4D
        #no maskedview.reshape
        #   need to reshape the 4d mask
        #also data needs to be masked first
        #   mask needs to be broadcasted to 4D to match and then mask data
        data = MaskedView(mask4d.reshape((np.prod(dims[0:3]),dims[3])),data[mask4d>0]) #mask[:,:,:,np.newaxis]>0]) 

        eig_decomp, design_mat = WLS_fit(data,grad_table,b_values,verbose=verbose)
        
        mask = mask.reshape(dims)
        eig_decomp = MaskedView(mask,eig_decomp)
        self.evals = eig_decomp[:,:,:,0:3]
        self.evecs = eig_decomp[:,:,:,3:12]
        self.prime_evec = eig_decomp[:,:,:,3:6]
        
        #this is for convenience (does not add much memory)
        self.adc = self.calc_adc()
        self.fa = self.calc_fa()
        #self.D = self.calc_D()
    
    def calc_D(self):
        D = np.dot(np.dot(Q,delta),np.linalg.pinv(Q))
        return D
        pass

    def calc_adc(self):
        #adc = (ev1+ev2+ev3)/3
        return (self.evals[:,:,:,0] + self.evals[:,:,:,1] + self.evals[:,:,:,2]) / 3

    def calc_fa(self):
        adc = self.calc_adc()
        ev1 = self.evals[:,:,:,0]
        ev2 = self.evals[:,:,:,1]
        ev3 = self.evals[:,:,:,2]
        ss_ev = ev1**2+ev2**2+ev3**2
        
        fa = np.zeros(ev1.shape,dtype='float32') #'int16')
        fa = np.sqrt( 1.5 * ( (ev1-adc)**2+(ev2-adc)**2+(ev3-adc)**2 ) / ss_ev )
        fa[ss_ev == 0] = 0
        return fa 


def WLS_fit (data,gtab,bval,verbose=False):    
    """
    Computes weighted least squares (WLS) fit to calculate self-diffusion tensor. 
    (Basser et al., 1994a)

    Parameters
    ----------
    data : ndarray (X,Y,Z,g) OR Maskedview (X*Y*Z,g) [preferred]
        The image data as a numpy ndarray.
    gtab : ndarray (3,g)
        Diffusion gradient table found in DICOM header as a numpy ndarray.
    bval : ndarray (g,1)
        Diffusion weighting factor b for each vector in gtab.
    verbose : boolean
        Boolean to indicate verbose output such as timing.

    Returns
    -------
    eig_decomp : ndarray (X,Y,Z,12) OR Maskedview (X*Y*Z,g)
        Eigenvalues and eigenvectors from eigen decomposition of the tensor
    design_mat : ndarray (g,7)
        DTI design matrix to reconstruct fitted data if desired

    """
    start_time = time.time()
    ####main part of code
    dims = data.shape
        
    if len(dims) == 4:
        fit_dim = (dims[0]*dims[1]*dims[2],dims[3])
        # Y matrix from Chris' paper
        data = data.reshape(fit_dim) #direct reshape for some reason does not work
    elif len(dims) == 2:
        fit_dim = dims

    ###Create log of signal and reshape it to be x:y:z by grad
    if isinstance(data,np.ndarray):
        data[data <= 0] = 1 # enforcing positive values to allow for natural log
        ###Create log of signal and reshape it to be x:y:z by grad
        # need to set this seperately to enforce int16 precision
        # set precision to 3 significant figures...to save memory
        # instead of later calculating it with log_s_ols
        log_s = np.int16(np.log(data) * 1000)
        scale=1/1000.
    else:
        log_s = np.log(data)
        scale=1

    ###Construct design matrix
    #For DTI this is the so called B matrix
    # X matrix from Chris' paper
    B = design_matrix(gtab,bval) # [g by 7]
	
    ###Weighted Least Squares (WLS) to solve "linear" regression
    # Y hat OLS from Chris' paper
    #  ( [x*y*z by g] [g by 7] [7 by g ] ) = [x*y*z by g]
    log_s_ols = np.dot(log_s, np.dot(B, np.linalg.pinv(B)))
    del log_s #freeing up memory
    
    #handling if ndarray is sent
    if isinstance(data,np.ndarray):
        log_s_ols = np.int16(log_s_ols)

    #Setting these arrays later to allow the previous step to have all memory
    #fit_data = np.zeros(fit_dim,dtype='int16') #original data is int16
    eig_decomp = np.zeros((fit_dim[0],12),dtype='float32')#'int16')

    time_diff = list((0,0))
    time_iter = time.time()
    # This step is because we cannot vectorize diagonal vector and tensor fit
    for i in range(log_s_ols.shape[0]): #range(np.size(log_s_ols,axis=0)):
        #Check every 1 slices
        if verbose and i % (dims[0]*dims[1]*1) == 0:
            slice = i/dims[0]/dims[1]+1.
            time_diff.append(time.time()-time_iter)
            min = np.mean(time_diff[2:len(time_diff)])/60.0/5*(dims[2]-slice)
            sec = np.round((min - np.fix(min)) * 60.0/5)
            min = np.fix(min)
            percent = 100.*slice/dims[2]
            print str(np.round(percent)) + '% ... time left: ' + str(min) + ' MIN ' \
                                + str(sec) + ' SEC ... memory: ' + memory()/1024. + 'MB'
            time_iter=time.time()
 
        #Split up weighting vector into little w to perform pinv
        w = np.exp(log_s_ols[i,:]*scale)[:,np.newaxis]
    
        #pointwise broadcasting to avoid diagonal matrix multiply!
        D = np.dot(np.linalg.pinv(B*w), w.ravel()*np.log(data[:,i])*scale) #log_s[i,:]
        
        ###Obtain eigenvalues and eigenvectors
        eig_decomp[i,:] = decompose_tensor(D[0:6],scale=1)

    #clear variables not needed to save memory
    del log_s_ols

    # Reshape the output
    if len(dims) == 4:
        eig_decomp = eig_decomp.reshape((dims[0],dims[1],dims[2],12))
   
    #Report how long it took to make the fit  
    if verbose:
        min = (time.time() - start_time) / 60.0
        sec = (min - np.fix(min)) * 60.0
        print 'TOTAL TIME: ' + str(np.fix(min)) + ' MIN ' + str(np.round(sec)) + ' SEC'

    return(eig_decomp, B)


def decompose_tensor(D,scale=1):
    """
    Computes tensor eigen decomposition to calculate eigenvalues and eigenvectors of 
    self-diffusion tensor. Assumes D has units on order of ~ 10^-4 mm^2/s
    (Basser et al., 1994a)

    Parameters
    ----------
    D : ndarray (X,Y,Z,g)
        The six unique diffusitivities (Dxx, Dyy,Dzz,Dxy,Dxz,Dyz)
    scale : integer range(1,N)
        Simple scaling parameter since diffusitivities are small.

    """
    tensor = np.zeros((3,3))
    tensor[0,0] = D[0]  #Dxx
    tensor[1,1] = D[1]  #Dyy
    tensor[2,2] = D[2]  #Dzz
    tensor[1,0] = D[3]  #Dxy
    tensor[2,0] = D[4]  #Dxz
    tensor[2,1] = D[5]  #Dyz
    tensor[0,1] = tensor[1,0] #Dyx
    tensor[0,2] = tensor[2,0] #Dzx
    tensor[1,2] = tensor[2,1] #Dzy

    #outputs multiplicity as well so need to unique
    eigenvals, eigenvecs = np.linalg.eig(tensor)

    if np.size(eigenvals) != 3:
        raise ValueError('not 3 eigenvalues : ' + str(eigenvals))

    #need to sort the eigenvalues and associated eigenvectors
    eigenvecs = eigenvecs[:,eigenvals.argsort()[::-1]]
    eigenvals.sort() #very fast
    eigenvals = eigenvals[::-1]

    #Forcing negative eigenvalues to 0
    eigenvals[eigenvals <0] = 0
    # b ~ 10^3 s/mm^2 and D ~ 10^-4 mm^2/s
    # eigenvecs: each vector is columnar
	
    eig_params = np.concatenate((eigenvals,eigenvecs.T.flat[:]))*scale
    
    return(eig_params)


def design_matrix(gtab,bval,dtype='float32'):
    """
    Constructs design matrix for DTI weighted least squares or least squares fitting. 
    (Basser et al., 1994a)

    Parameters
    ----------
    gtab : ndarray (3,g)
        Diffusion gradient table found in DICOM header as a numpy ndarray.
    bval : ndarray (g,1)
        Diffusion weighting factor b for each vector in gtab.
    dtype : string
        Parameter to control the dtype of returned designed matrix

    """
    
    B = np.zeros((bval.size,7),dtype=dtype)
    G = gtab
    
    if gtab.shape[1] != bval.shape[0] :
        print 'Gradient table size is not consistent with bval vector... could be b/c \
               of b0 images'
        print 'Will try to set nonzero bval index with gradient table to construct \
               B matrix'
        
        G = np.zeros((3,np.size(bval)))
        G[:,np.where(bval > 0)]=gtab
    
    B[:,0] = G[0,:]*G[0,:]*1.*bval   #Bxx
    B[:,1] = G[1,:]*G[1,:]*1.*bval   #Byy
    B[:,2] = G[2,:]*G[2,:]*1.*bval   #Bzz
    B[:,3] = G[0,:]*G[1,:]*2.*bval   #Bxy
    B[:,4] = G[0,:]*G[2,:]*2.*bval   #Bxz
    B[:,5] = G[1,:]*G[2,:]*2.*bval   #Byz
    B[:,6] = np.ones(np.size(bval),dtype=dtype)
    
    #Need to return [g by 7]
    return -B


def save_scalar_maps(scalar_maps, img=None, coordmap=None):
    #for io of writing and reading nifti images
    from nipy import load_image, save_image
    from nipy.core.api import fromarray #data --> image
    
    #For writing out with save_image with appropriate affine matrix
    if img != None:
        coordmap = get_coord_4D_to_3D(img.affine)
        header = img.header.copy()

    ###Save scalar maps if requested
    print ''
    print 'Saving t2di map ... '+out_root+'_t2di.nii.gz'
        
    #fyi the dtype flag for save image does not appear to work right now...
    t2di_img = fromarray(data[:,:,:,0],'ijk','xyz',coordmap=coordmap)
    if img != []: 
        t2di_img.header = header
    save_image(t2di_img,out_root+'_t2di.nii.gz',dtype=np.int16)

        
    scalar_fnames = ('ev1','ev2','ev3','adc','fa','ev1p','ev1f','ev1s')
    for i in range(np.size(scalar_maps,axis=3)):
        #need to send in 4 x 4 affine matrix for 3D image not 5 x 5 from original 4D image
        print 'Saving '+ scalar_fnames[i] + ' map ... '+out_root+'_'+scalar_fnames[i]+'.nii.gz'
        scalar_img = fromarray(np.int16(scalar_maps[:,:,:,i]),'ijk' ,'xyz',coordmap=coordmap)
        if img != []:
            scalar_img.header = header
        save_image(scalar_img,out_root+'_'+scalar_fnames[i]+'.nii.gz',dtype=np.int16)

    print ''
    print 'Saving D = [Dxx,Dyy,Dzz,Dxy,Dxz,Dyz] map ... '+out_root+'_self_diffusion.nii.gz'
    #Saving 4D matrix holding diffusion coefficients
    if img != [] :
        coordmap = img.coordmap
        header = img.header.copy()
    tensor_img = fromarray(tensor_data,'ijkl','xyzt',coordmap=coordmap)
    tensor_img.header = header
    save_image(tensor_img,out_root+'_self_diffusion.nii.gz',dtype=np.int16)

    print

    return
