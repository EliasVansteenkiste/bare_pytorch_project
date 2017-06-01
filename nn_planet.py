import lasagne as nn
import theano.tensor as T
import numpy as np
from lasagne import nonlinearities
from lasagne.layers.dnn import Conv2DDNNLayer


def lb_softplus(lb=1):
    return lambda x: nn.nonlinearities.softplus(x) + lb


class MultLayer(nn.layers.MergeLayer):
    """
    takes elementwise product between 2 layers
    """

    def __init__(self, input1, input2, log=False, **kwargs):
        super(MultLayer, self).__init__([input1, input2], **kwargs)

    def get_output_shape_for(self, input_shapes):
        return input_shapes[0]

    def get_output_for(self, inputs, **kwargs):
        return inputs[0] * inputs[1]


class ConstantLayer(nn.layers.Layer):
    """
    Makes a layer of constant value the same shape as the given input layer
    """

    def __init__(self, shape_layer, constant=1, **kwargs):
        super(ConstantLayer, self).__init__(shape_layer, **kwargs)
        self.constant = constant

    def get_output_shape_for(self, input_shape):
        return input_shape

    def get_output_for(self, input, **kwargs):
        return T.ones_like(input) * self.constant


class RepeatLayer(nn.layers.Layer):
    def __init__(self, incoming, repeats, axis=0, **kwargs):
        super(RepeatLayer, self).__init__(incoming, **kwargs)
        self.repeats = repeats
        self.axis = axis

    def get_output_shape_for(self, input_shape):
        output_shape = list(input_shape)
        output_shape.insert(self.axis, self.repeats)
        return tuple(output_shape)

    def get_output_for(self, input, **kwargs):
        shape_ones = [1] * input.ndim
        shape_ones.insert(self.axis, self.repeats)
        ones = T.ones(tuple(shape_ones), dtype=input.dtype)

        pattern = range(input.ndim)
        pattern.insert(self.axis, "x")
        # print shape_ones, pattern
        return ones * input.dimshuffle(*pattern)


class AttentionLayer(nn.layers.Layer):
    def __init__(self, incoming, u=nn.init.GlorotUniform(), **kwargs):
        super(AttentionLayer, self).__init__(incoming, **kwargs)
        num_inputs = self.input_shape[-1]
        self.u = self.add_param(u, (num_inputs, 1), name='u')

    def get_output_shape_for(self, input_shape):
        return input_shape[0], input_shape[-1]

    def get_output_for(self, input, **kwargs):
        a = T.nnet.softmax(T.dot(input, self.u)[:, :, 0])
        return T.sum(a[:, :, np.newaxis] * input, axis=1)


class MaskedMeanPoolLayer(nn.layers.MergeLayer):
    """
    pools globally across all trailing dimensions beyond the given axis.
    give it a mask
    """

    def __init__(self, incoming, mask, axis, **kwargs):
        super(MaskedMeanPoolLayer, self).__init__([incoming, mask], **kwargs)
        self.axis = axis

    def get_output_shape_for(self, input_shapes):
        return input_shapes[0][:self.axis] + (1,)

    def get_output_for(self, inputs, **kwargs):
        input = inputs[0]
        mask = inputs[1]
        masked_input = input * mask.dimshuffle(0, 1, 'x')
        return T.sum(masked_input.flatten(self.axis + 1), axis=self.axis, keepdims=True) / T.sum(mask, axis=-1,
                                                                                                 keepdims=True)


class MaskedSTDPoolLayer(nn.layers.MergeLayer):
    """
    pools globally across all trailing dimensions beyond the given axis.
    give it a mask
    """

    def __init__(self, incoming, mask, axis, **kwargs):
        super(MaskedSTDPoolLayer, self).__init__([incoming, mask], **kwargs)
        self.axis = axis

    def get_output_shape_for(self, input_shapes):
        return input_shapes[0][:self.axis] + (1,)

    def get_output_for(self, inputs, **kwargs):
        input = inputs[0]
        mask = inputs[1]
        masked_input = input * mask.dimshuffle(0, 1, 'x')
        mu_x = T.sum(masked_input.flatten(self.axis + 1), axis=self.axis, keepdims=True) / T.sum(mask, axis=-1,
                                                                                                 keepdims=True)
        mu_x2 = T.sum(masked_input.flatten(self.axis + 1) ** 2, axis=self.axis, keepdims=True) / T.sum(mask, axis=-1,
                                                                                                       keepdims=True)
        return T.sqrt(mu_x2 - mu_x ** 2)







