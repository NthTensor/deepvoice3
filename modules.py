# -*- coding: utf-8 -*-
#/usr/bin/python2
'''
By kyubyong park. kbpark.linguist@gmail.com. 
https://www.github.com/kyubyong/deepvoice3
'''

from __future__ import print_function, division

from hyperparams import Hyperparams as hp
import tensorflow as tf
import numpy as np
import math


def embed(inputs, vocab_size, num_units, zero_pad=True, scope="embedding", reuse=None):
    '''Embeds a given tensor. 
    
    Args:
      inputs: A `Tensor` with type `int32` or `int64` containing the ids
         to be looked up in `lookup table`.
      vocab_size: An int. Vocabulary size.
      num_units: An int. Number of embedding hidden units.
      zero_pad: A boolean. If True, all the values of the fist row (id 0)
        should be constant zeros.
      scope: Optional scope for `variable_scope`.  
      reuse: Boolean, whether to reuse the weights of a previous layer
        by the same name.
        
    Returns:
      A `Tensor` with one more rank than inputs's. The last dimesionality
        should be `num_units`.
    '''
    with tf.variable_scope(scope, reuse=reuse):
        lookup_table = tf.get_variable('lookup_table', 
                                       dtype=tf.float32, 
                                       shape=[vocab_size, num_units],
                                       initializer=tf.contrib.layers.xavier_initializer())
        if zero_pad:
            lookup_table = tf.concat((tf.zeros(shape=[1, num_units]), 
                                      lookup_table[1:, :]), 0)
    return tf.nn.embedding_lookup(lookup_table, inputs)   
 
def normalize(inputs, 
              type="bn",
              decay=.999,
              epsilon=1e-8,
              training=True, 
              activation_fn=None,
              reuse=None,
              scope="normalize"):
    '''Applies {batch|layer|weight} normalization.
    
    Args:
      inputs: A tensor with 2 or more dimensions, where the first dimension has
        `batch_size`. If type is `bn`, the normalization is over all but 
        the last dimension. Or if type is `ln`, the normalization is over 
        the last dimension. Note that this is different from the native 
        `tf.contrib.layers.batch_norm`. For this I recommend you change
        a line in ``tensorflow/contrib/layers/python/layers/layer.py` 
        as follows.
        Before: mean, variance = nn.moments(inputs, axis, keep_dims=True)
        After: mean, variance = nn.moments(inputs, [-1], keep_dims=True)
      type: A string. Either "bn" or "ln".
      decay: Decay for the moving average. Reasonable values for `decay` are close
        to 1.0, typically in the multiple-nines range: 0.999, 0.99, 0.9, etc.
        Lower `decay` value (recommend trying `decay`=0.9) if model experiences
        reasonably good training performance but poor validation and/or test
        performance.
      training: Whether or not the layer is in training mode. W
      activation_fn: Activation function.
      scope: Optional scope for `variable_scope`.
      
    Returns:
      A tensor with the same shape and data dtype as `inputs`.
    '''
    if type=="bn":
        inputs_shape = inputs.get_shape()
        inputs_rank = inputs_shape.ndims
        
        # use fused batch norm if inputs_rank in [2, 3, 4] as it is much faster.
        # pay attention to the fact that fused_batch_norm requires shape to be rank 4 of NHWC.
        if inputs_rank in [2, 3, 4]:
            if inputs_rank==2:
                inputs = tf.expand_dims(inputs, axis=1)
                inputs = tf.expand_dims(inputs, axis=2)
            elif inputs_rank==3:
                inputs = tf.expand_dims(inputs, axis=1)
            
            outputs = tf.contrib.layers.batch_norm(inputs=inputs, 
                                               decay=decay,
                                               center=True, 
                                               scale=True, 
                                               updates_collections=None,
                                               is_training=training,
                                               scope=scope,
                                               fused=True,
                                               reuse=reuse)
            # restore original shape
            if inputs_rank==2:
                outputs = tf.squeeze(outputs, axis=[1, 2])
            elif inputs_rank==3:
                outputs = tf.squeeze(outputs, axis=1)
        else: # fallback to naive batch norm
            outputs = tf.contrib.layers.batch_norm(inputs=inputs, 
                                               decay=decay,
                                               center=True, 
                                               scale=True, 
                                               updates_collections=None,
                                               training=training,
                                               scope=scope,
                                               reuse=reuse,
                                               fused=False)    
    elif type in ("ln",  "ins"):
        reduction_axis = -1 if type=="ln" else 1   
        with tf.variable_scope(scope, reuse=reuse):
            inputs_shape = inputs.get_shape()
            params_shape = inputs_shape[-1:]
        
            mean, variance = tf.nn.moments(inputs, [reduction_axis], keep_dims=True)
            beta = tf.Variable(tf.zeros(params_shape))
            gamma = tf.Variable(tf.ones(params_shape))
            normalized = (inputs - mean) / ( (variance + epsilon) ** (.5) )
            outputs = gamma * normalized + beta
    else:
        outputs = inputs
    
    if activation_fn:
        outputs = activation_fn(outputs)
                
    return outputs

