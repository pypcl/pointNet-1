import tensorflow as tf
tfgan = tf.contrib.gan
layers = tf.contrib.layers
import numpy as np
import argparse
import provider
import importlib
import h5py
import os
import sys
from tensorflow.python.ops import variable_scope
from tensorflow.contrib.gan.python.losses.python import tuple_losses_impl as tfgan_losses
from tensorflow.contrib.data import Dataset, Iterator

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
sys.path.append(os.path.join(BASE_DIR, 'models'))
sys.path.append(os.path.join(BASE_DIR, 'utils'))
import tf_util
from transform_nets import input_transform_net, feature_transform_net

parser = argparse.ArgumentParser()
parser.add_argument('--gpu', type=int, default=0, help='GPU to use [default: GPU 0]')
parser.add_argument('--model', default='pointnet_cls', help='Model name: pointnet_cls or pointnet_cls_basic [default: pointnet_cls]')
parser.add_argument('--log_dir', default='log', help='Log dir [default: log]')
parser.add_argument('--num_point', type=int, default=1024, help='Point Number [256/512/1024/2048] [default: 1024]')
parser.add_argument('--max_epoch', type=int, default=250, help='Epoch to run [default: 250]')
parser.add_argument('--batch_size', type=int, default=32, help='Batch Size during training [default: 32]')
parser.add_argument('--learning_rate', type=float, default=0.00001, help='Initial learning rate [default: 0.001]')
parser.add_argument('--momentum', type=float, default=0.9, help='Initial learning rate [default: 0.9]')
parser.add_argument('--optimizer', default='adam', help='adam or momentum [default: adam]')
parser.add_argument('--decay_step', type=int, default=200000, help='Decay step for lr decay [default: 200000]')
parser.add_argument('--decay_rate', type=float, default=0.7, help='Decay rate for lr decay [default: 0.8]')
FLAGS = parser.parse_args()
FLAGS.noise_dims = 128
FLAGS.max_number_of_steps = 120

MAX_NUM_POINT = 2048
NUM_CLASSES = 40
DECAY_STEP = FLAGS.decay_step
BATCH_SIZE = FLAGS.batch_size
BASE_LEARNING_RATE = FLAGS.learning_rate
NUM_POINT = FLAGS.num_point
BN_INIT_DECAY = 0.5
BN_DECAY_DECAY_RATE = 0.5
BN_DECAY_DECAY_STEP = float(DECAY_STEP)
BN_DECAY_CLIP = 0.99
DECAY_STEP = FLAGS.decay_step
DECAY_RATE = FLAGS.decay_rate

MODEL = importlib.import_module(FLAGS.model)

LOG_DIR = './log/gan_log'
LOG_FOUT = open(os.path.join('./log/gan_log', 'log_train.txt'), 'w')
LOG_FOUT.write(str(FLAGS)+'\n')
def log_string(out_str):
    LOG_FOUT.write(out_str+'\n')
    LOG_FOUT.flush()
    print(out_str)

def get_learning_rateG(batch):
    learning_rate = tf.train.exponential_decay(
                        BASE_LEARNING_RATE,  # Base learning rate.
                        batch * BATCH_SIZE,  # Current index into the dataset.
                        DECAY_STEP,          # Decay step.
                        DECAY_RATE,          # Decay rate.
                        staircase=True)
    learning_rate = tf.maximum(learning_rate, 0.000001) # CLIP THE LEARNING RATE!
    return learning_rate

def get_learning_rateD(batch):
    learning_rate = tf.train.exponential_decay(
                        BASE_LEARNING_RATE,  # Base learning rate.
                        batch * BATCH_SIZE,  # Current index into the dataset.
                        DECAY_STEP,          # Decay step.
                        DECAY_RATE,          # Decay rate.
                        staircase=True)
    learning_rate = tf.maximum(learning_rate, 0.00000001) # CLIP THE LEARNING RATE!
    return learning_rate