class NormalisationLayer(nn.layers.Layer):
    def __init__(self, incoming, norm_sum=1.0, allow_negative=False, **kwargs):
        super(NormalisationLayer, self).__init__(incoming, **kwargs)
        self.norm_sum = norm_sum
        self.allow_negative = allow_negative

    def get_output_for(self, input, **kwargs):
        # take the minimal working slice size, and use that one.
        if self.allow_negative:
            inp_low_zero = input - T.min(input, axis=1).dimshuffle(0, 'x')
        else:
            inp_low_zero = input
        return inp_low_zero / T.sum(inp_low_zero, axis=1).dimshuffle(0, 'x') * self.norm_sum


class HighwayLayer(nn.layers.MergeLayer):
    def __init__(self, gate, input1, input2, **kwargs):
        incomings = [gate, input1, input2]
        super(HighwayLayer, self).__init__(incomings, **kwargs)
        assert gate.output_shape == input1.output_shape == input2.output_shape

    def get_output_shape_for(self, input_shapes):
        return input_shapes[0]

    def get_output_for(self, inputs, **kwargs):
        return inputs[0] * inputs[1] + (1 - inputs[0]) * inputs[2]


def highway_conv3(incoming, nonlinearity=nn.nonlinearities.rectify, **kwargs):
    wh = nn.init.Orthogonal('relu')
    bh = nn.init.Constant(0.0)
    wt = nn.init.Orthogonal('relu')
    bt = nn.init.Constant(-2.)
    num_filters = incoming.output_shape[1]

    # H
    l_h = Conv2DDNNLayer(incoming, num_filters=num_filters,
                         filter_size=(3, 3), stride=(1, 1),
                         pad='same', W=wh, b=bh,
                         nonlinearity=nonlinearity)
    # T
    l_t = Conv2DDNNLayer(incoming, num_filters=num_filters,
                         filter_size=(3, 3), stride=(1, 1),
                         pad='same', W=wt, b=bt,
                         nonlinearity=T.nnet.sigmoid)

    return HighwayLayer(gate=l_t, input1=l_h, input2=incoming)


class CastingLayer(nn.layers.Layer):
    def __init__(self, incoming, dtype, **kwargs):
        super(CastingLayer, self).__init__(incoming, **kwargs)
        self.dtype = dtype

    def get_output_for(self, input, **kwargs):
        return T.cast(input, self.dtype)


def heaviside(x, size):
    return T.arange(0, size).dimshuffle('x', 0) - T.repeat(x, size, axis=1) >= 0.


class NormalCDFLayer(nn.layers.MergeLayer):
    def __init__(self, mu, sigma, max_support, **kwargs):
        super(NormalCDFLayer, self).__init__([mu, sigma], **kwargs)
        self.max_support = max_support

    def get_output_shape_for(self, input_shapes):
        return input_shapes[0][0], self.max_support

    def get_output_for(self, input, **kwargs):
        mu = input[0]
        sigma = input[1]

        x_range = T.arange(0, self.max_support).dimshuffle('x', 0)
        mu = T.repeat(mu, self.max_support, axis=1)
        sigma = T.repeat(sigma, self.max_support, axis=1)
        x = (x_range - mu) / (sigma * T.sqrt(2.) + 1e-16)
        cdf = (T.erf(x) + 1.) / 2.
        return cdf



class AggAllBenignExp(nn.layers.Layer):
    """
    Aggregates the chances
    """

    def __init__(self, incoming, **kwargs):
        super(AggAllBenignExp, self).__init__(incoming, **kwargs)

    def get_output_shape_for(self, input_shape):
        assert(len(input_shape)==3)
        assert(input_shape[2]==1)
        return (input_shape[0], 1)

    def get_output_for(self, input, **kwargs):
        rectified = nonlinearities.softplus(input)
        sum_rect = T.sum(rectified, axis=(1,2))
        output = 1 - T.exp(-sum_rect)
        return output

