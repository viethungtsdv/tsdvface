import os
import numpy as np
import facenet

import tensorflow as tf
import numpy as np
import argparse
import facenet
import lfw
import os
import sys
from tensorflow.python.ops import data_flow_ops
from sklearn import metrics
from scipy.optimize import brentq
from scipy import interpolate

os.environ["CUDA_VISIBLE_DEVICES"] = "1"

def evaluate(embeddings, actual_issame, nrof_folds=10, distance_metric=0, subtract_mean=False):
    # Calculate evaluation metrics
    thresholds = np.arange(0, 4, 0.01)
    embeddings1 = embeddings[0::2]
    embeddings2 = embeddings[1::2]
    
    tpr, fpr, accuracy = facenet.calculate_roc(thresholds, embeddings1, embeddings2,
                      np.asarray(actual_issame), nrof_folds=nrof_folds, distance_metric=distance_metric, subtract_mean=subtract_mean)
    
    thresholds = np.arange(0, 4, 0.01)
    val, val_std, far = facenet.calculate_val(thresholds, embeddings1, embeddings2, np.asarray(actual_issame),
                     1e-3, nrof_folds=nrof_folds, distance_metric=distance_metric, subtract_mean=subtract_mean)
    
    return tpr, fpr, accuracy, val, val_std, far

def get_paths(tsdvface_dir, pairs):
    nrof_skipped_pairs = 0
    path_list = []
    issame_list = []
    
    for pair in pairs:
        if len(pair) == 3:    # make all matches
            path0 = add_extension(os.path.join(tsdvface_dir, pair[0], pair[1]))
            path1 = add_extension(os.path.join(tsdvface_dir, pair[0], pair[2]))
            issame = True
        elif len(pair) == 4:    # make all mismatches
            path0 = add_extension(os.path.join(tsdvface_dir, pair[0], pair[1]))
            path1 = add_extension(os.path.join(tsdvface_dir, pair[2], pair[3]))
            issame = False
        if os.path.exists(path0) and os.path.exists(path1):   # Only add the pair if both paths exist
            path_list += (path0, path1)
            issame_list.append(issame)
        else:
            nrof_skipped_pairs += 1
    if nrof_skipped_pairs > 0:
        print('Skipped %d image pairs' % nrof_skipped_pairs)
        
    return path_list, issame_list

def add_extension(path):
    if os.path.exists(path + '.jpg'):
        return path + '.jpg'
    elif os.path.exists(path + '.png'):
        return path + '.png'
    else:
        raise RuntimeError('No file "%s" with extension png or jpg.' % path)
        
def read_pairs(pairs_filename):
    pairs = []
    with open(pairs_filename, 'r') as f:
        for line in f.readlines()[0:]:
            pair = line.strip().split()
            pairs.append(pair)
    return np.array(pairs)
    
def evaluate_tsdvface(sess, enqueue_op, image_paths_placeholder, labels_placeholder, phase_train_placeholder, batch_size_placeholder, control_placeholder,
        embeddings, labels, image_paths, actual_issame, batch_size, nrof_folds, distance_metric, subtract_mean, use_flipped_images, use_fixed_image_standardization):
    # Run forward pass to calculate embeddings
    print('Runnning forward pass on TSDV face Dataset (TSDVFace) images')
    
    # Enqueue one epoch of image paths and labels
    nrof_embeddings = len(actual_issame)*2  # nrof_pairs * nrof_images_per_pair
    nrof_flips = 2 if use_flipped_images else 1
    nrof_images = nrof_embeddings * nrof_flips
    labels_array = np.expand_dims(np.arange(0,nrof_images),1)
    image_paths_array = np.expand_dims(np.repeat(np.array(image_paths),nrof_flips),1)
    control_array = np.zeros_like(labels_array, np.int32)
    if use_fixed_image_standardization:
        control_array += np.ones_like(labels_array)*facenet.FIXED_STANDARDIZATION
    if use_flipped_images:
        # Flip every second image
        control_array += (labels_array % 2)*facenet.FLIP
    sess.run(enqueue_op, {image_paths_placeholder: image_paths_array, labels_placeholder: labels_array, control_placeholder: control_array})
    
    embedding_size = int(embeddings.get_shape()[1])
    print (nrof_embeddings, nrof_flips)
    print (nrof_images, batch_size)
    assert nrof_images % batch_size == 0, 'The number of TSDVFace images must be an integer multiple of the TSDVFace batch size'
    nrof_batches = nrof_images // batch_size
    emb_array = np.zeros((nrof_images, embedding_size))
    lab_array = np.zeros((nrof_images,))
    for i in range(nrof_batches):
        feed_dict = {phase_train_placeholder:False, batch_size_placeholder:batch_size}
        emb, lab = sess.run([embeddings, labels], feed_dict=feed_dict)
        lab_array[lab] = lab
        emb_array[lab, :] = emb
        if i % 10 == 9:
