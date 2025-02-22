import numpy as np
import torch
import gpytorch
import pandas as pd
#from gps import SpectralMixtureGPModel as SMG
#from gps import SpectralMixtureKISSGPModel as SMKG
#from gps import TwoDSpectralMixtureGPModel as TMG
from .gps import * #FIX THIS LATER!
import matplotlib.pyplot as plt
from trainers import train
from gpytorch.constraints import Interval
from gpytorch.priors import LogNormalPrior, NormalPrior, UniformPrior
import pyro
from pyro.infer.mcmc import NUTS, MCMC, HMC
from pyro.optim import Adam
from pyro.infer import SVI, Trace_ELBO
import pyro.distributions as dist

class Transformer(object):
    def transform(self, data, **kwargs):
        raise NotImplementedError

    def inverse(self, data, **kwargs):
        raise NotImplementedError

class MinMax(Transformer):
    def transform(self, data, dim=0, recalc = False, **kwargs):
        if recalc or self.min is None:
            self.min = torch.min(data, dim=dim, keepdim=True)
            self.range = torch.max(data, dim=dim, keepdim=True) - self.min
        return (data-self.min)/self.range

    def inverse(self, data, **kwargs):
        return (data * self.range)+self.min

class ZScore(Transformer):
    def transform(self, data, dim=0, recalc = False, **kwargs):
        if recalc or self.mean is None:
            self.mean = torch.mean(data, dim=dim, keepdim=True)
            self.sd = torch.std(data, dim=dim, keepdim=True)
        return (data - self.mean)/self.sd

    def inverse(self, data, **kwargs):
        return (data*self.sd) + self.mean


class RobustZScore(Transformer):
    def transform(self, data, dim=0, recalc = False, **kwargs):
        if recalc or self.mad is None:
            self.median = torch.median(data, dim=dim, keepdim=True)
            self.mad = torch.median(torch.abs(data - self.median), dim=dim, keepdim=True)
        return (data - self.median)/self.mad

    def inverse(self, data, **kwargs):
        return (data * self.mad) + self.median

def minmax(data, dim=0):
    m = torch.min(data, dim=dim, keepdim=True)
    r = torch.max(data, dim=dim, keepdim=True) - m
    return (data-m)/r, m, r

class Lightcurve(object):
    def __init__(self, xdata, ydata, yerr = None,
                 xtransform='minmax', ytransform = None,
                 **kwargs):
        if xtransform is 'minmax':
            self.xtransform = MinMax()
        elif xtransform is 'zscore':
            self.xtransform = ZScore()
        elif xtransform is 'robust_zscore':
            self.xtransform = RobustZScore()
        elif xtransform is None or isinstance(xtransform, Transformer):
            self.xtransform = xtransform

        if ytransform is 'minmax':
            self.ytransform = MinMax()
        elif ytransform is 'zscore':
            self.ytransform = ZScore()
        elif ytransform is 'robust_zscore':
            self.ytransform = RobustZScore()
        elif ytransform is None or isinstance(ytransform, Transformer):
            self.ytransform = ytransform
        self.xdata = xdata
        self.ydata = ydata
        if yerr is not None:
            self.yerr = yerr
        pass


    @property
    def magnitudes(self):
        pass

    @magnitudes.setter
    def magnitudes(self, value):
        pass

    @property
    def xdata(self):
        return self._xdata_raw

    @xdata.setter
    def xdata(self, values):
        #first, store the raw data internally
        self._xdata_raw = values
        #then, apply the transformation to the values, so it can be used to train the GP
        if self.xtransform is None:
            self._xdata_transformed = values
        elif isinstance(self.xtransform, Transformer):
            self._xdata_transformed = self.xtransform.transform(values)

    @property
    def ydata(self):
        return self._ydata_raw
    
    @ydata.setter
    def ydata(self, values):
        #first, store the raw data internally
        self._ydata_raw = values
        #then, apply the transformation to the values
        if self.ytransform is None:
            self._ydata_transformed = values
        elif isinstance(self.ytransform, Transformer):
            self._ydata_transformed = self.ytransform.transform(values)

    @property
    def yerr(self):
        return self._yerr_raw

    @yerr.setter
    def yerr(self, values):
        self._yerr_raw = values
        #now apply the same transformation that was applied to the ydata
        if self.ytransform is None:
            self._yerr_transformed = values
        elif isinstance(self.ytransform, Transformer):
            self._yerr_transformed = self.ytransform.transform(values)

    def append_data(self, new_values_x, new_values_y):
        pass


    def transform_x(self, values):
        if self.xtransform is None:
            return values
        elif isinstance(self.xtransform, Transformer):
            return self.xtransform.transform(values)


    def transform_y(self, values):
        if self.ytransform is None:
            return values
        elif isinstance(self.xtransform, Transformer):
            return self.xtransform.transform(values)    
        

    
    def fit(self, model = None, likelihood = None, num_mixtures = 4,
            guess = None, grid_size = 2000, cuda = False,
            training_iter=300, max_cg_iterations = None,
            optim="AdamW", miniter=100, stop=1e-5, lr = 0.1,
            stopavg=30,
            **kwargs):
        if self._yerr_transformed is not None and likelihood is None:
            self.likelihood = gpytorch.likelihoods.FixedNoiseGaussianLikelihood(train_mag_err) #, learn_additional_noise = True)
        elif self._yerr_transformed is not None and likelihood is "learn":
            self.likelihood = gpytorch.likelihoods.FixedNoiseGaussianLikelihood(train_mag_err, learn_additional_noise = True)
        elif "Constraint" in [t.__name__ for t in type(likelihood).__mro__]:
            #In this case, the likelihood has been passed a constraint, which means we want a constrained GaussianLikelihood
            self.likelihood = gpytorch.likelihoods.GaussianLikelihood(noise_constraint=likelihood)
        elif likelihood is None:
            #We're just going to make the simplest assumption
            self.likelihood = gpytorch.likelihoods.GaussianLikelihood()
        #Also add a case for if it is a Likelihood object

        if model == "2D": #This if/else is very annoying. Need a better way to do this!
            self.model = TwoDSpectralMixtureGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif model == "1D":
            self.model = SpectralMixtureGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif model == "1DLinear":
            self.model = SpectralMixtureLinearMeanGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif model == "2DLinear":
            self.model = TwoDSpectralMixtureLinearMeanGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif model == "1DSKI":
            self.model = SpectralMixtureKISSGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif model == "2DSKI":
            self.model = TwoDSpectralMixtureKISSGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif model == "1DLinearSKI":
            self.model = SpectralMixtureLinearMeanKISSGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif model == "2DLinearSKI":
            self.model = TwoDSpectrakMixtureLinearMeanKISSGPModel(self._xdata_transformed,
                                                    self._ydata_transformed,
                                                    self.likelihood,
                                                    num_mixtures = num_mixtures)
        elif "GP" in [t.__name__ for t in type(model).__mro__]: #check if it is or inherets from a GPyTorch model
            self.model = model
            

        if cuda:
            self.likelihood.cuda()
            self.model.cuda()
            

        if guess is not None:
            self.model.initialize(**guess)

        # Next we probably want to report some setup info


        #Now we're going 