class AggAllBenignProd(nn.layers.Layer):
    """
    takes elementwise product between 2 layers
    """

    def __init__(self, incoming, apply_nl = True, **kwargs):
        super(AggAllBenignProd, self).__init__(incoming, **kwargs)
        self.apply_nl = apply_nl

    def get_output_shape_for(self, input_shape):
        assert(len(input_shape)==3)
        assert(input_shape[2]==1)
        return (input_shape[0], 1)

    def get_output_for(self, input, **kwargs):
        if apply_nl:
            ps = nonlinearities.sigmoid(input)
        prod = T.prod(ps, axis=(1,2))
        output = 1 - prod
        return output

class AggSoPP(nn.layers.Layer):
    """
    Aggregates via Sum of powers
    """
    def __init__(self, incoming, exp=nn.init.Constant(2.),  **kwargs):
        super(AggSoPP, self).__init__(incoming, **kwargs)
        self.exp = self.add_param(exp, (1,), name='exp', regularizable=False)

    def get_output_shape_for(self, input_shape):
        assert(len(input_shape)==3)
        assert(input_shape[2]==1)
        return (input_shape[0], 1)

    def get_output_for(self, input, **kwargs):
        ps = nonlinearities.sigmoid(input)
        powd = ps ** self.exp
        tmean = T.mean(powd, axis=(1,2))
        return tmean

class Unbroadcast(nn.layers.Layer):
    """
    takes elementwise product between 2 layers
    """
    def __init__(self, incoming,  **kwargs):
        super(Unbroadcast, self).__init__(incoming, **kwargs)

    def get_output_shape_for(self, input_shape):
        return input_shape

    def get_output_for(self, input, **kwargs):
        all_dims = range(len(T.shape(input)))
        print all_dims
        return T.Unbroadcast(input, *all_dims)

class LogMeanExp(nn.layers.Layer):
    """
    ln(mean(exp( r * x ))) /  r
    """

    def __init__(self, incoming, r=1, axis=(0,1), **kwargs):
        super(LogMeanExp, self).__init__(incoming, **kwargs)
        self.r = np.float32(r)
        self.axis = axis

    def get_output_shape_for(self, input_shape):
        return (input_shape[0], input_shape[1])

    def get_output_for(self, input, **kwargs):
        return T.log(T.mean(T.exp(self.r * input), axis=self.axis) + 1e-7) / self.r


class AggMILLoss(nn.layers.Layer):
    """
    ln(mean(exp( r * x ))) /  r
    """

    def __init__(self, incoming, r=1, **kwargs):
        super(AggMILLoss, self).__init__(incoming, **kwargs)
        self.r = np.float32(r)

    def get_output_shape_for(self, input_shape):
        assert(len(input_shape)==3)
        assert(input_shape[2]==1)
        return (input_shape[0], 2)

    def get_output_for(self, input, **kwargs):

        ps = nonlinearities.sigmoid(input)
        sum_p_r_benign = T.sum(ps,axis=1)
        sum_log = T.sum(T.log(1-ps+1.e-12),axis=1)
        return T.concatenate([sum_log, sum_p_r_benign])

def tlogit(x):
    return T.log(x/(np.float32(1)-x))

def texpit(x):
    return np.float32(1)/(np.float32(1)+T.exp(-x))

def part_softmax(x,a):
    T.exp(-x)

class MajorExclusivityLayer(nn.layers.Layer):
    def __init__(self, incoming, idx_major, apply_nl=True,
                 **kwargs):
        super(MajorExclusivityLayer, self).__init__(incoming, **kwargs)
        self.apply_nl = apply_nl
        self.idx_major = idx_major

    def get_output_shape_for(self, input_shape):
        assert len(input_shape)==2
        assert self.idx_major < input_shape[1]
        return input_shape

    def get_output_for(self, input, **kwargs):
        sig = T.nnet.sigmoid(input)
        output = sig * (1-sig[:,self.idx_major][:,None])
        return output
