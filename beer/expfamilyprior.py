
# pylint: disable=E1102
# pylint: disable=C0103

import abc
import math
import torch
import torch.autograd as ta


def _bregman_divergence(f_val1, f_val2, grad_f_val2, val1, val2):
    return f_val1 - f_val2 - grad_f_val2 @ (val1 - val2)


# The following code compute the log of the determinant of a
# positive definite matrix. This is equivalent to:
#   >>> torch.log(torch.det(mat))
# Note: the hook is necessary to correct the gradient as pytorch
# will return upper triangular gradient.
def _logdet(mat):
    mat.register_hook(lambda grad: .5 * (grad + grad.t()))
    return 2 * torch.log(torch.diag(torch.potrf(mat))).sum()


class ExpFamilyPrior(metaclass=abc.ABCMeta):
    '''Abstract base class for (conjugate) priors from the exponential
    family of distribution. Prior distributions subclassing
    ``ExpFamilyPrior`` are of the form:

    .. math::
       p(x | \\theta ) = \\exp \\big\\{ \\eta(\\theta)^T T(x)
        - A\\big(\\eta(\\theta) \\big) \\big\\}

    where

      * :math:`x` is the parameter for a model for which we want to
        have a prior/posterior distribution.
      * :math:`\\theta` is the set of *standard hyper-parameters*
      * :math:`\\eta(\\theta)` is the vector of *natural
        hyper-parameters*
      * :math:`T(x)` are the sufficient statistics.
      * :math:`A\\big(\\eta(\\theta) \\big)` is the log-normalizing
        function

    '''

    # pylint: disable=W0102
    def __init__(self, natural_hparams):
        '''Initialize the base class.

        Args:
            natural_hparams (``torch.Tensor``): Natural hyper-parameters
                of the distribution.

        Note:
            When subclassing ``beer.ExpFamilyPrior``, the child class
            should call the ``__init__`` method.

            .. code-block:: python

               class MyPrior(beer.ExpFamilyPrior):

                   def __init__(self, std_hparams):
                        # Transfrom the standard hyper-parameters into
                        # the natural hyper-parameters.
                        natural_hparams = transform(std_hparams)
                        super().__init__(natural_hparams)

                   ...

        '''
        # This will be initialized when setting the natural params
        # property.
        self._expected_sufficient_statistics = None
        self._natural_hparams = None

        self.natural_hparams = natural_hparams

    @property
    def expected_sufficient_statistics(self):
        '''``torch.Tensor``: Expected value of the sufficient statistics.

        .. math::
           \\langle T(x) \\rangle_{p(x | \\theta)} = \\nabla_{\\eta} \\;
                A\\big(\\eta(\\theta) \\big)

        '''
        return self._expected_sufficient_statistics.data

    @property
    def natural_hparams(self):
        '``torch.Tensor``: Natural hyper-parameters vector.'
        return self._natural_hparams.data

    @natural_hparams.setter
    def natural_hparams(self, value):
        if value.grad is not None:
            value.grad.zero_()()
        log_norm_value = self.log_norm(value)
        ta.backward(log_norm_value)
        self._expected_sufficient_statistics = value.grad
        self._natural_hparams = value

    @abc.abstractmethod
    def split_sufficient_statistics(self, s_stats):
        '''Abstract method to be implemented by subclasses of
        ``beer.ExpFamilyPrior``.

        Split the sufficient statistics vector into meaningful groups.
        The notion of *meaningful group* depends on the type of the
        subclass. For instance, the sufficient statistics of the
        Normal density are :math:`T(x) = (x^2, x)^T` leading to
        the following groups: :math:`x^2` and :math:`x`.

        Args:
            s_stats (``torch.Tensor``): Sufficients statistics to
                split

        Returns:
            A ``torch.Tensor`` or a tuple of ``torch.Tensor`` depending
            on the type of density.

        '''
        pass

    @abc.abstractmethod
    def log_norm(self, natural_hparams):
        '''Abstract method to be implemented by subclasses of
        ``beer.ExpFamilyPrior``.

        Log-normalizing function of the density.

        Args:
            natural_hparams (``torch.Tensor``): Natural hyper-parameters
                of the distribution.

        Returns:
            ``torch.Tensor`` of size 1: Log-normalization value.

        '''
        pass