def provide_data():
    BATCH_SIZE = FLAGS.batch_size
    current_data, current_label = provider.loadDataFile('./data/modelnet40_ply_hdf5_2048/train_all.h5')
    current_data, current_label, _ = provider.shuffle_data(current_data, np.squeeze(current_label))
    current_label = np.squeeze(current_label)


    file_size = current_data.shape[0]
    num_batches = file_size // BATCH_SIZE

    for batch_idx in range(num_batches):
        start_idx = batch_idx * BATCH_SIZE
        end_idx = (batch_idx + 1) * BATCH_SIZE

        # mantipulation data
        rotated_data = provider.rotate_point_cloud(current_data[start_idx:end_idx, :, :])
        jittered_data = provider.jitter_point_cloud(rotated_data)
        # mantipulate labe
        one_hot_labe = np.zeros((BATCH_SIZE, 41))
        one_hot_labe[np.arange(BATCH_SIZE), current_label[start_idx:end_idx]] = 1

        #out['data'] = jittered_data
        #out['labe'] = one_hot_labe
        yield jittered_data, one_hot_labe, current_label[start_idx:end_idx]

def get_model(point_cloud, is_training, one_hot_labels, bn_decay=None,):
    """ Classification PointNet, input is BxNx3, output Bx40 """
    batch_size = point_cloud.get_shape()[0].value
    num_point = point_cloud.get_shape()[1].value
    end_points = {}

    with tf.variable_scope('transform_net1', reuse=tf.AUTO_REUSE) as sc:
        transform = input_transform_net(point_cloud, is_training, bn_decay, K=3)

    point_cloud_transformed = tf.matmul(point_cloud, transform)
    input_image = tf.expand_dims(point_cloud_transformed, -1)

    net = tf_util.conv2d(input_image, 64, [1,3],
                         padding='VALID', stride=[1,1],
                         bn=True, is_training=is_training,
                         scope='conv1', bn_decay=bn_decay)
    net = tf_util.conv2d(net, 64, [1,1],
                         padding='VALID', stride=[1,1],
                         bn=True, is_training=is_training,
                         scope='conv2', bn_decay=bn_decay)

    with tf.variable_scope('transform_net2', reuse=tf.AUTO_REUSE) as sc:
        transform = feature_transform_net(net, is_training, bn_decay, K=64)

    end_points['transform'] = transform
    net_transformed = tf.matmul(tf.squeeze(net, axis=[2]), transform)
    net_transformed = tf.expand_dims(net_transformed, [2])

    net = tf_util.conv2d(net_transformed, 64, [1,1],
                         padding='VALID', stride=[1,1],
                         bn=True, is_training=is_training,
                         scope='conv3', bn_decay=bn_decay)
    net = tf_util.conv2d(net, 128, [1,1],
                         padding='VALID', stride=[1,1],
                         bn=True, is_training=is_training,
                         scope='conv4', bn_decay=bn_decay)
    net = tf_util.conv2d(net, 1024, [1,1],
                         padding='VALID', stride=[1,1],
                         bn=True, is_training=is_training,
                         scope='conv5', bn_decay=bn_decay)

    # Symmetric function: max pooling
    net = tf_util.max_pool2d(net, [num_point,1],
                             padding='VALID', scope='maxpool')

    net = tf.reshape(net, [batch_size, -1])
    net = tf_util.fully_connected(net, 512, bn=True, is_training=is_training,
                                  scope='fc1', bn_decay=bn_decay)
    net = tf_util.dropout(net, keep_prob=0.7, is_training=is_training,
                          scope='dp1')
    net = tf_util.fully_connected(net, 256, bn=True, is_training=is_training,
                                  scope='fc2', bn_decay=bn_decay)
    net = tf_util.dropout(net, keep_prob=0.7, is_training=is_training,
                          scope='dp2')
    net = tf_util.fully_connected(net, 41, activation_fn=None, scope='fc3')

    return net, end_points

def conditional_discriminator(point_clouds, one_hot_labels):
    is_training_pl = tf.constant([True])
    # Get model and loss
    #with tf.variable_scope('Discriminator', reuse=tf.AUTO_REUSE):
    pred, end_points = get_model(point_clouds, tf.squeeze(is_training_pl), one_hot_labels)
    return pred, end_points


