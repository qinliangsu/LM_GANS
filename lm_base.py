'''
Build a simple neural language model using GRU units
'''
import theano
import theano.tensor as tensor
from theano.sandbox.rng_mrg import MRG_RandomStreams as RandomStreams
import ipdb
import numpy
import numpy as np
import os
import sys
from collections import OrderedDict
from utils import  norm_weight
from layers import get_layer

batch_size = 16

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


# batch preparation, returns padded batch and mask
def prepare_data(seqs_x, maxlen=30, n_words=30000, minlen=0):
    # x: a list of sentences
    lengths_x = [len(s) for s in seqs_x]

    # filter according to mexlen
    if maxlen is not None:
        new_seqs_x = []
        new_lengths_x = []
        for l_x, s_x in zip(lengths_x, seqs_x):
            if True:#l_x < maxlen:
                new_seqs_x.append(s_x[:maxlen])
                new_lengths_x.append(min(l_x,maxlen))
        lengths_x = new_lengths_x
        seqs_x = new_seqs_x

        if len(lengths_x) < 1:
            return None, None


    n_samples = len(seqs_x)
#    maxlen_x = numpy.max(lengths_x) + 1


    x = numpy.zeros((maxlen, n_samples)).astype('int64')
    x_mask = numpy.zeros((maxlen, n_samples)).astype('float32')
    for idx, s_x in enumerate(seqs_x):
        x[:lengths_x[idx], idx] = s_x
        x_mask[:lengths_x[idx]+1, idx] = 1.

    return x, x_mask



# initialize all parameters
def init_params(options):
    params = OrderedDict()
    # embedding
    params['Wemb'] = norm_weight(options['n_words'], options['dim_word'])
    params = get_layer(options['encoder'])[0](options, params,
                                              prefix='encoder',
                                              nin=options['dim_word'],
                                              dim=options['dim'])
    #params = get_layer(options['encoder'])[0](options, params,
    #                                          prefix='encoder_1',
    #                                          nin=options['dim'],
    #                                          dim=options['dim'])
    #params = get_layer(options['encoder'])[0](options, params,
    #                                          prefix='encoder_2',
    #                                          nin=options['dim'],
    #                                          dim=options['dim'])
    # readout
    params = get_layer('ff')[0](options, params, prefix='ff_logit_lstm',
                                nin=options['dim'], nout=options['dim_word'],
                                ortho=False)
    params = get_layer('ff')[0](options, params, prefix='ff_logit_prev',
                                nin=options['dim_word'],
                                nout=options['dim_word'], ortho=False)
    params = get_layer('ff')[0](options, params, prefix='ff_logit',
                                nin=options['dim_word'],
                                nout=options['n_words'])


    #params['initial_hidden_state_0'] = np.zeros(shape = (1, options['dim'])).astype('float32')
    params['initial_hidden_state_1'] = np.zeros(shape = (1, options['dim'])).astype('float32')

    return params


