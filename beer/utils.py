'''Utility functions.'''

import torch


def onehot(labels, max_label, dtype, device):
    '''Convert a sequence of indices into a one-hot encoded matrix.

    Args:
        labels (seq): Sequence of indices (int) to convert.
        max_label (int): Maximum value for the index. This parameter
            defined the dimension of the returned matrix.
        dtype (``torch.dtype``): Data type of the return tensor.
        device (``torch.devce``): On which device to allocate the
            tensor.

    Returns:
        ``torch.Tensor``: a matrix of N x `max_label` where each column \
            has a single element set to 1.
    '''
    retval = torch.zeros(len(labels), max_label, dtype=dtype, device=device)
    idxs = torch.range(0, len(labels) - 1).long()
    retval[idxs, labels] = 1
    return retval


def logsumexp(tensor, dim=0):
    '''Stable log -> sum -> exponential computation

    Args:
        tensor (``torch.Tensor``): Values.
        dim (int): Dimension along which to do the summation.

    Returns:
        ``torch.Tensor``
    '''
    tmax, _ = torch.max(tensor, dim=dim, keepdim=True)
    retval = tmax + (tensor - tmax).exp().sum(dim=dim, keepdim=True).log()
    new_size = list(tensor.size())
    del new_size[dim]
    return retval.view(*new_size)


def symmetrize_matrix(mat):
    '''Enforce a matrix to be symmetric.

    Args:
        mat (``torch.Tensor[dim, dim]``): Matrix to symmetrize

    Returns:
        ``torch.Tensor[dim, dim]``

    '''
    return .5 * (mat + mat.t())


def make_symposdef(mat, eval_threshold=1e-3):
    '''Enforce a matrix to be symmetric and positive definite.

    Args:
        mat (``torch.Tensor[dim, dim]``): Input matrix.
        eval_threshold (float): Minimum value of the eigen values of
            the matrix.

    Returns:
        ``torch.Tensor[dim, dim]``

    '''
    sym_mat = symmetrize_matrix(mat)
    evals, evecs = torch.symeig(sym_mat, eigenvectors=True)

    threshold = torch.tensor(eval_threshold, dtype=sym_mat.dtype,
                             device=sym_mat.device)
    new_evals = torch.where(evals < threshold, threshold, evals)
    return (evecs @ torch.diag(new_evals) @ evecs.t()).view(*mat.shape)


__all__ = ['onehot', 'logsumexp', 'symmetrize_matrix', 'make_symposdef']