def generate_cloud(feature, noise):
    feature = tf.concat([feature, feature], axis=1)#2
    feature = tf.concat([feature, feature], axis=1)#4
    feature = tf.concat([feature, feature], axis=1)#8
    feature = tf.concat([feature, feature], axis=1)#16
    feature = tf.concat([feature, feature], axis=1)#32
    feature = tf.concat([feature, feature], axis=1)#64
    feature = tf.concat([feature, feature], axis=1)#128
    feature = tf.concat([feature, feature], axis=1)#256
    feature = tf.concat([feature, feature], axis=1)#512
    feature = tf.concat([feature, feature], axis=1)#1024

    #noise = tf.concat([noise, noise], axis=1)#2
    #noise = tf.concat([noise, noise], axis=1)#4
    #noise = tf.concat([noise, noise], axis=1)#8
    #noise = tf.concat([noise, noise], axis=1)#16
    #noise = tf.concat([noise, noise], axis=1)#32
    #noise = tf.concat([noise, noise], axis=1)#64
    #noise = tf.concat([noise, noise], axis=1)#128
    #noise = tf.concat([noise, noise], axis=1)#256
    #noise = tf.concat([noise, noise], axis=1)#512
    #noise = tf.concat([noise, noise], axis=1)#1024

    feature = tf.concat([feature, noise], axis=2)
    point = layers.fully_connected(feature, 256)
    point = layers.fully_connected(point, 64)
    point = layers.fully_connected(point, 32)
    point = layers.fully_connected(point, 16, activation_fn=tf.nn.softsign)
    point = layers.fully_connected(point, 3, activation_fn=tf.nn.softsign)

    return point


def conditional_generator(inputs):
    noise, cloud_labels = inputs
    #with tf.variable_scope('Generator', reuse=tf.AUTO_REUSE):
    with tf.contrib.framework.arg_scope(
            [layers.fully_connected, layers.conv2d_transpose],
            activation_fn=tf.nn.relu, normalizer_fn=layers.batch_norm,
            weights_regularizer=layers.l2_regularizer(2.5e-5)):

        net = layers.fully_connected(noise, 256)
        net = tfgan.features.condition_tensor_from_onehot(net, cloud_labels)
        net = layers.fully_connected(net, 512)
        feature = layers.fully_connected(net, 1024)

    noise2 = tf.random_normal([32, 1024, 128])
    cloud = generate_cloud(tf.expand_dims(feature, axis=1), noise2)

    return cloud

def density_penalty_for_one(G_output):
    shuffled = tf.random_shuffle(G_output)
    stoped_shuffled = tf.stop_gradient(shuffled)
    return tf.norm(stoped_shuffled-G_output)

def density_penalty(G_output):
    #with tf.variable_scope('NO_TRAINING'):
    loss = tf.constant([0.0])
    for i in range(BATCH_SIZE):
        loss += density_penalty_for_one(G_output[i, :, :])
    return loss


######################################### main #############################################
######################################### main #############################################
######################################### main #############################################
######################################### main #############################################
######################################### main #############################################
def train():
    ## global steps
    stepsG = tf.Variable(0)
    stepsD = tf.Variable(0)

    ## setup input data
    point_cloudsG = tf.placeholder(dtype=tf.float32, shape=(BATCH_SIZE, 1024, 3))
    cloud_labelsG = tf.placeholder(dtype=tf.float32, shape=(BATCH_SIZE, 41))
    point_cloudsD = tf.placeholder(dtype=tf.float32, shape=(BATCH_SIZE, 1024, 3))
    cloud_labelsD = tf.placeholder(dtype=tf.float32, shape=(BATCH_SIZE, 41))
    noise = tf.placeholder(dtype=tf.float32, shape=(FLAGS.batch_size, FLAGS.noise_dims))
    gt_trainD = tf.placeholder(tf.int32, shape=(2*FLAGS.batch_size))
    gt_trainG = tf.placeholder(tf.int32, shape=(FLAGS.batch_size))
    D_input = tf.placeholder(dtype=tf.float32, shape=(BATCH_SIZE, 1024, 3))

    with tf.variable_scope('Generator'):
        G_input = noise, cloud_labelsG
        G_output = conditional_generator(G_input)
    with tf.variable_scope('Discriminator'):
        D_output = conditional_discriminator(D_input)











if __name__ == "__main__":
    train()
    # LOG_FOUT.close()