class DirichletPrior(ExpFamilyPrior):
    '''The Dirichlet density defined as:

    .. math::
       p(x | \\alpha) = \\frac{\\Gamma(\\sum_{i=1}^K \\alpha_i)}
            {\\prod_{i=1}^K \\Gamma(\\alpha_i)}
            \\prod_{i=1}^K x_i^{\\alpha_i - 1}

    where :math:`\\alpha` is the concentration parameter.

    '''

    def __init__(self, concentrations):
        '''
        Args:
            concentrations (``torch.Tensor``): Concentration for each
                dimension.
        '''
        natural_hparams = torch.tensor(concentrations - 1, requires_grad=True)
        super().__init__(natural_hparams)

    def split_sufficient_statistics(self, s_stats):
        '''For the Dirichichlet density, this is simply the identity
        function as there is only a single "group" of sufficient
        statistics.

        Args:
            s_stats (``torch.Tensor``): Sufficients statistics to
                split

        Returns:
            ``torch.Tensor``: ``s_stats`` unchanged.

        '''
        return s_stats

    def log_norm(self, natural_hparams):
        '''Log-normalizing function

        Args:
            natural_hparams (``torch.Tensor``): Natural hyper-parameters
                of the distribution.

        Returns:
            ``torch.Tensor`` of size 1: Log-normalization value.

        '''
        return - torch.lgamma((natural_hparams + 1).sum()) +\
            torch.lgamma(natural_hparams + 1).sum()


########################################################################
## Densities log-normalizer functions.
########################################################################


def _normalgamma_log_norm(natural_params):
    np1, np2, np3, np4 = natural_params.view(4, -1)
    lognorm = torch.lgamma(.5 * (np4 + 1))
    lognorm += -.5 * torch.log(np3)
    lognorm += -.5 * (np4 + 1) * torch.log(.5 * (np1 - ((np2**2) / np3)))
    return torch.sum(lognorm)


def _jointnormalgamma_split_nparams(natural_params, ncomp):
    # Retrieve the 4 natural parameters organized as
    # follows:
    #   [ np1_1, ..., np1_D, np2_1_1, ..., np2_k_D, np3_1, ..., np3_1_D,
    #     np3_k_D, np4_1, ..., np4_D]
    dim = len(natural_params) // (2 + 2 * ncomp)
    np1 = natural_params[:dim]
    np2s = natural_params[dim: dim + dim * ncomp]
    np3s = natural_params[dim + dim * ncomp: dim + 2 * dim * ncomp]
    np4 = natural_params[dim + 2 * dim * ncomp:]
    return np1, np2s.view(ncomp, dim), np3s.view(ncomp, dim), np4, dim


def _jointnormalgamma_log_norm(natural_params, ncomp):
    np1, np2s, np3s, np4, dim = _jointnormalgamma_split_nparams(natural_params,
                                                                ncomp)
    lognorm = torch.lgamma(.5 * (np4 + 1)).sum()
    lognorm += -.5 * torch.log(np3s).sum()
    tmp = ((np2s ** 2) / np3s).view(ncomp, dim)
    lognorm += torch.sum(-.5 * (np4 + 1) * \
        torch.log(.5 * (np1 - tmp.sum(dim=0))))
    return lognorm


def _normalwishart_split_nparams(natural_params):
    # We need to retrieve the 4 natural parameters organized as
    # follows:
    #   [ np1_1, ..., np1_D^2, np2_1, ..., np2_D, np3, np4]
    #
    # The dimension D is found by solving the polynomial:
    #   D^2 + D - len(self.natural_params[:-2]) = 0
    dim = int(.5 * (-1 + math.sqrt(1 + 4 * len(natural_params[:-2]))))
    np1, np2 = natural_params[:int(dim ** 2)].view(dim, dim), \
         natural_params[int(dim ** 2):-2]
    np3, np4 = natural_params[-2:]
    return np1, np2, np3, np4, dim