def conv1d(inputs, 
           filters=None, 
           size=1, 
           rate=1, 
           padding="SAME", 
           use_bias=True,
           activation_fn=None,
           scope="conv1d",
           reuse=None):
    '''
    Args:
      inputs: A 3-D tensor with shape of [batch, time, depth].
      filters: An int. Number of outputs (=activation maps)
      size: An int. Filter size.
      rate: An int. Dilation rate.
      padding: Either `same` or `valid` or `causal` (case-insensitive).
      use_bias: A boolean.
      scope: Optional scope for `variable_scope`.
      reuse: Boolean, whether to reuse the weights of a previous layer
        by the same name.
    
    Returns:
      A masked tensor of the same shape and dtypes as `inputs`.
    '''
    with tf.variable_scope(scope):
        if padding.lower()=="causal":
            # pre-padding for causality
            pad_len = (size - 1) * rate  # padding size
            inputs = tf.pad(inputs, [[0, 0], [pad_len, 0], [0, 0]])
            padding = "valid"
        
        if filters is None:
            filters = inputs.get_shape().as_list[-1]
        
        params = {"inputs":inputs, "filters":filters, "kernel_size":size,
                "dilation_rate":rate, "padding":padding, "activation":activation_fn, 
                "use_bias":use_bias, "reuse":reuse, "kernel_initializer":tf.contrib.layers.xavier_initializer()}
        
        outputs = tf.layers.conv1d(**params)
    return outputs

def glu(inputs):
    '''Gated linear unit
    Args:
      inputs: A tensor of even dimensions.

    Returns:
      outputs: A tensor of the same shape and dtype as inputs.
    '''
    A, B = tf.split(inputs, 2, -1)  # (N, T_x, c) * 2
    outputs = A*tf.nn.sigmoid(B)
    return outputs

def conv_block(inputs,
               size=5,
               dropout_rate=0,
               padding="SAME",
               norm_type=None,
               activation_fn=None,
               training=False,
               scope="conv_block",
               reuse=None):
    '''Convolution block.
    Args:
      inputs: A 3-D tensor with shape of [batch, time, depth].
      size: An int. Filter size.
      dropout_rate: A float of [0, 1]. Dropout rate.
      padding: Either `same` or `valid` or `causal` (case-insensitive).
      norm_type: A string. See `normalize`.
      activation_fn: A string. Activation function.
      training: A boolean. Whether or not the layer is in training mode.
      scope: Optional scope for `variable_scope`.
      reuse: Boolean, whether to reuse the weights of a previous layer
        by the same name.

    Returns:
      A tensor of the same shape and dtype as inputs.
    '''
    num_inputs = inputs.get_shape()[-1]
    _inputs = inputs
    with tf.variable_scope(scope, reuse=reuse):
        inputs = tf.layers.dropout(inputs, rate=dropout_rate, training=training)
        inputs = conv1d(inputs, num_inputs*2, size=size, padding=padding)  # (N, T_x, c*2)
        inputs = normalize(inputs, type=norm_type, training=training, activation_fn=activation_fn)
        inputs += _inputs # residual connection
        inputs *= math.sqrt(0.5) # scale

    return inputs

def fc_block(inputs,
             num_units,
             dropout_rate=0,
             norm_type=None,
             activation_fn=None,
             training=False,
             scope="fc_block",
             reuse=None):
    '''Fully connected layer block.
        Args:
          inputs: A 3-D tensor with shape of [batch, time, depth].
          num_units: An int. Output dimensionality.
          dropout_rate: A float of [0, 1]. Dropout rate.
          norm_type: A string. See `normalize`.
          activation_fn: A string. Activation function.
          training: A boolean. Whether or not the layer is in training mode.
          scope: Optional scope for `variable_scope`.
          reuse: Boolean, whether to reuse the weights of a previous layer
            by the same name.

        Returns:
          A tensor with shape of [batch, time, num_units].
    '''
    with tf.variable_scope(scope, reuse=reuse):
        inputs = tf.layers.dropout(inputs, rate=dropout_rate, training=training)

        # Transformation
        tensor = tf.layers.dense(inputs, units=num_units, activation=None)  # (N, T_x, c1)

        # Normalization -> Activation
        tensor = normalize(tensor, type=norm_type, training=training, activation_fn=activation_fn)

    return tensor


