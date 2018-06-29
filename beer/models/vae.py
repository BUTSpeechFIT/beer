
'''Implementation of the Variational Auto-Encoder with arbitrary
prior over the latent space.

'''

import copy
import math
import torch
from .bayesmodel import BayesianModel
from .normal import NormalDiagonalCovariance
from .normal import NormalIsotropicCovariance
from ..utils import sample_from_normals


def _normal_diag_natural_params(mean, var):
    '''Transform the standard parameters of a Normal (diag. cov.) into
    their canonical forms.

    Note:
        The (negative) log normalizer is appended to it.

    '''
    return torch.cat([
        -1. / (2 * var),
        mean / var,
        -(mean ** 2) / (2 * var),
        -.5 * torch.log(var)
    ], dim=-1)


def _log_likelihood(data, means, variances):
    distance_term = 0.5 * (data - means).pow(2) / variances
    precision_term = 0.5 * variances.log()
    llh =  (-distance_term - precision_term).sum(dim=-1)
    llh -= .5 * means.shape[-1] * math.log(2 * math.pi)
    return llh


class VAE(BayesianModel):
    '''Variational Auto-Encoder (VAE).'''

    def __init__(self, encoder, decoder, latent_model):
        '''Initialize the VAE.

        Args:
            encoder (``MLPModel``): Encoder of the VAE.
            decoder (``MLPModel``): Decoder of the VAE.
            latent_model(``BayesianModel``): Bayesian Model
                for the prior over the latent space.
            nsamples (int): Number of samples to approximate the
                expectation of the log-likelihood.

        '''
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder
        self.latent_model = latent_model

    def _estimate_prior(self, means, variances, nsamples, **kwargs):
        exp_np_params, s_stats = self.latent_model.expected_natural_params(
            means.detach(), variances.detach(), nsamples=nsamples,
            **kwargs)
        self.cache['latent_stats'] = s_stats
        return exp_np_params

    def _compute_local_kl_div(self, means, variances, exp_np_params):
        exp_s_stats = \
            NormalDiagonalCovariance.sufficient_statistics_from_mean_var(
                means, variances)
        nparams = _normal_diag_natural_params(means, variances)
        self.cache['kl_divergence'] = \
            ((nparams - exp_np_params) * exp_s_stats).sum(dim=-1)

    def _expected_llh(self, data, means, variances, nsamples):
        len_data = len(data)
        samples = sample_from_normals(means, variances, nsamples)
        samples = samples.view(nsamples * len_data, -1)
        dec_means, dec_variances = self.decoder(samples)
        dec_means = dec_means.view(nsamples, len_data, -1)
        dec_variances = dec_variances.view(nsamples, len_data, -1)
        return _log_likelihood(data, dec_means, dec_variances).mean(dim=0)

    ####################################################################
    # BayesianModel interface.
    ####################################################################

    @staticmethod
    def sufficient_statistics(data):
        return data

    def float(self):
        return self.__class__(
            self.encoder.float(),
            self.decoder.float(),
            self.latent_model.float(),
        )

    def double(self):
        return self.__class__(
            self.encoder.double(),
            self.decoder.double(),
            self.latent_model.double(),
        )

    def to(self, device):
        return self.__class__(
            self.encoder.to(device),
            self.decoder.to(device),
            self.latent_model.to(device),
        )

    def non_bayesian_parameters(self):
        retval = [param.data for param in self.encoder.parameters()]
        retval += [param.data for param in self.decoder.parameters()]
        return retval

    def set_non_bayesian_parameters(self, new_params):
        self.encoder = copy.deepcopy(self.encoder)
        self.decoder = copy.deepcopy(self.decoder)
        n_params_enc = len(list(self.encoder.parameters()))
        params_enc, params_dec = new_params[:n_params_enc], new_params[n_params_enc:]
        for param, new_p_data in zip(self.encoder.parameters(), params_enc):
            param.data = new_p_data
        for param, new_p_data in zip(self.decoder.parameters(), params_dec):
            param.data = new_p_data

    def forward(self, s_stats, nsamples=1, **kwargs):
        # For the case of the VAE, the sufficient statistics is just
        # the data itself. We just rename s_stats to avoid
        # confusion with the sufficient statistics of the latent model.
        data = s_stats
        means, variances = self.encoder(data)
        exp_np_params = self._estimate_prior(means, variances, nsamples, **kwargs)
        self._compute_local_kl_div(means, variances, exp_np_params)
        return self._expected_llh(data, means, variances, nsamples)

    def local_kl_div_posterior_prior(self, parent_msg=None):
        return self.cache['kl_divergence'] + \
            self.latent_model.local_kl_div_posterior_prior()

    def accumulate(self, _, parent_msg=None):
        latent_stats = self.cache['latent_stats']
        self.clear_cache()
        return self.latent_model.accumulate(latent_stats, parent_msg)