def _jointnormalwishart_split_nparams(natural_params, ncomp):
    # We need to retrieve the 4 natural parameters organized as
    # follows:
    #   [ np1_1, ..., np1_D^2, np2_1_1, ..., np2_k_D, np3_1, ...,
    #     np3_k, np4]
    #
    # The dimension D is found by solving the polynomial:
    #   D^2 + ncomp * D - len(self.natural_params[:-(ncomp + 1]) = 0
    dim = int(.5 * (-ncomp + math.sqrt(ncomp**2 + \
        4 * len(natural_params[:-(ncomp + 1)]))))
    np1, np2s = natural_params[:int(dim ** 2)].view(dim, dim), \
         natural_params[int(dim ** 2):-(ncomp + 1)].view(ncomp, dim)
    np3s = natural_params[-(ncomp + 1):-1]
    np4 = natural_params[-1]
    return np1, np2s, np3s, np4, dim


def _normalwishart_log_norm(natural_params):
    np1, np2, np3, np4, dim = _normalwishart_split_nparams(natural_params)
    lognorm = .5 * ((np4 + dim) * dim * math.log(2) - dim * torch.log(np3))
    lognorm += -.5 * (np4 + dim) * _logdet(np1 - torch.ger(np2, np2)/np3)
    seq = torch.arange(1, dim + 1, 1).type(natural_params.type())
    lognorm += torch.lgamma(.5 * (np4 + dim + 1 - seq)).sum()
    return lognorm


def _jointnormalwishart_log_norm(natural_params, ncomp):
    np1, np2s, np3s, np4, dim = _jointnormalwishart_split_nparams(natural_params,
                                                                  ncomp=ncomp)
    lognorm = .5 * ((np4 + dim) * dim * math.log(2) - dim * torch.log(np3s).sum())
    quad_exp = ((np2s[:, None, :] * np2s[:, :, None]) / \
        np3s[:, None, None]).sum(dim=0)
    lognorm += -.5 * (np4 + dim) * _logdet(np1 - quad_exp)
    seq = torch.arange(1, dim + 1, 1).type(natural_params.type())
    lognorm += torch.lgamma(.5 * (np4 + dim + 1 - seq)).sum()
    return lognorm

def kl_div(model1, model2):
    '''Kullback-Leibler divergence between two densities of the same
    type.

    '''
    return _bregman_divergence(model2.log_norm, model1.log_norm,
                               model1.expected_sufficient_statistics,
                               model2.natural_params, model1.natural_params)


def NormalGammaPrior(mean, precision, prior_counts):
    '''Create a NormalGamma density function.

    Args:
        mean (Tensor): Mean of the Normal.
        precision (Tensor): Mean of the Gamma.
        prior_counts (float): Strength of the prior.

    Returns:
        A NormalGamma density.

    '''
    n_mean = mean
    n_precision = prior_counts * torch.ones_like(n_mean)
    g_shapes = precision * prior_counts
    g_rates = prior_counts
    natural_params = torch.tensor(torch.cat([
        n_precision * (n_mean ** 2) + 2 * g_rates,
        n_precision * n_mean,
        n_precision,
        2 * g_shapes - 1
    ]), requires_grad=True)
    #return ExpFamilyPrior(natural_params, _normalgamma_log_norm)


