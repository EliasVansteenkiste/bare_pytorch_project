import cPickle as pickle
import string
import sys
import time
from itertools import izip
import lasagne as nn
import numpy as np
import theano
from datetime import datetime, timedelta
import theano.tensor as T

import buffering
import utils
import logger
from configuration import config, set_configuration
import pathfinder
import app

nn.random.set_rng(np.random.RandomState(317070))
theano.config.warn_float64 = 'raise'

if len(sys.argv) < 2:
    sys.exit("Usage: train.py <configuration_name>")

config_name = sys.argv[1]
set_configuration('configs', config_name)
expid = utils.generate_expid(config_name)
print
print "Experiment ID: %s" % expid
print

# metadata
metadata_dir = utils.get_dir_path('models', pathfinder.METADATA_PATH)
metadata_path = metadata_dir + '/%s.pkl' % expid

# logs
logs_dir = utils.get_dir_path('logs', pathfinder.METADATA_PATH)
sys.stdout = logger.Logger(logs_dir + '/%s.log' % expid)
sys.stderr = sys.stdout

print 'Build model'
model = config().build_model()
all_layers = nn.layers.get_all_layers(model.l_out)
all_params = nn.layers.get_all_params(model.l_out)
num_params = nn.layers.count_params(model.l_out)
print '  number of parameters: %d' % num_params
print string.ljust('  layer output shapes:', 36),
print string.ljust('#params:', 10),
print 'output shape:'
for layer in all_layers:
    name = string.ljust(layer.__class__.__name__, 32)
    num_param = sum([np.prod(p.get_value().shape) for p in layer.get_params()])
    num_param = string.ljust(num_param.__str__(), 10)
    print '    %s %s %s' % (name, num_param, layer.output_shape)

train_loss = config().build_objective(model, deterministic=False)
train_loss2 = config().build_objective2(model, deterministic=False)
valid_loss = config().build_objective(model, deterministic=True)
valid_loss2 = config().build_objective2(model, deterministic=True)

learning_rate_schedule = config().learning_rate_schedule
learning_rate = theano.shared(np.float32(learning_rate_schedule[0]))
updates = config().build_updates(train_loss, model, learning_rate)

x_shared = nn.utils.shared_empty(dim=len(model.l_in.shape))
y_shared = nn.utils.shared_empty(dim=len(model.l_target.shape))

idx = T.lscalar('idx')
givens_train = {}
givens_train[model.l_in.input_var] = x_shared[idx * config().batch_size:(idx + 1) * config().batch_size]
givens_train[model.l_target.input_var] = y_shared[idx * config().batch_size:(idx + 1) * config().batch_size]

givens_valid = {}
givens_valid[model.l_in.input_var] = x_shared
givens_valid[model.l_target.input_var] = y_shared

# theano functions
iter_train = theano.function([idx], [train_loss, train_loss2, nn.layers.get_output(model.l_out)], givens=givens_train, updates=updates)
iter_validate = theano.function([], [valid_loss, valid_loss2, nn.layers.get_output(model.l_out)], givens=givens_valid)

if config().restart_from_save:
    print 'Load model parameters for resuming'
    resume_metadata = utils.load_pkl(config().restart_from_save)
    nn.layers.set_all_param_values(model.l_out, resume_metadata['param_values'])
    start_chunk_idx = resume_metadata['chunks_since_start'] + 1
    chunk_idxs = range(start_chunk_idx, config().max_nchunks)

    lr = np.float32(utils.current_learning_rate(learning_rate_schedule, start_chunk_idx))
    print '  setting learning rate to %.7f' % lr
    learning_rate.set_value(lr)
    losses_eval_train = resume_metadata['losses_eval_train']
    losses_eval_valid = resume_metadata['losses_eval_valid']
    losses_eval_train2 = resume_metadata['losses_eval_train2']
    losses_eval_valid2 = resume_metadata['losses_eval_valid2']
else:
    chunk_idxs = range(config().max_nchunks)
    losses_eval_train = []
    losses_eval_valid = []
    losses_eval_train2 = []
    losses_eval_valid2 = []
    start_chunk_idx = 0

train_data_iterator = config().train_data_iterator
valid_data_iterator = config().valid_data_iterator

print
print 'Data'
print 'n train: %d' % train_data_iterator.nsamples
print 'n validation: %d' % valid_data_iterator.nsamples
print 'n chunks per epoch', config().nchunks_per_epoch

print
print 'Train model'
chunk_idx = 0
start_time = time.time()
prev_time = start_time

tmp_preds = []
tmp_gts = []

tmp_losses_train = []
tmp_losses_train2 = []
tmp_preds_train = []
tmp_gts_train = []

losses_train_print = []
losses_train_print2 = []
preds_train_print = []
gts_train_print = []
losses_time_print = []