class VAEGlobalMeanDiagonalCovariance(VAE):
    '''Variational Auto-Encoder (VAE) with a global mean and (diagonal)
    covariance matrix parameters.

    '''

    def __init__(self, normal, encoder, decoder, latent_model):
        '''Initialize the VAE.

        Args:
            normal (:any:`beer.NormalDiagonalCovariance`): Main
                component of the model.
            encoder (``MLPModel``): Encoder of the VAE.
            decoder (``MLPModel``): Decoder of the VAE.
            latent_model(``BayesianModel``): Bayesian Model
                for the prior over the latent space.

        '''
        super().__init__(encoder, decoder, latent_model)
        self.normal = normal

    @classmethod
    def create(cls, mean, diag_cov, encoder, decoder, latent_model, pseudo_counts=1.):
        '''Create a :any:`NormalDiagonalCovariance`.

        Args:
            mean (``torch.Tensor``): Mean of the Normal to create.
            diag_cov (``torch.Tensor``): Diagonal of the covariance
                matrix of the Normal to create.
            encoder (``MLPModel``): Encoder of the VAE.
            decoder (``MLPModel``): Decoder of the VAE.
            latent_model(``BayesianModel``): Bayesian Model
                for the prior over the latent space.
            nsamples (int): Number of samples to approximate the
                expectation of the log-likelihood.
            pseudo_counts (``torch.Tensor``): Strength of the prior.
                Should be greater than 0.

        Returns:
            :any:`VAEGlobalMeanVar`

        '''
        normal = NormalDiagonalCovariance.create(mean, diag_cov, pseudo_counts)
        return cls(normal, encoder, decoder, latent_model)

    def _expected_llh(self, data, means, variances, nsamples):
        samples = sample_from_normals(means, variances, nsamples)
        samples = samples.view(nsamples * len(data), -1)
        dec_means = self.decoder(samples).view(nsamples, len(data), -1)
        centered_data = data[None] - dec_means
        s_stats = self.normal.sufficient_statistics(centered_data).mean(dim=0)
        self.cache['centered_s_stats'] = s_stats
        return self.normal(s_stats)

    ####################################################################
    # BayesianModel interface.
    ####################################################################

    # Most of the BayesianModel interface is implemented in the parent
    # class VAE.

    def float(self):
        return self.__class__(
            self.normal.float(),
            self.encoder.float(),
            self.decoder.float(),
            self.latent_model.float(),
        )

    def double(self):
        return self.__class__(
            self.normal.double(),
            self.encoder.double(),
            self.decoder.double(),
            self.latent_model.double(),
        )

    def to(self, device):
        return self.__class__(
            self.normal.to(device),
            self.encoder.to(device),
            self.decoder.to(device),
            self.latent_model.to(device),
        )

    def accumulate(self, _, parent_msg=None):
        latent_stats = self.cache['latent_stats']
        centered_s_stats = self.cache['centered_s_stats']
        self.clear_cache()
        return {
            **self.latent_model.accumulate(latent_stats),
            **self.normal.accumulate(centered_s_stats)
        }


class VAEGlobalMeanIsotropicCovariance(VAE):
    '''Variational Auto-Encoder (VAE) with a global mean and
    (isostropic) covariance matrix parameters.

    '''

    def __init__(self, normal, encoder, decoder, latent_model):
        '''Initialize the VAE.

        Args:
            normal (:any:`beer.NormalIsotropicCovariance`): Main
                component of the model.
            encoder (``MLPModel``): Encoder of the VAE.
            decoder (``MLPModel``): Decoder of the VAE.
            latent_model(``BayesianModel``): Bayesian Model
                for the prior over the latent space.

        '''
        super().__init__(encoder, decoder, latent_model)
        self.normal = normal

    @classmethod
    def create(cls, mean, variance, encoder, decoder, latent_model, pseudo_counts=1.):
        '''Create a :any:`VAEGlobalMeanIsotropicCovariance`.

        Args:
            mean (``torch.Tensor[d]``): Mean of the normal.
            variance (``torch.Tensor[1]``): Variance of the normal.
            encoder (``MLPModel``): Encoder of the VAE.
            decoder (``MLPModel``): Decoder of the VAE.
            latent_model(``BayesianModel``): Bayesian Model
                for the prior over the latent space.
            nsamples (int): Number of samples to approximate the
                expectation of the log-likelihood.
            pseudo_counts (``torch.Tensor``): Strength of the prior.
                Should be greater than 0.

        Returns:
            :any:`VAEGlobalMeanIsotropicCovariance`

        '''
        normal = NormalIsotropicCovariance.create(mean, variance, pseudo_counts)
        return cls(normal, encoder, decoder, latent_model)

    def _expected_llh(self, data, means, variances, nsamples):
        samples = sample_from_normals(means, variances, nsamples)
        samples = samples.view(nsamples * len(data), -1)
        dec_means = self.decoder(samples).view(nsamples, len(data), -1)
        centered_data = (data[None] - dec_means).view(nsamples * len(data), -1)
        s_stats = self.normal.sufficient_statistics(centered_data)
        s_stats = s_stats.view(nsamples, len(data), -1).mean(dim=0)
        self.cache['centered_s_stats'] = s_stats
        return self.normal(s_stats)

    ####################################################################
    # BayesianModel interface.
    ####################################################################

    # Most of the BayesianModel interface is implemented in the parent
    # class VAE.

    def float(self):
        return self.__class__(
            self.normal.float(),
            self.encoder.float(),
            self.decoder.float(),
            self.latent_model.float(),
        )

    def double(self):
        return self.__class__(
            self.normal.double(),
            self.encoder.double(),
            self.decoder.double(),
            self.latent_model.double(),
        )

    def to(self, device):
        return self.__class__(
            self.normal.to(device),
            self.encoder.to(device),
            self.decoder.to(device),
            self.latent_model.to(device),
        )

    def accumulate(self, _, parent_msg=None):
        latent_stats = self.cache['latent_stats']
        centered_s_stats = self.cache['centered_s_stats']
        self.clear_cache()
        return {
            **self.latent_model.accumulate(latent_stats),
            **self.normal.accumulate(centered_s_stats)
        }


__all__ = [
    'VAE',
    'VAEGlobalMeanDiagonalCovariance',
    'VAEGlobalMeanIsotropicCovariance',
]