def JointNormalGammaPrior(means, prec, prior_counts):
    '''Create a joint Normal-Gamma density function.

    Note:
        By "joint" normal-gamma density we mean a set of independent
        Normal density sharing the same diagonal covariance matrix
        (up to a multiplicative constant) and the probability over the
        diagonal of the covariance matrix is given by a D indenpendent
        Gamma distributions.

    Args:
        means (Tensor): Expected mean of the Normal densities.
        prec (Tensor): Expected precision for each dimension.
        prior_counts (float): Strength of the prior.

    Returns:
        ``JointNormalGammaPrior``

    '''
    dim = means.size(1)
    ncomp = len(means)
    natural_params = torch.tensor(torch.cat([
        (prior_counts * (means**2).sum(dim=0) + 2 * prior_counts).view(-1),
        (prior_counts * means).view(-1),
        (torch.ones(ncomp, dim) * prior_counts).type(means.type()).view(-1),
        2 * prec * prior_counts - 1
    ]), requires_grad=True)
    #return ExpFamilyPrior(natural_params, _jointnormalgamma_log_norm,
    #                      args={'ncomp': means.size(0)})


def NormalWishartPrior(mean, cov, prior_counts):
    '''Create a NormalWishart density function.

    Args:
        mean (Tensor): Expected mean of the Normal.
        cov (Tensor): Expected covariance matrix.
        prior_counts (float): Strength of the prior.

    Returns:
        A NormalWishart density.

    '''
    if len(cov.size()) != 2:
        raise ValueError('Expect a (D x D) matrix')

    D = mean.size(0)
    dof = prior_counts + D
    V = dof * cov
    natural_params = torch.tensor(torch.cat([
        (prior_counts * torch.ger(mean, mean) + V).view(-1),
        prior_counts * mean,
        (torch.ones(1) * prior_counts).type(mean.type()),
        (torch.ones(1) * (dof - D)).type(mean.type())
    ]), requires_grad=True)
    #return ExpFamilyPrior(natural_params, _normalwishart_log_norm)


def JointNormalWishartPrior(means, cov, prior_counts):
    '''Create a JointNormalWishart density function.

    Note:
        By "joint" normal-wishart density we mean a set of independent
        Normal density sharing the same covariance matrix (up to a
        multiplicative constant) and the probability over the
        covariance matrix is given by a Wishart distribution.

    Args:
        means (Tensor): Expected mean of the Normal densities.
        cov (Tensor): Expected covariance matrix.
        prior_counts (float): Strength of the prior.

    Returns:
        ``JointNormalWishartPrior``

    '''
    if len(cov.size()) != 2:
        raise ValueError('Expect a (D x D) matrix')

    D = means.size(1)
    dof = prior_counts + D
    V = dof * cov
    mmT = (means[:, None, :] * means[:, :, None]).sum(dim=0)
    natural_params = torch.tensor(torch.cat([
        (prior_counts * mmT + V).view(-1),
        prior_counts * means.view(-1),
        (torch.ones(means.size(0)) * prior_counts).type(means.type()),
        (torch.ones(1) * (dof - (D))).type(means.type())
    ]), requires_grad=True)
    #return ExpFamilyPrior(natural_params, _jointnormalwishart_log_norm,
    #                      args={'ncomp': means.size(0)})


########################################################################
# Normal Prior (full cov).
########################################################################

def _normal_fc_split_nparams(natural_params):
    # We need to retrieve the 2 natural parameters organized as
    # follows:
    #   [ np1_1, ..., np1_D^2, np2_1, ..., np2_D]
    #
    # The dimension D is found by solving the polynomial:
    #   D^2 + D - len(self.natural_params) = 0
    dim = int(.5 * (-1 + math.sqrt(1 + 4 * len(natural_params))))
    np1, np2 = natural_params[:int(dim ** 2)].view(dim, dim), \
         natural_params[int(dim ** 2):]
    return np1, np2, dim


def _normal_fc_log_norm(natural_params):
    np1, np2, _ = _normal_fc_split_nparams(natural_params)
    inv_np1 = torch.inverse(np1)
    return -.5 * _logdet(-2 * np1) - .25 * ((np2[None, :] @ inv_np1) @ np2)[0]