# build a training model
def build_model(tparams, options):
    opt_ret = dict()

    trng = RandomStreams(1234)
    use_noise = theano.shared(numpy.float32(0.))

    # description string: #words x #samples
    x = tensor.matrix('x', dtype='int64')
    x_mask = tensor.matrix('x_mask', dtype='float32')

    n_timesteps = x.shape[0]
    n_samples = x.shape[1]

    # input
    emb = tparams['Wemb'][x.flatten()]
    emb = emb.reshape([n_timesteps, n_samples, options['dim_word']])
    emb_shifted = tensor.zeros_like(emb)
    emb_shifted = tensor.set_subtensor(emb_shifted[1:], emb[:-1])
    emb = emb_shifted
    opt_ret['emb'] = emb

    # pass through gru layer, recurrence here
    proj = get_layer(options['encoder'])[1](tparams, emb, options,
                                            prefix='encoder',
                                            mask=x_mask)
    proj_h = proj[0]
    opt_ret['proj_h'] = proj_h

    # compute word probabilities
    logit_lstm = get_layer('ff')[1](tparams, proj_h, options,
                                    prefix='ff_logit_lstm', activ='linear')
    logit_prev = get_layer('ff')[1](tparams, emb, options,
                                    prefix='ff_logit_prev', activ='linear')
    logit = tensor.tanh(logit_lstm+logit_prev)
    logit = get_layer('ff')[1](tparams, logit, options, prefix='ff_logit',
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

    return trng, use_noise, x, x_mask, opt_ret, cost, probs


# build a sampler
def build_sampler(tparams, options, trng,biased_sampling_term):
    # x: 1 x 1
    y = tensor.vector('y_sampler', dtype='int64')
    init_state = tensor.matrix('init_state', dtype='float32')

    # if it's the first word, emb should be all zero
    emb = tensor.switch(y[:, None] < 0,
                        tensor.alloc(0., 1, tparams['Wemb'].shape[1]),
                        tparams['Wemb'][y])

    # apply one step of gru layer
    proj = get_layer(options['encoder'])[1](tparams, emb, options,
                                            prefix='encoder',
                                            mask=None,
                                            one_step=True,
                                            init_state=init_state)

    next_state = proj[0]
    #32 x 1024
    

    #next_state = proj[0]

    # compute the output probability dist and sample
    logit_lstm = get_layer('ff')[1](tparams, proj[0], options,
                                    prefix='ff_logit_lstm', activ='linear')
    logit_prev = get_layer('ff')[1](tparams, emb, options,
                                    prefix='ff_logit_prev', activ='linear')
    logit = tensor.tanh(logit_lstm + logit_prev)

    logit = get_layer('ff')[1](tparams, logit, options,
                               prefix='ff_logit', activ='linear')
    next_probs = tensor.nnet.softmax(logit * biased_sampling_term)
    next_sample = trng.multinomial(pvals=next_probs).argmax(1)

    # next word probability
    print 'Building f_next..',
    inps = [y, init_state]
    outs = [next_probs, next_sample, next_state]
    f_next = theano.function(inps, outs, name='f_next', profile=profile)
    print 'Done'

    return f_next


# generate sample
def gen_sample_batch(tparams, f_next, options, trng=None, maxlen=30, argmax=False, batch_size = batch_size, initial_state = None):

    sample = []
    sample_score = 0

    # initial token is indicated by a -1 and initial state is zero
    next_w = -1 * numpy.ones((batch_size,)).astype('int64')
    if initial_state == None:
        next_state = numpy.zeros((batch_size, 3 * options['dim'])).astype('float32')
    else:
        next_state = np.vstack([initial_state] * batch_size)
        print "next state shape", next_state.shape

    next_state_lst = []

    for ii in xrange(maxlen):
        inps = [next_w, next_state]
        ret = f_next(*inps)
        next_p, next_w, next_state = ret[0], ret[1], ret[2]

        next_state_lst += [next_state]

        sample.append(next_w)

        #sample.append(nw)
        #sample_score += next_p[0, nw]
        
        #if nw == 0:
        #    break

    sample_return = np.asarray(sample).T.tolist()

    return sample_return, 0.0, 0.0


# generate sample
def gen_sample(tparams, f_next, options, trng=None, maxlen=30, argmax=False):

    sample = []
    sample_score = 0

    # initial token is indicated by a -1 and initial state is zero
    next_w = -1 * numpy.ones((1,)).astype('int64')
    next_state = numpy.zeros((1, 3 * options['dim'])).astype('float32')

    next_state_lst = []

    for ii in xrange(maxlen):
        inps = [next_w, next_state]
        ret = f_next(*inps)
        next_p, next_w, next_state = ret[0], ret[1], ret[2]

        next_state_lst += [next_state]

        if argmax:
            nw = next_p[0].argmax()
        else:
            nw = next_w[0]
        sample.append(nw)
        sample_score += next_p[0, nw]
        if nw == 0:
            break

    return sample, sample_score, np.vstack(next_state_lst)



# calculate the log probablities on a given corpus using language model
def pred_probs(f_log_probs, prepare_data, options, iterator, maxlen, verbose=True):
    probs = []

    n_done = 0

    for x in iterator:
        n_done += len(x)

        x, x_mask = prepare_data(x, maxlen=maxlen, n_words=50)
        avg_len = float(x_mask.sum(0).mean())
        if x is None:
            print 'Minibatch with zero sample under length, in pred_probs'
            continue

        bern_dist = numpy.random.binomial(1, .5, size=x.shape)
        uniform_sampling = numpy.random.uniform(size = x.flatten().shape[0])

        print x.shape
        if x.shape[1] != batch_size:
            continue

        pprobs = f_log_probs(x, x_mask, bern_dist.astype('float32'), uniform_sampling.astype('float32'))
        for pp in pprobs:
            probs.append(pp)

        if numpy.isnan(numpy.mean(probs)):
            ipdb.set_trace()

        if verbose:
            print >>sys.stderr, '%d samples computed' % (n_done)

    return numpy.array(probs), avg_len


