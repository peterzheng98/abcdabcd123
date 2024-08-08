import docker
import os
import uuid
import logging
import sys
import time
import queue
import multiprocessing
import re
import subprocess
import shutil
import datetime
import redis
import mysql.connector

from SetupConstant import *


# set logging with date time, and both write to files
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=log_path,
                    filemode='a')



def build_image_worker(path, tag, rm, container_limits, network_mode, q, user_id, url):
    client = docker.from_env()
    try:
        user_image = client.images.build(path=path, tag=tag, rm=rm, container_limits=container_limits, network_mode=network_mode)
        message = ''
        for chunk in user_image[1]:
            if 'stream' in chunk:
                message = message + chunk['stream'].strip() + '\n'
            elif 'errorDetail' in chunk:
                message = message + chunk['errorDetail']['message'].strip() + '\n'
        q.put((0, message))
        logging.info('[worker]build docker image success {} (request {}, url {})'.format(user_image[0].id, user_id, url))
    except docker.errors.BuildError as e:
        err_message = ''
        for line in e.build_log:
            if 'stream' in line:
                err_message += (line['stream'].strip() + '\n')
            elif 'errorDetail' in line:
                err_message += (line['errorDetail']['message'].strip() + '\n')
        q.put((1, err_message))
        logging.error('[worker]build docker image failed {} (request {}, url {})'.format(e, user_id, url))
    except Exception as e:
        logging.error('[worker]build docker image general failed {} (request {}, url {})'.format(e, user_id, url))
        q.put((2, ''))