def NormalFullCovariancePrior(mean, cov):
    '''Create a Normal density prior.

    Args:
        mean (Tensor): Expected mean.
        cov (Tensor): Expected covariance of the mean.

    Returns:
        ``NormalFullCovariancePrior``: A Normal density.

    '''
    prec = torch.inverse(cov)
    natural_params = torch.tensor(torch.cat([
        -.5 * prec.contiguous().view(-1),
        prec @ mean,
    ]), requires_grad=True)
    #return ExpFamilyPrior(natural_params, _normal_fc_log_norm)


########################################################################
# Normal Prior (isotropic cov).
########################################################################

def _normal_iso_split_nparams(natural_params):
    np1, np2 = natural_params[0], natural_params[1:]
    #return np1, np2


def _normal_iso_log_norm(natural_params):
    np1, np2 = _normal_iso_split_nparams(natural_params)
    inv_np1 = 1 / np1
    logdet = len(np2) * torch.log(-2 * np1)
    #return -.5 * logdet - .25 * inv_np1 * (np2[None, :] @ np2)


def NormalIsotropicCovariancePrior(mean, variance):
    '''Create a Normal density prior with isotropic covariance matrix.

    Args:
        mean (Tensor): Expected mean.
        variance (Tensor): The variance parameter.

    Returns:
        ``NormalIsotropicPrior``: A Normal density.

    '''
    prec = 1 / variance
    natural_params = torch.tensor(torch.cat([
        -.5 * prec,
        prec * mean,
    ]), requires_grad=True)
    #return ExpFamilyPrior(natural_params, _normal_iso_log_norm)


########################################################################
# Matrix Normal Prior.
########################################################################

def _matrixnormal_fc_split_nparams(natural_params, dim1, dim2):
    np1, np2 = natural_params[:int(dim1 ** 2)].view(dim1, dim1), \
         natural_params[int(dim1 ** 2):].view(dim1, dim2)
    #return np1, np2


def _matrixnormal_fc_log_norm(natural_params, dim1, dim2):
    np1, np2 = _matrixnormal_fc_split_nparams(natural_params, dim1, dim2)
    inv_np1 = torch.inverse(np1)
    #mat1, mat2 = np2.t() @ inv_np1, np2
    #trace_mat1_mat2 = mat1.view(-1) @ mat2.t().contiguous().view(-1)
    #return -.5 * dim2 * _logdet(-2 * np1) - .25 * torch.trace(np2.t() @ inv_np1 @ np2)


def MatrixNormalPrior(mean, cov):
    '''Create a Matrix Normal density prior.

    Note:
        The ``MatrixNormalPrior`` is a special case of the Matrix
        Normal density with a single scale matrix (the other is
        assumed to be the identity matrix).

    Args:
        mean (Tensor (q x d)): Expected mean.
        cov (Tensor (q x q)): Expected covariance of the mean.

    Returns:
        ``NormalPrior``: A Normal density.

    '''
    prec = torch.inverse(cov)
    natural_params = torch.tensor(torch.cat([
        -.5 * prec.contiguous().view(-1),
        (prec @ mean).view(-1),
    ]), dtype=mean.dtype, requires_grad=True)
    #return ExpFamilyPrior(natural_params, _matrixnormal_fc_log_norm,
    #                      args={'dim1': mean.size(0), 'dim2': mean.size(1)})


########################################################################
# Gamma Prior.
########################################################################


def _gamma_log_norm(natural_params):
    return torch.lgamma(natural_params[0] + 1) - \
        (natural_params[0] + 1) * torch.log(- natural_params[1])


def GammaPrior(shape, rate):
    '''Create a Gamma density prior.

    Args:
        shape (scalar torch Tensor): Expected mean.
        rate (scalar torch Tensor): Expected covariance of the mean.

    Returns:
        ``NormalPrior``: A Normal density.

    '''
    natural_params = torch.tensor(torch.cat([shape - 1, -rate]),
                                  requires_grad=True)
    #return ExpFamilyPrior(natural_params, _gamma_log_norm)
