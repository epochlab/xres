import os, math, random, datetime

from PIL import Image

import numpy as np
import matplotlib.pyplot as plt

import tensorflow as tf

from tensorflow.keras.callbacks import TensorBoard
from tensorflow.keras.optimizers import Adam

from model import build_discriminator, build_generator, build_vgg
from data import sample_data
from loss import generator_loss, discriminator_loss
from utils import generate_images

physical_devices = tf.config.experimental.list_physical_devices("GPU")
tf.config.experimental.set_memory_growth(physical_devices[0], True)

print("Version:", tf.__version__)
print("Eager mode:", tf.executing_eagerly())
print("GPU is", "available" if physical_devices else "NOT AVAILABLE")

OUTDIR = 'metrics'

ROOT_0 = '/mnt/vanguard/datasets/vimeo_90k/toflow'
ROOT_1 = '/mnt/vanguard/datasets/ffhq-dataset/ffhq-512'
ROOT_2 = '/mnt/vanguard/datasets/celeba_bundle/data_hq_1024'

dir_list = [ROOT_1, ROOT_2]

dataset = []

for ROOT in dir_list:
    print(ROOT)
    for path, subdirs, files in os.walk(ROOT):
        for name in sorted(files):
            filepath = os.path.join(path, name)
            dataset.append(filepath)

random.shuffle(dataset)

r = 4

image_shape = (256, 256, 3)
downsample_shape = (image_shape[0]//r, image_shape[1]//r, image_shape[2])

low_resolution_shape = downsample_shape
high_resolution_shape = image_shape

print("Low Resolution Shape =", low_resolution_shape)
print("High Resolution Shape =", high_resolution_shape)

BATCH_SIZE = 16
SPLIT_RATIO = 0.9
VALIDATION_SIZE = 100

total_imgs = len(dataset)
split_index = int(math.floor(total_imgs) * SPLIT_RATIO)

n_train_imgs = dataset[:split_index]
n_test_imgs = dataset[split_index:-VALIDATION_SIZE]
n_val_imgs = dataset[total_imgs-VALIDATION_SIZE:]

train_ds_low, train_ds_high = sample_data(n_train_imgs, BATCH_SIZE, coco=True, rgb_mean=True)
print("train_ds_low.shape = {}".format(train_ds_low.shape))
print("train_ds_high.shape = {}".format(train_ds_high.shape))

test_ds_low, test_ds_high = sample_data(n_test_imgs, BATCH_SIZE, coco=False, rgb_mean=False)
print("test_ds_low.shape = {}".format(test_ds_low.shape))
print("test_ds_high.shape = {}".format(test_ds_high.shape))

generator = build_generator()
discriminator = build_discriminator()
vgg = build_vgg()

generator_optimizer = Adam(0.0002, 0.5)
discriminator_optimizer = Adam(0.0002, 0.5)

mean_squared_error = tf.keras.losses.MeanSquaredError()

@tf.function
def content_loss(hr, sr):
    sr = tf.keras.applications.vgg19.preprocess_input(sr)
    hr = tf.keras.applications.vgg19.preprocess_input(hr)
    sr_features = vgg(sr) / 12.75
    hr_features = vgg(hr) / 12.75
    return mean_squared_error(hr_features, sr_features)

@tf.function
def train_step(lr, hr):
    with tf.GradientTape() as gen_tape, tf.GradientTape() as disc_tape:
        # Forward pass
        sr = generator(lr, training=True)
        hr_output = discriminator(hr, training=True)
        sr_output = discriminator(sr, training=True)

        # Compute losses
        con_loss = content_loss(hr, sr)
        gen_loss = generator_loss(sr_output)
        perc_loss = con_loss + 0.001 * gen_loss
        disc_loss = discriminator_loss(hr_output, sr_output)

    # Compute gradients
    generator_gradients = gen_tape.gradient(perc_loss, generator.trainable_variables)
    discriminator_gradients = disc_tape.gradient(disc_loss, discriminator.trainable_variables)

    # Update weights
    generator_optimizer.apply_gradients(zip(generator_gradients, generator.trainable_variables))
    discriminator_optimizer.apply_gradients(zip(discriminator_gradients, discriminator.trainable_variables))

    return con_loss, gen_loss, perc_loss, disc_loss

timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
print("ID:", timestamp)

log_dir = OUTDIR + "/logs"
summary_writer = tf.summary.create_file_writer(log_dir + "/fit/" + timestamp)

checkpoint_dir = OUTDIR + '/training_checkpoints/'
checkpoint_prefix = os.path.join(checkpoint_dir, "ckpt")
checkpoint = tf.train.Checkpoint(generator_optimizer=generator_optimizer,
                                 discriminator_optimizer=discriminator_optimizer,
                                 generator=generator,
                                 discriminator=discriminator)

# checkpoint.restore(tf.train.latest_checkpoint(checkpoint_dir))

command = "tensorboard --logdir=" + str(log_dir) + " --port=6006 &"
os.system(command)

tensorboard_callback = TensorBoard(log_dir=log_dir, histogram_freq=1)

EPOCHS = 1000000
loss_min = 9999999

for epoch in range(EPOCHS):

    test_ds_low, test_ds_high = sample_data(n_test_imgs, BATCH_SIZE, coco=False, rgb_mean=False)
    train_ds_low, train_ds_high = sample_data(n_train_imgs, BATCH_SIZE, coco=True, rgb_mean=True)

    generate_images(generator, test_ds_low, test_ds_high)

    print("Epoch: ", epoch)

    # Train
    for i in range(BATCH_SIZE):
        print('.', end='')
        if (i+1) % 100 == 0:
            print()

        lr = tf.expand_dims(train_ds_low[i], axis=0)
        hr = tf.expand_dims(train_ds_high[i], axis=0)

        con_loss, gen_loss, perc_loss, disc_loss = train_step(lr, hr)

        with summary_writer.as_default():
            tf.summary.scalar('con_loss', con_loss, step=epoch)
            tf.summary.scalar('gen_loss', gen_loss, step=epoch)
            tf.summary.scalar('perc_loss', perc_loss, step=epoch)
            tf.summary.scalar('disc_loss', disc_loss, step=epoch)

    if perc_loss < loss_min:
        generator.save(OUTDIR + "/results/generator_" + timestamp + '.h5')
        print(" Model saved")
        loss_min = perc_loss

    if (epoch + 1) % 10000 == 0:
        checkpoint.save(file_prefix = checkpoint_prefix)
