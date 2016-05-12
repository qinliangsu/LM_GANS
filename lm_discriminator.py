'''
Build a simple neural language model using GAN Auxillary loss
'''
import theano
import theano.tensor as tensor
from theano.sandbox.rng_mrg import MRG_RandomStreams as RandomStreams
import numpy
import os


from layers import get_layer




profile = False

def save_params(params, filename, symlink=None):
    """Save the parameters.
       Saves the parameters as an ``.npz`` file. It optionally also creates a
       symlink to this archive.
    """
    numpy.savez(filename, **params)
    if symlink:
        if os.path.lexists(symlink):
            os.remove(symlink)
        os.symlink(filename, symlink)


# build a training model
def build_GAN_model(tparams, options):
    opt_ret = dict()

    trng = RandomStreams(1234)
    use_noise = theano.shared(numpy.float32(0.))

    # description string: #words x #samples
    x = tensor.matrix('x', dtype='int64')
    x_mask = tensor.matrix('x_mask', dtype='float32')
    bern_dist = tensor.matrix('bern_dist', dtype='float32')
    uniform_sampling = tensor.vector('uniform_sampling', dtype='float32')

    n_timesteps = x.shape[0]
    n_samples = x.shape[1]

    # input
    emb = tparams['Wemb'][x.flatten()]

    emb = emb.reshape([n_timesteps, n_samples, options['dim_word']])
    emb_shifted = tensor.zeros_like(emb)
    emb_shifted = tensor.set_subtensor(emb_shifted[1:], emb[:-1])
    emb = emb_shifted
    opt_ret['emb'] = emb

    print "embedding dim", emb.ndim

    initial_state_reshaped = tensor.concatenate([tparams['initial_hidden_state_1']] * 32, axis = 0) + 0.0 * tensor.mean(tparams['initial_hidden_state_0'])

    # pass through gru layer, recurrence here
    proj = get_layer(options['encoder'])[1](tparams, emb, options,
                                            prefix='encoder',
                                            mask=x_mask, init_state = initial_state_reshaped[:,0:512])

    proj[0] = proj[0] + 0.0 * tensor.sum(bern_dist) + 0.0 * tensor.sum(uniform_sampling)

    proj_1 = get_layer(options['encoder'])[1](tparams, proj[0], options,
                                            prefix='encoder_1',
                                            mask=None, init_state = initial_state_reshaped[:,512:512*2])

    proj_2 = get_layer(options['encoder'])[1](tparams, proj_1[0], options,
                                            prefix='encoder_2',
                                            mask=None, init_state = initial_state_reshaped[:,512*2:512*3])

    #1024 x 30

    states_concat = tensor.concatenate([proj[0], proj_1[0], proj_2[0]], axis = 2)
    
    # compute word probabilities
    logit_lstm = get_layer('ff')[1](tparams, proj_2[0], options,
                                    prefix='ff_logit_lstm', activ='linear')
    logit_prev = get_layer('ff')[1](tparams, emb, options,
                                    prefix='ff_logit_prev', activ='linear')

    logit_init = tensor.tanh(logit_lstm + logit_prev)

    #proj_h = proj[0]
    #opt_ret['proj_h'] = proj_h

    logit = get_layer('ff')[1](tparams, logit_init, options, prefix='ff_logit',
                               activ='linear')
    
    logit_shp = logit.shape
    probs = tensor.nnet.softmax(
        logit.reshape([logit_shp[0]*logit_shp[1], logit_shp[2]]))


    # cost
    x_flat = x.flatten()
    x_flat_idx = tensor.arange(x_flat.shape[0]) * options['n_words'] + x_flat


    cost = -tensor.log(probs.flatten()[x_flat_idx])
    cost = cost.reshape([x.shape[0], x.shape[1]])
    opt_ret['cost_per_sample'] = cost
    cost = (cost * x_mask).sum(0)


    #get_proj_h = theano.function([x, x_mask, bern_dist, uniform_sampling],[states_concat])
    ##states_concat_disc = tensor.concatenate([proj[0], proj_1[0], proj_2[0], logit_init], axis = 2)
    states_concat_disc = tensor.concatenate([logit_lstm + logit_prev, logit], axis = 2)

    get_proj_h = theano.function([x, x_mask, bern_dist, uniform_sampling],[states_concat_disc])

    return trng, use_noise, x, x_mask, opt_ret, cost, bern_dist, uniform_sampling, states_concat_disc, emb, get_proj_h, probs