def positional_encoding(inputs,
                        num_units,
                        position_rate=1.,
                        zero_pad=True,
                        scale=True,
                        scope="positional_encoding",
                        reuse=None):
    '''Sinusoidal Positional_Encoding.

    Args:
      inputs: A 2d Tensor with shape of (N, T).
      num_units: Output dimensionality
      position_rate: A float. Average slope of the line in the attention distribution
      zero_pad: Boolean. If True, all the values of the first row (id = 0) should be constant zero
      scale: Boolean. If True, the output will be multiplied by sqrt num_units(check details from paper)
      scope: Optional scope for `variable_scope`.
      reuse: Boolean, whether to reuse the weights of a previous layer
        by the same name.

    Returns:
        A 'Tensor' with one more rank than inputs's, with the dimensionality should be 'num_units'
    '''

    N, T = inputs.get_shape().as_list()
    with tf.variable_scope(scope, reuse=reuse):
        position_ind = tf.tile(tf.expand_dims(tf.range(T), 0), [N, 1])

        # First part of the PE function: sin and cos argument
        position_enc = np.array([
            [pos*position_rate / np.power(10000, 2.*i/num_units) for i in range(num_units)]
            for pos in range(T)])

        # Second part, apply the cosine to even columns and sin to odds.
        position_enc[:, 0::2] = np.sin(position_enc[:, 0::2])  # dim 2i
        position_enc[:, 1::2] = np.cos(position_enc[:, 1::2])  # dim 2i+1

        # Convert to a tensor
        lookup_table = tf.convert_to_tensor(position_enc, tf.float32)

        if zero_pad:
            lookup_table = tf.concat((tf.zeros(shape=[1, num_units]),
                                      lookup_table[1:, :]), 0)
        outputs = tf.nn.embedding_lookup(lookup_table, position_ind)

        if scale:
            outputs = outputs * num_units**0.5

        return outputs

def attention_block(queries,
                    keys,
                    vals,
                    num_units,
                    dropout_rate=0,
                    prev_max_attentions=None,
                    norm_type=None,
                    activation_fn=None,
                    training=False,
                    scope="attention_block",
                    reuse=None):
    '''Attention block.
     Args:
       queries: A 3-D tensor with shape of [batch, T_y//r, dec_channels].
       keys: A 3-D tensor with shape of [batch, T_x, embed_size].
       vals: A 3-D tensor with shape of [batch, T_x, embed_size].
       num_units: An int. Attention size.
       dropout_rate: A float of [0, 1]. Dropout rate.
       norm_type: A string. See `normalize`.
       activation_fn: A string. Activation function.
       training: A boolean. Whether or not the layer is in training mode.
       scope: Optional scope for `variable_scope`.
       reuse: Boolean, whether to reuse the weights of a previous layer
         by the same name.

     Returns:
       A tensor with shape of [batch, time, num_units].
    '''
    num_inputs = queries.get_shape().as_list()[-1]
    with tf.variable_scope(scope, reuse=reuse):
        queries += positional_encoding(queries[:, :, 0],
                                      num_units=num_inputs,
                                      position_rate=hp.T_x/hp.T_y,
                                      zero_pad=False,
                                      scale=True)  # (N, T_y/r, d)
        keys += positional_encoding(keys[:, :, 0],
                                    num_units=keys.get_shape().as_list()[-1],
                                    position_rate=1.,
                                    zero_pad=False,
                                    scale=True)  # (N, T_y/r, e)

        queries = fc_block(queries,
                           num_units=num_units,
                           dropout_rate=0,
                           norm_type=norm_type,
                           training=training,
                           activation_fn=activation_fn,
                           scope="queries_fc_block")  # (N, T_y/r, a)
        keys = fc_block(keys,
                        num_units=num_units,
                        dropout_rate=0,
                        norm_type=norm_type,
                        training=training,
                        activation_fn=activation_fn,
                        scope="keys_fc_block")  # (N, T_x, a)
        vals = fc_block(vals,
                        num_units=num_units,
                        dropout_rate=0,
                        norm_type=norm_type,
                        training=training,
                        activation_fn=activation_fn,
                        scope="vals_fc_block")  # (N, T_x, a)

        attention_weights = tf.matmul(queries, keys, transpose_b=True)  # (N, T_y/r, T_x)
        if training:
            alignments = tf.nn.softmax(attention_weights)
            max_attentions = prev_max_attentions
        else: # force monotonic attention
            n, t, c = attention_weights.get_shape().as_list()
            paddings = tf.ones_like(attention_weights) * (-2 ** 32 + 1)
            masks = tf.sequence_mask(prev_max_attentions, c)
            masks = tf.tile(tf.expand_dims(masks, 1), [1, t, 1])
            attention_weights = tf.where(tf.equal(masks, False), attention_weights, paddings)
            alignments = tf.nn.softmax(attention_weights)
            max_attentions = tf.argmax(alignments, -1)

        tensor = tf.layers.dropout(alignments, rate=dropout_rate, training=training)
        tensor = tf.matmul(tensor, vals)  # (N, T_y/r, a)
        tensor /= tf.sqrt(tf.to_float(tensor.get_shape()[-1]))

        # Restore shape for residual connection
        tensor = fc_block(tensor,
                          num_units=num_inputs,
                          dropout_rate=0,
                          norm_type=norm_type,
                          training=training,
                          activation_fn=activation_fn,
                          scope="tensor_fc_block")  # (N, T_x, dec_channels)

    return tensor, alignments, max_attentions