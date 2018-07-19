'''Implementation of a generic Neural Network composed of blocks
(residual or not).

'''

import ast
import torch


class ReshapeLayer(torch.nn.Module):
    '''This layer is just a convenience so that the user can parameterize
    how the data should be presented to the neural network or, to
    alternate between simple feed-forward and convolutional layers.

    '''
    def __init__(self, shape):
        super().__init__()
        self.shape = shape

    def forward(self, data):
        return data.view(*self.shape)


class NeuralNetworkBlock(torch.nn.Module):
    '''Generic Neural Network block with (optional) residual connection.

    '''
    def __init__(self, structure, residual_connection=False):
        super().__init__()
        self.structure = structure
        self.residual_connection = residual_connection

    def forward(self, data):
        h = self.structure(data)
        if self.residual_connection:
            h += data
        return h


class MergeTransform(torch.nn.Module):
    '''Simple "layer" that sum the output of multiple transform.
    Obviously, all the transforms shall have the same output dimension.

    '''
    def __init__(self, *transforms):
        super().__init__()
        self.transforms = torch.nn.Sequential(*transforms)

    def forward(self, *inputs):
        retval = self.transforms[0](inputs[0])
        for input_data, transform in zip(inputs[1:], self.transforms[1:]):
            retval += transform(input_data)
        return retval


def load_value(value):
    '''Evaluate the string representation of a python type if the given
    value is a string.

    Args:
        value (object): value to load w/o interpretation.
    '''
    if not isinstance(value, str):
        return value
    return ast.literal_eval(value)


def parse_nnet_element(strval):
    '''Parse a string definining a neural network element (layer,
    activation function, ...).

    The given string should be formatted as
    "object:param1=val1,param2=val2". For instance:

      Linear:in_features=10,out_features=20

    Args:
        strval (string): String defining the neural network element.

    Returns:
        ``torch.nn.<function>``: name of the object/function
        dict: Keyword arguments and their values (as string)

    '''
    str_kwargs = {}
    if ':' in strval:
        function_name, args_and_vals = strval.strip().split(':')
        for arg_and_val in args_and_vals.split(';'):
            argname, str_argval = arg_and_val.split('=')
            str_kwargs[argname] = str_argval
    else:
        function_name = strval
    return function_name, str_kwargs


def create_nnet_element(strval):
    '''Create a pytorch nnet element from a string.

    Args:
        strval (string): String defining the nnet element.

    Returs:
        ``torch.nn.<object>``

    '''
    str_elements = strval.split('|')
    elements = []
    for str_element in str_elements:
        function_name, str_kwargs = parse_nnet_element(str_element)
        if hasattr(torch.nn, function_name):
            function = getattr(torch.nn, function_name)
        # If the function name is not part of the pytorch std. API look
        # for the beer extensions or raise an error.
        elif function_name == 'ReshapeLayer':
            function = ReshapeLayer
        else:
            raise ValueError('Unknown nnet element type: {}'.format(function_name))
        kwargs = {
            argname: load_value(arg_strval)
            for argname, arg_strval in str_kwargs.items()
        }
        elements.append(function(**kwargs))
    if len(elements) > 1:
        return MergeTransform(*elements)
    else:
        return elements[0]


def create_nnet_block(block_conf):
    '''Create a part of neural network.

    Args:
        block_conf (dict): Configuration dictionary.

    Returns:
        :any:`NeuralNetworkBlock`

    '''
    structure_list = [create_nnet_element(strval)
                      for strval in block_conf['block_structure']]
    structure = torch.nn.Sequential(*structure_list)
    res_connection = block_conf['residual']
    return NeuralNetworkBlock(structure, res_connection)


def create(nnet_blocks_conf, dtype, device):
    '''Create a neural network.

    Args:
        nnet_blocks_conf (list of dict): Configuration dictionaries.
        dtype (``torch.dtype``): Type of the returned network.
        device (``torch.device``): Device of the returned network.

    Returns:
        list of :any:`NeuralNetworkBlock`

    '''
    network = torch.nn.Sequential(*[create_nnet_block(block_conf)
                                  for block_conf in nnet_blocks_conf])
    return network.type(dtype).to(device)


# This module does not have a public interface.
__all__ = []
