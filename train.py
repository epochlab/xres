#!/usr/bin/env python3

import os, math, random

import numpy as np

import tensorflow as tf
from tensorflow.keras.optimizers import Adam

from model.srgan import build_discriminator, build_generator
from model.edsr import build_edsr
from data import sample_data, rgb_mean
from loss import generator_loss, discriminator_loss, content_loss
from utils import generate_images, log_callback

print("Eager mode:", tf.executing_eagerly())

# -----------------------------

OUTDIR = 'metrics'

ROOT_0 = '/mnt/vanguard/datasets/vimeo_90k/toflow'
ROOT_1 = '/mnt/vanguard/datasets/ffhq-dataset/ffhq-512'
ROOT_2 = '/mnt/vanguard/datasets/celeba_bundle/data_hq_1024'

DIR_LIST = [ROOT_1, ROOT_2]

dataset = []
for ROOT in DIR_LIST:
    for path, subdirs, files in os.walk(ROOT):
        for name in sorted(files):
            filepath = os.path.join(path, name)
            dataset.append(filepath)
random.shuffle(dataset)

# -----------------------------

NETWORK = "EDSR"                                                                                # SRGAN | EDSR
RGB_MEAN = False                                                                                # Feature-wise RGB mean average

DELTA = 4                                                                                       # Scale factor (r)
IMAGE_SHAPE = (256, 256, 3)                                                                     # High Resolution Shape
DOWNSAMPLE_SHAPE = (IMAGE_SHAPE[0]//DELTA, IMAGE_SHAPE[1]//DELTA, IMAGE_SHAPE[2])               # Low Resolution Shape

BATCH_SIZE = 16
SPLIT_RATIO = 0.9
VALIDATION_SIZE = 100

RES_BLOCKS = 16
NUM_FILTERS = 64

EPOCHS = 300000

low_resolution_shape = DOWNSAMPLE_SHAPE
high_resolution_shape = IMAGE_SHAPE
print("Low Resolution Shape =", low_resolution_shape)
print("High Resolution Shape =", high_resolution_shape)

# -----------------------------

total_imgs = len(dataset)
split_index = int(math.floor(total_imgs) * SPLIT_RATIO)

n_train_imgs = dataset[:split_index]
n_test_imgs = dataset[split_index:-VALIDATION_SIZE]
n_val_imgs = dataset[total_imgs-VALIDATION_SIZE:]

train_ds_low, train_ds_high = sample_data(n_train_imgs, BATCH_SIZE, coco=True, rgb_mean=True)
test_ds_low, test_ds_high = sample_data(n_test_imgs, BATCH_SIZE, coco=False, rgb_mean=False)

if NETWORK == "SRGAN":
    generator = build_generator(low_resolution_shape)
if NETWORK == "EDSR":
    # generator = build_edsr(low_resolution_shape, DELTA, NUM_FILTERS, RES_BLOCKS)
    generator = build_edsr(low_resolution_shape, NUM_FILTERS, RES_BLOCKS)

discriminator = build_discriminator(high_resolution_shape)

generator_optimizer = Adam(0.0002, 0.5)
discriminator_optimizer = Adam(0.0002, 0.5)

if RGB_MEAN:
    mean_array = rgb_mean(IMAGE_SHAPE, dataset)

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

# -----------------------------

timestamp, summary_writer, checkpoint_prefix = log_callback(OUTDIR, generator, discriminator, generator_optimizer, discriminator_optimizer)
loss_min = 9999999

for epoch in range(EPOCHS):
    print("Epoch: ", epoch)

    test_ds_low, test_ds_high = sample_data(n_test_imgs, BATCH_SIZE, coco=False, rgb_mean=False)
    train_ds_low, train_ds_high = sample_data(n_train_imgs, BATCH_SIZE, coco=True, rgb_mean=RGB_MEAN)

    generate_images(generator, test_ds_low, test_ds_high)

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