#             print('.', end='')
            sys.stdout.flush()
    print('')
    embeddings = np.zeros((nrof_embeddings, embedding_size*nrof_flips))
    if use_flipped_images:
        # Concatenate embeddings for flipped and non flipped version of the images
        embeddings[:,:embedding_size] = emb_array[0::2,:]
        embeddings[:,embedding_size:] = emb_array[1::2,:]
    else:
        embeddings = emb_array

    assert np.array_equal(lab_array, np.arange(nrof_images))==True, 'Wrong labels used for evaluation, possibly caused by training examples left in the input pipeline'
    tpr, fpr, accuracy, val, val_std, far = evaluate(embeddings, actual_issame, nrof_folds=nrof_folds, distance_metric=distance_metric, subtract_mean=subtract_mean)
    
    print('Accuracy: %2.5f+-%2.5f' % (np.mean(accuracy), np.std(accuracy)))
    print('Validation rate: %2.5f+-%2.5f @ FAR=%2.5f' % (val, val_std, far))
    
    auc = metrics.auc(fpr, tpr)
    print('Area Under Curve (AUC): %1.3f' % auc)
#     eer = brentq(lambda x: 1. - x - interpolate.interp1d(fpr, tpr)(x), 0., 1.)
#     print('Equal Error Rate (EER): %1.3f' % eer)
    
ACD_PAIRS = './pairs_tsdvface_20180919.txt'    # The file containing the pairs to use for validation.
ACD_DIR = '/home/hungnv/master/src/facenet/datasets/tsdv_face_mtcnnpy_160/' # Path to the data directory containing aligned LFW face patches.
IMAGE_SIZE = 160    # Image size (height, width) in pixels.
ACD_BATCH_SIZE = 100    # Number of images to process in a batch in the TSDV face test set.
ACD_NROF_FOLDS = 10    # Number of folds to use for cross validation. Mainly used for testing.
DISTANCE_METRIC = 1    # Distance metric  0:euclidian, 1:cosine similarity.
SUBTRACT_MEAN = 'store_true'    # Subtract feature mean before calculating distance.
USE_FLIPPED_IMAGES = 'store_true'    # Concatenates embeddings for the image and its horizontally flipped counterpart.
USE_FIXED_STD = 'store_true'
MODEL_DIR = '/home/hungnv/master/src/Face_Recognition-master/lib/src/ckpt/20180408-102900/'
def main():
    with tf.Graph().as_default():
        with tf.Session() as sess:
            # Read the file containing the pairs used for testing
            pairs = read_pairs(os.path.expanduser(ACD_PAIRS))
            
            # Get the paths for the corresponding images
            paths, actual_issame = get_paths(os.path.expanduser(ACD_DIR), pairs)
            
            image_paths_placeholder = tf.placeholder(tf.string, shape=(None,1), name='image_paths')
            labels_placeholder = tf.placeholder(tf.int32, shape=(None,1), name='labels')
            batch_size_placeholder = tf.placeholder(tf.int32, name='batch_size')
            control_placeholder = tf.placeholder(tf.int32, shape=(None,1), name='control')
            phase_train_placeholder = tf.placeholder(tf.bool, name='phase_train')
            
            nrof_preprocess_threads = 4
            image_size = (IMAGE_SIZE, IMAGE_SIZE)
            eval_input_queue = data_flow_ops.FIFOQueue(capacity=2000000,
                                        dtypes=[tf.string, tf.int32, tf.int32],
                                        shapes=[(1,), (1,), (1,)],
                                        shared_name=None, name=None)
            eval_enqueue_op = eval_input_queue.enqueue_many([image_paths_placeholder, labels_placeholder, control_placeholder], name='eval_enqueue_op')
            image_batch, label_batch = facenet.create_input_pipeline(eval_input_queue, image_size, nrof_preprocess_threads, batch_size_placeholder)
            
            # Load the model
            input_map = {'image_batch': image_batch, 'label_batch': label_batch, 'phase_train': phase_train_placeholder}
            facenet.load_model(MODEL_DIR, input_map=input_map)
            
            # Get output tensor
            embeddings = tf.get_default_graph().get_tensor_by_name("embeddings:0")
            
            coord = tf.train.Coordinator()
            tf.train.start_queue_runners(coord=coord, sess=sess)

            evaluate_tsdvface(sess, eval_enqueue_op, image_paths_placeholder, labels_placeholder, phase_train_placeholder, batch_size_placeholder, control_placeholder,
                embeddings, label_batch, paths, actual_issame, ACD_BATCH_SIZE, ACD_NROF_FOLDS, DISTANCE_METRIC, SUBTRACT_MEAN,
                USE_FLIPPED_IMAGES, USE_FIXED_STD)