# use buffering.buffered_gen_threaded()
for chunk_idx, (x_chunk_train, y_chunk_train, id_train) in izip(chunk_idxs, buffering.buffered_gen_threaded(
        train_data_iterator.generate(), buffer_size=128)):
    if chunk_idx in learning_rate_schedule:
        lr = np.float32(learning_rate_schedule[chunk_idx])
        print '  setting learning rate to %.7f' % lr
        print
        learning_rate.set_value(lr)

    # load chunk to GPU
    x_shared.set_value(x_chunk_train)
    y_shared.set_value(y_chunk_train)

    for gt in y_chunk_train:
        tmp_gts.append(gt)
        tmp_gts_train.append(gt)
        gts_train_print.append(gt)

    #print 'y_chunk_train.shape', y_chunk_train.shape

    # make nbatches_chunk iterations
    for b in xrange(config().nbatches_chunk):
        losses_time_print.append(time.time())
        loss, loss2, pred = iter_train(b)
        if np.isnan(pred).any():
            print 'nan in pred'
            print 'loss', loss
            print 'loss2', loss2
            print 'pred', pred 
            print 'y_chunk_train', y_chunk_train
            raise 
        elif np.isnan(loss).any():
            print 'nan in loss'
            print 'loss', loss
            print 'loss2', loss2
            print 'pred', pred 
            print 'y_chunk_train', y_chunk_train
            raise 
        elif np.isnan(loss2).any():
            print 'nan in loss2'
            print 'loss', loss
            print 'loss2', loss2
            print 'pred', pred 
            print 'y_chunk_train', y_chunk_train
            raise 
        # else:
        #     print 'loss', loss
        #     print 'loss2', loss2
        #     print 'pred', pred 

        #print loss, pred
        for pr in pred:
            tmp_preds.append(pr)
            tmp_preds_train.append(pr)
            preds_train_print.append(pr)

        tmp_losses_train.append(loss)
        tmp_losses_train2.append(loss2)
        losses_train_print.append(loss)
        losses_train_print2.append(loss2)

    if (chunk_idx + 1) % 10 == 0:
        print 'Chunk %d/%d %.1fHz' % (chunk_idx + 1, config().max_nchunks,10.*config().nbatches_chunk * config().batch_size/(time.time()-losses_time_print[0]) ), 
        print np.mean(losses_train_print), np.mean(losses_train_print2),
        print 'score', config().score(gts_train_print, preds_train_print)
        preds_train_print = []
        gts_train_print = []
        losses_train_print = []
        losses_time_print = []
        losses_train_print2 = []
        losses_time_print2 = []

    if ((chunk_idx + 1) % config().validate_every) == 0:
        print
        print 'Chunk %d/%d' % (chunk_idx + 1, config().max_nchunks)
        # calculate mean train loss since the last validation phase
        mean_train_loss = np.mean(tmp_losses_train)
        mean_train_loss2 = np.mean(tmp_losses_train2)
        mean_train_score = np.mean(config().score(tmp_gts_train, tmp_preds_train))
        print 'Mean train loss: %7f' % mean_train_loss, mean_train_loss2, mean_train_score 
        losses_eval_train.append(mean_train_loss)
        losses_eval_train2.append(mean_train_loss2)
        tmp_losses_train = []
        tmp_losses_train2 = []
        tmp_preds_train = []
        tmp_gts_train = []

        # load validation data to GPU

        tmp_losses_valid = []
        tmp_losses_valid2 = []
        tmp_preds_valid = []
        tmp_gts_valid = []
        for i, (x_chunk_valid, y_chunk_valid, ids_batch) in enumerate(
                buffering.buffered_gen_threaded(valid_data_iterator.generate(),
                                                buffer_size=2)):
            x_shared.set_value(x_chunk_valid)
            y_shared.set_value(y_chunk_valid)
            l_valid, l_valid2, pred = iter_validate()
            for gt in y_chunk_valid:
                tmp_gts_valid.append(gt)
            for pr in pred:
                tmp_preds_valid.append(pr)
            #print i, l_valid, l_valid2
            tmp_losses_valid.append(l_valid)
            tmp_losses_valid2.append(l_valid2)


        # calculate validation loss across validation set
        valid_loss = np.mean(tmp_losses_valid)
        valid_loss2 = np.mean(tmp_losses_valid2)
        valid_score = np.mean(config().test_score(tmp_gts_valid, tmp_preds_valid))
        print 'Validation loss: ', valid_loss, valid_loss2, valid_score
        losses_eval_valid.append(valid_loss)
        losses_eval_valid2.append(valid_loss2)

        now = time.time()
        time_since_start = now - start_time
        time_since_prev = now - prev_time
        prev_time = now
        est_time_left = time_since_start * (config().max_nchunks - chunk_idx + 1.) / (chunk_idx + 1. - start_chunk_idx)
        eta = datetime.now() + timedelta(seconds=est_time_left)
        eta_str = eta.strftime("%c")
        print "  %s since start (%.2f s)" % (utils.hms(time_since_start), time_since_prev)
        print "  estimated %s to go (ETA: %s)" % (utils.hms(est_time_left), eta_str)
        print

    if ((chunk_idx + 1) % config().save_every) == 0:
        print
        print 'Chunk %d/%d' % (chunk_idx + 1, config().max_nchunks)
        print 'Saving metadata, parameters'

        with open(metadata_path, 'w') as f:
            pickle.dump({
                'configuration_file': config_name,
                'git_revision_hash': utils.get_git_revision_hash(),
                'experiment_id': expid,
                'chunks_since_start': chunk_idx,
                'losses_eval_train': losses_eval_train,
                'losses_eval_valid': losses_eval_valid,
                'losses_eval_train2': losses_eval_train2,
                'losses_eval_valid2': losses_eval_valid2,
                'param_values': nn.layers.get_all_param_values(model.l_out)
            }, f, pickle.HIGHEST_PROTOCOL)
            print '  saved to %s' % metadata_path
            print

